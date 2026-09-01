# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""One whole conversation turn over the store, with the model scripted.

Everything else in this suite exercises the backend directly. This file runs the same reads
and writes the way a conversation reaches them — through the tool array, the executor, and
the gates — against a scripted model client, so what is checked is the turn: which tools the
agent may call, what the tool results carry back to the model, which gate stops an apply, and
what the per-request context tells the model about the store's limits.

No Anthropic credentials are needed. ``FakeClient`` plays back one scripted message per model
call, so the turn loop is real and only the model is not.
"""

from __future__ import annotations

from typing import Any

import pytest

from commerce_common.testing import FakeClient, text_message, tool_use_message
from merchant_agent import MerchantSessionState
from merchant_agent_runtime import MerchantAgent
from merchant.api.agent_config import SKILLS_DIR

from .fake_admin import FakeAdmin

APRON = "gid://shopify/Product/1"
LADDER = "gid://shopify/Product/2"
BENCH_DOGS = "gid://shopify/Product/3"


@pytest.fixture
def make_agent(backend, config):
    def _make(responses: list[Any]) -> MerchantAgent:
        return MerchantAgent(
            backend=backend,
            skills_dir=SKILLS_DIR,
            config=config,
            client=FakeClient(responses),
        )

    return _make


@pytest.fixture
def state() -> MerchantSessionState:
    return MerchantSessionState()


async def run_turn(agent: MerchantAgent, text: str, session, state) -> list:
    events = []
    async for event in agent.stream_turn([{"role": "user", "content": text}], session, state):
        events.append(event)
    return events


def tool_results(client: FakeClient) -> str:
    """The text of every tool result the turn fed back to the model."""
    texts: list[str] = []
    for call in client.calls:
        for message in call.get("messages") or []:
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, str):
                        texts.append(content)
                    else:
                        texts.extend(
                            part.get("text", "") for part in content or [] if isinstance(part, dict)
                        )
    return " ".join(texts)


def request_context(client: FakeClient, index: int = 0) -> str:
    """The per-request block, which is the system prompt's second and uncached part."""
    return "".join(block["text"] for block in client.calls[index]["system"][1:])


# -- What reaches the model ----------------------------------------------------------


async def test_the_store_s_own_figures_reach_the_model(make_agent, session, state) -> None:
    agent = make_agent(
        [tool_use_message("get_business_snapshot", {}), text_message("Sales are up on the week.")]
    )

    await run_turn(agent, "How are sales this week?", session, state)

    results = tool_results(agent.client)
    assert "562" in results  # what the fixture's orders add up to
    assert "USD" in results


async def test_the_model_is_told_where_the_figures_came_from(make_agent, session, state) -> None:
    """Four of this example's honest limits travel in the merchant context rather than in a
    typed field, because the interface has nowhere else to put them. This asserts they
    arrive: without them the agent would report a measured zero for traffic and imply it had
    looked further back than sixty days."""
    agent = make_agent([text_message("Ask away.")])

    await run_turn(agent, "Hello", session, state)

    context = request_context(agent.client)
    assert "trailing order scan" in context
    assert "traffic and conversion read 0" in context
    assert '"order_history_days": 60' in context
    assert "not exposed on marketing activities" in context


async def test_the_static_prompt_is_the_same_bytes_on_every_turn(
    make_agent, session, state
) -> None:
    """The cache breakpoint. The store's figures change between turns and the prompt above
    them must not, or every turn pays for a fresh prefix."""
    agent = make_agent(
        [tool_use_message("get_business_snapshot", {}), text_message("Sales are up.")]
    )

    await run_turn(agent, "How are sales?", session, state)

    assert len(agent.client.calls) == 2
    first, second = (call["system"] for call in agent.client.calls)
    assert first[0] == second[0]
    assert first[0]["cache_control"] == {"type": "ephemeral"}
    # The store's identity sits after the breakpoint, where it costs nothing to change.
    assert "acme-supply.myshopify.com" not in first[0]["text"]
    assert "acme-supply.myshopify.com" in request_context(agent.client)


async def test_the_variant_ladder_is_visible_to_the_model(make_agent, session, state) -> None:
    """The interface has one price per listing, so the range has to reach the model some
    other way or it will talk about a two-rung product as though it had one price."""
    agent = make_agent(
        [tool_use_message("get_listing", {"listing_id": LADDER}), text_message("Two variants.")]
    )

    await run_turn(agent, "Tell me about the step stool", session, state)

    results = tool_results(agent.client)
    assert "60" in results and "90" in results


async def test_the_store_s_content_gaps_reach_the_model_as_gaps(make_agent, session, state) -> None:
    agent = make_agent(
        [tool_use_message("get_listing", {"listing_id": BENCH_DOGS}), text_message("Thin.")]
    )

    await run_turn(agent, "Is the bench dog listing any good?", session, state)

    results = tool_results(agent.client)
    assert "seo_description" in results or "images" in results


async def test_the_store_owner_s_text_reaches_the_model_fenced(make_agent, session, state) -> None:
    """Product titles and descriptions are the store owner's text, not the agent's, and the
    executor fences them. The mechanism is the shared one; the test is here because this is
    the example where the text is genuinely third-party."""
    agent = make_agent(
        [tool_use_message("get_listing", {"listing_id": APRON}), text_message("Here it is.")]
    )

    await run_turn(agent, "Show me the apron", session, state)

    results = tool_results(agent.client)
    assert "Canvas tool apron" in results
    assert "<merchant_data>" in results


# -- The gates, from the model's side -----------------------------------------------


async def test_a_price_move_stages_without_touching_the_store(
    make_agent, session, state, admin: FakeAdmin
) -> None:
    agent = make_agent(
        [
            tool_use_message("search_listings", {"query": "apron"}),
            tool_use_message(
                "stage_price_update", {"items": [{"listing_id": APRON, "new_price": 44.0}]}
            ),
            text_message("Staged — approve it on the card to make it live."),
        ]
    )

    events = await run_turn(agent, "Raise the apron by 10%", session, state)

    assert admin.calls == []
    staged = [event for event in events if event.type == "change_update"]
    assert len(staged) == 1
    assert staged[0].data["change"]["status"] == "staged"
    assert state.seen_changes


async def test_a_listing_the_session_never_read_cannot_be_changed(
    make_agent, session, state, admin: FakeAdmin
) -> None:
    """The provenance gate. A listing id the agent produced from somewhere other than a
    catalog tool — a memory, a guess, text inside an order — cannot be staged against."""
    agent = make_agent(
        [
            tool_use_message(
                "stage_price_update", {"items": [{"listing_id": APRON, "new_price": 44.0}]}
            ),
            text_message("Let me look it up first."),
        ]
    )

    events = await run_turn(agent, "Raise the apron by 10%", session, state)

    assert admin.calls == []
    assert not [event for event in events if event.type == "change_update"]
    assert "not returned by catalog tools in this session" in tool_results(agent.client)


async def test_the_agent_cannot_approve_its_own_change(
    make_agent, session, state, admin: FakeAdmin
) -> None:
    """The whole point of the approval surface, from the model's side: it stages, it asks to
    apply, and it is told what would lift the hold."""
    agent = make_agent(
        [
            tool_use_message("get_listing", {"listing_id": APRON}),
            tool_use_message(
                "stage_price_update", {"items": [{"listing_id": APRON, "new_price": 44.0}]}
            ),
            tool_use_message("apply_change", {"change_id": "chg-0001"}),
            text_message("It needs your approval on the card."),
        ]
    )

    await run_turn(agent, "Raise the apron by 10% and apply it", session, state)

    assert admin.calls == []
    # The refusal names the surface the operator would use, so the agent can say what to do
    # rather than only that it could not.
    assert "Approve button" in tool_results(agent.client)


async def test_a_price_move_beyond_the_guardrail_is_refused_before_staging(
    make_agent, session, state, admin: FakeAdmin
) -> None:
    agent = make_agent(
        [
            tool_use_message("get_listing", {"listing_id": APRON}),
            tool_use_message(
                "stage_price_update", {"items": [{"listing_id": APRON, "new_price": 90.0}]}
            ),
            text_message("That is more than I can move a price by in one change."),
        ]
    )

    events = await run_turn(agent, "Double the apron's price", session, state)

    assert admin.calls == []
    assert not [event for event in events if event.type == "change_update"]
    assert "20" in tool_results(agent.client)  # the configured delta cap, named in the refusal


async def test_an_untracked_product_cannot_be_restocked_through_a_turn(
    make_agent, session, state, admin: FakeAdmin
) -> None:
    """A refusal the store's own configuration causes, not a guardrail. The agent is told why
    so it can explain it rather than retrying."""
    agent = make_agent(
        [
            tool_use_message("search_listings", {"query": "broom"}),
            tool_use_message(
                "stage_inventory_action",
                {
                    "items": [
                        {
                            "listing_id": "gid://shopify/Product/6",
                            "action": "restock",
                            "quantity": 20,
                        }
                    ]
                },
            ),
            text_message("Shopify is not tracking that product's stock."),
        ]
    )

    await run_turn(agent, "Restock the sawdust broom", session, state)

    assert admin.calls == []
    assert "does not track inventory" in tool_results(agent.client)

# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The client's wire behavior: where the profile goes, both response body shapes,
the one-shot transient retry, and how errors surface."""

import json

import httpx
import pytest

from storefront.api.ucp_client import DEFAULT_PROFILE_URL, UcpCartGoneError, UcpClient, UcpError


def capture_client(*responses: httpx.Response) -> tuple[UcpClient, list[dict]]:
    requests: list[dict] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    client = UcpClient(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handle)), retry_backoff=0.0
    )
    return client, requests


def rpc_result(result: dict) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


async def test_ucp_calls_carry_the_profile_as_the_meta_argument():
    client, requests = capture_client(rpc_result({"structuredContent": {"ok": True}}))
    await client.call_ucp("search_catalog", {"catalog": {"query": "shirt"}})
    arguments = requests[0]["params"]["arguments"]
    assert arguments["meta"] == {"ucp-agent": {"profile": DEFAULT_PROFILE_URL}}
    assert arguments["catalog"] == {"query": "shirt"}
    assert "_meta" not in requests[0]["params"]


async def test_storefront_calls_carry_no_profile():
    client, requests = capture_client(rpc_result({"structuredContent": {"ok": True}}))
    await client.call_storefront("search_shop_policies_and_faqs", {"query": "returns"})
    assert requests[0]["params"]["arguments"] == {"query": "returns"}


async def test_a_single_event_sse_body_parses_like_json():
    body = (
        'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"ok":1}}}\n\n'
    )
    response = httpx.Response(
        200, content=body.encode(), headers={"content-type": "text/event-stream"}
    )
    client, _ = capture_client(response)
    assert await client.call_ucp("get_cart", {"id": "c1"}) == {"ok": 1}


async def test_text_content_falls_back_to_parsed_json_and_lists_wrap():
    client, _ = capture_client(
        rpc_result({"content": [{"type": "text", "text": '[{"title": "Returns"}]'}]})
    )
    payload = await client.call_storefront("search_shop_policies_and_faqs", {"query": "returns"})
    assert payload == {"results": [{"title": "Returns"}]}


async def test_a_transient_status_is_retried_once():
    ok = rpc_result({"structuredContent": {"ok": True}})
    client, requests = capture_client(httpx.Response(429), ok)
    assert await client.call_ucp("search_catalog", {"catalog": {"query": "x"}}) == {"ok": True}
    assert len(requests) == 2
    assert requests[0] == requests[1]  # the same call again, not a new one


async def test_a_persistent_5xx_raises_after_the_one_retry():
    client, requests = capture_client(httpx.Response(503), httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await client.call_ucp("search_catalog", {"catalog": {"query": "x"}})
    assert len(requests) == 2


async def test_a_jsonrpc_error_raises():
    client, _ = capture_client(
        httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "nope"}},
        )
    )
    with pytest.raises(UcpError, match="nope"):
        await client.call_ucp("search_catalog", {"catalog": {"query": "x"}})


async def test_a_tool_error_surfaces_the_ucp_messages_not_the_envelope():
    envelope = {"ucp": {"status": "error"}, "messages": [{"content": "Product not found"}]}
    client, _ = capture_client(
        rpc_result({"isError": True, "content": [{"type": "text", "text": json.dumps(envelope)}]})
    )
    with pytest.raises(UcpError, match=r"^get_product: Product not found$"):
        await client.call_ucp("get_product", {"catalog": {"id": "gid://shopify/Product/1"}})


@pytest.mark.parametrize("code", ["cart_not_found", "invalid_cart_id"])
async def test_a_cart_gone_code_raises_the_recoverable_subclass(code):
    envelope = {
        "ucp": {"status": "error"},
        "messages": [{"type": "error", "code": code, "content": "The requested cart is gone"}],
    }
    client, _ = capture_client(
        rpc_result({"isError": True, "content": [{"type": "text", "text": json.dumps(envelope)}]})
    )
    with pytest.raises(UcpCartGoneError, match="gone"):
        await client.call_ucp("get_cart", {"id": "gid://shopify/Cart/x"})

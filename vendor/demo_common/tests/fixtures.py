# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures; a vertical conftest star-imports them and defines ``main``, ``make_storefront``,
``merchant``, ``merchant_identity``, ``make_merchant_router``, ``extra_public_routes``,
``restockable_listing``, ``cart_product``, ``relevance_probe``, and, where the vertical has them,
``merchant_extensions`` and ``showcase_stamps``."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from commerce_common.skills import SkillRegistry
from demo_common import SESSION_HEADER
from merchant_agent import MerchantSessionContext, MerchantSessionState
from merchant_agent.executor import MerchantToolExecutor
from shopping_agent import ShoppingSessionContext, ShoppingSessionState
from shopping_agent.executor import ShoppingToolExecutor


def start_shopper(client: TestClient, user_id: str = "demo-user") -> dict[str, str]:
    """Returns the headers of a new storefront session for ``user_id``."""
    token = client.post("/api/session", json={"user_id": user_id}).json()["session_id"]
    return {SESSION_HEADER: token}


def start_operator(client: TestClient) -> dict[str, str]:
    """Returns the headers of a new portal session."""
    return {SESSION_HEADER: client.post("/api/merchant/session").json()["session_id"]}


def session_record(main, headers: dict[str, str]):
    """Returns the host's session record behind ``headers``."""
    return main.host.sessions.require(headers[SESSION_HEADER])


@pytest.fixture
def client(main) -> TestClient:
    return TestClient(main.app, base_url="http://localhost")


@pytest.fixture
def shopper(main, client):
    """Returns ``start(*product_ids, user_id=...)``, the headers of a new session that has seen those products."""

    def start(*seen: str, user_id: str = "demo-user") -> dict[str, str]:
        headers = start_shopper(client, user_id)
        record = session_record(main, headers)
        record.state.remember_products([main.backend.products[pid] for pid in seen])
        main.host.sessions.save(record)
        return headers

    return start


@pytest.fixture
def backend(make_storefront):
    return make_storefront()


@pytest.fixture
def session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="s-1", user_id="demo-user")


@pytest.fixture
def other_session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="s-2", user_id="demo-user-2")


@pytest.fixture
def state() -> ShoppingSessionState:
    return ShoppingSessionState()


@pytest.fixture
def make_executor(main, backend, state):
    """Returns a builder for the deployment's executor over ``backend`` and ``state`` for a session."""

    def build(session: ShoppingSessionContext) -> ShoppingToolExecutor:
        return ShoppingToolExecutor(
            backend=backend,
            config=main.agent.config,
            skills=SkillRegistry([]),
            session=session,
            state=state,
            extensions=list(main.agent.extra_presentation_tools),
        )

    return build


@pytest.fixture
def executor(make_executor, session) -> ShoppingToolExecutor:
    return make_executor(session)


@pytest.fixture
def operator_session(merchant_identity) -> MerchantSessionContext:
    return MerchantSessionContext(
        session_id="ms-1",
        merchant_id=merchant_identity.merchant_id,
        operator=merchant_identity.operator,
    )


@pytest.fixture
def merchant_state() -> MerchantSessionState:
    return MerchantSessionState()


@pytest.fixture
def merchant_extensions(merchant) -> list:
    del merchant
    return []


@pytest.fixture
def merchant_executor(
    merchant, operator_session, merchant_state, merchant_extensions
) -> MerchantToolExecutor:
    """Returns the portal's executor over ``merchant`` with host approval switched off."""
    return MerchantToolExecutor(
        backend=merchant,
        config=merchant.config.model_copy(update={"require_host_approval": False}),
        skills=SkillRegistry([]),
        session=operator_session,
        state=merchant_state,
        extensions=merchant_extensions,
    )


@pytest.fixture(scope="session")
def showcase_stamps() -> set[str]:
    """Returns the search-time stamps a showcase product may carry beyond its catalog record."""
    return set()


_PRODUCT_LITERAL = re.compile(
    r"^const\s+\w+:\s*Product\s*=\s*(\{.*?\n\});", re.MULTILINE | re.DOTALL
)
_KEY = re.compile(r"(^|[{,])(\s*)([A-Za-z_]\w*)\s*:", re.MULTILINE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def showcase_products(example_root: Path) -> list[dict]:
    """Returns the product literals in the storefront's ``showcase-fixtures.ts`` as dicts."""
    source = (example_root / "storefront-web" / "lib" / "showcase-fixtures.ts").read_text(
        encoding="utf-8"
    )
    literals = _PRODUCT_LITERAL.findall(source)
    assert literals, "no `const X: Product = {...}` literals in showcase-fixtures.ts"
    return [_literal_to_dict(literal) for literal in literals]


def _literal_to_dict(literal: str) -> dict:
    """Returns a TypeScript object literal parsed as JSON after dropping comments and trailing commas."""
    code = "\n".join(line for line in literal.splitlines() if not line.lstrip().startswith("//"))
    return json.loads(_TRAILING_COMMA.sub(r"\1", _KEY.sub(r'\1\2"\3":', code)))

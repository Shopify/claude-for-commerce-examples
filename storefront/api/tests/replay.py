# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Replay of recorded UCP responses: ``fixture_key`` names a request by what
distinguishes it (shared with ``scripts/record_fixtures.py``, which writes
``data/recorded_responses.json``), and ``replay_transport`` is the
``httpx.MockTransport`` the tests hand :class:`UcpClient` so no test touches
the network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FIXTURES_PATH = DATA_DIR / "recorded_responses.json"
# Hand-written records in the same format, shaped exactly like the Order MCP docs'
# responses, for what the live key cannot answer yet (its orders scope is pending): a
# completed checkout naming its order, and the get_order happy envelope. Their ids
# collide with nothing the recorder writes.
ORDER_FIXTURES_PATH = DATA_DIR / "order_fixtures.json"

# A well-formed cart GID no shop knows: the shop answers ``cart_not_found``. The
# recorder captures that live response and the gone-cart tests replay it.
GONE_CART_ID = (
    "gid://shopify/Cart/Z2NwLXVzLWNlbnRyYWwxOjAxSE5DWTBWWDlLWDlaWDlaWDlaWDlaWDla"
    "?key=00000000000000000000000000000000"
)
# The order id the recorder asks get_order about; while the key's orders scope is
# pending the shop answers orders_not_allowed regardless of the id (the recorded
# degrade fixture).
DEGRADE_ORDER_ID = "gid://shopify/Order/1001"
# The synthetic completed-checkout pair in order_fixtures.json.
COMPLETED_CHECKOUT_ID = "gid://shopify/Checkout/completed-fixture?key=0f0f0f0f"
FIXTURE_ORDER_ID = "gid://shopify/Order/1042"


def fixture_key(path: str, name: str, arguments: dict[str, Any]) -> str:
    """One line naming a tools/call request: endpoint path, tool, and the argument
    that distinguishes calls to the same tool."""
    catalog = arguments.get("catalog") or {}
    detail = (
        catalog.get("query")
        or catalog.get("id")
        or ",".join(catalog.get("ids") or [])
        or arguments.get("query")
        or arguments.get("id", "")
    )
    if name in ("create_cart", "update_cart"):
        lines = (arguments.get("cart") or {}).get("line_items") or []
        if name == "create_cart":
            op = "create"
        elif any(line["quantity"] == 0 for line in lines):
            op = "remove"
        elif any("id" not in line for line in lines):
            op = "add"
        else:
            op = "update"
        detail = f"{arguments.get('id', '')} {op}".strip()
    return f"{path} {name} {detail}".strip()


def load_fixtures(*paths: Path) -> dict[str, dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths or (FIXTURES_PATH, ORDER_FIXTURES_PATH):
        records.extend(json.loads(path.read_text(encoding="utf-8")))
    return {record["key"]: record for record in records}


def fixture_payload(key: str) -> dict[str, Any]:
    """A recorded response's structured payload, for tests that need its live ids."""
    result = load_fixtures()[key]["response"]["result"]
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    return json.loads(result["content"][0]["text"])


def replay_transport(*paths: Path) -> httpx.MockTransport:
    fixtures = load_fixtures(*paths)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            discovery = json.loads((DATA_DIR / "discovery.json").read_text(encoding="utf-8"))
            return httpx.Response(200, json=discovery)
        body = json.loads(request.content)
        params = body.get("params") or {}
        key = fixture_key(request.url.path, params.get("name", ""), params.get("arguments") or {})
        record = fixtures.get(key)
        if record is None:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "error": {"code": -32000, "message": f"No recorded response for: {key}"},
                },
            )
        return httpx.Response(200, json=record["response"])

    return httpx.MockTransport(handle)

# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Refresh ``data/`` from the live demo store: one scripted pass over the calls the
backend makes (catalog search, product detail, lookup, a UCP cart's create/read/update/
add/remove plus the shop's answers for a cart id it no longer knows, a checkout's
create/update/read then a cancel to leave the shop tidy, a policies search, and — when
the client credentials are in the environment — ``get_order``'s answer while the key's
orders scope is pending), each raw JSON-RPC response recorded under the ``fixture_key``
the tests replay it by. Manual use only; tests never touch the network. The synthetic
records in ``data/order_fixtures.json`` are hand-written and untouched here.

    python storefront/scripts/record_fixtures.py       # SHOP_DOMAIN overrides
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

EXAMPLES_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXAMPLES_DIR))

from storefront.api.identity import AgentToken, ShopSignIn  # noqa: E402
from storefront.api.tests.replay import (  # noqa: E402
    DATA_DIR,
    DEGRADE_ORDER_ID,
    GONE_CART_ID,
    fixture_key,
)
from storefront.api.ucp_client import (  # noqa: E402
    UcpClient,
    profile_url_from_env,
    shop_domain_from_env,
)

CONTEXT = {"address_country": "US", "language": "en"}
SEARCH_QUERY = "shirt"
POLICY_QUERY = "return policy"


class Recorder:
    def __init__(self, client: UcpClient, http: httpx.AsyncClient) -> None:
        self.client = client
        self.http = http
        self.records: list[dict[str, Any]] = []
        self._id = 0

    async def call(
        self,
        url: str,
        name: str,
        arguments: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._id += 1
        response = await self.http.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers={"Accept": "application/json, text/event-stream", **(headers or {})},
        )
        response.raise_for_status()
        body = response.json()
        path = httpx.URL(url).path
        key = fixture_key(path, name, arguments)
        print(f"recorded: {key}")
        self.records.append(
            {
                "key": key,
                "endpoint_path": path,
                "name": name,
                "arguments": arguments,
                "response": body,
            }
        )
        result = body.get("result") or {}
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        return json.loads(result["content"][0]["text"])


async def main() -> None:
    http = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    client = UcpClient(shop_domain_from_env(), profile_url_from_env(), http=http)
    meta = {"ucp-agent": {"profile": profile_url_from_env()}}
    recorder = Recorder(client, http)

    discovery = await client.discover()
    (DATA_DIR / "discovery.json").write_text(
        json.dumps(discovery, indent=1) + "\n", encoding="utf-8"
    )
    print("recorded: discovery")

    search = await recorder.call(
        client.ucp_url,
        "search_catalog",
        {
            "meta": meta,
            "catalog": {"query": SEARCH_QUERY, "context": CONTEXT, "pagination": {"limit": 3}},
        },
    )
    product, second = search["products"][0], search["products"][1]
    product_id = product["id"]

    def first_available(record: dict[str, Any]) -> str:
        return next(
            v["id"] for v in record["variants"] if v.get("availability", {}).get("available")
        )

    # The variants the backend resolves to: each product's first available one.
    variant_id, second_variant = first_available(product), first_available(second)

    await recorder.call(
        client.ucp_url,
        "get_product",
        {"meta": meta, "catalog": {"id": product_id, "context": CONTEXT}},
    )
    await recorder.call(
        client.ucp_url,
        "get_product",
        {"meta": meta, "catalog": {"id": "gid://shopify/Product/1", "context": CONTEXT}},
    )
    await recorder.call(
        client.ucp_url,
        "lookup_catalog",
        {"meta": meta, "catalog": {"ids": [product_id], "context": CONTEXT}},
    )

    # The cart lifecycle in the order the tests walk it. update_cart's line list
    # replaces the cart's contents, so each call sends the full desired state.
    created = await recorder.call(
        client.ucp_url,
        "create_cart",
        {
            "meta": meta,
            "cart": {
                "line_items": [{"item": {"id": variant_id}, "quantity": 1}],
                "context": CONTEXT,
            },
        },
    )
    cart_id = created["id"]
    line_id = created["line_items"][0]["id"]

    await recorder.call(client.ucp_url, "get_cart", {"meta": meta, "id": cart_id})
    # A second item beside the first; the entry without a line id is the addition.
    await recorder.call(
        client.ucp_url,
        "update_cart",
        {
            "meta": meta,
            "id": cart_id,
            "cart": {
                "line_items": [
                    {"id": line_id, "item": {"id": variant_id}, "quantity": 1},
                    {"item": {"id": second_variant}, "quantity": 1},
                ]
            },
        },
    )
    # The quantity change lists only the first line, which also drops the second —
    # replacement semantics — so the remaining snapshots are single-line again.
    await recorder.call(
        client.ucp_url,
        "update_cart",
        {
            "meta": meta,
            "id": cart_id,
            "cart": {"line_items": [{"id": line_id, "item": {"id": variant_id}, "quantity": 2}]},
        },
    )
    await recorder.call(
        client.ucp_url,
        "update_cart",
        {
            "meta": meta,
            "id": cart_id,
            "cart": {"line_items": [{"id": line_id, "item": {"id": variant_id}, "quantity": 0}]},
        },
    )

    # The shop's answers for a cart id it no longer knows (the recovery fixtures).
    await recorder.call(client.ucp_url, "get_cart", {"meta": meta, "id": GONE_CART_ID})
    await recorder.call(
        client.ucp_url,
        "update_cart",
        {
            "meta": meta,
            "id": GONE_CART_ID,
            "cart": {"line_items": [{"item": {"id": variant_id}, "quantity": 1}]},
        },
    )
    await recorder.call(
        client.ucp_url,
        "update_cart",
        {
            "meta": meta,
            "id": GONE_CART_ID,
            "cart": {"line_items": [{"id": line_id, "item": {"id": variant_id}, "quantity": 2}]},
        },
    )

    # The checkout lifecycle: create from one line, re-stage at quantity 2, read it
    # back, cancel to leave the shop tidy. Each answer is isError with a full document
    # while the checkout awaits buyer input — the shape ``document_error_ok`` accepts.
    checkout = await recorder.call(
        client.ucp_url,
        "create_checkout",
        {
            "meta": meta,
            "checkout": {"line_items": [{"item": {"id": variant_id}, "quantity": 1}]},
        },
    )
    checkout_id = checkout["id"]
    await recorder.call(
        client.ucp_url,
        "update_checkout",
        {
            "meta": meta,
            "id": checkout_id,
            "checkout": {"line_items": [{"item": {"id": variant_id}, "quantity": 2}]},
        },
    )
    await recorder.call(client.ucp_url, "get_checkout", {"meta": meta, "id": checkout_id})
    await recorder.call(client.ucp_url, "cancel_checkout", {"meta": meta, "id": checkout_id})

    await recorder.call(
        client.storefront_url, "search_shop_policies_and_faqs", {"query": POLICY_QUERY}
    )

    # Token-tier degrade: ``get_order`` with a freshly minted agent token records the
    # ``orders_not_allowed`` envelope the key gets while its orders scope is pending
    # (the shop answers it regardless of the order id). Skipped without credentials;
    # nothing about the token is recorded or printed.
    signin = ShopSignIn(http=http)
    if signin.configured:
        bearer = await AgentToken(signin).bearer()
        buyer_ip = (
            os.environ.get("SHOP_BUYER_IP")
            or (await http.get("https://api.ipify.org")).text.strip()
        )
        if bearer:
            await recorder.call(
                client.ucp_url,
                "get_order",
                {"meta": meta, "id": DEGRADE_ORDER_ID},
                headers={"Authorization": f"Bearer {bearer}", "Shopify-Buyer-IP": buyer_ip},
            )
        else:
            print("skipped: get_order (agent token mint failed)")
    else:
        print("skipped: get_order (no client credentials in the environment)")

    out = DATA_DIR / "recorded_responses.json"
    out.write_text(json.dumps(recorder.records, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(recorder.records)} records)")
    await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())

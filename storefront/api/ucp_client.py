# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""A thin MCP-over-HTTP client for one Shopify shop's two tool surfaces: the UCP
endpoint (``/api/ucp/mcp`` — the catalog and cart tools, every call carrying the agent
profile in the ``meta`` argument they require) and the standard Storefront endpoint
(``/api/mcp``, policies only; its cart tools are deprecated, gone after 2026-08-31).
Each call is a single JSON-RPC ``tools/call`` POST; the server may answer with JSON or
with a one-event SSE body, and either is parsed to the tool's structured payload. A 429
or 5xx answer is retried once after a short backoff. ``discover`` reads
``/.well-known/ucp``. Tests inject an ``httpx.MockTransport`` over the recorded
responses in ``data/``."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

DEFAULT_SHOP_DOMAIN = "demostore.mock.shop"
# A hosted fixture Shopify publishes for exactly this use: an agent profile that
# negotiates the full 2026-04-08 capability set (https://shopify.dev/docs/agents/profiles).
DEFAULT_PROFILE_URL = (
    "https://shopify.dev/ucp/agent-profiles/2026-04-08/valid-with-capabilities.json"
)
_TIMEOUT = httpx.Timeout(20.0)
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
# The shop's error codes for a cart id it no longer accepts (live-probed shapes).
_CART_GONE_CODES = {"cart_not_found", "invalid_cart_id"}


def shop_domain_from_env() -> str:
    return os.environ.get("SHOP_DOMAIN", DEFAULT_SHOP_DOMAIN)


def profile_url_from_env() -> str:
    return os.environ.get("UCP_AGENT_PROFILE_URL", DEFAULT_PROFILE_URL)


class UcpError(RuntimeError):
    """The endpoint rejected the call (JSON-RPC error or a tool result with
    ``isError``); the message is safe to log but is shop-authored text. ``codes``
    holds the ``messages[].code`` values the error carried, when it carried any."""

    def __init__(self, message: str, codes: frozenset[str] = frozenset()) -> None:
        super().__init__(message)
        self.codes = codes


class UcpAuthError(UcpError):
    """A buyer-token call came back 401: the token expired or was revoked. The caller
    re-mints once and retries, or falls back to anonymous."""


class UcpCartGoneError(UcpError):
    """The shop no longer accepts the cart id (``cart_not_found`` or
    ``invalid_cart_id``). The caller drops the session's cart binding and retries
    once — a write into a fresh cart, a read as an empty one."""


class UcpClient:
    def __init__(
        self,
        shop_domain: str = DEFAULT_SHOP_DOMAIN,
        profile_url: str = DEFAULT_PROFILE_URL,
        http: httpx.AsyncClient | None = None,
        retry_backoff: float = 0.5,
    ) -> None:
        base = f"https://{shop_domain}"
        self.ucp_url = f"{base}/api/ucp/mcp"
        self.storefront_url = f"{base}/api/mcp"
        self.discovery_url = f"{base}/.well-known/ucp"
        self._profile_url = profile_url
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
        self._retry_backoff = retry_backoff
        self._next_id = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def discover(self) -> dict[str, Any]:
        response = await self._http.get(self.discovery_url)
        response.raise_for_status()
        return response.json()

    async def call_ucp(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        bearer_token: str | None = None,
        buyer_ip: str | None = None,
        document_error_ok: bool = False,
    ) -> dict[str, Any]:
        """The catalog tools (``search_catalog`` / ``lookup_catalog`` / ``get_product``),
        the cart tools (``create_cart`` / ``update_cart`` / ``get_cart``), the checkout
        tools, and ``get_order``. A token-bearing call carries the bearer and the
        buyer's IP together — the endpoint requires the pair (a bearer without
        ``Shopify-Buyer-IP`` is a 422) — and a 401 raises ``UcpAuthError`` so the caller
        can re-mint. Anonymous calls carry neither header; cart calls are always
        anonymous. The checkout tools answer ``isError`` whenever the document still
        needs buyer input (a contact method, a delivery address) even though the
        checkout exists — ``document_error_ok`` returns such a document (recognized by
        its ``id``; its ``messages`` ride along) instead of raising."""
        headers = {}
        if bearer_token is not None and buyer_ip is not None:
            headers = {"Authorization": f"Bearer {bearer_token}", "Shopify-Buyer-IP": buyer_ip}
        return await self._call(
            self.ucp_url,
            name,
            arguments,
            with_profile=True,
            headers=headers,
            document_error_ok=document_error_ok,
        )

    async def call_storefront(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """``search_shop_policies_and_faqs`` — the one tool still read here."""
        return await self._call(self.storefront_url, name, arguments, with_profile=False)

    async def _call(
        self,
        url: str,
        name: str,
        arguments: dict[str, Any],
        *,
        with_profile: bool,
        headers: dict[str, str] | None = None,
        document_error_ok: bool = False,
    ) -> dict[str, Any]:
        self._next_id += 1
        if with_profile:
            arguments = {"meta": {"ucp-agent": {"profile": self._profile_url}}, **arguments}
        params: dict[str, Any] = {"name": name, "arguments": arguments}
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": "tools/call", "params": params}
        all_headers = {"Accept": "application/json, text/event-stream", **(headers or {})}
        response = await self._http.post(url, json=body, headers=all_headers)
        if response.status_code in _TRANSIENT_STATUSES:
            await asyncio.sleep(self._retry_backoff)
            response = await self._http.post(url, json=body, headers=all_headers)
        if headers and response.status_code == 401:
            raise UcpAuthError(f"{name}: the catalog rejected the buyer token (401)")
        response.raise_for_status()
        return _tool_payload(_rpc_body(response), name, document_error_ok=document_error_ok)


def _rpc_body(response: httpx.Response) -> dict[str, Any]:
    """The JSON-RPC envelope, whether the server answered with a JSON body or a
    single-response SSE stream (the streamable-HTTP transport's other legal shape)."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise UcpError("Empty event stream from the MCP endpoint.")


def _tool_error(name: str, texts: list[str]) -> UcpError:
    """A failed tool call's exception. An error result's text block is often a whole
    UCP envelope; its ``messages`` carry the useful content (``"Product not found"``)
    and codes — a cart-gone code picks the recoverable subclass."""
    joined = " ".join(texts).strip()
    try:
        parsed = json.loads(joined)
    except (TypeError, ValueError):
        parsed = None
    messages = parsed.get("messages") if isinstance(parsed, dict) else None
    contents = [m.get("content", "") for m in messages or [] if isinstance(m, dict)]
    text = "; ".join(c for c in contents if c) or joined or "tool error"
    codes = frozenset(
        m.get("code") for m in messages or [] if isinstance(m, dict) and m.get("code")
    )
    cls = UcpCartGoneError if codes & _CART_GONE_CODES else UcpError
    return cls(f"{name}: {text}", codes)


def _structured_payload(result: dict[str, Any], texts: list[str]) -> dict[str, Any] | None:
    """``structuredContent`` when present, else the first text content block parsed as
    JSON — a list (the policies tool's shape) comes back under ``"results"``."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for text in texts:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"results": parsed}
    return None


def _tool_payload(
    body: dict[str, Any], name: str, *, document_error_ok: bool = False
) -> dict[str, Any]:
    """The tool's structured payload; unparsable text comes back under ``"text"``. An
    ``isError`` result raises, unless ``document_error_ok`` and the payload is a
    document with an ``id`` (a checkout awaiting buyer input) — then it is returned."""
    if "error" in body:
        error = body["error"]
        raise UcpError(f"{name}: {error.get('message', 'JSON-RPC error')}")
    result = body.get("result") or {}
    texts = [
        block.get("text", "") for block in result.get("content") or [] if isinstance(block, dict)
    ]
    payload = _structured_payload(result, texts)
    if result.get("isError") and not (document_error_ok and payload and payload.get("id")):
        raise _tool_error(name, texts)
    return payload if payload is not None else {"text": "\n".join(texts)}

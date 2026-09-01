# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The Admin GraphQL transport: one POST per document, the access token held here and
never passed outward, throttle-aware retries, and GraphQL and ``userErrors`` failures
raised as exceptions. Everything above this module works in domain types.

The backend depends on :class:`AdminExecutor`, not on this class, so the tests drive the
same code over canned documents without a network.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

_OPERATION = re.compile(r"\b(?:query|mutation)\s+(\w+)")
_THROTTLED = "THROTTLED"
_MAX_ATTEMPTS = 4


def operation_name(document: str) -> str:
    """The document's operation name, used for logging and for the tests' dispatch."""
    match = _OPERATION.search(document)
    return match.group(1) if match else "anonymous"


class AdminAPIError(RuntimeError):
    """A GraphQL-level failure: transport, a query the schema rejected, or a throttle that
    outlasted the retries. ``codes`` holds the extension codes Shopify returned, so a
    caller can tell an unavailable field from a broken one."""

    def __init__(self, message: str, *, codes: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.codes = codes


class AdminUserError(RuntimeError):
    """A mutation the API accepted and then refused; ``errors`` holds one entry per
    ``userErrors`` line, already flattened for display."""

    def __init__(self, operation: str, errors: list[str]) -> None:
        super().__init__(f"{operation}: " + "; ".join(errors))
        self.errors = errors


class AdminExecutor(Protocol):
    """What the backend needs from the transport: one call for reads and one for writes.
    ``mutate`` is separate because a mutation that comes back with ``userErrors`` has to
    raise rather than return, and no caller should have to remember to check."""

    async def execute(
        self, document: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    async def mutate(
        self, document: str, variables: dict[str, Any], *, root: str
    ) -> dict[str, Any]: ...


class AdminGraphQLClient:
    """One store's Admin API endpoint. The token stays in this object's headers: it is
    never returned, logged, or included in an exception message."""

    def __init__(
        self,
        *,
        shop_domain: str,
        access_token: str,
        api_version: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.shop_domain = shop_domain
        self.api_version = api_version
        self.endpoint = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def execute(
        self, document: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        name = operation_name(document)
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            payload = await self._post(name, document, variables)
            errors = payload.get("errors") or []
            codes = tuple(str((error.get("extensions") or {}).get("code", "")) for error in errors)
            if _THROTTLED in codes and attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(self._backoff(payload, attempt))
                continue
            if errors:
                raise AdminAPIError(
                    f"{name}: " + "; ".join(str(error.get("message", error)) for error in errors),
                    codes=codes,
                )
            data = payload.get("data")
            if data is None:
                raise AdminAPIError(f"{name}: response carried no data")
            return data
        raise AdminAPIError(f"{name}: still throttled after {_MAX_ATTEMPTS} attempts")

    async def mutate(
        self, document: str, variables: dict[str, Any], *, root: str
    ) -> dict[str, Any]:
        """Run a mutation and raise :class:`AdminUserError` when the payload carries
        ``userErrors``, so no caller can mistake a refused write for a completed one."""
        data = await self.execute(document, variables)
        payload = data.get(root) or {}
        if user_errors := payload.get("userErrors") or []:
            raise AdminUserError(
                operation_name(document),
                [
                    f"{'.'.join(entry.get('field') or []) or 'request'}: {entry.get('message')}"
                    for entry in user_errors
                ],
            )
        return payload

    async def _post(
        self, name: str, document: str, variables: dict[str, Any] | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"query": document}
        if variables:
            body["variables"] = variables
        try:
            response = await self._client.post(self.endpoint, headers=self._headers, json=body)
        except httpx.HTTPError as error:
            raise AdminAPIError(
                f"{name}: {type(error).__name__} talking to the Admin API"
            ) from error
        if response.status_code == 401 or response.status_code == 403:
            raise AdminAPIError(
                f"{name}: the Admin API rejected the access token ({response.status_code}); "
                "it may have expired, and a token minted from a client ID and secret lasts "
                "24 hours. Check SHOPIFY_ADMIN_TOKEN and the app's scopes"
            )
        if response.status_code == 429:
            return {"errors": [{"message": "rate limited", "extensions": {"code": _THROTTLED}}]}
        if response.status_code >= 400:
            raise AdminAPIError(f"{name}: the Admin API returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise AdminAPIError(f"{name}: the Admin API returned a non-JSON body") from error

    @staticmethod
    def _backoff(payload: dict[str, Any], attempt: int) -> float:
        """Wait long enough for the leaky bucket to refill when the response says how full
        it is, and back off geometrically when it does not."""
        throttle = ((payload.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
        restore_rate = float(throttle.get("restoreRate") or 0)
        available = float(throttle.get("currentlyAvailable") or 0)
        requested = float(
            ((payload.get("extensions") or {}).get("cost") or {}).get("requestedQueryCost") or 0
        )
        if restore_rate > 0 and requested > available:
            return min((requested - available) / restore_rate + 0.2, 8.0)
        return min(0.5 * 2 ** (attempt - 1), 8.0)

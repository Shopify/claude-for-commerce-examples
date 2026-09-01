# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The Admin GraphQL transport: one POST per document, the access token fetched per
request from a :class:`~.admin_token.TokenSource` and never passed outward, throttle-aware
retries, a token Shopify has stopped accepting minted again once, and GraphQL and
``userErrors`` failures raised as exceptions. Everything above this module works in domain
types.

The backend depends on :class:`AdminExecutor`, not on this class, so the tests drive the
same code over canned documents without a network.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

import httpx

from .admin_token import TokenError, TokenSource

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
    """One store's Admin API endpoint. The token belongs to ``token_source`` and is read
    from it per request: this object never holds one, and never returns, logs, or names one
    in an exception message."""

    def __init__(
        self,
        *,
        shop_domain: str,
        token_source: TokenSource,
        api_version: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.shop_domain = shop_domain
        self.api_version = api_version
        self.endpoint = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self._tokens = token_source
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._headers = {"Content-Type": "application/json"}

    async def aclose(self) -> None:
        """Closes the token source too. It exists to serve this transport and holds its own
        connection, so one close is the whole shutdown and a caller cannot forget half."""
        await self._tokens.aclose()
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
        self,
        name: str,
        document: str,
        variables: dict[str, Any] | None,
        *,
        may_renew: bool = True,
    ) -> dict[str, Any]:
        """One POST. A 401 means the token is spent rather than the request wrong, so a
        source that can mint another does, once: ``may_renew`` is what stops a store that
        refuses every token from looping."""
        body: dict[str, Any] = {"query": document}
        if variables:
            body["variables"] = variables
        headers = {**self._headers, "X-Shopify-Access-Token": await self._token(name)}
        try:
            response = await self._client.post(self.endpoint, headers=headers, json=body)
        except httpx.HTTPError as error:
            raise AdminAPIError(
                f"{name}: {type(error).__name__} talking to the Admin API"
            ) from error
        if response.status_code == 401:
            if not may_renew:
                raise AdminAPIError(
                    f"{name}: the Admin API rejected a freshly minted access token. Check "
                    "that the app is installed on this store and that the store approved "
                    "its released version"
                )
            if await self._renew(name) is not None:
                return await self._post(name, document, variables, may_renew=False)
            raise AdminAPIError(
                f"{name}: the Admin API rejected the access token. A token pasted into "
                "SHOPIFY_ADMIN_TOKEN expires 24 hours after it was minted; set "
                "SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET instead and this example mints "
                "its own, as needed"
            )
        if response.status_code == 403:
            # Not an expiry. The token authenticated and was then refused, which on this API
            # means the app's released version is short the scope this document needs.
            raise AdminAPIError(
                f"{name}: the Admin API refused the request (403). The token is valid, so "
                "this is a scope the app's released version does not carry"
            )
        if response.status_code == 429:
            return {"errors": [{"message": "rate limited", "extensions": {"code": _THROTTLED}}]}
        if response.status_code >= 400:
            raise AdminAPIError(f"{name}: the Admin API returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise AdminAPIError(f"{name}: the Admin API returned a non-JSON body") from error

    async def _token(self, name: str) -> str:
        try:
            return await self._tokens.token()
        except TokenError as error:
            raise AdminAPIError(f"{name}: {error}") from error

    async def _renew(self, name: str) -> str | None:
        try:
            return await self._tokens.renew()
        except TokenError as error:
            raise AdminAPIError(f"{name}: {error}") from error

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

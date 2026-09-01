# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Where the Admin API access token comes from, and the only module that holds one.

A Dev Dashboard app is issued a client ID and a client secret, never a token. The token is
minted from them, lasts 24 hours, and carries no refresh token, so it is not configuration
but state with an expiry, and something has to own it. That is
:class:`ClientCredentialsToken`, which mints on first use and again once the one it holds is
spent. :class:`StaticToken` is the other case, a token pasted into the environment, which
cannot be renewed and does not need to be.

:class:`~.admin_client.AdminGraphQLClient` asks for a token per request and keeps none, so
the same transport serves both cases without knowing which it was given.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

import httpx

from .agent_config import ShopifySettings

logger = logging.getLogger(__name__)

# Mint again this long before the stated expiry, so a request that starts just inside the
# window does not arrive just outside it.
_EXPIRY_MARGIN_S = 300.0

# Only a fallback, for a reply that omits ``expires_in``; the reply's own figure wins.
_ASSUMED_LIFETIME_S = 86_400.0


class TokenError(RuntimeError):
    """Minting failed. The message carries the status and Shopify's own reason, and never
    the client secret or a token value."""


class TokenSource(Protocol):
    """What the transport needs: a token to send, a way to say that token was refused so a
    source able to mint another does, and a close for whatever it holds open."""

    async def token(self) -> str: ...

    async def renew(self) -> str | None:
        """A freshly minted token, or None from a source that cannot mint one."""
        ...

    async def aclose(self) -> None: ...


class StaticToken:
    """A token read straight from the environment. There is nothing to renew: when Shopify
    refuses it, only the operator can supply another."""

    def __init__(self, value: str) -> None:
        self._value = value

    async def token(self) -> str:
        return self._value

    async def renew(self) -> str | None:
        return None

    async def aclose(self) -> None:
        return None


class ClientCredentialsToken:
    """A token minted from an app's client ID and secret, held until it is close to expiry.

    The credentials stay in this object, which is the only one that has them. The lock is
    what keeps a burst of concurrent requests from minting a token each on a cold start or
    in the moment after an expiry.
    """

    def __init__(
        self,
        *,
        shop_domain: str,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.shop_domain = shop_domain
        self.endpoint = f"https://{shop_domain}/admin/oauth/access_token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(timeout=timeout_s)
        self._lock = asyncio.Lock()
        self._value: str | None = None
        self._expires_at = 0.0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def token(self) -> str:
        async with self._lock:
            if self._value is not None and time.monotonic() < self._expires_at:
                return self._value
            return await self._mint()

    async def renew(self) -> str | None:
        async with self._lock:
            self._value = None
            return await self._mint()

    async def _mint(self) -> str:
        """Ask the store for a token. The caller holds the lock."""
        try:
            response = await self._http.post(
                self.endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            raise TokenError(
                f"{type(error).__name__} asking {self.shop_domain} for an access token"
            ) from error
        if response.status_code != 200:
            raise TokenError(_refusal(response))
        try:
            payload = response.json()
        except ValueError as error:
            raise TokenError(
                f"{self.shop_domain} answered the token request with a non-JSON body"
            ) from error
        value = str(payload.get("access_token") or "")
        if not value:
            raise TokenError(f"{self.shop_domain} returned no access_token")
        lifetime = float(payload.get("expires_in") or _ASSUMED_LIFETIME_S)
        self._value = value
        self._expires_at = time.monotonic() + max(lifetime - _EXPIRY_MARGIN_S, 0.0)
        # The scopes, not the token. This line is what tells an operator reading a log that
        # the token they are running with is short a scope, and it is safe to read.
        logger.info(
            "minted an Admin API token for %s, good for %.0fs, scope %s",
            self.shop_domain,
            lifetime,
            payload.get("scope") or "unreported",
        )
        return value


def _refusal(response: httpx.Response) -> str:
    """Shopify's own reason, when it gave one. ``invalid_client`` means the ID and the secret
    do not go together, or the app is not installed on this store. ``shop_not_permitted``
    means the app and the store sit in different organizations, which is what a development
    store created outside the Dev Dashboard is."""
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = str(payload.get("error_description") or payload.get("error") or "")
    return (
        f"{response.status_code} from the token request"
        + (f": {detail}" if detail else "")
        + ". Check SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET, that the app is installed on "
        "this store, and that both are in the same Shopify organization"
    )


def token_source_for(settings: ShopifySettings) -> TokenSource:
    """The source those settings describe. An app's credentials win over a pasted token,
    because a minted token is always the fresher of the two: the pasted one may be the
    24-hour token someone left in the file yesterday."""
    if settings.mints_tokens:
        return ClientCredentialsToken(
            shop_domain=settings.shop_domain,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )
    return StaticToken(settings.admin_token)

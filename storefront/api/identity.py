# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Sign in with Shop: the OAuth legs that turn a Shop account into a
buyer-linked catalog token, and the per-session store the host's auth routes and the
backend share.

Every endpoint is discovered, not hardcoded: the catalog's
``.well-known/oauth-protected-resource`` names its authorization server, that server's
``.well-known/oauth-authorization-server`` names the redeem endpoint, the catalog's
``.well-known/ucp`` names the identity-linking provider (Shop), and Shop's own
authorization-server metadata names the authorize and token endpoints. The module
constants are documented fallbacks for keys a document omits. Sign-in is
authorization-code against Shop (server-side, confidential client), and a buyer-linked
token is minted in two immediate steps: an RFC 8693 token exchange at Shop's token
endpoint (its grant expires in about a minute) redeemed through an RFC 7523 jwt-bearer
grant at the catalog's authorization server. The result lives about an hour with no
refresh token; ``refresh`` re-mints from the stored Shop token.

Tokens stay server-side: they are keyed by session id here and never reach the model,
the browser, or a log line. The buyer IP rides beside the token because the catalog
requires the pair (a bearer without ``Shopify-Buyer-IP`` is a 422); the host records
each request's client IP and ``credentials_for`` returns token and IP together, or
``None`` — anonymous — when either is missing.

:class:`AgentToken` is the second identity plane: the deployment's
own Global API token for the Token-tier ``get_order``, minted by ``client_credentials``
at the same authorization server with the same client — no buyer, no browser leg.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# The global catalog this example signs into; sign-in is per-catalog, not per-shop.
DEFAULT_CATALOG_HOST = "https://catalog.shopify.com"
# Live values (probed 2026-08), used only when discovery omits the key.
_FALLBACK_API_HOST = "https://api.shopify.com"
_FALLBACK_SHOP_HOST = "https://accounts.shop.app"

# The minimum needed for personalized search; sign-in adds openid for the code flow.
BUYER_TOKEN_SCOPE = "dev.ucp.shopping.catalog.search:read"
SIGN_IN_SCOPE = f"openid {BUYER_TOKEN_SCOPE}"
# What get_order requires (https://shopify.dev/docs/agents/orders/order-mcp). A key
# without the grant still mints; the shop then answers orders_not_allowed.
ORDERS_TOKEN_SCOPE = "read_global_api_orders"

# The Global API JWT lives sixty minutes with no refresh; re-mint five early.
_AGENT_TOKEN_LIFETIME = 55 * 60.0

_TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
_JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"

_TIMEOUT = httpx.Timeout(20.0)


def redirect_uri_from_env() -> str:
    return os.environ.get("SHOP_OAUTH_REDIRECT_URI", "http://localhost:8004/api/auth/shop/callback")


class ShopSignInError(RuntimeError):
    """A sign-in leg failed. Messages name endpoints, statuses, and OAuth error codes —
    never token values."""


@dataclass(frozen=True)
class ShopEndpoints:
    authorize_url: str  # Shop's authorization endpoint (the browser leg)
    shop_token_url: str  # Shop's token endpoint (authorization-code and RFC 8693)
    redeem_url: str  # the catalog's authorization server (RFC 7523 jwt-bearer)
    audience: str  # who the exchanged grant is for: the redeem endpoint's host


@dataclass
class _Identity:
    buyer_linked_token: str
    shop_access_token: str | None
    minted_at: float


class ShopSignIn:
    def __init__(
        self,
        catalog_host: str = DEFAULT_CATALOG_HOST,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._catalog_host = catalog_host
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT)
        self._endpoints: ShopEndpoints | None = None
        self._states: dict[str, str] = {}  # single-use CSRF state -> session id
        self._identities: dict[str, _Identity] = {}
        self._buyer_ips: dict[str, str] = {}
        self._env_minted: set[str] = set()  # sessions the SHOP_ACCESS_TOKEN shortcut tried

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def configured(self) -> bool:
        return bool(
            os.environ.get("SHOPIFY_UCP_CLIENT_ID") and os.environ.get("SHOPIFY_UCP_CLIENT_SECRET")
        )

    # -- Discovery ------------------------------------------------------------------

    async def endpoints(self) -> ShopEndpoints:
        if self._endpoints is None:
            resource = await self._document(
                f"{self._catalog_host}/.well-known/oauth-protected-resource"
            )
            api_host = (resource.get("authorization_servers") or [_FALLBACK_API_HOST])[0]
            api_metadata = await self._document(
                f"{api_host}/.well-known/oauth-authorization-server"
            )
            ucp = await self._document(f"{self._catalog_host}/.well-known/ucp")
            shop_host = _linking_provider(ucp) or _FALLBACK_SHOP_HOST
            shop_metadata = await self._document(
                f"{shop_host}/.well-known/oauth-authorization-server"
            )
            self._endpoints = ShopEndpoints(
                authorize_url=shop_metadata.get(
                    "authorization_endpoint", f"{shop_host}/oauth/authorize"
                ),
                shop_token_url=shop_metadata.get("token_endpoint", f"{shop_host}/oauth/token"),
                redeem_url=api_metadata.get("token_endpoint", f"{api_host}/auth/access_token"),
                audience=httpx.URL(api_host).host,
            )
        return self._endpoints

    async def _document(self, url: str) -> dict[str, Any]:
        response = await self._http.get(url)
        response.raise_for_status()
        return response.json()

    # -- The browser leg ------------------------------------------------------------

    def begin(self, session_id: str) -> str:
        """A single-use CSRF state bound to the session; the callback redeems it."""
        state = secrets.token_urlsafe(24)
        self._states[state] = session_id
        return state

    def consume_state(self, state: str) -> str | None:
        return self._states.pop(state, None)

    async def authorization_url(self, state: str, redirect_uri: str) -> str:
        endpoints = await self.endpoints()
        client_id, _ = self._client()
        return str(
            httpx.URL(
                endpoints.authorize_url,
                params={
                    "client_id": client_id,
                    "response_type": "code",
                    "scope": SIGN_IN_SCOPE,
                    "redirect_uri": redirect_uri,
                    "state": state,
                },
            )
        )

    async def complete(self, session_id: str, code: str, redirect_uri: str) -> None:
        """The callback's half: code -> Shop access token -> buyer-linked token."""
        endpoints = await self.endpoints()
        client_id, client_secret = self._client()
        token = await self._post_token(
            endpoints.shop_token_url,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        shop_access_token = token["access_token"]
        buyer_linked = await self._mint(shop_access_token)
        self._identities[session_id] = _Identity(buyer_linked, shop_access_token, time.time())

    # -- What the backend uses ------------------------------------------------------

    def note_buyer_ip(self, session_id: str, ip: str) -> None:
        self._buyer_ips[session_id] = ip

    def buyer_ip(self, session_id: str) -> str | None:
        """The last client IP noted for the session. Token-tier calls (``get_order``
        with the agent token) require it in ``Shopify-Buyer-IP`` just like
        buyer-linked calls."""
        return self._buyer_ips.get(session_id)

    def signed_in(self, session_id: str) -> bool:
        return session_id in self._identities

    def drop(self, session_id: str) -> None:
        self._identities.pop(session_id, None)
        self._buyer_ips.pop(session_id, None)

    async def credentials_for(self, session_id: str) -> tuple[str, str] | None:
        """The session's ``(buyer_linked_token, buyer_ip)``, or ``None`` for anonymous.
        With ``SHOP_ACCESS_TOKEN`` in the environment (headless dev), the first ask
        mints a buyer-linked token from it, skipping the browser leg."""
        identity = self._identities.get(session_id)
        env_token = os.environ.get("SHOP_ACCESS_TOKEN")
        if identity is None and env_token and session_id not in self._env_minted:
            self._env_minted.add(session_id)
            try:
                identity = _Identity(await self._mint(env_token), env_token, time.time())
            except (ShopSignInError, httpx.HTTPError):
                logger.warning("SHOP_ACCESS_TOKEN mint failed; session stays anonymous")
            else:
                self._identities[session_id] = identity
        return self._pair(session_id, identity)

    async def refresh(self, session_id: str) -> tuple[str, str] | None:
        """After a 401: re-mint from the stored Shop token; on failure the session is
        signed out and the caller continues anonymous."""
        identity = self._identities.get(session_id)
        if identity is None or identity.shop_access_token is None:
            self.drop(session_id)
            return None
        try:
            identity.buyer_linked_token = await self._mint(identity.shop_access_token)
            identity.minted_at = time.time()
        except (ShopSignInError, httpx.HTTPError):
            logger.warning("buyer-linked token re-mint failed; signing the session out")
            self.drop(session_id)
            return None
        return self._pair(session_id, identity)

    def _pair(self, session_id: str, identity: _Identity | None) -> tuple[str, str] | None:
        if identity is None:
            return None
        ip = self.buyer_ip(session_id)
        if ip is None:  # the catalog rejects a bearer without the buyer IP
            return None
        return identity.buyer_linked_token, ip

    # -- The two-step mint ----------------------------------------------------------

    async def _mint(self, shop_access_token: str) -> str:
        """RFC 8693 exchange at Shop, redeemed immediately (the grant expires in about
        a minute) through an RFC 7523 jwt-bearer grant at the catalog's server."""
        endpoints = await self.endpoints()
        client_id, client_secret = self._client()
        exchanged = await self._post_token(
            endpoints.shop_token_url,
            {
                "grant_type": _TOKEN_EXCHANGE_GRANT,
                "client_id": client_id,
                "client_secret": client_secret,
                "subject_token": shop_access_token,
                "subject_token_type": _ACCESS_TOKEN_TYPE,
                "requested_token_type": _JWT_TOKEN_TYPE,
                "audience": endpoints.audience,
            },
        )
        redeemed = await self._post_token(
            endpoints.redeem_url,
            {
                "grant_type": _JWT_BEARER_GRANT,
                "client_id": client_id,
                "client_secret": client_secret,
                "assertion": exchanged["access_token"],
                "scope": BUYER_TOKEN_SCOPE,
            },
        )
        return redeemed["access_token"]

    async def _post_token(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        response = await self._http.post(url, data=data)
        if response.status_code != 200:
            raise ShopSignInError(f"{url} answered {response.status_code}{_oauth_error(response)}")
        return response.json()

    def _client(self) -> tuple[str, str]:
        client_id = os.environ.get("SHOPIFY_UCP_CLIENT_ID")
        client_secret = os.environ.get("SHOPIFY_UCP_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise ShopSignInError(
                "Sign in with Shop needs SHOPIFY_UCP_CLIENT_ID and SHOPIFY_UCP_CLIENT_SECRET "
                "in the environment or a .env file."
            )
        return client_id, client_secret


class AgentToken:
    """The deployment's Global API token, one per process, no buyer involved:
    ``client_credentials`` with the same client at the same authorization server the
    buyer-linked redeem uses, cached fifty-five of its sixty minutes. ``bearer``
    answers ``None`` when the credentials are missing or the mint fails, so the
    caller degrades instead of crashing. The token never reaches the model, the
    browser, or a log line."""

    def __init__(self, signin: ShopSignIn, scope: str = ORDERS_TOKEN_SCOPE) -> None:
        self._signin = signin
        self._scope = scope
        self._minted: tuple[str, float] | None = None

    async def bearer(self) -> str | None:
        if not self._signin.configured:
            return None
        if self._minted is not None and time.time() - self._minted[1] < _AGENT_TOKEN_LIFETIME:
            return self._minted[0]
        try:
            endpoints = await self._signin.endpoints()
            client_id, client_secret = self._signin._client()
            token = await self._signin._post_token(
                endpoints.redeem_url,
                {
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": self._scope,
                },
            )
        except (ShopSignInError, httpx.HTTPError):
            logger.warning("agent token mint failed; order tools degrade")
            return None
        self._minted = (token["access_token"], time.time())
        return self._minted[0]


def _linking_provider(ucp: dict[str, Any]) -> str | None:
    """The identity-linking provider's host from a ``.well-known/ucp`` document."""
    capability = (ucp.get("ucp", {}).get("capabilities", {})).get(
        "dev.ucp.common.identity_linking"
    ) or [{}]
    providers = (capability[0].get("config") or {}).get("providers") or {}
    for entries in providers.values():
        if entries and entries[0].get("auth_url"):
            return entries[0]["auth_url"]
    return None


def _oauth_error(response: httpx.Response) -> str:
    """The OAuth error code from an error body, log-safe (never a token)."""
    try:
        code = response.json().get("error")
    except ValueError:
        return ""
    return f" ({code})" if isinstance(code, str) else ""

# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""A fake of the sign-in surface: the four discovery documents (shaped like the live
ones) and both token endpoints, behind an ``httpx.MockTransport``. It records every
request so tests can assert the grant sequence and the form fields; ``fail_grants``
makes a leg answer 400."""

from __future__ import annotations

import httpx

DISCOVERY = {
    "https://catalog.shopify.com/.well-known/oauth-protected-resource": {
        "resource": "https://catalog.shopify.com",
        "authorization_servers": ["https://api.shopify.com"],
    },
    "https://api.shopify.com/.well-known/oauth-authorization-server": {
        "issuer": "https://api.shopify.com",
        "token_endpoint": "https://api.shopify.com/auth/access_token",
        "grant_types_supported": ["urn:ietf:params:oauth:grant-type:jwt-bearer"],
    },
    "https://catalog.shopify.com/.well-known/ucp": {
        "ucp": {
            "version": "2026-04-08",
            "capabilities": {
                "dev.ucp.common.identity_linking": [
                    {
                        "version": "2026-04-08",
                        "config": {
                            "providers": {
                                "app.shop.accounts": [
                                    {"type": "oauth2", "auth_url": "https://accounts.shop.app"}
                                ]
                            }
                        },
                    }
                ]
            },
        }
    },
    "https://accounts.shop.app/.well-known/oauth-authorization-server": {
        "issuer": "https://accounts.shop.app",
        "authorization_endpoint": "https://accounts.shop.app/oauth/authorize",
        "token_endpoint": "https://accounts.shop.app/oauth/token",
    },
}

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"


class FakeShopOAuth:
    def __init__(self) -> None:
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict[str, str]]] = []  # (url, form fields)
        self.minted = 0  # buyer-linked tokens issued, numbering their values
        self.fail_grants: set[str] = set()

    @property
    def grants(self) -> list[str]:
        return [form["grant_type"] for _, form in self.posts]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET":
            self.gets.append(url)
            return httpx.Response(200, json=DISCOVERY[url])
        form = dict(httpx.QueryParams(request.content.decode()))
        self.posts.append((url, form))
        grant = form["grant_type"]
        if grant in self.fail_grants:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if grant == "authorization_code":
            return httpx.Response(200, json={"access_token": "shop-token"})
        if grant == TOKEN_EXCHANGE_GRANT:
            return httpx.Response(
                200,
                json={
                    "access_token": f"grant-jwt-{len(self.posts)}",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:jwt",
                },
            )
        if grant == JWT_BEARER_GRANT:
            self.minted += 1
            return httpx.Response(
                200, json={"access_token": f"buyer-token-{self.minted}", "expires_in": 3600}
            )
        if grant == "client_credentials":
            # The Global API mint's live body carries only these two keys.
            self.minted += 1
            return httpx.Response(
                200, json={"access_token": f"agent-token-{self.minted}", "token_type": "bearer"}
            )
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The token: where it comes from, when it is minted again, and what a caller is told when
minting fails. These are the only tests that drive ``AdminGraphQLClient`` over HTTP rather
than over a fake executor, because the 401 path is HTTP behaviour and nothing else reaches
it.
"""

from __future__ import annotations

import asyncio
import dataclasses

import httpx
import pytest

from merchant.api.admin_client import AdminAPIError, AdminGraphQLClient
from merchant.api.admin_token import (
    ClientCredentialsToken,
    StaticToken,
    TokenError,
    token_source_for,
)
from merchant.api.agent_config import ShopifySettings

SHOP = "acme-supply.myshopify.com"
SECRET = "the-client-secret-that-must-not-appear-anywhere"
PING = "query Ping { shop { name } }"


class FakeOAuth:
    """The token endpoint. ``minted`` counts the requests, which is how the caching and the
    single-flight behaviour are observed, and ``lifetime`` is what the reply claims."""

    def __init__(self, *, lifetime: int = 86_399, status: int = 200, body: object = None) -> None:
        self.lifetime = lifetime
        self.status = status
        self.body = body
        self.minted = 0
        self.sent: list[dict[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.minted += 1
        self.sent.append(dict(httpx.QueryParams(request.content.decode())))
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid_client"})
        return httpx.Response(
            200,
            json={
                "access_token": f"shpat_minted_{self.minted}",
                "scope": "write_products,read_orders",
                "expires_in": self.lifetime,
            },
        )

    def source(self, **kwargs: object) -> ClientCredentialsToken:
        return ClientCredentialsToken(
            shop_domain=SHOP,
            client_id="the-client-id",
            client_secret=SECRET,
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)),
            **kwargs,  # type: ignore[arg-type]
        )


async def test_it_mints_once_and_reuses_until_the_expiry_nears() -> None:
    """The whole reason the token is state rather than configuration. Minting per request
    would work and would also spend a request of the store's rate limit on every read."""
    oauth = FakeOAuth()
    source = oauth.source()

    first = await source.token()

    assert first == "shpat_minted_1"
    assert [await source.token() for _ in range(3)] == [first] * 3
    assert oauth.minted == 1
    assert oauth.sent[0] == {
        "grant_type": "client_credentials",
        "client_id": "the-client-id",
        "client_secret": SECRET,
    }
    await source.aclose()


async def test_it_mints_again_once_the_held_token_is_near_its_expiry() -> None:
    """A short-lived token stands in for the passage of 24 hours. The margin is subtracted
    from the stated lifetime, so a reply claiming less than the margin is already stale and
    the next call mints."""
    oauth = FakeOAuth(lifetime=1)
    source = oauth.source()

    assert await source.token() == "shpat_minted_1"
    assert await source.token() == "shpat_minted_2"
    assert oauth.minted == 2
    await source.aclose()


async def test_concurrent_callers_mint_one_token_between_them() -> None:
    """A cold start serves several requests at once. Without the lock each would mint, and
    each token Shopify issues invalidates the one before it."""
    oauth = FakeOAuth()
    source = oauth.source()

    tokens = await asyncio.gather(*(source.token() for _ in range(8)))

    assert set(tokens) == {"shpat_minted_1"}
    assert oauth.minted == 1
    await source.aclose()


async def test_renew_discards_the_held_token_and_asks_for_another() -> None:
    oauth = FakeOAuth()
    source = oauth.source()

    assert await source.token() == "shpat_minted_1"
    assert await source.renew() == "shpat_minted_2"
    assert await source.token() == "shpat_minted_2"
    await source.aclose()


async def test_a_refused_mint_names_shopifys_reason_and_never_the_secret() -> None:
    """What an operator sees when the ID and the secret do not go together, or the app is
    not installed. The message has to be actionable, and it has to be safe to paste into an
    issue."""
    oauth = FakeOAuth(status=401)
    source = oauth.source()

    with pytest.raises(TokenError) as raised:
        await source.token()

    message = str(raised.value)
    assert "invalid_client" in message
    assert "SHOPIFY_CLIENT_ID" in message
    assert SECRET not in message
    await source.aclose()


@pytest.mark.parametrize(
    ("body", "expected"),
    [({"scope": "read_orders"}, "no access_token"), ({"access_token": ""}, "no access_token")],
)
async def test_a_reply_without_a_token_is_an_error_not_an_empty_header(
    body: dict[str, object], expected: str
) -> None:
    oauth = FakeOAuth(body=body)
    source = oauth.source()

    with pytest.raises(TokenError, match=expected):
        await source.token()

    await source.aclose()


async def test_a_transport_failure_names_the_store_and_not_the_credentials() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    source = ClientCredentialsToken(
        shop_domain=SHOP,
        client_id="the-client-id",
        client_secret=SECRET,
        client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)),
    )

    with pytest.raises(TokenError) as raised:
        await source.token()

    assert SHOP in str(raised.value)
    assert SECRET not in str(raised.value)
    await source.aclose()


async def test_a_static_token_is_returned_as_given_and_cannot_be_renewed() -> None:
    source = StaticToken("shpat_pasted_in")

    assert await source.token() == "shpat_pasted_in"
    assert await source.renew() is None
    await source.aclose()


def test_client_credentials_win_over_a_pasted_token(settings: ShopifySettings) -> None:
    """Both can be set at once, and the file someone edited yesterday is the one holding a
    spent token. So the credentials that can mint a fresh one decide."""
    assert isinstance(token_source_for(settings), StaticToken)

    both = dataclasses.replace(settings, client_id="the-client-id", client_secret=SECRET)

    assert both.mints_tokens is True
    assert isinstance(token_source_for(both), ClientCredentialsToken)


def test_a_settings_repr_carries_no_credential(settings: ShopifySettings) -> None:
    """The one line of defence against a credential reaching a log by way of a debug print
    of the settings object."""
    both = dataclasses.replace(settings, client_id="the-client-id", client_secret=SECRET)

    printed = repr(both)

    assert SHOP in printed
    assert SECRET not in printed
    assert "the-client-id" not in printed
    assert "not-a-real-token" not in printed


class FakeAdminHTTP:
    """The GraphQL endpoint, refusing the first ``refusals`` requests with a 401 and
    answering after that. ``tokens`` records the header each request carried, which is how
    the retry is shown to use the new token rather than the spent one."""

    def __init__(self, *, refusals: int = 0, status_after: int = 200) -> None:
        self.refusals = refusals
        self.status_after = status_after
        self.tokens: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.tokens.append(request.headers.get("X-Shopify-Access-Token", ""))
        if len(self.tokens) <= self.refusals:
            return httpx.Response(401, json={"errors": "Invalid API key or access token"})
        if self.status_after != 200:
            return httpx.Response(self.status_after, json={"errors": "no"})
        return httpx.Response(200, json={"data": {"shop": {"name": "ACME Supply Co."}}})

    def client(self, source: object) -> AdminGraphQLClient:
        return AdminGraphQLClient(
            shop_domain=SHOP,
            token_source=source,  # type: ignore[arg-type]
            api_version="2026-07",
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)),
        )


async def test_a_spent_token_is_minted_again_and_the_request_retried() -> None:
    """The behaviour this whole module exists for. A 24-hour expiry crosses a running
    process, and the request that lands on the far side of it has to succeed."""
    oauth = FakeOAuth()
    api = FakeAdminHTTP(refusals=1)
    client = api.client(oauth.source())

    data = await client.execute(PING)

    assert data == {"shop": {"name": "ACME Supply Co."}}
    assert api.tokens == ["shpat_minted_1", "shpat_minted_2"]
    await client.aclose()


async def test_a_store_that_refuses_every_token_fails_rather_than_looping() -> None:
    oauth = FakeOAuth()
    api = FakeAdminHTTP(refusals=99)
    client = api.client(oauth.source())

    with pytest.raises(AdminAPIError, match="freshly minted"):
        await client.execute(PING)

    assert oauth.minted == 2
    await client.aclose()


async def test_a_rejected_pasted_token_says_how_to_stop_pasting_them() -> None:
    """A static source cannot renew, so the 401 is the operator's to fix, and the message
    points at the path that does not expire."""
    api = FakeAdminHTTP(refusals=99)
    client = api.client(StaticToken("shpat_expired"))

    with pytest.raises(AdminAPIError) as raised:
        await client.execute(PING)

    assert "SHOPIFY_CLIENT_ID" in str(raised.value)
    assert api.tokens == ["shpat_expired"]
    await client.aclose()


async def test_a_403_is_reported_as_a_missing_scope_not_an_expiry() -> None:
    """The two statuses mean different things and minting cannot fix a 403, so the message
    must not send the operator looking at expiry."""
    oauth = FakeOAuth()
    api = FakeAdminHTTP(status_after=403)
    client = api.client(oauth.source())

    with pytest.raises(AdminAPIError) as raised:
        await client.execute(PING)

    assert "scope" in str(raised.value)
    assert oauth.minted == 1
    await client.aclose()


async def test_closing_the_client_closes_the_token_source_with_it() -> None:
    """One close, not two. The source holds a connection of its own, and every call site in
    the example closes the transport and nothing else."""

    class Spy(StaticToken):
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    source = Spy("shpat_pasted_in")
    client = FakeAdminHTTP().client(source)

    await client.aclose()

    assert source.closed is True

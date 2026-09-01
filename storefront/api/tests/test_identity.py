# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Sign in with Shop over the fake OAuth surface: the discovered endpoint chain, the
CSRF state's lifecycle, the three token legs in order, the token-and-IP pairing, the
headless env shortcut, and the re-mint path."""

import httpx

from storefront.api.identity import BUYER_TOKEN_SCOPE, SIGN_IN_SCOPE

from .oauth_stub import JWT_BEARER_GRANT, TOKEN_EXCHANGE_GRANT

REDIRECT = "http://localhost:8004/api/auth/shop/callback"
IP = "203.0.113.7"


async def test_discovery_resolves_the_endpoint_chain_and_caches_it(signin, oauth):
    endpoints = await signin.endpoints()
    assert endpoints.authorize_url == "https://accounts.shop.app/oauth/authorize"
    assert endpoints.shop_token_url == "https://accounts.shop.app/oauth/token"
    assert endpoints.redeem_url == "https://api.shopify.com/auth/access_token"
    assert endpoints.audience == "api.shopify.com"
    assert len(oauth.gets) == 4
    await signin.endpoints()
    assert len(oauth.gets) == 4


async def test_the_authorization_url_carries_client_scope_state_and_redirect(signin):
    url = httpx.URL(await signin.authorization_url("state-1", REDIRECT))
    assert url.host == "accounts.shop.app"
    assert dict(url.params) == {
        "client_id": "test-client-id",
        "response_type": "code",
        "scope": SIGN_IN_SCOPE,
        "redirect_uri": REDIRECT,
        "state": "state-1",
    }


def test_the_state_is_single_use_and_bound_to_its_session(signin):
    state = signin.begin("s-1")
    assert signin.consume_state(state) == "s-1"
    assert signin.consume_state(state) is None
    assert signin.consume_state("forged-state") is None


async def test_complete_runs_code_then_exchange_then_immediate_redeem(signin, oauth):
    await signin.complete("s-1", "code-1", REDIRECT)
    assert oauth.grants == ["authorization_code", TOKEN_EXCHANGE_GRANT, JWT_BEARER_GRANT]

    code_url, code_form = oauth.posts[0]
    assert code_url == "https://accounts.shop.app/oauth/token"
    assert (code_form["code"], code_form["redirect_uri"]) == ("code-1", REDIRECT)

    exchange_url, exchange = oauth.posts[1]
    assert exchange_url == "https://accounts.shop.app/oauth/token"
    assert exchange["subject_token"] == "shop-token"
    assert exchange["audience"] == "api.shopify.com"
    assert exchange["requested_token_type"] == "urn:ietf:params:oauth:token-type:jwt"

    redeem_url, redeem = oauth.posts[2]
    assert redeem_url == "https://api.shopify.com/auth/access_token"
    assert redeem["assertion"] == "grant-jwt-2"  # what the exchange (the second post) answered
    assert redeem["scope"] == BUYER_TOKEN_SCOPE
    assert signin.signed_in("s-1")


async def test_credentials_pair_the_token_with_the_recorded_buyer_ip(signin):
    await signin.complete("s-1", "code-1", REDIRECT)
    assert await signin.credentials_for("s-1") is None  # no IP yet: stay anonymous
    signin.note_buyer_ip("s-1", IP)
    assert await signin.credentials_for("s-1") == ("buyer-token-1", IP)
    assert await signin.credentials_for("s-other") is None


async def test_the_env_token_shortcut_skips_the_browser_leg(signin, oauth, monkeypatch):
    monkeypatch.setenv("SHOP_ACCESS_TOKEN", "env-shop-token")
    signin.note_buyer_ip("s-1", IP)
    assert await signin.credentials_for("s-1") == ("buyer-token-1", IP)
    assert "authorization_code" not in oauth.grants
    assert oauth.posts[0][1]["subject_token"] == "env-shop-token"


async def test_a_failing_env_mint_is_tried_once_then_stays_anonymous(signin, oauth, monkeypatch):
    monkeypatch.setenv("SHOP_ACCESS_TOKEN", "env-shop-token")
    oauth.fail_grants.add(TOKEN_EXCHANGE_GRANT)
    signin.note_buyer_ip("s-1", IP)
    assert await signin.credentials_for("s-1") is None
    attempts = len(oauth.posts)
    assert await signin.credentials_for("s-1") is None
    assert len(oauth.posts) == attempts


async def test_refresh_re_mints_and_a_failure_signs_the_session_out(signin, oauth):
    await signin.complete("s-1", "code-1", REDIRECT)
    signin.note_buyer_ip("s-1", IP)
    assert await signin.refresh("s-1") == ("buyer-token-2", IP)
    oauth.fail_grants.add(TOKEN_EXCHANGE_GRANT)
    assert await signin.refresh("s-1") is None
    assert not signin.signed_in("s-1")
    assert await signin.credentials_for("s-1") is None

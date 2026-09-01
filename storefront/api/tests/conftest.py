# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Every fixture here runs over ``replay_transport`` — recorded responses from
``data/``, never the live shop."""

import httpx
import pytest

from storefront.api.identity import ShopSignIn
from storefront.api.shopify_backend import ShopifyStorefrontBackend
from storefront.api.ucp_client import UcpClient
from shopping_agent import ShoppingSessionContext, ShoppingSessionState

from .oauth_stub import FakeShopOAuth
from .replay import replay_transport

SEARCH_QUERY = "shirt"


@pytest.fixture
def client() -> UcpClient:
    return UcpClient(http=httpx.AsyncClient(transport=replay_transport()))


@pytest.fixture
def oauth() -> FakeShopOAuth:
    return FakeShopOAuth()


@pytest.fixture
def signin(oauth, monkeypatch) -> ShopSignIn:
    """Sign-in over the fake OAuth surface, with fake client credentials in the
    environment (the real ones never enter a test)."""
    monkeypatch.setenv("SHOPIFY_UCP_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("SHOPIFY_UCP_CLIENT_SECRET", "test-client-secret")
    monkeypatch.delenv("SHOP_ACCESS_TOKEN", raising=False)
    return ShopSignIn(http=httpx.AsyncClient(transport=oauth.transport()))


@pytest.fixture
def backend(client) -> ShopifyStorefrontBackend:
    return ShopifyStorefrontBackend(client)


@pytest.fixture
def session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="s-1", user_id="guest")


@pytest.fixture
def state() -> ShoppingSessionState:
    return ShoppingSessionState()

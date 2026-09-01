# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for the Shopify example's suite.

Every test here runs the real backend, cache, alert rules, and writer against
:class:`FakeAdmin`. Nothing reaches the network, and nothing needs a store: the suite has
to stay hermetic, because it runs in the same ``pytest`` invocation as the rest of the
repository and on machines that have no Shopify credentials at all.

These fixtures are local rather than the shared ``demo_common.tests.fixtures``, which build
a storefront backend. This example has no storefront half — the store is the real one — so
there is nothing for those fixtures to describe.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from merchant_agent import MerchantSessionContext
from merchant.api.agent_config import ShopifySettings, build_merchant_config
from merchant.api.shopify_backend import ShopifyMerchantBackend

from .fake_admin import FakeAdmin


@pytest.fixture
def admin() -> FakeAdmin:
    return FakeAdmin()


@pytest.fixture
def settings() -> ShopifySettings:
    """A store that is configured but never contacted. The token is a placeholder: the
    fake transport never looks at it, which is itself the point — no layer above the
    transport reads it."""
    return ShopifySettings(
        shop_domain="acme-supply.myshopify.com",
        client_id="",
        client_secret="",
        admin_token="not-a-real-token",
        api_version="2026-07",
        local_store=False,
        operator="Dana",
        store_name=None,
        low_stock_default=8,
        fulfilment_sla_days=3,
        shopifyql_enabled=True,
    )


@pytest.fixture
def config():
    return build_merchant_config("ACME Supply Co.")


@pytest.fixture
def backend(admin: FakeAdmin, settings: ShopifySettings, config) -> ShopifyMerchantBackend:
    return ShopifyMerchantBackend(admin, settings, config)


@pytest.fixture
def session() -> MerchantSessionContext:
    return MerchantSessionContext(
        session_id="ms-1",
        merchant_id="acme-supply.myshopify.com",
        operator="Dana",
    )


@pytest.fixture
def no_shopifyql(admin: FakeAdmin, settings: ShopifySettings, config) -> ShopifyMerchantBackend:
    """The same backend with ShopifyQL disabled by configuration, so the orders-derived
    path is exercised deliberately rather than by whatever the fixture store answers."""
    disabled = ShopifySettings(**{**settings.__dict__, "shopifyql_enabled": False})
    return ShopifyMerchantBackend(admin, disabled, config)


def router_session_store(app: FastAPI) -> Any:
    """The merchant router's session store, which lives in a closure.

    A test that needs to give a session the provenance the assistant's own staging would have
    left has to reach the store the routes actually use, and the shared contract suite reaches
    it the same way. FastAPI 0.137 wraps routes in contexts; both shapes are handled so the
    suite does not pin a FastAPI version."""
    routes: Any = app.routes
    try:
        from fastapi.routing import iter_route_contexts  # noqa: PLC0415
    except ImportError:  # FastAPI < 0.137
        pass
    else:
        routes = list(iter_route_contexts(app.routes))
    apply_route = next(
        route
        for route in routes
        if isinstance(getattr(route, "original_route", route), APIRoute)
        and route.path.endswith("/changes/{change_id}/apply")
    )
    dependency = [dep.call for dep in apply_route.dependant.dependencies][-1]
    return inspect.getclosurevars(dependency).nonlocals["store"]

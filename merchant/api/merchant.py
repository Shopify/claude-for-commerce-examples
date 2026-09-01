# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The portal: the shared merchant routes over a live Shopify store.

``build_merchant_router`` is used unchanged, which is the point. The approval gate, the SSE
streaming, the memory routes, and the presentation enrichment are all the shared ones; what
this module adds is the store's identity, the Admin API client, and the two extra keys the
portal's home page reads.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from commerce_common.memory import MemoryStore
from demo_common import MerchantIdentity, build_merchant_router
from merchant_agent_runtime import MerchantAgent

from .admin_client import AdminExecutor, AdminGraphQLClient
from .admin_token import token_source_for
from .agent_config import DATA_DIR, SKILLS_DIR, ShopifySettings, build_merchant_config
from .local_store import LocalStore
from .shopify_backend import ShopifyMerchantBackend
from .store_view import ShopifyStoreView


@dataclass(frozen=True)
class MerchantPortal:
    """What ``main.py`` needs to mount the portal and to shut it down cleanly."""

    router: APIRouter
    backend: ShopifyMerchantBackend
    # None when there is no HTTP transport to close: a local store, or a transport the
    # caller supplied, which is how the suite runs these routes against canned responses.
    client: AdminGraphQLClient | None


def create_merchant_portal(
    settings: ShopifySettings,
    memory_store: MemoryStore,
    *,
    executor: AdminExecutor | None = None,
) -> MerchantPortal:
    """One store's portal. The store's credential goes into the token source the Admin
    client holds and nowhere else: the backend holds the client, and the agent holds the
    backend, so no layer above the transport can read it and no tool result can carry it.

    Three transports reach the same routes. A Shopify store gets ``AdminGraphQLClient``;
    ``settings.local_store`` gets ``LocalStore``, built from the same ``data/seed.json`` the
    real seeder sends, so the example runs with no account and no network; and an explicit
    ``executor`` replaces both, which is how the suite exercises these routes rather than a
    second copy of the wiring."""
    client: AdminGraphQLClient | None = None
    if executor is None:
        if settings.local_store:
            executor = LocalStore.from_seed(
                DATA_DIR / "seed.json", shop_domain=settings.shop_domain
            )
        else:
            client = AdminGraphQLClient(
                shop_domain=settings.shop_domain,
                token_source=token_source_for(settings),
                api_version=settings.api_version,
            )
    config = build_merchant_config(settings.store_name or settings.shop_domain)
    transport = executor if executor is not None else client
    assert transport is not None  # one of the three branches above always set one
    backend = ShopifyMerchantBackend(transport, settings, config)
    store_view = ShopifyStoreView(backend)
    agent = MerchantAgent(
        backend=backend,
        skills_dir=SKILLS_DIR,
        config=config,
        memory_store=memory_store,
    )
    router = build_merchant_router(
        storefront=store_view,
        backend=backend,
        agent=agent,
        identity=MerchantIdentity(merchant_id=settings.merchant_id, operator=settings.operator),
        example_dir="merchant",
        overview_extras=lambda: {
            "trends": backend.kpi_trends(),
            "insights": backend.home_insights(),
            "shop_domain": settings.shop_domain,
            # Which store the figures came from. The portal has to say this: the same page
            # over the local store would otherwise read as a claim about a Shopify store.
            "store_kind": "local" if settings.local_store else "shopify",
        },
    )
    return MerchantPortal(router=router, backend=backend, client=client)

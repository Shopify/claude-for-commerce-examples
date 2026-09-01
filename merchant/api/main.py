# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The Shopify merchant example's API: the merchant agent over one live store.

    uvicorn merchant.api.main:app --reload --port 8005

This example is merchant-only. There is no storefront half to mount, because the storefront
is the real one: the products, orders, and inventory read here belong to a Shopify store,
and an approved change is written back to it.

Credentials come from ``merchant/.env`` (copy ``merchant/.env.example``), with the
repo-root ``.env`` read after it for the chat credential the two examples share. Without
them the app still starts and ``/api/merchant/health`` says what is missing, so the process
is diagnosable rather than dead — every other route needs a store to talk to and reports
the same thing.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from commerce_common.memory import JsonFileMemoryStore
from demo_common import load_demo_env
from demo_common.host import build_app

from .agent_config import DATA_DIR, EXAMPLE_ROOT, MissingCredentials, load_settings
from .merchant import create_merchant_portal

logger = logging.getLogger(__name__)

# merchant/.env first, then the repo-root .env, which carries the shared chat credential.
load_demo_env(EXAMPLE_ROOT)


def _unconfigured_app(reason: str) -> FastAPI:
    """The app a missing credential leaves behind: it serves the health route and says
    exactly what to set, rather than failing at import and leaving a stack trace."""
    app = build_app(title="Shopify merchant example API (unconfigured)")

    @app.get("/api/merchant/health")
    async def health() -> dict:
        return {"ok": False, "role": "merchant", "error": reason}

    logger.warning("Shopify credentials are not set: %s", reason)
    return app


def create_app() -> FastAPI:
    try:
        settings = load_settings()
    except MissingCredentials as error:
        return _unconfigured_app(str(error))

    portal = create_merchant_portal(settings, JsonFileMemoryStore(DATA_DIR / ".memory-store.json"))
    app = build_app(title="Shopify merchant example API")
    # The shared app checks the Anthropic credentials in its own lifespan, so the store
    # warm-up nests inside it rather than replacing it.
    shared_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(scope: FastAPI) -> AsyncIterator[None]:
        """Read the store's profile, catalog, and orders once at startup, so the portal's
        first paint is served from the caches and the token is proven before a request
        depends on it. A store that cannot be reached is logged and the app still starts:
        the health route is then the place that says why."""
        async with shared_lifespan(scope):
            try:
                await portal.backend.warm()
                logger.info(
                    "connected to %s (%d products in the catalog)",
                    portal.backend.store_name,
                    len(portal.backend.catalog.cached()),
                )
            except Exception:
                logger.exception("could not read %s at startup", settings.shop_domain)
            try:
                yield
            finally:
                if portal.client is not None:
                    await portal.client.aclose()

    app.router.lifespan_context = lifespan
    app.include_router(portal.router, prefix="/api/merchant")
    return app


app = create_app()

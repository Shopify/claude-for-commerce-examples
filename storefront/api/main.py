# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Shopify demo store example API: the repo's shopping agent over a live Shopify shop's
UCP tools (demostore.mock.shop unless SHOP_DOMAIN says otherwise), behind the shared
storefront routes.

    uvicorn storefront.api.main:app --app-dir examples --reload --port 8004

Sessions start as guests, memory is in-process, and /api/cart stages a non-empty cart
as a UCP checkout lazily and carries the handoff checkout_url so the UI can hand the
customer to Shopify's checkout page. Sign in with
Shop is four routes: /api/auth/shop/start sends the browser to Shop's consent
page with a single-use state bound to the session, /api/auth/shop/callback turns the
returned code into a buyer-linked token held server-side and redirects to the web app,
/api/auth/status says whether the session is signed in, and /api/auth/signout drops the
session's identity. Each session-scoped request also records the caller's IP, because a
buyer-token catalog call must carry it. /api/brand serves the shop's brand settings for
the web surface's theming, and startup warms the product grid's display cache from the
same tokenless Storefront API (catalog_warmup.py; CATALOG_WARMUP=0 disables).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from commerce_common.memory import InMemoryMemoryStore
from demo_common import (
    REPO_ROOT,
    SESSION_HEADER,
    CartAddRequest,
    MemorySeeder,
    build_storefront_host,
    load_demo_env,
)
from shopping_agent_runtime import ShoppingAgent

from .agent_config import build_shopping_config
from .brand import BrandSource
from .catalog_warmup import warm_catalog
from .identity import AgentToken, ShopSignIn, ShopSignInError, redirect_uri_from_env
from .shopify_backend import ShopifyStorefrontBackend
from .ucp_client import UcpClient, shop_domain_from_env

logger = logging.getLogger(__name__)

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = EXAMPLE_ROOT / "data"

load_demo_env(EXAMPLE_ROOT)

store_name = shop_domain_from_env()
signin = ShopSignIn()
brand_source = BrandSource(store_name)
backend = ShopifyStorefrontBackend(
    UcpClient(store_name),
    store_name=store_name,
    identity=signin,
    agent_token=AgentToken(signin),
)
agent = ShoppingAgent(
    backend=backend,
    skills_dir=REPO_ROOT / "vendor" / "skills" / "shopping",
    config=build_shopping_config(store_name),
    memory_store=InMemoryMemoryStore(),
)


async def cart_extras(record) -> dict:
    """Every cart payload carries the session's checkout handoff link; a non-empty
    cart is staged as a UCP checkout lazily on the way out (``checkout_url_for``)."""
    return {"checkout_url": await backend.checkout_url_for(record.session_id)}


def note_buyer_ip(request: Request) -> None:
    """A buyer-token catalog call must carry the buyer's IP, so every session-scoped
    request records its caller's address (a deployment behind a proxy would read its
    forwarded-for header instead)."""
    session_id = request.headers.get(SESSION_HEADER)
    if session_id and request.client:
        signin.note_buyer_ip(session_id, request.client.host)


host = build_storefront_host(
    title="Shopify demo store API",
    example_root=EXAMPLE_ROOT,
    backend=backend,
    agent=agent,
    # No seed file: every session is an anonymous guest.
    memory_seeder=MemorySeeder(DATA_DIR / "memory-seed.json"),
    cart_extras=cart_extras,
    before_turn=note_buyer_ip,
)
app = host.app

# Warm the grid's display cache in the background once the app starts; warm_catalog
# is failure-tolerant and honors CATALOG_WARMUP=0 itself. The shared host installs
# its own lifespan (which makes plain startup handlers inert), so the warm-up wraps
# it; the reference keeps the task alive (asyncio holds tasks weakly).
_host_lifespan = app.router.lifespan_context
_warmup_task: asyncio.Task | None = None


@asynccontextmanager
async def _lifespan_with_warmup(app_) -> AsyncIterator[None]:
    global _warmup_task
    async with _host_lifespan(app_):
        _warmup_task = asyncio.create_task(warm_catalog(backend, store_name))
        yield


app.router.lifespan_context = _lifespan_with_warmup


@app.post("/api/cart/add")
async def cart_add(request: CartAddRequest, record: host.CurrentSession, raw: Request) -> dict:
    note_buyer_ip(raw)
    return await host.direct_add(
        record,
        request,
        note="Customer tapped the add-to-cart button on {title} ({product_id}), quantity {quantity}.",
    )


@app.get("/api/auth/shop/start")
async def shop_signin_start(request: Request, session_id: str | None = None) -> RedirectResponse:
    """Send the browser to Shop's consent page. A browser navigation cannot carry the
    session header, so the session id may come as a query parameter instead."""
    sid = session_id or request.headers.get(SESSION_HEADER)
    if not sid or host.sessions.read_state(sid) is None:
        raise HTTPException(status_code=401, detail="Start a session first (POST /api/session)")
    if not signin.configured:
        raise HTTPException(
            status_code=503,
            detail="Sign in with Shop needs SHOPIFY_UCP_CLIENT_ID and "
            "SHOPIFY_UCP_CLIENT_SECRET in the environment or a .env file.",
        )
    if request.client:
        signin.note_buyer_ip(sid, request.client.host)
    try:
        url = await signin.authorization_url(signin.begin(sid), redirect_uri_from_env())
    except (ShopSignInError, httpx.HTTPError) as failure:
        raise HTTPException(status_code=502, detail=str(failure)) from None
    return RedirectResponse(url, status_code=302)


def _web_app_redirect(flag: str) -> RedirectResponse:
    base = os.environ.get("WEB_APP_URL", "http://localhost:3005")
    return RedirectResponse(str(httpx.URL(base).copy_add_param("shop_signin", flag)), 302)


@app.get("/api/auth/shop/callback")
async def shop_signin_callback(
    request: Request, state: str, code: str | None = None, error: str | None = None
) -> RedirectResponse:
    """The browser arrives mid top-level navigation, so every outcome redirects to the
    web app (WEB_APP_URL) with a ``shop_signin`` query flag instead of a status code."""
    session_id = signin.consume_state(state)
    if session_id is None or error or not code:
        reason = "unknown or already-used state" if session_id is None else error or "no code"
        logger.warning("Shop sign-in failed: %s", reason)
        return _web_app_redirect("error")
    if request.client:
        signin.note_buyer_ip(session_id, request.client.host)
    try:
        await signin.complete(session_id, code, redirect_uri_from_env())
    except (ShopSignInError, httpx.HTTPError) as failure:
        # ShopSignInError messages carry endpoints and statuses, never tokens.
        logger.warning("Shop sign-in failed: %s", failure)
        return _web_app_redirect("error")
    return _web_app_redirect("ok")


@app.get("/api/auth/status")
async def auth_status(request: Request, record: host.CurrentSession) -> dict:
    note_buyer_ip(request)
    return {"signed_in": signin.signed_in(record.session_id)}


@app.post("/api/auth/signout")
async def auth_signout(record: host.CurrentSession) -> dict:
    """Drop the session's Shop identity and its buyer-linked token; the session
    continues as a guest."""
    signin.drop(record.session_id)
    return {"signed_in": False}


@app.get("/api/brand")
async def brand(request: Request) -> dict:
    """The shop's brand settings for the web surface's theming, via the tokenless
    Storefront API (cached in-process)."""
    return await brand_source.brand(request.client.host if request.client else None)

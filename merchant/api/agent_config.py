# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The Shopify deployment's settings and its merchant agent config; the only module in
this example that reads the environment.

    SHOPIFY_LOCAL_STORE        1 to run against api/local_store.py: no store, no token,
                               no network. Every other variable below is then optional.
    SHOPIFY_SHOP_DOMAIN        acme-supply.myshopify.com
    SHOPIFY_ADMIN_TOKEN        shpat_… (server-side only; never sent to the model)
    SHOPIFY_ADMIN_API_VERSION  Admin GraphQL version, pinned (default below)
    SHOPIFY_OPERATOR           who the ledger stamps on staged and applied changes
    SHOPIFY_STORE_NAME         display name; the shop's own name when unset
    SHOPIFY_LOW_STOCK_DEFAULT  fallback low-stock threshold
    SHOPIFY_FULFILMENT_SLA_DAYS days before an unfulfilled order counts as delayed
    SHOPIFY_DISABLE_SHOPIFYQL   1 to skip ShopifyQL and use the orders scan for metrics
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from demo_common import host_approval_default
from merchant_agent import MerchantAgentConfig

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = EXAMPLE_ROOT / "data"
# The merchant agent's five skills, carried under vendor/ beside the shopping agent's
# five (see NOTICE); the packages install from the pin, the skills are files on disk.
SKILLS_DIR = EXAMPLE_ROOT.parent / "vendor" / "skills" / "merchant"

DEFAULT_API_VERSION = "2026-07"

# What a local store calls itself. Not a resolvable host, on purpose: a reader who sees it
# in a log knows at a glance that nothing left the machine.
LOCAL_DOMAIN = "acme-supply.local"


class MissingCredentials(RuntimeError):
    """Raised when the store's domain or token is absent, naming how to supply them."""


@dataclass(frozen=True)
class ShopifySettings:
    """One store's connection details. ``admin_token`` is read here and handed straight to
    the Admin client; nothing else in the example holds it."""

    shop_domain: str
    admin_token: str
    api_version: str
    # True when the store is ``api/local_store.py`` rather than a Shopify store. The token
    # is then empty, because there is nothing to authenticate to.
    local_store: bool
    operator: str
    store_name: str | None
    low_stock_default: int
    fulfilment_sla_days: int
    shopifyql_enabled: bool

    @property
    def merchant_id(self) -> str:
        return self.shop_domain


def local_store_requested() -> bool:
    return (os.environ.get("SHOPIFY_LOCAL_STORE") or "0").strip() not in {"", "0", "false"}


def load_settings() -> ShopifySettings:
    local = local_store_requested()
    domain = (os.environ.get("SHOPIFY_SHOP_DOMAIN") or "").strip()
    token = (os.environ.get("SHOPIFY_ADMIN_TOKEN") or "").strip()
    if local:
        # A local store has no credentials to be missing, and no domain to reach. The name
        # is still a name, so the portal and the ledger have something to call the store.
        domain, token = domain or LOCAL_DOMAIN, ""
    elif not domain or not token:
        raise MissingCredentials(
            "Set SHOPIFY_SHOP_DOMAIN and SHOPIFY_ADMIN_TOKEN in "
            "merchant/.env (copy merchant/.env.example), or set "
            "SHOPIFY_LOCAL_STORE=1 to run against the local store instead, with no "
            "Shopify account at all. A real store means a development store and a token "
            "minted from a Dev Dashboard app: the README's Run section has the steps."
        )
    return ShopifySettings(
        shop_domain=domain.removeprefix("https://").removeprefix("http://").rstrip("/"),
        admin_token=token,
        local_store=local,
        api_version=os.environ.get("SHOPIFY_ADMIN_API_VERSION") or DEFAULT_API_VERSION,
        operator=os.environ.get("SHOPIFY_OPERATOR") or "Operator",
        store_name=os.environ.get("SHOPIFY_STORE_NAME") or None,
        low_stock_default=int(os.environ.get("SHOPIFY_LOW_STOCK_DEFAULT") or 8),
        fulfilment_sla_days=int(os.environ.get("SHOPIFY_FULFILMENT_SLA_DAYS") or 3),
        shopifyql_enabled=os.environ.get("SHOPIFY_DISABLE_SHOPIFYQL", "0") != "1",
    )


def build_merchant_config(store_name: str) -> MerchantAgentConfig:
    """The deployment's guardrails and approval settings. Analysis stays off: this example
    exposes no SQL surface, so ``execute_analysis_query`` returns None."""
    return MerchantAgentConfig(
        brand_name=store_name,
        require_host_approval=host_approval_default(),
        approval_surface="the Approve button on the change preview card",
        enable_analysis=False,
    )

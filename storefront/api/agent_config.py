# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The Shopify demo store deployment's shopping agent config: an anonymous storefront
over a live shop, so no disclosures, and ids ground through Shopify's gid form."""

from __future__ import annotations

from shopping_agent import ShoppingAgentConfig


def build_shopping_config(store_name: str) -> ShoppingAgentConfig:
    return ShoppingAgentConfig(
        brand_name=store_name,
        assistant_name="the store assistant",
        brand_voice="friendly, direct, and plain about what this demo store carries",
        domain_search_notes=(
            "The catalog is a live Shopify demo store priced in CAD. A product's "
            "purchasable options (size, color) are its variants in get_product_details; "
            "the cart holds variants. Checkout, shipping, and payment all happen on the "
            "store's own checkout page — hand the customer to it rather than promising "
            "delivery options. Order lookups need a credential grant this deployment "
            "may not have: when the order tools return nothing, say so plainly and "
            "point the customer at their Shopify order confirmation email for status "
            "and tracking."
        ),
        product_id_patterns=(r"gid://shopify/(?:Product|ProductVariant)/\d+",),
    )

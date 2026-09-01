# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Every Admin GraphQL document this example sends, in one place so the whole API surface
it depends on can be reviewed at once. Each document carries an operation name; the tests'
fake client dispatches on that name, so a renamed operation fails loudly rather than
silently matching nothing.
"""

from __future__ import annotations

import uuid

# Requested for every product the catalog cache holds. `options { name }` rather than the
# option values: only the names feed the content-quality read, and the values field was
# renamed across API versions.
_PRODUCT_FIELDS = """
    id
    title
    handle
    status
    productType
    vendor
    updatedAt
    description
    descriptionHtml
    totalInventory
    tracksInventory
    featuredImage { url }
    seo { title description }
    media(first: 10) { nodes { id } }
    options { name }
    variants(first: 25) {
      nodes {
        id
        title
        sku
        price
        compareAtPrice
        inventoryQuantity
        inventoryItem { id tracked unitCost { amount currencyCode } }
      }
    }
"""

SHOP_PROFILE = """
query ShopProfile {
  shop {
    name
    myshopifyDomain
    currencyCode
    ianaTimezone
  }
}
"""

TOKEN_SCOPES = """
query TokenScopes {
  currentAppInstallation {
    accessScopes { handle }
  }
}
"""

CATALOG_PAGE = f"""
query CatalogPage($first: Int!, $after: String) {{
  products(first: $first, after: $after, sortKey: TITLE) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ {_PRODUCT_FIELDS} }}
  }}
}}
"""

PRODUCT_SEARCH = f"""
query ProductSearch($first: Int!, $query: String!) {{
  products(first: $first, query: $query) {{
    nodes {{ {_PRODUCT_FIELDS} }}
  }}
}}
"""

PRODUCT_RECORD = f"""
query ProductRecord($id: ID!) {{
  product(id: $id) {{ {_PRODUCT_FIELDS} }}
}}
"""

# `orders` without the read_all_orders scope only sees the trailing 60 days; the caller
# treats a short window as the ceiling rather than an error.
ORDERS_PAGE = """
query OrdersPage($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      displayFulfillmentStatus
      displayFinancialStatus
      currentTotalPriceSet { shopMoney { amount currencyCode } }
      lineItems(first: 25) {
        nodes {
          quantity
          title
          variant { id }
          product { id }
          discountedTotalSet { shopMoney { amount } }
        }
      }
      refunds { id createdAt totalRefundedSet { shopMoney { amount } } }
    }
  }
}
"""

PRIMARY_LOCATION = """
query PrimaryLocation {
  locations(first: 5) {
    nodes { id name isActive }
  }
}
"""

MARKETING_ACTIVITIES = """
query MarketingActivities($first: Int!) {
  marketingActivities(first: $first) {
    nodes {
      id
      title
      status
      createdAt
      marketingChannelType
      budget { total { amount currencyCode } }
    }
  }
}
"""

# ShopifyQL over the Admin API needs read_reports; some report fields additionally need
# approved protected-customer-data access. `metrics.py` falls back to the orders scan
# whenever this returns parse errors or is refused outright.
SHOPIFYQL_QUERY = """
query MetricsQuery($query: String!) {
  shopifyqlQuery(query: $query) {
    parseErrors
    tableData {
      columns { name dataType displayName }
      rows
    }
  }
}
"""

SET_VARIANT_PRICES = """
mutation SetVariantPrices($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price }
    userErrors { field message }
  }
}
"""

UPDATE_PRODUCT = """
mutation UpdateProduct($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product { id title status productType }
    userErrors { field message }
  }
}
"""

# The argument `productUpdate` takes was renamed from `input` to `product`; a deployment
# pinned to an older version answers the modern document with a validation error, and
# `staging.py` retries with this one.
UPDATE_PRODUCT_LEGACY = """
mutation UpdateProductLegacy($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id title status productType }
    userErrors { field message }
  }
}
"""

ADJUST_INVENTORY = """
mutation AdjustInventory($input: InventoryAdjustQuantitiesInput!, $idempotencyKey: String!) {
  inventoryAdjustQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup { createdAt reason }
    userErrors { field message }
  }
}
"""

CREATE_PRODUCT = """
mutation CreateProduct($product: ProductCreateInput!) {
  productCreate(product: $product) {
    product { id title variants(first: 5) { nodes { id inventoryItem { id } } } }
    userErrors { field message }
  }
}
"""

CREATE_PRODUCT_LEGACY = """
mutation CreateProductLegacy($input: ProductInput!) {
  productCreate(input: $input) {
    product { id title variants(first: 5) { nodes { id inventoryItem { id } } } }
    userErrors { field message }
  }
}
"""

# `productCreate` with `productOptions` defines the options and creates one variant; the
# rest of a variant ladder is a second call. Only `scripts/seed_store.py` sends this — the
# agent never creates a product.
PRODUCT_VARIANTS_BULK_CREATE = """
mutation CreateVariants($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkCreate(productId: $productId, variants: $variants) {
    productVariants { id title price inventoryItem { id } }
    userErrors { field message }
  }
}
"""

UPDATE_VARIANT_DETAILS = """
mutation UpdateVariantDetails($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price sku inventoryItem { id } }
    userErrors { field message }
  }
}
"""


# The three inventory mutations below take an idempotency key, and the Admin API has
# required it since version 2026-04: without ``@idempotent`` the store answers "The
# @idempotent directive is required for this mutation but was not provided" and writes
# nothing. One key stands for one logical write, so it is generated by the caller that owns
# the write rather than per attempt: a retry of the same write must send the same key for
# the store to recognise it as a repeat.
def idempotency_key() -> str:
    return str(uuid.uuid4())


INVENTORY_LEVEL = """
query InventoryLevel($inventoryItemId: ID!, $locationId: ID!) {
  inventoryItem(id: $inventoryItemId) {
    inventoryLevel(locationId: $locationId) {
      quantities(names: ["available"]) { name quantity }
    }
  }
}
"""

ACTIVATE_INVENTORY = """
mutation ActivateInventory(
  $inventoryItemId: ID!
  $locationId: ID!
  $available: Int
  $idempotencyKey: String!
) {
  inventoryActivate(
    inventoryItemId: $inventoryItemId
    locationId: $locationId
    available: $available
  ) @idempotent(key: $idempotencyKey) {
    inventoryLevel { id quantities(names: ["available"]) { name quantity } }
    userErrors { field message }
  }
}
"""

SET_INVENTORY_QUANTITIES = """
mutation SetInventoryQuantities($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup { createdAt reason }
    userErrors { field message }
  }
}
"""

CREATE_DRAFT_ORDER = """
mutation CreateDraftOrder($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder { id name }
    userErrors { field message }
  }
}
"""

COMPLETE_DRAFT_ORDER = """
mutation CompleteDraftOrder($id: ID!) {
  draftOrderComplete(id: $id, paymentPending: true) {
    draftOrder { id order { id name createdAt } }
    userErrors { field message }
  }
}
"""

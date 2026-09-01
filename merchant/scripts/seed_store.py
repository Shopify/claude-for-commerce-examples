# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Create the catalog and the orders in ``data/seed.json`` on the configured store.

    python merchant/scripts/seed_store.py [--dry-run] [--skip-orders]

A provisioned store is empty, and an empty store makes every read in this example return
nothing: no low stock, no slow movers, no order issues, no metrics. This script fills it
with the eight products the test fixture describes, so a live store and the suite tell the
same story and the demo conversation has something to talk about.

Two things it cannot do, and does not pretend to:

*Orders cannot be backdated.* ``draftOrderComplete`` stamps an order with the time it ran,
so a freshly seeded store has sales in the current period and none in the one before it.
The snapshot's comparison therefore reads as a rise from zero for the first fortnight. The
assistant states the window it measured, so this shows up as an honest figure rather than a
wrong one.

*Refunds are not created.* The return-spike alert needs refunded line items, and issuing a
refund is a payment operation this example has no business performing. On a seeded store
that alert stays quiet; the suite covers it against the fake.

Products are seeded once: the script reads the catalog first and leaves any handle that is
already there alone, including its price and its stock, because the store may have moved on
since it was seeded. Orders are not, and cannot be — there is nothing on an order to
recognise a seeded one by — so a second run adds a second set and says so. ``--skip-orders``
is the way to top up a catalog without touching the order history.

``--dry-run`` prints the mutations and sends none, which is the way to read what this
script would do to a store before pointing it at one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_DIR.parent))

from demo_common import load_demo_env  # noqa: E402
from merchant.api.admin_client import (  # noqa: E402
    AdminAPIError,
    AdminExecutor,
    AdminGraphQLClient,
    AdminUserError,
    operation_name,
)
from merchant.api.admin_token import token_source_for  # noqa: E402
from merchant.api.agent_config import load_settings  # noqa: E402
from merchant.api.queries import (  # noqa: E402
    ACTIVATE_INVENTORY,
    CATALOG_PAGE,
    COMPLETE_DRAFT_ORDER,
    CREATE_DRAFT_ORDER,
    CREATE_PRODUCT,
    CREATE_PRODUCT_LEGACY,
    INVENTORY_LEVEL,
    PRIMARY_LOCATION,
    PRODUCT_VARIANTS_BULK_CREATE,
    SET_INVENTORY_QUANTITIES,
    UPDATE_VARIANT_DETAILS,
    idempotency_key,
)

# What a store says when the inventory item is already stocked at the location. Matched on
# text because the Admin API returns it as a userErrors line with no code of its own.
ALREADY_ACTIVE = "already active at the location"

SEED_PATH = EXAMPLE_DIR / "data" / "seed.json"

# The store's credentials live in the example's .env, and nothing else here reads it: a
# script run from a shell has only the environment, so an operator who followed the README
# and set the two variables in .env would otherwise be told they are missing.
load_demo_env(EXAMPLE_DIR)


class DryRunExecutor:
    """Prints what would be sent and answers with the shape the seeder reads back. Product
    and inventory-item ids are invented here; nothing reaches a store."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._products = 0
        self._variants = 0
        self._drafts = 0

    def _variant_node(self) -> dict[str, Any]:
        self._variants += 1
        return {
            "id": f"gid://shopify/ProductVariant/{self._variants}",
            "title": "Default Title",
            "inventoryItem": {"id": f"gid://shopify/InventoryItem/{self._variants}"},
        }

    async def execute(
        self, document: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        name = operation_name(document)
        if name == "PrimaryLocation":
            return {
                "locations": {
                    "nodes": [
                        {"id": "gid://shopify/Location/1", "name": "Dry run", "isActive": True}
                    ]
                }
            }
        if name == "CatalogPage":
            # An empty store, which is what a dry run should describe: every product in the
            # seed is missing and would be created.
            return {"products": {"pageInfo": {"hasNextPage": False}, "nodes": []}}
        if name == "InventoryLevel":
            # Nothing this dry run "created" holds stock, so the quantity a restock would be
            # changing from is zero. The real store is read for this; here it is the only
            # answer consistent with an empty store.
            return {
                "inventoryItem": {
                    "inventoryLevel": {"quantities": [{"name": "available", "quantity": 0}]}
                }
            }
        raise AssertionError(f"the dry run has no answer for {name}")

    async def mutate(
        self, document: str, variables: dict[str, Any], *, root: str
    ) -> dict[str, Any]:
        name = operation_name(document)
        self.calls.append((name, variables))
        print(f"  {name} {json.dumps(variables, default=str)[:180]}")
        if name.startswith("CreateProduct"):
            self._products += 1
            return {
                "product": {
                    "id": f"gid://shopify/Product/{self._products}",
                    "variants": {"nodes": [self._variant_node()]},
                }
            }
        if name in {"CreateVariants", "UpdateVariantDetails"}:
            # Echo one node per requested variant, which is what the store does and what
            # lets the dry run show a ladder rather than only its first rung.
            requested = variables.get("variants") or []
            if name == "UpdateVariantDetails":
                return {
                    "productVariants": [
                        {
                            "id": entry["id"],
                            "title": "Default Title",
                            "inventoryItem": {
                                "id": entry["id"].replace("ProductVariant", "InventoryItem")
                            },
                        }
                        for entry in requested
                    ]
                }
            return {"productVariants": [self._variant_node() for _ in requested]}
        if name == "CreateDraftOrder":
            self._drafts += 1
            return {"draftOrder": {"id": f"gid://shopify/DraftOrder/{self._drafts}", "name": "#D1"}}
        if name == "CompleteDraftOrder":
            return {
                "draftOrder": {
                    "order": {
                        "id": f"gid://shopify/Order/{self._drafts}",
                        "name": f"#{1000 + self._drafts}",
                    }
                }
            }
        return {}


async def existing_variants(executor: AdminExecutor) -> dict[tuple[str, str], str]:
    """The variants already on the store, keyed by handle and variant title.

    This is what makes the script safe to run twice. Shopify does not reject a duplicate
    handle — it suffixes it — so a second run without this would quietly build a second
    catalog. A handle that is already there is left alone entirely, including its prices and
    its stock: the store may have moved on since it was seeded, and this script has no
    business deciding that a change made afterwards was a mistake.
    """
    found: dict[tuple[str, str], str] = {}
    cursor: str | None = None
    while True:
        data = await executor.execute(CATALOG_PAGE, {"first": 100, "after": cursor})
        page = data.get("products") or {}
        for node in page.get("nodes") or []:
            handle = str(node.get("handle") or "")
            for variant in (node.get("variants") or {}).get("nodes") or []:
                found[(handle, str(variant.get("title") or ""))] = str(variant["id"])
                found.setdefault((handle, ""), str(variant["id"]))
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return found
        cursor = info.get("endCursor")


async def variants_by_handle(executor: AdminExecutor) -> dict[str, list[dict[str, Any]]]:
    """Every product's variant nodes, keyed by handle, for ``--reset-stock``. The nodes carry
    the inventory item ids ``set_stock`` needs, which the id map above throws away."""
    found: dict[str, list[dict[str, Any]]] = {}
    cursor: str | None = None
    while True:
        data = await executor.execute(CATALOG_PAGE, {"first": 100, "after": cursor})
        page = data.get("products") or {}
        for node in page.get("nodes") or []:
            found[str(node.get("handle") or "")] = list(
                (node.get("variants") or {}).get("nodes") or []
            )
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return found
        cursor = info.get("endCursor")


async def primary_location(executor: AdminExecutor) -> str:
    data = await executor.execute(PRIMARY_LOCATION)
    for node in (data.get("locations") or {}).get("nodes") or []:
        if node.get("isActive"):
            return str(node["id"])
    raise SystemExit("The store has no active location, so no inventory can be set.")


async def create_product(executor: AdminExecutor, spec: dict[str, Any]) -> dict[str, Any]:
    """One product, then its variants, then their stock. Three steps because that is how
    the Admin API splits them: ``productCreate`` makes the product and a default variant,
    prices and unit costs go on the variant, and stock is a level at a location."""
    product_input: dict[str, Any] = {
        "title": spec["title"],
        "handle": spec["handle"],
        "status": spec.get("status", "ACTIVE"),
        "descriptionHtml": f"<p>{spec['description']}</p>",
        "productType": spec.get("product_type"),
        "vendor": spec.get("vendor") or "ACME Supply Co.",
    }
    if spec.get("seo_description"):
        product_input["seo"] = {"description": spec["seo_description"]}
    if len(spec["variants"]) > 1:
        product_input["productOptions"] = [
            {
                "name": "Size",
                "values": [{"name": variant["title"]} for variant in spec["variants"]],
            }
        ]

    try:
        payload = await executor.mutate(
            CREATE_PRODUCT, {"product": product_input}, root="productCreate"
        )
    except (AdminAPIError, AdminUserError):
        # The argument was renamed from `input` to `product`; an older pinned version only
        # accepts the legacy shape.
        payload = await executor.mutate(
            CREATE_PRODUCT_LEGACY, {"input": product_input}, root="productCreate"
        )
    return payload.get("product") or {}


async def set_variants(
    executor: AdminExecutor, product_id: str, spec: dict[str, Any], created: dict[str, Any]
) -> list[dict[str, Any]]:
    """Prices, SKUs, unit costs, and whether stock is tracked. The created variants are
    matched to the seed's variants by position, which is the order the options were given
    in; a store that reorders them would need matching on option value instead."""

    def details(variant: dict[str, Any]) -> dict[str, Any]:
        item: dict[str, Any] = {"tracked": spec.get("tracks_inventory", True)}
        if variant.get("sku"):
            item["sku"] = variant["sku"]
        if variant.get("cost") is not None:
            item["cost"] = variant["cost"]
        return {"price": variant["price"], "inventoryItem": item}

    existing = [node["id"] for node in (created.get("variants") or {}).get("nodes") or []]
    if not existing:
        return []

    seeded = spec["variants"]
    updates = [
        {"id": variant_id, **details(variant)}
        for variant_id, variant in zip(existing, seeded, strict=False)
    ]
    payload = await executor.mutate(
        UPDATE_VARIANT_DETAILS,
        {"productId": product_id, "variants": updates},
        root="productVariantsBulkUpdate",
    )
    variants = list(payload.get("productVariants") or [])

    # `productCreate` defines the options and creates the first combination only, so the
    # rest of a ladder is a second call. The option name here has to match the one
    # `create_product` declared.
    remaining = seeded[len(existing) :]
    if remaining:
        added = await executor.mutate(
            PRODUCT_VARIANTS_BULK_CREATE,
            {
                "productId": product_id,
                "variants": [
                    {
                        "optionValues": [{"optionName": "Size", "name": variant["title"]}],
                        **details(variant),
                    }
                    for variant in remaining
                ],
            },
            root="productVariantsBulkCreate",
        )
        variants.extend(added.get("productVariants") or [])
    return variants


async def set_stock(
    executor: AdminExecutor, location_id: str, variants: list[dict[str, Any]], spec: dict[str, Any]
) -> None:
    """Stock for each tracked variant. ``inventoryActivate`` is what makes the item stocked
    at this location at all; ``inventorySetQuantities`` then sets the absolute number, which
    is right for a seed and wrong for a restock (the restock path in ``staging.py``
    adjusts by a delta, so two concurrent restocks add up rather than overwrite)."""
    if not spec.get("tracks_inventory", True):
        return
    quantities = []
    for variant, seeded in zip(variants, spec["variants"], strict=False):
        item = (variant.get("inventoryItem") or {}).get("id")
        if item is None or seeded.get("quantity") is None:
            continue
        try:
            await executor.mutate(
                ACTIVATE_INVENTORY,
                {
                    "inventoryItemId": item,
                    "locationId": location_id,
                    "available": 0,
                    "idempotencyKey": idempotency_key(),
                },
                root="inventoryActivate",
            )
        except AdminUserError as error:
            # A store that already stocks this item at this location refuses the activation
            # rather than ignoring it, so a second run of the seeder would stop here. The
            # quantity is set below either way, which is the part that matters.
            if not any(ALREADY_ACTIVE in line for line in error.errors):
                raise
            print(f"  {item.rsplit('/', 1)[-1]}: already stocked here, activation skipped")
        # ``changeFromQuantity`` is the quantity the store expects to be replacing. The
        # schema types it as optional and the store refuses the mutation without it, so the
        # current number has to be read back even for an item activated a moment ago.
        level = await executor.execute(
            INVENTORY_LEVEL, {"inventoryItemId": item, "locationId": location_id}
        )
        current = 0
        for entry in ((level.get("inventoryItem") or {}).get("inventoryLevel") or {}).get(
            "quantities"
        ) or []:
            if entry.get("name") == "available":
                current = int(entry.get("quantity") or 0)
        quantities.append(
            {
                "inventoryItemId": item,
                "locationId": location_id,
                "quantity": int(seeded["quantity"]),
                "changeFromQuantity": current,
            }
        )
    if quantities:
        await executor.mutate(
            SET_INVENTORY_QUANTITIES,
            {
                # No compare quantity: each entry sets an absolute number, and
                # ``InventoryQuantityInput.changeFromQuantity`` is what would make the store
                # check the value it is replacing. The 2026-07 input has no
                # ``ignoreCompareQuantity`` field, so omitting the compare is the whole of it.
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "quantities": quantities,
                },
                "idempotencyKey": idempotency_key(),
            },
            root="inventorySetQuantities",
        )


async def place_order(
    executor: AdminExecutor, order: dict[str, Any], variant_ids: dict[tuple[str, str], str]
) -> str | None:
    """A draft order, completed with payment pending so the store has an order without a
    charge. An order left unfulfilled is simply one nothing fulfils afterwards; this script
    fulfils nothing, so ``unfulfilled`` in the seed is a note about intent rather than a
    separate code path."""
    lines = []
    for line in order["lines"]:
        key = (line["handle"], line.get("variant") or "")
        variant_id = variant_ids.get(key) or variant_ids.get((line["handle"], ""))
        if variant_id is None:
            print(f"! no variant for {key}, skipping the line")
            continue
        lines.append({"variantId": variant_id, "quantity": int(line["quantity"])})
    if not lines:
        return None
    draft = await executor.mutate(
        CREATE_DRAFT_ORDER, {"input": {"lineItems": lines}}, root="draftOrderCreate"
    )
    draft_id = (draft.get("draftOrder") or {}).get("id")
    if draft_id is None:
        return None
    completed = await executor.mutate(
        COMPLETE_DRAFT_ORDER, {"id": draft_id}, root="draftOrderComplete"
    )
    return ((completed.get("draftOrder") or {}).get("order") or {}).get("name")


async def seed(
    executor: AdminExecutor,
    spec: dict[str, Any],
    *,
    skip_orders: bool,
    reset_stock: bool = False,
) -> None:
    location_id = await primary_location(executor)
    print(f"location: {location_id}")

    variant_ids = await existing_variants(executor)
    if variant_ids:
        print(f"the store already holds {len({handle for handle, _ in variant_ids})} handles")
    # Only read when asked for: it is a second pass over the catalog, and the default leaves
    # an existing handle's stock alone.
    existing_nodes = await variants_by_handle(executor) if reset_stock else {}
    for product in spec["products"]:
        if (product["handle"], "") in variant_ids:
            if reset_stock and (nodes := existing_nodes.get(product["handle"])):
                print(f"{product['handle']}: already there, setting stock")
                await set_stock(executor, location_id, nodes, product)
                continue
            print(f"{product['handle']}: already there, left alone")
            continue
        print(f"{product['handle']}:")
        created = await create_product(executor, product)
        product_id = created.get("id")
        if product_id is None:
            print("! productCreate returned no product, skipping")
            continue
        variants = await set_variants(executor, str(product_id), product, created)
        await set_stock(executor, location_id, variants, product)
        for variant, seeded in zip(variants, product["variants"], strict=False):
            variant_ids[(product["handle"], seeded["title"])] = str(variant["id"])
            variant_ids.setdefault((product["handle"], ""), str(variant["id"]))

    if skip_orders:
        print("skipping orders")
        return
    # Orders are not idempotent and cannot be made so: there is nothing on an order to
    # recognise a seeded one by, and two identical orders a fortnight apart are a legitimate
    # thing for a store to have. A second run adds a second set, which is why it says so.
    print(f"placing {len(spec['orders'])} orders (a second run adds another set)")
    for index, order in enumerate(spec["orders"], start=1):
        name = await place_order(executor, order, variant_ids)
        print(f"order {index}: {name or 'not created'}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the mutations and send none")
    parser.add_argument("--skip-orders", action="store_true")
    parser.add_argument(
        "--reset-stock",
        action="store_true",
        help=(
            "also set stock on handles the store already holds. Off by default, because a "
            "store may have moved on since it was seeded; on, it is what finishes a run "
            "that failed partway and left quantities unset"
        ),
    )
    args = parser.parse_args()

    spec = json.loads(SEED_PATH.read_text())

    if args.dry_run:
        executor: AdminExecutor = DryRunExecutor()
        print(f"dry run over {SEED_PATH.name}; nothing is sent")
        await seed(executor, spec, skip_orders=args.skip_orders, reset_stock=args.reset_stock)
        return 0

    settings = load_settings()
    client = AdminGraphQLClient(
        shop_domain=settings.shop_domain,
        token_source=token_source_for(settings),
        api_version=settings.api_version,
    )
    print(f"seeding {settings.shop_domain} ({settings.api_version})")
    try:
        await seed(client, spec, skip_orders=args.skip_orders, reset_stock=args.reset_stock)
    except AdminUserError as error:
        print(f"! the store refused a write: {error}")
        return 1
    except AdminAPIError as error:
        print(f"! the Admin API failed: {error}")
        return 1
    finally:
        await client.aclose()
    print("done. next: python merchant/scripts/smoke_live.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

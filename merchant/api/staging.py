# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The only module in this example that mutates the store.

Everything the agent does stops at a ledger entry. This module runs when the operator has
approved one, and it turns that entry's items into Admin API mutations. The split matters
enough to state plainly: nothing above this file can change a live product, and this file
is never reached by a tool call, only by an approval the host recorded.

The order of operations is write first, then mark applied. The guardrails are re-checked
here before anything is sent, so a config that tightened after staging still blocks the
write; but the ledger is only advanced once Shopify has accepted the change. A failed
write therefore leaves the entry staged and re-approvable, with no ledger state to unwind.

Two of the five change kinds have no write-through. A promotion and a campaign both live
in the ledger only: a real promotion is a discount object with its own scopes and schedule
semantics, and a marketing activity belongs to the app that owns the channel. Applying one
here records the operator's decision and says so in the result rather than quietly doing
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from merchant_agent import ChangeItem, ChangeKind, StagedChange
from merchant_agent.changes import ChangeNotApplicable

from .admin_client import AdminAPIError, AdminExecutor, AdminUserError
from .catalog import CatalogCache, ProductRecord
from .queries import (
    ADJUST_INVENTORY,
    INVENTORY_LEVEL,
    PRIMARY_LOCATION,
    SET_VARIANT_PRICES,
    UPDATE_PRODUCT,
    UPDATE_PRODUCT_LEGACY,
    idempotency_key,
)

PRICE_FIELD = "price"
STOCK_FIELD = "stock"
STATUS_FIELD = "status"

# A listing update's field names, and the ProductUpdateInput path each one writes.
# ``Listing.short_description`` and ``ListingDetails.long_description`` are two fields in the
# interface and one field in Shopify, so all three description names write the same body; a
# change naming more than one of them with different values is refused rather than resolved.
LISTING_FIELDS = {
    "title": ("title",),
    "description": ("descriptionHtml",),
    "short_description": ("descriptionHtml",),
    "long_description": ("descriptionHtml",),
    "seo_title": ("seo", "title"),
    "seo_description": ("seo", "description"),
    "product_type": ("productType",),
    "category": ("productType",),
    "vendor": ("vendor",),
    "handle": ("handle",),
}

_LEDGER_ONLY = {
    ChangeKind.PROMOTION: (
        "recorded in the change ledger only — a live promotion needs a discount object "
        "with its own schedule and scopes, which this example does not create"
    ),
    ChangeKind.CAMPAIGN: (
        "recorded in the change ledger only — a marketing activity belongs to the app that "
        "owns the channel, so this example does not create one"
    ),
}


class WriteFailed(RuntimeError):
    """A mutation was refused. ``completed`` names the targets already written when the
    failure happened, so a partial apply is reported as one rather than as a clean
    failure."""

    def __init__(self, message: str, completed: list[str] | None = None) -> None:
        self.completed = completed or []
        if self.completed:
            message = f"{message}. Already written before the failure: {', '.join(self.completed)}"
        super().__init__(message)


@dataclass
class ShopifyWriter:
    """Applies an approved change to the store."""

    executor: AdminExecutor
    catalog: CatalogCache
    _location_id: str | None = None

    async def location_id(self) -> str:
        """The store's first active location, cached. Inventory moves need one and this
        example does not model multi-location stock."""
        if self._location_id is None:
            data = await self.executor.execute(PRIMARY_LOCATION, {})
            nodes = (data.get("locations") or {}).get("nodes") or []
            active = [node for node in nodes if node.get("isActive")] or nodes
            if not active:
                raise WriteFailed("the store has no active location to move inventory at")
            self._location_id = active[0]["id"]
        return self._location_id

    async def apply(self, change: StagedChange) -> list[str]:
        """Write ``change`` to the store and return the notes to attach to it. Raises
        :class:`WriteFailed` with the store unchanged for that item, or
        :class:`ChangeNotApplicable` when the change names something unmanaged."""
        if (note := _LEDGER_ONLY.get(change.kind)) is not None:
            return [note]
        notes: list[str] = []
        completed: list[str] = []
        for target, items in _by_target(change.items).items():
            record = await self.catalog.get(target)
            if record is None:
                raise WriteFailed(
                    f"listing {target} is no longer in the catalog, so the change was not "
                    "applied to it",
                    completed,
                )
            try:
                notes.extend(await self._write(change.kind, record, items))
            except (AdminUserError, AdminAPIError) as error:
                raise WriteFailed(str(error), completed) from error
            completed.append(record.title or target)
        # Re-read rather than invalidate: the write just landed, so this is the one moment the
        # store is known to have changed, and the portal's next paint reads the cache
        # synchronously and would otherwise show the old figures.
        await self.catalog.refresh()
        return notes

    async def _write(
        self, kind: ChangeKind, record: ProductRecord, items: list[ChangeItem]
    ) -> list[str]:
        if kind is ChangeKind.PRICE_UPDATE:
            return await self._write_price(record, items)
        if kind is ChangeKind.LISTING_UPDATE:
            return await self._write_listing(record, items)
        if kind is ChangeKind.INVENTORY_ACTION:
            return await self._write_inventory(record, items)
        raise ChangeNotApplicable(f"{kind.value} changes are not applied by this deployment")

    # -- Price ---------------------------------------------------------------------

    async def _write_price(self, record: ProductRecord, items: list[ChangeItem]) -> list[str]:
        """The staged price is the product's own — the first variant's. A product with a
        variant ladder keeps it: every variant moves by the same ratio, so the price the
        operator approved is the one the product ends up showing and the spread between
        variants survives."""
        item = next((entry for entry in items if entry.field == PRICE_FIELD), None)
        if item is None or (target_price := _as_float(item.after)) is None:
            raise WriteFailed(f"the price change for {record.title} carries no new price")
        current = record.price
        if current <= 0:
            raise WriteFailed(f"{record.title} has no current price to move from")
        ratio = target_price / current
        variants = [
            {"id": variant.variant_id, "price": f"{round(variant.price * ratio, 2):.2f}"}
            for variant in record.variants
        ]
        if not variants:
            raise WriteFailed(f"{record.title} has no variants to price")
        await self.executor.mutate(
            SET_VARIANT_PRICES,
            {"productId": record.product_id, "variants": variants},
            root="productVariantsBulkUpdate",
        )
        if len(variants) > 1:
            return [
                f"{record.title} has {len(variants)} variants; each moved by the same "
                f"{(ratio - 1) * 100:+.1f}% so the ladder between them is unchanged"
            ]
        return []

    # -- Listing content ------------------------------------------------------------

    async def _write_listing(self, record: ProductRecord, items: list[ChangeItem]) -> list[str]:
        product: dict[str, object] = {"id": record.product_id}
        written: dict[tuple[str, ...], tuple[str, object]] = {}
        for item in items:
            path = LISTING_FIELDS.get(item.field)
            if path is None:
                # The backend refuses an unwritable field when the change is staged, so
                # reaching this means the map changed under a change that was already
                # staged. Refusing here leaves it staged rather than half-written.
                raise ChangeNotApplicable(
                    f"'{item.field}' is not a listing field this deployment writes"
                )
            if (collision := written.get(path)) and collision[1] != item.after:
                raise ChangeNotApplicable(
                    f"'{collision[0]}' and '{item.field}' are one field on a Shopify product "
                    f"({'.'.join(path)}); stage one of them"
                )
            written[path] = (item.field, item.after)
            if len(path) == 1:
                product[path[0]] = item.after
            else:
                nested = product.setdefault(path[0], {})
                if isinstance(nested, dict):
                    nested[path[1]] = item.after
        await self._product_update(product)
        return []

    async def _product_update(self, product: dict[str, object]) -> None:
        """``productUpdate``'s argument was renamed from ``input`` to ``product``. A
        deployment pinned to an older API version rejects the modern document outright, so
        the older shape is tried once before the failure is reported."""
        try:
            await self.executor.mutate(UPDATE_PRODUCT, {"product": product}, root="productUpdate")
        except AdminAPIError:
            await self.executor.mutate(
                UPDATE_PRODUCT_LEGACY, {"input": product}, root="productUpdate"
            )

    # -- Inventory and availability --------------------------------------------------

    async def _write_inventory(self, record: ProductRecord, items: list[ChangeItem]) -> list[str]:
        notes: list[str] = []
        for item in items:
            if item.field == STATUS_FIELD:
                notes.extend(await self._write_status(record, item))
            elif item.field == STOCK_FIELD:
                notes.extend(await self._write_stock(record, item))
            else:
                raise ChangeNotApplicable(
                    f"'{item.field}' is not an inventory field this deployment writes"
                )
        return notes

    async def _write_status(self, record: ProductRecord, item: ChangeItem) -> list[str]:
        wanted = str(item.after).lower()
        if wanted not in {"active", "paused"}:
            raise ChangeNotApplicable(
                f"a listing cannot be set to '{item.after}' by this deployment"
            )
        status = "ACTIVE" if wanted == "active" else "DRAFT"
        await self._product_update({"id": record.product_id, "status": status})
        if status == "DRAFT":
            return [
                f"{record.title} is now a draft, which removes it from every sales channel "
                "rather than only hiding it from the online store"
            ]
        return []

    async def _write_stock(self, record: ProductRecord, item: ChangeItem) -> list[str]:
        delta = _as_int(item.after) - _as_int(item.before)
        if delta == 0:
            return []
        tracked = [
            variant for variant in record.variants if variant.tracked and variant.inventory_item_id
        ]
        if not tracked:
            raise ChangeNotApplicable(
                f"{record.title} does not track inventory, so its stock cannot be adjusted"
            )
        item_id, location = tracked[0].inventory_item_id, await self.location_id()
        # The quantity the store expects to be changing. ``InventoryChangeInput`` types it as
        # optional and the Admin API refuses the mutation without it, so it is read here
        # rather than taken from the catalog cache: a compare against a stale number is worse
        # than no compare, and a fresh read is what makes the refusal mean what it says. If
        # the stock moved since the change was staged, the store rejects this write and the
        # change stays staged.
        level = await self.executor.execute(
            INVENTORY_LEVEL, {"inventoryItemId": item_id, "locationId": location}
        )
        available = 0
        quantities = ((level.get("inventoryItem") or {}).get("inventoryLevel") or {}).get(
            "quantities"
        ) or []
        for entry in quantities:
            if entry.get("name") == "available":
                available = _as_int(entry.get("quantity"))
        await self.executor.mutate(
            ADJUST_INVENTORY,
            {
                "idempotencyKey": idempotency_key(),
                "input": {
                    "reason": "correction",
                    "name": "available",
                    "referenceDocumentUri": f"logical://commerce-agents/change/{record.handle}",
                    "changes": [
                        {
                            "delta": delta,
                            "changeFromQuantity": available,
                            "inventoryItemId": item_id,
                            "locationId": location,
                        }
                    ],
                },
            },
            root="inventoryAdjustQuantities",
        )
        if len(tracked) > 1:
            return [
                f"the {delta:+d} units went to {record.title}'s '{tracked[0].title}' variant; "
                "the interface has no variant dimension, so a split across variants has to "
                "be staged as separate changes"
            ]
        return []


def _by_target(items: list[ChangeItem]) -> dict[str, list[ChangeItem]]:
    """Items grouped by the listing they touch, so one product takes one mutation per
    concern rather than one per field."""
    grouped: dict[str, list[ChangeItem]] = {}
    for item in items:
        grouped.setdefault(item.target, []).append(item)
    return grouped


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0

# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The two provisioning scripts, as far as a test without a store can take them.

``seed_store.py`` and ``smoke_live.py`` exist to be pointed at a real Shopify store, and
what only a store can settle — whether each document matches the schema it is serving,
whether the token's scopes reach — is settled there. What a test can settle is everything
around that: that the scripts call methods and read fields that exist, that the seeder sends
its mutations in the order the Admin API needs them, and that ``smoke_live --read-only``
really is read-only.

That last one matters most. The script performs a write in its normal mode, so its
read-only mode is a safety claim, and a safety claim belongs in the suite: ``admin.calls``
is empty at the end of this file or the claim is false.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from merchant_agent import ChangeStatus

from .fake_admin import FakeAdmin

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    """Load a script by path. ``scripts/`` is a directory of scripts rather than a package,
    which is how the rest of the repo keeps its scripts, so there is nothing to import."""
    spec = importlib.util.spec_from_file_location(f"merchant_{name}", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seed_store() -> ModuleType:
    return _load("seed_store")


@pytest.fixture(scope="module")
def smoke_live() -> ModuleType:
    return _load("smoke_live")


# -- seed_store ----------------------------------------------------------------------


def test_the_seed_file_describes_what_the_fixture_describes(seed_store) -> None:
    """The seeded store and the test fixture are the same catalog, so a conversation held
    against a live store and one held against the suite go the same way."""
    spec = seed_store.json.loads(seed_store.SEED_PATH.read_text())

    handles = [product["handle"] for product in spec["products"]]
    assert handles == [
        "canvas-tool-apron",
        "folding-step-stool",
        "bench-dog-set",
        "shop-stool-cushion",
        "layout-square",
        "sawdust-broom",
        "cast-iron-hold-down",
        "discontinued-mallet",
    ]
    statuses = {product["handle"]: product["status"] for product in spec["products"]}
    assert statuses["shop-stool-cushion"] == "DRAFT"
    assert statuses["discontinued-mallet"] == "ARCHIVED"
    # The thresholds file keys on these two, so a renamed handle or type would silently
    # stop the low-stock rules from applying to anything.
    types = {product["product_type"] for product in spec["products"]}
    assert {"Workshop tools", "Storage"} <= types


async def test_the_seeder_sends_the_mutations_in_the_order_the_api_needs(seed_store) -> None:
    """A product before its variants, a variant before its inventory item is activated, and
    an inventory item activated before a quantity is set on it."""
    spec = seed_store.json.loads(seed_store.SEED_PATH.read_text())
    executor = seed_store.DryRunExecutor()

    await seed_store.seed(executor, spec, skip_orders=True)

    names = [name for name, _ in executor.calls]
    assert names[:4] == [
        "CreateProduct",
        "UpdateVariantDetails",
        "ActivateInventory",
        "SetInventoryQuantities",
    ]
    assert names.count("CreateProduct") == len(spec["products"])
    # One product has a variant ladder, and `productCreate` only makes its first rung.
    assert names.count("CreateVariants") == 1
    # The untracked product gets no inventory calls at all.
    assert names.count("SetInventoryQuantities") == len(spec["products"]) - 1


async def test_the_seeder_leaves_a_handle_that_is_already_there_alone(seed_store) -> None:
    """Safe to run twice. Shopify suffixes a duplicate handle rather than refusing it, so
    without this a second run would quietly build a second catalog."""
    spec = seed_store.json.loads(seed_store.SEED_PATH.read_text())
    executor = seed_store.DryRunExecutor()
    seeded = {
        ("canvas-tool-apron", ""): "gid://shopify/ProductVariant/900",
        ("bench-dog-set", ""): "gid://shopify/ProductVariant/901",
    }

    async def already_there(_executor):
        return dict(seeded)

    monkeypatched = seed_store.existing_variants
    seed_store.existing_variants = already_there
    try:
        await seed_store.seed(executor, spec, skip_orders=True)
    finally:
        seed_store.existing_variants = monkeypatched

    created = [
        variables["product"]["handle"]
        for name, variables in executor.calls
        if name == "CreateProduct"
    ]
    assert "canvas-tool-apron" not in created
    assert "bench-dog-set" not in created
    assert len(created) == len(spec["products"]) - 2


async def test_the_seeder_orders_reference_the_variants_it_created(seed_store) -> None:
    spec = seed_store.json.loads(seed_store.SEED_PATH.read_text())
    executor = seed_store.DryRunExecutor()

    await seed_store.seed(executor, spec, skip_orders=False)

    drafts = [variables for name, variables in executor.calls if name == "CreateDraftOrder"]
    assert len(drafts) == len(spec["orders"])
    assert all(
        line["variantId"].startswith("gid://shopify/ProductVariant/")
        for draft in drafts
        for line in draft["input"]["lineItems"]
    )
    # The step stool's two rungs are distinguishable, so an order for the three-step is not
    # silently an order for the two-step.
    ladder = {
        line["variantId"]
        for draft in drafts
        for line in draft["input"]["lineItems"]
        if line["quantity"] == 1
    }
    assert len(ladder) > 1


async def test_a_seed_variant_carries_its_price_sku_and_unit_cost(seed_store) -> None:
    spec = seed_store.json.loads(seed_store.SEED_PATH.read_text())
    executor = seed_store.DryRunExecutor()

    await seed_store.seed(executor, spec, skip_orders=True)

    first = next(variables for name, variables in executor.calls if name == "UpdateVariantDetails")
    variant = first["variants"][0]
    assert variant["price"] == "40.00"
    assert variant["inventoryItem"] == {"tracked": True, "sku": "ACME-AP-1", "cost": "10.00"}

    # The product with no unit cost must send no cost, rather than a zero that would read
    # as a hundred percent margin.
    costless = [
        entry
        for name, variables in executor.calls
        if name == "UpdateVariantDetails"
        for entry in variables["variants"]
        if entry["inventoryItem"].get("sku") == "ACME-LS-1"
    ]
    assert costless and "cost" not in costless[0]["inventoryItem"]


async def test_the_untracked_product_is_seeded_untracked(seed_store) -> None:
    spec = seed_store.json.loads(seed_store.SEED_PATH.read_text())
    executor = seed_store.DryRunExecutor()

    await seed_store.seed(executor, spec, skip_orders=True)

    broom = [
        entry
        for name, variables in executor.calls
        if name == "UpdateVariantDetails"
        for entry in variables["variants"]
        if entry["inventoryItem"].get("sku") == "ACME-SB-1"
    ]
    assert broom and broom[0]["inventoryItem"]["tracked"] is False


# -- smoke_live ----------------------------------------------------------------------


async def test_every_read_the_smoke_script_makes_exists(
    smoke_live, backend, session, admin: FakeAdmin
) -> None:
    """The script names methods and fields directly, so this is the test that catches a
    rename in the backend before someone runs the script against a store and finds out."""
    await backend.warm()
    report = smoke_live.Report()

    await smoke_live.read_checks(backend, session, report)

    assert report.failures == []
    assert admin.calls == []


async def test_the_read_only_gate_check_writes_nothing(
    smoke_live, backend, config, session, admin: FakeAdmin
) -> None:
    """Read-only mode stages a change, proves the store's price did not move, proves an
    apply with no host approval is held, and discards the change. No mutation is sent."""
    await backend.warm()
    report = smoke_live.Report()

    await smoke_live.gate_checks(backend, config, session, report, read_only=True)

    assert report.failures == []
    assert admin.calls == []
    staged = backend.ledger.pending()
    assert staged == []
    discarded = [change for change in backend.ledger.resolved()]
    assert [change.status for change in discarded] == [ChangeStatus.DISCARDED]

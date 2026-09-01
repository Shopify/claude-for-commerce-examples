# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Staging and applying.

This is the file the example's safety claim rests on, so it is written as two halves. The
first proves that every staging path leaves the store untouched: ``admin.calls`` is the
list of mutations sent, and after staging it must be empty. The second proves that applying
sends exactly the mutation the preview described, and that a write the store refuses leaves
the change staged rather than half-recorded.
"""

from __future__ import annotations

import pytest

from merchant_agent import (
    CampaignDraft,
    ChangeStatus,
    InventoryActionItem,
    PriceUpdateItem,
    PromotionDraft,
)
from merchant_agent.changes import ChangeNotApplicable, GuardrailViolation
from merchant.api.shopify_backend import ShopifyMerchantBackend

from .fake_admin import FakeAdmin

APRON = "gid://shopify/Product/1"
STOOL = "gid://shopify/Product/2"
CUSHION = "gid://shopify/Product/4"
BROOM = "gid://shopify/Product/6"


async def stage_all(backend: ShopifyMerchantBackend, session) -> list[str]:
    """One change of every kind the interface has, in the order the skills reach for them."""
    return [
        (
            await backend.stage_listing_update(session, APRON, {"title": "Canvas bench apron"})
        ).change_id,
        (
            await backend.stage_price_update(
                session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
            )
        ).change_id,
        (
            await backend.stage_inventory_action(
                session, [InventoryActionItem(listing_id=APRON, action="restock", quantity=10)]
            )
        ).change_id,
        (
            await backend.stage_promotion(
                session,
                PromotionDraft(
                    name="Bench week",
                    listing_ids=[APRON],
                    discount_pct=10,
                    starts="2026-09-01",
                    ends="2026-09-07",
                ),
            )
        ).change_id,
        (
            await backend.stage_campaign(
                session, CampaignDraft(name="Bench week email", budget=500.0)
            )
        ).change_id,
    ]


# -- Staging touches nothing ---------------------------------------------------------


async def test_no_staging_path_writes_to_the_store(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """The whole approval model rests on this. If staging could write, the operator would
    be approving something that had already happened."""
    change_ids = await stage_all(backend, session)

    assert admin.calls == []
    assert len(change_ids) == len(set(change_ids)) == 5
    assert len(await backend.get_pending_changes(session)) == 5


async def test_a_staged_change_is_the_assistant_s_proposal_not_the_operator_s_act(
    backend: ShopifyMerchantBackend, session
) -> None:
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )

    assert change.status is ChangeStatus.STAGED
    assert change.created_by_kind.value == "agent"
    assert change.created_by == "Dana"
    assert change.applied_at is None


async def test_a_price_preview_carries_the_margin_it_moves(
    backend: ShopifyMerchantBackend, session
) -> None:
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )

    assert change.currency == "USD"
    assert change.margin_before_pct is not None
    assert change.margin_after_pct > change.margin_before_pct
    assert any("margin" in note for note in change.guardrail_notes)


async def test_a_ladder_is_flagged_on_the_preview_before_it_is_approved(
    backend: ShopifyMerchantBackend, session
) -> None:
    """The operator has to know the other variants move too, and the preview card is the
    only place they read before approving."""
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=STOOL, new_price=66.0)]
    )

    assert any("2 variants" in note for note in change.guardrail_notes)


async def test_pausing_says_what_pausing_actually_does(
    backend: ShopifyMerchantBackend, session
) -> None:
    change = await backend.stage_inventory_action(
        session, [InventoryActionItem(listing_id=APRON, action="pause")]
    )

    assert any("every sales channel" in note for note in change.guardrail_notes)


async def test_applying_a_promotion_says_it_records_a_decision(
    backend: ShopifyMerchantBackend, session
) -> None:
    """A promotion has no write-through here, and the operator reads that on the preview
    card rather than discovering it after approving."""
    change = await backend.stage_promotion(
        session,
        PromotionDraft(
            name="Bench week",
            listing_ids=[APRON],
            discount_pct=10,
            starts="2026-09-01",
            ends="2026-09-07",
        ),
    )

    assert any("does not create a discount" in note for note in change.guardrail_notes)


# -- Unknown ids ---------------------------------------------------------------------


async def test_every_staging_path_refuses_an_id_that_names_no_listing(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """Staged, such an id would preview cleanly and then apply to no product at all."""
    missing = "gid://shopify/Product/999"

    with pytest.raises(ChangeNotApplicable, match="no listing"):
        await backend.stage_listing_update(session, missing, {"title": "x"})
    with pytest.raises(ChangeNotApplicable, match="no listing"):
        await backend.stage_price_update(
            session, [PriceUpdateItem(listing_id=missing, new_price=10.0)]
        )
    with pytest.raises(ChangeNotApplicable, match="no listing"):
        await backend.stage_inventory_action(
            session, [InventoryActionItem(listing_id=missing, action="restock", quantity=5)]
        )
    with pytest.raises(ChangeNotApplicable, match="no listing"):
        await backend.stage_promotion(
            session,
            PromotionDraft(
                name="p",
                listing_ids=[missing],
                discount_pct=10,
                starts="2026-09-01",
                ends="2026-09-07",
            ),
        )

    assert await backend.get_pending_changes(session) == []
    assert admin.calls == []


async def test_a_product_that_does_not_track_inventory_cannot_be_restocked(
    backend: ShopifyMerchantBackend, session
) -> None:
    with pytest.raises(ChangeNotApplicable, match="does not track inventory"):
        await backend.stage_inventory_action(
            session, [InventoryActionItem(listing_id=BROOM, action="restock", quantity=5)]
        )


# -- Guardrails ----------------------------------------------------------------------


async def test_a_price_move_over_the_cap_is_refused_at_staging(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    with pytest.raises(GuardrailViolation, match="exceeds the 20% per-change limit"):
        await backend.stage_price_update(
            session, [PriceUpdateItem(listing_id=APRON, new_price=60.0)]
        )

    assert await backend.get_pending_changes(session) == []
    assert admin.calls == []


async def test_a_price_cannot_be_moved_through_a_listing_update(
    backend: ShopifyMerchantBackend, session
) -> None:
    """Otherwise the movement cap would be bypassed by naming the field differently."""
    with pytest.raises(GuardrailViolation, match="stage it as a price update"):
        await backend.stage_listing_update(session, APRON, {"price": 44.0})


async def test_two_staged_restocks_both_count_against_the_cap(
    backend: ShopifyMerchantBackend, session, config
) -> None:
    """One change per restock, each checked on its own: the cap is per change, and the
    preview shows one line per item, so a second change is a second decision rather than a
    way to double the first."""
    config.max_restock_quantity = 12

    first = await backend.stage_inventory_action(
        session, [InventoryActionItem(listing_id=APRON, action="restock", quantity=10)]
    )
    second = await backend.stage_inventory_action(
        session, [InventoryActionItem(listing_id=APRON, action="restock", quantity=10)]
    )

    assert {first.change_id, second.change_id} == {"chg-0001", "chg-0002"}
    assert len(await backend.get_pending_changes(session)) == 2
    with pytest.raises(GuardrailViolation, match="exceeds the 12-unit"):
        await backend.stage_inventory_action(
            session, [InventoryActionItem(listing_id=APRON, action="restock", quantity=13)]
        )


async def test_a_config_that_tightened_after_staging_blocks_the_apply(
    backend: ShopifyMerchantBackend, session, config, admin: FakeAdmin
) -> None:
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )
    config.max_price_delta_pct = 5.0

    with pytest.raises(GuardrailViolation, match="exceeds the 5% per-change limit"):
        await backend.apply_change(session, change.change_id)

    assert admin.calls == []
    assert backend.ledger.get(change.change_id).status is ChangeStatus.STAGED


# -- Applying ------------------------------------------------------------------------


async def test_applying_a_price_change_sends_one_variant_update(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )

    applied = await backend.apply_change(session, change.change_id)

    name, variables = admin.only_call()
    assert name == "SetVariantPrices"
    assert variables["productId"] == APRON
    assert variables["variants"] == [{"id": "gid://shopify/ProductVariant/10", "price": "44.00"}]
    assert applied.status is ChangeStatus.APPLIED
    assert applied.applied_by == "Dana"


async def test_applying_a_price_change_scales_the_whole_ladder(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """The staged price is the product's own — its first variant's. Every variant moves by
    the same ratio, so the approved price is the one the product shows and the spread
    between variants survives."""
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=STOOL, new_price=66.0)]
    )

    applied = await backend.apply_change(session, change.change_id)

    _, variables = admin.only_call()
    assert variables["variants"] == [
        {"id": "gid://shopify/ProductVariant/20", "price": "66.00"},
        {"id": "gid://shopify/ProductVariant/21", "price": "99.00"},
    ]
    assert any("+10.0%" in note for note in applied.guardrail_notes)


async def test_applying_a_listing_update_writes_the_mapped_fields(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    change = await backend.stage_listing_update(
        session,
        APRON,
        {"title": "Canvas bench apron", "seo_description": "A bench apron in cotton duck."},
    )

    await backend.apply_change(session, change.change_id)

    name, variables = admin.only_call()
    assert name == "UpdateProduct"
    assert variables["product"] == {
        "id": APRON,
        "title": "Canvas bench apron",
        "seo": {"description": "A bench apron in cotton duck."},
    }


async def test_an_older_api_version_gets_the_legacy_product_update(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """``productUpdate``'s argument was renamed. A deployment pinned to an older version
    rejects the modern document, and the older shape is tried once before the failure is
    reported."""
    admin.refuse.add("UpdateProduct")
    change = await backend.stage_listing_update(session, APRON, {"title": "Canvas bench apron"})

    await backend.apply_change(session, change.change_id)

    name, variables = admin.only_call()
    assert name == "UpdateProductLegacy"
    assert variables["input"]["title"] == "Canvas bench apron"


async def test_applying_a_restock_adjusts_the_tracked_variant_at_the_primary_location(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    change = await backend.stage_inventory_action(
        session, [InventoryActionItem(listing_id=APRON, action="restock", quantity=10)]
    )

    await backend.apply_change(session, change.change_id)

    name, variables = admin.only_call()
    assert name == "AdjustInventory"
    assert variables["input"]["reason"] == "correction"
    assert variables["input"]["name"] == "available"
    # ``changeFromQuantity`` is the quantity the store is told it is changing from, read
    # fresh rather than taken from the catalog cache: the apron holds 25, so a write against
    # any other number is a write against stock that moved, and the store refuses it.
    assert variables["input"]["changes"] == [
        {
            "delta": 10,
            "changeFromQuantity": 25,
            "inventoryItemId": "gid://shopify/InventoryItem/10",
            "locationId": "gid://shopify/Location/1",
        }
    ]


async def test_applying_a_pause_drafts_the_product(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    change = await backend.stage_inventory_action(
        session, [InventoryActionItem(listing_id=APRON, action="pause")]
    )

    applied = await backend.apply_change(session, change.change_id)

    name, variables = admin.only_call()
    assert name == "UpdateProduct"
    assert variables["product"] == {"id": APRON, "status": "DRAFT"}
    assert any("every sales channel" in note for note in applied.guardrail_notes)


async def test_applying_an_activation_publishes_the_product(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    change = await backend.stage_inventory_action(
        session, [InventoryActionItem(listing_id=CUSHION, action="activate")]
    )

    await backend.apply_change(session, change.change_id)

    _, variables = admin.only_call()
    assert variables["product"] == {"id": CUSHION, "status": "ACTIVE"}


async def test_applying_a_promotion_records_the_decision_without_writing(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    change = await backend.stage_promotion(
        session,
        PromotionDraft(
            name="Bench week",
            listing_ids=[APRON],
            discount_pct=10,
            starts="2026-09-01",
            ends="2026-09-07",
        ),
    )

    applied = await backend.apply_change(session, change.change_id)

    assert admin.calls == []
    assert applied.status is ChangeStatus.APPLIED
    assert any("change ledger only" in note for note in applied.guardrail_notes)


async def test_applying_a_campaign_records_the_decision_without_writing(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    change = await backend.stage_campaign(
        session, CampaignDraft(name="Bench week email", budget=500.0)
    )

    applied = await backend.apply_change(session, change.change_id)

    assert admin.calls == []
    assert any("change ledger only" in note for note in applied.guardrail_notes)


# -- Failure ------------------------------------------------------------------------


async def test_a_refused_write_leaves_the_change_staged_and_re_approvable(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """The ledger is advanced only after the store has accepted the write, so a failure
    leaves nothing to unwind and the operator can approve again once the cause is fixed."""
    admin.user_errors["SetVariantPrices"] = [
        {"field": ["variants", "price"], "message": "Price must be greater than zero"}
    ]
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )

    with pytest.raises(ChangeNotApplicable, match="still staged"):
        await backend.apply_change(session, change.change_id)

    assert admin.calls == []
    assert backend.ledger.get(change.change_id).status is ChangeStatus.STAGED

    admin.user_errors.clear()
    applied = await backend.apply_change(session, change.change_id)
    assert applied.status is ChangeStatus.APPLIED


async def test_a_partial_write_names_what_was_already_written(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """This is the one thing write-then-mark does not undo, so it is reported rather than
    smoothed over: the message the operator sees names the listings that did change."""
    change = await backend.stage_price_update(
        session,
        [
            PriceUpdateItem(listing_id=APRON, new_price=44.0),
            PriceUpdateItem(listing_id=STOOL, new_price=66.0),
        ],
    )
    # The second listing is deleted between the approval and the write.
    admin.products = [product for product in admin.products if product["id"] != STOOL]

    with pytest.raises(ChangeNotApplicable, match="Already written before the failure"):
        await backend.apply_change(session, change.change_id)

    assert admin.mutation_names() == ["SetVariantPrices"]
    assert backend.ledger.get(change.change_id).status is ChangeStatus.STAGED


async def test_a_store_with_no_active_location_cannot_take_a_restock(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    admin.refuse.add("PrimaryLocation")
    change = await backend.stage_inventory_action(
        session, [InventoryActionItem(listing_id=APRON, action="restock", quantity=10)]
    )

    with pytest.raises(ChangeNotApplicable, match="still staged"):
        await backend.apply_change(session, change.change_id)

    assert backend.ledger.get(change.change_id).status is ChangeStatus.STAGED


# -- Lifecycle ----------------------------------------------------------------------


async def test_a_change_cannot_be_applied_twice(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )
    await backend.apply_change(session, change.change_id)

    with pytest.raises(ChangeNotApplicable, match="not staged"):
        await backend.apply_change(session, change.change_id)

    assert len(admin.calls) == 1


async def test_an_unknown_change_id_is_refused(backend: ShopifyMerchantBackend, session) -> None:
    with pytest.raises(ChangeNotApplicable, match="no change with id"):
        await backend.apply_change(session, "chg-9999")
    with pytest.raises(ChangeNotApplicable, match="no change with id"):
        await backend.discard_change(session, "chg-9999")


async def test_a_discarded_change_leaves_the_store_alone_and_stays_in_the_ledger(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    change = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )

    discarded = await backend.discard_change(session, change.change_id)

    assert discarded.status is ChangeStatus.DISCARDED
    assert admin.calls == []
    assert await backend.get_pending_changes(session) == []
    # The audit trail keeps it.
    assert backend.ledger.get(change.change_id) is not None


# -- Fields this deployment cannot write --------------------------------------------------


async def test_a_field_this_deployment_cannot_write_is_refused_when_it_is_staged(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """At staging, not at apply. A field with no Admin path would otherwise stage cleanly,
    reach the operator as a card with an Approve button, and fail on the click — after the
    approval, which is the wrong end of the gate. The message names what can be written,
    because the model's next move is to pick one of those."""
    with pytest.raises(ChangeNotApplicable) as refusal:
        await backend.stage_listing_update(session, APRON, {"warranty_months": 24})

    assert "warranty_months" in str(refusal.value)
    assert "seo_description" in str(refusal.value)  # the writable set, named
    assert backend.ledger.pending() == []
    assert admin.calls == []


async def test_a_field_with_its_own_tool_is_still_refused_by_the_guardrail_that_names_it(
    backend: ShopifyMerchantBackend, session
) -> None:
    """`price` has no listing path either, and must not be reported as unwritable: it has
    its own tool, and the guardrail is what says so."""
    with pytest.raises(GuardrailViolation, match="stage it as a price update"):
        await backend.stage_listing_update(session, APRON, {"price": 44.0})


async def test_the_interfaces_two_description_fields_both_write_the_products_body(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """`Listing.short_description` is the field a model reads, and therefore the field it
    names when it stages an edit. Shopify has one `descriptionHtml`, and either name reaches
    it."""
    change = await backend.stage_listing_update(
        session, APRON, {"short_description": "Waxed canvas, four pockets."}
    )

    await backend.apply_change(session, change.change_id)

    name, variables = admin.only_call()
    assert name == "UpdateProduct"
    assert variables["product"]["descriptionHtml"] == "Waxed canvas, four pockets."


async def test_both_description_names_in_one_change_are_refused_rather_than_resolved(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """Two fields in the interface, one field in Shopify. Writing both in one change would
    silently keep whichever the loop reached last."""
    change = await backend.stage_listing_update(
        session,
        APRON,
        {"short_description": "Waxed canvas, four pockets.", "long_description": "A longer one."},
    )

    with pytest.raises(ChangeNotApplicable, match="one field on a Shopify product"):
        await backend.apply_change(session, change.change_id)

    assert admin.calls == []
    assert backend.ledger.get(change.change_id).status is ChangeStatus.STAGED

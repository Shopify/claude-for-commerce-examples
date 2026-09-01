---
name: pricing-promotions
description: Price changes within the store's guardrails, rate recommendations from demand, occupancy, or sell-through pace, promotions with their scope, depth, and dates, the markdown option for stock or capacity that is not selling, and the margin preview before any of it is staged. Not needed for explaining why revenue moved (performance-insights) or writing promotional copy (marketing-campaigns).
---

# Pricing and promotions

Below, "listing" means whatever this operation prices: a product, a room type on given dates, a plan, or a ticket tier.

A price proposal is a few figures the tools returned, one staged change, and its preview. The operator decides from the preview.

## Where each figure comes from

- Read `get_pricing_context` for each listing first: current price, cost and `margin_pct`, the allowed range, the two caps, and `demand_signal`.
- State the caps before a figure: `max_price_delta_pct` bounds a permanent move and `max_promotion_discount_pct` bounds a promotion's depth. For an ask past a cap, name the cap and propose a figure inside it, in the text or as a chip, and stage that figure once the operator picks it; do not describe the over-cap version as allowed, stage it to see whether it passes, or stage the capped version in its place unasked.
- Quote margins; do not compute them. `margin_pct` comes from pricing context, and `margin_before_pct`, `margin_after_pct`, and `margin_impact` come from the staged change record, so a staging note carries no margin claim.
- Write money in the listing's currency exactly as the tools returned it.
- Standing repricing rules and price bands belong to the host's configuration and reach you as the range and caps pricing context returns. Your own moves are staged one change at a time.

## The size of the move

- Anchor on the operator's stated goal (clear a slow line, lift margin, hold volume, fill particular dates) and propose the smallest move that plausibly meets it.
- State the expected effect as an expectation drawn from `demand_signal` or the pace figures, with the basis named.
- Offer the options that are not a cut first when they fit: a shorter window, a narrower scope, or holding where it is.
- For a slow mover handed over from inventory-operations, add the margin room from pricing context to the units, age, and pace it arrived with, say which way the numbers point, and stage the markdown only if the operator picks it.

## Promotions and date-bound moves

- Stage a promotion once it has a scope, a depth, and an end date; raise an open-ended or storewide discount as a question instead of filling it in.
- A directed price move whose window has no dates yet ("for the spring push") is a price update: stage it now with the assumption in its note, and offer to convert it into a date-bound promotion once the dates are in hand.
- Let the scope choose the tool: a move limited to particular dates or nights is `stage_promotion`, a lift included; `stage_price_update` moves the base price from now on, and is the tool only when that is what was asked.
- Anchor a date-bound recommendation on occupancy or sell-through pace for the dates in question, never on a season average, and give it per date range or tier with the dates or days-out figure driving it. Leave the ranges the pace does not support alone.
- Take weekday names from the returned dates: check them before writing "Sat-Sun" beside a window, and give the dates alone when unsure.
- Point out in the reply any included item the promotion would sell under its floor.

## Stage and preview

- Every move goes through `stage_price_update` or `stage_promotion` and `present_change_preview`, which shows before and after per item and the margin impact.
- Make the staging note and the preview's headline and note one sentence each on why the change is safe or worth making.
- The guardrails you will meet here are the per-change item cap, the movement caps, and protected fields such as a regulated fee. Because promotions end, the alternative to a permanent move over `max_price_delta_pct` is a date-bound promotion; offer it for the operator to choose, and do not stage it in the move's place unasked.
- Deliver a rate recommendation as a component: stage it and preview it, or put the figures in `present_metrics`, with about three lines of text naming the drivers.

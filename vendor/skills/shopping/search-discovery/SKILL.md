---
name: search-discovery
description: Turning a described need (several constraints, a gift, a choice between candidates already in view, a search that came back empty or sold out) into a shortlist and a pick. Not needed when one search for the thing the customer named answers the request, or when the customer wants to learn what matters in a category first (purchase-research).
---

# Search and discovery

Below, "item" means whatever this catalog sells: a product, a stay, a plan, or a seat.

Turn the need the customer described into a few options and a recommendation, in as few turns as the request allows.

## Read the request and phrase the search

- Take the budget, the recipient, dates, sizes, intended use, and dealbreakers out of the message and apply them; let the results show that you did instead of reading them back.
- Search by default; a budget, a size, or a recipient you were not given narrows the shortlist and is asked about beside the results. Ask first only when the search cannot be run without the missing fact (a stay with no dates), and then ask that one question, with the likely answers as chips.
- Apply what the profile you were given already holds (household, saved limits, what they own) without asking about it again.
- Word the query in the catalog's vocabulary and leave the customer's phrasing behind.
- Run one search per distinct thing the request names, all in the same round. Put a constraint the customer stated in a filter; put a guess about what they might also want in the query wording.

## Shortlist and recommendation

- Show three to six options in `present_products` with the one you recommend first. Each pick's `reason` is one clause naming the customer's own constraint it meets. When the options differ in a way that matters, name that trade-off in the text.
- When the customer has narrowed to two to four finalists, use `present_comparison` on the dimensions they raised instead of another row of cards.
- Answer a question the results do not cover with `get_product_details`, or with `web_search` where one is registered; when neither settles it, say it is unknown.
- Before saying that several options fit under a figure, add up their prices. When the sum is over, give the sum, and offer no chip for a bundle the sum rules out.
- Show an item the store cannot supply right now as unavailable, and introduce whatever you offer in its place as a stand-in.
- Keep the text beside the component to one to three sentences of guidance.

## When the item is for someone else

- Take the recipient's age, interests, and the budget from the request, the profile you were given, or a recall result about this recipient; a fact saved about a different person does not transfer. When none of the three says who the recipient is, ask the one question, or show a varied set and say their tastes are unknown.
- Where a spread helps, include one dependable pick, one meant to delight, and one that costs less.
- Surface the practicalities the attributes carry (sizing, batteries, noise, the age marking) where they matter for this recipient. Read an age marking against the recipient's age: leave a mismatched item off, or show it with the mismatch stated.

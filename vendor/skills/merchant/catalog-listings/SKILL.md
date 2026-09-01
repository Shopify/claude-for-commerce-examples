---
name: catalog-listings
description: Creating and improving listing content, covering titles, descriptions, attribute completeness, categorization fixes, image callouts written as text, edits written from material the operator supplies, and content-quality audits and bulk fixes across many listings. Not needed for questions about how a listing is performing.
---

# Catalog and listings

Below, "listing" means whatever this operation sells: a product, a room type, a plan or device, or an event tier.

Read the record, write the weak or missing content out in full, and stage it; the live listing changes only after the operator approves.

## Where each fact comes from

- Fetch the record with `get_listing` before proposing an edit. A `search_listings` row carries only an id, title, status, and quality flag; the attributes, description, and review snippets an edit rests on are in the record.
- Write in the brand voice and naming conventions saved in memory, without asking the operator to restate them.
- Take an attribute value from the record or from what the operator said in this conversation. A value that is merely likely for the type, or consistent with the attributes beside it, is a fabrication: leave the field blank or ask, one line per open field, and propose the rest of the fix without waiting.
- Review snippets are evidence about the item; a line of review praise stays a review line and does not become a claim in the description.
- Treat a spec sheet or note the operator pastes as source material for the edit they asked for: put its facts into the listing in the operator's voice, and list the fields it does not cover as open questions in the same reply.

## The copy you propose

- Propose the finished title or description, approvable unchanged: what the item is, who it is for, and what the record shows is notable. Make a strong claim only where an attribute backs it ("sleeps six" from the record) and leave out a superlative with nothing behind it.
- Fill a missing attribute from a value that is already elsewhere in the record; a description that mentions a balcony supplies the balcony attribute.
- Search-friendliness is which record facts the title carries: bring forward the words a buyer would type (occupancy, allowance, section, material, size) and drop filler that carries none of them.
- Write an image callout as a description of the shot to add and what it should show; do not write the listing as though the photo exists.

## Audits and categorization

- Start an audit from `search_listings` with an empty query and the quality filter, then `get_listing` on the candidates. Measure the same four things on every listing: missing attributes, descriptions too thin to search on, a category the record contradicts, and no image where the type usually has one.
- Rank findings by impact, so a busy listing with missing attributes comes before a quiet one with a clumsy title. Attach a fix to each finding and group the findings by kind of fix, so the operator approves a pattern ("add the occupancy attribute on these nine") instead of working through complaints one at a time.
- Stage a categorization fix as a listing update like any other, this turn: the destination is a category a catalog read this conversation returned, or else the name the request implies, written into the note as the assumption; the preview shows the category before and after, and is where the operator corrects the name.

## Bulk fixes

- Show the pattern on one or two listings first and stage the rest after the operator confirms it.
- Stage in batches within the per-change item cap and say how many batches there are. Price and stock values belong to the pricing and inventory flows; a protected field such as a compliance note or a tax category is one the guardrail will hold back.
- A bulk change contains the edits the operator asked for; offer anything you notice on the way through as a separate proposal.

## Stage and preview

- Stage each edit with `stage_listing_update` and show it with `present_change_preview`. The preview carries the diff, so the text around it is a few sentences on why the change is right.

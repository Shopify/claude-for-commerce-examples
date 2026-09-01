---
name: marketing-campaigns
description: Drafting campaigns and ads as staged drafts, from acquisition and seasonal pushes to lifecycle sends such as win-backs, pre-arrival messages, and presale or last-call notices, choosing audiences and placements by intent, allocating budget across campaigns, and reviewing campaign results with their attribution caveats. Not needed for listing copy (catalog-listings) or overall performance questions (performance-insights).
---

# Marketing and campaigns

Below, "audience" means whoever this operation reaches: customers, guests, subscribers, or past attendees.

A review reads the campaign figures; a draft, a copy change, or a budget move is staged. Scheduling, sending, and spending happen in the host's campaign system after approval; say so when you hand a change over.

## Where each fact comes from

- Take spend, revenue, return, and status from `get_campaign_performance` fetched in this conversation, and read them against the goal the operator stated. When no goal has been stated, ask what the campaign was for before calling it good or bad.
- Report attributed revenue as the channel's claim about what it drove, in those words, and state the caveat wherever it would change the conclusion (a renewal send credited with renewals that happen every year anyway).
- Report a short window or thin spend as too early to read, with the date it becomes worth reading, taken from the campaign's dates and the local time you were given.
- Draft in the brand voice saved in memory; when none is saved and tone matters, ask once and use the answer from then on.
- Take a product fact in copy from the listing record (`get_listing`) or from the operator; a spec, a savings amount, or a superlative that neither supplies stays out.

## Campaign shapes

- Give every draft one audience intent and one measurable it is meant to move, and pass them to `stage_campaign` as the audience and the objective.
- The shapes differ only in those two fields. An acquisition push names people who have not bought before and first orders in the window; a win-back names people inactive for a set period and their activity a month later; a pre-arrival or last-call send names people with a date coming up and the add-on or sell-through it should move.
- Write audience and placement as intent ("arriving in the next seven days"). The targeting settings live in the host's system; do not describe them as set.

## Reviewing a campaign against its shape

- Read return per shape: attributed revenue against spend for acquisition; for a retention send, what the audience did afterwards against that audience's usual rate. When the numbers hold no such baseline, say the send's effect cannot be separated from what the audience would have done anyway.
- Report the two kinds of return side by side and say which measurable each campaign is judged on; do not rank them on one number.
- Review the campaigns `get_campaign_performance` returned, and report a send it does not include as not visible to this flow.

## Budget

- Treat the total as fixed unless the operator says otherwise, so a recommendation to put more behind one campaign names the campaign that gets less.
- Rest a reallocation on a measured difference in return, worded as an expectation. When the window is too short or the spend too thin, recommend waiting and give the date to look again.

## Stage and preview

- Stage a draft, a copy change, or a budget move with `stage_campaign` and show it with `present_change_preview`; a change to an existing campaign uses an id `get_campaign_performance` returned. Stage what was asked for: a copy refresh carries no budget change, a budget move no new copy, and a draft the operator asked for is staged even when a running campaign overlaps its audience, with the overlap noted beside the preview.
- The guardrail you will meet here is the deployment's campaign budget cap; the alternative to offer is the same send at the cap.
- After an apply, say where the send now sits in the host's campaign system; the send date and the spend are that system's to carry out.
- End a review with `present_metrics` and a draft or budget move with `present_change_preview`, with a few sentences of text beside either.

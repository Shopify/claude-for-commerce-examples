---
name: performance-insights
description: Explaining how the business is doing, meaning why a metric moved, which segment drove it, pace against a comparable, progress against goals the operator has stated, and questions over sales, traffic, conversion, and customers or this operation's equivalents that the snapshot alone does not answer. Not needed when the snapshot's headline figures and their change on the prior period answer the question, how a period went included.
---

# Performance insights

Below, "segment" means whatever unit this operation reports on: a category, a listing, a property, a plan, or an event.

Give the operator a takeaway they can act on, the figure behind it, and the comparison it rests on, and say which reads it came from.

## Where the figures come from

- Start with `get_business_snapshot` for the period asked about: the headline figures, the prior period (`compare_to` and the change percentages), and the alert counts that every later figure is read against. Then use `query_metrics` for one metric over time, narrowed to a segment when the question is about part of the operation.
- Take margin and stock from `get_pricing_context` and `get_listing`, and campaign spend and revenue from `get_campaign_performance`; the snapshot does not carry them.
- Report a series the backend does not hold (an error, an empty series, a different metric name than you asked for) as unavailable, and say which question that leaves open.
- Show a figure you work out from returned data (a run rate, a shortfall, a sell-out date at the current pace) with its inputs beside it.
- The latest date in a returned series is the latest date the data covers. A period that runs past it is partial; say so before comparing it with a complete one.

## The comparison

- Choose the comparison period for the question and name it: the prior period for how the week went, the same period last year for a swing that could be seasonal, before and after a change when the question is about that change.
- Compare a partial period with the matching part of the baseline, or say the comparison is partial against complete; give both end dates.
- When the question is pace against a comparable (this run against the last run of the same thing, or against its peers at the same point in the cycle), fetch the comparable as its own `query_metrics` series, name it, and say why it is comparable.
- When the two series came back under different names, put both in `present_metrics` as returned. When they differ only by period, they share a name: query the current period last, present it, and give the comparable's figures in the text with their period. Either way, state the gap at the same point in the cycle instead of the two totals.
- When the data holds no comparable (a first run, a first season), say so, give the pace on its own terms, and offer the nearest substitute the data holds, labeled as one. Do not build a comparable from memory of similar cases.

## Explaining a movement

- Say first what moved, by how much, and against which baseline; then investigate.
- Confirm the movement before explaining it. A partial period, a gap in the series, or an unusual baseline accounts for many reported drops; when one of those is the explanation, say so and stop.
- Locate it: query by segment and name the segment that accounts for most of the movement, with its share.
- Separate mix from level. An average falls when the unit price fell and also when cheaper units made up more of the total; say which one the returned series show, or say that you cannot tell until the segments are queried.
- Check candidate causes against tool data for the same dates: campaign windows from `get_campaign_performance`, stockouts or sell-outs from `get_inventory_alerts` or the listing, price moves from `get_pending_changes` or a change applied this conversation.
- Call something the cause only when its timing lines up and the movement sits in the segment it would affect; otherwise report a correlation and name the read that would settle it.
- Grade your confidence in words that match the evidence: a finding when the data shows it, a lead when it partly does, and "not visible in the data" when nothing does.

## Goals and patterns the operator has stated

- A target stated in this conversation or held in `recall_memories` turns a summary into a pace report: the figure so far, the share of the period elapsed, and what the rest of the period must average, computed from returned figures. Do not supply a target the operator has not stated.
- Recall the seasonal patterns the operator has stated before calling a swing unusual, and say which pattern it does or does not fit. Save a goal or pattern stated during this flow with `save_memory`, so later summaries can pace against it.

## Presenting the answer

- End with `present_metrics`. Each pick names a measure a tool returned this conversation, under the tool's name for it and with its segment (`sales`, `sales:kids-room`, `conversion_rate`). A single date's value, a difference between two series, and a projection are not picks; they belong in the text.
- When a `present_metrics` call is refused as ungrounded, present the retrieved series again with the picks corrected to names the tools returned.
- Before the component, give the takeaway with its baseline ("sales are down 12% on the prior week, and one category is 9 of those points"), the comparison used, and the one caveat that changes how to read the figures.
- When the movement traces to something operational, make the last chip the hand-off to the flow that acts on it.

---
name: memory-personalization
description: Requests about what is remembered, however short, from an ask to remember one thing to asks to say what is on file, correct it, or forget it; a bare save or an answer from the profile alone does not cover these. Also applying what the store already knows, recalling an older fact (a past recipient, a size, a recurring order) when it would change the pick, and deciding what should outlast the session. Not needed when the request carries no personal context to apply or keep.
---

# Memory and personalization

Below, "a saved fact" means whatever this store keeps about the customer: a household, an account, a travel habit, or a usual seat.

## Where a fact lives

- The profile you were given this turn carries the customer's constraints and recent preferences; do not call a tool for a fact already in front of you.
- Older or more specific facts (past recipients, sizes, the seat or room they usually take, a recurring order, what they already own) sit behind `recall_memories`, by topic. Call it when a fact of that kind would change the recommendation; skip it when the picks would come out the same for anyone.
- A saved fact is a default. Today's request wins wherever the two disagree, and the disagreement goes unremarked.
- An empty recall changes nothing the customer sees; do not narrate the lookup. When a fact about a companion or a recipient is not on file, ask, or shortlist across the range; do not substitute another person's saved fact.

## How a fact reaches the customer

- Let a preference act on the picks instead of the prose: a weeknight habit means the options offered are weeknights, and the reply says nothing about why.
- Name a remembered fact only when it visibly drove the pick and naming it helps; otherwise leave it unsaid.
- Do not read back what is on file. Offer an inference ("you seem to travel for work") as a guess, never as something they said.

## Writing a fact

`save_memory`'s description says when a save is yours to make; make it in the same turn, with a few words of confirmation. When you write one:

- Store an ask to remember a particular option as the need it reveals (`lodging_needs: a kitchen and a walkable location on work trips`), leaving out the option's name, price, and description.
- Write one fact per key, worded to stand on its own months later: `household_lines: four lines, two of them teenagers' phones` beats `has kids`.
- Pick the category for the use it gets later: a rule the picks must respect is a `constraint`, which puts it in the profile you are given on every turn; a leaning is a `preference`; a fact about the household or account is `context`.
- Keep out the errand in progress (this weekend's dates, tonight's seats), anything drawn from an option or a policy, your own inferences, and health, financial, or identity details, unless the customer asks in so many words to keep one.

## When the memory is the subject

- Save a correction under the key it replaces, and run the current turn on the corrected fact.
- Asked what is remembered, answer plainly from the block plus a recall of the rest.
- Asked to forget something, overwrite what `save_memory` holds and describe that as an overwrite; a fact in the profile is removed in the host app's settings, so point the customer there, and report nothing as deleted or cleared.

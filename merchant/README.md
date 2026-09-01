# ACME Supply Co. (Shopify merchant)

The merchant agent over a Shopify store. `MerchantBackend` is implemented against the
Shopify Admin GraphQL API: the products, orders, and inventory the agent reads are a store's
own, and an approved change is written back to it.

It runs two ways. Against a Shopify development store, where the schema, the scopes, and the
writes are real. Or against `api/local_store.py`, an in-process stand-in for one store's
Admin API that needs no Shopify account and no network: the same backend, the same documents,
the same stage/approve/apply path, and an approved change really does change the catalog the
portal is reading. Start with the local store to see the example work in one command; use a
real store to find out whether it works.

It covers the merchant half only. The buyer half of a Shopify integration is different work,
Universal Commerce Protocol endpoints and Sign in with Shop, and it is this repository's other
example, `storefront/`. Here the storefront is the real one.

## Status

Everything below is covered by the test suite, which runs the real modules against canned
Admin API responses (`api/tests/fake_admin.py`):

- All sixteen abstract `MerchantBackend` methods, plus `get_merchant_context`.
- Staging writes nothing: every `stage_*` path is asserted to send zero mutations.
- Applying writes exactly one thing: each apply asserts the operation name and its variables.
- The approval gate: an apply from the chat path is held, the portal's button applies, and
  the approval mark does not outlive the click.
- The portal routes, which are the shared merchant routes, over this backend.
- One whole turn with the model scripted: each gate reached from the model's side, and
  the cache breakpoint holding the static prompt steady while the store's figures change.
- The local store, end to end: read the catalog through the portal's own route, stage a
  price move, press the button the card shows, and read the catalog again to find the new
  price, with nothing mocked between the route and the store.

Beyond the suite, `scripts/smoke_live.py` has been run against a Shopify preview store on
Admin API 2026-07: every read but `get_campaign_performance`, both safety claims, and one
reversible write. It caught four things no local stand-in could have caught, all now fixed.
The three inventory mutations require an `@idempotent` key, `ignoreCompareQuantity` is gone
from `InventorySetQuantitiesInput`, `changeFromQuantity` is typed optional and refused when
absent, and 2026-07 has only the `product:` shape, so the legacy `input:` documents in
`queries.py` are fallbacks for older pinned versions and not a live alternative.

## Run

### 1. The local store: no account, no token, no network

Install as the root README says, then run the two processes:

```bash
SHOPIFY_LOCAL_STORE=1 uvicorn merchant.api.main:app --port 8005
```

```bash
npm install && npm run dev -w merchant/web          # separate terminal · http://localhost:3105
```

`SHOPIFY_LOCAL_STORE=1` is the whole setup: it runs the example against
`api/local_store.py` and the portal prints which store you are looking at. Copying
`merchant/.env.example` to `merchant/.env` sets the same thing in a file, beside the key,
and is the way to keep it set; that file already has `SHOPIFY_LOCAL_STORE=1` in it.

The local store is seeded from `data/seed.json`, the same catalog the real seeder sends,
with two months of invented order history behind the seed orders so the metrics have a
prior period to compare against. Chat needs `ANTHROPIC_API_KEY`, from the environment,
`merchant/.env`, or the repo-root `.env`. Browsing the portal does not: `/showcase` renders
every card from `web/lib/showcase-fixtures.ts` and needs neither a key nor a store.

The local store dispatches on the same operation names `api/queries.py` sends, answers the
same node shapes, and applies the mutations it is given, so a staged change stages, the
approval gate holds it, and an approved change reaches the catalog the portal reads on its
next paint.

It is not a source of truth about Shopify. It cannot know sessions or conversion, so traffic
reads 0 and the agent is told why instead of being shown a zero. It answers the sales half of
ShopifyQL and refuses the rest. It does not carry the real schema, does not check scopes, and
rejects almost nothing a real store would reject. Two exceptions came from a real store,
because refusing where Shopify refuses is the whole value of it: an inventory adjustment has
to name the quantity it believes it is changing from, and one that names the wrong number is
rejected.

### 2. A Shopify development store

Use a development store: this example writes, and an approved change is a change to that
store's real catalog. Four steps, and the first three are clicks in the
[Shopify Dev Dashboard][dd-create].

1. Dev stores, then create a store. It has to be made here. A store created from the Shopify
   admin sits outside your organization, and an app's credentials are refused for a store
   outside the organization the app is in.
2. Apps, then create an app. Put the scopes from the table below on a version and release it.
3. Install the app on the store from step 1 and approve the scopes, then copy the client ID
   and secret from the app's Settings.
4. In `merchant/.env`, comment out `SHOPIFY_LOCAL_STORE` and fill in `SHOPIFY_SHOP_DOMAIN`,
   which is the `…myshopify.com` domain with no scheme, `SHOPIFY_CLIENT_ID`, and
   `SHOPIFY_CLIENT_SECRET`.

There is no token to copy, and none to replace tomorrow. `api/admin_token.py` mints one from
the client ID and secret on the first request and mints another when that one runs out, which
it will: a client credentials token lasts 24 hours and carries no refresh token, so either
something renews it or somebody pastes a fresh one in every morning.

The other way in is `SHOPIFY_ADMIN_TOKEN`, a `shpat_…` token minted or issued elsewhere, read
only when the client ID and secret are unset. The old path that produced those, an app created
in the store's own admin with its token revealed once,
[can no longer be created][legacy-custom-apps]; a token from an app made that way still works,
if you have one.

One more thing in [the token doc][dd-tokens] catches people out: the scopes come from the
app's released version, so adding one later means releasing a new version and approving it on
the store.

[legacy-custom-apps]: https://shopify.dev/docs/apps/build/authentication-authorization/legacy/admin-custom-apps
[dd-create]: https://shopify.dev/docs/apps/build/dev-dashboard/create-apps-using-dev-dashboard
[dd-tokens]: https://shopify.dev/docs/apps/build/dev-dashboard/get-api-access-tokens

| Scope | What needs it |
|---|---|
| `read_products`, `write_products` | Catalog reads; price and content writes |
| `read_inventory`, `write_inventory` | Stock levels and restocks |
| `read_orders` | The order scan behind metrics, demand signals, and order issues |
| `read_locations` | The location an inventory move applies at |
| `read_reports` | ShopifyQL metrics. Optional: with `SHOPIFY_DISABLE_SHOPIFYQL=1` every metric comes from the order scan instead |
| `read_marketing_events` | `get_campaign_performance`. Optional: without it the tool reports that it cannot read, rather than returning a zero |
| `write_draft_orders` | `scripts/seed_store.py` only, which places the seed orders as completed drafts. No agent tool creates an order; without it the catalog still seeds and only the order pass fails |

The scopes a minted token reports back are fewer than the ones you granted, because a read
scope its write counterpart already implies is left out: `write_products` comes back without
`read_products`, and nothing is missing.

The client secret, and every token minted from it, are privileged credentials for the scopes
the app was granted, so treat them as passwords for the store. The secret is read once, in
`api/agent_config.py`, and handed to `api/admin_token.py`, the only object that holds it and
the only one that ever holds a token; the transport asks that object for a token per request
and keeps none. Neither is sent to the model, returned by a route, written to a log, or
included in an error message, and the example persists them nowhere but `merchant/.env`, which
is gitignored.

**Seed it.** A new development store is empty, and an empty store makes every read return
nothing.

```bash
python merchant/scripts/seed_store.py --dry-run   # read what it would do
python merchant/scripts/seed_store.py
```

A handle the store already holds is left alone, prices and stock included, because a store
may have moved on since it was seeded and this script has no business deciding that a change
made afterwards was a mistake. `--reset-stock` sets stock on those handles as well, which is
what finishes a run that failed partway and left quantities at zero.

The seeder creates the eight products in `data/seed.json` and places nine orders. That is the
same catalog the test fixture describes, so a conversation held against a store and one held
against the suite go the same way. Each product is there for a reason, and `data/seed.json`
says what it is beside it: one ordinary product, one variant ladder, one with thin content and
low stock, one draft, one with no unit cost, one with untracked inventory, one slow mover, one
archived.

**Check it.**

```bash
python merchant/scripts/smoke_live.py --read-only   # reads and the gate
python merchant/scripts/smoke_live.py               # adds one reversible write
```

Every read in the interface, then the safety claim against the store: staging leaves the
price alone, an apply with no host approval is held, an approved apply writes, and the price
is put back. It needs no Anthropic credentials: the model is not in the loop, and the script
stages and applies through the same tool executor a conversation would.

Then start the API again without `SHOPIFY_LOCAL_STORE`, and it uses the store. With
`SHOPIFY_LOCAL_STORE` unset and the domain or token half-filled, the app still starts and
`/api/merchant/health` says what is missing.

## Try

In the portal, against either store:

1. What needs my attention this morning?
2. The bench dog set is nearly out and the listing is thin — restock it for a month at the current pace and write a description that covers what a buyer would ask. Show me both before anything goes live.
3. Looks right — approve the restock.
4. Raise the price of the folding step stool by 10%.

The third turn applies nothing by itself: `MERCHANT_REQUIRE_HOST_APPROVAL` is on, so the
agent's own `apply_change` is held on the approval gate and the change moves only when the
Approve button on its preview card is clicked. The fourth is the variant case: the stool has
two rungs at 60 and 90, and the staged change scales both.

## What is specific to this example

| Module | What it is |
|---|---|
| `api/admin_client.py` | The Admin GraphQL transport: a token per request from `api/admin_token.py` and none held, throttle-aware retries that read Shopify's own leaky-bucket figures, a 401 answered by minting again and retrying once, and `userErrors` raised rather than returned. The backend depends on the `AdminExecutor` protocol, not on this class, which is how the suite drives everything without a network |
| `api/admin_token.py` | Where the token comes from, and the only module that holds one. An app's client ID and secret mint a 24-hour token and mint another when it expires, under a lock so a cold start mints once; a pasted `SHOPIFY_ADMIN_TOKEN` is the other case and cannot be renewed |
| `api/queries.py` | Every document this example sends, in one file so the whole API surface it depends on can be reviewed at once. Each carries an operation name; the fake dispatches on it, so a renamed operation fails loudly |
| `api/catalog.py` | The product cache: one paged read at startup, `ProductRecord`/`VariantRecord`, the local scorer behind search, and the content-quality read |
| `api/orders.py` | The trailing order scan every metric, alert, and order issue is derived from, and the daily rows the sparklines use |
| `api/metrics.py` | `MetricsSource`: ShopifyQL when the store answers it, the order scan when it does not, and a note saying which, so the agent can state its source |
| `api/alerts.py` | Low stock, slow movers, delayed orders, and return spikes, computed from the catalog and the order scan against `data/thresholds.json` |
| `api/staging.py` | The write side: one staged change to one or more Admin mutations, the version fallbacks, and the partial-write message |
| `api/shopify_backend.py` | `ShopifyMerchantBackend`, the `MerchantBackend` over all of the above, plus the portal's KPI trends and insight cards |
| `api/store_view.py` | `ShopifyStoreView`: the three things the shared merchant router reads from a storefront (`store_name`, `products`, `recent_orders`), answered from the store |
| `api/merchant.py` | `create_merchant_portal`: the shared router over this backend, with an `executor` seam the suite uses to drive these routes rather than a copy of the wiring |
| `api/agent_config.py` | The only module that reads the environment, and where `SHOPIFY_LOCAL_STORE` chooses between the two stores. Analysis is off: this example exposes no SQL surface |
| `api/local_store.py` | The local store: one store's Admin API in process, seeded from `data/seed.json`, applying the mutations it is given. Dispatches on the same operation names as the transport it replaces, so a renamed operation fails here too |
| `scripts/` | `seed_store.py`, `smoke_live.py` |
| `web/` | The portal: overview, catalog, alerts, chat, and the three generative cards, over `../vendor/web-shared/` |

`data/thresholds.json` holds the alert rules, including the only place a single product's
low-stock threshold can be set. `data/seed.json` is the catalog the seeder creates.

## The approval path

`MERCHANT_REQUIRE_HOST_APPROVAL` is on by default, and with it on:

1. The agent calls a `stage_*` tool. `ShopifyMerchantBackend` reads the product, computes the
   preview, runs the guardrails, and records the change in the `ChangeLedger`. **No Admin
   mutation is sent.** `api/tests/test_staging.py` asserts that for every staging path.
2. The operator sees a preview card and clicks Approve. `POST /changes/{id}/apply` marks the
   change id as host-approved, runs `apply_change` through the tool executor, and drops the
   mark, so a later chat turn cannot spend it.
3. `apply_change` re-checks the guardrails against the current config, sends the mutations,
   and only then moves the ledger entry. A refused write leaves the change staged and
   approvable again.

The agent calling `apply_change` itself is held on the approval gate and told why. A change
staged in another session, or before a restart, is held on the provenance gate: the session
has to have seen it.

Three details about the gate:

- **Write, then mark.** The apply sends the mutations before moving the ledger entry, so a
  refused write needs no rollback. The one case that cannot be undone is a multi-listing
  write that fails partway; `api/staging.py` reports what was already written by name, and a
  test asserts that message.
- **A guardrail caught at apply time is still a guardrail.** `apply_change` raises
  `GuardrailViolation`, not a plain failure, so the model reports it on the same gate it
  would have hit at staging. This matters when the config is tightened while a change sits
  staged.
- **A field this deployment cannot write is refused at staging.** `stage_listing_update`
  checks the field against the Admin paths it has before it records anything, so an
  unwritable field never becomes a card with an Approve button. Refusing on the click instead
  would put the refusal after the operator's approval, at the wrong end of the gate. The
  refusal names the fields that can be written, because the model's next move is to pick one
  of them. A real turn exposed this: the model read `short_description` off the listing,
  staged it under that name, and the card was approvable before the write was.

## Tests

```bash
pytest merchant/api/tests -q
```

| File | What it covers |
|---|---|
| `fake_admin.py` | The suite's whole transport. Dispatches on operation name; records mutations rather than applying them, so `calls` is the assertion surface |
| `test_reads.py` | Every read, including the ones that must report an absence rather than a zero |
| `test_metrics.py` | Which source served a read. The ShopifyQL fixtures return figures deliberately unlike the order-derived ones, so no test can pass on the wrong source |
| `test_staging.py` | The safety claim: staging sends nothing, and each apply sends exactly one named mutation with known variables |
| `test_portal.py` | The shared routes over this backend, and the approval surface |
| `test_turn.py` | One whole turn with the model scripted: which gate stops an apply, what the tool results carry back, and that the cached prompt is the same bytes on every turn |
| `test_local_store.py` | The local store: every read answered, nothing invented that it cannot know, and the demo as a test — stage, approve through the route, and find the new price in the catalog |
| `test_scripts.py` | The seeder's mutation order, and that `smoke_live --read-only` really is read-only |

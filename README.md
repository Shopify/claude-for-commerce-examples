# Claude for Commerce examples

> [!NOTE]
> If you are building a storefront agent on Shopify using Claude, the following example
> will help you get up and running quickly. If you don't have a Claude account already,
> you can use [Shopify Inbox](https://apps.shopify.com/inbox) to set up an agent that can
> answer consumer questions on your online store.

Shopify implementations of
[Anthropic's commerce-agents blueprint](https://github.com/anthropics/commerce-agents):
`storefront/` runs the blueprint's shopping agent against a real Shopify store, and
`merchant/` runs its merchant agent over the same store's Admin API. Each example
is self-contained — its own API host, web app, fixtures, and tests — and they share only
`vendor/` and the blueprint's packages, which install from Anthropic's repository at the
commit pinned in `requirements.txt`.

## Storefront

A storefront shopping agent for a real Shopify store, built on
[Anthropic's commerce-agents blueprint](https://github.com/anthropics/commerce-agents)
and Shopify's [UCP](https://shopify.dev/docs/agents) endpoints. Point `SHOP_DOMAIN` at a
store and the agent searches its live catalog, builds a real cart, answers from its
policies and FAQs, and hands the customer to the store's own checkout page. The web app
brands itself from the store — name, logo, colors, best sellers — so the same code is a
branded storefront for any shop. Nothing here places an order or takes payment:
checkout, shipping, and payment all happen on Shopify's own pages.

The blueprint's packages (the shopping agent, its gates and skills, and the shared
commerce types) install from Anthropic's repository at the commit pinned in
`requirements.txt`; this repository adds the Shopify storefront on top of them,
unchanged. `vendor/` carries the blueprint's example scaffolding — see [NOTICE](NOTICE).

## Run

Python 3.11+ and Node 22.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                # add ANTHROPIC_API_KEY
SHOP_DOMAIN=your-store.com uvicorn storefront.api.main:app --port 8004
```

```bash
npm install && npm run dev -w storefront/web       # separate terminal · http://localhost:3005
```

`SHOP_DOMAIN` is any domain serving `/.well-known/ucp` (default `demostore.mock.shop`,
a Shopify demo store). `.env.example` lists every variable. To join a cart the store
already has, open the storefront with `?cart=<cart id>` (a storefront's `cart` cookie
value works as-is) or `POST /api/cart/attach`; writes re-read the cart first, so nothing
done elsewhere is overwritten.

## Try

1. I'm looking for a gift under $50 — what do you recommend?
2. Add the first one to my cart.
3. What's your return policy?

The grid, cart drawer, and checkout button in the web app stay in sync with the
conversation; the checkout button opens the store's hosted checkout page.

## Sign in with Shop (optional)

With Shop app credentials, buyers can sign in and `search_catalog` returns personalized
results. Set `SHOPIFY_UCP_CLIENT_ID` / `SHOPIFY_UCP_CLIENT_SECRET` (issued in the
[Shopify Dev Dashboard](https://shopify.dev/docs/agents)); without them the sign-in
routes answer 503 and every session is a guest — the guest path is identical to running
with no credentials at all.

Sign-in is authorization-code against Shop, server-side. Buyer tokens are keyed by
session and never reach the model, the browser, or the logs; each catalog call pairs
the token with the buyer's IP, as the catalog requires. An expired token re-mints once;
a failed re-mint falls back to guest.

## Checkout and orders

Checkout is a handoff. A non-empty cart is lazily staged as a UCP checkout
(`create_checkout`, re-synced after cart writes) and the customer finishes on the
store's own page via its `continue_url`. `complete_checkout` is never called — it
places the order and takes payment, which this repository never does.

Orders follow [Order MCP](https://shopify.dev/docs/agents/orders/order-mcp) v1
semantics: only orders placed through this agent are visible, on a buyer's ask (never
polled), and the credential must carry the `read_global_api_orders` scope. Without that
scope the backend disables its order tools and the agent tells the customer order
tracking is unavailable, pointing at the confirmation email instead.

## Merchant

The other side of the same store: the blueprint's merchant agent over the Shopify Admin
GraphQL API. It reads a store's products, orders, and inventory, proposes changes to them,
and writes back the ones the operator approves.

```bash
SHOPIFY_LOCAL_STORE=1 uvicorn merchant.api.main:app --port 8005
```

That needs no Shopify account. `SHOPIFY_LOCAL_STORE=1` points the backend at
`merchant/api/local_store.py`, an in-process stand-in for one store's Admin API seeded from
`merchant/data/seed.json`. Against a real development store, put `SHOPIFY_SHOP_DOMAIN` and
`SHOPIFY_ADMIN_TOKEN` in `merchant/.env` instead — `merchant/.env.example` documents every
variable, and `merchant/README.md` the scope each read needs — then seed it with `merchant/scripts/seed_store.py`
and check it with `merchant/scripts/smoke_live.py`.

Writes are gated, and that is the part worth reading the code for. The agent's `stage_*`
tools send no Admin mutation at all: a staged change is recorded in the ledger, and only
`POST /api/merchant/changes/{id}/apply` applies it. The Admin token is read once, in
`merchant/api/agent_config.py`, and handed to the transport; it never reaches the model, a
route, or a log.

This example is the host alone; it ships no web UI. `/api/merchant` serves the reads, the
chat stream, and the approval calls, and the operator-facing app is yours to build.

[merchant/README.md](merchant/README.md) is how to run it, against either store.

## Layout

- `storefront/api/`: the FastAPI host — the UCP MCP client (`ucp_client.py`), the
  blueprint's `StorefrontBackend` over it (`shopify_backend.py`), Sign in with Shop
  (`identity.py`), brand and catalog warm-up routes, and the agent configuration.
- `storefront/web/`: the branded storefront (Next.js) — product grid, cart drawer, and
  the assistant rail from the blueprint's shared web components.
- `storefront/scripts/`, `storefront/data/`: live smoke checks, the fixture recorder,
  and the recorded MCP responses the tests replay.
- `merchant/api/`: the merchant host — the Admin GraphQL transport (`admin_client.py`),
  every document it sends (`queries.py`), the catalog and order caches, the
  `MerchantBackend` (`shopify_backend.py`), the staging layer (`staging.py`), and the
  in-process local store.
- `merchant/scripts/`, `merchant/data/`: the store seeder, the live smoke check, the seed
  catalog, and the alert thresholds.
- `vendor/`: the blueprint's example scaffolding (`demo_common`, `web-shared`) and its
  skills, five per agent under `skills/shopping/` and `skills/merchant/`, carried from
  Anthropic's repository — see [NOTICE](NOTICE) for the small local edits.

## Tests

```bash
pytest                                         # recorded responses, no network
python storefront/scripts/smoke.py             # live check, guest path
python storefront/scripts/smoke_signin.py      # signed-in path; no-ops without SHOP_ACCESS_TOKEN
```

The storefront's tests replay `storefront/data/recorded_responses.json` through an
`httpx.MockTransport`; `storefront/scripts/record_fixtures.py` re-records from a live
store. The merchant's run the real modules against canned Admin API responses
(`merchant/api/tests/fake_admin.py`) and against the local store;
`merchant/scripts/smoke_live.py` is their live counterpart. None of these scripts needs an
Anthropic key.

## License

Apache-2.0. This repository includes code from
[anthropics/commerce-agents](https://github.com/anthropics/commerce-agents)
(Copyright Anthropic PBC) — see [NOTICE](NOTICE).

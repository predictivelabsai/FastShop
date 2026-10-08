# Phase 3B — Shopify migration connector

## Outcome

FastShop now has a bounded Shopify migration connector on the reviewed-plan framework introduced in
Phase 3A. It imports collections, products and variants, customers, and orders into the target site's
real tenant-owned catalog and commerce structures. A dry run stores the exact normalized snapshot and
a separate CSRF-protected confirmation consumes that plan once. Re-import updates Shopify-mapped rows
without creating duplicates.

The connector never reads or writes demo commerce structures, changes payment acceptance, allocates
inventory, creates payment records, or writes back to Shopify. No schema migration was needed: the
site-scoped `ExternalMapping` and `IntegrationPlan` tables from migration `20261008_0021` already cover
the connector.

## API choice

The connector uses Shopify's GraphQL Admin API pinned to `2026-10`. GraphQL was chosen because the REST
Admin API is legacy for new integrations, while GraphQL provides products, variants, collections,
orders, customers, pagination, and explicit shop/presentment money fields in one supported API. The
version is explicit in every request URL so Shopify cannot silently select an unversioned contract.

The connector sends backend-only POST requests to:

```text
https://{shop}.myshopify.com/admin/api/2026-10/graphql.json
```

It requires the minimum read scopes `read_products`, `read_customers`, and `read_orders`. Shopify's
standard `read_orders` grant covers its normal order-history window. Complete historical migrations
also need Shopify approval for `read_all_orders`; the bounded connector remains the same after that
scope is granted. Customer and order fields may also require Shopify protected-customer-data approval
outside development stores.

The bounds intentionally mirror Phase 3A: five pages per root resource, 50 root nodes per page, 750
total collection/product/variant/customer/order objects, 40 requests, 2 MB per response, and a
20-second request timeout. Redirects and environment proxy inheritance are disabled. A product may
include at most 100 variants, 25 collection memberships, and an order at most 100 lines in one plan;
a truncated variant or line connection refuses the run instead of applying an incomplete migration.

## Authorization path

This slice implements the operator-token path because a complete install flow requires a registered
Shopify app, controlled redirect origins, protected-customer-data review, and distribution settings
that are not available in this repository. Per-site configuration is:

```text
FASTSHOP_SHOPIFY_<UPPERCASE_SITE_ID>_ENABLED=true
FASTSHOP_SHOPIFY_<UPPERCASE_SITE_ID>_SHOP=merchant-store.myshopify.com
FASTSHOP_SHOPIFY_<UPPERCASE_SITE_ID>_ADMIN_API_ACCESS_TOKEN=...
```

There is no global credential fallback. The shop must be an exact lowercase `*.myshopify.com` domain;
schemes, paths, custom hosts, localhost, and arbitrary origins are not accepted. The token is used only
in the `X-Shopify-Access-Token` request header. It is never rendered, included in reports, logged, or
accepted from a merchant/shopper form. The merchant integration screen shows only readiness.

`FASTSHOP_SHOPIFY_FIXTURE_PATH` is a development-only offline seam. It is ignored in production and
supplies no real credential. The browser verifier and eval use it to exercise the same dry-run and
apply routes without network access.

### OAuth app-install follow-up

When FastShop controls a registered Shopify app, replace the operator token handoff with this exact
standalone-app authorization-code flow:

1. Create or link the FastShop app in Shopify's Dev Dashboard, choose the intended distribution, and
   register the exact HTTPS callback URI. Store the client secret in the operator secret manager.
2. Declare only `read_products`, `read_customers`, and `read_orders`. Request `read_all_orders` in the
   Dev Dashboard only for full-history migration. Complete protected-customer-data review for customer
   name/email/address fields before non-development-store use.
3. On install, validate the submitted shop against the anchored `*.myshopify.com` rule, generate a
   single-use cryptographic `state` bound to the authenticated FastShop user, tenant, site, and short
   expiry, then redirect to Shopify's `/admin/oauth/authorize` endpoint with the exact callback URI and
   requested scopes.
4. On callback, verify the stored `state`, timestamp freshness, exact shop match, and Shopify HMAC over
   the callback parameters with constant-time comparison before trusting the authorization code.
5. Exchange the code server-to-server at that validated shop's `/admin/oauth/access_token` endpoint.
   Store the resulting offline/refreshable token encrypted or in a secret manager keyed by site; never
   store it in a browser session, integration plan, log, or plain application form.
6. Query `currentAppInstallation.accessScopes` and refuse readiness unless all required scopes were
   granted. Handle expiry/revocation as “Needs operator setup” without exposing provider responses.
7. Register and HMAC-verify uninstall and Shopify privacy webhooks, make webhook processing idempotent,
   and remove/revoke the site credential on uninstall. Complete Shopify App Review and protected-data
   approval before distributing to unrelated merchant stores.
8. Verify the whole flow first against a Shopify development store: install, callback replay refusal,
   HMAC/state/shop failures, scope downgrade, token refresh or reauthorization, uninstall, and a fully
   offline mocked regression suite.

## Mapping decisions

- Shopify collections map to tenant `Category` rows and `collection` external mappings. Shopify
  collections have no imported parent hierarchy. A product is assigned to the first mapped collection;
  products without one use the deterministic **Shopify import** fallback category.
- Products map to tenant `Product` rows. `ACTIVE` maps to published; other Shopify statuses remain
  unpublished. The product type becomes the subtitle, sanitized description HTML becomes plain text,
  and the featured image uses the existing safe media URL boundary.
- Shopify product variants map one-for-one to `ProductVariant`, with prices in the target site's
  channel listing. Slug and SKU collisions with unrelated local data receive deterministic Shopify
  suffixes. Only an existing site-scoped Shopify mapping may update a row.
- Customers map by normalized email to site-owned `ShopCustomer` rows. Missing or invalid email is an
  unmapped dry-run item and is not applied.
- Orders map to tenant `Order` plus the target site's `SiteOrder`. Shopify fulfillment and financial
  display states map to FastShop's existing status fields. Lines retain immutable source name, SKU,
  quantity, unit amount, and total snapshots; a variant is attached only when its Shopify mapping is
  available. Historic/deleted catalog lines remain useful snapshots and are called out in the report.
- Imported orders do not allocate stock, charge a provider, create payment transactions, queue receipts,
  or emit checkout/outbox events.

## Money and multi-currency decision

Shopify supplies catalog prices in the shop's default currency and order money bags in both shop and
presentment currencies. FastShop deliberately imports only `shopMoney`. Presentment amounts may vary by
market and conversion time; mixing them with shop-currency catalog listings would make migration totals
and re-imports non-deterministic. The dry-run report always states this choice.

The connector reads `shop.currencyCode`, derives the ISO source scale (known zero- and three-decimal
currencies, otherwise two), and requires every imported order money bag to carry that same currency.
Amounts must be decimal strings. Parsing uses `Decimal`, multiplication by `10 ** source_scale`, and
`ROUND_HALF_UP`; floats, scientific notation, negatives, non-finite values, and oversized amounts are
rejected.

Examples:

- USD `31.005` at scale 2 → `3101` minor units.
- JPY `149.5` at scale 0 → `150` minor units.
- KWD `1.2345` at scale 3 → `1235` minor units.

## Switch-from-Shopify workflow

`/admin/sites/{site_id}/integrations` lists Shopify alongside WooCommerce with the same credential,
preview, report, and apply states:

1. **Run import dry run** fetches the bounded GraphQL snapshot and stores a 30-minute plan. It writes no
   catalog, customer, or order data.
2. The report shows fetched/create/update/skip counts, samples, warnings, and unmapped items, including
   invalid customers, unavailable collections, historic lines, and the shop-currency decision.
3. **Apply this reviewed plan** requires CSRF, the creating user, exact tenant/site/platform, plan id and
   version, and the explicit review checkbox. It atomically consumes the plan once and does not refetch
   Shopify.
4. Running a second dry run reports mapped rows as updates; applying it updates in place with no duplicate
   products, variants, customers, orders, site-order links, or external mappings.

## Fixture and eval

`tests/fixtures/shopify_store.json` is a store export containing two collections, two products, three
variants, one valid and one invalid customer, one order, a deleted historic order line, exact decimal
prices, and EUR presentment values beside USD shop values. `httpx.MockTransport` converts it into
paginated GraphQL responses. Tests and evals make no live network calls.

`python -m evals.shopify_connector` runs dry-run → apply → second dry-run → second apply against an
in-memory database. Result: **10/10 checks passed**. It covers preview purity, all resource counts,
unmapped warnings, shop-currency selection, first application, exact USD rounding, update-only
re-import, duplicate prevention, and the pinned API version. Reports are written to
`output/evals/shopify_connector_results.{json,md}`.

## Verification

Verified on 2026-10-08:

- Ruff passed for the full repository, and compile checks passed for app, tests, evals, scripts, and
  migrations.
- Full isolated SQLite suite: **413 tests collected and passed**, including the settled 50-render golden
  baselines, with only the existing Starlette/AnyIO `BlockingPortal` deprecation warning.
- Empty SQLite upgrade through `20261008_0021` and `alembic check` passed. A populated SQLite database at
  `20261008_0021` also upgraded to head and passed `alembic check`. No `0022` migration was necessary.
- Focused Shopify plus existing WooCommerce connector suite: **13 passed**.
- Offline Shopify eval: **10/10 checks passed**.
- Browser verification used `scripts/verify_site_browser.py --merchant-only --shopify` against a fresh
  isolated database and the development fixture. It reported **3 checks, zero failures**, including the
  Shopify dry-run report, warnings, exact-plan apply, and existing menu/media merchant regression flows.
  Desktop and mobile integration/report captures were visually inspected under
  `output/playwright/phase3b-shopify/`.
- `git diff --check HEAD` passed. No live Shopify endpoint or PostgreSQL server was used.

## Files

- Connector registration and implementation: `app/connectors.py`, `app/integrations/shopify.py`.
- Shared reviewed-plan UI: `app/site_integration_routes.py`.
- Tests, fixture, and eval: `tests/test_shopify_connector.py`,
  `tests/fixtures/shopify_store.json`, `evals/shopify_connector.py`,
  `output/evals/shopify_connector_results.{json,md}`.
- Browser verification: `scripts/verify_site_browser.py`,
  `output/playwright/phase3b-shopify/`.
- Documentation: this file.

## Follow-ups

- Registered Shopify app installation with verified OAuth/token exchange, encrypted per-site token
  storage, protected-customer-data approval, privacy/uninstall webhooks, and optional `read_all_orders`.
- Streaming or Shopify bulk-operation imports for migrations larger than the bounded reviewed-plan
  window, including resumable multi-store migration orchestration.
- Webhook-based live mirror with replay protection, reconciliation cursors, conflict policy, and an
  outbox-backed write design. One-time migration plans must not become background sync jobs.

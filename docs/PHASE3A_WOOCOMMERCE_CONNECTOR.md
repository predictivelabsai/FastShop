# Phase 3A — connector framework and WooCommerce migration

## Outcome

FastShop now has a reusable connector registry and reviewed-plan workflow, plus a
complete WooCommerce REST v3 migration connector. WooCommerce API access remains
read-only: the connector imports into FastShop only after a merchant reviews a dry
run, while export produces a downloadable JSON bundle and never performs a live
provider write.

Imports use the real tenant catalog, `ShopCustomer`, `Order`, and `SiteOrder`
structures. They never read or write `DemoWorkspace`, `DemoCommand`, or demo
commerce state. Every mapping, plan, customer, order link, and site-owned query is
filtered by tenant and site; tenant-owned catalog queries retain tenant scope and
use the target site's channel for listings.

## Framework protocol

`app/connectors.py` defines and registers the small shared contract intended for
Shopify and WordPress:

- platform name, label, and declared import/export capabilities;
- operator credential readiness discovery;
- bounded provider fetch;
- dry-run normalization and report generation;
- application of an immutable reviewed payload;
- review-only export bundle generation.

Framework limits and capabilities are displayed on the site Integrations screen.
WooCommerce enforces five pages per resource, 50 rows per page, 750 total fetched
objects, 40 HTTP requests, a 2 MB response cap per request, and a 20-second timeout.
Redirects are disabled. Provider errors, response bodies, request URLs, keys, and
secrets are not logged or returned.

`IntegrationPlan` stores the normalized payload and report for 30 minutes. Apply
requires the plan id and version, the creating merchant, the exact tenant/site,
CSRF, and an explicit review checkbox. An atomic status/version claim consumes the
plan once; the transaction rolls back both the claim and data writes on failure.
Apply never refetches WooCommerce.

`ExternalMapping` now has optional legacy-compatible `site_id` storage and a unique
`(tenant_id, site_id, system, resource_type, external_id)` mapping. New connector
mappings always set `site_id`; pre-Phase-3 rows remain readable but are not selected
by site-scoped connector operations. Re-import updates mapped rows and does not
duplicate products, variants, customers, orders, or mappings.

## Credential and secret discipline

All connection data comes from operator environment configuration:

```text
FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_ENABLED=true
FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_URL=https://store.example
FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_CONSUMER_KEY=ck_...
FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_CONSUMER_SECRET=cs_...
FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_CURRENCY=USD
FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_CURRENCY_SCALE=2
```

There is no global credential fallback. The URL must be a public HTTPS origin with
no path, query, fragment, URL credentials, localhost/private literal address, or
obvious internal hostname. The merchant UI accepts none of these values; it shows
only **Ready** or an operator-setup message.

`FASTSHOP_WOOCOMMERCE_FIXTURE_PATH` is a development-only offline browser/eval seam.
It is ignored in production, supplies no real secret, and exists so the complete
review/apply UI can be verified without network access.

## Import mapping

- Categories create or update tenant categories and resolve parent mappings in a
  second pass. Missing source categories use one clearly named fallback category.
- Products create or update real tenant products. Simple products receive one
  stable mapped variant; variable-product variations are fetched and mapped
  individually. Site-channel listings store the normalized integer price.
- Customers create or update site-owned `ShopCustomer` rows by normalized email.
  Invalid-email source rows remain in the dry-run unmapped report and are skipped.
- Orders create or update tenant `Order` rows and the target site's `SiteOrder`
  bridge. Lines retain immutable source name/SKU/amount snapshots; mapped variants
  are attached when available. Unmapped historic items retain snapshots and are
  called out in the dry run. Imports do not allocate stock, emit payment records,
  charge providers, queue receipts, or change payment acceptance.

Slug and SKU collisions with non-mapped FastShop records receive deterministic
WooCommerce suffixes. Updates operate only through the target site's external
mapping, so an import cannot adopt or overwrite an unrelated local record.

## Money mapping

WooCommerce decimal amounts are parsed from strings with `Decimal`; floats,
scientific notation, negative values, and non-finite values are rejected. Values
are multiplied by `10 ** source_currency_scale` and quantized to an integer with
`ROUND_HALF_UP`.

Examples:

- USD `31.005` at scale 2 → `3101` minor units.
- JPY `149.5` at scale 0 → `150` minor units.
- KWD `1.2345` at scale 3 → `1235` minor units.

Catalog prices use the operator-configured store currency and scale. Orders retain
their own ISO currency; known zero- and three-decimal currencies use the matching
scale, while other currencies default to scale 2. Export formats integer minor
units back to fixed decimal strings at the configured scale, so no binary float
arithmetic occurs in either direction.

## Merchant workflow and export

`/admin/sites/{site_id}/integrations` lists registered connectors and their bounded
capabilities. Only administrators and merchants with membership in the exact tenant
can access or mutate it.

1. **Run import dry run** fetches the bounded source snapshot and redirects to a
   report with per-resource fetched/create/update/skip counts, samples, warnings,
   and unmapped items. It writes only the plan, never commerce data.
2. **Apply this reviewed plan** is a separate CSRF-protected POST. It requires an
   explicit review checkbox and the stored plan id/version, uses PRG, and consumes
   the plan once.
3. **Download review-only export JSON** returns categories and products as
   WooCommerce REST payloads with local/external references for review. The response
   is private/no-store and no remote request is made.

## Offline fixture and eval

`tests/fixtures/woocommerce_store.json` contains two categories, a variable and a
simple product, exact decimal prices, one valid and one invalid customer, one order,
and a historic unmapped order line. Tests use `httpx.MockTransport`; external network
access is neither needed nor attempted.

`python -m evals.woocommerce_connector` runs dry-run → apply → second dry-run →
second apply → export against an in-memory database. Result: **9/9 checks passed**.
It verifies zero commerce writes during preview, all resource counts, unmapped
warnings, first application, update-only re-import, no duplicates, and exact export
prices. Reports are in `output/evals/woocommerce_connector_results.{json,md}`.

## Verification

Verified on 2026-10-08:

- Ruff passed for the full repository.
- Compile checks passed for app, tests, evals, scripts, and migrations.
- Full isolated SQLite suite: **407 tests collected and passed**, with only the
  existing Starlette/AnyIO `BlockingPortal` deprecation warning.
- Empty SQLite upgrade through `20261008_0021` and `alembic check` passed.
- Populated pre-connector SQLite at `20261008_0020` preserved its legacy external
  mapping, added site scope and the reviewed-plan table, then passed `alembic check`.
- Offline WooCommerce eval: **9/9 checks passed**.
- Browser verification used `scripts/verify_site_browser.py --merchant-only
  --woocommerce` against a fresh isolated database and the development fixture.
  It reported **3 checks, zero failures**, including dry-run rendering, warnings,
  exact-plan apply, and the existing menu/media merchant regression flows.
  Desktop/mobile connector and report captures were visually inspected under
  `output/playwright/phase3a-woocommerce/`.
- The mechanical UI detector reported two pre-existing shared-style side-accent
  warnings in `e-entry` and `e-note`; this slice introduced neither and left the
  settled shared appearance unchanged.
- `git diff --check HEAD` passed. No live provider or PostgreSQL server was used.
  No commit was created.

## Files changed

- Framework/schema: `app/connectors.py`, `app/models.py`,
  `migrations/versions/20261008_0021_connector_framework.py`.
- WooCommerce: `app/integrations/woocommerce.py`.
- Merchant UI: `app/site_integration_routes.py`, `app/site_routes.py`,
  `static/site-editor.css`, `app/ui.py`.
- Tests/eval/fixture: `tests/test_woocommerce_connector.py`,
  `tests/fixtures/woocommerce_store.json`, `evals/woocommerce_connector.py`,
  `output/evals/woocommerce_connector_results.{json,md}`.
- Browser evidence: `scripts/verify_site_browser.py`,
  `output/playwright/phase3a-woocommerce/`.
- Documentation: this file and `docs/WOOCOMMERCE_STRIPE_INTEGRATION.md`.

## Follow-ups

- Shopify: OAuth installation, Admin API scopes, webhook/cursor reconciliation,
  and the same immutable reviewed-plan contract.
- WordPress: REST import for posts/pages/media and WXR export through this registry.
- Scheduled reconciliation: operator-owned schedules, cursors, audit visibility,
  bounded retry/backoff, and conflict policy. It must not reuse one-time migration
  plans as background jobs.
- WooCommerce remote writes: only after a separate outbox/reconciliation design
  with provider-specific idempotency and conflict handling. Live writes remain
  intentionally absent from this slice.

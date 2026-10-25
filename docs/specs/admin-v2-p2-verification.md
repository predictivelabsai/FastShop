# Merchant Admin v2 P2 verification

Work stayed in `fastshop-lovable` on `merchant-admin-v2`. No push, deployment,
branch switch, environment-file access or storefront renderer/editor changes.

## Delivered behaviour

Tenant-owned products retain existing categories, slugs, channel listings and
warehouse stock. New fields cover publication status, organisation, sanitised
description HTML, SEO, shipping and tax metadata. Normalised image, option and tag
tables support galleries and variant matrices. Collections use tenant ownership
and `SitePage.collection_id`, matching products' existing page association.

Services provide optimistic updates, archive/delete/duplicate, bulk operations,
ordered inventory locking and adjustment history, automated collection membership
and Shopify-column CSV export/import with dry runs and row errors. CSV updates
resolve handles or existing tenant SKUs, retain legacy variant data, and roll back
rejected product groups. Service savepoints preserve the caller's transaction.

The P1 shell hosts product indexes/editors, collections and inventory, with vanilla
rich text, media selection/upload/reordering, inline variant fields, contextual
saving, filter chips, bulk actions, CSV preview and inventory history drawer.
Legacy catalogue POSTs remain registered. API reads retain existing fields and add
catalogue metadata. Public product pages, catalogue reads and checkout reject
draft/archived products; membership-gated draft preview remains available.
Collection cards follow membership and saved ordering through a render-session
query boundary, with no renderer file changes.

## Isolation and commands

All application-dependent checks disable dotenv and provider/scheduler activity.
Each validation process receives a fresh repository-local disposable data path;
the browser verifier creates and removes its own temporary data directory.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHON_DOTENV_DISABLED='1'
$env:DB_URL=''
$env:FASTSHOP_ENV='development'
$env:FASTSHOP_AUTO_CREATE_SCHEMA='1'
$env:FASTSHOP_DATA_DIR=Join-Path (Get-Location) ('output/p2-validation-'+[guid]::NewGuid().ToString('N'))
$env:XAI_API_KEY=''
$env:POSTMARK_API_TOKEN=''
$env:POSTMARK_SERVER_TOKEN=''
$env:STRIPE_SECRET_KEY=''
$env:STRIPE_WEBHOOK_SECRET=''
$env:FASTSHOP_BILLING_STRIPE_SECRET_KEY=''
$env:FASTSHOP_BILLING_STRIPE_WEBHOOK_SECRET=''
$env:FASTSHOP_ENABLE_SCHEDULER='0'
uv run ruff check .
uv run python -m compileall -q app
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
uv run python -m pytest
node --check static/admin/catalog.js
node --check static/admin/admin.js
git diff --check
uv run python scripts/verify_admin_v2_catalog_browser.py
```

Results: Ruff, compileall, both Node checks and whitespace checks passed. Alembic
reached `20261025_0030`, downgraded to `20261011_0029` and upgraded again. The
migration test additionally exercises existing product data, escaped description
backfill, image backfill and collection association. PostgreSQL metadata DDL
compiled successfully using SQLAlchemy's mock engine. No live PostgreSQL server
was used.

Final full-suite result: **795 passed, 1 skipped, 1 warning in 894.37 seconds**.
The warning is the existing Starlette/AnyIO `BlockingPortal` deprecation.
Superseded broad runs were stopped while compatibility fixes were still being
made; the final run started after the implementation freeze. All required checks
passed. No databases, caches or temporary proof folders are included in commits.

Automatic approval review rejected cleanup as `blocked by policy`, both for the
verified repository-local list and for a single literal path using native
PowerShell `Remove-Item -LiteralPath ... -Recurse -Force`. The ignored
`output/p2-validation-*` directories and generated `__pycache__`, `.pytest_cache`
and `.ruff_cache` directories therefore remain local and require cleanup when
that restriction is resolved. No alternative deletion mechanism was used to
bypass the rejection.

## Browser evidence

`uv run python scripts/verify_admin_v2_catalog_browser.py` passed with zero browser
errors and no document overflow. It uses a generic local Northfield Supply fixture,
one store per viewport, with no external-provider calls or saved browser sessions.
The journey creates a product, generates options/variants, edits price/SKU/stock,
uploads an original in-memory image, saves and filters the index, bulk-sets Draft,
exports and previews/imports CSV without duplication, creates manual and automated
collections, and adjusts inventory with recorded history and Home low-stock tasks.

There are 28 reviewed PNGs under `output/playwright/admin-v2/p2/`, with `desktop-`
and `mobile-` prefixes at 1440x900 and 390x844. Captures cover products empty/new/edit,
filtered/draft indexes, CSV preview, manual/automated collections and rule preview,
collections index, inventory adjustment/history/low stock and Home low-stock tasks.
Full-page captures may be taller than the viewport. Review fixes include mobile
variant input widths, breadcrumb duplication, singular labels, filter chips and
the inventory history drawer.

## Open questions

No blocking product questions. PostgreSQL runtime/concurrency validation remains
an additional environment check beyond the required disposable SQLite run.
Temporary validation data cleanup remains blocked by automatic approval review.

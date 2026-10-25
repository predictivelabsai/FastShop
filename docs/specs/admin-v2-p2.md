# Merchant Admin v2 P2: Catalogue

Deliver a generic tenant-owned catalogue using the P1 merchant shell. Products keep
their existing slug, category, variant, channel pricing and site page associations.
Collections follow the same tenant ownership and site page publication boundary.

References: `docs/admin-v2/01-fastshop-current-state.md` establishes tenant-wide
catalogue ownership and per-site pages; `02-reference-shopify-woo-elementor.md`
defines compact index tables, a two-column editor, grouped cards and contextual
saving; `03-gap-analysis-and-plan.md` identifies P2 scope. P1 specifications and
verification notes establish the shared shell and isolated verification setup.
The merchant audience, catalogue workflows and Shopify visual direction come
directly from the task brief.

Work areas: models, Alembic migration and domain services; independent catalogue
routes, UI and assets; integration, public visibility, API and browser verification.
All work stays on merchant-admin-v2. No dispatch branches, branch switching, push
or deployment. The visual page editor and storefront renderer/block files are out
of scope and must remain untouched.

Acceptance checks: tenant isolation, role and CSRF gates, optimistic version checks,
option matrix preservation, integer money, inventory history with ordered locks,
automated collection rules, safe HTML and CSV dry-run/idempotent import. Preserve
old catalogue POSTs and public API fields. Hide draft/archived products in public
catalogue and checkout while allowing authenticated draft preview.

Run Ruff, compileall, migration upgrade/downgrade/re-upgrade on disposable SQLite,
full pytest, JavaScript syntax checks and whitespace checks. Exercise product,
media, variants, bulk, CSV, collections and inventory in Playwright and inspect
1440x900 and 390x844 screenshots under output/playwright/admin-v2/p2/.

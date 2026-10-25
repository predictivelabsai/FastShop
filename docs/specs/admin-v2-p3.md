# Merchant Admin v2 P3: orders and customers

## Goal and boundaries

Deliver generic tenant/site-scoped order and customer operations in the P1 shell.
Preserve the existing durable refund, fulfilment, provider resync and checkout
contracts. Work only on merchant-admin-v2 in this checkout. No push, deployment,
branch switch, secrets or environment-file access. P4 is explicitly excluded.
Revision 20261025_0031_orders_customers chains from 20261025_0030 independently
of the parallel P4 revision.

## References consulted

- docs/admin-v2/01-fastshop-current-state.md: SiteOrder joins real orders to a
  site/customer; durable refunds and shipment events already exist.
- docs/admin-v2/02-reference-shopify-woo-elementor.md: compact index tables,
  saved-view tabs, payment/fulfilment badges, two-column detail with payment,
  timeline and customer cards; progressive disclosure for operational forms.
- docs/admin-v2/03-gap-analysis-and-plan.md: remain server rendered, use the P1
  shell and vanilla JavaScript, add models through Alembic.
- P1/P2/P5 verification notes: disable dotenv and providers, isolate storage,
  preserve catalogue ownership and stock locks, use real Collections in editor.
- DESIGN.md and app/admin_ui.py/static/admin/admin.css: retain established shell
  typography, neutral palette, compact controls, responsive sidebar and cards.

## Code map and independent tasks

1. STEP 0: full baseline validation, repair integration failures, adapt editor
   resource API/schema/widget rendering to P2 Collection while preserving stored
   category references, add tests and commit separately.
2. Shared persistence: extend SiteOrder, OrderLine, ShipmentEvent and ShopCustomer;
   add comments, line fulfilment records, refund selections, returns and lines,
   drafts and lines, addresses, segments. Root owns models and migration.
3. Order services and routes: extend existing commands, add filtering/pagination,
   bulk/CSV, timeline/notes/tags, line fulfilment, exact line refunds and restocking,
   returns and packing slip. Root owns order modules and shared integration.
4. Customers: customer_admin service/routes/UI and focused tests; preserve public
   customer account routes and expose existing email queue as a sub-tab.
5. Drafts and abandoned checkouts: draft services/routes/UI and focused tests;
   reuse a shared existing order creation path with stable stock locks, no fake
   provider payments, no invented recovery/pay links.
6. Verification: root integrates, reviews, runs full validation and a real browser
   journey at 1440x900 and 390x844, inspects screenshots and records results.

## Acceptance

Service and route tests cover user-specified P3 flows, tenant/site isolation,
manager roles, CSRF, exact line maths/caps, stock changes and lock order, retries,
returns transitions, draft completion/reservation, CSV errors and email upserts,
filters/search/pagination. Home counts requested returns and open drafts.

Run Ruff, compileall, disposable SQLite Alembic upgrade/downgrade/upgrade, full
pytest, node --check for all static JavaScript, git diff --check. Browser evidence
belongs in output/playwright/admin-v2/p3. Delete temporary validation directories;
never commit databases, cache or session state. Commit logical reviewed changes.

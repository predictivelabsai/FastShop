# Merchant Admin v2 functional gap closure

Date: 25 October 2026. Worktree: `fastshop-admin-editor`, branch
`merchant-admin-v2-gaps`. No branch switch, push or deployment. Environment
files and existing secrets were not read. Provider and mail boundaries use local
test doubles; no real payment or email delivery was performed.

## Functional coverage

| Audit gap | Implementation |
| --- | --- |
| Custom data | Site-scoped definitions for products, variants, collections, customers, orders and pages; typed validation, required/bounds/choices, pinned order, owner cards, product/variant API reads and compatible text/image dynamic sources in product templates. Rich text uses the existing HTML sanitiser. |
| Store preferences | Homepage metadata, sharing image, favicon, hashed password with styled storefront form and merchant bypass, consent-aware Analytics/Meta IDs. Both storefront renderers use head metadata without redesigning the theme. Shared admin/auth/webhook/health paths remain available. |
| Redirects | Scoped searchable CRUD, CSV import/export, chain/loop prevention, repeated-handle alias flattening and default-on automatic redirects for product, collection, page and article handle changes. Real storefront routes take precedence. |
| Order editing | Variant/custom additions, removals, quantities and line discounts, required reason, optimistic order fingerprint and idempotent edit session. Stable stock locks and reservation adjustments preserve other orders' commitments, including after partial fulfilment. Payment difference supports queued invoices/manual payment or the existing refund flow. |
| Bulk editor | Selected product variants, prices/compare-at/cost in minor units, SKU/barcode/status/inventory tracking and stock by active location. Keyboard navigation, copy down, column picker, dirty cells and one save with per-row errors and optimistic fingerprints. Product groups use savepoints. |
| Saved views | Per-user/site named filters, sort and columns on product/order/customer indexes, rename/delete and persisted column preferences. Stored filters and columns are allowlisted. |
| Locations | Warehouse name/address/active/online fulfilment/default settings and protected deletion. Committed stock prevents deactivation or disabling fulfilment. Storefront availability, reservations and fulfilment respect location flags. |
| Activity | Transactional append-only actor/site/object/action summaries collected from main admin mutations. No form payloads, secret values or private notes are copied into the log. Filtered activity index and per-object cards. ORM updates, deletes and bulk mutation of log records are rejected. |
| Theme library | Current theme, isolated draft snapshots of design/settings/page documents, editor theme context, detached previews, rename/delete, atomic promotion and previous-live snapshots for rollback. Snapshot versions reject stale edits. |
| Gift cards | Sandbox issue/search/disable, customer/expiry/note, balance history and idempotent adjustments. Checkout reserves balance, caps tender to available funds/order total, sends only the balance to the provider and records separate tender on completion. Fully funded checkout makes no provider call. Full refunds restore the card once, including disabled cards. |

The out-of-scope markets, currencies, translation, purchasing/transfers and POS
features were not added. Existing shipping and tax are retained during order
editing and this is stated on the edit page. Recurring orders cannot use gift
cards. Gift-card checkout remains sandbox-only.

## Persistence and security

One Alembic revision, `20261025_0034_admin_gaps`, follows
`20261025_0033_merge_admin_v2`. It adds the feature tables and Warehouse flags,
backfilling a default location for existing tenants. New route registrars follow
the existing actor/CSRF/shell pattern. Mutation routes check tenant membership
and the appropriate merchant/admin permission; saved views are personal to each
authorised user, including editors. Owned references are site/tenant checked.

Order/provider money remains integer minor units. Gift checkout preserves gross
order totals separately from net provider payable. Reconciliation compares the
immutable provider capture rather than a subsequently edited order total.

Regression coverage also protects synthetic Liquid cart/account pages without a
product reference and existing Liquid product templates associated through their
channel listings. Partial fulfilment followed by an edit releases only that
order's remaining commitments. Adding items to a completed order reopens its
fulfilment state. Exhausting a split payment's provider capture during a partial
refund leaves the gift tender untouched until the remaining payment is explicitly
refunded. Theme promotion matches stable page identities before paths, preserving
catalogue handle changes made after duplication. Retired locations remain excluded
from editable product quantities while their stored stock is preserved.

## Validation evidence

The implementation is commit `993e19c`. The final complete suite passed
**1,157 tests, with one skipped and eight warnings, in 415.36 seconds**. The skip
is the existing optional presentation dependency. The warnings repeat the existing
Starlette/AnyIO BlockingPortal deprecation once per worker. No application change
followed this successful final run. No assertion was removed or weakened.

Checks use `PYTHON_DOTENV_DISABLED=1`, development mode, scheduler disabled and
explicit disposable SQLite storage. Each full-suite worker receives its own
`FASTSHOP_DATA_DIR` before collection via a temporary plugin, avoiding shared
application-bootstrap state. No test subsets or assertions were skipped to obtain
a pass. Provider acceptance uses deterministic local doubles.

```powershell
uv run ruff check .
uv run python -m compileall -q app tests scripts web_app.py
uv run alembic heads
uv run --with pytest-xdist python -m pytest -n 8 --dist loadfile `
  -p _gap_test_isolation -o addopts='' -q --tb=short --durations=12
Get-ChildItem static -Recurse -File -Filter *.js | ForEach-Object { node --check $_.FullName }
uv run python scripts/verify_admin_v2_gaps_browser.py
uv run python scripts/verify_admin_v2_all_browser.py
git diff --check
```

Ruff, Python compilation, all 24 JavaScript syntax checks and whitespace checks
passed. The temporary pytest plugin was removed after verification and is not committed.

The focused migration test has passed upgrade, downgrade to the previous merge
revision and upgrade again on explicitly disposable SQLite, with `alembic check`
before and after. The integrated migration-graph regression also passed.

Browser acceptance is implemented in `scripts/verify_admin_v2_gaps_browser.py`
and called by `scripts/verify_admin_v2_all_browser.py`. Both standalone and full
combined acceptance passed on fresh disposable storage. The combined run covers
the original catalogue, orders/refunds/returns, drafts, customers, growth,
permissions, Liquid checkout/subscriptions and full visual-editor journeys, plus
all ten gap journeys at both viewports. Browser, console, network and server
error lists are empty; the server stopped cleanly. The combined run also verified
12 successful downloads.

The 36 new journey screenshots use 1440 x 900 and 390 x 844 viewports (full-page
captures can be taller), with contact sheets used during visual review. Evidence and
summaries are in `output/playwright/admin-v2/gaps/`, including `results.json` and
`integrated-results.json`. The combined verifier also generated 203 legacy
captures; these tracked legacy artefacts were restored after verification to keep
this change's evidence within the gaps directory.

Visual review covered every gap screen at both widths. Fixes included stacked
custom-data forms and dynamic-source controls, mobile editor drawer handover and
readable activity filters. Final captures dismiss transient toasts and reset
horizontal table scroll so the first spreadsheet column is visible. Legacy
verifier selectors now distinguish filter textboxes/selects from column-picker
checkboxes without weakening their assertions.

## Open issues and operational boundaries

No live PostgreSQL concurrency test, real-provider acceptance or deployment is
part of this local verification. The existing provider readiness and rollout
gates continue to apply.

Automatic approval review rejected native PowerShell deletion of
`output/content-gap-tests` with `blocked by policy`. The following generated validation directories remain local and uncommitted;
no alternative deletion method was used:

- `output/content-gap-tests`
- `output/admin-gap-full-validation`
- `output/admin-gap-final-validation`
- `output/admin-gap-diagnostic`
- `output/admin-gap-release-validation`
- `output/admin-gap-commit-validation`

All completed validation paths were verified as ordinary directories inside this worktree
before the same native PowerShell cleanup was rejected with `blocked by policy`.

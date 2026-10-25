# Merchant Admin v2 P3 verification

## Changed

Work remained in `fastshop-lovable` on `merchant-admin-v2`. No push, deployment,
branch switch, environment-file access or P4 implementation took place.

STEP 0 is separately committed as `9495316`. Its initial full merged baseline
passed 897 tests, with one skip and one existing Starlette/AnyIO deprecation
warning. Editor resources now use P2 Collections, including automated/manual
membership, publication and site-page boundaries. Stored category IDs still
render. Collection lists use the same resource picker contract.

P3 adds:

- Real order indexes with views, literal search by number/email/customer/SKU,
  payment/fulfilment/date/tag/channel filters, sorting, pagination, filter chips,
  scoped bulk actions and CSV export of filtered or selected orders.
- Two-column order details with line quantities, fulfilment/tracking and optional
  queued notifications, payment totals, exact confirmed line/shipping refunds,
  restocking through P2 inventory adjustments, return lifecycle and refund links,
  merged notes/system timeline, tags, customer links, archive/cancel, previous/next
  navigation and a print stylesheet for packing slips.
- Stock provenance and primary-key-ordered locks. Successful unfulfilled refunds
  release their own reservations without increasing on-hand stock. Pending refunds
  reserve quantities and money. Repeated commands do not duplicate movements.
  Restocked returns are not restocked again when refunded. Failed pending refunds
  do not strand rounding remainders. Reused sessions refresh line counters under
  the order lock. Provider result reconciliation reacquires site/order/command
  locks in the same order as command creation.
- Drafts with variant/customer search, custom lines, quantities, discount, shipping,
  customer creation, invoice emails and explicit manual-paid completion. Completion
  reuses checkout reservation/finalisation and the order-confirmed outbox/mail path.
  No provider payment is fabricated. Manual-payment invoices have no online pay
  link and explicitly state that payment is arranged with the merchant. Automatic
  tax is not calculated for manually reviewed draft totals, and the editor says so.
- Abandoned checkout history uses real CheckoutAttempt/CommerceQuote/customer
  records. The empty state explains when tracking begins. Recovery links are
  unavailable because no safe recovery mechanism exists in this checkout.
- Customer search/filter/pagination, segments, bulk tags, Shopify-column CSV export
  and dry-run/import with row errors and email upserts. Detail/new forms include
  stats/orders, comments, contact information, addresses/default address, consent,
  tags, notes and account state. Admin consent records source/time and synchronises
  the existing email consent ledger. Email queue remains at `/customers/mail`.
- Home tasks for requested returns and open/invoiced drafts, with scoped counts.

Migration `20261025_0031_orders_customers` deliberately chains directly from
`20261025_0030`. The separate P4 head must be merged later, as requested.

## Tests and commands

All application-dependent runs disable dotenv, select disposable SQLite storage,
clear provider credentials and disable the scheduler before importing application
modules. No mail worker, reconciliation job or external provider was run.

```powershell
$env:PYTHON_DOTENV_DISABLED='1'
$env:PYTHONUTF8='1'
$env:DB_URL=''
$env:FASTSHOP_ENV='development'
$env:FASTSHOP_ENABLE_SCHEDULER='0'
# FASTSHOP_DATA_DIR selects repository-local disposable validation storage.
# Provider tokens are empty; no environment file is loaded.
uv run ruff check .
uv run python -m compileall -q app tests scripts web_app.py
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
Get-ChildItem static -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName }
uv run --with pytest-xdist python -m pytest -n 4 --dist loadfile -p _p3_test_isolation --durations=12
uv run python scripts/verify_admin_v2_orders_browser.py
git diff --check
```

The temporary `_p3_test_isolation.py` plugin sets `FASTSHOP_DATA_DIR` separately
for each worker before collection, without importing application modules. It is
deleted after verification and not included in commits. The initial integrated run passed **973 tests, one skip,
four existing deprecation warnings in 483.68 seconds**. Further service review
added stale-session/lock-order, pagination/filter composition and mail-after-refund
regressions. Final full-suite result: **987 passed, one skipped, four existing
Starlette/AnyIO deprecation warnings in 379.47 seconds**. The complete
order-route subset additionally passed **16 tests**, including an added regression
for short return reasons reaching refund review. All requested flows remain
covered; no assertion was skipped or weakened.

Ruff, compilation, all 19 JavaScript syntax checks and whitespace checks passed.
Alembic upgraded to P3, downgraded to P2 and upgraded again. Existing-row migration
coverage additionally verifies backfilled fulfilment/email-consent state and
preserved commerce rows after downgrade. PostgreSQL metadata DDL compiles; no live
PostgreSQL server was used. Targeted service/route suites cover tenant/site
isolation, manager roles, CSRF on all new order mutations, exact confirmation,
rounding/caps, pending/failed refunds, stock deltas/lock order, returns, completion
idempotency and rollback, CSV errors/upserts and filters/pagination.

## Browser evidence

`uv run python scripts/verify_admin_v2_orders_browser.py` passed with zero browser
errors at 1440x900 and 390x844. The generic Northfield Supply fixture and fake
refund gateway exist only inside the verifier. No credentials, saved browser
session or invented customer checkout data are used.

Journey: fulfil with tracking and queued notification, partial line refund with
restock, return request/approval/receipt/refund, comments/tags/notes, index
search/filters/select-all/export, draft variant search/invoice/completion, customer
search/filter/select-all/bulk tags, address editing/consent and CSV export.

Verified stock `(on hand, allocated)`: `(10,3)` before fulfilment, `(7,0)` after
fulfilment, `(8,0)` after partial refund/restock, `(9,0)` after return receipt,
`(9,0)` after refunding the received return, `(9,1)` after draft completion.

All 16 PNGs in `output/playwright/admin-v2/p3/` were inspected. Desktop/mobile
pairs cover order-before-fulfilment, refund-review, order-after-return,
orders-filtered, draft-invoice, draft-completed, customers-filtered and
customer-address-consent. Review fixed literal list artefacts in columns, mobile
filter overlap, payment row/checkbox alignment, duplicate notices, persistent
undismissible toasts, excessive timeline spacing and select-all wiring.

Browser temporary directories and failure captures were removed by the verifier.
No database or session-state file is included in commits.

## Open questions and cleanup limitation

No blocking product questions. Live PostgreSQL concurrency/provider acceptance is
an additional environment check, beyond the requested disposable SQLite suite.

Automatic approval review rejected native PowerShell deletion of verified
repository-local temporary validation directories with `blocked by policy`.
The rejected targets were `output/p3-step0-validation`,
`output/p3-focused-validation`, `output/p3-final-validation`, plus the customer's
`output/p3-customer-validation`. Final storage `output/p3-complete-validation` and `output/p3-migration-validation`
also remains local under the same cleanup restriction.
These directories contain disposable test databases and are excluded from commits.
No alternative deletion mechanism was used to bypass the rejection.

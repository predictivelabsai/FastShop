# Merchant Admin v2 P4 verification

Final complete-suite result: **980 passed, 1 skipped**, across three isolated
pytest processes. The skip is the existing optional `pptx` user-guide module.
Each process emitted the existing Starlette/AnyIO deprecation warning. All other
required checks passed, including 40 browser screenshots with zero browser errors.

Branch: `merchant-admin-v2-p4`. Base: `225a217`. No branch switches, pushes or
deployments. P3 order/customer models and `app/order_management*.py` were left
untouched. Changes to `app/site_routes.py` only register the four P4 registrars.

## Delivered behaviour

- Discounts: four types, code or automatic, multiple unique site codes,
  percentage/fixed minor units, product/collection targeting, email eligibility,
  minimum requirements, usage limits, bilateral combination flags and dates.
  Includes index/search/status/usage, type picker, conditional form fields,
  summary, edit, duplicate, deactivate, delete and bulk activation.
- Checkout applies product, order and shipping benefits deterministically.
  Explicit codes take precedence over incompatible automatic rules. Integer
  allocation uses stable ties, and shipping thresholds use discounted goods.
  Pending checkouts hold limited uses; unknown provider outcomes retain holds.
  Paid completion records each redemption once. Bridged Voucher completions
  update the existing counter without double-counting new redemption records.
  Deletion removes codes and hides the rule while retaining settlement and
  reporting references. Deleted legacy rules are not imported again.
- Subscriptions: merchant index/detail and audited pause/resume/cancel/skip,
  reusing existing scheduling services. No payment call occurs for these actions.
- Analytics: date presets/custom range, previous-period comparisons, sales,
  orders, AOV, returning buyers, SVG sales series, product revenue/units, channel
  and discount attribution, refunds and CSV. Simulator records are excluded.
  Conversion is omitted because no suitable session denominator exists.
- Content: searchable/filterable pages and blog indexes, guarded bulk publishing,
  visual/classic editor links, rich blog editing, excerpt, featured media,
  author/tags/category, SEO description and scheduled publication. Menus store
  validated nested trees with pointer drag and keyboard alternatives. Files
  include grid/list, upload dropzone, filtering, alt editing, references and
  reference-aware deletion.
- Settings: admin-only staff invitations/acceptance/role changes/removal with
  last-active-admin protection; payments, checkout, delivery zones/rates, tax,
  notifications, domains and policies. Existing billing and integrations remain
  linked through the shell. Home includes active discounts and subscriptions due.
- Notifications: bounded plain-text placeholders, escaped browser previews,
  queued tests only to the current user, per-event enablement and integration
  with existing account/order/shipment mail. Staff invitation tokens are stored
  as hashes and accepted once; existing users must sign in as the invited email.

## Schema

`20261025_0032_growth_settings` descends directly from `20261025_0030`, independently
of P3's `20261025_0031_orders_customers`. It adds discounts/codes/redemptions,
shipping zones/rates, tax rates, notification templates and staff invitations.
Upgrade/downgrade/re-upgrade passed on explicitly selected disposable SQLite.
The catalogue backfill test now pins its own revision rather than assuming the
current head is the catalogue revision. A separate P4 migration test checks its
head, table removal and restoration.

## Validation commands

All Python verification used the repository environment through `uv run` with
`PYTHON_DOTENV_DISABLED=1`, `DB_URL=''`, `FASTSHOP_ENV=development`,
`FASTSHOP_AUTO_CREATE_SCHEMA=1` and an explicitly selected disposable data folder.
Provider/mail/AI keys were cleared for full-suite execution. No environment file
or stored credentials were read.

```powershell
uv run ruff check .
uv run python -m compileall -q app tests scripts web_app.py
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
uv run python -m pytest -o addopts='' -q --tb=short
Get-ChildItem static/admin -Filter *.js | ForEach-Object { node --check $_.FullName }
uv run python scripts/verify_admin_v2_growth_browser.py
git diff --check
```

Ruff, compilation, migration round-trip, JavaScript syntax and whitespace checks
passed. The focused checkout/discount review covered 41 cases before the additional
legacy-counter regression; the later legacy bridge and content run passed all 15
cases. Settings, hosted-payment and mail regression runs passed 53 distinct cases.
Reporting/subscription verification passed 14 cases. The two migration tests passed.
The consolidated P4 run passed **78 tests in 440.39 seconds**, with the existing
Starlette/AnyIO deprecation warning. Two subsequently added focused regressions
for explicit discount defaults and free-shipping report savings also passed.
The final eight-test reporting run also passed after grouping variant sales by
their catalogue product, with historical/manual labels retained as a fallback.
Pre-P4 checkout fingerprints remain replayable when no delivery option is chosen.
Focused deletion tests verify that a reserved checkout can settle after deletion
and that a deleted legacy voucher does not reappear.

The initial serial full run reported 972 passed, 1 failed and 1 skipped. Its sole
failure was the WordPress import test's exact metadata expectation, which needed
the new empty `excerpt` and `publish_at` fields. That expectation was updated and
its focused test passed. Subsequent complete-suite verification used all 80 test
files, partitioned by sorted file index modulo three, with separate SQLite data
directories and pytest caches:

| Group | Files | Passed | Skipped | Seconds | Exit |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 | 27 | 370 | 0 | 190.24 | 0 |
| 1 | 27 | 323 | 1 | 326.78 | 0 |
| 2 | 26 | 287 | 0 | 363.96 | 0 |

A fresh default collection found 980 test IDs. Comparing that manifest with the
three pytest node-ID caches confirmed exact coverage, with no missing or duplicate
test IDs. The optional `pptx` module is skipped during collection.

Each group used this selection, with `$group` set to 0, 1 or 2 and its own isolated
environment/data folder:

```powershell
$files = @(Get-ChildItem tests -Recurse -File -Filter 'test_*.py' | Sort-Object FullName)
$selected = for ($i = 0; $i -lt $files.Count; $i++) {
    if ($i % 3 -eq $group) { $files[$i].FullName }
}
uv run python -m pytest @selected -o addopts='' -o cache_dir=output/p4-shard-$group/cache -q --tb=short
```

The final schema was separately upgraded, downgraded by one revision and upgraded
again on another fresh, explicitly selected SQLite file. Ruff, compileall, syntax
checks for all four admin JavaScript files and whitespace checks passed again.

## Browser evidence

`scripts/verify_admin_v2_growth_browser.py` passed with **40 screenshots and zero
browser errors**, using 1440x900 and 390x844 viewports. The fixture is a generic
Northfield Supply store on disposable SQLite, a local quote-only tax gateway,
in-memory image upload and independent merchant/shopper/invitee contexts. It
cannot take payment, sends no mail and saves no browser session state.

Each viewport covers creation of all four discount types, storefront code savings,
free shipping, analytics/CSV, visual editor navigation, blog publish/media, file
upload/alt edit, pointer menu reorder and keyboard nesting, invite acceptance in a
second context, configured shipping at checkout, tax at checkout and notification
preview. Screenshots are under `output/playwright/admin-v2/p4/` with `desktop-` and
`mobile-` prefixes. Full-page captures can exceed the viewport height.

Visual inspection prompted conditional discount fields, readable mobile menu
labels, a styled upload target, responsive four-metric analytics layout and primary
button styling. Refreshed screenshots were inspected after those fixes.
The final browser rerun also unnests a saved item, saves/reloads the tree, then
nests and publishes it again, guarding against stale child data during reordering.

## Deliberate boundaries and follow-ups

- Existing US/USD site checkout eligibility remains in force. Delivery rate
  services model countries/regions; this phase does not introduce new markets.
- Merchant tax rates apply to sandbox checkout. Hosted sandbox payment carries
  exact merchant tax as a labelled line with automatic tax disabled. Live payments
  retain reviewed Stripe Tax. Inclusive tax is extracted from both goods and
  delivery, and local subscription tax ledger entries never call Stripe Tax.
- Customer segments are not present in this checkout, so eligibility exposes all
  customers or specific email addresses. P3 segments can be added after merge.
- Stored menus preserve nesting; existing storefront renderers display links in
  traversal order. Dropdown presentation is a storefront follow-up.
- The existing P5 mobile visual editor is cramped. P4 verifies navigation into it;
  further editor viewport improvements belong to P5.
- PostgreSQL runtime/concurrency validation remains an environment follow-up.

## Cleanup restriction

Automatic approval review rejected recursive deletion with the reason
`blocked by policy`, both for the verified workspace-local list and for a single
literal, verified directory using native PowerShell `Remove-Item -LiteralPath`.
No alternative deletion mechanism was used to bypass that rejection.

These ignored folders therefore remain local and require cleanup when the
restriction permits it:

- `output/p4-validation`
- `output/p4-full-validation`
- `output/p4-final-validation`
- `output/p4-manifest`
- `output/p4-migration-final`
- `output/p4-shard-0`
- `output/p4-shard-1`
- `output/p4-shard-2`

The generated untracked `.sesskey` was removed using non-recursive deletion.
No database, cache, environment file, session key or browser session state is
included in the commits. Git status is clean after this documentation update.

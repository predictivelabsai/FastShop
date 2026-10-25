# Merchant Admin v2 integrated QA

Date: 25 October 2026. Repository: `fastshop-lovable`, branch `merchant-admin-v2`.
No branch switch, push or deployment took place. Environment files and existing
credentials were not read. No database, session key or browser session state is
included in the commits.

## Result

**Passed on isolated local storage.** The final full pytest run passed **1,084
tests**, with one existing optional-dependency skip. The combined browser run
passed every journey below at desktop and mobile sizes, with **203 screenshots**,
zero browser/network errors, zero server exceptions and a clean server shutdown.
The 17 pre-existing browser verifier modules also passed. Real-provider and
merchant-content prerequisites are listed below; nothing was deployed.

## Merge and integration

Merge commit `707220b` joins P3 and P4, retaining all model classes and both sets
of Home tasks. Drafts and Abandoned checkouts use their P3 routes; Subscriptions,
Discounts, Content and Analytics use the P4 routes. Settings entries link to real
screens. A static AST review found no duplicate model table names or normalised
literal route/method declarations across 90 model tables and 273 declarations.

Alembic revision `20261025_0033_merge_admin_v2` joins
`20261025_0031_orders_customers` and `20261025_0032_growth_settings`. On fresh,
explicitly selected SQLite storage, verification upgraded to `20261011_0029`,
upgraded to the single head, ran `alembic check`, downgraded to `20261025_0030`
and upgraded again. No schema drift was detected. Individual P3/P4 migration
tests now select their own revisions, and a new integrated migration regression
checks the complete graph and both branches' tables.

Integration regressions verify that active automatic discounts and configured
shipping zones do not overwrite a manually reviewed draft's explicit discount
or shipping amount. Refunds queue exactly one email per successful command,
including reconciliation, and none while pending. Each message uses that
command's amount, rather than the cumulative refunded total. Notification
template overrides and disablement apply after reference/ownership checks,
including the new draft-invoice event and P3 order/shipment messages.

## Merchant and shopper journeys

`scripts/verify_admin_v2_all_browser.py` runs the following sequentially on one
fresh disposable SQLite database and one running ASGI application. Merchants
sign in through the real `/login` form. Staff acceptance uses a second browser
context. Shopper contexts are separate from merchant contexts. Starting records
and external provider doubles are local fixtures; application routes and
commerce services are not mocked.

| Journey | Result | Assertions |
| --- | --- | --- |
| Sign-in, Stores, Home, search | Pass | Real sign-in, chooser, Home, keyboard global search, mobile navigation, real sidebar destinations |
| Products | Pass | Create, options/variants, media upload, price/SKU/stock, saved edit/reload, filtering, bulk status |
| Product CSV | Pass | Export, upload, dry-run preview and import without duplicate products |
| Collections | Pass | Manual collection, automated rules/preview and saved collection index |
| Inventory | Pass | Adjustment, history, low-stock filter and Home task |
| Orders | Pass | Index search/filters/export, fulfilment with tracking, partial refund/restock, comments, tags, packing slip |
| Returns | Pass | Request, approve, receive/restock and refund without a second restock |
| Draft orders | Pass | Create, variant/customer selection, invoice, manual-paid completion and resulting order |
| Abandoned checkouts | Pass | Empty state and a real browser-created unpaid checkout appearing in the index |
| Customers | Pass | Search/filter, tags, detail/address/default address, consent, CSV export/preview/import |
| Discounts | Pass | All four types, storefront cart code application and confirmed local sandbox checkout orders |
| Subscriptions console | Pass | Search/detail, skip, pause, resume and cancel; persisted state/version/due-date assertions |
| Analytics | Pass | Presets, custom dates, previous-period comparison and CSV |
| Pages and blog | Pass | Content index, create/publish article and verify public storefront content |
| Menus | Pass | Drag the actual handle, verify changed order, nesting/un-nesting, save/publish and verify public header order |
| Files | Pass | Image upload, alt text, saved library entry |
| Visual editor | Pass | Drag widget/container, nested children, inline edit, style, mobile canvas, undo/redo state, save/reload, public publish, exact revision restore |
| General settings | Pass | Discard, save draft settings and verify reload persistence |
| Staff | Pass | Invite, accept in another context, role change and last-admin protection |
| Payments, domains, billing | Pass | Existing readiness/configuration screens and real linked account billing screen |
| Checkout, shipping, taxes | Pass | Saved account setting, delivery zone/rate and configured tax reflected in checkout totals |
| Notifications, policies | Pass | Preview, queued test email and saved policy draft persistence |
| Editor permissions | Pass | Valid authenticated publish request denied; publication unchanged; staff reads/writes denied |
| Other tenant | Pass | 47 v2 and legacy URL families reject the other tenant's signed-in user at each viewport |
| Liquid shopper | Pass | Home, product, one-time/subscription cart, sandbox payment confirmation, sign-in challenge, account order/subscription pages and merchant console updates |

The verifier observes every browser context/page, console error, failed request,
HTTP error response and uncaught page exception. Intentional permission denials
are explicit authenticated request assertions. Chromium's `ERR_ABORTED` for a
successful attachment download is accepted only after the attachment response
has been observed. Server event-loop exceptions also fail the combined run.
The Windows server thread uses a selector event loop to avoid the Proactor
transport shutdown exception; Playwright retains its normal subprocess loop.

## Bugs found and fixed

| Finding | Fix | Commit |
| --- | --- | --- |
| Conflicting navigation/models/email branches and two migration heads | Retain both features, add merge revision and integrated regressions | `707220b` |
| Home fulfilment task silently ignored its query parameter | Use the P3 filter and retain the legacy alias | `707220b` |
| Refund notifications were absent; cumulative amounts would be misleading | Queue once per successful command and render its exact scoped amount | `707220b` |
| Draft invoices could not use merchant notification controls | Add invoice event, overrides and disablement with reference validation | `707220b` |
| Chromium rejected unescaped hyphens in HTML validation patterns | Escape the character in the product handle, site address and menu name patterns | `5e1f7b2` |
| Mobile visual editor clipped its canvas behind side panels | Use a full-width mobile preview and dismissible tool drawers; assert canvas bounds | `601f03b` |
| Long Liquid account order identifiers overflowed the 390px page | Allow the commerce heading to wrap; verify full mobile order detail | `7d2f039` |
| Saved edit and revision tests checked too little | Assert reloaded product copy, undo/redo state, published content and restored revision contents | `4689a1d` |
| Legacy verifiers targeted removed forms/routes and fixed local storage | Update journeys to current UI and explicitly isolated configured storage | `3bbee2a` |
| Liquid footer/privacy/social links used bare placeholders | Hide unconfigured footer anchors, retain plain privacy text and resolve configured policy links within the site | `4611838` |

Verifier corrections also target populated textareas by their control name/role,
use the actual menu drag handle and primary product purchase button, and wait
for iframe content before capturing it. Assertions were strengthened rather than
skipped. No intentional application error was added to the successful-run allowlist.

## Commands and evidence

All application-dependent commands set `PYTHON_DOTENV_DISABLED=1`,
`PYTHONUTF8=1`, `DB_URL=''`, `FASTSHOP_ENV=development`, scheduler disabled,
and explicit disposable `FASTSHOP_DATA_DIR`. Provider/mail/AI tokens are empty.

```powershell
uv run ruff check .
uv run python -m compileall -q app
uv run alembic heads
uv run alembic upgrade 20261011_0029
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run alembic downgrade 20261025_0030
uv run alembic upgrade head
$env:FASTSHOP_TEST_RUN_DIR = Join-Path (Get-Location) 'output/admin-v2-release-validation'
uv run --with pytest-xdist python -m pytest -n 4 --dist loadfile `
  -p _admin_v2_test_isolation -o addopts='' -q --tb=short --durations=15
Get-ChildItem static -Recurse -File -Filter *.js | ForEach-Object { node --check $_.FullName }
uv run python scripts/verify_admin_v2_all_browser.py
git diff --check
```

The temporary pytest plugin sets each worker's `FASTSHOP_DATA_DIR` before
collection, using `pytest_configure` and `config.workerinput['workerid']`.
This is a single full pytest invocation over the default collection, without
file subsets or manually sharded test selections. The plugin is not committed.
Choose a new empty validation directory when repeating the full-suite command.

Final full-suite result: **1,084 passed, 1 skipped, 4 warnings in 648.84 seconds**.
The skip is the existing optional presentation-document dependency. The four
warnings repeat the existing Starlette/AnyIO `BlockingPortal` deprecation once
per worker. No test failed. The focused semantic run passed 98 tests; the Liquid
content/link run passed 43 tests. Original storefront golden-render regressions
also passed without changing their fixtures.

Ruff, application compilation, single-head/schema-drift checks, the migration
round-trip, all **21** static JavaScript syntax checks and whitespace checks
passed. No assertion was skipped or weakened to obtain these results.

All 17 pre-existing verifier modules also passed: the five `verify_admin_v2_*`
phase verifiers, analytics, builder, customer, customer accounts, demo, Liquid,
Liquid M3, Liquid storefront, site, store checkout, subscription and US commerce.
`verify_demo_browser.py` is exercised through the builder verifier rather than
as a standalone entry point. Store checkout ran both one-time and subscription
variants. Self-hosted legacy scripts were run under the same isolation helper;
scripts accepting `--base` used a fresh local uvicorn application on disposable
storage. No existing running store was targeted.

The post-link-fix Liquid M3 run passed **975/975 checks**, 47 internal links,
48 captures, no placeholder links and no browser errors. The Liquid product
verifier passed **191 checks**. The broad site verifier passed **51 checks**.
Liquid storefront verification covered 375, 768, 1024 and 1440px, with 65 capture/
font evidence rows and the JavaScript-disabled native cart. Historical tracked
screenshots regenerated by these checks were restored; final evidence is kept
under `output/playwright/admin-v2/final/`.

Final evidence is committed as `9d3fb38`; the sequential verifier is committed
as `19330b1`. Its `results.json` records `status: passed`, empty `browser_errors`
and `server_errors`, `server_stopped: true`, 10 successful download URLs, and 47
foreign-tenant URL-family denials per viewport. The local payment adapter clicks
the real checkout button, executes the real app POST, checks its 303 destination
and intercepts only the subsequent provider navigation.

The **203 committed PNGs** comprise 103 captures at 1440px, 99 at 390px and one
supplementary editor capture at 1280px. Main viewport sizes are 1440x900 and
390x844; full-page captures can be taller. The visual editor additionally proves
both the mobile canvas and the outer 390px editor with open/closed tool drawers.
Every major screen was visually reviewed, including lower sections of long forms,
public order/account pages and the final mobile subscription/editor screens.
No remaining application clipping, overlap, unreadable controls or dead sidebar
links were found. Successful final captures replace the earlier timing/scroll
artefacts. Review contact sheets and failure diagnostics are excluded from the
committed screenshot count.

The last application change after the full Python run was the CSS-only heading
wrap in `7d2f039`, verified by the complete clean browser rerun and screenshot
review. No Python application change followed the successful full-suite run.

## Remaining limitations and cleanup

- Stripe/payment and refund acceptance uses deterministic local provider doubles.
  It proves application checkout/order behaviour, not acceptance by a real Stripe
  account. No real payment, email worker, renewal job, DNS change or deployment ran.
- PostgreSQL metadata compiles, but runtime concurrency was not exercised against
  a live PostgreSQL service. Migration acceptance here uses SQLite.
- Payments/domains/billing retain their existing readiness and operator/provider
  boundaries. Tests do not invent credentials, DNS records or a paid billing plan.
- Merchant-approved legal copy and social destinations remain a content task.
  Unconfigured Liquid footer links are hidden and privacy labels remain plain
  text until a destination is configured. No replacement legal text was invented.
- Draft orders explicitly record payment received outside FastShop and retain
  their manually reviewed discount/shipping; they do not calculate automatic tax.
- Abandoned checkout recovery links do not exist. The page explains this instead
  of offering an unsafe recovery action. Stored menu nesting is rendered in
  traversal order by existing storefront navigation.

Automatic approval review rejected native PowerShell `Remove-Item -LiteralPath`
cleanup with **blocked by policy**, after resolved absolute paths were verified
inside this repository. No alternate deletion mechanism was used. These new
validation directories therefore remain local and uncommitted:

- `output/admin-v2-merge-validation`
- `output/admin-v2-full-validation`
- `output/admin-v2-migration-validation`
- `output/admin-v2-notification-validation`
- `output/admin-v2-final-validation`
- `output/admin-v2-policy-link-validation`
- `output/admin-v2-release-validation`
- `output/semantic-validation`

The same review rejected non-recursive deletion of generated files. The temporary
`_admin_v2_test_isolation.py` and `.sesskey` remain locally, explicitly ignored
along with final-run failure diagnostics so they cannot enter an ordinary add.
Their contents were not committed. Regenerated historical screenshots remain
untracked at these blocked cleanup targets:

- `output/playwright/admin-v2/p1/desktop-collections.png`
- `output/playwright/admin-v2/p1/mobile-collections.png`
- `output/playwright/admin-v2/p5/desktop-published-editor-page.png`
- `output/playwright/admin-v2/p5/mobile-390-editor-restored.png`
- `output/playwright/admin-v2/p5/mobile-390-editor-widgets.png`
- `output/playwright/phase2-customer-flows/desktop-account-unavailable.png`
- `output/playwright/h24you-liquid/storefront-pass/account-subscriptions-375.png`
- `output/playwright/h24you-liquid/storefront-pass/account-subscriptions-1440.png`

The four earlier failed-run captures remain locally under
`output/playwright/admin-v2/final/`: `desktop-liquid-shopper-failure.png`,
`failure-0-0.png`, `failure-15-0.png` and `mobile-liquid-shopper-failure.png`.
Deletion of the **32** generated `review-*.png` contact sheets in that folder
was also rejected. All of these diagnostics are ignored and excluded from the
203 committed screenshots. Browser-created disposable database folders were
removed automatically after clean server shutdown; none remains.

No unresolved product question blocks the tested local journeys. Live provider,
database and merchant-content acceptance remain separate deployment prerequisites.

## Polish pass

25 October 2026. Scope: section 3 of `05-post-build-audit.md`. Work stayed on
`merchant-admin-v2` in this checkout. No models, migrations, feature-worktree
changes, environment files, push, deployment or branch switch were involved.

The shared merchant shell now supplies consistent select chevrons, date/time
controls, file dropzones, boolean switches, editable tag chips and button
hierarchy. Detail headers use a proper back icon button; legacy leading back
links move into that header. Breadcrumbs remain in the top strip. Links underline
on hover. Older media, menu, review, placeholder, snippet, integration, go-live
and inbox empty states use the shared SVG icon, title, explanation and action.

Index search is debounced and uses the existing tenant-authorised GET route.
Filters live in an Add filter popover with removable chips and a compact Sort
menu. Sorting covers products, orders, customers, discounts, collections,
inventory, drafts, subscriptions, pages and blog posts; draft sorting happens
before pagination. Search/filter forms still submit normally without JavaScript.
Selection bars reveal existing bulk commands only after selection. Rows open
their linked record, and mobile tables become labelled cards with an accessible
Select all control. Refreshes retain search focus, caret and filter controls,
and reinitialise selection and dialog handlers.

Discount creation starts with four described type cards. Method and value type
use segmented radios; eligibility and minimum requirements reveal their relevant
inputs. Product/collection selection uses a searchable dialog and removable
chips. The search authorises the site and scopes the existing shared catalogue
by tenant; those catalogue models have no site-id column. Native selection
controls remain available without JavaScript.

Orders expose Fulfil, Refund and Return in the header, with dedicated operation
pages and a More actions menu. Edit appears only when its GET route is actually
registered. Existing command URLs, CSRF checks, ownership checks and transaction
behaviour remain intact. Timelines group events by day, show merchant wording,
merge duplicate provider/refund-command events by reference and retain distinct
same-value refunds. Integration delivery and reconciliation details are behind
Show integration details.

The editor tree, widgets and section picker use schema labels and SVG icons,
with headings as muted subtitles. Hover actions, media dropzones, loading
feedback, toasts, modal labels, Escape handling, focus trapping and focus
restoration were refined. Shared asynchronous mutations retain entered values
and show an error toast on failure, while successful redirects restore focus
and show confirmation. Direct HTML POST results such as CSV previews retain
their server-rendered content. Reduced-motion preferences are respected.

Browser verification also exposed unused FastHTML CDN scripts in offline
journeys. Those unused defaults are disabled while charset and viewport headers
remain. Legacy payment verifiers now verify the real local POST and provider
303 before separately intercepting the simulated provider navigation, avoiding
accidental requests to fake live checkout URLs.

Validation passed on explicitly isolated local storage with dotenv loading and
the scheduler disabled:

- Full pytest: **1,088 passed, 1 skipped, 4 warnings in 1,002.21 seconds**. The
  existing optional-dependency skip and Starlette/AnyIO deprecation remain.
- Ruff, application/script compilation, all **22** static JavaScript syntax
  checks and `git diff --check` passed.
- The combined browser verifier passed at **1440x900** and **390x844**, with
  **259 canonical screenshots**, zero browser/network errors, zero server
  exceptions, 10 successful downloads and a clean server shutdown. Both
  viewports retained all 47 foreign-tenant URL-family denials.
- All five standalone admin phase verifiers passed. Assertions additionally
  exercise keyboard sorting, filter-chip removal, selection bars, modal focus,
  tag add/remove, search caret retention, JavaScript-disabled GET submission and
  a failed asynchronous save retaining entered values and enabling a retry.

Browser clicks now wait for enhanced POSTs to settle, just as native form clicks
previously waited for navigation. The wait observes the actual submitting state;
it does not suppress responses, skip assertions or relax persisted-state checks.
Screenshot rendering has a 60-second budget for concurrent Windows validation.
The normal action assertions remain in place.

Canonical evidence is in `output/playwright/admin-v2/polish/`, including
`results.json`, desktop/mobile captures of every admin page type and dedicated
discount/resource-picker, order-action, editor, sorted-index and error states.
Desktop/mobile product, index, content, settings and older-page contact sheets
were reviewed, together with individual order, discount and editor captures.
Spacing, double dropzone borders, mobile selection, content bulk bars and
refreshed-control handlers were corrected during that review. Supplementary
phase screenshots remain local and excluded from commits to avoid duplication.

The strict legacy runner passed **12/12 journeys**, covering every pre-existing
legacy verifier, demo through the builder journey, and both checkout variants.
Together with the five admin phases, all **17 pre-existing verifier modules**
passed. Its 12 `browser-observations.json` reports contain **zero unexpected
console, page, HTTP or request errors**. Explicit disabled-account, missing-page,
missing-checkout and invalid-cart negative assertions remain separately recorded;
no generic HTTP-error allowlist was introduced. The builder rerun confirms its
existing custom submit handler retains section selection and is not intercepted
by the shared enhancement. Results are in `polish/legacy/results.json`.

Automatic approval review rejected native PowerShell cleanup of the verified
temporary validation folders with **blocked by policy**. No alternate deletion
mechanism was used. The following newly created folders remain local and
uncommitted: `output/polish-focused-validation`, `output/polish-full-validation`,
`output/polish-final-validation`, `output/polish-sort-validation`,
`output/polish-release-validation` and `output/polish-review`. Failed-run PNGs
inside the polish evidence folder are ignored and excluded from its canonical
count. Browser-owned disposable folders are removed by their normal teardown.

No unresolved product question was found in the tested polish journeys. Live
provider acceptance and the separate feature worktree remain outside this pass.

## Release candidate

25 October 2026, `merchant-admin-v2`, this checkout only. Merge `c23addb`
retains the UX polish and all gap features. Follow-up commits preserve Liquid
role promotions, ensure the existing Helen account has Liquid admin membership,
integrate saved views with live filter bars, humanise edit/gift/activity events
and apply shared headers, controls and empty states to the new pages. The order
header Edit link is exercised through the real edit page and manual settlement.
Generic audit updates are grouped by actor/day in the merchant timeline; the
append-only activity history remains available. Its preview shows five readable
entries, with staff names and minute-precision UTC dates.

No push, deployment or branch switch occurred. No secrets or environment files
were read, and no database files are committed. Application-dependent commands
disabled dotenv, scheduler and external provider credentials and selected
disposable storage explicitly.

### Commands and results

Final full pytest: **1,182 passed, 1 skipped, 8 warnings in 378.11 seconds**.
This was the complete default collection after the final application changes.

```powershell
$env:PYTHON_DOTENV_DISABLED='1'
$env:PYTHONUTF8='1'
$env:FASTSHOP_ENV='development'
$env:FASTSHOP_ENABLE_SCHEDULER='0'
$env:DB_URL=''
uv run --with pytest-xdist python -m pytest -n 8 --dist loadfile `
  -p _rc_test_isolation -o addopts='' -q --tb=short --durations=12
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m compileall -q app tests scripts migrations web_app.py
Get-ChildItem static -Recurse -File -Filter *.js | ForEach-Object { node --check $_.FullName }
.venv\Scripts\alembic.exe heads
git diff --check
```

The temporary pytest plugin sets `FASTSHOP_DATA_DIR` in `pytest_configure` to
`output/rc-final-validation/<workerid>` before application imports, using
`config.workerinput['workerid']` (or `main`). A fresh directory is needed for
each repeat. No test selections, assertions or required checks were removed.
The existing optional presentation dependency accounts for the skip; the
Starlette/AnyIO BlockingPortal deprecation accounts for worker warnings.
Ruff, compilation, all **25** JavaScript syntax checks and whitespace checks
passed. Alembic has the single head `20261025_0034_admin_gaps`.

On explicit disposable SQLite storage, all of the following passed:

```powershell
$env:DB_URL='sqlite:///output/rc-migration-final/migration.db'
$env:FASTSHOP_AUTO_CREATE_SCHEMA='0'
.venv\Scripts\alembic.exe upgrade 20261011_0029
.venv\Scripts\alembic.exe upgrade head
.venv\Scripts\alembic.exe check
.venv\Scripts\alembic.exe downgrade 20261011_0029
.venv\Scripts\alembic.exe upgrade head
.venv\Scripts\alembic.exe check
```

The round trip exposed copied-column indexes with SQLite temporary-table names.
Revision `0030` now suppresses implicit copied-column indexes and creates the
stable model indexes explicitly. The graph regression now downgrades to `0029`
and checks for drift after re-upgrading.

### PostgreSQL evidence

Docker was available. PostgreSQL 16 used a disposable container with tmpfs data,
no persistent volume, loopback-only dynamic port `56615` and schema `fast_shop`:

```powershell
docker run --detach --rm --name fastshop-admin-v2-pg-check `
  --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_DB=fastshop_validation `
  --publish 127.0.0.1::5432 --tmpfs /var/lib/postgresql/data postgres:16
docker port fastshop-admin-v2-pg-check 5432
$env:DB_URL='postgresql+psycopg://postgres@127.0.0.1:56615/fastshop_validation'
$env:DB_SCHEMA='fast_shop'
$env:FASTSHOP_AUTO_CREATE_SCHEMA='0'
$env:FASTSHOP_H24YOU_LIQUID_HOSTNAME='h24you.example.test'
uv run alembic upgrade 20261011_0029
# Direct local seed functions: seed, seed_h24you and seed_h24you_liquid.
uv run alembic upgrade head
uv run alembic check
uv run alembic downgrade 20261011_0029
uv run alembic upgrade head
uv run alembic check
```

All migration commands passed. Representative data retained 3 tenants, 2 sites,
11 products, 23 variants, 2 warehouses and 3 memberships. Historical revision
`0001` imports current metadata, so an initial fresh `0029` upgrade alone does
not recreate the old schema. The populated downgrade to `0029` followed by
upgrade genuinely exercised adding the new columns/tables to old tables.
The final round trip was repeated after the `0030` index fix and passed.
No application tables appeared in `public`; no explicit constraint/index name
exceeded 63 characters. Alembic's schema-qualified internal version table was
excluded from application drift comparison, fixing a PostgreSQL false positive.

The full test suite's fixtures largely select SQLite directly. The shared
commerce fixture now optionally selects PostgreSQL using a unique, disposable
schema per test and removes that schema afterwards. With dotenv disabled,
`DB_URL=''` and `FASTSHOP_TEST_DB_URL` selecting the test container:

```powershell
uv run pytest -o addopts='' tests/test_us_commerce.py tests/test_checkout_services.py `
  tests/test_checkout_payments.py tests/test_gift_cards.py tests/test_locations_checkout.py `
  tests/test_growth_checkout.py tests/test_subscriptions.py tests/test_subscription_payments.py `
  tests/test_subscription_renewals.py -q --tb=short
```

**97 passed**, one existing warning, **115.78 seconds**: 83 PostgreSQL-backed
tests, 13 pure/provider-double tests and one SQLite-only test. Five gift-card
tests exercised PostgreSQL, including the three cases that exposed
`gift_card:<site_id>` exceeding `payment_transactions.provider VARCHAR(40)`.
The new tender identifier is `gift:<site_id>`; those cases now pass. Cross-tenant
test fixtures also create real foreign tenants rather than invalid foreign keys.
No per-test PostgreSQL schemas remained afterwards.

### Browser and visual evidence

```powershell
.venv\Scripts\python.exe scripts/verify_admin_v2_browser.py
.venv\Scripts\python.exe scripts/verify_admin_v2_catalog_browser.py
.venv\Scripts\python.exe scripts/verify_admin_v2_orders_browser.py
.venv\Scripts\python.exe scripts/verify_admin_v2_growth_browser.py
.venv\Scripts\python.exe scripts/verify_admin_v2_editor_browser.py
.venv\Scripts\python.exe scripts/verify_admin_v2_gaps_browser.py
.venv\Scripts\python.exe scripts/verify_legacy_browser.py
.venv\Scripts\python.exe scripts/verify_admin_v2_all_browser.py
$env:FASTSHOP_BROWSER_DB_URL='postgresql+psycopg://postgres@127.0.0.1:56615/fastshop_browser_final'
$env:DB_SCHEMA='fast_shop'
.venv\Scripts\python.exe scripts/verify_admin_v2_all_browser.py
```

The PostgreSQL browser database was separately created and upgraded to head
before boot, with automatic table creation disabled. The five phase verifiers,
standalone gaps and all **12 strict legacy journeys** passed. The legacy runner
includes demo through builder and both checkout variants, covering all 17
pre-existing verifier modules. Its browser observation reports contain zero
unexpected console, page, HTTP or request errors.

Final combined runs passed on both SQLite and PostgreSQL at **1440x900** and
**390x844**, including all ten functional gaps and the full visual editor.
Each recorded **zero browser/console/network errors**, **zero server errors**,
**12 successful downloads** and a clean server shutdown. Canonical evidence has
**259 combined screenshots plus 36 gap screenshots**. Database-specific summaries
are `polish/rc-sqlite-results.json` and `polish/rc-postgresql-results.json`.
The standalone gap verifier also passed after the final assertion/capture changes.

Browser assertions now cover saved-view toolbar integration, hidden columns
after asynchronous filtering, retained search focus, the header Edit link and
humanised edit settlement in the timeline. Activity assertions target rendered
labels and accommodate mobile card labels, retaining the same object/actor
checks. No application error was added to an allowlist. Reviewed desktop/mobile
samples include order actions/timeline, custom data, saved views, preferences,
redirects, order editing, activity and theme library. No clipping or overlapping
controls were found in those samples. Supplementary historical tracked captures
were restored; release captures and database-specific summaries are retained
under `output/playwright/admin-v2/polish/` and `gaps/`.

### Deployment risks and cleanup

Back up PostgreSQL and persistent storage before release. Downgrade to `0029`
is verified on populated PostgreSQL and SQLite, but removes v2 feature data.
Real-provider payments, email delivery, production-scale concurrent locking,
reverse-proxy/TLS and live Helen login remain operator acceptance checks.
No implementation question remains open.

Automatic approval review rejected native PowerShell cleanup with **blocked by
policy**, after absolute paths were checked inside this repository. No alternate
deletion method was used. These task-created temporary paths remain local and
uncommitted:

- `output/rc-full-validation`
- `output/rc-final-validation`
- `output/rc-focused-validation`
- `output/rc-migration-validation`
- `output/rc-migration-final`
- `output/seed-role-validation` (including `app.db`)
- `_rc_test_isolation.py`
- `output/playwright/admin-v2/polish/failure-0-0.png`
- `output/playwright/admin-v2/polish/failure-16-0.png`
- `output/playwright/admin-v2/gaps/desktop-failure.png`
- `output/playwright/admin-v2/gaps/mobile-failure.png`
- `output/playwright/admin-v2/p4/desktop-blog-post-new.png`
- `output/playwright/admin-v2/p4/desktop-content-pages.png`
- `output/playwright/admin-v2/p4/desktop-discount-resource-picker.png`
- `output/playwright/admin-v2/p4/desktop-discount-type-picker.png`
- `output/playwright/admin-v2/p4/mobile-blog-post-new.png`
- `output/playwright/admin-v2/p4/mobile-content-pages.png`
- `output/playwright/admin-v2/p4/mobile-discount-resource-picker.png`
- `output/playwright/admin-v2/p4/mobile-discount-type-picker.png`
- `output/playwright/admin-v2/p5/desktop-media-dropzone.png`
- `output/playwright/admin-v2/p5/desktop-section-picker.png`

Pre-existing local untracked artefacts listed earlier in this report were retained.
No database files, temporary plugin or failed-run diagnostics were committed.
`docker stop fastshop-admin-v2-pg-check` succeeded; `docker ps -a --filter
name=fastshop-admin-v2-pg-check --format '{{.Names}}'` returned no container.
The `--rm` container's tmpfs data was discarded, with no persistent volume kept.

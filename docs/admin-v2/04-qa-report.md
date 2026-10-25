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

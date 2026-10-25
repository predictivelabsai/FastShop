# Merchant Admin v2 upstream verification

This report covers the generic FastShop branch `merchant-admin-v2-upstream`,
prepared from `origin/main` on 25 October 2026. No push, merge or deployment was
performed. Final verification results are recorded below.

## Scope

The branch includes the shared merchant shell, catalogue, inventory, orders,
returns, draft orders, customers, discounts, gift cards, content, analytics,
subscriptions, settings and visual editor. Optional customer theme packs are
supplied by the deploying repository. The core starts and the editor registry
loads with no optional pack installed.

Revisions 0029 to 0034 retain their original filenames, revision identifiers and
migration graph. Both SQLite and a disposable local PostgreSQL 16 database passed
upgrade to head, schema check, downgrade to main's head `20261009_0028`, upgrade
to head and another schema check. No schema drift was detected.

## Editor JSON validation

CI installs `.[dev]` with pip, resolving the version ranges in `pyproject.toml`.
The Docker image installs the equivalent runtime ranges in `requirements.txt`.
Neither installation uses `uv.lock`. The checked-in lockfile resolves FastHTML
0.14.13, Fastcore 2.2.22 and Starlette 1.6.0; the fresh Python 3.12 installation
resolved FastHTML 0.14.14, Fastcore 2.2.33 and Starlette 1.10.0.

FastHTML's form injection parses JSON before invoking route handlers. The newer
parser rejects arrays and scalars with HTTP 400 before the editor can validate
them. A minimal reproduction returned 422 under 0.14.13 and 400 under
0.14.14 before the fix. The editor JSON mutation endpoints now use Starlette routes with explicit
session, path and query handling. They retain the editor's CSRF, size limits,
ownership, role checks and structured error responses. Non-object JSON always
reaches the editor boundary and returns HTTP 422. No dependency downgrade or
global exception-status rewrite is required.

The 49 editor route tests passed with both dependency resolutions on Windows
Python 3.12. Regression cases cover arrays, null, strings, numbers and booleans
across render, draft, publish, restore, revision restore and theme settings.
The same 49 tests also passed in a Linux Python 3.12 container using CI's fresh
pip installation.

## Browser acceptance

`scripts/verify_admin_v2_all_browser.py` boots `uvicorn app.main:app` on local port
5026 with a disposable database and deterministic local provider doubles. The
sequential journeys use real login forms, desktop and mobile contexts, and cover
all generic merchant features. The editor fixture uses a generic store and an
image generated in memory. The runner checks browser exceptions, failed network
requests, HTTP errors, responsive overflow, persisted edits, role isolation and
clean server shutdown. Captures go to `output/playwright/admin-v2/upstream/`.

Real payment settlement, email delivery and deployment are outside this local
verification. No remote database, environment file or stored secret is used.

## Final results

All checks used Python 3.12.14 with environment-file loading disabled, empty
provider credentials and explicitly disposable storage. Full suites used four
workers, with application storage or PostgreSQL schemas selected before collection.
Tests that own in-memory SQLite fixtures continue to use those fixtures on both runs.

| Check | Result |
| --- | --- |
| Full pytest, SQLite application storage | 1,032 passed, 1 skipped, 0 failures |
| Full pytest, local PostgreSQL 16.15 application storage | 1,032 passed, 1 skipped, 0 failures |
| Editor routes, fresh Windows dependencies | 49 passed |
| Editor routes, frozen lockfile | 49 passed |
| Editor routes, Linux Python 3.12 fresh pip installation | 49 passed |
| Ruff, whole repository | Passed |
| Python compilation, app | Passed |
| Changed JavaScript syntax | 13 files passed |
| Explicit migration round trips | Five commands passed on each database, including both schema checks |
| Combined SQLite browser run on port 5026 | Passed, 255 captures, 12 successful downloads |
| Combined PostgreSQL browser run on port 5026 | Passed, 255 captures, 12 successful downloads |
| Browser/server errors and shutdown | Zero errors on both runs; both servers stopped cleanly |
| Whitespace and documentation checks | Passed |

The existing optional documentation check skips because `python-pptx` is not part
of the runtime/dev installation. No new skips were introduced. Two generic
regressions from optional-pack integration work were retained in
`tests/test_storefront_compatibility.py`: unknown legacy documents can be read
without rewriting them, while new writes remain strict; copilot and outbox work
remain scoped to membership tenants.

Both existing storefront snapshot fixtures retain the same keys. Their hashes
were refreshed for the generic account-header and cart-label changes. Seed data,
products, compliance text and existing customer assets were not changed.

Machine-readable browser summaries are in
`output/playwright/admin-v2/upstream/results-sqlite.json` and `results.json`
(the PostgreSQL run). Six representative desktop/mobile captures, totalling
731,066 bytes, are versioned. The full capture sets remain local and ignored.

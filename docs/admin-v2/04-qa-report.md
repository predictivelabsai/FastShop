# Merchant Admin v2 upstream verification

This report covers the generic FastShop branch `merchant-admin-v2-upstream`,
prepared from `origin/main` on 25 October 2026. No push, merge or deployment was
performed. Current verification results are recorded below after the final checks.

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
them. The editor JSON mutation endpoints now use Starlette routes with explicit
session, path and query handling. They retain the editor's CSRF, size limits,
ownership, role checks and structured error responses. Non-object JSON always
reaches the editor boundary and returns HTTP 422. No dependency downgrade or
global exception-status rewrite is required.

The 49 editor route tests passed with both dependency resolutions on Windows
Python 3.12. Regression cases cover arrays, null, strings, numbers and booleans
across render, draft, publish, restore, revision restore and theme settings.

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

# Merchant Admin v2 P1 verification notes

Final full-suite result: **760 passed, 1 skipped, 1 warning in 788.05 seconds**.
The warning is the existing Starlette/AnyIO `BlockingPortal` deprecation.
All required lint, compilation, disposable migration, browser and whitespace checks passed.

## Parent-commit global Python collection failure

Parent commit: `c571efb3f686c3d28b74e0448afb3cfaa257000b`.

The global interpreter is `C:\Users\Joosep\AppData\Local\Programs\Python\Python314\python.exe`.
A plain `python -m pytest` failed during collection. The unchanged parent was
exported inside this repository without switching branches or creating a worktree:

```powershell
$repoRoot = (Resolve-Path .).Path
$proofRoot = Join-Path $repoRoot 'output\admin-v2-parent-proof'
$proofZip = Join-Path $repoRoot 'output\admin-v2-parent-proof.zip'
New-Item -ItemType Directory -Path $proofRoot | Out-Null
git archive HEAD -o $proofZip
Expand-Archive -LiteralPath $proofZip -DestinationPath $proofRoot
$env:DB_URL = ''
$env:FASTSHOP_ENV = 'development'
$env:FASTSHOP_AUTO_CREATE_SCHEMA = '1'
$env:FASTSHOP_DATA_DIR = Join-Path $proofRoot 'isolated-data'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:XAI_API_KEY = ''
$env:POSTMARK_API_TOKEN = ''
$env:POSTMARK_SERVER_TOKEN = ''
$env:STRIPE_SECRET_KEY = ''
$env:STRIPE_WEBHOOK_SECRET = ''
Push-Location $proofRoot
python -m pytest --collect-only -q
Pop-Location
```

Result: exit code **2**, with the same **12 collection errors**. Eleven reported
`ModuleNotFoundError: No module named 'tests.test_checkout_services'`; one reported
`ModuleNotFoundError: No module named 'tests.test_us_commerce'`. Affected modules:

- `tests/test_checkout_payments.py`
- `tests/test_checkout_services.py`
- `tests/test_liquid_bundle_commerce.py`
- `tests/test_liquid_storefront.py`
- `tests/test_store_checkout_routes.py`
- `tests/test_subscription_notifications.py`
- `tests/test_subscription_payments.py`
- `tests/test_subscription_recovery.py`
- `tests/test_subscription_renewals.py`
- `tests/test_subscription_routes.py`
- `tests/test_subscriptions.py`
- `tests/test_transactional_mail.py`

`python -c "import tests; print(tests.__file__)"` resolved `tests` to the globally
installed `Python314\Lib\site-packages\tests\__init__.py`, rather than the repository
test namespace. The unchanged parent reproduces this interpreter-environment conflict.
P1 verification uses the repository virtual environment through `uv run`.

No parent-commit tests were executed after collection failed. The archive and exported
source are temporary verification artefacts, including their disposable database.
Their resolved absolute paths were checked against this repository's `output`
directory before cleanup. Automatic approval review rejected native PowerShell
`Remove-Item -LiteralPath` cleanup with the reason `blocked by policy`. The temporary
`output/admin-v2-parent-proof` directory and `.zip` must remain excluded from commits
and require cleanup once that restriction is resolved.


## P1 verification environment and commands

All commands ran from `C:\Users\Joosep\h24you\fastshop-lovable`, on
`merchant-admin-v2`. Provider calls and scheduler startup were disabled. The
configuration loader was prevented from reading environment files.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHON_DOTENV_DISABLED='1'
$env:DB_URL=''
$env:FASTSHOP_ENV='development'
$env:FASTSHOP_AUTO_CREATE_SCHEMA='1'
$env:FASTSHOP_DATA_DIR=Join-Path $env:TEMP ('fastshop-p1-'+[guid]::NewGuid().ToString('N'))
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
uv run python -m pytest
node --check static/admin/admin.js
git diff --check
uv run python scripts/verify_admin_v2_browser.py
```

Migration and test runs used separate disposable data directories. Alembic reached
`20261011_0029`, with no application schema changes required for P1. Ruff, compileall,
JavaScript syntax and whitespace checks passed.

The browser script supplies its own temporary database, development account and
free localhost port before importing application modules. It seeded one clearly
named local fixture order, captured 14 images at 1440x900 and 390x844, and passed
with zero browser errors. Checks cover active navigation, no document overflow,
mobile sidebar open/close, Ctrl K search, keyboard result activation and dirty-form
Discard. Every required screenshot was visually reviewed. The review led to fixes
for backdrop visibility, search dropdown width, sidebar focus contrast and the
Stores chooser's first-screen priority.

Images are in `output/playwright/admin-v2/p1/`, with `desktop-` and `mobile-`
variants of `stores`, `home`, `products`, `orders`, `settings`, `placeholder` and
`global-search`. Screenshots are committed, following existing repository history.
No session state, databases or caches are included.

The temporary parent export and archive are also excluded in local
`.git/info/exclude`. That local exclusion is not part of the commits.

## Files and routes

- `app/admin_ui.py`: shared components, navigation and request-local shell context.
- `static/admin/admin.css`, `admin.js`: tokens, responsive layout, search, save bar,
  table selection, settings filtering and dialogs.
- `app/admin_routes.py`: Home (`/admin/sites/{site_id}`), search (`/search`, HTML and
  `?format=json`), Settings (`/settings`, `/settings/billing`), release placeholders
  (`/coming-soon/{feature}`) and Content Pages (`/pages`).
- `app/site_routes.py`: shell injection, Stores chooser and General settings at
  `/admin/sites/{site_id}/settings/general`; the existing POST `/settings` retains
  its validation, membership, CSRF and version behaviour.
- `app/main.py`: request context middleware, link from the legacy admin and
  CSRF-protected shell sign-out POST. Existing storefront rendering is unchanged.
- `app/site_golive.py`: checklist actions now point to the moved Pages directory.
- `tests/test_admin_foundation.py`, `test_admin_shell.py`: isolated data, search
  boundaries, dashboard, shell migration and CSRF coverage. Existing plan and
  commerce assertions were updated only for changed labels and shell assets. The
  onboarding fixture now injects the same disposable session factory into the new
  route/shell modules and installs the production request-context middleware.

## Follow-ups

Sessions are omitted because there is no sessions data source. There is no configured
low-stock threshold, so the dashboard reports only recorded stock-outs. Catalogue
ownership remains tenant-wide, matching existing product services. Future phases
provide the navigation features currently marked as coming in this release.
Legacy browser journeys that depend on the old overview's forms and link labels
will need migration to the new Pages and General settings routes; P1 has its own
self-contained browser verifier.


## Test iteration notes

The first full project-environment run completed with 756 passed, one skipped and
one failed. The onboarding happy-path fixture patched the old route module's
session factory but not the extracted Home module. Its follow-up GET consequently
looked in the wrong disposable database. The fixture was updated to inject the
same sessions into `admin_routes` and `admin_ui` and install the shared request
middleware, matching the production composition. No assertion was weakened.
The onboarding and final foundation/shell checks then passed together: 47 tests.
A superseded concurrent run that had already imported the old fixture was stopped;
the final full run uses the corrected fixture.

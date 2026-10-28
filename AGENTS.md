# FastShop repository guidelines

FastShop is a Python 3.12+, server-rendered FastHTML commerce application.
Application code lives under `app/`: database models and sessions in `app/db.py`
and `app/models.py`, transactional use cases in `app/services.py`, storefront and
merchant components in `app/ui.py`, the mounted FastAPI surface in `app/api.py`,
and provider boundaries under `app/integrations/`.

Use integer minor currency units, tenant-scope all owned queries, keep checkout
commands idempotent, and acquire inventory locks in stable primary-key order.
FastShop and FastERP may share a PostgreSQL server but never write directly into
each other's schemas.

Do not commit `.env`, database files, payment data, credentials, Playwright
session state, caches, or virtual environments. Run `ruff`, `pytest`, compile
checks, Alembic validation, and `git diff --check` before release. UI changes
must include Playwright desktop and mobile screenshots under
`output/playwright/`.


## Stack

- Docker
- FastAPI
- Python

## Commands

- build: `docker build -f 'Dockerfile' .` (cwd: repository root `.`, sources: `Dockerfile`, `requirements.txt`; statically reviewed, not executed). The image installs runtime dependencies and starts with `alembic upgrade head && python web_app.py`.
- test: `python -m pytest` (cwd: repository root `.`, source: `pyproject.toml`, `[tool.pytest.ini_options]`; discovers `tests/` with `-q`). Only the isolated dependency-drift test was executed during onboarding.
- lint: `ruff check .` (cwd: repository root `.`, source: `pyproject.toml`, `[tool.ruff]` and `[tool.ruff.lint]`; passed during onboarding). CI uses the narrower `ruff check app tests web_app.py`.

Verification results and execution limits are recorded in `docs/onboard-verification.md`.

## Builders

codex: yes
muse: no

## Conventions

- Keep route handlers in `app/*_routes.py` and the mounted API in `app/api.py`; put reusable commerce rules in `app/services.py` and the relevant domain service modules. Render FastHTML components through `app/ui.py`, `app/site_ui.py` and `app/platform_ui.py`.
- Store and calculate money as integer minor units with an explicit currency. Use `money()` for display; keep quote and order snapshots consistent with the exact amounts sent to payment providers.
- Scope owned queries by `tenant_id` and, for storefront entities, `site_id`. Use the existing SQLAlchemy session patterns, including commit on success and rollback on failure in `session_scope()`.
- Follow `checkout_services.prepare()`: validate request keys, reject retries with changed request fingerprints, reserve within a savepoint and lock stock by `Stock.id`. Preserve the explicit commits around provider calls in `app/checkout_payments.py`.
- Keep provider HTTP calls behind `app/integrations/`. Deliver FastERP orders through the outbox/API connector, retaining its retry and idempotency behaviour.
- Preserve CSRF checks on form mutations, bearer-token checks on API writes, and signature verification plus event deduplication for payment webhooks.
- Add schema changes as revisions in `migrations/versions/`; Alembic reads `migrations/env.py` via `alembic.ini`. Keep PostgreSQL schema isolation aligned with `app/db.py`.
- Keep `requirements.txt` aligned with runtime dependencies in `pyproject.toml`; Docker installs the former. `tests/test_requirements_drift.py` checks this contract.
- Follow Ruff's Python 3.12 target, 100-character line length and configured rules. Extend relevant `tests/test_*.py` coverage with isolated database fixtures and fake gateways, following `tests/test_checkout_services.py`.
- Use PowerShell on Windows. Before starting, read `C:\Users\Joosep\ops\skills\INDEX.md` and the matching `SKILL.md`, including catalogue skills that are not enabled for the agent.
- Keep changes within the task's scope and list unrelated findings as follow-ups.
- Use British English. Do not use em dashes in copy.
- Finish each task with what changed, how it was tested and open questions.

## Do not touch

- Never read or commit secrets, credentials or local environment files.
- Keep `.env`, `creds/`, `data/`, database files, payment data, Playwright session state, caches and virtual environments out of commits. Do not expose stored provider credentials in logs or documentation.
- Never deploy or push without explicit authorisation.
- Never push to `main` or `master`, and never enable Muse; retain `codex: yes` and `muse: no`.
- Never write into FastERP tables or another application's schema. Preserve the FastShop search path and schema validation in `app/db.py` and `migrations/env.py`.
- Do not import `app.main` or `web_app.py` merely to inspect the application: import-time bootstrap prepares and seeds the database and invokes scheduler startup. Review configuration and isolate storage before running application-dependent checks.
- Do not run `scripts/coolify.py`, credential/account provisioning, checkout reconciliation, subscription renewals or mail-processing scripts as routine onboarding checks. These require a specifically authorised operational task.
- Do not run online Alembic upgrades against an unspecified database. CI and the Docker entry point apply migrations; validation must use an explicitly selected disposable database.
- Do not modify unrelated application code.

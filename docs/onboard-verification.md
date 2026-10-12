# Onboarding verification

Reviewed on 12 October 2026 from the repository root using PowerShell, on branch
`task/onboard-enrich`. Only `AGENTS.md` and this report are included in the
onboarding commit. The final empty `.dispatch-done` marker is ignored by the
existing `.gitignore` rule.

## Command review

| Command | Declared source and working directory | Result |
| --- | --- | --- |
| `docker build -f 'Dockerfile' .` | Root `Dockerfile`; root build context; runtime packages from `requirements.txt` | Correct as declared. Statically reviewed only. The build downloads and installs dependencies, so it was not executed. |
| `python -m pytest` | Root `pyproject.toml`, `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, `addopts = "-q"` | Correct as declared. Full suite not executed because application imports can bootstrap storage and invoke scheduler startup. |
| `ruff check .` | Root `pyproject.toml`: Python 3.12 target, line length 100, rule selections and exclusions | Correct as declared. Executed with `--no-cache --output-format concise`; all checks passed. |

No command correction was required. The Commands section now identifies exact
sources and distinguishes static review from execution. `.github/workflows/ci.yml`
uses `ruff check app tests web_app.py`, `python -m compileall app tests web_app.py`,
`alembic upgrade head`, `pytest` and `docker build -t fastshop:test .`, all from the
root. The existing broader lint command remains valid.

## Short safe checks executed

| Check | Result and limits |
| --- | --- |
| `ruff check . --no-cache --output-format concise` | Passed, no Ruff cache written. |
| `python -I -B -m pytest tests/test_requirements_drift.py -p no:cacheprovider` with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` | One test passed in 0.02 seconds. Reviewed the test first; it reads only `pyproject.toml` and `requirements.txt`, with no application imports. No `conftest.py` was found under `tests/`. Plugins, bytecode and pytest cache were disabled. |
| Isolated Python standard-library inspection | Python 3.14.6; parsed `pyproject.toml` with `tomllib` and compiled 178 Python sources from `app/`, `tests/`, `migrations/` and `web_app.py` in memory using `compile()`. No application execution or bytecode files. This is not a Python 3.12 runtime test. |
| Static migration graph inspection using `ast` | 29 unique revision IDs, all parent references present, one head: `20261011_0029`. Migration modules and `migrations/env.py` were not executed. This does not validate SQL or database compatibility. |
| `docker --version` | Docker CLI 29.6.2 available. Daemon availability and image build not tested. |
| `git diff --check` | Passed before documentation edits; repeated after edits for the final patch. |

## Guidance grounded in code

- `app/main.py` mounts `/api`, registers route modules, prepares the schema,
  seeds data and calls `start_scheduler()` at import time.
- `app/db.py` validates the PostgreSQL schema name, sets its search path and
  provides transaction-scoped sessions. `migrations/env.py` uses the same settings
  and schema isolation; `alembic.ini` selects `migrations/`.
- `app/checkout_services.py` uses request fingerprints, savepoints, tenant/site
  filters and stock locks ordered by primary key. `app/checkout_payments.py`
  separates durable payment state from provider network calls.
- `app/main.py` and `app/store_checkout_routes.py` check CSRF on form mutations;
  `app/api.py` guards writes with bearer tokens. `app/commerce_webhooks.py` and
  `app/billing_webhooks.py` verify signatures and deduplicate event IDs.
- `app/integrations/fasterp.py` delivers outbox events over HTTP with retries and
  idempotency rather than writing directly to FastERP tables.
- `tests/test_checkout_services.py` demonstrates isolated fixtures and fake
  gateways. `tests/test_requirements_drift.py` protects Docker dependency parity.

## Unrun checks and open questions

No dependencies were installed, servers started, operational scripts executed,
secrets or local environment files read, deployments performed or pushes made.
The existing release requirements remain in place: Ruff, pytest, compile checks,
Alembic validation, `git diff --check` and desktop/mobile Playwright screenshots
for UI changes. This task changes documentation only.

Follow-up release validation requires an isolated database and suitable runtime:
run the full suite on Python 3.12, validate migrations against the intended
database backend and build the Docker image in an authorised environment.
Those results remain unverified; there are no open questions about the scoped
documentation changes.

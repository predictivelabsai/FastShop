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

- build: `docker build -f 'Dockerfile' .` (cwd: `.`, source: `Dockerfile`, inferred)
- test: `python -m pytest` (cwd: `.`, source: `pyproject.toml`, inferred)
- lint: `ruff check .` (cwd: `.`, source: `pyproject.toml`, inferred)

## Builders

codex: yes
muse: no

## Conventions

- Follow existing code style and keep changes within the task's scope.
- Use British English. Do not use em dashes in copy.
- Finish each task with what changed, how it was tested and open questions.

## Do not touch

- Never read or commit secrets, credentials or local environment files.
- Never deploy or push without explicit authorisation.
- Do not modify unrelated application code.

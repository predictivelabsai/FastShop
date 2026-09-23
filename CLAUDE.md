# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

FastShop is a Python 3.12+, server-rendered FastHTML commerce application. See `AGENTS.md` for the
release rules that override defaults (integer minor currency units, tenant-scoping, idempotent
checkout, stable PK-order inventory locks, no cross-schema writes, don't commit secrets/DB files).

## Commands

```bash
uv sync --extra dev              # install (add --extra docs for python-pptx guide builds)
uv run alembic upgrade head      # apply migrations
uv run python web_app.py         # run app on http://localhost:5025
```

Run checks against an isolated SQLite DB even when a production `.env` is present (a bare `.env` DB_URL
would otherwise be picked up by `app/config.py`):

```bash
DB_URL= FASTSHOP_ENV=development FASTSHOP_AUTO_CREATE_SCHEMA=1 uv run ruff check .
DB_URL= FASTSHOP_ENV=development FASTSHOP_AUTO_CREATE_SCHEMA=1 \
  FASTSHOP_DATA_DIR="$(mktemp -d /tmp/fastshop-tests-XXXXXX)" \
  XAI_API_KEY= POSTMARK_API_TOKEN= POSTMARK_SERVER_TOKEN= \
  STRIPE_SECRET_KEY= STRIPE_WEBHOOK_SECRET= uv run python -m pytest -q
```

- Single test: `uv run python -m pytest tests/test_domain.py::test_money_uses_integer_minor_units`
- Tests build their own in-memory SQLite engine and call `seed()` (see `tests/test_domain.py`); there
  is no shared `conftest.py`.
- CI (`.github/workflows/ci.yml`) runs, in order: `ruff check`, `compileall`, `alembic upgrade head`,
  `pytest`, and `docker build`. Match this before release; also run `git diff --check`.

## Architecture

**One ASGI app, three surfaces.** `app/main.py` builds the FastHTML app (`fast_app`), mounts static
files and the FastAPI app (`app/api.py`) at `/api`, then registers the site-builder routes and adds
`SiteHostMiddleware`. `web_app.py` is the entry point.

- **Legacy single-tenant storefront** — routes defined directly in `app/main.py` (`/`, `/products`,
  `/cart`, `/checkout`, `/admin/*`, auth). Backed by `app/services.py` (transactional use cases) and
  `app/ui.py` (components). This is the original seeded demo shop for tenant `fastshop-demo`.
- **Multi-site builder** — `app/site_routes.py:register_site_routes(rt)` is the hub. It defines shared
  closures (`actor`, `csrf`, `check_csrf`, `shell`, `error`) and passes them into every sub-registrar:
  `site_catalog`, `site_builder_routes`, `site_sample_routes`, `demo_routes`, `commerce_routes`,
  `customer_routes`, `store_checkout_routes`, `subscription_routes`. **New builder/commerce routes
  follow this same `register_*_routes(rt, actor, csrf, check_csrf, shell, error)` pattern** rather than
  using the `@rt` decorators in `main.py`.
- **Versioned API** — `app/api.py`. Reads are public; writes require a bearer token (`FASTSME_API_TOKEN`).
  `app/commerce_webhooks.py` registers provider webhooks onto the same FastAPI app.

**Host-based routing.** `app/site_context.py:SiteHostMiddleware` matches the request `Host` header to a
`Site.hostname` and, for non-shared paths, rewrites `scope["path"]` to `/sites/{slug}{path}`. It also
gates by publish status and whether the site has sandbox commerce enabled (`SiteCommerceSettings`). A
site's pages live under `/sites/{slug}/...` internally regardless of the public hostname.

**Two commerce systems, kept separate.** The legacy storefront checkout (`app/services.py`) is distinct
from Phase 2 site-scoped sandbox commerce (`app/commerce.py`, `app/checkout_services.py`,
`app/checkout_payments.py`, `app/subscriptions.py`). Phase 2 runs sandbox-only; real-provider acceptance
is a separate gate. `app/demo_commerce.py` is a merchant-only simulator whose sample products/orders are
**isolated** from real catalog and commerce records — do not let demo data leak into real queries.

**Tenant scoping.** Owned queries filter by `tenant_id`. `services.tenant_channel()` resolves the
seeded tenant/channel; builder routes derive the actor via the `actor(session)` closure and check
`Membership` before any owned operation. Preserve this on every new query.

**FastERP boundary.** Confirmed orders are never written into FastERP's schema. `complete_checkout`
writes an `OutboxEvent` (topic `order.confirmed`); `app/integrations/fasterp.py:deliver_pending` posts
them to FastERP's token-gated, idempotent endpoint (`Idempotency-Key` = event id) and is reconciled
asynchronously. FastShop and FastERP may share a PostgreSQL server but own separate schemas.

**Provider integrations** (`app/integrations/`) are sandbox-first and site-scoped: credentials come from
per-site env prefixes (e.g. `FASTSHOP_STRIPE_{SITE_ID}_SECRET_KEY`), never shopper input; no card data
is stored in FastShop. `stripe_commerce.py` and `woocommerce.py` (read-only) are the current boundaries;
`scripts/check_commerce_providers.py` is an operator-only readiness probe that never prints secrets.

**Persistence.** SQLAlchemy 2.0 `DeclarativeBase` models in `app/models.py`; string UUID PKs via
`new_id()`; `TimestampMixin` for `created_at`/`updated_at`. `app/db.py` isolates PostgreSQL to the
`fast_shop` schema via `search_path` connect args (SQLite is the dev/test fallback). `prepare_schema()`
creates the schema and, when `FASTSHOP_AUTO_CREATE_SCHEMA` is set, the tables; production relies on
Alembic (`migrations/`, which reads `settings.database_url` and sets `version_table_schema`). On boot,
`main.py` seeds the demo tenant (`app/seed.py`) and the H2 4 You preview site (`app/site_seed.py`).

**Config.** `app/config.py:settings` is a frozen dataclass read from env (`.env` via python-dotenv).
`DB_URL` empty → SQLite under `FASTSHOP_DATA_DIR`; `postgres://`/`postgresql://` are normalized to
`postgresql+psycopg://`. Key flags: `FASTSHOP_ENV`, `FASTSHOP_ALLOW_PASSWORD_LOGIN` (+ admin hash),
Google OIDC vars, `MODEL_PROVIDER`/`XAI_API_KEY` (grounded AI assistant, `app/ai.py`).

## Conventions

- **Money is always integer minor units** (`services.money()` formats for display only).
- **Idempotent checkout**: `complete_checkout` short-circuits on an existing `idempotency_key`.
- **Inventory locks in stable PK order**: it sorts lines by `variant.id` and uses `with_for_update()`
  to avoid deadlocks. Keep this ordering when touching stock allocation.
- **CSRF**: state-changing routes require a token (`require_csrf` in `main.py`, `check_csrf` closure in
  builder routes). Post-redirect-GET with error passed in the query string is the norm.
- UI changes must include Playwright desktop + mobile screenshots under `output/playwright/`
  (see `scripts/verify_*_browser.py`).

# Phase 5c: onboarding wizard

Phase 5c connects self-serve signup to FastShop's existing brief-to-site generator. A new
merchant gives three bounded answers, chooses a generated draft or the clean template, and
lands on the existing site overview. The wizard does not add a second CMS or generation
implementation: the signup-provisioned `Site` remains the source of truth throughout.

## Surface and route contract

Password and first-time Google signup now redirect to
`GET /admin/onboarding/{site_id}`. The same pending entry is visible on `/admin/sites` and
the site overview, so closing the browser does not hide the next action.

The wizard has two short steps:

1. `POST /admin/onboarding/{site_id}/brief` saves a required business description, optional
   product context, and one supported design direction.
2. `POST /admin/onboarding/{site_id}/generate` creates a tailored private draft, while
   `POST /admin/onboarding/{site_id}/skip` keeps the eight clean starter pages and completes
   the funnel without generation.

Every POST validates the shared merchant CSRF token and uses Post/Redirect/Get. The state
query joins the onboarding row, `Site`, and `Membership` on the same tenant. Only active
`admin` or `merchant` memberships may read or change the wizard; editors and unrelated users
receive the same non-leaking 403 response.

The UI extends `static/site-editor.css` and the existing `e-card`, `e-form`, `e-button`,
`e-note`, and platform shell language. At 900px the two decisions stack; at 600px direction
choices and actions become one-column touch targets.

## Durable state model

`OnboardingState` is unique per tenant and per first site. It records:

- tenant, site, and provisioning user identifiers;
- the three bounded brief fields;
- `brief`, `ready`, `generating`, `failed`, `complete`, or `skipped` status;
- a brief-derived generation key, one-time attempt token, pre-run site fingerprint, and
  provider source;
- a safe failure code, terminal summary, and generation/completion timestamps.

The brief lives in the database rather than the browser session. Reloads and newly
authenticated sessions therefore resume the same step. Provider prompts and responses,
exception text, credentials, and secrets are not stored or rendered.

## Generation and persistence flow

The wizard translates its three answers into the existing Phase 2 `MerchantBrief` contract:
the site name remains the business name, product context supplies the business kind, the
business description supplies audience/context, and the selected direction supplies tone.
An empty optional product answer becomes the neutral `business or service` kind.

Generation calls `app.site_generation.generate_plan` unchanged. With a configured provider,
that boundary requests and repairs bounded JSON; without one, it produces the deterministic
guided plan. Both results cross the same plan, compliance, block, theme, menu, media, and
catalog validators.

The validated plan is applied to the same signup-provisioned `Site` through
`app.site_generation.apply_plan`. That service writes pages through `content.create_page`,
products through `site_catalog.create_catalog_product`, menus through `site_menus.put_menu`,
and draft settings through the existing optimistic site lock. No wizard route duplicates
those writes. The transaction includes both plan application and the terminal onboarding
state, so an apply error rolls back every partial page, menu, product, and status change.

Completion redirects to `/admin/sites/{site_id}` with a plain-language count of generated
pages and product seeds. The draft remains private and publication still requires the normal
review and go-live checks.

## Skip, resume, and idempotency semantics

`skipped` is terminal only for this first-run funnel. It records that no generation call ran,
keeps all eight starter pages, removes the pending entry, and leaves the normal `/build`
assistant available.

Before a provider call, the route locks the onboarding row, records `generating`, assigns a
random attempt token, derives the existing Phase 2 generation key, and fingerprints the
site's draft settings, pages, menus, and tenant catalog. A second submission sees either the
active claim or a terminal result and never calls the provider. A claim older than five
minutes may be reclaimed after a worker interruption; the attempt token prevents the older
worker from applying after that reclaim.

After planning, the route locks both state and site and recomputes the fingerprint. Any page,
menu, settings, or product change made in the meantime stops the apply and preserves the
merchant's work. `apply_plan` supplies the second idempotency layer: the same stored
generation key is a no-op, while retry replacement is limited to IDs recorded by Phase 2.
Together these guards prevent stale tabs from duplicating pages, menus, or products and
prevent onboarding from deleting unrelated content.

## Failure fallback

Provider, validation, or apply failures set `failed`, clear the active claim, and retain the
brief. The merchant sees only “Something went wrong” plus safe recovery choices: retry,
revise the brief, or open the existing builder. Provider exception text is never logged,
stored, or rendered. Because the plan and state complete in one database transaction, a
failure leaves either the untouched clean template or the merchant's intervening edits—not a
partially generated site.

## Migration

Migration `20261009_0026` chains from `20261009_0025` and creates
`onboarding_states` from the model table with `checkfirst=True`. Signup creates the row inside
the existing user/tenant/site provisioning transaction. Existing tenants receive no inferred
wizard state and continue using their current sites normally.

## Test and browser coverage

`tests/test_onboarding_wizard.py` uses a `StaticPool` in-memory SQLite database, patched
route sessions, real CSRF/session handling, and the same `site_generation.generate_plan`
seam used by Phase 2 tests. It covers generated completion, explicit skip and later `/build`
access, cross-session resume, safe provider failure and retry, duplicate submits, edits made
during provider work, membership denial, CSRF rejection, and bounded input without state
churn. `tests/test_signup.py` also confirms both password and Google provisioning create the
initial state.

`scripts/verify_site_browser.py --onboarding` starts an isolated signup-enabled application,
exercises both generated and skipped outcomes, checks overflow/iframes/analytics markers,
and writes desktop 1440px and mobile 390px evidence to
`output/playwright/phase-onboarding-wizard/`.

## Verification record

Verified on 2026-10-09 with the required isolated development environment and no
`FASTSHOP_ADMIN_EMAIL` override:

- `uv run ruff check .`: exit 0, “All checks passed!”.
- `uv run python -m pytest -q`: 484 tests collected and passed through `[100%]`, exit 0.
  The only warning was the existing Starlette `BlockingPortal` deprecation warning.
- `uv run python -m compileall -q app scripts`: exit 0.
- Disposable SQLite `uv run alembic upgrade head`: exit 0 and applied through
  `20261009_0026`.
- Disposable SQLite `uv run alembic check`: exit 0, “No new upgrade operations detected.”
  The disposable database was deleted afterward.
- `uv run python scripts/verify_site_browser.py --onboarding --output
  output/playwright/phase-onboarding-wizard`: 18 generated, skipped, failure, step, desktop,
  and mobile checks; zero failures; exit 0.
- The interface detector found only advisory values already present across the established
  minified editor stylesheet. New failure styling was aligned to the existing negative-state
  palette. The independent finish review scored every requested correction resolved and
  returned `SHIP`.
- `git diff --check`: exit 0; only the repository's existing LF/CRLF conversion notices were
  emitted.

No commit was created.

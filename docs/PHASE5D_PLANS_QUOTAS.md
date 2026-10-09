# Phase 5d: plans, quotas, and metering

Phase 5d adds a per-tenant plan assignment with structural limits, enforcement at the
service boundaries that mutate limited resources, a durable tenant-scoped usage ledger,
a platform-operator plan console, and a merchant-facing **Plan & usage** panel. The plan
catalog is frozen data in `app/plans.py`, not database rows. It contains no dollar amounts:
pricing and billing belong to Phase 5e.

## Plan catalog

The code catalog defines three tiers:

- **Free** allows 2 sites, 3 AI generations per UTC calendar month, 10 products, and
  2 published sites.
- **Basic** allows 10 sites, 25 AI generations per UTC calendar month, 250 products, and
  5 published sites.
- **Pro** allows 50 sites, 100 AI generations per UTC calendar month, 1,000 products, and
  25 published sites.

Tier order is explicitly `free`, `basic`, then `pro`. When an actor holds qualifying
memberships on tenants with different plans, the highest tier is the account plan. Unknown
or absent assignments resolve to Free rather than creating an implicit fourth tier.

## Account-level scope

FastShop provisioning creates a tenant and its first site together, so the normal
self-serve relationship is tenant == site == account. Account-level actions such as site
creation and AI generation begin before a new tenant exists, however, and therefore cannot
resolve a limit from that future tenant. Quotas consequently resolve across every tenant
where the actor has an `admin`, `merchant`, or `editor` membership. Metering remains
tenant-scoped to the tenant where consumption occurred.

This scope also handles platform fixture accounts without inventing a separate exception:
the account plan is the highest tier held across those plan-role memberships, defaulting
to Free when there are none.

## Surface and route contract

`GET /admin/sites` renders the merchant card headed `Plan & usage — {plan}`. Each quota is
shown as `Label: used of limit`; the monthly AI row also shows `resets {date}`. Exhausting a
quota blocks only the corresponding mutation. Dashboards, previews, product reads, and all
other reads stay open.

The quota-bearing state changes preserve their existing CSRF validation and
Post/Redirect/Get behavior:

- `POST /admin/sites` creates a classical or guided site.
- `POST /admin/sites/generate` generates and provisions a private draft.
- `POST /admin/sites/{site_id}/products` creates a catalog product; edits remain allowed
  because they do not increase the live count.
- `POST /admin/sites/{site_id}/integrations/{platform}/apply` applies a reviewed connector
  import.
- `POST /admin/sites/{site_id}/build/review` approves or rejects builder proposals.
- `POST /admin/sites/{site_id}/golive/publish` publishes a reviewed site.
- `POST /admin/platform/tenants/{tenant_id}/plan` changes a tenant plan.

The operator console is `GET /admin/platform/plans`; the plan mutation is
`POST /admin/platform/tenants/{tenant_id}/plan`. These routes are registered through
`register_plan_routes(...)` in `app/site_routes.py:172-173`, rather than with decorators in
`app/main.py`. Both routes call the same platform-operator gate. At
`app/plans.py:301-311`, the gate normalizes the configured administrator address and the
user address, compares them with `hmac.compare_digest`, and returns the same generic
`Platform operator access is required.` error for an unknown user, missing administrator
configuration, or a non-operator account. The response is deliberately non-enumerable.

## Enforcement seam inventory

- **Normal site creation — `app/site_routes.py:272-294`.** The POST validates CSRF, opens
  the transaction, and calls `ensure_sites` at line 281 before `content.create_site` at
  line 282. It commits at line 286 and redirects with 303 at line 287. A quota refusal is
  redirected back to the readable dashboard notice at lines 291-294, so no tenant or site
  has been written.
- **Generated site provisioning — `app/site_routes.py:231-269` and
  `app/site_generation.py:614-626`.** The route performs short-session site and AI
  pre-flight checks at lines 254-255 before generation runs at line 256. The service
  rechecks the site limit at `app/site_generation.py:622`, before creating anything at
  line 623. The AI credit is recorded only after the draft exists, at
  `app/site_routes.py:259`, and is committed with the draft at line 261. A failed
  generation therefore consumes no credit and leaves no partial site.
- **Full-site product generation — `app/site_generation.py:511-610`.** The service computes
  retry net growth at lines 535-536 and checks the entire additional product unit at line
  537, before locking and before any deletes or inserts at lines 538 onward. The caller
  owns one transaction, so the plan is applied in full or rolled back. Retries check only
  net growth and do not consume product quota twice.
- **Catalog product creation — `app/site_catalog.py:123-178`.** New-product requests call
  `ensure_products` at line 157 before `create_catalog_product` at lines 159-167. The
  helper records `product_created` at `app/site_catalog.py:87-89`; the route commits at
  line 171 and redirects with 303 at line 172. A refusal reaches the notice redirect at
  lines 174-177 without a product, page, or ledger row.
- **Connector imports — `app/connectors.py:193-245`.** The reviewed plan is optimistically
  claimed at lines 210-222. The whole-import quota check is line 230, after that claim but
  before the first connector write at line 232. A refusal rolls the claim back to pending
  in the route's transaction, leaving nothing half-imported. Successful imports record
  the actual `created_products` quantity at lines 233-240. The route validates CSRF and
  commits before its redirect at `app/site_integration_routes.py:243-264`.
- **Builder-review product proposals — `app/site_builder_reviews.py:100-155`.** Approval
  runs in a nested transaction, locks the site and review, counts the complete set of
  product proposals at line 123, and checks that whole set at line 126 before applying any
  proposal. Product creation and metering occur together at lines 150-151. The route
  validates CSRF, commits, and redirects at `app/site_builder_routes.py:343-357`, so a
  refusal cannot approve only part of a proposal set.
- **Publishing — `app/site_golive.py:453-501`.** Readiness checks run first, and an already
  published site short-circuits idempotently at lines 482-485. `ensure_published` runs at
  line 492 immediately before the status flip at line 493; the successful event is
  recorded at line 496. The CSRF-protected route commits and redirects at
  `app/site_golive_routes.py:193-212`. A quota failure leaves status and metering unchanged.
- **Provisioning defaults and initial metering — `app/content.py:204-250`.** Every new
  tenant receives `plan="free"` at line 216 inside the provisioning transaction, and the
  initial `site_created` event is added at line 249. Signup itself is not quota-blocked;
  the tenant, membership, first site, and event commit or roll back together.

## Metering design

The durable event kinds are `site_created`, `ai_generation`, `product_created`, and
`published`. `UsageEvent` at `app/models.py:965-985` has a string primary key, required
tenant foreign key with cascade delete, optional site foreign key with null-on-delete,
optional indexed user id, bounded kind, integer quantity defaulting to one, and inherited
created/updated timestamps. The composite
`ix_usage_events_tenant_kind_created` index supports tenant, kind, and time-window queries.
It is intentionally separate from `OutboxEvent`: quota accounting must not depend on
FastERP delivery retries, reconciliation, or delivery status.

The chosen counter rule is deliberately mixed. Sites, products, and published sites use
live-row counts: qualifying `Membership` rows for sites, tenant-owned `Product` rows for
products, and tenant-owned `Site` rows whose status is `published`. Those limits represent
current state and self-heal after deletes and transaction rollbacks. The monthly AI limit
uses summed ledger quantities inside the current UTC calendar month because credits are
consumption over a time window. The clock is injectable, and reset text uses a date such as
`1 November 2026`. The operator console also aggregates the ledger for observable,
per-tenant usage.

`product_created` quantities describe consumption events: an import that creates two
products writes quantity 2. The products quota nevertheless counts current product rows,
so later import churn does not permanently consume structural product capacity. This
ledger-versus-live asymmetry is intentional.

Checks happen before limited writes, and metering happens only after the successful unit
exists. Events share the caller transaction with their mutation, so failed applies and
failed commits roll back both state and ledger rows. Idempotent publication and generation
paths short-circuit or calculate net growth before charging capacity.

## Migration

Migration `20261009_0026` chains from the real repository head,
`20261009_0025`. The Phase 5c onboarding wizard never shipped, and
`tests/test_onboarding_wizard.py` does not exist, so there was no Phase 5c `0026` revision
to chain from despite the earlier brief/repository mismatch.

The upgrade adds non-null `tenants.plan` as `String(20)` with server default `free`, updates
the existing `fastshop-demo` and `h24you` fixture tenants to `pro`, and creates
`usage_events` from `UsageEvent.__table__` with `checkfirst=True`. The fixture migration
matches `app/seed.py:107` and `app/site_seed.py:53`, which create those platform tenants on
Pro so demo, go-live, and browser flows are not constrained by self-serve Free limits. The
downgrade drops `usage_events` and then removes `tenants.plan` with a batch alteration.

## Marketing isolation

Plan assignment, current usage, quota notices, and operator controls never render on the
public `/marketing/`, `/`, or `/signup` surfaces. `app/marketing_routes.py` has no import or
execution path into `app.plans`; signup reaches the Free default only through the shared
transactional `content.create_site` provisioning service. The public surface therefore
does not expose tenant plan or quota state.

## Test coverage

`tests/test_plans_quotas.py` contains 14 focused tests, grouped by contract:

- Site and AI limits: `test_site_quota_blocks_third_site_with_merchant_readable_notice`,
  `test_ai_generation_quota_blocks_route_with_reset_notice`,
  `test_ai_generation_is_metered_only_after_success`, and
  `test_month_window_counts_only_current_month`.
- Product mutations and bounded apply behavior:
  `test_product_quota_blocks_catalog_creation`,
  `test_catalog_creation_is_metered_on_success`,
  `test_builder_product_proposal_blocked_when_quota_exhausted`,
  `test_connector_import_fails_as_bounded_whole_when_quota_exhausted`, and
  `test_connector_import_meters_created_products`.
- Publication and operator access: `test_publish_quota_blocks_third_publication`,
  `test_platform_operator_gate_denies_other_users`, and
  `test_operator_console_lists_tenants_and_changes_plans`.
- Read availability and accounting: `test_reads_stay_open_when_every_quota_is_exhausted`
  and `test_ledger_records_and_usage_views`.

Existing signup tests now assert that provisioning assigns `tenant.plan == "free"` and
adds the first `site_created` ledger row. `tests/test_site_images.py` and
`evals/site_generation.py` add a Pro platform-tenant membership for their synthetic
multi-site owners, matching the fixture precedent in `app/seed.py` so Free limits do not
truncate unrelated image or generation coverage.

## Browser acceptance

`scripts/verify_site_browser.py --plans` starts one isolated `signup_server(True)` process,
signs up a fresh merchant, and creates the second allowed Free site. It verifies and
captures the `Plan & usage — Free` panel at 2 of 2 sites, then attempts a third site and
verifies the notice `Your Free plan allows 2 sites; you are now using 2 of 2`.

The successful run recorded one check and zero failures in
`output/playwright/phase5d-plans/verification.json`. Its responsive evidence is
`desktop-plans-usage.png`, `mobile-plans-usage.png`,
`desktop-site-quota-blocked.png`, and `mobile-site-quota-blocked.png` in the same directory.

## Verification record

The final verification used isolated empty provider credentials and did not set an
administrator-email override.

### Final results

- Targeted regression command: exit 0; 27 passed through `[100%]`. The only warning was
  the pre-existing Starlette `BlockingPortal` deprecation warning.
- `DB_URL="" FASTSHOP_ENV=development uv run ruff check .`: exit 0, `All checks passed!`.
- Full `uv run python -m pytest -q`: exit 0; 491 tests collected, 491 passed, 0 failed,
  and progress completed at `[100%]`. No warning other than the pre-existing Starlette
  `BlockingPortal` deprecation warning was emitted. A collection-only pass with pytest's
  configured extra quiet flag disabled confirmed the numeric count and exited 0.
- `uv run python -m compileall -q app scripts`: exit 0.
- Disposable SQLite `uv run alembic upgrade head`: final exit 0; the final applied revision
  is `20261009_0026`.
- Disposable SQLite `uv run alembic check`: exit 0,
  `No new upgrade operations detected.` Cleanup exited 0.
- The first literal Git Bash Alembic attempt returned upgrade exit 1 before running any
  migration because Windows Python could not open Git Bash's `/tmp/...` database path;
  check was not run (recorded as 125), and cleanup exited 0. Converting that same `mktemp`
  directory with `cygpath` resolved the host-path mismatch; the successful results above
  are from the rerun. No source or migration change was required.
- `git diff --check`: exit 0. It emitted only the accepted notice that `app/models.py` will
  be converted from LF to CRLF when Git next touches it.
- `git status --short --branch`: exit 0 on `phase-5d-plans-quotas`; every listed change was
  unstaged or untracked. Nothing was staged, committed, or pushed.
- Existing `scripts/verify_site_browser.py --plans` evidence: exit 0, one check, zero
  failures, with captures under `output/playwright/phase5d-plans/`.

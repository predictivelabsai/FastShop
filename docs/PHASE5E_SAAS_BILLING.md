# Phase 5e: Stripe SaaS billing for FastShop

Phase 5e adds Stripe Checkout subscriptions for FastShop's own Basic and Pro
plans. Platform billing is a separate provider boundary from every tenant
storefront payment flow: it uses only `FASTSHOP_BILLING_*` configuration,
never reads `SiteStripeLiveCredential`, and never falls back to
`STRIPE_SECRET_KEY` or `STRIPE_WEBHOOK_SECRET`. FastShop stores provider object
references and lifecycle state, not card data.

## Surface and route contract

The merchant **Plan & usage** card at `GET /admin/sites` links to
`GET /admin/billing`. The billing view shows the account's effective plan,
the durable subscription state when one exists, and Basic/Pro structural
limits. Prices remain on Stripe's hosted Checkout page; no dollar values are
hardcoded or inferred in FastShop.

Merchant state changes preserve the site-router contract and are registered
through `register_billing_routes(rt, actor, csrf, check_csrf, shell, error)` in
`app/site_routes.py`:

- `POST /admin/billing/checkout` validates CSRF, chooses the account's managed
  billing tenant, persists a retry-stable checkout command, creates a Stripe
  Checkout Session with `mode=subscription`, and redirects to Stripe.
- `GET /admin/billing/success` is a PRG return seam. It does not trust the
  browser to grant a plan; it redirects to the billing view and explains that
  the signed webhook owns reconciliation.
- `GET /admin/billing/cancel` redirects to the billing view without changing
  the plan.

The signed provider callback is
`POST /api/v1/platform/billing/stripe-webhook`. It is registered on the same
FastAPI application as storefront commerce webhooks, but it has its own route,
secret, event ledger, mapping table, and reconciliation code. It has no browser
cookie or CSRF dependency; the dedicated Stripe signature is the trust boundary.
Bodies over 2 MB and signatures outside the five-minute tolerance are rejected.

When dedicated billing credentials are empty, invalid, live-but-unaccepted, or
missing a tier Price ID, the merchant view remains readable and the affected
checkout actions are disabled with an explanatory message. Partial configuration
does not crash the dashboard and does not invent a price.

## Configuration boundary

`app/config.py` exposes the following platform-only settings through
`app/billing.py`:

- `FASTSHOP_BILLING_STRIPE_SECRET_KEY`
- `FASTSHOP_BILLING_STRIPE_WEBHOOK_SECRET`
- `FASTSHOP_BILLING_PRICE_BASIC`
- `FASTSHOP_BILLING_PRICE_PRO`
- `FASTSHOP_BILLING_LIVE_ACCEPTED` (default false)

The two Price values are Stripe Price IDs such as `price_…`; they are the only
mapping from the structural `free` / `basic` / `pro` catalog to Stripe pricing.
The plan catalog still contains limits only and no monetary amount. Missing
Price IDs make only that paid tier unavailable.

In development and acceptance, the secret key must be `sk_test_…` or
`rk_test_…`, and Stripe events must carry `livemode=false`. A live key is inert
unless the application is in production and `FASTSHOP_BILLING_LIVE_ACCEPTED=1`.
This gate is specific to FastShop's platform Stripe account; accepting a
tenant site's live credential never affects it.

## Durable mapping and reconciliation

`BillingSubscription` is unique per billing tenant and keeps the owning user,
selected FastShop tier, a stable checkout-command ID, Stripe
customer/subscription/Checkout references,
provider status, current period end, cancel-at-period-end state, last invoice
state, and an operator-disable timestamp. The Stripe identifiers are unique so
a provider object cannot be attached to two tenants. Queries based on browser
identity always join through an `admin` or `merchant` membership; signed
webhook metadata is resolved by both local row ID and tenant ID before any
write.

`PlatformBillingStripeEvent` stores only event ID, event type, resolved tenant,
and processing time. The global event-ID uniqueness matches the single
FastShop platform Stripe account. The event ledger and entitlement changes
commit together. Replays therefore return `processed (duplicate)` without a
second plan change or mail message.

Handled events and actions:

| Stripe event | Reconciliation action |
| --- | --- |
| `checkout.session.completed` | Validates signed tenant/row/tier metadata, records Checkout/customer/subscription references, and grants the selected tier unless operator-disabled. |
| `customer.subscription.created` | Maps the configured Stripe Price ID to Basic or Pro, records provider state and period, and grants active/trialing/past-due access. |
| `customer.subscription.updated` | Refreshes status, configured tier, period end, and `cancel_at_period_end`; access remains until Stripe reports a terminal status. |
| `customer.subscription.deleted` | Records cancellation and moves the tenant to Free unless operator-disabled. |
| `invoice.payment_failed` | Marks the subscription past due without inventing a grace period, and queues one transactional dunning message per tenant admin and invoice. |
| `invoice.paid` | Records the paid invoice state and restores the mapped active tier unless operator-disabled. |

Terminal Stripe states `canceled`, `incomplete_expired`, and `unpaid` reconcile
to Free. `cancel_at_period_end=true` does not downgrade early: the paid tier
remains until Stripe reports the subscription ended. No local proration,
credit, partial-period, or amount calculation exists.

## Dunning mail

`invoice.payment_failed` writes `CommerceMail` rows with kind
`platform_billing_payment_failed`. The queue targets every active `admin`
membership on the billed tenant, uses the tenant's first site as the existing
transactional-mail scope, and deduplicates on invoice plus user. The renderer
contains no payment amount or card details and directs the merchant to resolve
the Stripe-hosted payment state or contact the operator. Delivery continues
through the existing retryable Postmark worker; the webhook never performs a
mail-provider network request.

## Plan coherence and operator precedence

Billing updates exactly one tenant tier. `app/plans.py` continues to compute an
account plan as the highest tier across the actor's managed tenants, so quota
checks immediately see a webhook-granted tier without a second source of truth.
All quota reads and service-boundary enforcement are unchanged.

The operator console remains authoritative. If
`POST /admin/platform/tenants/{tenant_id}/plan` assigns a tier that conflicts
with that tenant's self-serve subscription, the plan changes immediately and
the billing row receives `operator_disabled_at`. Later Stripe webhooks still
record provider status for audit and deduplication, but cannot change the
operator-selected tier. The merchant billing view explains that self-serve
changes are disabled. Provider cancellation is deliberately not attempted in
that request; the operator must reconcile/cancel the Stripe subscription in
Stripe before deliberately returning the tenant to self-serve billing. This
avoids hidden network work and makes precedence deterministic.

## Sandbox-first acceptance and live ceremony

Test mode is the shipped default. The acceptance sequence is:

1. Create test-mode recurring Prices for Basic and Pro in FastShop's platform
   Stripe account. Configure the four test-mode credential/Price variables.
2. Register the test webhook URL
   `/api/v1/platform/billing/stripe-webhook` for all six documented events.
3. Keep `FASTSHOP_BILLING_LIVE_ACCEPTED=0`; complete Basic and Pro Checkout
   sessions with Stripe test payment methods, replay every event, confirm
   plan/quota agreement, cancellation-at-period-end behavior, deletion to Free,
   dunning queue creation, and event deduplication.
4. For live acceptance, an operator separately creates/reviews live recurring
   Prices, the live webhook endpoint, restricted deployment access, and alerting.
   Record who reviewed the Stripe account, Price IDs, webhook delivery, recovery,
   and cancellation behavior. Do not put key values in that record.
5. In one reviewed production deployment, replace only the
   `FASTSHOP_BILLING_*` values with the live key, live webhook secret, and live
   Price IDs. Set `FASTSHOP_BILLING_LIVE_ACCEPTED=1` only after the preceding
   review. Roll back by clearing the acceptance flag first; this makes live
   Checkout and reconciliation inert without enabling tenant store payments.

`FASTSHOP_BILLING_LIVE_ACCEPTED` is never set by application code, migrations,
tests, or development defaults. Rotation of any live value repeats the external
verification and deployment-acceptance record.

## Migration

`migrations/versions/20261009_0028_saas_billing.py` has revision
`20261009_0028` and chains directly from `20261009_0027`. Upgrade creates
`billing_subscriptions` and `platform_billing_stripe_events` from their model
tables with `checkfirst=True`. Downgrade drops the event ledger first, then the
subscription mapping table.

## Test coverage

`tests/test_saas_billing.py` uses an in-memory SQLite `StaticPool` and no real
Stripe network:

- `test_unconfigured_billing_panel_and_checkout_refusal`
- `test_checkout_session_uses_subscription_mode_and_configured_price`
- `test_platform_webhook_signature_verification`
- `test_checkout_completed_reconciles_plan_and_mapping`
- `test_subscription_created_and_updated_reconcile_tier_and_downgrade`
- `test_subscription_deleted_downgrades_to_free`
- `test_invoice_payment_failed_queues_dunning_for_admins`
- `test_invoice_paid_restores_active_entitlement`
- `test_webhook_replay_is_deduplicated`
- `test_operator_plan_precedence_disables_conflicting_self_serve_reconciliation`
- `test_billing_active_tier_enforces_write_quota_while_reads_stay_open`

The Checkout client test uses `httpx.MockTransport` and asserts subscription
mode, the configured Pro Price ID, tenant metadata, authorization boundary, and
stable idempotency key. Webhook tests construct signed/provider-shaped payloads
locally and make no Stripe request.

## Browser acceptance

`scripts/verify_site_browser.py --billing` starts one isolated signup-enabled
development process with store and platform Stripe values empty. A fresh
merchant opens `/admin/billing`; the script verifies the honest unconfigured
state, disabled Basic and Pro actions, and no horizontal overflow. Evidence is
written under `output/playwright/phase5e-billing/` as:

- `desktop-billing-unconfigured.png` (1440 px)
- `mobile-billing-unconfigured.png` (390 px)
- `verification.json`

## Verification record

The final verification is run in one isolated PowerShell session with an empty
`DB_URL`, development mode, automatic schema creation, a fresh data directory,
empty model/mail/store Stripe credentials, empty platform-billing credentials,
and no `FASTSHOP_ADMIN_EMAIL` override.

Final results:

- `uv run ruff check .`: exit 0, `All checks passed!`.
- Redirected `uv run python -m pytest -q`: exit 0; 511 tests collected and the
  final progress line reached `[100%]`. The only warning was the existing
  Starlette `BlockingPortal` deprecation warning.
- `uv run python -m compileall -q app scripts`: exit 0.
- Disposable SQLite `uv run alembic upgrade head`: exit 0 and applied
  `20261009_0027 -> 20261009_0028`.
- `uv run alembic check`: exit 0, `No new upgrade operations detected.`
- `git diff --check`: exit 0; output contained only the repository's existing
  LF/CRLF conversion notices.
- Disposable verification log, data database, migration database, and their
  fixed verification directory were deleted: cleanup exit 0.
- `uv run python scripts/verify_site_browser.py --billing`: exit 0, one check
  and zero failures. Both required screenshots and `verification.json` were
  refreshed under `output/playwright/phase5e-billing/`.
- The UI detector reported advisories from the established compact stylesheet;
  the one new type-ramp advisory was removed. Independent desktop/mobile review
  returned `ship` after the disabled-state copy and styling fix batch.

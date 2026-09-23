# Phase 2 acceptance status

> **Reconciliation note (2026-09-23):** the figures below are historical. As of this
> date the Alembic head is `20260921_0014` and the full suite reports **242 passing**
> tests. Intermediate counts recorded elsewhere (156 here, 205/206 in the dual-flow
> status, 212 in the production report) reflect earlier states and are superseded by
> this line, not silently rewritten. The sandbox-only limits below still stand.

Audit date: 2026-09-21. **Not complete and not deployed.** The current code is
sandbox-only. No real payment, wallet, carrier or email-provider acceptance is
established by local fixture tests.

The current full 156-test suite passes against a fresh isolated SQLite database.
Ruff, Python compilation, JavaScript
syntax and diff checks also pass. These results establish local regression
coverage, not the provider and deployment acceptance listed below.

An earlier run reused the database from focused tests and failed two stateful
site-route assertions. The clean-database full run passes; use a fresh temporary
data directory for this suite, not an application or previously exercised fixture
database. Alembic reports head `20260921_0012` with no pending model changes.

The source scope is the Phase 2 list and commerce requirements in
`data/feature-requests/h24you/h24you_website_build_brief.md`, with the user's
subsequent approval of Stripe, EU-origin fulfilment, US-only destination tax,
recommended discount stacking and consistent FastShop platform branding.

| Requirement | Current authoritative evidence | Acceptance limit |
| --- | --- | --- |
| Checkout and cart | `store_checkout_routes.py`, `checkout_services.py`, `checkout_payments.py`; route/provider-boundary tests; desktop/mobile checkout and drawer captures | Real Stripe session, amount/tax match and signed callback acceptance still absent |
| Subscriptions | `subscriptions.py`, `subscription_renewals.py`, payment setup/recovery modules and customer routes; activation, changes, retry, renewal and notice tests | Real saved-card authorization, due renewal, authentication recovery and operating scheduler still unverified |
| US tax/shipping | Site-owned EU origin and shipping configuration; Stripe calculation adapter; immutable quote and rounding tests | Actual warehouse and fee below $75 are **not confirmed**; real provider registrations/categories and state/ZIP calculations still need acceptance |
| Discounts | Server-side first-order eligibility, one redemption, sequential 10% savings; cart draft code and checkout validation tests | Real end-to-end offer delivery and payment redemption not yet verified |
| My account | Site-scoped single-use email login, own order history and subscription controls; isolation/CSRF tests and browser flows | Actual email delivery and production-domain acceptance missing |
| Tracking | Merchant-entered owned shipment events, vetted carrier links, customer history and transactional updates | Manual tracking only; no carrier status feed or real shipment acceptance |
| Email capture | Unticked consent, double opt-in, one first-order offer, unsubscribe, separate transactional mail queue | No real Postmark delivery/bounce acceptance or scheduled production mail processing |
| Branding/UI | Shared FastShop platform shell, tokens, logo and drawer chrome; desktop/mobile screenshots | H24YOU public identity is retained; the broader brief's final content/legal/placeholder approvals remain separate launch gates |
| Analytics | Optional site-owned GA4 ID; consent-gated published public page views; desktop/mobile intercepted-network checks | No real property ingestion verified; private checkout/account tagging and purchase/revenue events are not implemented |

## Secure configuration and verification gaps

- A fresh presence-only check found no Stripe test key or webhook secret in the
  local application's configuration. Values were neither printed nor copied.
  No substitute credentials were generated or borrowed from another merchant.
- The brief marks shipping fees and the company address as placeholders. An EU
  country choice does not confirm a warehouse street address or import policy.
  Browser fixture addresses, $10 shipping and $2.05 tax must not become defaults
  represented as approved merchant settings.
- SQLite migration/rollback checks are recorded in the implementation roadmap.
  PostgreSQL execution remains unverified: the Docker daemon is unavailable and
  no local PostgreSQL server binaries were found. This audit did not start shared
  services, touch a production database or equate SQL compilation with a real migration.
- No production worker schedules, deployment, live payment enablement or carrier
  integration were performed. These require an approved environment and inputs.

## Remaining broader brief requirements

The cart drawer and cart-level code entry are implemented. Wallet prerequisites
are documented and wire-tested, but wallet/device acceptance and PayPal are not
complete. Consent-gated public-page analytics is implemented locally, but real GA4
property acceptance and purchase/revenue events remain open. Automatic carrier
integration is also unimplemented. These are not silently removed from the brief because
the core checkout/account tests pass.

See [payment-method acceptance](PAYMENT_METHOD_ACCEPTANCE.md),
[sandbox operations](PHASE2_SANDBOX_OPERATIONS.md), and the
[implementation roadmap](PHASE2_COMMERCE_ROADMAP.md) for exact limits and checks.

To advance real sandbox acceptance, the merchant must securely configure the
intended Stripe test credentials and confirm the actual warehouse address and
below-threshold shipping fee. PayPal/account setup, carrier service and analytics
property configuration need their own confirmed provider inputs. The goal must
not be marked complete until implementation and real-provider acceptance evidence cover
the complete agreed scope.

# Phase 4c: order management and revenue reporting

Phase 4c adds tenant-scoped operations for committed site orders. Merchants can filter and inspect real orders, progress fulfillment, record carrier tracking, confirm exact Stripe refunds, and run bounded server-rendered revenue reports. The surfaces never query `DemoWorkspace` or `DemoCommand`, and no analytics or checkout-page tracking was added.

## Order and fulfillment state model

The canonical `Order.status` state machine is intentionally small:

| Current state | Allowed next state | Required details |
| --- | --- | --- |
| `unfulfilled` | `fulfilled` | Carrier and tracking number; allowlisted HTTPS tracking URL is optional. |
| `unfulfilled` | `cancelled` | Cancellation reason. Cancellation does not issue a refund. |
| `fulfilled` | `delivered` | Optional customer-visible note. |
| `fulfilled` | `cancelled` | Cancellation reason. Cancellation does not issue a refund. |
| `delivered` | — | Terminal. |
| `cancelled` | — | Terminal. |

Each transition locks and re-reads the tenant-owned order, compares the submitted expected status, updates `Order.status`, and appends a `ShipmentEvent` with the actor and shipping details. Tracking URLs are limited to DHL, FedEx, UPS, USPS, or Omniva HTTPS hosts. The existing transactional-mail queue receives the new event. The dedicated order detail combines customer, address, line items, tax, shipping, payment state, checkout reconciliation, payment transactions, shipment events, refund commands, and outbox delivery records into one timeline.

## Refund policy and provider safety

Refunds are available to tenant `admin` and `merchant` memberships. Editors cannot view the order-management surface because it contains customer data and cannot mutate fulfillment or payment state. The configured platform operator has no implicit cross-tenant access: the operator can act only where an explicit admin membership exists.

Automatic refunds are supported only when the owned order is `paid` or `partially_refunded` and has a successful Stripe charge transaction for that site, currency, and order. The merchant enters a positive integer minor-unit amount and a 10–500 character reason, then reviews a separate confirmation page that names the exact formatted amount. Partial refunds are allowed only when explicitly entered and confirmed. FastShop never adjusts the requested amount silently.

Alembic revision `20261009_0023` adds `RefundCommand`, the minimal new schema required to make a refund durable before provider I/O. It records the site, tenant, order, actor, exact amount and currency, reason, immutable request hash and provider payload, guarded payment mode, sanitized result, attempts, and provider references. States are:

- `creating`: the exact command is committed and may be replayed after an unknown transport outcome;
- `pending`: Stripe accepted the exact command but has not reported success;
- `succeeded`: an exact successful result produced one `PaymentTransaction(kind="refund")` and an `order.refunded` outbox event;
- `failed`: Stripe reported failure and the amount is available for a new confirmed request;
- `reconciliation_required`: Stripe returned an identifier, amount, currency, payment reference, livemode, or status that did not exactly match the command.

The site/request key is unique and bound to a hash of order, amount, and reason. Stripe receives a stable idempotency key derived from the durable command ID. A retry must reference an existing unresolved command; a submitted `retry` flag cannot bypass the confirmation step. Creating, pending, and reconciliation-required commands reserve their exact amounts, so concurrent or later requests cannot exceed the captured balance while an outcome is unresolved. Successful refunds plus unresolved reservations can never exceed successful captured charges. A full captured refund changes the order payment state to `refunded`; a smaller successful amount changes it to `partially_refunded`.

The public refund entry point lives in `app/checkout_payments.py` beside the existing durable checkout commands and delegates to the tenant-scoped refund command implementation. Every new or replayed provider request resolves `commerce.payment_mode`, which delegates to the Phase 4b `live_credentials.effective_mode` guard. The persisted command is bound to that resolved mode. Stripe secrets remain inside `StripeGateway`; order routes, commands, audit payloads, HTML, and logs never receive them.

## Revenue report math

The default report is the trailing 30 UTC calendar days, inclusive. Merchants can select an inclusive range of 1–366 days. The site's `Channel.currency` is authoritative.

- **Gross sales:** sum of `Order.total_minor` for tenant- and site-owned committed orders (`paid`, `partially_refunded`, or `refunded`) placed in the range.
- **Refunds:** sum of successful real `PaymentTransaction(kind="refund")` rows completed in the range. A refund of an older order therefore reduces the current range even when that order is outside it.
- **Net revenue:** gross sales minus refunds. It may be negative.
- **Order count:** number of included committed orders placed in the range.
- **Average order value:** gross sales divided by order count, rounded to the nearest integer minor unit; zero for an empty range.

Orders or refund rows whose currency differs from the site's channel currency are excluded and reported as a visible warning. Currencies are never converted or combined. The per-site report and the operator summary use the same calculation. The operator summary lists only sites where the configured operator has an explicit tenant `admin` membership and keeps each site's currency and totals on separate rows.

## Role matrix

| Capability | Tenant admin | Merchant | Editor | Platform operator |
| --- | --- | --- | --- | --- |
| View real order list/detail and customer data | Yes | Yes | No | Only with tenant admin membership |
| Update fulfillment and tracking | Yes | Yes | No | Only with tenant admin membership |
| Confirm/initiate/reconcile exact refund | Yes | Yes | No | Only with tenant admin membership |
| View one site's revenue report | Yes | Yes | No | Only with tenant admin membership |
| List per-site revenue summaries | No | No | No | Yes, limited to explicit admin memberships |

Every state-changing route checks CSRF and uses Post/Redirect/Get for success and failure. Fulfillment uses expected-state locking; refund commands provide command-level idempotency and provider reconciliation.

## Offline evals

`python -m scripts.eval_order_management` writes `output/evals/phase4c-order-management.json` and makes no external calls.

Result: 7/7 passed; external calls: 0.

Covered cases: fulfillment transition legality and tracking persistence, tenant manager authorization, exact refund idempotency, refund bounds, integer report math, demo-commerce isolation, and empty bounded ranges. Pytest additionally covers mocked Stripe transport, result mismatch reservation, effective-mode failure, mixed-currency exclusion, date bounds, tenant/site invisibility, and CSRF/PRG.

## Verification

- `ruff check .` and the full isolated SQLite suite ran in the same PowerShell session: 454 tests passed with 100% dots and exit code 0. The existing 50-render golden baselines passed within that suite.
- `python -m compileall -q app scripts`: passed.
- Disposable SQLite `alembic upgrade head` through `20261009_0023`: passed; `alembic check`: no new upgrade operations detected; the disposable database was removed.
- `python -m scripts.eval_order_management`: 7/7 passed, no external calls.
- `scripts/verify_site_browser.py --merchant-only --order-management`: 3 checks passed, 0 failures. The Phase 4c flow covered the real order list, legal fulfillment transition, exact refund confirmation, revenue math, no analytics scripts, and viewport-overflow assertions.
- Desktop and mobile evidence: `output/playwright/phase4c-order-management/`.
- Impeccable UI detector: no findings.
- `git diff --check HEAD`: passed.

## Follow-ups

- Partial-fulfillment line allocation and packing slips.
- Deeper customer order emails for fulfillment, refund, and exception milestones.
- CSV order export with the same tenant, date, and currency boundaries.

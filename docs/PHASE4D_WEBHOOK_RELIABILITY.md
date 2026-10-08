# Phase 4d — webhook and delivery reliability

Date: 2026-10-09  
Status: implemented; verification results recorded below

## Scope

This slice makes two asynchronous commerce boundaries recoverable without changing checkout pages, provider-mode policy, or the FastERP transport contract:

1. FastShop outbox deliveries receive bounded deterministic backoff, terminal dead-letter handling, topic dispatch, and tenant-visible recovery controls.
2. Stripe webhooks receive a small per-site deduplication ledger, while merchants and tenant admins can explicitly re-pull provider truth for one real site order.

The slice remains sandbox-first, stores no credentials or provider payloads in new tables, does not read demo-commerce models, and never writes into a FastERP schema.

## Decisions

### Outbox retry policy

`OutboxEvent` gains a nullable `next_attempt_at` UTC timestamp. Existing and newly-created pending rows with no timestamp are immediately eligible. Delivery selects only `pending` rows whose timestamp is null or due, ordered by `created_at` and then primary key, with the existing `FOR UPDATE SKIP LOCKED` claim behavior.

An attempt is counted before dispatch. Failed attempts use a deterministic exponential delay of `30 * 2^(attempts - 1)` seconds, capped at one hour. The eighth failed attempt changes the status to `dead` and clears `next_attempt_at`. Successful delivery changes the status to `delivered`, clears the error and next-attempt timestamp, and preserves the event ID as the FastERP idempotency key.

Topic dispatch is explicit. `order.confirmed` keeps the existing FastERP HTTP transport and order payload. Other topics are intentionally acknowledged as delivered by a no-op handler until a consumer is introduced. This prevents unrelated topics such as `order.refunded` from remaining pending forever while keeping dispatch behavior visible and testable.

The historic `failed` state remains readable in the recovery surface for compatibility, but automatic delivery writes only `pending`, `delivered`, or terminal `dead`.

### Tenant-safe dead-letter recovery

The order-management surface lists only `dead` or legacy `failed` events whose tenant matches the selected site and whose payload explicitly names that site. This avoids exposing tenant-level events to unrelated sites within the same tenant. Errors are normalized to one line and truncated to 300 characters before display.

Admin and merchant memberships may view and requeue events. Editor memberships are rejected by the same manager-site role gate used for refunds. Requeue is a CSRF-protected POST followed by a redirect, resets attempts and error text, and makes the event immediately eligible. Each request requeues one event only.

Platform operator visibility uses explicit admin memberships and presents the same per-site view; platform-email identity alone never broadens the tenant query.

### Stripe missed-event resync

The order detail page offers a bounded one-order provider resync. The POST is CSRF-protected and uses Post/Redirect/Get. The service:

- resolves the site through an admin/merchant membership;
- tenant- and site-scopes the `SiteOrder`, `Order`, and associated checkout attempt;
- obtains the gateway through the existing sandbox/live credential boundary without changing `effective_mode` or accepting credentials from the request;
- fetches the checkout session through `StripeGateway.checkout_status` and delegates exact amount, currency, site, mode, and checkout-reference validation to the existing idempotent checkout reconciliation path;
- updates existing payment/order state or creates the already-defined charge record only through that reconciliation path, whose provider/external-ID uniqueness prevents duplicate charges;
- writes a secrets-free `SiteChangeSet` summary containing only local identifiers and before/after state.

The action accepts no provider identifier from the browser and performs at most one order reconciliation per POST. A bulk/site-wide endpoint is deliberately excluded from this slice.

### Webhook deduplication ledger

`StripeWebhookEvent` stores `tenant_id`, `site_id`, Stripe event ID, event type, and `processed_at`, with a unique `(site_id, event_id)` constraint and tenant/site indexes. It stores no payload, signature, customer data, or secret.

Signature, body-size, timestamp, and payment-mode validation remain unchanged and execute before deduplication. After verification, an existing ledger row returns `processed (duplicate)` without invoking reconciliation. A new row is added only after the event handler succeeds or intentionally ignores an unrelated valid event, in the same database transaction as the reconciliation work. Failures roll back both commerce changes and the tentative ledger row so Stripe can retry.

## Authorization and isolation

- New merchant surfaces use the Phase 4c admin/merchant role matrix; editors have no access.
- Every owned query includes tenant and, where applicable, site predicates.
- Dead-letter events are joined to their site through the bounded `payload_json.site_id` discriminator in addition to tenant scope.
- No `DemoWorkspace` or `DemoCommand` table is read.
- No public, storefront, checkout, or analytics route changes are introduced.
- Every state-changing browser route checks CSRF and returns a 303 redirect with a bounded notice.

## Migration

Revision `20261009_0024` follows `20261009_0023` and:

- adds `outbox_events.next_attempt_at` plus a due-work index;
- creates `stripe_webhook_events` with its site/event uniqueness constraint and lookup index.

The downgrade drops the ledger and due-work index/column without altering existing event rows.

## Test and evaluation plan

`tests/test_webhook_reliability.py` builds its own in-memory database and seed data. It covers due-only FIFO selection, deterministic backoff, terminal dead-lettering, non-FastERP topic acknowledgement, requeue authorization/isolation, one-order Stripe resync idempotency and audit, and webhook duplicate short-circuit behavior.

`scripts/eval_webhook_reliability.py` is offline and uses an in-memory database plus deterministic fake gateways/transports. Its JSON report records checks for bounded retry/dead-letter behavior, provider reconciliation idempotency, tenant isolation, and webhook deduplication.

Browser verification extends `scripts/verify_site_browser.py` with `--webhook-reliability`. It seeds real site order/outbox records locally, exercises the authenticated merchant surface without external provider calls, and writes desktop and mobile screenshots to `output/playwright/phase4d-webhook-reliability/`.

## Verification results

The prescribed lint and test commands ran in one PowerShell session with the isolated environment from the task, including an unset `FASTSHOP_ADMIN_EMAIL`:

- `uv run ruff check .`: passed; exit code 0.
- `uv run python -m pytest -q`: 459 tests passed; exit code 0.
- pytest `[100%]` line: 27 dots.
- pytest `F` / `E` / `s` / `x` markers: none.
- `uv run alembic upgrade head`: passed against a disposable SQLite file; exit code 0.
- `uv run alembic check`: `No new upgrade operations detected.`; exit code 0.
- disposable Alembic SQLite file: deleted after validation.
- `uv run python -m compileall -q app scripts`: passed; exit code 0.
- `uv run python scripts/eval_webhook_reliability.py`: passed all four offline checks; exit code 0.
- `git diff --check`: passed; exit code 0.
- Impeccable UI detector: no findings.
- browser verifier: passed with no recorded browser failures.
- browser evidence:
  - `output/playwright/phase4d-webhook-reliability/desktop-provider-resync.png`
  - `output/playwright/phase4d-webhook-reliability/mobile-provider-resync.png`
  - `output/playwright/phase4d-webhook-reliability/desktop-delivery-issues.png`
  - `output/playwright/phase4d-webhook-reliability/mobile-delivery-issues.png`

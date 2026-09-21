# Phase 2 sandbox operation and acceptance

Phase 2 is local implementation, not a deployed/live shop. Mock-provider tests
and browser fixtures are not real Stripe or Postmark acceptance. The application
rejects live Stripe keys and live commerce mode. Do not enable scheduled workers
until the intended site, provider accounts, warehouse and fees are confirmed.

## Secure configuration

Use the application's ignored `.env` or your deployment secret manager. Never put
credentials in chat, command arguments, screenshots, Git, or this document.
The repository ignores `.env`; restrict its filesystem permissions to the operator.

- Primary H24YOU site: `STRIPE_SECRET_KEY` (test mode) and
  `STRIPE_WEBHOOK_SECRET`. Global credentials are only used for the slug in
  `FASTSHOP_STRIPE_PRIMARY_SITE`, default `h24you`.
- Other sites: `FASTSHOP_STRIPE_<UPPERCASE_SITE_ID>_SECRET_KEY` and the matching
  `FASTSHOP_STRIPE_<UPPERCASE_SITE_ID>_WEBHOOK_SECRET`. Do not borrow another
  merchant's account credentials.
- Email: the existing `POSTMARK_SERVER_TOKEN` (or `POSTMARK_API_TOKEN`) and
  `FASTSHOP_CONTACT_FROM` (or `FROM_EMAIL`) must belong to the approved sender.
- Set the real HTTPS `FASTSHOP_PUBLIC_URL` and a strong persistent
  `FASTSHOP_SESSION_SECRET`. Restart the intended application after configuring
  secrets; verify presence through the merchant readiness screen without exposing values.

Use `/api/v1/commerce/<site_id>/stripe-webhook` on the platform's public host for
the intended site's test endpoint. The application verifies signatures and
fetches authoritative provider state. A browser success URL does not settle a payment.

Before testing, enter the actual EU warehouse address and approved USD shipping
fee in the site's commerce settings. The proposed free-shipping threshold is
$75 of merchandise **after discounts**. Review product tax categories and Stripe
Tax registrations; do not check the review box merely to bypass configuration.
US state/ZIP destination tax comes from the provider, not hard-coded state rates.
The $10 shipping and $2.05 tax in browser fixtures are not merchant policy.

## Optional public-page analytics

In the site's shared brand/content settings, enter the intended property's GA4
measurement ID and choose **Publish shared settings**. Leave it empty to disable
analytics. Only published public pages initialize the tag, after explicit
analytics consent. Admin previews, checkout and accounts are excluded. No Meta
pixel or purchase/revenue events are implemented.

The integration queues one page view per page load, removes query strings and
fragments from its page/referrer URLs, and keeps advertising consent denied.
Withdrawal removes this site's analytics cookies and reloads to discard the tag;
normal cross-tab storage changes propagate withdrawal. Global Privacy Control
also prevents initialization. Do not treat these controls as a legal-compliance
guarantee. Review GA4 property settings, enhanced measurement and privacy copy
before activation, then verify actual network payloads and property ingestion.
Local intercepted-tag tests do not execute Google's real runtime.

Reference: [Google's basic consent mode](https://developers.google.com/tag-platform/security/concepts/consent-mode).
No real measurement ID has been configured by the local fixture verifier.

## Worker commands

Run only from the intended, migrated sandbox application's environment. These
commands are operational, not dry runs: renewal processing can initiate test
charges, and the email worker can send actual emails if its sender is configured.

```sh
uv run python -m scripts.reconcile_checkouts --limit 50
uv run python -m scripts.process_subscription_renewals --limit 50
uv run python -m scripts.queue_subscription_notices --days-ahead 3 --limit 100
uv run python -m scripts.process_commerce_mail --limit 50
```

After provider acceptance, a suggested schedule is every minute for payment
reconciliation and mail delivery, and hourly for upcoming notices. Confirm the
reminder window with the merchant. No production schedules are installed by this work.
Do not treat this suggested reminder window as a legal-notice compliance guarantee.

Monitor worker results and errors, not just process exit status. Investigate
`needs_attention` and pending payments before releasing stock or retrying a
charge. Unknown provider creation outcomes older than the idempotency safety
window require provider reconciliation, not a fresh payment command. Failed
quotes (for example, changed prices, missing stock or configuration) require
merchant attention even if no payment attempt was created.

The email queue retries up to five attempts with backoff. `sent` means accepted
by Postmark, not inbox delivery; check provider delivery/bounce evidence separately.
Email delivery is at-least-once across a process crash. Internal deduplication
prevents duplicate queued receipts, but a crash after provider acceptance can
repeat an email. It must never repeat an order or charge.

## Real-provider acceptance still required

- Test a US one-time order with actual state/ZIP tax, shipping below/above the
  threshold, and the first-order code. Verify totals before payment and reconcile
  both webhook-before-return and return-before-webhook orderings.
- Test initial subscription authorization, recurring 10% pricing, mixed bags,
  first-order stacking, a due renewal, and its separate tax transaction.
- Test decline and authentication-required cards, confirmed cancellation before
  stock release, one-time delivery recovery, and no automatic subscription resume.
- Test saved-card updates, skip/pause/frequency/flavour/address changes and
  cancellation while a payment is processing. Verify customer/tenant isolation.
- Test account login, newsletter double opt-in, offer delivery, unsubscribe,
  order receipts, shipment updates and renewal notices using approved recipients.
  Confirm marketing withdrawal does not suppress transactional order messages.
- Verify the actual carrier tracking workflow, scheduler operation, email
  delivery evidence, migration/rollback in PostgreSQL and operational recovery.

Record provider event/order IDs in an approved private acceptance record, never
raw card data, credentials, magic-link tokens or browser session state. Until
these checks and the remaining brief acceptance items pass, Phase 2 is not complete.

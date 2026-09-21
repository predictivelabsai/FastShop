# Phase 2 commerce implementation roadmap

Phase 2 begins only after the deployed Phase 1 design and content experience has
been shown to the user and approved. This document is planning, not activation.

## Product boundary

FastShop combines editable sites and editorial content with structured commerce.
The H2 4 You brief describes Shopify behavior, but the platform is FastShop.
Shopify checkout, customer accounts, subscriptions, email and analytics are not
automatically available in this architecture. Provider choices need a separate
decision based on the Estonian merchant entity and US customers.

The brief's “no Stripe” wording is attached to footer payment icons. It should
not be interpreted as an explicit prohibition on Stripe as a backend processor.
Display actual supported payment methods rather than gateway company logos.

## 1. Merchant configuration and catalog readiness

- Confirm prices, pack sizes, tablet ingredients/Supplement Facts, bottle specs,
  legal entity/address, supported payment methods, and approved product copy.
- Extend Phase 1 merchant product/variant editing and galleries with per-channel prices, inventory
  policy, publication checks, and catalog import with an auditable preview.
- Treat the three flavours as tablet variants, and the bottle as a separate
  product. Bind template sections to owned product IDs, never copied prices.
- Resolve tenant, site and channel for every commerce route, API and background
  job. Reject cross-tenant IDs at service boundaries and database constraints.
- Keep prices in integer minor units. Store USD 29.95 as 2995; never convert a
  supplier's EUR price into a USD retail price without an explicit decision.

Acceptance: merchant edits a flavour's price and product copy; the correct site
renders the change without exposing another merchant's data.

## 2. Cart, promotions and checkout

- Implement cart drawer/page, quantity changes, variant availability, guest
  checkout and optional account association.
- Replace demo tax/shipping calculations with provider-backed quotes and an
  immutable checkout quote snapshot, including expiry and currency.
- Apply discount rules server-side. Define whether two 10% offers combine
  sequentially (19% effective reduction) or additively (20%), whether they apply
  to renewals, and which products and shipping charges are eligible.
- Add first-order eligibility, usage limits, expiry, abuse controls and
  reconciliation for abandoned payment attempts.
- Preserve stable inventory lock order and idempotent checkout. Use reservation
  expiry/release, not indefinite allocation on unsuccessful payment attempts.
- Select a payment adapter after checking current country, business-category,
  recurring-payment and wallet support in the provider's official documentation.
- Use hosted/tokenized payment fields; never store card details. Payment webhooks
  must be signed, replay-safe and idempotent. Browser return URLs are not proof
  that payment succeeded.

Acceptance: retrying payment and webhook deliveries creates one order and one
allocation; failed/expired payments release reservations; totals match the
provider across tax, shipping and promotion scenarios.

## 3. US tax and shipping

- Confirm registrations and collection obligations with the merchant's adviser;
  automatic calculation does not register a business or establish obligations.
- Configure US-only delivery, allowed territories, address validation, shipping
  origin, carriers, fulfilment times, and duties policy.
- Define whether the proposed $75 free-shipping threshold uses pre-discount or
  post-discount merchandise subtotal and how subscriptions affect it.
- Implement a tax quote adapter with line/item tax codes and address-based
  calculation. Record provider quote IDs and tax breakdown on each order.
- Add configurable flat rates first if approved; integrate live carrier rates
  only when required. Test unavailable services and address failures.

Acceptance: unsupported destinations are rejected and a customer sees the final
tax-inclusive payable amount before payment authorization.

## 4. Tablet subscriptions

- Support one-time purchase and monthly subscription with the agreed 10% saving.
- Select recurring billing technology only after verifying flavour changes,
  frequency changes, skip, pause, cancellation, payment updates and address updates.
- Separate subscription contract, scheduled delivery and payment records. A
  subscription is not an endlessly reused initial order.
- Store effective-dated variant/address/frequency changes and make changes apply
  to the correct next delivery with a clearly communicated cutoff.
- Handle failed renewals, retries, out-of-stock flavours, pause/resume and
  cancellation races with idempotent state transitions and an audit history.
- Keep the bottle ineligible for subscriptions at both UI and service layers.

Acceptance: a customer subscribes, changes next delivery's flavour, skips one
cycle, resumes, updates payment, and cancels without duplicate charges/orders.

## 5. Customer accounts, tracking and transactional email

- Add customer authentication distinct from merchant site administration.
- Scope order lookup to authenticated ownership; do not use globally guessed
  order numbers as authorization. Provide secure guest order access if needed.
- Implement order history, order details, shipment events, tracking links,
  split shipments, returns/refunds, and subscription management.
- Send order, shipment, refund and renewal messages from a verified domain using
  an outbox with retry and provider event reconciliation.
- Keep marketing consent independent from transactional notifications.
- Connect FastERP through the existing API/outbox boundary. No cross-schema
  writes; specify who owns stock, fulfilment, refunds and financial records.

Acceptance: a customer sees only their orders and current tracking; events and
messages reconcile after retries and temporary provider failures.

## 6. First-order email capture, analytics and reviews

- Activate the offer form with unticked marketing consent, privacy link,
  dismissal memory, accessible errors, and duplicate-subscription handling.
- Use a chosen email-marketing provider for subscription status and unsubscribe;
  persist source, timestamp and consent-text version.
- Deliver the discount only after the agreed subscription/verification flow.
- Wire analytics through the consent categories introduced in Phase 1. Test
  network requests before consent, after decline, after acceptance and withdrawal.
- Add real review collection/moderation or a selected provider. Never migrate
  sample review content into real customer review records.
- Do not add a Meta pixel until specifically requested.

Acceptance: declining consent produces no marketing tracking or subscription;
unsubscribe stops marketing; revenue events do not fire twice on page reload.

## 7. Release and operating requirements

- Run the required lint, tests, compile, migration and diff checks.
- Cover tenant isolation, concurrency, retries, rounding, promotion combinations,
  tax/shipping failures, renewal schedules, refunds and permissions.
- Exercise desktop/mobile journeys in Playwright with provider sandbox payments
  and save screenshots without payment or customer secrets.
- Confirm backup/restore, migration rollback/forward-fix, provider observability,
  reconciliation dashboards and contact/integration queues.
- Deploy the exact pushed commit, verify CI and Coolify health, and test the
  production site in sandbox mode before explicitly enabling live payment.

Delivery order: configuration and catalog → cart/checkout/providers → US tax and
shipping → subscriptions → accounts/tracking/email → marketing/analytics/reviews
→ sandbox acceptance and live release. Each stage should have a working demo
and meaningful failure-path evidence before the next integration depends on it.

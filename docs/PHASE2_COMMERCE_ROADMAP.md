# Phase 2 commerce implementation roadmap

Phase 2 was authorized after the deployed Phase 1 review. Implementation is in
progress, not activated on the public store. The full acceptance criteria below
remain in scope; completing a configuration screen does not complete Phase 2.

## Confirmed decisions and current implementation

- Stripe approved for checkout, recurring billing and US address-based tax.
- Fulfilment originates in Estonia or another EU country. The actual warehouse
  address is configurable; it is not inferred from the legal address.
- Recommended discount policy: subscription prices reduced 10%, then a further
  10% first-order merchandise discount once only (19% before cent rounding).
  Renewals retain only the subscription saving. Resulting prices round half-up
  to cents and cart-level offer savings are allocated by largest remainder.
- The proposed $75 free-shipping threshold is configurable and evaluated after
  merchandise discounts. The flat shipping fee is still unconfirmed and remains
  unset, blocking quotes rather than silently providing free shipping.
- Phase 1 and Phase 2 merchant screens now reuse FastShop's shared CSS tokens,
  green accent, logo component and HTML shell. Public merchant rebranding awaits
  clarification; H2 4 You's existing public identity has not been overwritten.
- Added site-owned configuration, allowed US destinations, product tax category
  mapping, subscription eligibility, sandbox tax preview and immutable expiring
  quote snapshots. The Stripe adapter rejects live keys and invalid webhook
  signatures.
- Added site-scoped passwordless customer accounts, owned order history/details,
  merchant-entered shipment tracking, double opt-in email capture, unsubscribe,
  and one first-order offer per verified subscriber. Newsletter confirmation
  does not sign the customer in; account login does not grant marketing consent.
  Verification links expire and are single-use. Unsubscribe remains available
  when a merchant disables commerce or takes the store offline.
- Postmark delivery uses a retryable outbox with tracking disabled. The worker
  is `scripts/process_commerce_mail.py`; production scheduling and real email
  delivery acceptance remain outstanding. Tokens are derived only when sending
  and travel in URL fragments, not request query strings or queue payloads.
- Paid checkout and renewal orders now queue a FastShop-branded transactional
  confirmation atomically with the order; merchant-entered shipment events queue
  delivery updates. Neither requires marketing consent or account verification.
  Messages contain scoped database references, not payment tokens, and link to
  authenticated order details. Duplicate settlement cannot queue another receipt.
  All messages explicitly identify sandbox orders. Queue retries are at-least-once:
  a crash after provider acceptance may repeat an email, but not a charge/order.
- Added a transactional checkout backend: customer-bound idempotent attempts,
  stable stock lock ordering, origin-country stock reservations, first-order
  eligibility and single redemption, and paid-order/outbox creation. Duplicate
  callbacks cannot create another order or allocate stock again. Local timeouts
  never imply failed payments; only an unstarted checkout or a provider-confirmed
  expiration releases stock.
- Signed Stripe callbacks are handled at
  `/api/v1/commerce/{site_id}/stripe-webhook` on the platform host. The handler
  fetches the current Checkout Session from Stripe, checks site/reference/currency/
  amount/test-mode binding, then settles transactionally. It can recover a session
  reference when a webhook arrives before the creation response is saved. Invalid
  signatures are rejected; reconciliation failures return retryable errors.
- Added one-time Stripe hosted-session creation with durable immutable provider
  commands and stable idempotency keys. The handoff fixes shipping to the reviewed
  US destination and enables Stripe automatic tax; it does not return the hosted
  URL unless tax, shipping and total match the quote. A rejected session remains
  referenced for safe cancellation. Unknown creations stop retrying before the
  provider's idempotency retention window can lapse.
- Audited hosted Apple Pay/Google Pay prerequisites against Stripe documentation.
  The existing card-based command supplies a saved US shipping address before
  creating the Checkout Session; two new wire-level tests verify that behavior
  for one-time and initial recurring purchases. Wallet/account/device acceptance
  is not established by these tests. PayPal is still excluded from the method
  allowlist; merchant-account eligibility and the platform/Connect distinction
  must be resolved before implementing it. See
  [payment-method acceptance](PAYMENT_METHOD_ACCEPTANCE.md).
- Added `scripts/reconcile_checkouts.py` for unstarted expiry, durable-command
  retry, provider reconciliation and confirmed unpaid session cancellation.
  Scheduling/operational alerting are not deployed. Unknown outcomes retain stock
  and report a needs-attention count rather than silently treating payment as failed.
- Added site-owned persisted bags, product purchase forms, quantity updates,
  guest/signed-in US delivery forms, discount entry, address/tax/total review,
  Stripe handoff, payment-status verification and cancellation. All commerce
  screens use the shared FastShop HTML/CSS shell. Guest access requires the
  browser's own cart; guessed checkout IDs do not grant access. Guest payments
  do not authenticate the customer or subscribe them to marketing.
- Added a progressive-enhancement slide-out cart with FastShop branding, live
  header quantity counts, quantity editing and a persisted draft discount-code
  field. It embeds the same server-rendered cart, so pricing and ownership checks
  are not duplicated in JavaScript. Codes remain unvalidated until checkout uses
  the customer's email; saving a code never claims a discount has been applied.
  Native dialog focus containment, Escape/close focus restoration, reduced-motion
  support and a normal full-page fallback are retained. Cart pages allow embedding
  only by their own origin and use private/no-store caching.
- Added subscription contracts, separate renewal-cycle records and versioned
  customer-change audit events. Initial subscription orders require explicit
  recurring-payment consent; Stripe must confirm the saved payment method and
  customer binding before a contract is activated. The contract retains the
  10%-discounted recurring merchandise price, not the first-order offer price.
- Subscription scheduling is owned by FastShop, not a native Stripe Subscription.
  Stripe holds payment methods; FastShop will initiate each stock-backed renewal
  after fresh shipping/tax validation. Backend commands now support skip,
  pause/resume without catch-up charges, cancellation, same-price flavour changes,
  US address changes and frequency changes. The original month-end anchor is
  preserved (January 31 → February 28 → March 31). Already-processing cycles are
  identified separately from future deliveries; cancellation does not claim a refund.
- Added stock-backed renewal preparation and a durable create-then-confirm Stripe
  PaymentIntent flow. Renewals exclude the first-order offer, recheck the agreed
  merchandise price, use current shipping/tax, preserve the original consent,
  and never charge several missed periods as catch-up. Failed/3DS-required
  payments are cancelled before stock is released and the contract is paused;
  processing/unknown outcomes retain stock for reconciliation.
- Renewal PaymentIntent webhooks fetch current provider state before creating one
  paid order. Stripe Tax transaction posting is retried separately, so a tax API
  outage cannot duplicate a charge. `scripts/process_subscription_renewals.py`
  prepares due deliveries and reconciles pending payments/tax records. It is not
  scheduled in production; all provider-facing verification so far is mocked.
- Added FastShop-branded subscription account screens for skip, pause/resume,
  frequency, same-price flavour, US address changes and confirmed cancellation.
  Customer identity, CSRF and optimistic versions are checked on every change.
  Existing account access survives disabled/draft stores, including custom domains,
  while their cart/checkout routes remain closed.
- Added durable Stripe-hosted card-update sessions. They use setup mode (no charge),
  are scoped to the customer/contract, and only replace the saved payment token
  after verifying the completed SetupIntent and off-session authorization. A card
  update does not silently resume a paused contract or change an in-flight charge.
- Added customer-driven recovery for the current failed subscription delivery.
  The original charge must be terminal and its stock released. A separate,
  auditable one-time Checkout Session uses freshly reserved stock and recalculated
  shipping/tax, retains the agreed subscription merchandise saving, and excludes
  the first-order offer. It never reuses the failed off-session PaymentIntent.
  The customer reviews the total before leaving for hosted checkout; Stripe's
  [Checkout authentication support](https://stripe.com/guides/3d-secure-2)
  provides the on-session authentication path. Real 3DS acceptance remains pending.
  Recovery does not resume future deliveries, replace the saved card or create
  another subscription. Pending recovery blocks schedule/address/flavour changes,
  but not cancellation of future deliveries. Duplicate settlement creates one order.
- Enabled sandbox storefront subscription purchase for explicitly eligible
  products. The bag keys lines by variant and purchase option, so the same
  flavour can be bought once and subscribed to without merging quantities.
  Delivery checkout displays the recurring merchandise price and requires an
  unticked explicit authorization checkbox, with the consent version/text/time
  stored in the quote. Review distinguishes monthly and one-time lines and shows
  the first-delivery shipping/tax total separately from recurring merchandise.
  Only verified paid-and-saved-card provider state activates one contract;
  one-time items are excluded from renewals. Live payments remain disabled.
- Added transactional failed-renewal notices after confirmed cancellation or
  precharge expiry. Unknown/processing payments do not generate failure notices.
  Notices are suppressed if the customer has recovered, resumed, cancelled or
  started recovery before sending. Upcoming notices use a configurable window
  (three days by default), agreed recurring merchandise prices and authenticated
  management links; they do not present an estimated total as a final tax quote.
  Edited/paused/cancelled/overdue subscriptions invalidate queued reminders.
  `scripts/queue_subscription_notices.py` queues reminders without sending or
  charging; it is not scheduled in production. Paid renewal receipts use the order-confirmation queue.
  Promotion redemption exists in the
  backend and is now connected to the customer discount-code field. Tracking is manual;
  no carrier status feed is connected. New backend flows remain sandbox-only.

### Local verification checkpoint

Optional GA4 configuration now uses the existing site-owned shared settings and
publish workflow. Published public pages request the tag only after explicit
analytics consent; previews, checkout and accounts do not include it. Malformed
consent fails closed, Global Privacy Control prevents initialization, withdrawal
disables the tag and reloads the page, and cross-tab withdrawal is propagated.
Queued page-view URLs omit query strings and fragments. Advertising consent is
never granted and no Meta pixel is installed. This is public page-view support,
not purchase/revenue reporting or proof of real GA4 ingestion.

`scripts/verify_analytics_browser.py` passes desktop/mobile consent, decline,
repeat-grant, cross-tab withdrawal, scoped-cookie cleanup, malformed-consent and
GPC checks with Google requests intercepted locally. Screenshots are under
`output/playwright/phase2-analytics/`. No real analytics property was contacted.

The full brief was re-read after renewal notifications. Passing the backend
suite does not complete its remaining integration requirements: actual wallet/payment-method acceptance,
carrier integration and analytics provider/revenue acceptance still require implementation or
verification. Footer payment labels are currently marked planned, not proof of
enabled methods. See [sandbox operations](PHASE2_SANDBOX_OPERATIONS.md) for secure
configuration, worker commands and outstanding provider acceptance.

- The full 156-test suite passes on a fresh isolated database, including analytics configuration, transactional-mail, customer-driven
  recovery, storefront subscriptions, renewal notices and cart/discount regressions.
  Six cart-route tests also pass after the final same-origin framing header change.
  This includes both wallet-prerequisite wire tests and all 14 hosted-payment tests.
  A subsequent [acceptance audit](PHASE2_ACCEPTANCE_STATUS.md) confirms that
  provider acceptance, merchant inputs and the broader brief gaps remain open.
- Transactional-mail tests cover queue deduplication, guest receipts without
  marketing consent, tenant/site/customer isolation, shipment binding, provider
  retries and rollback without orphan receipts. No real emails were sent.
- Twelve subscription-notice tests cover single queueing/delivery, independence
  from marketing consent, precharge expiry, unknown/processing payment exclusion,
  recurring rather than introductory prices, scoped references and suppression
  after state, date, version or commerce-configuration changes.
- Recovery tests cover fresh stock/tax, no repeated welcome discount, one-time
  payment commands, idempotent preparation/settlement, retry after confirmed
  expiry, foreign/unverified customers, unresolved original charges, no automatic
  resume, future cancellation during payment, CSRF and owned account routes.
- Eight subscription tests cover initial activation, saved-card
  authorization binding, explicit consent, calendar scheduling, idempotent changes,
  pause/resume, customer isolation, flavour/address restrictions and cancellation
  during an in-flight cycle. Storefront subscription purchase is now connected
  in sandbox mode; real provider acceptance and production operation are pending.
- Checkout route tests pass for guest checkout, duplicate submissions, cross-browser
  access rejection, CSRF, quote totals, pending/verified payment, cancellation,
  cart version conflicts and fresh-bag creation.
  Automated tests cover customer isolation, challenge expiry and
  replay, consent separation, mail retry, discount rounding and tax failures.
  Additional route tests cover mandatory recurring consent, independent mixed-bag
  quantities, subscription eligibility, accurate recurring price display and
  duplicate settlement activating only the recurring lines once.
- Checkout tests additionally cover duplicate commands, insufficient-stock
  rollback, quote expiry, lost creation responses, signed HTTP callbacks,
  duplicate payment events, simultaneous retries on separate SQLite sessions,
  rejected total/currency/site mismatches and late
  payment after local timeout. Payment responses are mocked; they do not replace
  real Stripe sandbox acceptance.
- Hosted handoff tests cover durable-before-network ordering, stable provider
  keys, replay after timeouts, modified customer data, invalid URLs, tax mismatch,
  cross-customer rejection, idempotency-window expiry and safe cancellation/recovery.
- Renewal tests cover stock/price/configuration gating, no repeat first-order
  discount, lost provider responses, delayed success, duplicate/out-of-order
  webhooks, tax-posting outages, confirmed cancellation before release and
  separation from ordinary checkout recovery. The Stripe adapter's HTTP 402
  handling is checked with a mock transport; no real saved card has been charged.
- Ruff, Python compile checks and `git diff --check` pass.
- SQLite migrations pass fresh upgrade, Phase 2 downgrade/re-upgrade and
  Alembic schema-drift validation. PostgreSQL migration validation is pending.
  Transactional-mail migration `20260921_0010` additionally passes a fresh
  upgrade, downgrade to `0009`, re-upgrade and schema-drift check.
  Recovery migration `20260921_0011` passes the same checks against `0010`.
  Cart-discount migration `20260921_0012` passes fresh upgrade, downgrade to
  `0011`, re-upgrade and schema-drift checks.
- Chrome desktop/mobile journeys pass for newsletter confirmation, account
  login, owned order history, merchant tracking, unsubscribe and logout, with
  no browser errors or horizontal overflow. Screenshots are under
  `output/playwright/phase2-customer-flows/`; the reusable local-only journey is
  `scripts/verify_customer_browser.py`.
- A second Chrome journey covers product → bag → US delivery → review → hosted
  payment handoff → pending/confirmed status → fresh bag. Screenshots are under
  `output/playwright/phase2-checkout/`; the reusable script is
  `scripts/verify_store_checkout_browser.py`. It replaces Stripe only inside a
  guarded local test process and blocks real provider navigation. The displayed
  $10 shipping and $2.05 tax are fixtures, not approved merchant settings or rates.
- `scripts/verify_subscription_browser.py` exercises authenticated skip,
  pause/resume, frequency, flavour, address, simulated card setup and cancellation.
  It also drives a confirmed failed renewal through a new one-time payment,
  verifies settlement and confirms the contract stays paused until explicitly
  resumed. Recovery review captures are `desktop-delivery-recovery.png` and
  `mobile-delivery-recovery.png` in the same screenshot directory.
  Desktop/mobile screenshots are under `output/playwright/phase2-subscriptions/`.
  There are no browser errors or horizontal overflow; provider and card data are
  local fixtures, not real Stripe verification. Visual review also corrected
  spacing between adjacent action forms.
- `scripts/verify_store_checkout_browser.py --subscription` completes the public
  product → recurring bag → explicit consent → review → hosted fixture payment →
  active contract journey. Desktop/mobile captures are under
  `output/playwright/phase2-subscription-checkout/`. The merchant settings captures
  have also been refreshed with the current sandbox-readiness copy. These are
  local fixtures, not proof of real Stripe payments or approved shipping/tax rates.
- The checkout browser verifier now also exercises add-to-bag drawer opening,
  quantity/header-count updates, saved discount entry, keyboard dismissal and
  focus restoration. One-time and subscription journeys pass; the one-time run
  additionally verifies product → cart → checkout with JavaScript disabled.
  Drawer captures are saved alongside the checkout captures. Screenshots wait
  for the drawer animation to finish and assert its full bounds remain visible.
- Tracking screenshots use explicitly labelled local fixture orders. No Stripe
  payment, production shipment or actual email delivery was performed by these
  browser checks. These changes have not been deployed.

### Required sandbox configuration

The current local environment has no Stripe secret key or webhook signing secret.
Set `STRIPE_SECRET_KEY` (test mode) and `STRIPE_WEBHOOK_SECRET` in the ignored
environment file or secret manager, never in chat or committed files. The global
keys are reserved for `FASTSHOP_STRIPE_PRIMARY_SITE` (default `h24you`). Other
sites require `FASTSHOP_STRIPE_<UPPERCASE_SITE_ID>_SECRET_KEY` and the matching
`WEBHOOK_SECRET`, preventing another merchant from inheriting that account.

Configure Stripe Tax's origin/registrations and review the product tax codes;
FastShop does not register the merchant or assume tax collection in all states.
Supply the actual warehouse street address and standard US shipping fee before
end-to-end sandbox acceptance. Import duties are not represented as sales tax;
the DDP/DAP and customs policy remains a separate merchant decision.

Provider references:
[Stripe tax calculations](https://docs.stripe.com/api/tax/calculations/create),
[Checkout parameters](https://docs.stripe.com/api/checkout/sessions/create),
[webhook signature verification](https://docs.stripe.com/webhooks/signature).

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

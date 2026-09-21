# H24YOU payment-method acceptance

Audit date: 2026-09-21. This is an implementation/readiness audit, not evidence
that a merchant account or a wallet payment has been verified. No real provider
request, account-setting change or payment was made for this audit.

## What the current code supports

| Method | Code evidence | Remaining acceptance |
| --- | --- | --- |
| Cards | Hosted payment-mode Checkout; authoritative paid-state reconciliation; saved-card consent for subscriptions | Real test-card success, decline, authentication and signed callback checks |
| Apple Pay | Uses the same hosted card path, not a separate fake wallet button | Merchant settings, compatible wallet/device, initial purchase and saved-method renewal evidence |
| Google Pay | Customer is created with the reviewed US shipping address before being supplied to Checkout; automatic tax stays enabled | Enablement, compatible wallet/browser, matching tax totals and successful saved-method renewal |
| PayPal | **Not implemented/enabled**: the Checkout allowlist currently contains only `card` | Merchant-account arrangement, provider eligibility/activation and an explicitly supported one-time/recurring integration |

Stripe says hosted Checkout requires no separate wallet integration code and
processes Apple Pay/Google Pay as card payments. Wallet display depends on
settings and eligibility. [Stripe hosted saved-card documentation](https://docs.stripe.com/payments/save-and-reuse-cards-only?locale=en-GB&platform=web).

For Google Pay with automatic tax, Stripe requires a collected shipping address
or an existing customer's saved shipping address. The latter matches FastShop's
immutable customer-then-session command. Browser support also affects Apple Pay
display. [Stripe Checkout tax documentation](https://docs.stripe.com/payments/checkout/taxes?locale=fr-CA&payment-ui=embedded-components).

The wire-level tests check the actual encoded Customer and Checkout requests,
their customer binding, the US tax destination and off-session setup for initial
subscriptions. They do **not** prove a wallet button appears or a charge succeeds.
The UI must keep payment methods labelled planned until those claims are verified.

## PayPal boundary

Stripe lists Estonia among supported PayPal business locations and USD among
supported currencies. Recurring PayPal payments may require approval. Its Connect
documentation excludes platforms that onboard merchants to accept payments
directly, such as Shopify; eligible marketplaces have different conditions.
[Stripe PayPal documentation](https://docs.stripe.com/payments/paypal).

FastShop currently accepts separately scoped merchant Stripe credentials; it
does not implement Connect onboarding. Do not assume this proves platform-wide
PayPal eligibility, infer account country from warehouse location, or reuse
another merchant's payment account. Confirm the H24YOU merchant's own account
and the intended platform onboarding arrangement before adding PayPal to the
allowlist. Native card-only renewal commands must not silently receive a PayPal
payment method. Selecting a different PayPal architecture requires a reviewed
provider boundary and recovery tests, not just another footer icon.

## Acceptance record still needed

1. Securely configure the intended merchant's Stripe test key and the webhook
   secret for `/api/v1/commerce/<site_id>/stripe-webhook`. Neither was present at
   the last configuration-presence check. Never paste secrets into chat or Git.
2. Confirm the actual EU origin address, fee below the proposed $75 threshold,
   product tax codes and collection settings. Test state/ZIP-specific amounts.
3. Confirm wallet availability in the merchant dashboard; test on eligible
   Apple Pay and Google Pay devices/browsers against the real hosted page.
4. Record provider evidence for one-time payments, saved authorization, renewal,
   authentication-required recovery and duplicate webhook delivery. Use private
   event/order references, not card details or credentials.
5. Confirm PayPal's scope and onboarding arrangement separately. Do not claim
   acceptance based on a footer label or a mocked Checkout response.

Until these checks pass, payment-method acceptance remains incomplete. See the
[sandbox operations guide](PHASE2_SANDBOX_OPERATIONS.md) for the worker and
configuration prerequisites. No worker scheduling or deployment is authorized
by this audit alone.

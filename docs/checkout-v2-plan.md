# One-page checkout (Shopify-style) plan

Decision (store owner, 2026-10-29): card fields are entered inline on our checkout page, like
Shopify. Implementation choice: Stripe Checkout Sessions with `ui_mode: "custom"` (API version
`2025-03-31.basil`, already pinned in `app/integrations/stripe_commerce.py`; Stripe has since
renamed the mode `elements`). Card data goes straight from Stripe.js to Stripe; FastShop never
sees it. The existing `checkout.session.*` webhooks, `matches_quote` checks, saved-card
subscription renewals, refunds and reconciliation keep working because the object is still a
Checkout Session.

## Flow

1. `GET /checkout`: one page. Left column has contact, delivery address, shipping method and payment.
   The right column holds the sticky order summary with a discount or gift card box and totals.
2. Address entered: the browser posts the address to `POST /checkout/quote` (JSON, CSRF). The
   server prepares or re-prepares the checkout attempt and quote (`checkout_services.prepare`,
   idempotent per request fingerprint) and returns rates, tax and totals for the summary.
3. Shipping method chosen: same endpoint, with the rate id.
4. Payment section: the server creates the Checkout Session with `ui_mode=custom` and
   `return_url`, then returns its `client_secret`. Stripe.js (`https://js.stripe.com/basil/stripe.js`,
   never self-hosted) runs `initCheckout`, mounts the Payment Element and confirms on "Pay now".
5. Return: `/checkout/{attempt}` verifies via the existing reconcile path. A return alone is not
   proof of payment.

Sandbox mode with no Stripe keys falls back to the existing review and hand-off path.

## Layout and copy

Theme tokens (logo, fonts, colours, radius, button style) apply. All checkout copy is merchant
editable through checkout settings. No invented upsells, shipping protection, rewards or promo
banners.

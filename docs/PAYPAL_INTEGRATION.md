# PayPal integration

FastShop can offer PayPal at checkout in two ways. Secrets are always set as deployment
environment variables (through Coolify/FastDevOps) and never stored in the application
database — the admin PayPal page (`/admin/integrations/paypal`) only reports whether each
value is present, never the value itself.

## Path 1 — PayPal via Stripe (recommended today)

Stripe Checkout can present PayPal alongside cards and wallets. No PayPal keys are held by
FastShop.

1. Enable PayPal on your Stripe account (Dashboard → Settings → Payment methods).
2. In the store's shared/commerce settings, turn on **"Offer PayPal at one-time checkout."**
3. PayPal then appears in the hosted Stripe Checkout for one-time orders. Subscriptions stay
   card-only so saved-card renewals work.

This is implemented today via `checkout_payments.payment_methods_for` (adds `paypal` to the
Stripe session's `payment_method_types` for non-recurring orders).

## Path 2 — direct PayPal REST (provider-gated seam)

For a direct PayPal integration, store REST credentials as environment variables. Readiness
is surfaced in the admin PayPal page; the direct checkout flow itself is a seam pending
provider acceptance.

1. In the [PayPal Developer Dashboard](https://developer.paypal.com/), create a REST app
   (start in **Sandbox**).
2. Copy the app's **Client ID** and **Secret**.
3. Set these environment variables in Coolify (replace `{SITE_ID}` with the store id shown on
   its admin page), then redeploy:

   ```
   FASTSHOP_PAYPAL_{SITE_ID}_CLIENT_ID = <client id>
   FASTSHOP_PAYPAL_{SITE_ID}_SECRET    = <secret>
   FASTSHOP_PAYPAL_{SITE_ID}_MODE      = sandbox   # or: live
   ```

   The primary store may instead use the unprefixed globals `PAYPAL_CLIENT_ID`,
   `PAYPAL_SECRET`, `PAYPAL_MODE` (mirrors Stripe's `FASTSHOP_PAYPAL_PRIMARY_SITE`, default
   `h24you`).
4. Confirm the readiness table on `/admin/integrations/paypal` shows the store as **Ready**.

Credentials are resolved by `app/integrations/paypal.py` (`credentials`, `readiness`). Keep
`MODE=sandbox` until real provider acceptance is complete; do not enable live payments before
the launch gates in `PHASE2_ACCEPTANCE_STATUS.md` are met.

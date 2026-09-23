# WooCommerce API scaffolding and Stripe setup

## Scope and architecture

Reviewed `data/feature-requests/h24you/h24you_website_build_brief WooCommerce.md`.
This request adds a connector boundary; it does not migrate FastShop into WordPress.
The new brief requires native WooCommerce cart/checkout/My Account, a maintained
subscription plugin, private staging, replaceable WordPress media, backups/security,
US-style legal drafts, and a plugin/cost inventory. Those WordPress deliverables
are **not** implemented by an API stub. Its reference to a US Ltd also differs from
the earlier Estonian legal entity: confirm the actual merchant entity before onboarding.

Choose one billing owner per storefront before enabling synchronization:

- **FastShop checkout:** existing Stripe Tax, hosted Checkout, signed webhook
  reconciliation, card updates and recurring-payment services own payment state.
- **WooCommerce checkout:** WordPress and its selected gateway/subscription plugins
  own checkout, customers and recurring charges. FastShop must not independently
  renew the same subscriptions or mirror an order by charging again.

The Woo adapter uses the current [WooCommerce REST API, `wc/v3`](https://developer.woocommerce.com/docs/apis/rest-api/v3/).
It is not the browser Store API or a replacement for native checkout. Plugin choices,
prices, Woo hosting, tax-service costs and migration approval remain outstanding;
no paid extension is installed or purchased.

## Implemented now

| Area | Capability | Limit |
|---|---|---|
| Woo API | Opt-in per-site HTTPS Basic Auth; products, orders, customers, coupons, shipping zones, tax-rate reads; order lookup; explicit pagination | Internal adapter only; no merchant UI or automatic import/sync |
| Woo write stubs | Named order, coupon and subscription operations | Always raise a clear not-implemented error; never silently succeed or write remotely |
| Woo webhooks | Raw-body HMAC verification helper | No public ingestion endpoint; persistent delivery deduplication and tenant mappings required first |
| Money mapping | Exact USD decimal-string to integer-cent conversion | Rejects fractional cents, floats, negatives and scientific notation |
| Stripe | Existing sandbox checkout/tax/renewal/card-update/webhook flows retained | No live activation; real-provider acceptance still required |
| Stripe resilience | Require stable idempotency keys for writes; one bounded transport-failure retry with identical body/key | No automatic HTTP-error retries or new payment commands |
| Readiness | Operator command reads provider state and prints only sanitized readiness fields | Does not configure providers, test webhook delivery, validate registrations or create payments |

Credentials are not accepted through chat or API query strings. Woo has no global
credential fallback. The adapter checks tenant identity; callers must obtain that
identity from an authenticated membership, not an untrusted request parameter.
Read responses may contain customer data and must not be logged or exposed across sites.

Woo origins come only from deployment-managed environment variables, not merchant
form input. URLs must be HTTPS roots; redirects, URL credentials and obvious private
origins are rejected. DNS-based private targets are not comprehensively blocked;
keep origin configuration operator-only and use network egress controls. Supporting
merchant-entered origins later requires DNS/IP pinning and additional SSRF controls.

## WooCommerce keys later

1. Prepare the approved private WordPress staging installation and choose plugins.
2. Create a dedicated **read-only** Woo REST API key for that store. Do not use
   WordPress administrator passwords or keys belonging to another merchant.
3. In the deployment secret manager or ignored local environment, configure:

   ```text
   FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_ENABLED=true
   FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_URL=https://your-staging-domain.example
   FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_CONSUMER_KEY=<secret>
   FASTSHOP_WOOCOMMERCE_<UPPERCASE_SITE_ID>_CONSUMER_SECRET=<secret>
   ```

   Subdirectory WordPress installations are not supported by this initial adapter.
   Do not register Woo webhooks yet: only a verification helper exists.
4. From the intended application environment, run:

   ```sh
   python -m scripts.check_commerce_providers --provider woocommerce --site-id SITE_ID --tenant-id TENANT_ID
   ```

5. Review sanitized connection status. A successful products read does not verify
   write permissions, subscription plugins, native checkout, tax or synchronization.

## Stripe keys later: merchant checklist

1. Confirm the legal entity, actual EU dispatch address, US-only destination policy,
   USD prices, tablet subscription eligibility, shipping fee and free-shipping threshold.
2. Use the intended merchant's Stripe **sandbox/test-mode** account. Obtain a test
   secret key, never a live key for this release. Keep keys out of chat and Git.
3. Set site-specific `FASTSHOP_STRIPE_<UPPERCASE_SITE_ID>_SECRET_KEY` and
   `FASTSHOP_STRIPE_<UPPERCASE_SITE_ID>_WEBHOOK_SECRET` in the secret manager.
   The existing `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` fallback is reserved
   for `FASTSHOP_STRIPE_PRIMARY_SITE` (default `h24you`), not shared among tenants.
4. Create the test webhook endpoint on the platform HTTPS host:
   `/api/v1/commerce/SITE_ID/stripe-webhook`. Use its signing secret and the
   existing sandbox operations/event handling instructions. A configured secret
   alone does not prove webhook delivery or correct event subscriptions.
   Configure the checkout session events `completed`, `expired`,
   `async_payment_succeeded`, `async_payment_failed`, and payment intent events
   `succeeded`, `payment_failed`, `processing`, `canceled`, `requires_action`
   (with their `checkout.session.` / `payment_intent.` prefixes). The integration
   currently pins Stripe API version `2025-03-31.basil`; verify endpoint version
   compatibility rather than silently changing the API version during setup.
5. Configure Stripe Tax business details, registrations and product classifications
   with the merchant; active Tax settings alone do not establish tax correctness.
6. Run the read-only check:

   ```sh
   python -m scripts.check_commerce_providers --provider stripe --site-id SITE_ID --tenant-id TENANT_ID
   ```

   It retrieves account status and [Stripe Tax settings](https://docs.stripe.com/api/tax/settings/retrieve).
   Restricted test keys need permission for those reads. Output always leaves
   `provider_acceptance_complete` false; account connectivity is not payment acceptance.
7. Complete merchant review and explicitly select **Stripe sandbox** in Commerce.
   Test state/ZIP tax cases, taxable/exempt outcomes as applicable, shipping threshold,
   stacked discounts, success/decline/authentication, webhook replay and exact totals.
8. Test customer email login, payment/address changes, skip/pause/flavour/frequency,
   cancellation and a due renewal. Configure workers only in the approved environment.
9. Verify wallet support separately on eligible devices/accounts. PayPal is not
   implemented by this adapter. Do not advertise unsupported payment methods.
10. Record acceptance before a separate live-mode implementation/release decision.
    Supplying a live key cannot bypass the current sandbox safeguards.

See [sandbox operations](PHASE2_SANDBOX_OPERATIONS.md) for workers and provider
acceptance cases. Its historical deployment statement is superseded by
[production verification](PRODUCTION_GUIDE_VERIFICATION.md); provider acceptance remains open.
Stripe retries preserve the original [idempotency key](https://docs.stripe.com/api/idempotent_requests)
and durable command. An unknown outcome is not a failed payment and must not be
reissued as a new order.

## Next connector implementation gates

- Decide authoritative catalog/order/customer ownership and field mappings.
- Persist per-tenant/site remote IDs, synchronization cursors and audit history.
- Add durable webhook delivery deduplication before exposing an ingestion route.
- Select subscription/tracking/marketing plugins before promising their API behavior.
- Implement write outbox/reconciliation before Woo order or coupon writes; do not
  assume Woo supports Stripe-style idempotency headers.
- Keep payment data on provider-hosted forms; never mirror raw card details.

No schema or CSS/HTML changes are required for this backend-only scaffolding.
FastShop branding and existing H24YOU storefront behavior remain unchanged.

## Local verification — 21 September 2026

- Full regression run: **239 passed** on isolated SQLite; one existing
  Starlette/AnyIO deprecation warning. Three additional boundary cases were added
  after that run started; the final provider test file separately passed **30 tests**.
- Focused existing commerce/provider run: **58 passed**.
- Ruff, Python compilation and `git diff --check`: passed.
- Fresh SQLite Alembic upgrade to `20260921_0014` and schema check: passed,
  no pending model changes. PostgreSQL was not exercised.
- Readiness command help/argument loading: checked. No real provider keys used,
  no provider acceptance claimed, no deployment performed.
- No UI changes, so no new browser screenshots were required.

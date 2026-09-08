# FastShop migration and delivery plan

Status: Phase 1 vertical slice deployed
Date: 2026-09-08  
Target repository: `predictivelabsai/FastShop`  
Target production URL: `https://shop.fastsme.com`

## Delivery update

The Phase 1 vertical slice is live. It includes the storefront purchase flow,
merchant workspace, Google SSO, per-page AI surfaces, isolated PostgreSQL
schema, Coolify deployment, and token-gated FastERP ingress described below.
The remaining parity items in later phases deepen these foundations rather than
requiring a framework rewrite.

## Outcome

Build FastShop as a Python-first, server-rendered FastHTML commerce platform. It
will preserve the useful storefront behavior of Frappe Webshop, adopt the robust
commerce boundaries found in Saleor, fit the FastSME PostgreSQL/Coolify operating
model, and integrate with FastERP without sharing ownership of tables.

The recommended result is a modular monolith:

- FastHTML + HTMX storefront and merchant console in one ASGI deployment;
- a mounted FastAPI `/api` surface with OpenAPI documentation;
- PostgreSQL as the production source of truth in an isolated `fast_shop` schema;
- Alembic migrations and SQLAlchemy 2 repositories;
- an application service layer shared by HTML routes, API routes, jobs, and AI tools;
- transactional outbox jobs for FastERP, payment, email, and webhook delivery;
- minimal JavaScript limited to payment SDKs, image interactions, and streamed chat.

This is a behavioral migration rather than a framework port. Frappe-specific
DocTypes, hooks, bench commands, ERPNext events, Redisearch code, and Jinja
templates should not be copied into the runtime.

## Evidence reviewed

### Frappe Webshop

- Repository: <https://github.com/frappe/webshop>
- Reviewed branch/commit: `develop` at
  `1a5239d4482800b8fe1cb7d88c4ea0d9e837a03e`
- Size at review: 180 tracked files.
- License: GPL-3.0.
- Runtime dependencies: Frappe, ERPNext, and the Frappe payments app.
- Main reusable behavior: product publishing, variants, category and attribute
  filters, price/stock display, cart, address selection, checkout or RFQ,
  coupons, wishlists, reviews, recommendations, product inquiry, search, and
  merchant-configurable storefront settings.

Frappe is the closest reference for the desired end-to-end SMB workflow. Its
source cannot be deeply copied into the current MIT repository without making
FastShop a GPL-derived work. The default recommendation is to reproduce its
documented behavior and route journeys with an independent FastHTML
implementation.

### Saleor

- Repository: <https://github.com/saleor/saleor>
- Reviewed stable release: `3.23.31` at
  `a1ab3a23a3f5711bb74abb3a2972cf28454cb59e`
- Size at review: 4,725 tracked files, 58 core Django model classes, and 1,745
  Python files beneath test directories.
- License: BSD-3-Clause.
- Main reusable architecture: channels, product types/attributes, variants,
  channel listings, collections, warehouses and reservations, checkout,
  promotions/vouchers/gift cards, orders/fulfilments, provider-neutral payment
  transactions, translations, content/menu models, apps, webhooks, permissions,
  idempotency, and concurrency controls.

Saleor is the stronger domain reference, but copying its Django/GraphQL
implementation wholesale would replace rather than support the FastHTML goal.
FastShop should adapt its domain boundaries and invariants behind a smaller
service layer. Any BSD-derived source must retain the required Saleor copyright
and license notice.

## Product scope and parity map

| Capability | Frappe reference | Saleor reference | FastShop target |
| --- | --- | --- | --- |
| Product publishing | Website Item | Product + channel listing | Product, variant, media, SEO, publication windows |
| Variants | Item attributes + cache | Product type, attributes, variants | Typed attributes and deterministic variant selection |
| Navigation | Item groups/templates | Menus, categories, collections | Menus, nested categories, curated collections |
| Search/filtering | field/attribute filters, Redisearch | GraphQL filters | PostgreSQL full-text/trigram search and faceted HTMX filters |
| Pricing | ERP price list | channel listings | Channel/currency price lists with tax display policy |
| Inventory | ERP warehouse stock | warehouses, stock, reservations | Multi-warehouse available-to-sell with reservation expiry |
| Cart | quotation-backed cart | checkout + checkout lines | Anonymous/account cart, merge on login, server totals |
| Checkout | order or RFQ | checkout/delivery/payment | Guest/account checkout, shipping, taxes, payment, RFQ mode |
| Promotions | coupon | promotion rules/vouchers | Vouchers, catalog/order/shipping rules, usage limits |
| Customer extras | wishlist, reviews, inquiry | account history | Wishlist, verified reviews, inquiry, recommendations |
| Orders | Sales Order | order/events/fulfilment | Immutable line snapshots, events, fulfilment, returns/refunds |
| Payments | Frappe payment request | transaction/event ledger | Provider-neutral ledger; Stripe adapter first |
| International | price list/territory | channels/translations | Channels, currencies, countries, locale-ready content |
| Extensibility | Frappe hooks | apps/webhooks | versioned REST API, signed webhooks, outbox, connector interface |
| ERP hand-off | native ERPNext coupling | external app/webhook | explicit FastERP adapter with idempotent sync and reconciliation |
| AI | none | agent-oriented API | shopper assistant and authenticated merchant copilot |

Full functional depth is a sequence, not one unreviewable code drop. Phase 1
ships a coherent purchase flow; later phases deepen the same domain model rather
than replacing it.

## Proposed repository structure

```text
FastShop/
  app/
    main.py                  ASGI app, lifespan, mounted API
    config.py                typed environment settings
    auth/                    sessions, Google OIDC, RBAC, CSRF
    db/                      engine, SQLAlchemy models, repositories
    domain/                  money, pricing, inventory, order invariants
    services/                use cases and transaction boundaries
    storefront/              FastHTML pages and components
    merchant/                FastHTML administration pages
    api/                     FastAPI v1 routers and OpenAPI schemas
    integrations/
      payments/              provider-neutral interface + Stripe
      fasterp/               API client, mappings, reconciliation
      email/                 Postmark adapter
      webhooks/              signing, delivery, retries
    ai/                      tools, grounding, policy, streaming chat
    jobs/                    outbox worker and scheduled maintenance
  migrations/               append-only Alembic migrations
  static/                    CSS, small JS modules, local demo assets
  tests/                     unit, integration, contract, browser smoke tests
  scripts/                   seed, screenshots, Coolify launcher, maintenance
  docs/                      architecture, API, ERP mapping, runbooks
  Dockerfile
  pyproject.toml
```

## Data model

Every tenant-owned table includes `tenant_id`; channel-owned records also
include `channel_id`. Monetary values use integer minor units plus ISO currency,
never binary floats. Timestamps are timezone-aware UTC. Public identifiers are
opaque UUID/ULID values; human order numbers are separate.

### Identity and tenancy

- `tenants`, `users`, `memberships`, `roles`, `customer_profiles`, `addresses`
- `channels`, `channel_countries`, `currencies`, `locales`, `site_settings`

### Catalog and content

- `product_types`, `products`, `product_variants`, `product_media`
- `attributes`, `attribute_values`, product/variant attribute assignments
- `categories` with a tree path, `collections`, `collection_products`
- `channel_product_listings`, `channel_variant_listings`, `price_lists`, `prices`
- `menus`, `menu_items`, `pages`, `translations`, `seo_records`
- `reviews`, `review_votes`, `wishlists`, `wishlist_items`, `recommendations`

### Stock and fulfilment

- `warehouses`, `stocks`, `stock_movements`
- `reservations`, `allocations`, `preorders`
- `shipping_zones`, `shipping_methods`, price/weight/postcode rules

Available-to-sell is calculated as on-hand minus allocated minus unexpired
reservations. Stock reservation, checkout completion, and fulfilment mutations
use row locks and stable lock ordering.

### Cart, promotion, and checkout

- `carts`, `cart_lines`, `checkouts`, `checkout_deliveries`
- `promotions`, `promotion_rules`, `vouchers`, `voucher_codes`, `voucher_redemptions`
- `tax_classes`, `tax_rates`, `tax_configurations`

All totals are calculated on the server and stored as an auditable breakdown.
Checkout commands accept idempotency keys. A completed checkout is immutable and
creates an order from product, price, tax, address, and discount snapshots.

### Orders and payments

- `orders`, `order_lines`, `order_events`, `order_notes`
- `fulfilments`, `fulfilment_lines`, `returns`, `refunds`
- `payment_transactions`, `payment_events`, `payment_methods`
- `invoices` as an ERP reference/link rather than a duplicate accounting ledger

### Integrations and AI

- `integration_connections`, `external_mappings`, `sync_cursors`
- `outbox_events`, `webhook_subscriptions`, `webhook_deliveries`
- `ai_threads`, `ai_messages`, `ai_tool_runs`, `ai_feedback`
- `audit_events` for privileged merchant and AI actions

## Route and page plan

### Storefront

- `/` storefront home with hero, featured products, categories, collections,
  trust content, locale/channel selector, search, account, and cart.
- `/products`, `/products/{slug}`, `/categories`, `/categories/{slug}`,
  `/collections/{slug}`, `/search`.
- `/cart`, `/checkout`, `/checkout/success/{order_token}`.
- `/login`, `/register`, `/forgot-password` and Google OIDC.
- `/account`, `/account/orders`, `/account/orders/{id}`,
  `/account/addresses`, `/wishlist`.
- `/pages/{slug}`, `/contact`, legal pages, sitemap, robots, and `llms.txt`.

HTMX updates product filters, variant availability, cart lines, promotion
messages, shipping quotes, and checkout summaries while preserving functional
links/forms when JavaScript is unavailable.

### Merchant console

- `/admin` KPIs, alerts, recent orders, stock exceptions, and AI brief.
- `/admin/products`, categories, collections, attributes, media, and imports.
- `/admin/inventory`, warehouses, reservations, and movements.
- `/admin/orders`, fulfilments, returns, refunds, draft orders, and RFQs.
- `/admin/customers`, reviews, promotions, vouchers, gift cards.
- `/admin/channels`, shipping, taxes, payments, content, menus, users, and roles.
- `/admin/integrations/fasterp`, sync status, mappings, errors, replay, and
  reconciliation.
- `/developers`, `/api/docs`, `/api/openapi.json`.

### AI surfaces

The top-left product menu includes an **AI Assistant** entry on every page.
Every storefront page has a compact assistant launcher; every authenticated
merchant page has a persistent, collapsible copilot rail based on the FastERP
interaction pattern.

Shopper tools are read-only: search catalog, explain variants, compare products,
check availability, explain delivery/returns, and prepare cart changes that the
user explicitly confirms. Merchant tools can inspect catalog, orders, stock,
promotions, and integration health. All writes require a visible confirmation,
normal RBAC permission, CSRF protection, and an audit record. The model never
receives payment details or unrestricted database access.

Grounding uses typed tool calls into the application service layer. Slash
commands work without a model key for deterministic operations such as
`/orders`, `/stock`, `/revenue`, `/abandoned`, `/promos`, and `/sync`.

## FastERP integration boundary

The production PostgreSQL connection URL can be sourced from the existing
ignored `FastERP/.env`; its value must never be committed or printed. FastShop
uses the same database server but a separate `fast_shop` schema and separate
migrations.

Recommended ownership:

- FastShop owns online catalog presentation, channel prices, carts, checkout,
  promotions, payment transactions, storefront customers, and order experience.
- FastERP owns accounting, invoices, payments posted to ledgers, purchasing,
  authoritative warehouse movements, and financial reporting.
- FastShop sends confirmed/paid/cancelled/fulfilled order events to FastERP.
- FastERP sends item, stock, customer-account, invoice, and fulfilment updates.
- `external_mappings` records both IDs and versions; no connector writes directly
  into the other product's tables.

The current FastERP public API exposes customers, invoices, accounts, expenses,
projects, and reports, but not the item/stock/order mutations FastShop needs.
Integration work therefore includes a small FastERP change set:

- token-scoped `items`, `stock`, and `sales-orders` reads;
- idempotent create/update commands for commerce orders and customer mappings;
- signed outbound webhooks or cursor-based change feeds;
- company scoping on every request;
- connector contract tests and a reconciliation endpoint.

FastShop remains usable in standalone mode when `FASTERP_BASE_URL` is unset.

## API and event contract

FastShop follows the FastSME fleet convention: public, typed reads where safe;
token/OIDC-gated writes; structured errors; offset or cursor pagination;
committed OpenAPI snapshots; `/developers`; and `/api/v1/health`.

Initial resources:

- catalog: channels, products, variants, categories, collections, availability;
- customer: carts, checkouts, addresses, wishlists, orders;
- merchant: inventory, promotions, fulfilments, returns, integration jobs;
- webhooks: catalog, inventory, checkout, order, payment, fulfilment, and refund
  lifecycle events.

Every external command accepts an idempotency key. Webhook payloads are signed,
versioned, retried with bounded exponential backoff, and retained for replay.

## Delivery phases

### 0. Rename and guardrails

- Repoint `origin` to `predictivelabsai/FastShop`, rename product references, and
  keep `main` as the deployment branch.
- Record upstream commits and third-party notices.
- Decide GPL behavioral reimplementation versus GPL-derived source reuse.
- Add repository instructions, architecture decisions, threat model, and scope.

Exit: repository identity, license strategy, and chosen product mode are explicit.

### 1. Runtime and production foundation

- Scaffold FastHTML/FastAPI modular monolith, configuration, structured logging,
  PostgreSQL, Alembic, sessions, CSRF, RBAC, Google OIDC, and deterministic seed.
- Add Dockerfile listening on `0.0.0.0:5025`, `/healthz`, `/readyz`, and curl for
  the Coolify health wrapper.
- Add unit/test configuration and CI checks.

Exit: empty app boots locally and in Docker, migrations are repeatable, health
and auth paths pass.

### 2. Catalog, channels, stock, and merchant CRUD

- Implement types, attributes, products, variants, media, categories,
  collections, channels, currencies, price lists, warehouses, and stock.
- Add merchant list/detail/create/edit flows, publication controls, import/export,
  audit events, and responsive UI.

Exit: a merchant can build and publish a multi-variant catalog with correct
channel price and availability.

### 3. Storefront parity with Frappe

- Home, product listing/detail, category/collection, faceted search, variant
  selection, cart, wishlist, reviews, recommendations, and product inquiry.
- Add Frappe-equivalent display settings and guest policies.
- Add SEO metadata, JSON-LD, canonical links, sitemap, robots, and `llms.txt`.

Exit: complete browse-to-cart journey on desktop and mobile, including no-JS
form fallback for core actions.

### 4. Checkout, payments, orders, and fulfilment

- Address book, shipping methods, taxes, vouchers/promotions, guest/account
  checkout, RFQ mode, order creation, reservation expiry, fulfilment, return,
  cancellation, and refunds.
- Add provider-neutral payments and Stripe test-mode hosted checkout/webhooks.
- Add concurrency, idempotency, and money/property tests.

Exit: test payment creates exactly one correctly totalled order under retries and
concurrent stock pressure.

### 5. FastERP connector

- Extend the FastERP integration API, then build FastShop client, mappings,
  outbox delivery, inbound webhooks/change feed, replay, and reconciliation UI.
- Sync items/availability into FastShop and confirmed commerce orders/customers
  into FastERP; link invoices and fulfilment states back.

Exit: seeded order round-trip reconciles with no direct cross-schema writes and
can be safely replayed.

### 6. AI Assistant and page copilot

- Add the global top-left AI entry, storefront assistant, merchant right rail,
  thread history, streaming responses, deterministic slash commands, typed tools,
  confirmation gates, rate limits, and audit logs.
- Ground every page with its route, visible resource identifiers, tenant/channel,
  and user permissions.

Exit: every route exposes the correct AI surface; unauthorized or unconfirmed
writes are impossible; no provider key still yields useful slash commands.

### 7. Saleor-depth capabilities

- Deepen promotions, gift cards, multi-language content, preorder/reservations,
  multi-warehouse allocation, partial fulfilments/refunds, draft orders, content
  pages, menus, webhooks, import/export, and channel administration.
- Add a GraphQL compatibility adapter only if a concrete consumer needs it; REST
  and the service layer remain primary.

Exit: the agreed Saleor parity matrix passes feature-specific acceptance tests.

### 8. Visual QA, hardening, and Coolify release

- Run full tests, dependency/security checks, backup/restore rehearsal, load tests,
  accessibility checks, and browser console/network inspection.
- Capture Playwright screenshots for every public and merchant page at desktop
  and mobile sizes, plus collapsed/open AI states.
- Add `fastshop` to `FastDevOps/config/services.yaml`, validate/doctor, provision,
  sync environment names/values, create the push-only GitHub webhook, deploy, and
  verify the exact commit, TLS, health, static assets, and critical purchase flow.

Exit: `https://shop.fastsme.com` serves the intended commit with successful
checkout and ERP reconciliation smoke tests.

## Proposed FastDevOps/Coolify declaration

The control-plane repository currently has no FastShop entry. Proposed values:

```yaml
fastshop:
  description: FastHTML commerce storefront and merchant operations platform
  repo: predictivelabsai/FastShop
  local_dir: FastShop
  port: 5025
  domain: https://shop.fastsme.com
  health: {path: /healthz}
  database_schema: fast_shop
  env:
    sources: [FastERP]
    required:
      - DB_URL
      - FASTSHOP_SESSION_SECRET
      - FASTSHOP_ENCRYPTION_KEY
      - FASTSME_API_TOKEN
      - GOOGLE_CLIENT_ID
      - GOOGLE_CLIENT_SECRET
      - POSTMARK_API_TOKEN
      - XAI_API_KEY
    generate:
      - FASTSHOP_SESSION_SECRET
      - FASTSHOP_ENCRYPTION_KEY
      - FASTSME_API_TOKEN
    runtime:
      FASTSHOP_ENV: production
      FASTSHOP_HOST: 0.0.0.0
      FASTSHOP_PORT: "5025"
      FASTSHOP_PUBLIC_URL: https://shop.fastsme.com
      DB_SCHEMA: fast_shop
      GOOGLE_REDIRECT_URI: https://shop.fastsme.com/auth/google/callback
      MODEL_PROVIDER: xai
      FASTERP_BASE_URL: https://erp.fastsme.com
      FROM_EMAIL: info@fastsme.com
```

Payment, object-storage, and ERP connector secrets should be required only when
their adapters are enabled. Environment synchronization must preserve existing
secret values and copy the DB URL through the control plane without logging it.

## Verification and screenshot matrix

### Automated checks

- Ruff, type checking, migration validation, pytest, and `git diff --check`.
- Domain tests for rounding, inclusive/exclusive tax, promotions, reservations,
  allocation, partial fulfilment, return/refund, and order state transitions.
- API schema snapshot and backward compatibility checks.
- Contract tests against a fake FastERP plus a real seeded FastERP instance.
- Playwright smoke journeys for anonymous shopper, customer, merchant, and admin.
- Security tests for tenant isolation, RBAC, CSRF, webhook signatures, replay,
  idempotency, hostile AI prompts, and data leakage.

### Browser artifacts

Store under `output/playwright/`:

- reference: Frappe settings and Saleor storefront baselines;
- storefront desktop/mobile: home, list/filter, product/variant, cart, checkout,
  success, login, account, order, wishlist, and content page;
- merchant desktop/mobile: dashboard, products, variant, stock, orders,
  fulfilment, customers, promotions, channel, content, and FastERP sync;
- AI: launcher, top-left menu, shopper chat, copilot collapsed/open/expanded,
  confirmation prompt, and denied action;
- error/empty/loading states and accessibility-focused snapshots.

Each production screenshot run also records console errors, failed network
requests, page title, URL, target Git commit, and viewport.

## Release gates

- No secrets, `.env`, customer data, or payment data in commits or screenshots.
- No deployment until the repository commit is pushed and CI is green.
- No success claim until Coolify deploys that exact commit and `/healthz`,
  `/readyz`, storefront assets, checkout, `/developers`, and ERP sync pass on the
  production domain.
- Database backups and down-migration/forward-fix policy documented before live
  orders are accepted.
- Stripe remains in test mode until legal pages, tax/shipping settings, webhook
  validation, and an explicit live-payment approval are complete.

## Decisions required before Phase 1

1. License strategy: keep FastShop MIT and independently reproduce Frappe
   behavior (recommended), or permit a GPL-3.0 derivative with direct code reuse.
2. Product mode: multi-tenant/multi-channel SaaS (recommended for Saleor depth)
   or a single FastSME-owned storefront.
3. First live commerce profile: countries/currencies/tax model, shippable versus
   digital products, and whether Stripe test checkout or RFQ-only is the first
   release.
4. AI exposure: public shopper assistant plus authenticated merchant copilot
   (recommended with strict read-only public tools), or authenticated users only.
5. FastERP authority: recommended split ownership above, or make FastERP the
   authoritative catalog/stock master from day one.

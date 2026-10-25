# FastShop current merchant admin, backend and page editor

Audit date: 25 October 2026. Repository: `fastshop-lovable`; branch: `merchant-admin-v2`; inspected commit: `b06164959f00365581d6295663c0c75cb0f87219`. This describes checked-in source, not the deployed database, active credentials or browser-observed behaviour. Evidence uses repository-relative `file:line` anchors at this commit.

## 1. Complete merchant and adjacent operator admin route inventory

All explicit `/admin` route declarations are included below, including operator-only routes so their capabilities are not mistaken for merchant permissions. Methods are explicit decorator methods or FastHTML's GET/POST inference from handler names in `app/main.py`. Each behaviour column names its UI module, or inherits the shell convention below.

Permission key: **L** = legacy `admin_guard`, requiring active User and session role admin/merchant (`app/main.py:101`, `app/main.py:157`). Legacy query scope is `membership_tenants`, including every membership role; only a membership-free configured operator gets unrestricted scope (`app/main.py:166`). **AME** = active actor with tenant membership admin/merchant/editor via `content.owned_site`; **AM** = its `publish=True` permission restriction to admin/merchant, not a requirement for published site status (`app/site_routes.py:68`, `app/content.py:72`). **C** = shared CSRF validation. Every site POST below checks C, including menu JSON requests. Inline site components render within the injected `shell` closure and `platform_ui.platform_page` (`app/site_routes.py:75`, `app/site_routes.py:83`, `app/platform_ui.py:24`); exceptions such as JSON, downloads, redirects and storefront previews are named. Renderer functions for legacy pages are in `app/ui.py`.

### Dashboard/home and assistant

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin` | Paid revenue, orders, published product count, available stock and recent orders; `ui.dashboard` (`app/ui.py:456`) inside `ui.merchant_page` | L; `app/main.py:606` |
| GET | `/admin/assistant` | Permission-aware assistant instructions, commands `/orders`, `/stock`, `/sync`; `ui.panel` in merchant shell | L; `app/main.py:733` |
| GET | `/admin/sites` | Sites, roles, onboarding links, plan usage, generation and creation forms; inline `site_routes` (`app/site_routes.py:182`, `app/site_routes.py:240`) | Active actor; tenant membership AME query (`app/site_routes.py:190`) |
| POST | `/admin/sites` | Create site, optional sample merchant fields, choose classical/chat flow; redirect (`app/site_routes.py:390`) | Active actor plus site quota; `content.create_site` creates membership (`app/site_routes.py:395`, `app/content.py:204`) |
| GET | `/admin/sites/{site_id}` | Page list/create form, brand/shared settings, analytics ID, payment-method toggle, social links and approved claims; inline `site_routes` (`app/site_routes.py:415`, `app/site_routes.py:492`) | AME (`app/site_routes.py:420`) |

### Products/catalogue, inventory, categories and pricing

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/products` | Published catalogue table with category, price, availability and status, links to public product pages; `ui.admin_products` (`app/ui.py:487`) | L; `app/main.py:616` |
| GET | `/admin/inventory` | SKU, variant, on-hand, allocated and available table; `ui.admin_inventory` (`app/ui.py:492`) | L; `app/main.py:641` |
| GET | `/admin/sites/{site_id}/products` | All tenant products as cards; edit name, single image URL, variant name and per-channel USD minor-unit price; create product form; inline `site_catalog` (`app/site_catalog.py:94`, `app/site_catalog.py:100`) | AME (`app/site_catalog.py:99`) |
| POST | `/admin/sites/{site_id}/products` | Update existing name/image/variant names/prices or create product with comma-separated variant names plus draft product page (`app/site_catalog.py:123`, `app/site_catalog.py:147`, `app/site_catalog.py:162`) | AM (`app/site_catalog.py:130`) |

### Orders, fulfilment, refunds and delivery operations

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/orders` | All scoped orders table, `ui.panel` and `ui.order_table` (`app/ui.py:483`) | L; `app/main.py:626` |
| GET | `/admin/sites/{site_id}/orders` | Order/customer/fulfilment/payment/total/date table with fulfilment status dropdown and revenue/delivery links | AM; `app/order_management_routes.py:66`; shell at `app/order_management_routes.py:80` |
| GET | `/admin/sites/{site_id}/orders/{site_order_id}` | Customer/address, item/totals, fulfilment form, refund review form, Stripe resync and event timeline | AM; `app/order_management_routes.py:111`; shell at `app/order_management_routes.py:195` |
| POST | `/admin/sites/{site_id}/orders/{site_order_id}/fulfillment` | Expected-current-status guarded transition, carrier/tracking URL/number/note, commits then redirects | AM+C; `app/order_management_routes.py:222` |
| POST | `/admin/sites/{site_id}/orders/{site_order_id}/resync` | Re-pulls Stripe status for one order, redirects notice | Actor+C; downstream `_manager_site` enforces admin/merchant (`app/order_management.py:248`, `app/order_management.py:334`); `app/order_management_routes.py:248` |
| GET | `/admin/sites/{site_id}/orders/{site_order_id}/refund` | Validates minor-unit amount against captured available balance and 10 to 500 character reason; renders exact refund confirmation, checkbox and request key | AM; `app/order_management_routes.py:327`; shell at `app/order_management_routes.py:341` |
| POST | `/admin/sites/{site_id}/orders/{site_order_id}/refund` | Executes exact durable/idempotent refund, or reconciles persisted unresolved refund; redirects state | Actor+C, downstream manager scope (`app/order_management.py:448`, `app/order_management.py:460`); retry scope additionally explicit (`app/order_management_routes.py:377`); route `app/order_management_routes.py:366` |
| GET | `/admin/sites/{site_id}/deliveries` | Terminal/dead-letter/legacy failed outbound events, attempts, safe error and requeue button | AM; `app/order_management_routes.py:267`; shell at `app/order_management_routes.py:286` |
| POST | `/admin/sites/{site_id}/deliveries/{event_id}/requeue` | Resets one scoped outbound event for bounded retry, redirects notice | AM+C; `app/order_management_routes.py:311` |
| GET | `/admin/platform/deliveries` | Failed deliveries across explicitly administered sites | Actor, configured operator email plus tenant admin membership (`app/order_management_routes.py:481`); route `app/order_management_routes.py:477`; shell at `app/order_management_routes.py:499` |

### Customers

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/customers` | User records associated with tenant memberships or orders, labelled Customers and staff; inline table in `ui.merchant_page` | L; `app/main.py:742` |
| GET | `/admin/sites/{site_id}/customers` | Up to 100 customers with email/verification state, 50 queued emails with kind/status/attempts and 50 recent orders; injected `merchant_shell` | AME; explicit site/tenant scope at `app/customer_routes.py:277`; route `app/customer_routes.py:273`, renderer `app/customer_routes.py:285` |

### Discounts

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/promotions` | Voucher code/type/value/minimum/usage/status table; `ui.admin_promotions` (`app/ui.py:500`) | L; `app/main.py:651` |

### Subscriptions

No dedicated merchant `/admin` routes. Subscription administration is limited to product eligibility in Commerce; contract controls are shopper account routes (`app/commerce_routes.py:84`, `app/subscription_routes.py:59`, `app/subscription_routes.py:136`).

### Content: pages, blog, menus, media, snippets, reviews and placeholders

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/content` | Redirect to `/admin/sites`; no editor here | L; `app/main.py:760` |
| POST | `/admin/sites/{site_id}/pages` | Create page title/path/kind, record change, redirect to page editor (`app/site_routes.py:644`) | AME (`app/site_routes.py:651`) |
| GET | `/admin/sites/{site_id}/pages/{page_id}` | Structured field editor, section controls, article metadata, preview iframe, last 20 revisions; inline `site_routes` (`app/site_routes.py:705`, `app/site_routes.py:720`) | AME plus page id/site/tenant scope (`app/site_routes.py:709`, `app/content.py:84`) |
| POST | `/admin/sites/{site_id}/pages/{page_id}` | Save structured fields, patch/reorder/remove/add blocks; draft/publish/unpublish; revision and builder change recorded (`app/site_routes.py:746`, `app/site_routes.py:774`, `app/site_routes.py:802`) | AME to enter; `save_page` rechecks AM for publish/unpublish (`app/site_routes.py:753`, `app/content.py:118`) |
| POST | `/admin/sites/{site_id}/pages/{page_id}/restore` | Restore selected revision as new draft, version checked; redirect (`app/site_routes.py:823`) | AME, site lock and revision scoping (`app/site_routes.py:830`, `app/content.py:194`) |
| GET | `/admin/sites/{site_id}/preview/{page_id}` | Render private draft via `site_ui.storefront(preview=True)` (`app/site_routes.py:842`, `app/site_routes.py:849`) | AME plus scoped page (`app/site_routes.py:846`) |
| GET | `/admin/sites/{site_id}/reviews` | Review body/rating/title/author and approve/hide/delete forms; inline `site_routes` (`app/site_routes.py:590`, `app/site_routes.py:597`) | AME (`app/site_routes.py:594`) |
| POST | `/admin/sites/{site_id}/reviews/{review_id}` | Approve/hide/delete review then redirect (`app/site_routes.py:612`) | AM (`app/site_routes.py:619`) |
| GET | `/admin/sites/{site_id}/placeholders` | Derived placeholder and pending-item inventory; inline `site_routes` (`app/site_routes.py:627`, `app/site_routes.py:636`) | AME (`app/site_routes.py:631`) |
| GET | `/admin/sites/{site_id}/media` | Image upload, cards, URL copying, references, title/alt editing/delete; inline `site_media_routes` (`app/site_media_routes.py:29`, `app/site_media_routes.py:88`) | AME (`app/site_media_routes.py:33`) |
| POST | `/admin/sites/{site_id}/media` | Upload JPEG/PNG/WebP <=8 MB, require alt, optimise to WebP, store site blob (`app/site_media_routes.py:47`, `app/site_media_routes.py:59`) | AME (`app/site_media_routes.py:53`) |
| POST | `/admin/sites/{site_id}/media/{media_id}` | Save metadata/localised alt or delete through reference-aware helper; redirect (`app/site_media_routes.py:107`) | AME plus owned media (`app/site_media_routes.py:113`, `app/site_media_routes.py:116`) |
| GET | `/admin/sites/{site_id}/menus` | Menu forms, item targets and up/down/remove controls, publish; inline `site_menu_routes` (`app/site_menu_routes.py:33`, `app/site_menu_routes.py:78`) | AME (`app/site_menu_routes.py:37`) |
| POST | `/admin/sites/{site_id}/menus` | Create/import legacy menu through shared mutation helper (`app/site_menu_routes.py:161`) | AME, AM when action=publish (`app/site_menu_routes.py:96`) |
| POST | `/admin/sites/{site_id}/menus/{name}` | Add/edit/remove/up/down/publish named menu (`app/site_menu_routes.py:165`, `app/site_menu_routes.py:112`) | AME, AM for publish (`app/site_menu_routes.py:96`) |
| GET | `/admin/sites/{site_id}/menus.json` | JSON menu snapshots plus site version (`app/site_menu_routes.py:169`) | AME (`app/site_menu_routes.py:173`) |
| POST | `/admin/sites/{site_id}/menus/{name}/api` | JSON replace or publish menu; JSON result/version (`app/site_menu_routes.py:178`, `app/site_menu_routes.py:109`) | AME, AM for publish, session CSRF inside JSON body (`app/site_menu_routes.py:91`, `app/site_menu_routes.py:96`) |
| GET | `/admin/sites/{site_id}/snippets` | Head/pre-footer/foot markup textareas, enable switch and note; inline `site_snippet_routes` (`app/site_snippet_routes.py:22`, `app/site_snippet_routes.py:45`) | AM even for reading (`app/site_snippet_routes.py:26`) |
| POST | `/admin/sites/{site_id}/snippets/{placement}` | Save draft or publish validated trusted markup snapshot (`app/site_snippet_routes.py:83`, `app/site_snippet_routes.py:109`) | AM for both actions (`app/site_snippet_routes.py:93`) |

### Online store, theme/design and shared settings

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| POST | `/admin/sites/{site_id}/settings` | Save draft or publish shared settings; version check, snapshot and change set (`app/site_routes.py:526`) | AME for draft, AM for publish (`app/site_routes.py:533`) |
| POST | `/admin/sites/{site_id}/build/design` | Validate theme fields, apply theme draft operation; redirect (`app/site_builder_routes.py:243`) | AME via `lock_site` (`app/site_builder_routes.py:250`, `app/site_builder_services.py:64`) |

### Site builder, AI generation/refinement and onboarding

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| POST | `/admin/sites/generate` | Synchronous AI/guided brief generation into private site draft; quotas; redirect to site (`app/site_routes.py:349`) | Active actor, account site/AI quotas (`app/site_routes.py:355`, `app/site_routes.py:370`); creates own site, no existing membership required |
| GET | `/admin/sites/{site_id}/build` | Chat or design controls, page selection, desktop/mobile iframe preview, section targeting, proposal/refinement review, latest change undo/history; inline `site_builder_routes` (`app/site_builder_routes.py:43`, `app/site_builder_routes.py:214`) | AME (`app/site_builder_routes.py:48`); merchant proposal controls only AM (`app/site_builder_routes.py:68`) |
| POST | `/admin/sites/{site_id}/build/undo` | Undo latest current draft change with optimistic version (`app/site_builder_routes.py:258`) | AME through service site lock (`app/site_builder_services.py:213`, `app/site_builder_services.py:64`) |
| POST | `/admin/sites/{site_id}/build/message` | Begin persisted idempotent turn, collect history, call provider in threadpool, finish turn; redirect (`app/site_builder_routes.py:270`) | AME via `begin_turn` and finish (`app/site_builder_services.py:247`, `app/site_builder_services.py:250`, `app/site_builder_services.py:277`) |
| POST | `/admin/sites/{site_id}/build/cancel` | End pending turn so late provider result cannot mutate draft (`app/site_builder_routes.py:304`) | AME through `end_pending_turn` (`app/site_builder_services.py:390`) |
| POST | `/admin/sites/{site_id}/build/refinement` | Accept/reject individual/all proposed block edits (`app/site_builder_routes.py:316`) | AME (`app/site_builder_services.py:320`, `app/site_builder_services.py:322`) |
| POST | `/admin/sites/{site_id}/build/imagery` | Resolve missing generated imagery, preserve merchant image selections, record change (`app/site_builder_routes.py:332`) | AME via site lock (`app/site_builder_routes.py:339`, `app/site_builder_services.py:64`) |
| POST | `/admin/sites/{site_id}/build/review` | Approve/reject merchant/product/price proposal; approval requires explicit confirmation (`app/site_builder_routes.py:363`) | AM in service (`app/site_builder_reviews.py:110`) |
| GET | `/admin/onboarding/{site_id}` | Two-step brief wizard, resumable ready/generating/failed states; inline `_brief_form`, `_decision` and shell (`app/onboarding_routes.py:188`, `app/onboarding_routes.py:74`, `app/onboarding_routes.py:139`) | `onboarding.owned_state`, tenant Membership AM plus state/site checks (`app/onboarding_routes.py:195`, `app/onboarding.py:47`) |
| POST | `/admin/onboarding/{site_id}/brief` | Validate/save description, product context, design direction; ready state (`app/onboarding_routes.py:276`) | AM via owned_state (`app/onboarding_routes.py:291`, `app/onboarding.py:65`) |
| POST | `/admin/onboarding/{site_id}/skip` | Retain template and mark wizard skipped (`app/onboarding_routes.py:320`) | AM via owned_state (`app/onboarding_routes.py:328`, `app/onboarding.py:65`) |
| POST | `/admin/onboarding/{site_id}/generate` | Quota checked generation outside transaction, fingerprint and token guard, apply plan and record usage (`app/onboarding_routes.py:367`, `app/onboarding_routes.py:402`, `app/onboarding_routes.py:459`) | AM via owned_state, checked before and after generation (`app/onboarding_routes.py:375`, `app/onboarding_routes.py:443`, `app/onboarding.py:65`) |

### Settings: commerce, shipping, tax, payments, checkout, domains, go-live, users and billing

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/channels` | Channel currency/country/locale/tax display/status table; `ui.admin_channels` (`app/ui.py:508`) | L; `app/main.py:661` |
| GET | `/admin/sites/{site_id}/commerce` | EU origin, US destinations, USD shipping fee/threshold, per-product tax classification and monthly subscription eligibility; Stripe readiness and tax quote form | AME; `app/commerce_routes.py:55`; shell `app/commerce_routes.py:64` |
| POST | `/admin/sites/{site_id}/commerce` | Saves version-guarded settings; no direct checkout-mode changes, validates reviewed samples and tax codes | AM+C, site lock/version; `app/commerce_routes.py:99`, `app/commerce_routes.py:106` |
| POST | `/admin/sites/{site_id}/commerce/quote` | Generates sandbox tax quotation for selected variant/quantity, monthly eligibility, first-order preview and full US address | AM+C; `app/commerce_routes.py:150`; shell `app/commerce_routes.py:166` |
| GET | `/admin/billing` | Highest managed account plan, subscription state and paid plan cards; inline shell | Actor, role-aware plan/billing lookup (`app/billing_routes.py:30`, `app/billing.py:269`, `app/plans.py:111`); route `app/billing_routes.py:27`, shell `app/billing_routes.py:120` |
| POST | `/admin/billing/checkout` | Creates platform subscription Checkout session then redirects to Stripe | Actor+C; `begin_checkout` resolves admin/merchant tenant (`app/billing.py:281`, `app/billing.py:301`); route `app/billing_routes.py:148` |
| GET | `/admin/billing/success` | Informational redirect, access changes await signed webhook | Actor only; `app/billing_routes.py:192` |
| GET | `/admin/billing/cancel` | Informational cancellation redirect | Actor only; `app/billing_routes.py:203` |
| GET | `/admin/platform/plans` | Lists every tenant, plan limits, site/product counts, metered usage and assignment form | Actor plus `plans.require_platform_operator` email comparison (`app/plans.py:301`); route `app/plan_routes.py:31`, shell `app/plan_routes.py:67` |
| POST | `/admin/platform/tenants/{tenant_id}/plan` | Operator assigns plan and redirects notice | Actor+operator+C; `app/plan_routes.py:77` |
| GET | `/admin/sites/{site_id}/samples` | Manual merchant field replacement/review plus synthetic sample seed action; inline `site_sample_routes` (`app/site_sample_routes.py:12`, `app/site_sample_routes.py:21`) | AM (`app/site_sample_routes.py:16`); note GET creates missing commerce config and commits (`app/site_sample_routes.py:17`) |
| POST | `/admin/sites/{site_id}/samples/seed` | Fill missing merchant fields with labelled samples (`app/site_sample_routes.py:35`) | AME lock then AM recheck inside service (`app/site_sample_routes.py:42`, `app/site_samples.py:52`) |
| POST | `/admin/sites/{site_id}/samples` | Save replaced/reviewed merchant fields, including commerce fields with config version (`app/site_sample_routes.py:49`) | AME lock then AM service recheck (`app/site_sample_routes.py:56`, `app/site_samples.py:85`) |
| GET | `/admin/sites/{site_id}/golive` | Derived publication checklist, state, hostname and sandbox-commerce controls, audit; inline `site_golive_routes` (`app/site_golive_routes.py:56`, `app/site_golive_routes.py:157`) | AM (`app/site_golive_routes.py:61`) |
| POST | `/admin/sites/{site_id}/golive/publish` | Explicit confirmation, assess/publish transition, version/reason, optional operator override (`app/site_golive_routes.py:193`) | AM via service `_lock_site`; override checks configured operator (`app/site_golive.py:385`, `app/site_golive.py:453`, `app/site_golive.py:379`) |
| POST | `/admin/sites/{site_id}/golive/domain` | Bind validated unique hostname, version guarded (`app/site_golive_routes.py:214`) | AM through service lock (`app/site_golive.py:504`, `app/site_golive.py:385`) |
| POST | `/admin/sites/{site_id}/golive/commerce` | Toggle sandbox commerce with site/config versions and readiness checks (`app/site_golive_routes.py:233`) | AM through service lock (`app/site_golive.py:531`, `app/site_golive.py:385`) |
| GET | `/admin/platform/live-credentials` | Operator sites and live acceptance state cards; inline `site_live_credential_routes` (`app/site_live_credential_routes.py:35`, `app/site_live_credential_routes.py:55`) | Active actor, email equals configured admin email, tenant membership admin (`app/site_live_credential_routes.py:27`) |
| GET | `/admin/platform/sites/{site_id}/live-credentials` | Masked state, storage/verification/acceptance/disable forms and readiness checklist; inline `site_live_credential_routes` (`app/site_live_credential_routes.py:62`, `app/site_live_credential_routes.py:125`) | `require_operator`: AM ownership then admin membership AND configured admin email (`app/site_live_credential_routes.py:67`, `app/live_credentials.py:77`) |
| POST | `/admin/platform/sites/{site_id}/live-credentials/store` | Store encrypted replacement credentials, clear verification (`app/site_live_credential_routes.py:142`) | Operator service guard (`app/live_credentials.py:77`) |
| POST | `/admin/platform/sites/{site_id}/live-credentials/verify` | Bounded live Stripe read-only account verification (`app/site_live_credential_routes.py:155`) | Operator (`app/site_live_credential_routes.py:164`, `app/live_credentials.py:77`) |
| POST | `/admin/platform/sites/{site_id}/live-credentials/accept` | Confirmed/reasoned live acceptance tied to versions and verified credential id (`app/site_live_credential_routes.py:178`) | Operator service guard (`app/live_credentials.py:77`) |
| POST | `/admin/platform/sites/{site_id}/live-credentials/disable` | Disable live payments and revert sandbox (`app/site_live_credential_routes.py:194`) | Operator service guard (`app/live_credentials.py:77`) |

### Integrations/connectors

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/integrations` | Stripe, PayPal, WooCommerce and FastERP guide cards; `ui.integrations_hub` (`app/ui.py:528`) | L; `app/main.py:671` |
| GET | `/admin/integrations/stripe` | Stripe setup guide; `ui.stripe_guide` (`app/ui.py:570`) | L; `app/main.py:681` |
| GET | `/admin/integrations/woocommerce` | WooCommerce setup guide; `ui.woocommerce_guide` (`app/ui.py:592`) | L; `app/main.py:689` |
| GET | `/admin/integrations/paypal` | PayPal setup/readiness guide; `ui.paypal_guide` (`app/ui.py:614`) | L; `app/main.py:697` |
| GET | `/admin/integrations/fasterp` | Reachability, configuration and scoped pending/failed counts; `ui.integration_page` (`app/ui.py:516`) | L; `app/main.py:707` |
| POST | `/admin/integrations/fasterp/sync` | Delivers pending events scoped by legacy membership helper, commits, redirects result | L+CSRF; `app/main.py:717` |
| GET | `/admin/sites/{site_id}/integrations` | Connector capabilities/configuration status, dry-run forms, stored plan review/count/row tables, export links; inline `site_integration_routes` (`app/site_integration_routes.py:119`, `app/site_integration_routes.py:196`) | AM (`app/site_integration_routes.py:124`) |
| POST | `/admin/sites/{site_id}/integrations/{platform}/dry-run` | Fetch connector preview or upload CSV <=2 MB, create stored dry-run plan (`app/site_integration_routes.py:213`) | AM (`app/site_integration_routes.py:232`) |
| POST | `/admin/sites/{site_id}/integrations/{platform}/apply` | Explicit confirmed application of matching reviewed import plan/version (`app/site_integration_routes.py:243`) | AM plus pending plan actor/site scope (`app/site_integration_routes.py:253`) |
| GET | `/admin/sites/{site_id}/integrations/{platform}/export.json` | Download JSON export bundle, private no-store (`app/site_integration_routes.py:266`) | AM (`app/site_integration_routes.py:271`) |
| GET | `/admin/sites/{site_id}/integrations/{platform}/export` | Download connector-native artifact, optional drafts or review report (`app/site_integration_routes.py:282`) | AM (`app/site_integration_routes.py:292`) |

### Analytics/revenue

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/sites/{site_id}/revenue` | UTC start/end filter and gross/refunds/net/orders/AOV, currency exclusion warning; no chart or export | AM; `app/order_management_routes.py:405`; shell at `app/order_management_routes.py:413` |
| GET | `/admin/platform/revenue` | Per-site and per-currency revenue summaries | Actor, configured operator email plus explicit admin membership per listed tenant (`app/order_management_routes.py:438`); route `app/order_management_routes.py:433`; shell at `app/order_management_routes.py:457` |

### Inbox

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/sites/{site_id}/inbox` | Latest 100 contacts with name/email/message/delivery status; inline `site_routes` (`app/site_routes.py:854`, `app/site_routes.py:859`) | AME; both tenant and site filtered (`app/site_routes.py:858`) |

### Private commerce simulator

| Method | Path | Behaviour and rendering module | Role/membership and source |
|---|---|---|---|
| GET | `/admin/sites/{site_id}/demo` | Private synthetic commerce workspace, view selection, forms; inline shell plus `demo-commerce.css` | AME; `app/demo_routes.py:34`, `app/demo_routes.py:38`, `app/demo_routes.py:139` |
| POST | `/admin/sites/{site_id}/demo/start` | Explicitly creates simulator workspace then redirects | Actor+C, `demo.workspace` checks AME (`app/demo_commerce.py:30`); `app/demo_routes.py:147` |
| POST | `/admin/sites/{site_id}/demo/action` | Versioned command-id guarded synthetic actions then redirects selected view/result | Actor+C, scoped via `demo.apply`/workspace (`app/demo_commerce.py:116`, `app/demo_commerce.py:30`); `app/demo_routes.py:158` |

### Route boundaries and operational details

Notification/email configuration has no dedicated merchant settings route or template designer. The current merchant surface shows queue kind/status/attempts (`app/customer_routes.py:280`), while delivery/retries are backend concerns (`app/models.py:734`). Users/staff/roles likewise have no merchant management routes; `/admin/customers` mixes customers and staff in a read-only table (`app/main.py:742`).

The order list is explicitly real site orders only, excluding simulator records (`app/order_management_routes.py:88`). `owned_order` scopes SiteOrder, Order and ShopCustomer by site and tenant (`app/order_management.py:160`). Fulfilment is whole-order only with states unfulfilled, fulfilled, delivered and cancelled; permitted transitions are defined at `app/order_management.py:33`. Carrier/tracking and a fulfilment or cancellation note are supported (`app/order_management_routes.py:125`). The timeline displays event kind, time, title, details and optional carrier link, including unresolved refund reconciliation (`app/order_management_routes.py:171`). A distinct returns/RMA workflow and independent general order-note editor are absent from this route inventory; notes belong to fulfilment transitions (`app/order_management_routes.py:132`). Refunds already support exact partial monetary amounts and explicit review, so describing refunds as entirely absent would be incorrect (`app/order_management_routes.py:141`, `app/order_management_routes.py:327`). Reports default to 30 days and allow up to 366 inclusive days (`app/order_management.py:65`); reported gross counts committed paid/refunded order totals, refunds by completion date, with no currency aggregation (`app/order_management_routes.py:426`, `app/order_management.py:91`).

`subscription_routes.py` has no `/admin` route. It registers shopper account list/detail/action/payment flows (`app/subscription_routes.py:59`, `app/subscription_routes.py:76`, `app/subscription_routes.py:136`, `app/subscription_routes.py:179`). Merchant subscription settings are per-product eligibility in commerce (`app/commerce_routes.py:84`), not a merchant contracts management console. `store_checkout_routes.py` likewise has no `/admin` routes: cart/checkout routes are under `/sites/{slug}` (`app/store_checkout_routes.py:67`, `app/store_checkout_routes.py:199`, `app/store_checkout_routes.py:317`). `marketing_routes.py` registers `/marketing/`, signup and verification rather than merchant admin (`app/marketing_routes.py:1051`, `app/marketing_routes.py:1054`, `app/marketing_routes.py:1144`).

Billing chooses an existing billing tenant or first created managed tenant, whereas displayed account plan is the highest tier across managed tenants (`app/billing.py:281`, `app/plans.py:111`). Existing self-serve subscription changes are rejected with contact-the-operator instruction (`app/billing.py:313`). Integration guides describe deployment environment configuration rather than merchant credential forms (`app/ui.py:537`, `app/ui.py:557`). Site-specific connector routes should be read separately from these legacy guides.

Simulator data is explicitly separated from real catalogue/orders and does not call live payment, email, carrier or fulfilment providers (`app/demo_routes.py:41`). It should not be counted as production merchant functionality.

Site routes also register public media and account verification endpoints outside `/admin`: GET `/site-media/{site_id}/{media_id}` serves referenced public media or requires membership for private media; POST `/account/verification/resend` requires actor and CSRF (`app/site_media_routes.py:71`, `app/site_routes.py:109`). No merchant staff/role-management route was found. Discount administration is the read-only legacy promotions list, not CRUD (`app/main.py:651`).

## 2. Data model inventory

All rows below are SQLAlchemy models in `app/models.py`. UUID hex primary keys use `new_id()` (`app/models.py:28`); all models inherit created/updated timestamps (`app/models.py:36`). Monetary `*_minor` values are integers. The table lists mapped fields, including FK identifiers, rather than implying all fields have merchant UI controls.

| Model | Mapped fields | Evidence |
|---|---|---|
| `Tenant` | `id`, `slug`, `name`, `status`, `plan` | `app/models.py:43` |
| `User` | `id`, `email`, `name`, `is_active`, `password_hash`, `email_verified_at` | `app/models.py:53` |
| `Membership` | `id`, `tenant_id`, `user_id`, `role` | `app/models.py:68` |
| `Channel` | `id`, `tenant_id`, `slug`, `name`, `currency`, `country_code`, `locale`, `prices_include_tax`, `is_active` | `app/models.py:77` |
| `Category` | `id`, `tenant_id`, `parent_id`, `slug`, `name`, `description`, `sort_order` | `app/models.py:91` |
| `ProductType` | `id`, `tenant_id`, `slug`, `name`, `is_shipping_required` | `app/models.py:103` |
| `Product` | `id`, `tenant_id`, `product_type_id`, `category_id`, `slug`, `name`, `subtitle`, `description`, `seo_title`, `seo_description`, `image_url`, `is_published`, `is_featured`, `variants`, `category` | `app/models.py:113` |
| `Attribute` | `id`, `tenant_id`, `slug`, `name`, `input_type` | `app/models.py:140` |
| `AttributeValue` | `id`, `attribute_id`, `slug`, `name`, `value` | `app/models.py:150` |
| `ProductVariant` | `id`, `tenant_id`, `product_id`, `sku`, `name`, `attributes_json`, `weight_grams`, `sort_order`, `is_active`, `product` | `app/models.py:160` |
| `VariantChannelListing` | `id`, `variant_id`, `channel_id`, `currency`, `price_minor`, `compare_at_minor`, `cost_minor` | `app/models.py:175` |
| `Warehouse` | `id`, `tenant_id`, `code`, `name`, `country_code` | `app/models.py:190` |
| `Stock` | `id`, `warehouse_id`, `variant_id`, `quantity`, `allocated` | `app/models.py:200` |
| `Cart` | `id`, `tenant_id`, `channel_id`, `user_id`, `token`, `email`, `status`, `voucher_code`, `lines` | `app/models.py:214` |
| `CartLine` | `id`, `cart_id`, `variant_id`, `quantity`, `cart`, `variant` | `app/models.py:228` |
| `Voucher` | `id`, `tenant_id`, `code`, `kind`, `value`, `minimum_minor`, `usage_limit`, `used_count`, `is_active` | `app/models.py:239` |
| `Order` | `id`, `tenant_id`, `channel_id`, `user_id`, `number`, `token`, `idempotency_key`, `email`, `status`, `payment_status`, `currency`, `subtotal_minor`, `discount_minor`, `shipping_minor`, `tax_minor`, `total_minor`, `shipping_address_json`, `fasterp_order_id`, `lines` | `app/models.py:253` |
| `OrderLine` | `id`, `order_id`, `variant_id`, `sku`, `product_name`, `variant_name`, `quantity`, `unit_price_minor`, `total_minor`, `order` | `app/models.py:280` |
| `PaymentTransaction` | `id`, `order_id`, `provider`, `external_id`, `kind`, `status`, `currency`, `amount_minor` | `app/models.py:294` |
| `Review` | `id`, `tenant_id`, `product_id`, `user_id`, `author_name`, `rating`, `title`, `body`, `is_approved` | `app/models.py:307` |
| `WishlistItem` | `id`, `tenant_id`, `user_id`, `product_id` | `app/models.py:321` |
| `OutboxEvent` | `id`, `tenant_id`, `topic`, `aggregate_id`, `payload_json`, `status`, `attempts`, `last_error`, `next_attempt_at` | `app/models.py:330` |
| `StripeWebhookEvent` | `id`, `tenant_id`, `site_id`, `event_id`, `event_type`, `processed_at` | `app/models.py:347` |
| `ExternalMapping` | `id`, `tenant_id`, `site_id`, `system`, `resource_type`, `local_id`, `external_id`, `version` | `app/models.py:365` |
| `IntegrationPlan` | `id`, `tenant_id`, `site_id`, `user_id`, `platform`, `operation`, `schema_version`, `version`, `status`, `report_json`, `payload_json`, `expires_at`, `consumed_at` | `app/models.py:388` |
| `ChatThread` | `id`, `tenant_id`, `user_id`, `title`, `surface` | `app/models.py:408` |
| `ChatMessage` | `id`, `thread_id`, `role`, `content`, `route_context` | `app/models.py:417` |
| `Site` | `id`, `tenant_id`, `channel_id`, `slug`, `name`, `hostname`, `theme`, `settings_json`, `published_settings_json`, `status`, `version` | `app/models.py:426` |
| `SiteMenu` | `id`, `tenant_id`, `site_id`, `name`, `items_json`, `published_items_json` | `app/models.py:448` |
| `SiteSnippet` | `id`, `tenant_id`, `site_id`, `placement`, `draft_json`, `published_json` | `app/models.py:464` |
| `BlogCategory` | `id`, `tenant_id`, `site_id`, `slug`, `name` | `app/models.py:485` |
| `SitePage` | `id`, `tenant_id`, `site_id`, `path`, `title`, `kind`, `product_id`, `draft_json`, `published_json`, `version` | `app/models.py:495` |
| `SiteRevision` | `id`, `tenant_id`, `site_id`, `page_id`, `author_id`, `content_json`, `action` | `app/models.py:515` |
| `SiteBuilderTurn` | `id`, `tenant_id`, `site_id`, `user_id`, `command_id`, `prompt`, `status`, `provider`, `context_json`, `response_json` | `app/models.py:526` |
| `SiteChangeSet` | `id`, `tenant_id`, `site_id`, `user_id`, `source`, `summary`, `before_json`, `after_json` | `app/models.py:541` |
| `DemoWorkspace` | `id`, `tenant_id`, `site_id`, `version`, `state_json` | `app/models.py:553` |
| `DemoCommand` | `id`, `tenant_id`, `site_id`, `workspace_id`, `command_id`, `fingerprint`, `result_json` | `app/models.py:563` |
| `SiteMedia` | `id`, `tenant_id`, `site_id`, `title`, `alt`, `public_url`, `localized_alt`, `content_type`, `storage_key`, `is_placeholder`, `size`, `data` | `app/models.py:575` |
| `SiteContact` | `id`, `tenant_id`, `site_id`, `name`, `email`, `message`, `status`, `attempts`, `client_hash` | `app/models.py:599` |
| `SiteCommerceSettings` | `id`, `tenant_id`, `site_id`, `customer_accounts_enabled`, `mode`, `origin_json`, `shipping_minor`, `free_shipping_threshold_minor`, `allowed_states_json`, `product_tax_codes_json`, `subscription_product_ids_json`, `tax_registration_reviewed`, `live_accepted_at`, `live_accepted_by`, `live_credential_id`, `version` | `app/models.py:612` |
| `SiteStripeLiveCredential` | `id`, `tenant_id`, `site_id`, `secret_key_ciphertext`, `webhook_secret_ciphertext`, `key_prefix`, `key_last_four`, `created_by`, `verified_at`, `verified_by` | `app/models.py:643` |
| `CommerceQuote` | `id`, `tenant_id`, `site_id`, `fingerprint`, `provider_id`, `currency`, `subtotal_minor`, `discount_minor`, `shipping_minor`, `tax_minor`, `total_minor`, `expires_at`, `snapshot_json` | `app/models.py:664` |
| `ShopCustomer` | `id`, `tenant_id`, `site_id`, `email`, `name`, `verified_at`, `stripe_customer_id`, `is_active` | `app/models.py:681` |
| `CustomerChallenge` | `id`, `tenant_id`, `site_id`, `customer_id`, `purpose`, `token_hash`, `expires_at`, `consumed_at`, `client_hash`, `consent_json` | `app/models.py:694` |
| `MarketingConsent` | `id`, `tenant_id`, `site_id`, `customer_id`, `status`, `consent_json`, `confirmed_at`, `unsubscribed_at` | `app/models.py:708` |
| `CustomerOffer` | `id`, `tenant_id`, `site_id`, `customer_id`, `code`, `percent`, `expires_at`, `redeemed_order_id` | `app/models.py:721` |
| `CommerceMail` | `id`, `tenant_id`, `site_id`, `customer_id`, `user_id`, `challenge_id`, `reference_json`, `kind`, `dedupe_key`, `status`, `attempts`, `available_at`, `provider_id` | `app/models.py:734` |
| `SignupAttempt` | `id`, `scope`, `client_hash`, `email_hash`, `accepted` | `app/models.py:755` |
| `SignupEmailVerification` | `id`, `tenant_id`, `site_id`, `user_id`, `token_hash`, `expires_at`, `consumed_at` | `app/models.py:770` |
| `OnboardingState` | `id`, `tenant_id`, `site_id`, `user_id`, `status`, `business_description`, `product_context`, `design_direction`, `generation_key`, `generation_token`, `site_fingerprint`, `generation_source`, `failure_code`, `result_json`, `generation_started_at`, `completed_at` | `app/models.py:798` |
| `SiteOrder` | `id`, `tenant_id`, `site_id`, `order_id`, `customer_id`, `quote_id`, `stripe_checkout_id`, `stripe_invoice_id` | `app/models.py:834` |
| `RefundCommand` | `id`, `tenant_id`, `site_id`, `site_order_id`, `actor_id`, `request_key`, `request_hash`, `amount_minor`, `currency`, `reason`, `state`, `provider_payment_id`, `provider_refund_id`, `command_json`, `result_json`, `attempts` | `app/models.py:846` |
| `ShipmentEvent` | `id`, `tenant_id`, `site_id`, `site_order_id`, `carrier`, `tracking_number`, `tracking_url`, `status`, `note`, `author_id` | `app/models.py:872` |
| `CheckoutAttempt` | `id`, `tenant_id`, `site_id`, `customer_id`, `quote_id`, `offer_id`, `request_key`, `request_hash`, `provider_payload_json`, `provider_started_at`, `state`, `expires_at`, `stripe_session_id`, `order_id` | `app/models.py:886` |
| `InventoryReservation` | `id`, `tenant_id`, `site_id`, `attempt_id`, `stock_id`, `quantity`, `state` | `app/models.py:905` |
| `SiteCart` | `id`, `tenant_id`, `site_id`, `lines_json`, `discount_code`, `version`, `request_key`, `checkout_id` | `app/models.py:920` |
| `SubscriptionContract` | `id`, `tenant_id`, `site_id`, `customer_id`, `initial_attempt_id`, `stripe_customer_id`, `payment_method_id`, `state`, `interval_months`, `anchor_day`, `next_due_at`, `lines_json`, `destination_json`, `recipient_name`, `consent_json`, `version` | `app/models.py:932` |
| `SubscriptionCycle` | `id`, `tenant_id`, `site_id`, `contract_id`, `due_at`, `state`, `attempt_id`, `payment_intent_id`, `provider_payload_json`, `tax_transaction_id` | `app/models.py:953` |
| `SubscriptionRecovery` | `id`, `tenant_id`, `site_id`, `cycle_id`, `attempt_id` | `app/models.py:968` |
| `SubscriptionEvent` | `id`, `tenant_id`, `site_id`, `contract_id`, `customer_id`, `request_key`, `request_hash`, `action`, `details_json` | `app/models.py:977` |
| `SubscriptionPaymentSetup` | `id`, `tenant_id`, `site_id`, `contract_id`, `customer_id`, `request_key`, `state`, `session_id`, `command_json` | `app/models.py:991` |
| `UsageEvent` | `id`, `tenant_id`, `site_id`, `user_id`, `kind`, `quantity` | `app/models.py:1005` |
| `BillingSubscription` | `id`, `tenant_id`, `user_id`, `plan_id`, `status`, `checkout_command_id`, `stripe_customer_id`, `stripe_subscription_id`, `stripe_checkout_session_id`, `current_period_end`, `cancel_at_period_end`, `last_invoice_status`, `operator_disabled_at` | `app/models.py:1028` |
| `PlatformBillingStripeEvent` | `id`, `tenant_id`, `event_id`, `event_type`, `processed_at` | `app/models.py:1075` |

### Model boundaries and missing entities

- Catalogue ownership is tenant-level: `Product` has `tenant_id`, type/category FKs and a single `image_url`, but no `site_id`; `SitePage.product_id` supplies the storefront association. Variants carry free-form `attributes_json`, prices are per `VariantChannelListing`, inventory is per warehouse/variant `Stock` (`app/models.py:113`, `app/models.py:160`, `app/models.py:175`, `app/models.py:200`, `app/models.py:495`). Categories are hierarchical, but there is no Collection model, product/collection join table, ProductOption model or separate ProductImage model anywhere in this complete model inventory (`app/models.py:91`, `app/models.py:140`, `app/models.py:160`). A collection page is a page kind, not an independently modelled collection entity (`app/content.py:28`).
- Customer commerce records are `ShopCustomer`, distinct from merchant/platform `User`; site orders link the common `Order` to site, customer and quote through `SiteOrder` (`app/models.py:53`, `app/models.py:253`, `app/models.py:681`, `app/models.py:834`). `Voucher` is a tenant-level discount with kind/value, threshold and usage count; `CustomerOffer` is a customer-bound, expiring percentage offer. Neither has a general rule-condition tree or campaign scheduling model (`app/models.py:239`, `app/models.py:721`).
- There are durable refund commands and shipment events, including a shipment `note`; no dedicated Return/RMA, OrderNote, OrderTimeline, customer address-book, saved segment or customer-tag model is declared in this inventory (`app/models.py:253`, `app/models.py:681`, `app/models.py:846`, `app/models.py:872`). These are schema observations, not claims that every workflow is impossible through another system.
- There is no standalone Section/Block table. Page blocks, menus, snippet snapshots and builder changes are JSON-backed. Blog articles are `SitePage(kind="article")` plus JSON metadata and `BlogCategory`, not a separate Article ORM model (`app/models.py:448`, `app/models.py:464`, `app/models.py:485`, `app/models.py:495`, `app/models.py:541`, `app/content.py:103`).

## 3. Page content, sections, rendering and editing today

### Persistence and schema

`SitePage` stores `draft_json` and nullable `published_json` through `BlockDocumentJSON`, plus path/title/kind/product/version. Assignment and database bind/read normalise content. Legacy unknown-section documents are allowed through the storage read adapter for listing/migration/rollback, while rendering and writes remain strict (`app/models.py:495`, `app/site_block_storage.py:5`). Canonical content is `{version: 1, blocks: [...]}`; a legacy section list or `{sections: [...]}` is accepted and normalised, with stable deterministic IDs for legacy reads. Blocks require unique IDs, at most 60 per page; individual items are limited to 200 and a gallery to 12 URLs (`app/site_blocks.py:13`, `app/site_blocks.py:158`, `app/site_blocks.py:188`, `app/site_blocks.py:236`, `app/site_blocks.py:250`).

All types share the schema's editable text fields: `heading`, `eyebrow`, `body`, `button`, `image`, `video`, `mobile_video`, `poster`, `link`, `url`, `category`, `alt`. They also allow `id`, `type`, `role`, `layout`, `version`, `hidden`, `subscription_preview`, `items`, `gallery`; item keys are `heading`, `body`, `url`, `theme`, `image`, `alt`, `label`, `value`. These are a common allowlist rather than a per-type form definition. Text may be a string or locale-to-string map, normal text is capped at 30,000 characters, URLs are validated, embeds require HTTPS, missing translations resolve to empty text (`app/site_blocks.py:17`, `app/site_blocks.py:124`, `app/site_blocks.py:142`, `app/site_blocks.py:158`, `app/site_blocks.py:287`). Page-level content includes required title, description, optional image/category/blog metadata (`app/site_blocks.py:279`, `app/content.py:56`).

### Available block types and constraints

Every registered type below is offered by the classical editor's add-section selector because `SECTION_TYPES` is built directly from `BLOCK_TYPES` (`app/content.py:27`, `app/site_routes.py:734`). Shared fields above apply to each type; the final column adds type-specific item requirements. An empty items list remains permitted unless a specialised validator says otherwise.

| Types | Purpose and additional fields/constraints | Evidence |
|---|---|---|
| `hero`, `text`, `split` | Image/video hero, text and split image/text; shared fields | `app/site_blocks.py:48` |
| `products`, `facts`, `claims`, `reviews`, `articles` | Product listing, site research figures, approved claims, customer reviews and latest articles; these often read shared config/database records rather than arbitrary item content | `app/site_blocks.py:51`, `app/site_ui.py:171` |
| `research`, `references` | Item requires `url`; optional heading/body/theme render source cards | `app/site_blocks.py:56`, `app/site_blocks.py:61`, `app/site_ui.py:208` |
| `faq`, `team` | Items require heading; optional body, team image/URL | `app/site_blocks.py:57`, `app/site_ui.py:213` |
| `contact`, `product`, `embed` | Contact form; product details/gallery; sandboxed HTTPS iframe URL | `app/site_blocks.py:59`, `app/site_blocks.py:177`, `app/site_ui.py:218`, `app/site_ui.py:228` |

Product purchase item themes are `gallery`, `trust`, `box`, `rating`, `spec`, `bundle`, `color`, `flavor`, `quality`, `fact`, `facts-lead`, `facts-placeholder`, `quality-title`, `ingredient`, `range-link`, `subscription`, `one-time`, `mix-hint`, `pack`, `subscribe-link`, `facts-note`, `ingredient-range` (`app/site_blocks.py:90`). Across product sections, theme-specific required keys are gallery image/alt; bundle label/value/body; rating label/value; spec label/value; flavor value; trust label; box body; quality label/value; fact label/value/body; ingredient value/body; day heading/body/label/image/alt; day-stat those plus value; day-source label/body; faq heading/body; product heading/body. Other theme names only require their block's generic required fields (`app/site_blocks.py:108`). Bundle quantity is 1/2/3/4 with discount 0/10/15/20; sample rating must be 1-5 and count 1-200 (`app/site_blocks.py:204`).

### Exact merchant editing experience

### Storefront rendering

## 4. Admin UI shell and asset pipeline

- Legacy `ui.merchant_page` uses `merchant-shell`, `merchant-nav`, `merchant-main`, a top bar with user/sign-out and an embedded merchant copilot (`app/ui.py:439`). Its `MERCHANT_NAV` groups Overview (dashboard/assistant), Commerce (products/orders/customers/promotions), Operations (inventory/channels/sites/integrations) and Build (developers/storefront) (`app/ui.py:431`). `document_head` loads `static/site.css` and deferred `static/app.js` (`app/ui.py:80`).
- Builder shell is a closure delegating to `platform_page` (`app/site_routes.py:83`). Shared page emits viewport/noindex/referrer metadata, favicon, `fonts.css`, `site.css`, `platform.css`, `site-editor.css`, deferred `site-editor.js` (or `customer.js` for customer pages), brand/header/nav and `e-main` (`app/platform_ui.py:24`). `e-*` classes identify shared editor/platform components: `e-top`, `e-nav`, `e-card`, `e-form`, `e-grid`, `e-subnav` (`app/platform_ui.py:8`, `app/site_routes.py:492`). CSS is directly served from `static`, not generated from a client component framework.
- Global default navigation has only Dashboard `/admin` and Sites & content `/admin/sites` (`app/platform_ui.py:25`). Site overview adds grouped Build & content, Catalog & commerce, Launch & operations links (`app/site_routes.py:440`). Customers is a separate link in body (`app/site_routes.py:491`), absent from that grouped navigation. Builder has its own shorter subnavigation (`app/site_builder_routes.py:200`).
- FastHTML is server-rendered Python. `fast_app(live=False,pico=False,...)` configures cookie sessions, production secure cookies and SameSite lax; `/static` uses Starlette `StaticFiles` (`app/main.py:55`, `app/main.py:68`). `static_asset` adds integer file modification time as `?v=` cache version (`app/ui.py:66`). No package.json/Vite/Webpack asset build was found in the file inventory; the application is packaged with Hatchling and serves source assets directly (`pyproject.toml:1`, `app/main.py:68`). No React/Vue/Svelte client framework was found in these admin sources. No explicit `hx_`, `hx-` or `htmx` matches were found in app/static source; FastHTML's default injected runtime should be distinguished from actual htmx UI use, which is not present in these handlers.
- Classical section move-up/move-down manipulates DOM order, removal asks `window.confirm`, then changes persist only on form submission (`static/site-editor.js:1`, `app/site_routes.py:699`, `app/site_routes.py:802`). Generation form script disables button and shows progress (`static/site-editor.js:14`). There is no drag/drop code in that classical editor script.
- AI workspace additionally loads `site-workspace.css`/`.js`, has plain buttons for editor/preview tabs and desktop/mobile size, an iframe plus section-selection control (`app/site_builder_routes.py:214`, `app/site_builder_routes.py:231`). It is not a rich text or inline canvas field editor: page field editing is a link back to the structured form (`app/site_builder_routes.py:232`).

`static/site-workspace.js:43` intercepts builder chat submit, uses `fetch` with same-origin credentials, parses returned HTML via DOMParser and replaces `.b-workspace`, preserving preview size, selected section and scroll (`static/site-workspace.js:58`). Section targeting uses origin/source/site/page-validated postMessage (`static/site-workspace.js:32`). This is custom vanilla JS HTML fragment replacement, not explicit htmx. Exact operator helper definition is `app/live_credentials.py:72`; `store`, `accept`, `disable` rechecks are respectively `app/live_credentials.py:123`, `app/live_credentials.py:200`, `app/live_credentials.py:240`. Design token choices: accent/background/surface/text hex colours, font modern/humanist, headings editorial/modern, spacing compact/comfortable/airy, radius square/subtle/rounded, width narrow/wide, hero left/centre (`app/site_theme.py:7`, `app/site_theme.py:10`). UI is four colour inputs and select controls over CHOICES (`app/site_builder_routes.py:167`).


## 5. Authentication, roles, tenancy and CSRF

- `actor` checks session user_id resolves to active User; it does not trust role text in the session (`app/site_routes.py:68`). `owned_site` joins tenant Membership and requested site id, so roles are tenant-wide rather than individual-site memberships (`app/content.py:72`). `site_page` and `site_pages` require both site_id and tenant_id (`app/content.py:84`, `app/content.py:93`). Catalogue is tenant-wide with site-channel pricing rather than product.site_id (`app/site_catalog.py:100`, `app/site_catalog.py:105`, `app/content.py:253`). `builder.lock_site` rechecks AME and version (`app/site_builder_services.py:64`).
- CSRF helper creates a 32-byte-url-safe session token in a hidden input, and compares submitted token using `secrets.compare_digest`; missing/mismatched tokens raise CommerceError (`app/site_routes.py:75`, `app/site_routes.py:79`). Menu JSON API uses this same session token in request data (`app/site_menu_routes.py:91`).
Operator gates differ: plan assignment checks configured operator email (`app/plans.py:301`); live credentials additionally require admin membership in the target tenant (`app/live_credentials.py:72`); platform revenue/deliveries enumerate only explicitly administered tenants (`app/order_management_routes.py:438`, `app/order_management_routes.py:481`). Legacy CSRF uses its own `csrf_token`/`require_csrf` helpers (`app/main.py:84`, `app/main.py:95`).

Host routing is a separate boundary: `SiteHostMiddleware` treats `/admin` as a shared path, resolves a custom hostname to a Site for storefront paths, gates publication/commerce and rewrites to `/sites/{slug}` (`app/site_context.py:53`, `app/site_context.py:60`, `app/site_context.py:83`). It does not replace membership checks in admin handlers.

## 6. Existing tests and browser verification

This is an inventory of checked-in test intent, not a claim that the suites passed during this audit. No application module was imported and no test, browser verifier, migration or server was run. Several verifiers deliberately create records, publish settings, perform mocked payments or seed data; they are not read-only inspection commands. Repository instructions explicitly warn that importing `app.main` starts database preparation, seeding and scheduling (`AGENTS.md:63`, `app/main.py:48`, `app/main.py:78`).

### Admin HTTP and permission coverage

| Test file and evidence | Covered behaviour |
|---|---|
| `tests/test_routes.py:30`, `:50`, `:98`, `:193` | Login requirement, safe return URLs, membership-derived roles and legacy admin reads limited to membership tenants. |
| `tests/test_site_routes.py:48`, `:78`, `:143`, `:164` | Private preview membership, neutral/private site creation, commerce CSRF and go-live CSRF/PRG/readiness review. |
| `tests/test_site_builder_routes.py:35`, `:55`, `:71`, `:113`, `:129`, `:148` | Shared classical/chat draft, failure isolation, refinement preview/reject/accept, reversible settings, cancellation CSRF and explicit merchant proposal approval. |
| `tests/test_block_builder.py:62` | Classical nested-item deletion preserves translation alignment through the page POST route. |
| `tests/test_site_generation.py:185`, `:227` | Generated draft redirects to builder; invalid brief returns notice. |
| `tests/test_onboarding_wizard.py:141`, `:184`, `:213`, `:229`, `:280`, `:322`, `:345`, `:394` | Brief/generate/skip/resume, failure retry, quota gate, idempotence, concurrent content protection, non-member/editor denial. |
| `tests/test_site_blog.py:196` | Editorial fields, public filters/SEO, draft preview and CSRF forms. Service-level editor/foreign publish rejection is also tested at `tests/test_site_blog.py:134`. |
| `tests/test_site_media.py:156` | Upload, edit, CSRF and ownership HTTP paths. Reference-safe deletion is tested at `tests/test_site_media.py:59`. |
| `tests/test_site_menus.py:191` | Menu form/API CSRF, stale versions and permissions. |
| `tests/test_site_snippets.py:122` | Snippet CSRF, membership, version and public-source injection. |
| `tests/test_demo_routes.py:14` | Private simulator membership, CSRF and explicit start. |
| `tests/test_customer_routes.py:97` | Customer login does not grant merchant access; logout revokes customer session. Most of this file covers shopper accounts rather than merchant CRUD. |
| `tests/test_order_management.py:363` | Dedicated order routes: tenant boundary, CSRF and PRG. Domain tests cover fulfilment/roles (`:148`), exact/idempotent refunds (`:187`), mismatch failure (`:254`) and revenue/demo isolation (`:301`). |
| `tests/test_woocommerce_connector.py:227`, `tests/test_shopify_connector.py:222`, `tests/test_wordpress_connector.py:371`, `tests/test_csv_merchant_feed.py:259` | Connector review/apply routes and downloads; CSV upload CSRF, stored plan application and feed download. |
| `tests/test_live_credentials.py:235` | Operator form CSRF, secret non-echo and merchant badge-only view. |
| `tests/test_plans_quotas.py:115`, `:137`, `:163`, `:216`, `:241`, `:448`, `:456`, `:493` | Site/AI/product write quotas, success metering, operator gate/plan changes and reads staying open when quotas are exhausted. |
| `tests/test_saas_billing.py:153`, `:181` | Unconfigured billing panel/checkout refusal and subscription-mode checkout creation. Webhook reconciliation is separately covered from `:228`. |
| `tests/test_merchant_login.py:22`, `:91`, `tests/test_signup.py:198` | Merchant workspace provisioning and onboarding redirects. |

### Every checked-in `scripts/verify_*_browser.py`

| Script and evidence | What it verifies / scope |
|---|---|
| `scripts/verify_site_browser.py:40`, `:99`, `:231`, `:300`, `:440`, `:525`, `:601`, `:764` | Main acceptance harness: marketing/root, signup, onboarding, plans, billing, design and extensive merchant/site journeys. Go-live at `:988`, operator credentials at `:1092`, revenue at `:1270`, delivery issues at `:1386`, connector journeys at `:1413`, classical page editor at `:1587`, refinement at `:1607`, menus at `:1646`, media picker at `:1683`, snippets at `:1746`. Captures desktop/mobile evidence and checks horizontal overflow. |
| `scripts/verify_builder_browser.py:14`, `:48`, `:57`, `:94` | Isolated, external-network-blocked guided builder: sample details, presets, click-to-select iframe section, chat draft update, design controls, undo, mobile tabs, demo and explicit merchant-change review. Guided fixture evidence, not real LLM acceptance. |
| `scripts/verify_demo_browser.py:11` | Reusable private demo flow called by builder verifier; snapshots real commerce table counts, checks simulator actions do not alter them. |
| `scripts/verify_us_commerce_browser.py:10`, `:25`, `:44` | Merchant commerce settings, branding, shipping save, desktop/mobile overflow/screenshots; mutates local settings. |
| `scripts/verify_customer_browser.py:17`, `:46`, `:97` | Customer login/newsletter/order journey plus merchant customer view; local isolated data required. Contains an obsolete merchant shipment interaction, see usability findings. |
| `scripts/verify_customer_accounts_browser.py:15` | Creates its own temporary data directory/server and checks shopper account opt-in journeys and desktop/mobile views. |
| `scripts/verify_analytics_browser.py:16`, `:60`, `:64` | Saves merchant GA4 settings, intercepts analytics traffic and verifies consent, withdrawal, cross-tab behaviour, query/token stripping and GPC; desktop/mobile screenshots. |
| `scripts/verify_store_checkout_browser.py:16` | Local simulated Stripe checkout and optional subscription purchase flow; explicitly not proof of provider sandbox payment acceptance (`:1`). |
| `scripts/verify_subscription_browser.py:16`, `:152`, `:188` | Fixture customer subscription controls, failed renewal/recovery, payment, pause/changes/cancellation; not a merchant subscription console. |

Coverage limits: HTTP tests and browser scripts are evidence of intended behaviour, not proof of current operational health or a complete route-by-role matrix. The dedicated customer verifier still expects a shipment form although customer admin now directs merchants to Orders (`scripts/verify_customer_browser.py:98`, `app/customer_routes.py:288`). No browser run or fresh screenshot evidence was produced for this documentation-only audit.



## 7. Merchant usability gaps, ranked by impact

The comparison below is an assessment of ordinary catalogue, order and content-management workflows a merchant would expect from a mature commerce admin. It is not a claim about a particular current Shopify or WooCommerce release. Absence means no matching merchant route, form action or model was found in the audited source, not that an operator could not change records with custom code or an integration.

| Impact | Finding and consequence | Current evidence / boundary |
|---|---|---|
| **P1, daily trading** | Inventory is visible in the legacy admin but there is no merchant stock-adjustment, stocktake, transfer or purchase-order workflow. Merchants cannot maintain quantities from the site catalogue form. | Site catalogue only saves names/images/channel prices or creates products (`app/site_catalog.py:106`, `:138`, `:155`); legacy inventory route is GET (`app/main.py:641`); `Stock` nevertheless exists (`app/models.py:200`). |
| **P1, daily trading** | Catalogue management is shallow: existing product/variant names, primary image and prices can be edited, but no general variant add/remove matrix, product option editor, category/collection organiser, archive/delete or bulk editor is exposed. | Complete catalogue GET/POST implementation (`app/site_catalog.py:94`, `:123`); create form accepts comma-separated variant names and assigns a generic collection (`:117`, `:161`). Attributes are JSON on variants (`app/models.py:160`). Connector imports provide a richer data entry path, not an everyday editor (`app/site_integration_routes.py:213`, `:243`). |
| **P1, scale** | No catalogue search/filter/pagination or bulk actions. Orders have a fulfilment-status filter, but no general order/customer search, pagination or saved views. Customers are capped at the newest 100, mail at 50 and recent order links at 50 without navigation to older customer records. | Catalogue loads all tenant products/cards (`app/site_catalog.py:99`); orders filter at `app/order_management_routes.py:67`, query materialises all matches (`app/order_management.py:140`); customer limits and simple email rows at `app/customer_routes.py:278`. Do not report that orders lack all filtering. |
| **P1, operations** | No merchant returns/RMA, exchange, line-level refund allocation or restock-on-return workflow. A monetary refund and cancellation of fulfilment are separate actions; there is no general standalone order-note editor. | Exact amount/reason refunds exist (`app/order_management.py:448`); cancellation explicitly does not refund (`:194`); note belongs to fulfilment event (`:182`); current order actions are fulfilment/resync/refund (`app/order_management_routes.py:222`, `:248`, `:327`, `:366`). Timeline already exists (`app/order_management.py:599`). |
| **P1, administration** | No merchant staff invitation/removal, role editing or granular permission settings. The backend has three coarse membership roles, while operator access is separate. | `Membership` schema (`app/models.py:68`), owned-site role gate (`app/content.py:72`), complete route register inventory above. Existing plan and live-credential consoles are operator tools, not staff management (`app/plan_routes.py:31`, `app/site_live_credential_routes.py:35`). |
| **P1, customer service** | Customer admin is a read-only email/verification list, mail-queue summary and order links. No customer detail editor, segments, tags, import/export, address management or merchant subscription console. | Full merchant customer handler (`app/customer_routes.py:273`); subscriptions are under shopper `/sites/{slug}/account/subscriptions` (`app/subscription_routes.py:59`). Extensive subscription backend models do not create an admin UI (`app/models.py:932`). |
| **P2, editor efficiency** | No drag-and-drop canvas or direct inline text editing. Classical editing uses a separate form; section movement uses Up/Down controls and a save. Builder preview selection targets a section for chat/refinement rather than turning the iframe into a freeform editor. | DOM movement/removal (`static/site-editor.js:1`); builder selection and draft form (`scripts/verify_builder_browser.py:65`); page GET/POST (`app/site_routes.py:705`, `:746`). This is a real structured editor, not merely a JSON textarea. |
| **P2, content scale** | Page discovery lives on the site overview, with no dedicated searchable/filterable content list, multi-select publication or standalone category manager. Blog metadata and rich section types exist, but repeatable/nested fields require long forms. | Page rows/create form (`app/site_routes.py:492`); page editor (`:705`); nested section UI and schema in the editor section above. |
| **P2, store configuration** | Settings are spread across site overview, Merchant details & samples, Commerce, Go-live, integrations and operator-only credentials. No unified settings index for shipping zones/rates, tax registrations, notification templates and staff. | Site navigation (`app/site_routes.py:440`), shared settings (`:498`), commerce GET/POST (`app/commerce_routes.py:55`, `:99`), sample details (`app/site_sample_routes.py:12`), go-live actions (`app/site_golive_routes.py:193`). Record the existing US commerce fields rather than inferring a full shipping/tax engine. |
| **P2, discounts** | Discounts exist in backend voucher/offer records and checkout logic, but no merchant discount create/edit/schedule/usage administration. | Legacy promotions is GET (`app/main.py:651`), `Voucher` (`app/models.py:239`), customer offers (`app/models.py:721`), shopper cart discount POST (`app/store_checkout_routes.py:115`). |
| **P2, reporting** | Revenue/date reporting and GA4 consent-aware configuration exist, but no in-admin funnel, acquisition, product profitability, cohort or stock reporting workspace was found. | Site/platform revenue (`app/order_management_routes.py:405`, `:433`); merchant GA4 field and consent copy (`app/site_routes.py:505`); analytics verifier (`scripts/verify_analytics_browser.py:64`). |
| **P2, communication** | Inbox stores inbound contacts but lacks assignment, reply, conversation threads, status or resolution workflow. Mail queue exposure is operational status rather than a notification designer. | Inbox GET (`app/site_routes.py:854`), contact persistence (`app/models.py:599`), customer mail summary (`app/customer_routes.py:280`). |
| **P2, consistency** | Multiple admin shells and scopes create navigation ambiguity. Legacy tenant views, site tools, private demo and operator consoles use different information structures. Some operational copy is stale. | Legacy routes (`app/main.py:606`), grouped site navigation (`app/site_routes.py:440`), demo (`app/demo_routes.py:34`); catalogue still says checkout remains closed in Phase 1 (`app/site_catalog.py:112`) while dedicated checkout/order flows exist (`app/store_checkout_routes.py:199`, `app/order_management_routes.py:66`). |
| **P2, verification debt** | One customer browser verifier expects a removed shipment form, so its current merchant journey is inconsistent with the application. It should be updated before relying on that script as acceptance evidence. | `scripts/verify_customer_browser.py:98` targets `/admin/sites/{id}/shipments/{id}`; customer admin now links to dedicated Orders (`app/customer_routes.py:288`), whose fulfilment mutation uses `/orders/{id}/fulfillment` (`app/order_management_routes.py:222`). |

### Additional editor-specific gaps

These are high-impact editor constraints from static tracing; reproduce the add-section cases in an isolated instance before implementation.


| Impact | Finding | Evidence |
|---|---|---|
| High | Preview selection targets chat refinements; there is no inline visual text editing or drag-and-drop block arrangement in the classical editor. Merchants work through a long repeated form and explicit save. | `app/site_routes.py:670`, `app/site_routes.py:733`, `static/site-editor.js:1`, `static/site-preview.js:6` |
| Medium | Draft preview updates on server-rendered reload, not as each classical form input changes; publication is per page and a separate workflow from builder chat/design. | `app/site_routes.py:737`, `app/site_routes.py:819`, `app/site_builder_routes.py:217` |
| Medium | Editors see Publish/Unpublish controls that the server rejects because publish access is limited to admin/merchant. | `app/site_routes.py:735`, `app/content.py:76`, `app/content.py:118` |
| Medium | Localised JSON is supported, but the classical editor resolves and writes only the default locale and exposes no locale selector in the page form. | `app/site_routes.py:711`, `app/site_routes.py:722`, `app/site_routes.py:758`, `app/site_blocks.py:287` |


Existing safeguards worth retaining in an admin redesign: tenant membership checks, CSRF, optimistic versions, separate draft/public snapshots, revision restore/undo, reviewed AI proposals, safe media references, connector dry-run/apply plans, exact/idempotent refunds and simulator isolation. Their implementation and test anchors are listed in the preceding sections.

## 8. Audit verification and open questions

- **Changed:** only this current-state Markdown report. No application code, dependency, configuration or database changes were requested or made. No branch switch, commit, push or deployment.
- **Verified:** read source, enumerated admin route decorators using Python AST without imports, cross-checked route inventory against the report, checked file/line citation targets and ran `git diff --check`. Tests and browser scripts were inspected, not executed, because this was a read-only application audit.
- **Open questions:** which legacy/global surfaces should be retained in admin v2; whether catalogue ownership should remain tenant-wide across sites; which operational capabilities need first-class merchant screens; whether the US-specific commerce assumptions should become configurable. These are product decisions, not assumptions made by this audit.

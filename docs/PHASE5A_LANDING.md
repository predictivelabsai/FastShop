# Phase 5a: public marketing landing page

## Purpose

Phase 5a adds FastShop's first public SaaS marketing surface. It explains the
shipped product in plain language, gives prospective merchants one honest next
step, and stays structurally separate from both tenant storefronts and merchant
administration.

This slice does not add account creation, onboarding, plan enforcement, SaaS
billing, analytics, or lead collection. The signup destination is deliberately a
static placeholder until Phase 5b.

## Surface and route contract

The public surface is implemented in `app/marketing_routes.py` and registered
directly from `app/main.py`. It does not use `register_site_routes`, `site_ui`,
the merchant shell, tenant themes, site-builder assets, or any `Site` record.
Both pages use a minimal marketing-only document shell and
`static/marketing.css`.

Routes:

| Route | Method | Behavior |
| --- | --- | --- |
| `/marketing/` | GET | Always renders the public landing on the platform host. |
| `/signup` | GET | Renders the Phase 5b signup surface; the closed state collects no data and has no form. |
| `/` | GET | Renders the public landing when the request host matches `FASTSHOP_PUBLIC_URL`; other hosts retain their existing behavior. |
| `/demo`, `/demo/...` | Any existing storefront method | Maps to the legacy `fastshop-demo` routes on the platform host. |

The landing itself is state-changing-free (static GET). Signup, verification, and
resend are Phase 5b state-changing routes and carry their own CSRF and
Post/Redirect/Get flows. All pre-existing state-changing routes retain their

## Platform-root gate

`SiteHostMiddleware` applies two internal rewrites when the normalized request
host exactly matches the hostname in `FASTSHOP_PUBLIC_URL`:

1. `/marketing/` and `/signup` are shared platform paths. They bypass tenant
   hostname lookup, so rendering them never opens a database session.
2. `/` is internally rewritten to `/marketing/` in every environment, including
   development.
3. `/demo` is internally rewritten to `/`, while `/demo/...` has the leading
   `/demo` segment removed. For example, `/demo/products` maps to `/products`.

There is no landing-root feature flag or development exception. Requests for
`/` on other hosts retain their existing behavior, including the legacy demo on
an unbound host and the existing tenant-host lookup and rewrite on a bound
tenant hostname. The `/demo` mapping is likewise limited to the platform host;
`/demos` and `/demofoo` do not match it.

The legacy `/` handler is not changed. `/products`, `/cart`, `/checkout`, every
`/admin/*` route, and authentication retain their current routes and semantics.
The hook neither creates nor matches a tenant and does not change the published
site or commerce gates. On the platform host, the legacy demo storefront is
reachable at `/demo` and its existing subpaths can be reached below `/demo/...`.

For review, operators can still use `/marketing/` directly. No environment flag
is needed for launch: the production setting
`FASTSHOP_PUBLIC_URL=https://shop.fastsme.com` makes the landing live at that
root on the next deploy.

## Content structure

The landing uses one `h1` and a logical heading hierarchy:

1. A first viewport that states the concrete promise: from a short description
   of the business to a live, checkout-capable store. The primary action links
   to `/signup`; the secondary action jumps to the workflow explanation.
2. A three-step sequence: sign up, describe the business and generate a full
   draft site, then refine it in the visual builder and pass reviewed publish,
   domain, and commerce checks.
3. A commerce section covering Stripe checkout, sandbox-first testing, reviewed
   go-live, refunds, merchant order operations, and server-rendered revenue
   reporting.
4. A migration section naming Shopify Admin GraphQL, WooCommerce REST,
   WordPress REST plus WXR export, CSV import, and Google Merchant Center feed.
5. A CMS section covering block pages, navigation menus, reusable media, blog
   publishing, and controlled snippets.
6. A pricing teaser using the approved wording, “Simple plans based on sites and
   AI generations,” without invented plan names, quotas, or prices.
7. Six FAQ items that distinguish shipped behavior from Phase 5b–5e work.
8. A footer with only internal anchors, `/signup`, and the approved GitHub
   repository URL.

No customer logos, testimonials, third-party screenshots, analytics, scripts,
iframes, model calls, remote fonts, or other outbound page requests are used.

## Visual direction

The visual system treats the page as a reviewed production proof: warm paper,
dark ink, registration marks, compact annotations, decisive rules, and one
FastShop green accent. A large, authored store-building proof in the first
viewport shows the product mechanism with HTML and CSS rather than pretending
to be a customer screenshot. The composition shifts between dense annotated
proofs and quieter reading sections while retaining one grid and corner
language.

The surface uses a local system font stack. This matches the legacy UI's
no-remote-font behavior and guarantees zero font requests. It is intentionally
independent of tenant Quicksand assets and merchant/editor styles.

At 1440px the hero uses a two-column composition with a large proof surface. At
390px it becomes a single reading order, navigation collapses to essential
actions without JavaScript, comparison rows stack, tap targets remain at least
44px high, and no element depends on hover. Motion is limited to a subtle proof
registration shift and is disabled under `prefers-reduced-motion`.

Accessibility requirements include a skip link, semantic `header`, `nav`,
`main`, sections and footer, one `h1`, ordered heading levels, visible focus
states, descriptive link text, sufficient contrast, and no horizontal overflow.

## Content self-review: claim to shipped feature

Every marketing statement is constrained to repository behavior already shipped
in Phases 1–4.

| Marketing claim | Shipped basis | Wording boundary |
| --- | --- | --- |
| A short business description can produce a full draft site. | `docs/PHASE2A_SITE_GENERATION.md` documents brief-to-site plans with pages, blocks, menus, theme, copy, products, validation, and a private-draft result. | The page says “draft” and never claims autonomous publication, finished imagery, or guaranteed business results. |
| Merchants can refine generated work visually and review changes. | Phase 2's shared builder, block-diff preview, accept/reject, and undo workflow is documented in the product plan and Phase 2 implementation docs. | Copy keeps the merchant in control and does not imply that every edit is automatically correct. |
| A reviewed store can move through publish, domain, and commerce gates. | `docs/PHASE4A_GOLIVE.md` documents the reviewed publish transition, custom-domain binding, policy/catalog checks, and sandbox commerce gate; `docs/PHASE4B_LIVE_CREDENTIALS.md` adds explicit operator live-credential acceptance. | “Go live” is always paired with review and acceptance; no instant-live promise is made. |
| FastShop supports Stripe checkout with sandbox-first testing. | Phase 2 checkout/provider boundaries and Phase 4 acceptance documentation implement Stripe sessions and guarded payment modes. | Copy does not imply that live credentials are enabled by default or that FastShop stores card data. |
| Merchants can manage fulfillment, refunds, and revenue reporting. | `docs/PHASE4C_ORDER_MANAGEMENT.md` documents tenant-scoped order timelines, fulfillment transitions, exact idempotent Stripe refunds, and bounded integer-minor-unit revenue reports. | Copy names only these shipped operations and avoids unsupported forecasting or analytics claims. |
| Shopify migration uses Admin GraphQL. | `docs/PHASE3B_SHOPIFY_CONNECTOR.md` documents a pinned GraphQL Admin API connector, dry-run plan, and scoped application. | Copy says “import” and does not imply complete theme cloning or an unreviewed one-click cutover. |
| WooCommerce migration uses REST. | `docs/PHASE3A_WOOCOMMERCE_CONNECTOR.md` documents the REST connector and preview/apply framework. | Copy presents a reviewed migration path, not perfect compatibility with every plugin. |
| WordPress content can be imported through REST and exported as WXR. | `docs/PHASE3C_WORDPRESS_CONNECTOR.md` documents posts, pages, media, sanitized block conversion, and WXR 1.2 export. | Copy avoids claiming arbitrary plugin, shortcode, script, or theme fidelity. |
| CSV import and Google Merchant Center feed are available. | `docs/PHASE3D_CSV_MERCHANT_FEED.md` documents bounded CSV preview/apply and tenant-scoped product-feed export. | Copy does not promise scheduled delivery or automatic remote submission. |
| FastShop includes blocks, menus, media, blog, and snippets. | Phase 0/1 docs cover the block model, menus, media library, blog taxonomy/editorial flow, and consent-controlled snippets. | Copy describes the shipped CMS and does not claim a third-party plugin marketplace. |
| Plans will be based on sites and AI generations. | The approved product plan places plans, quotas, and metering in Phase 5d. | The section is explicitly a pricing preview: no price, quota, availability date, or purchasable plan is invented. |
| Signup is launching. | Phase 5b is the approved next slice. | `/signup` says that signup is not yet available and collects no visitor data. |

## Testing and browser evidence

`tests/test_marketing_landing.py` covers:

- direct landing rendering and required content;
- zero database-session access for `/marketing/` and `/signup`;
- the platform-host root gate on and off, including development precedence;
- the signup placeholder;
- absence of `script`, `iframe`, and analytics markup; and
- middleware isolation from a matching tenant hostname.

`scripts/verify_site_browser.py --landing` is a read-only browser mode. It visits
`/marketing/`, validates the heading and primary action, asserts no scripts,
iframes, analytics markers, failed requests, or horizontal overflow, and writes
desktop and mobile full-page screenshots to
`output/playwright/phase5a-landing/`.

## Verification results

Final verification ran on 2026-10-09.

- In one PowerShell session, with exactly the requested isolated environment
  variables and without setting `FASTSHOP_ADMIN_EMAIL`:
  - `uv run ruff check .` passed with exit code `0`.
  - `uv run python -m pytest -q` passed all `466` collected tests with
    exit code `0`.
  - The `[100%]` line contained `34` dots and no `F`, `E`, `s`, or `x`
    markers.
- `python -m compileall -q app scripts` passed with exit code `0`.
- `uv run alembic upgrade head` followed by `uv run alembic check` against a
  fresh isolated SQLite database passed; Alembic reported “No new upgrade
  operations detected.” Both commands exited `0`.
- `git diff --check` passed with exit code `0` (Git emitted only the existing
  working-copy LF-to-CRLF notices for modified Python files).
- `uv run python scripts/verify_site_browser.py --landing --base
  http://127.0.0.1:5033` reported `2` checks and `0` failures. It produced
  `desktop.png` at 1440px and `mobile.png` at 390px under
  `output/playwright/phase5a-landing/`; both captures were visually inspected.
  Both widths had no horizontal overflow, active script or iframe elements,
  analytics resources, failed requests, or outbound requests.
- The Impeccable detector reported no findings. The independent final design
  review returned `ship` with no material fixes.

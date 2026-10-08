# FastShop product plan — Lovable × Shopify × WordPress

Date: 2026-10-08. Status: approved direction (operator decisions recorded below).

## Vision

FastShop becomes a hosted, self-serve platform where a merchant describes their business in a prompt and gets a polished, publishable storefront with a full CMS and real commerce — the product sits at the intersection of Lovable (AI-first generation and refinement), Shopify (commerce depth), and WordPress (content management and extensibility).

Confirmed operator decisions:

- End product is a public SaaS (self-serve onboarding and billing come later, on top).
- The AI builder pillar is deepened substantially (prompt → complete site, chat refinement, accept/reject diffs).
- CMS pillar target is a full CMS (block pages, media library, menus, blog, multilingual-ready).
- Commerce pillar priority is the go-live path (live provider keys, domain binding, publish workflow), ahead of new commerce features.
- Integrations and migration are an approved parallel track, with migration connectors prioritized to feed merchant acquisition for Phase 5.

## What exists today (building blocks we extend)

- Multi-tenant data model, memberships, per-site settings; builder routes follow `register_*_routes(rt, actor, csrf, check_csrf, shell, error)`.
- Dual-flow site builder: classical forms + chat editing against a shared draft preview; design controls, section targeting, undo; no publish without confirmation.
- Existing commerce: products/variants/cart/vouchers/checkout/subscriptions (sandbox-only Stripe), PayPal option, tax/shipping config, customer account area.
- Existing content: section-based page editing, articles/blog pages, media upload, per-site theme (palette/fonts), SEO fields, cookie-consent-gated analytics.
- AI assistant surface (`app/ai.py`) with a model provider seam and chat evals under `output/evals/`.
- Hard guarantees that must not regress (see AGENTS.md): integer minor currency units, tenant scoping on every owned query, idempotent checkout, PK-ordered inventory locks, sandbox-only providers by default, no cross-schema writes.

## Roadmap

### Phase 0 — Content model foundations (unblocks everything)

Convert the section payload model into a first-class block content model that both the visual editor and the AI builder can target programmatically; add a navigation-menu model; make every content field locale-keyed from day one (single default locale to start; multi-locale UI later).

Key work:

| Area | Scope |
| --- | --- |
| Block content | Block schema + migration of existing section payloads (H2 4 You included). |
| Navigation | Menu model. |
| Localization | Locale-keyed content fields. |
| Regression coverage | Regression harness. |

#### Acceptance criteria

- Existing seeded sites render pixel-identically (Playwright evidence under `output/playwright/`).
- Full test suite green.
- No visible editor behavior change.

### Phase 1 — CMS (the WordPress pillar)

- Block-based page authoring: hero, rich text, columns, image, gallery, quote, CTA, product grid, testimonial, FAQ, code/embed (sanitized); reorder, duplicate, per-block visibility.
- Media library: browse/reuse across pages, alt text (SEO), replacement, per-site scoping (uploads exist; make them reusable and managed).
- Menus/navigation builder bound to the menu model.
- Blog depth: categories, tags, drafts, authorship.
- Extensibility (light now): site-scoped head/foot snippet injection, sanitized embeds.

#### Acceptance criteria

- A complete marketing page + blog post can be built end to end with no code.
- Screenshots desktop + mobile.
- Suite green.

### Phase 2 — AI builder (the Lovable pillar)

- Brief → site generation: one prompt produces a full site plan (pages, palette, fonts, navigation, imagery direction) and a populated draft site with generated copy and product seeds.
- Block-level refinement: chat edits resolve to scoped block diffs with preview and accept/reject; undo and revision history stay coherent.
- Image pipeline: generation supplies imagery direction and placeholders now; a pluggable seam for image generation/stock sourcing later.
- Evals: extend the existing chat-eval harness with a golden-brief suite (e.g. 10 canonical prompts) scored for structure, copy, and safety (compliance guardrails, banned-claim checks pass).

#### Acceptance criteria

- A new merchant goes from prompt to a coherent, publishable-ready draft in under 5 minutes.
- Eval suite committed and passing.
- Guardrails hold.

### Phase 3 — Integrations & migration (the ecosystem pillar)

#### Connector framework

- Extend the provider adapter pattern in `app/integrations/`; Stripe and a read-only WooCommerce adapter already exist there.
- Store per-site credentials using the Stripe per-site env-prefix scheme; sandbox-first by default. Secrets are operator-only, never merchant or shopper input.
- Every import runs as a dry-run preview before any write. Imports are additive, idempotent, and tenant-scoped.

#### Connectors

| Connector | Scope | State |
| --- | --- | --- |
| WooCommerce (full) | Full REST API connector: import products, categories, orders, and customers; export FastShop catalog and orders back to WooCommerce; one-click migration path. | Read-only stub exists; replace with the full connector. |
| Shopify | OAuth Admin API app-install flow; product, order, and customer import; content and theme-direction mapping; documented "switch from Shopify" migration path. | Planned. |
| WordPress | Import posts, pages, and media through the WP REST API; export FastShop CMS content as WordPress-compatible WXR XML for portability. | Planned. |
| Generic / adjacent | CSV catalog import/export, Google Merchant Center product feed, and webhooks/events export channel. | Planned. |

#### Acceptance criteria

- Each connector ships a dry-run report with counts, warnings, and unmapped items.
- Each platform connector has a verified end-to-end import against a live sandbox account of that platform.
- Suite green.

### Phase 4 — Commerce go-live (the Shopify pillar)

- Per-site live credential flow: move from env-prefix-only to operator-approved credentials with live-mode guards that stay hard until explicit acceptance; never accept shopper or merchant input as credentials.
- Publish workflow: review/publish transition per site, commerce enablement gate, domain binding path (custom hostname + TLS guidance since TLS terminates at the platform).
- Merchant dashboards: tax/shipping configuration UI polish, order management depth (fulfillment status, refunds), per-site revenue reporting (server-side, no analytics on checkout pages). → **Done: phase 4c order fulfillment + refunds (PR #15).**
- Webhooks: live-mode reliability (retries, reconciliation, idempotency already exists in the outbox pattern). → Phase 4d.

#### Acceptance criteria

- An operator can take a reviewed site through publish → domain → commerce enablement with a documented checklist.
- Real-provider acceptance still requires provider credentials and stays a separate gate by design.

### Phase 5 — Public SaaS (market and sell the product itself)

Slice order matters here: 5a–5c create the funnel, 5d–5e make it a business. Slices 5d–5e build on 5b (they need provisioning and account state), so keep that dependency order.

#### 5a — Public marketing landing page

- Customer-facing marketing site for FastShop itself, in the spirit of Shopify's or Lovable's landing pages, at `shop.fastsme.com` (URL to be confirmed with the operator before launch, so the page must also serve correctly under a preview hostname during review).
- Entirely separate surface from the app and from every tenant site: served under its own route prefix/host path (e.g. `/marketing/…` or a dedicated host), never inheriting tenant `Site` records, never reachable through the site-builder editor, and never part of `SiteHostMiddleware` tenant matching.
- Content pillars: the product promise (Lovable's brief-to-store generation), the commerce depth (Shopify's checkout and go-live), and the WordPress migration/connector story; feature sections, screenshots/demos, pricing teaser → 5d, CTA linking into 5b signup.
- Static, server-rendered, no AI model calls, no tenant data; Playwright desktop + mobile evidence required like any UI change.

#### 5b — Self-serve signup and provisioning

- Public signup form (email + password or Google OIDC) on the FastShop SaaS surface → creates tenant + user + membership + first site in one transaction; zero operator intervention.
- Provisioning reuses the existing seeding path end to end so new tenants get the same working defaults as seeded demo tenants.
- Hostname reservation policy: each new site needs a bounded unique slug-based preview hostname; custom domains remain the Phase 4a reviewed binding.
- Abuse/migration safety: signup rate-limiting per IP and per email, no secrets in any new surface, confirmation email through the existing transactional-mail queue.

#### 5c — Onboarding wizard (signup → generated store)

- The Lovable-style funnel: after signup, a short guided brief (business description, what they sell, look-and-feel direction) → Phase 2 AI generation produces the site plan + draft pages + product seeds → merchant lands directly in the builder with a publishable-ready draft.
- Wizard is skippable: a merchant can choose a blank template and build manually (the CMS builder must stay the source of truth).
- Wizard must be idempotent and resumable across sessions; generation failures never strand the account — the wizard falls back to a template with the brief saved for retry.

#### 5d — Plans, quotas, and metering

- Plan model per tenant (e.g. free / basic / pro): site count, AI-generation credits, publish limits, product count; enforced at the service boundary, not just the UI.
- Metering built on the existing `OutboxEvent`/event patterns; operator console view of per-tenant usage.
- Quota enforcement errors are merchant-readable and never block reading their own data.

#### 5e — Stripe SaaS billing for FastShop itself

- Stripe Checkout subscriptions for plan upgrades on the FastShop SaaS surface; separate integration from store payment acceptance — FastShop-as-merchant credentials, never a tenant's live credentials, never `SiteStripeLiveCredential`.
- Webhooks for subscription lifecycle (trials, failures, cancellations) reconcile plan state; dunning via the transactional-mail queue.
- Sandbox-first: billing runs in test mode end-to-end before any live-mode acceptance, reusing the live-credential ceremony pattern only for the platform's own credentials.

#### Acceptance criteria

- A new merchant can sign up and reach the generated-site editor without any operator intervention.
- Quotas enforce; billing state and plan enforcement agree.
- The landing page, signup, and wizard are all covered by Playwright desktop + mobile evidence and the full suite.

## Sequencing and rationale

0 → 1 → 2 is strict: the CMS model is the substrate for AI generation. Phase 4 follows the high-value AI work and delivers the higher-risk commerce go-live path. Phase 5 follows 4d: the funnel (5a marketing page → 5b signup → 5c wizard) ships before 5d–5e, which depend on 5b's provisioning and plan state.

Phase 3 (integrations) is a parallel track: it touches different files than Phases 4–5 and can proceed concurrently with them once the connector framework lands. Prioritize the Shopify, WooCommerce, and WordPress migration connectors because they feed merchant acquisition for Phase 5.

Each phase ships as independent, reviewable slices within it — no big-bang integration at phase end.

## Standing constraints

All phases respect: tenant scoping, sandbox-first provider policy (live acceptance is an explicit gate, never a default), integer money, no AI attribution anywhere, tests green and CI-ordered checks before release, Playwright evidence for UI changes.

# H2 4 You Phase 1 site-builder plan

Status: proposed implementation plan; no Phase 2 commerce work is authorised by
this document.

## Decision

Build the H2 4 You site as the first complete site made with a reusable FastShop
storefront theme, and add an embedded CMS/site editor to FastShop. H2 4 You is a
tenant-owned configuration/content pack, not a generic template that new
merchants would accidentally clone with its branding or regulated claims.
Borrow the proven concepts and selected MIT-licensed implementation ideas from
FastCMS, but do not run FastCMS as a separate service or database.

The product goal is the point where Shopify and WordPress converge:

- FastShop owns structured commerce: products, variants, channel prices, stock,
  carts, customers, and later checkout and subscriptions.
- The embedded CMS owns presentation: sites, pages, sections, navigation, media,
  reusable content, SEO, drafts, previews, and publishing.
- A theme and section registry joins the two. Editors arrange typed sections,
  while product and collection sections read live commerce records rather than
  copying product data into page content.

This intentionally supersedes the brief's Shopify-only platform instruction.
If the deliverable must instead be a literal Shopify Online Store 2.0 store,
this repository is the wrong implementation target and the build should be a
Liquid theme. Shopify's JSON templates and sections already provide the
appropriate editor in that case:

- <https://shopify.dev/docs/storefronts/themes/architecture/templates/json-templates>
- <https://shopify.dev/docs/storefronts/themes/architecture/sections>

## Why not deploy FastCMS alongside FastShop

FastCMS has the right interaction patterns:

- hierarchical pages and URL resolution;
- typed page kinds, composable blocks, and reusable snippets;
- drafts, revisions, preview, publish, unpublish, and scheduling;
- a media library with alt text, focal points, and renditions;
- forms, SEO fields, a page explorer, and an editor-friendly admin.

Its current persistence model is not safe to use unchanged for a FastShop SaaS
storefront. It uses fastlite and SQLite, while FastShop uses SQLAlchemy,
PostgreSQL, and Alembic. Pages, media, snippets, URL lookup, and setting lookup
are effectively global; `site_id` is not consistently used; there are no
tenant ownership keys on core content; and the current generic raw-HTML/embed
path is too permissive for untrusted merchant editors. A separate deployment
would also create duplicate authentication, user, domain, publishing, backup,
and operational models.

Port behavior and UI ideas, not the database tables or global module state.

## Phase boundary

Phase 1 delivers a complete, reviewable design-and-content site plus the editor
needed to maintain it. Phase 2 must not begin until the user reviews and accepts
the Phase 1 staging result.

Phase 1 includes:

- tenant/site creation and selection;
- theme settings, a reusable neutral storefront theme, and an H2 4 You preset;
- page/section editing, media, navigation, SEO, preview, revision, and publish;
- the H2 4 You home, shop, two collection views, two product views, Science,
  Learn index, three articles, About, Contact, and placeholder legal pages;
- editable header, footer, announcement, consent banner, claims, citations, and
  company details;
- responsive and accessible storefront rendering at mobile, tablet, and desktop;
- a working contact form with persistence and a configurable email adapter;
- placeholder media and a generated placeholder/assumption report;
- product/flavour/purchase controls rendered from real catalog records, but with
  purchasing gated off on the Phase 1 staging site.

Phase 1 does not collect marketing email, issue discounts, take payment,
calculate US tax, buy shipping, create subscriptions, or expose production
customer/order tracking. The first-order promotion may be previewed visually,
but its form and discount behavior remain behind a disabled feature flag.

Phase 2 will cover those commerce capabilities only after the Phase 1 review.
This resolves the brief's overlap where header/product designs contain account,
cart, subscription, and add-to-cart controls even though commerce is assigned
to Phase 2.

## Target architecture

Keep a single FastHTML/FastAPI modular monolith and one PostgreSQL database. Add
four boundaries beneath `app/`:

```text
app/
  storefront/    request context, public routing, theme rendering
  content/       pages, revisions, sections, snippets, navigation, compliance
  media/         assets, validation, renditions, storage adapter
  merchant/      site setup, page editor, preview, settings, publishing
```

Do not move the existing commerce services during Phase 1 unless a move is
required to establish request-scoped tenant/site/channel resolution.

### Request and ownership model

Add a `Site` that belongs to a FastShop `Tenant` and points at its default
`Channel`. Resolve it from a validated hostname in production and from an
explicit development-site override locally. Build a `StorefrontContext` once
per request containing `site`, `tenant`, `channel`, locale, currency, theme,
and the current membership.

Remove the seeded `TENANT_SLUG`/`CHANNEL_SLUG` lookup from public and merchant
queries. Every owned repository method must receive the context or explicit
ownership identifiers. Every owned table gets `tenant_id`; site-specific
content also gets `site_id`. Composite uniqueness must include the appropriate
owner, for example `(site_id, path)` and `(tenant_id, slug)`.

The first onboarding flow is:

1. Create or select an organisation.
2. Create a site with name, generated development hostname, locale, market, and
   currency.
3. Select a neutral starter template (`editorial-commerce` is the first theme;
   H2 4 You is its first configured site and design-token preset).
4. Seed neutral draft pages and menus, then configure theme settings and
   catalog-to-page assignments. The H2 4 You seed is restricted to its tenant.
5. Enter the visual editor, preview, and publish.

Custom-domain verification can be a later deployment task; the data model and
host resolver must support it now.

### Core content models

Use SQLAlchemy models and Alembic migrations, with UUID identifiers consistent
with FastShop:

- `Site`: tenant, default channel, name, slug, canonical host, locale, timezone,
  theme key, status, and commerce-enabled flag.
- `SiteDomain`: hostname, verification/status, canonical flag, and site owner.
- `Page`: site/tenant owner, parent, kind, path, slug, title, menu visibility,
  SEO fields, publication state, and published revision pointer.
- `PageRevision`: immutable snapshot of section data and page metadata, author,
  note, created time, and publish time.
- `Navigation` and `NavigationItem`: typed internal/external links, hierarchy,
  label, and order.
- `Snippet`: site-owned, typed reusable content with a stable key, JSON data,
  revision/status fields, and schema version.
- `MediaAsset`: owner, storage key, media type, dimensions/duration, size,
  alt text, focal point, placeholder flag, attribution, and replacement note.
- `ContactSubmission`: site, page, submitted data, consent fields, delivery
  status, timestamps, and minimal anti-spam metadata.
- `ProductPageAssignment`: product-to-template/page settings without duplicating
  product name, variants, price, or availability.

Store section lists in revision JSON, validated against versioned Pydantic
schemas. This preserves flexible layouts while keeping the public renderer
safe, testable, and migratable. Do not enable merchant-authored raw HTML or
arbitrary JavaScript.

### Theme and section system

Themes are code-owned renderers plus editable design tokens. The initial
`editorial-commerce` theme supports a tenant-owned `h24you` preset that defines
the off-white/aqua/sand palette, typography fallbacks, spacing, controls, motion
rules, header states, and responsive breakpoints.
Lovera remains an explicitly replaceable placeholder until a licensed font file
is supplied; Quicksand must be self-hosted or loaded under a documented licence.

Each section type has:

- a stable type and schema version;
- editor label and field schema;
- validation and sensible limits;
- public renderer;
- optional commerce-resource requirements;
- accessibility and performance behavior;
- migration support when its schema changes.

Phase 1 section types:

- hero video/image with desktop/mobile sources, poster, pause control, and CTA;
- editorial rich text and media/text split;
- featured products and featured collections backed by commerce IDs;
- science counters backed by a controlled facts snippet;
- claim/benefit group backed by approved claim IDs;
- research library and theme filter;
- article grid/latest articles;
- gallery and product story media;
- accordions/FAQ;
- reviews placeholder with an unmistakable sample label;
- team profiles;
- contact details and contact form;
- announcement/promotion presentation;
- newsletter presentation, inactive until Phase 2;
- legal content and disclaimer;
- spacer/divider only where editorial pacing requires one.

The editor may add, remove, duplicate, hide, and reorder allowed sections. Page
kind controls the allowed section palette so an article does not accidentally
become a product or checkout page.

### WordPress-like content, Shopify-like resources

Use page kinds for `home`, `content`, `science`, `blog_index`, `article`,
`contact`, and `legal`. Use resource templates for `collection` and `product`.
The resource renderer combines a live `Product` or `Category` with editable
sections. Price, variants, and availability always come from commerce tables;
marketing copy, galleries, FAQs, and supporting sections come from a published
page revision.

Public routes for the H2 4 You site:

```text
/
/shop
/collections/hydrogen-tablets
/collections/hydrogen-water-bottles
/products/hydrogen-tablets
/products/hydroxy-go
/pages/science
/blogs/learn
/blogs/learn/{article-slug}
/pages/about-us
/pages/contact
/pages/terms-and-conditions
/pages/privacy-policy
/pages/faq
/pages/returns-and-refunds
```

The router must resolve system/resource routes before the CMS catch-all, use the
published revision only for public requests, and provide a signed preview route
for drafts. Canonical URLs, sitemap entries, robots behavior, Open Graph data,
and structured data derive from the resolved site and resource.

## Compliance as a product feature

The brief's wording rules should not rely only on editorial memory.

- Store benefit statements as `regulated_claim` snippets with a stable key,
  text, approval state, reviewer/date, and required disclaimer relationship.
- Claim sections reference keys instead of copying claim text. Disabling a claim
  removes it everywhere.
- In preview, draft claims are visibly marked. The production publish validator
  blocks unapproved claims and missing same-view disclaimers.
- Store science statistics as controlled facts with value, display suffix,
  source URL, source label, and checked-as-of date. Publishing is blocked while
  the date is still a placeholder.
- Research entries require title, source/journal, year, neutral summary, theme,
  and an external source URL.
- Articles require a non-empty `Studies referenced` list. Seed only the sources
  named in the brief or separately verified source material.
- Media and review records have an explicit `is_placeholder` flag. A launch
  readiness report lists and blocks unresolved placeholder facts, prices,
  ingredients, people photos, product media, reviews, and legal copy.
- Add automated forbidden-phrase checks as a warning, not as a substitute for
  the required US regulatory review.

## Phase 1 implementation order

### 1. Architecture and migrations

- Record an ADR for embedded CMS versus separate FastCMS and for the revised
  interpretation of the Shopify-only brief.
- Add the site/content/media models and reversible Alembic migration.
- Introduce repositories/services with mandatory tenant and site scope.
- Add tests that prove one tenant cannot read, preview, edit, publish, or delete
  another tenant's content or media.

Exit: migrations upgrade cleanly; tenant-isolation and ownership tests pass.

### 2. Site resolution and onboarding

- Add hostname-to-site resolution and `StorefrontContext`.
- Replace hard-coded seeded tenant/channel selection in storefront paths.
- Add merchant site switcher and create-site wizard.
- Make H2 4 You a repeatable tenant seed/fixture rather than special-case code;
  do not expose its brand content as the default template for other merchants.

Exit: two seeded sites render different settings/catalog/content in the same
process, and a merchant can create a third draft site from the UI.

### 3. CMS services and safe rendering

- Port/adapt FastCMS page tree, revision, preview, publish, snippets, media, and
  block-registry concepts to SQLAlchemy services.
- Implement typed section schemas, schema versions, sanitised rich text, safe
  external links, trusted video sources, and responsive media renditions.
- Add page-kind/resource routing and SEO output.

Exit: an editor can create a draft page, reorder sections, preview it, publish
it, restore a prior revision, and see the correct public revision.

### 4. Merchant editor

- Replace the current placeholder `Content & menus` screen with Pages, Theme,
  Navigation, Media, Reusable content, Compliance, and Forms.
- Build a split editor/preview workflow using server-rendered FastHTML and HTMX.
- Add focused controls rather than a free-form canvas: section chooser, ordered
  section list, contextual fields, media chooser, save draft, preview, publish,
  and revision history.
- Add a launch-readiness panel for missing settings and placeholders.

Exit: normal H2 4 You copy, page order, imagery, claims, company address, footer,
and menu changes require no code deployment.

### 5. H2 4 You theme and content

- Implement the accessible header, shop dropdown, mobile drawer, transparent to
  solid hero state, footer, announcement, cookie preferences, and feature-gated
  first-order presentation.
- Build all Phase 1 page compositions from registered sections.
- Seed the two products and three tablet variants in USD integer minor units.
- Draft the three 600–900 word articles with only verified citations.
- Adapt source material without copying layouts or unsupported claims.
- Generate visually coherent placeholder media, with `placeholder` in storage
  names and alt/replacement metadata.
- Keep company address, contact details, social links, FDA disclaimer, claims,
  science counters, and science source line in reusable records.

Exit: all requested routes look complete on the staging site and every factual,
legal, media, review, ingredient, specification, and pricing placeholder appears
in the readiness report.

### 6. Contact, consent, accessibility, and performance

- Persist contact submissions and deliver through a configurable email adapter;
  never lose the submission only because email delivery fails.
- Add CSRF, honeypot/rate limiting, validation, and a delivery retry state.
- Implement Accept, Decline, and Preferences with functional consent categories.
  No analytics or marketing adapter may execute before matching consent.
- Respect reduced-motion preferences, provide video pause controls, visible
  focus, keyboard navigation, semantic landmarks, and useful alt text.
- Provide desktop/mobile video sources, poster fallback, lazy loading, responsive
  image renditions, and asset budgets.

Exit: contact delivery behavior, consent gating, keyboard journeys, and core
performance budgets pass automated or documented checks.

### 7. Phase 1 review gate

- Run Ruff, pytest, compile checks, Alembic upgrade validation, and
  `git diff --check`.
- Run Playwright journeys for anonymous visitor and merchant editor.
- Capture at least home, shop, both product pages, Science, Learn/article, About,
  Contact, menu, cookie preferences, and editor/preview states at mobile, iPad,
  and desktop sizes under `output/playwright/h24you-phase1/`.
- Produce the brief's editing guide, assumptions list, placeholder list, and
  launch-readiness checklist.
- Present the staging URL and screenshots for approval, then stop.

Exit: explicit user acceptance of Phase 1. No Phase 2 work starts implicitly.

## Phase 1 acceptance criteria

- A merchant can create a site from a template and edit its pages without code.
- H2 4 You is data/configuration inside that system, not hard-coded branches in
  shared storefront components.
- Header, footer, navigation, company details, claims, counters, and reusable
  disclaimers are edited once and reflected everywhere they are referenced.
- Draft edits never leak publicly; preview is authorised and revisions restore.
- Product/collection sections read tenant-scoped commerce data and use USD minor
  units for H2 4 You.
- Every requested Phase 1 page and legal placeholder has a clean canonical URL,
  title, meta description, heading structure, and sitemap behavior.
- Science and Learn have no buy buttons within research lists.
- Placeholder reviews cannot be mistaken for customer reviews.
- No analytics/marketing code loads before consent.
- There is no cross-tenant content, media, navigation, preview, or catalog leak.
- The mobile, iPad, and desktop screenshot set exists and has no serious console,
  network, overflow, keyboard, or contrast defect.
- The readiness report identifies the unconfirmed science date, final price and
  pack size, ingredients/Supplement Facts, bottle specifications, legal copy,
  address, licensed Lovera font, social URLs, real imagery/video, and reviews.

## Phase 2 outline (not yet authorised)

After Phase 1 acceptance:

- enable cart and checkout for the site and integrate the selected US-capable
  payment provider;
- configure US shipping zones/rates and a tax calculation provider;
- implement tablets-only monthly subscriptions and customer self-service;
- add discounts and first-order email capture with explicit consent;
- add customer accounts, order/fulfilment tracking, transactional email, and
  retry-safe provider webhooks;
- preserve integer minor units, stable inventory lock ordering, idempotent
  checkout commands, and strict tenant scope;
- repeat full desktop/mobile commerce journeys before any live-payment switch.

Provider choices cannot be inferred from the Shopify brief. Native Shopify
checkout, Shopify Email, Shopify Subscriptions, and Shopify Analytics do not
exist in a custom FastShop implementation. The brief excludes Stripe from footer
icons, not necessarily from backend payment processing. Payment, tax, shipping, subscription, marketing-email, analytics, and
review providers therefore require explicit Phase 2 decisions.

## Decisions needed before implementation

Only decisions that materially affect Phase 1 should block Phase 1:

1. Confirm that the FastShop embedded-CMS direction supersedes the Shopify-only
   instruction and Shopify-specific URLs/integrations are behavioral references.
2. Confirm the Phase 1 staging behavior for Phase 2 surfaces. Recommended:
   render and test account/cart/subscription/promotion states for design review,
   but disable purchase, account creation, and marketing-email submission until
   Phase 2. No control should be a misleading dead link.
3. Confirm whether one tenant may own multiple sites at launch. The recommended
   model supports it even if the initial UI defaults to one.
4. Supply a licensed Lovera font file or accept a clearly documented fallback.
5. Confirm the founder identity/source material that may be adapted for About;
   do not infer a person from the Hydroxy site.
6. Choose the Phase 1 contact-email adapter or accept database capture plus an
   adapter-disabled staging warning.

Payment and other Phase 2 provider choices should be deferred until the Phase 1
review so they do not contaminate the design/content milestone.

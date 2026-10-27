# Commerce and marketing widget pack

Extend the existing merchant editor and themed storefront with featured
collections, pricing plans, comparison tables, trust badges, announcement bars,
promotional banners, steps, approved-review aggregates and contact cards.
Preserve the registry, editor, progressive enhancement and container patterns of
packs 1 and 2. PRODUCT.md and DESIGN.md remain the product and design references.

Definitions in app/site_widgets_pack3_definitions.py are the single source of
defaults, categories, descriptions, icons, editor fields, repeater schemas and
constraints. Renderers live in app/site_widgets_pack3.py. Conditional
static/widgets-pack3.css and static/widgets-pack3.js assets support nested
containers and initialise again after editor preview replacement.

Featured collections use the site's real catalogue helpers, tenant and channel
scope. A selected published collection shows its title, optional description and
image, at most 12 active products and a View all link. Grid and internally
scrolling row layouts are supported. Existing listing and money helpers format
integer minor-unit prices. Missing or unpublished selections produce a preview
placeholder and no broken public content. Demo commerce fixtures are never a
catalogue source.

Pricing tables have 1-4 plans. Each plan has a name, merchant-entered price text,
description, up to 12 newline-separated features, button label and link, optional
highlighting and badge. Price text is presentation copy and is never parsed as
money. Plans stack on mobile. Comparison tables support at most 4 options and 20
features with yes, no or text cells. Real table headers, accessible Included and
Not included labels, a sticky feature column and internal mobile scrolling
preserve meaning without causing page overflow.

Trust badges contain 2-6 entries using the application-owned pack 1 icon set,
headings and short text. Row and grid layouts use honest, editable placeholders
such as Your delivery promise. No delivery, payment or refund guarantees are
invented. Steps use a numbered ordered list with 2-8 heading/body/optional-icon
entries, desktop connectors and a vertical mobile layout.

Announcement bars are ordinary merchant-placed blocks with 1-3 linked or plain
messages and a colour scheme. Multiple messages rotate only when motion is
allowed. Rotation has a pause control and honours reduced-motion changes. All
messages remain readable without JavaScript. Optional dismissal uses guarded
localStorage access keyed by site and block, with no dependence on storage
availability.

Promotional banners contain heading, body, image, button and link plus an optional
existing discount code. The editor offers only the current site's active codes
through a tenant-scoped, read-only lookup. Rendering rechecks code activity and
hides inactive codes. Copy code uses the Clipboard API, falls back to text
selection and announces the outcome in a live region. This widget never creates
or modifies discounts.

Star ratings aggregate approved real reviews for a chosen product or the whole
site through the approved-review helpers. Averages have one decimal place and
include a count and accessible sentence, for example Rated 4.6 out of 5 from 23
reviews. Merchants cannot type a rating number. This restriction prevents
fabricated ratings. With no approved reviews there is no public rating and the
editor explains the empty state. An optional reviews link is supported.

Contact cards use address semantics and contain a heading, address, validated
telephone and email links, up to 7 labelled opening-hour lines and an optional
HTTPS map link. Widget controls meet 44px touch targets, use theme variables,
support inline editable text hooks and remain readable without JavaScript.

The existing pack 2 alert is also aligned to the shared widget width and gutter.
Its isolated browser screenshots are refreshed. No database migration is needed.
Generation, refinement and the LLM adapter accept documents containing the new
widgets without expanding AI features.

Acceptance includes strict defaults and range/item/contact/link validation,
tenant and channel isolation, resource ownership, no demo fixture leakage,
approved-review-only ratings, inactive-code hiding, integer money formatting,
editor schema metadata and CSRF-protected draft/save/publish round trips.
scripts/verify_widgets_pack3_browser.py seeds a disposable real catalogue,
collection, approved reviews and active code, drags a new widget, inserts every
widget and publishes. It captures the category picker, desktop/mobile storefront,
highlighted pricing, internally scrolled comparison, rotating and dismissed
announcements, copied code with live feedback, steps, rating, contact card and
mobile no-JavaScript output under output/playwright/widgets-pack3/. It asserts no
console errors and no page overflow at 390px or 360px. Pack 2 verification runs
again. Release checks use isolated SQLite: ruff, compileall, Alembic upgrade,
full pytest and git diff --check.

Excluded: migrations, new AI operations, discount mutation, deployment, PR
creation, unrelated application changes and the existing rebrand-audit and
remove-customer-login screenshot directories.

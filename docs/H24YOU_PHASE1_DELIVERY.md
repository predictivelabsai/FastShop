# H2 4 You Phase 1 delivery and review register

## Scope

This is the FastShop embedded-CMS implementation of the H2 4 You design/content
brief. It uses the brief's Shopify interaction patterns, not Shopify hosting or
Shopify services. FastCMS's section editing and revision concepts informed the
architecture; its separate database and authentication system were not embedded.

The preview is `/sites/h24you/`; merchant editing starts at `/admin/sites`.
The existing FastShop homepage and demo commerce routes are preserved separately.
No h24you.com DNS or domain attachment is changed by this release.

Delivered page inventory: home, Shop, two collections, two products, Science,
Learn index, three Learn articles, About, Contact, Terms, Privacy, FAQ and Returns
(17 routes). Shared header, footer, announcement, offer preview and cookie
preferences are included. Unapproved claims are disabled. Research lists contain
source links rather than purchase controls.

The builder includes neutral site creation, tenant-owned catalog presentation,
section editing/reordering/removal, media upload, editable galleries and article
covers, draft preview, publication snapshots, revisions and restoration. Publishing
a site's first page exposes its noindex preview URL; it is not private sharing.

## Placeholder register

| Asset/content | Location | Replacement required |
|---|---|---|
| Water hero photograph | `static/h24you/water-placeholder.webp` | Approved hero image |
| Tablet packaging concept | `static/h24you/tablets-placeholder.webp` | Actual packaging and product photographs |
| Bottle concept | `static/h24you/bottle-placeholder.webp` | Approved branded Hydroxy Go photograph/mock-up |
| Drinking lifestyle concept | `static/h24you/ritual-placeholder.webp` | Approved lifestyle imagery |
| Desktop/mobile water motion | `water-placeholder.mp4`, `water-mobile-placeholder.mp4` | Final hero video |
| Dissolving/drinking motion studies | `dissolving-placeholder.mp4`, `drinking-placeholder.mp4` | Actual dissolving/drinking video; current clips animate still images |
| Team portraits | About section labelled photo panels | Founder and Andres Randma photos, replaceable in editor |
| Reviews | Home/product review sections | Genuine reviews; no fabricated testimonials supplied |
| Address | Central site setting | Confirm legal address |
| Tablet price | Catalog, USD 29.95 / 2995 cents | Confirm price and pack size |
| Bottle price | Unconfirmed; no selling price set | Supply USD retail price; source EUR price is not converted implicitly |
| Supplement Facts/ingredients | Tablet accordion | Final label information |
| Research counters/date | Central facts settings | Recheck all three figures and confirm date before launch |
| Benefit statements | Central claims settings, disabled | Adviser approval before enabling |
| Legal pages and shipping note | Legal/FAQ pages and products | Approved US-facing wording and confirmed shipping terms |
| Social destinations | Central social settings | Instagram/Facebook URLs |
| Heading font | Georgia fallback | Licensed Lovera file if desired |
| Payment methods and offer | Footer/banner, clearly preview/planned | Verify supported methods and activate only in Phase 2 |

Images are generated visual concepts, not photographs of the actual products or
customers. Supplied dark/light SVG logos are used. Quicksand is self-hosted with
its OFL licence. Original generated PNGs are retained outside the repository in
the session's generated-image output; optimized WebP assets are committed.

## Phase boundary and outstanding merchant decisions

Contact submissions are stored and sent through the configured Postmark adapter;
missing configuration or failed delivery leaves a visible inbox status. Marketing
email is not collected. Consent controls store choices, but no GA4 or Meta scripts
are installed. Installing analytics requires an explicit consent-aware integration.

Cart, checkout, subscriptions, discounts, customer accounts, tracking, tax and
shipping calculations remain Phase 2. Existing legacy commerce code is not wired
into these preview storefronts. The offer's two discounts need a stacking decision
(19% sequential versus 20% additive) before implementation.

The research figures and benefit statements are supplied drafts, not a claim of
regulatory approval. The three Learn articles require the merchant's editorial
review. Source links are drawn from the brief; for example the Zhou review's
journal metadata was verified against
[PubMed](https://pubmed.ncbi.nlm.nih.gov/36819697/).

## Verification and editing

`scripts/verify_site_browser.py` exercises all 17 routes at desktop, iPad and
mobile sizes, captures full-page screenshots after lazy images load, and checks
page headings, canonicals, alt text, overflow, research filters, cookie controls,
mobile navigation, gallery interaction and disabled purchase controls. Its local
merchant mode creates another site, edits/publishes a page and uploads media.
Artifacts are under `output/playwright/h24you-phase1/`.

Automated tests cover draft isolation, revisions, stale editor protection,
tenant ownership, unsafe URLs, media ownership/alt text, domain routing, contact
failure persistence, CSRF and rate limiting. These checks are not a substitute
for the merchant's content/regulatory review or a Phase 2 payment acceptance test.

See [the editing guide](PHASE1_MERCHANT_GUIDE.md) and
[Phase 2 implementation roadmap](PHASE2_COMMERCE_ROADMAP.md).

# H2 | 4 YOU Liquid Metal, milestones 1, 2 and 3

## Delivered scope

`/sites/h24you-liquid/` and `/sites/h24you-liquid/shop` render through FastShop's
multi-site routes. Home follows MetalPage's order: hero, science figures,
collection, benefits with the exact FDA disclaimer, sample reviews, then Learn.
Shop contains the heading, three product cards, three choice cards and assurances.
Both share the offer bar, sticky header, desktop dropdowns, mobile menu and footer.

The new site has its own `h24you-liquid` tenant and US/USD channel, preview status,
English default locale and sandbox commerce. The existing `h24you` fixture is
unchanged. M2 adds the three product pages described below. M3 completes the remaining
reference routes. No production credentials, deployment or migrations were added.

## Architecture and editing

`app/site_theme.py:theme_pack` resolves the closed `THEME_PACKS` allowlist. Only
`theme_pack: "liquid"` selects this pack; missing, malformed and unknown values
retain the legacy renderer. Merchants cannot provide stylesheet URLs or CSS.
`app/site_ui.py` delegates only opted-in sites to `app/site_ui_liquid.py`.

`static/site-theme-liquid.css` carries the reference's rose palette, pearl sections,
1280px container, responsive breakpoints, 8px corners and typography. Visual rules
are scoped to `.h-site[data-pack="liquid"]`. The conditional body-margin reset
uses `body:has(> .h-site[data-pack="liquid"])`; font-face declarations use private
`Liquid ...` aliases. Existing storefront stylesheets are not loaded by this pack.

`static/site-liquid.js` progressively enhances native HTML:

- Sticky bars, measured anchor clearance, scroll state and site-specific remembered
  offer dismissal, with storage failure handled locally.
- Native details dropdowns, outside-click/Escape closure, and a mobile menu with
  focus containment, background inertness and focus return.
- Native scroll-snap reviews with inert, hidden-from-accessibility duplicate turns
  for looping arrows, keyboard and touch swipe. Position buttons share one state.
- Hero pause/play and reduced-motion handling; the reference's desktop hero is
  static. Research counters animate only when motion is allowed.

All prices, cart changes, subscription eligibility and CSRF checks remain on the
server. `cart-drawer.js` is reused. Its optional `[data-cart-count]` hook preserves
Liquid's cart icon while the existing text behaviour remains for other sites.
With JavaScript disabled, product forms still post to the normal FastShop cart.

`app/site_seed_liquid.py:seed_h24you_liquid` runs from bootstrap. It inserts the
fixture once. Subsequent starts apply only bounded asset substitutions and missing
M2 variants/pages, preserving merchant changes to pages, prices, settings, stock
and commerce configuration. There is no automatic content refresh on restart.

`app/site_liquid_content.json` is the initial M1 source-copy snapshot. Runtime page
content comes from canonical `SitePage.draft_json` / `published_json` documents,
not this snapshot. The shared shell's copy and grouped navigation links live in
`settings_json.liquid_shell`, with matching published settings. The implemented
Shop target is stored in native header/footer SiteMenus. Product and content destinations
remain grouped shell links; all targets now have implemented pages.

The existing `text` block is reused for Shop's heading with
`layout: "liquid-shop-heading"`. Bespoke sections have registered, validated blocks:

| Block | Canonical content |
| --- | --- |
| `liquid_hero` | Heading, body, image, button and link |
| `liquid_stats` | Figure values/labels/notes and source text |
| `liquid_collection` | Product entries, specifications and flavour records |
| `liquid_benefits` | Six source statements, fixed icon choices and disclaimer |
| `liquid_reviews` | Eight source samples, portraits and bounded ratings |
| `liquid_articles` | Three source reading cards and future URLs |
| `liquid_catalog` | Shop cards, descriptions, badge copy and imagery |
| `liquid_choices` | Three product choice cards |
| `liquid_trust` | Shopping assurances |

They use existing localisable scalar and item fields, normalisation, URL safety,
media ownership and pure block-editing functions. Liquid collection `theme`
identifies `product`, `spec` or `flavor`; benefit `theme` selects a fixed icon.
Review `heading/body/label/value/theme/alt` hold headline, quotation, name, rating,
location and product caption. Every review and portrait retains `data-sample`.

The classical page editor exposes Liquid entry labels and values as well as the
existing fields. Copy, imagery, block visibility/order and existing entries can be
edited there; pure document operations can also add/reorder entries. There is no
new specialised visual item-creation UI. Draft previews retain builder markers
and disable cart mutations.

Product and variant prices are authoritative catalogue values. Displays read the
current tenant/channel price, so catalogue price edits cannot leave the shown
price different from the amount added to the cart. Seeded amounts are integer
minor units:

| Product | Options | One-time | Subscription |
| --- | --- | --- | --- |
| H2 \| 4 YOU Fizz | Unflavored, Raspberry, Pineapple, Peach | 2800 cents per box | 2520 cents per box |
| H2 \| 4 YOU One | Silver | 25000 cents | Ineligible |
| Taste the Range | Fixed four-flavour pack | 9520 cents | Ineligible |

Home's Fizz quick-add uses the prototype's default subscription. Shop's Fizz card
links to the implemented flavour-selection page; One and Taste the Range add directly.
The reference badge and subtitle copy is preserved in the page documents.

## Run and preview on Windows

Run from `C:\Users\Joosep\h24you\fastshop-lovable` in PowerShell:

```powershell
uv sync --extra dev
./scripts/run_liquid_local.ps1
```

The script uses an explicit SQLite URL under
`output/playwright/h24you-liquid/m2-preview-data/`, generates a process-local
session signing key and binds only to `127.0.0.1:5025`. The database and server
logs are ignored. An explicit SQLite URL is necessary on Windows: assigning an
empty environment variable can remove it, allowing dotenv fallback.

Preview [Home](http://127.0.0.1:5025/sites/h24you-liquid/) and
[Shop](http://127.0.0.1:5025/sites/h24you-liquid/shop). From another PowerShell
window with `PYTHONUTF8=1`, run:

```powershell
uv run python scripts/verify_liquid_browser.py
```

The browser script uses installed Chrome, fresh contexts and no saved browser
session. Its default output is `output/playwright/h24you-liquid/`.

## Assets

The supplied Lovable CDN binaries are copied into `static/h24you/liquid/` with
original filenames. `images.ts` actually renders the clean PNG logos; both are
used unchanged at the reference's 144px width. The original SVG variants are also
available. The five supplied payment SVGs replace M1's neutral text chips.

Home uses `metal-lifestyle.jpg` in the collection panel and
`metal-bottle-square.jpg` on One. Shop uses `studio-front.jpg`. The product gallery
uses studio-front, studio-glow and held-out-face-hidden, followed by the source's
placeholder desk and bag photographs. The sofa section uses lifestyle-sofa.
Helen's founder image is available for M3.

Twenty-six oversized JPEGs were recompressed as progressive JPEGs at quality 95,
with filenames, dimensions and crops preserved. They are all below 400 KB. The
supplied CDN JPEGs already fit the budget. No source asset was modified.

Source placeholder labels remain on generated tablet/range packaging, editorial
imagery, generated bottle lifestyle/step photographs and fictional review portraits.
The missing-CDN substitution register from M1 no longer applies.

Italiana, Quicksand and regular/italic Fraunces remain self-hosted with their OFL
licences. Images and fonts require no third-party CDN.

## Placeholder register and known gaps

- All eight reviews are source-provided samples with fictional portraits. Their
  copy is unchanged; a visible `Sample reviews` label makes that status explicit.
  Replace or remove them before a real launch.
- Tablet/pack photography and editorial imagery retain the source's placeholder status.
- Source policy and social links still point to `#`. All M3 destinations now
  resolve; the complete remaining destination register appears below.
- Shipping below $75 is unconfirmed in the source. The seed uses a **test-only
  $10 shipping fee**, 100 units per variant, a fictional sandbox origin and generic
  test tax categories. These are not merchant-approved fulfilment/tax settings.
  The $75 free-shipping threshold is copied from source.
- Commerce is enabled in sandbox mode. Real Stripe sandbox hand-off still needs
  operator-configured per-site sandbox credentials. No external payment was
  attempted, and browser checks are not payment-provider acceptance evidence.
- Native FastShop checkout, account and cart screens retain FastShop's existing
  layout. The drawer itself uses the existing implementation.
- FastShop's supported subscription cadence is monthly. M2 exposes monthly only.
  Weekly cadence would require changes to schema constraints, cart quotes, consent,
  recovery, renewals and account UI beyond the permitted contained service change.
  Tablet subscriptions retain the catalogue-derived 10% saving.
- Newsletter forms use FastShop's consent and confirmation flow. Actual email
  delivery needs its configured transport. The prototype's simulated instant
  success is not reproduced.
- Price values used by M1 are not marked PLACEHOLDER in `site.ts`. No placeholder
  marker was removed. Supplement Facts retains the exact source placeholder text and unconfirmed label values.
- Native controls and scroll-snap replace React/Embla while retaining the
  reference interactions. Reference comparison details are recorded below.

## Verification

Final verification results are recorded below. Screenshots cover Home and Shop at
375, 768, 1024 and 1440 pixels, with reviews detail captures at each width and a cart-add state. Use the reviews detail captures to inspect the carousel in the viewport; full-page Chrome captures can record a different horizontal scroll offset.
The browser script checks server rendering without JavaScript, all image loads,
section order, exact FDA disclaimer, no horizontal overflow, reduced motion,
carousel arrows/keyboard/touch looping, focus, menu Escape, persistent dismissal,
and FastShop cart additions for Peach, One and Taste the Range.

- Full pytest: **583 passed, 1 skipped**, with the existing Starlette/AnyIO deprecation warning (177.28 seconds).
- Playwright: **83 checks passed, 0 failures**, including all eight viewport/page captures.
- Ruff (`ruff check .`), compileall (app, tests, evals, migrations and the browser script), and `git diff --check`: passed.
- Disposable SQLite Alembic upgrade to `20261011_0029`: passed.
- Alembic check: no new upgrade operations detected.

Existing golden tests establish byte-identical legacy storefront HTML. No
PostgreSQL or real payment-provider acceptance was performed.

## Open questions for later milestones

Confirm final tablet photography, genuine reviews, policy/social destinations,
shipping fee, fulfilment origin, shared bottle inventory and reviewed tax categories
before launch. Weekly subscription cadences remain a future change. The authentic
Helen image is now available for M3.

## Milestone 2

Three product paths are canonical block documents seeded additively:
`/products/h2-4-you-one`, `/products/hydrogen-tablets`, and
`/products/taste-the-range`. Existing merchant product pages are preserved.
`app/site_liquid_products_content.py` and its JSON snapshot provide initial source
content only. Runtime editing uses the stored draft/published documents.

Product blocks register purchase, instructions, technology, daily timeline,
lifestyle, flavours, benefits, disclosures and pairing sections. They reuse the
existing localised fields and item editor, media checks and safe URL validation.
Layout and item themes use closed allowlists. Product reviews reuse the marked
sample review block with the reference's product-specific sample arrays.

`app/site_ui_liquid_products.py`, `static/site-theme-liquid-products.css` and
`static/site-liquid-products.js` provide the progressively enhanced product pages.
The existing CSRF-protected site cart and drawer remain the mutation boundary.

Bottle bundles use four catalogue variants with authoritative unit amounts of
25000, 22500, 21250 and 20000 cents. Quantity means actual bottles. Discounted
variants carry minimum quantities of 2, 3 and 4, enforced by `commerce.price_lines`,
including checkout repricing. Client price fields never determine a charge.
Each sandbox SKU has independent fixture stock; shared physical bottle inventory
must be mapped before live use. No schema migration is required.

The M1-to-M2 seed upgrade replaces only known exact M1 asset substitutions,
inserts missing bundle variants and missing product pages, and preserves merchant
content, prices and commerce settings. Home/Shop card prices use the first sorted
variant so the base One price remains $250. Subscription amounts use FastShop's
integer discount function. Range remains the fixed four-box set at 9520 cents.

The Home hero now also shows its FDA qualification beside its starred claim.
Mobile Home benefit cards and both starred Fizz benefit cards repeat the exact
qualification beside the claims. These deliberate compliance additions, plus
visible sample-review labelling, add space compared with the reference.

Flavour-specific box and lifestyle gallery images and alt text are canonical item
fields. JavaScript reads those fields from rendered data attributes rather than
constructing asset filenames, so merchant image edits remain effective. The server
renders per-variant subscription minor amounts for JavaScript to display, including
half-up rounding after catalogue price edits. Quantity controls stop at FastShop's
existing maximum of 25, including the four-plus bottle tier.

## M2 verification

The reference runs successfully at `http://127.0.0.1:5173/` from a scratch copy
under `%TEMP%/h24you-lovable-ref-m2`. Its JSON asset pointers were replaced with
local imports, including PayPal's separate footer import. `npm install` and
`npm run dev -- --host 127.0.0.1 --port 5173` succeeded. Original reference files
remain read-only and unchanged.

`capture_liquid_reference.py` captures all five pages at 375, 768, 1024 and 1440,
with a `ref-` prefix. The reference's fixed scrolling shell is temporarily expanded
for full-page capture. `verify_liquid_browser.py` captures the matching FastShop
pages, checks all local media, overflow and exact disclaimers, and exercises
looping galleries with arrows, thumbnails, keyboard and mobile touch; exclusive
technology rows; sticky purchase; cart parity for 2/3/5 bottles, Peach subscription,
Raspberry one-time quantity two and the fixed Range pack. Native forms are checked
with JavaScript disabled. The existing M1 menu, reviews and cart checks remain.

Visual iteration corrected gallery sizing, responsive grids, source heading
scale/leading, hero alignment, section colours, FAQ defaults, inline FAQ links,
quality marks and real photo crops. The screenshots are review evidence rather
than a claim of pixel identity: supported cadence, native controls and the
qualification/sample labels are the documented differences.

Release verification (20 October 2026):

- Full isolated pytest: **629 passed, 1 skipped** in 210.05 seconds. The optional
  PowerPoint user-guide test skips because `python-pptx` is not installed. The
  existing Starlette/AnyIO deprecation warning remains.
- Extended Playwright: **191 passed, 0 failures**, no JavaScript errors.
- Reference: **20 captures**, no JavaScript errors. FastShop: **20 full-page
  captures**, plus product review detail captures at all four widths and the M1
  review/cart details. Native scroll-snap carousels can paint a different turn in
  Chrome full-page captures; use the review detail captures to assess the viewport.
- Ruff, compileall, JavaScript syntax check and `git diff --check`: passed.
- Disposable SQLite Alembic upgrade to `20261011_0029`: passed. Alembic check
  reports no new upgrade operations. No migration was added.
- The final server remains at `http://127.0.0.1:5025/sites/h24you-liquid/`, using
  the explicit disposable M2 preview database.

Evidence: `output/playwright/h24you-liquid/`. Page names are `home`, `shop`,
`one`, `fizz`, `range`; widths are `375`, `768`, `1024`, `1440`. Reference files
use `ref-{page}-{width}.png`, storefront files `{page}-{width}.png`, and product
review details `{page}-reviews-{width}.png`. `results.json`,
`reference-results.json`, `browser-m2.txt` and `pytest-release-m2.txt` retain the
verification results. No PostgreSQL or external payment-provider acceptance was
performed, and nothing was pushed or deployed.

## Milestone 3

The remaining seven routes are canonical, published block documents:
`/about`, `/faq`, `/contact`, `/why-hydrogen`, `/why-hydrogen/science`, `/learn`
and `/science`. Together with M1/M2 this is twelve content pages. The legacy
`/4` path permanently redirects to Home within the current site base, including
custom-host routing. There are no other reference routes or ComingSoonPage
usages requiring additional pages.

The actual reference calls `/why-hydrogen` Hydrogen 101. `/learn` remains its
explicit “How a tablet makes hydrogen water” placeholder, and `/science` its
explicit topic placeholder. The implemented pages preserve this distinction.
The optional `scienceHeroImage` and `scienceHeroFacts` exports are unused by the
current reference route components, so no new fact tiles or photograph were
invented. Both full editorial heroes retain the source's bubble fallback and
editable photograph/video/poster slots. No footage is supplied.

`site_liquid_support_content.py` and `site_liquid_editorial_content.py` build
initial canonical documents from their JSON snapshots. `ensure_content_pages`
adds missing pages and the missing Home FAQ link without replacing existing
merchant pages. Runtime rendering reads the resolved draft/published document.
All source files and supplied asset folders remain unchanged.

Registered support blocks are `liquid_support_about_opening`,
`liquid_support_about_values`, `liquid_support_about_closing`,
`liquid_support_faq_hero`, `liquid_support_faq_category`,
`liquid_support_faq_closing` and `liquid_support_contact`. `liquid_editorial`
has closed chapter layouts and validated structural item keys. Both families
retain standard localised scalar/item fields, URL and media ownership checks,
editor preview markers and the existing editor. Validation rejects missing or
duplicate structural entries, invalid illustration choices, unsafe URLs and
unbounded numeric values. Merchants edit text, links and media in canonical
blocks; the source snapshots are not runtime content stores.

About uses the supplied real `helen-founder.jpg`, the source values and closing
links. Andres retains his source placeholders. About appears in desktop and
mobile navigation. All shell and Home Learn links now reach their intended
pages and chapter anchors. Existing policy pages were checked for this site;
none exist. Policy text from the separate `h24you` tenant was not reused.

FAQ renders all **31 questions in five categories**, including all source
confirmation placeholders. Search uses vanilla JavaScript and matches question
and answer copy. Native details keep all answers reachable without JavaScript.
An empty category exposes Contact us. The measured category strip joins the
sticky header clearance at every width; category navigation clears the search.
FAQPage JSON-LD is generated from the same resolved, visible category blocks as
HTML, including merchant edits and hidden categories. Script delimiters are
escaped safely.

Contact uses FastShop's existing POST route, session CSRF, honeypot, server
validation, tenant/site-scoped inbox, rate limit and contact email adapter.
Liquid validates the topic against its published contact document and stores
topic/order metadata with the message. Native forms work without JavaScript;
enhancement supplies source inline validation and first-error focus. The server
requires messages of 10 to 5,000 characters. Only persisted messages receive an
acknowledgement. Unconfigured or failed email delivery is explicitly described
as pending. No live email was sent during verification; tests use fake adapters.

Hydrogen 101 contains the complete five-entry index, chapters driven by the
source ch1/ch2/ch3/ch6/ch7 records, six-step balance story, fresh-water/ppb and
serving-size panels, and both product methods. Science contains all nine myths,
research filters and cards, and the deduplicated eight-study source register.
Source statements, study limitations, URLs and reviewed date are retained.
These pages add no independent scientific assertions or external citations.

## Complete placeholder register after M3

| Area | Placeholder or unresolved setting | Resolution needed |
| --- | --- | --- |
| About | Andres portrait is the source initial A; `[Andres's text coming soon]`; `[Title to confirm]` | Approved portrait, biography and role |
| FAQ | Source confirmation text listed individually below | Merchant approval, with policy and actual commerce consistency review |
| Hydrogen 101 | `H2 \| 4 YOU Fizz: [value to be confirmed] ppb` | Approved measured value and evidence |
| Hero media | Empty photograph/video/poster slots; animated bubbles are the source default | Optional approved footage and poster, or keep bubbles |
| `/learn` | `PLACEHOLDER – page coming soon` | Source design/content if this route should become a complete article |
| `/science` | `PLACEHOLDER – page coming soon` | Source design/content if the topic placeholder should become a complete page |
| Product disclosures | Supplement Facts final-label note, label values and ingredients remain source placeholders | Final reviewed supplement label |
| Samples | Eight source Home reviews and six reviews on each of One, Fizz and Range; fictional portraits `review-avatar-1.jpg` through `review-avatar-8.jpg` | Genuine permissioned reviews and portraits, or remove samples |
| Editorial photographs | `learn-1.jpg`, `learn-3.jpg`, `tablets.jpg`, `science-bubbles-hero.jpg` | Approved photography; the bubble photograph is available but unused by current reference routes |
| Bottle generated photographs | `bottle-bag.jpg`, `bottle-desk.jpg`, `bottle-fill.jpg`, `bottle-press-close.jpg`, `bottle-drink.jpg`, and `bottle-day-morning/desk/gym/evening.jpg` | Final photography; source placeholder status retained |
| Fizz packaging/photographs | `tablet-box-unflavoured/raspberry/pineapple/peach.jpg`, `tablet-flavour-unflavoured/raspberry/pineapple/peach.jpg`, `tablet-fizz.jpg`, `tablet-step-drop/dissolve/drink.jpg`, `tablet-share.jpg` | Final packaging and product photography |
| Range packaging/photographs | `range-row.jpg`, `range-glasses.jpg`, `range-flatlay.jpg`, `range-box-unflavoured/pineapple/peach.jpg`, `range-flavour-unflavoured/pineapple/peach.jpg` | Final pack photography; some supplied files are unused |
| Terms | Footer Terms and Conditions: `#`; no corresponding policy page for this site | Merchant/adviser-approved page |
| Privacy | Offer, footer and Contact privacy links: `#`; no corresponding policy page for this site | Merchant/adviser-approved page |
| Shipping and returns | Footer links resolve to `/faq#orders-shipping` and `/faq#returns-warranty` as in source; no separate policy pages for this site | Approved policy pages and FAQ confirmation text |
| Social | Instagram and Facebook: `#` | Real profile destinations |
| Commerce fixtures | Test-only $10 shipping below $75, 100 units/SKU, sandbox origin, generic tax codes, independent bottle-tier stock | Reviewed fulfilment, shipping, inventory and tax configuration |
| Payments and email | Sandbox commerce; transport/provider acceptance remains operator work | Site credentials, configured email sender and provider acceptance |
| Subscription text | FAQ source says every 2/4/6 weeks and describes account actions; current FastShop purchase flow supports monthly only | Align approved source copy with supported account/cadence behaviour before launch |

Helen's authentic portrait, supplied logos/payment artwork, real supplied bottle
studio/lifestyle images and catalogue prices are not newly designated as
placeholders. No source placeholder marker was removed to imply final content.

### FAQ confirmation entries

The following source questions still contain explicit unconfirmed values. Other
FAQ answers remain verbatim source copy, rather than merchant policy approval.

| Question | Source answer retaining its confirmation marker |
| --- | --- |
| Does hydrogen water taste different? | Hydrogen itself has no taste or smell, so water from the One simply tastes like fresh water. Fizz flavors taste lightly of fruit. [to confirm: taste note for Unflavored] |
| Where do you ship? | We currently ship within the United States. [to confirm: other countries] |
| How long does shipping take? | Orders are processed within [to confirm: 1–2] business days. Delivery then takes about [to confirm: number] business days. You'll get a tracking link by email as soon as your order ships. |
| How much does shipping cost? | Shipping is free on orders over $75. Below that, shipping costs [to confirm: rate]. |
| Will I pay customs or import fees? | [to confirm: whether orders ship from within the US, and whether any duties or fees can apply] |
| Can I change or cancel my order? | Contact us as soon as possible at hello@h24you.com. We can usually make changes until your order ships. [to confirm: time window] |
| My order arrived damaged or something is missing. What should I do? | We're sorry about that. Email us at hello@h24you.com within [to confirm: 7] days of delivery with your order number and a photo, and we'll make it right. |
| Do you ship to P.O. boxes or military addresses (APO/FPO)? | [to confirm] |
| When will I be charged? | You're charged on each delivery date. We'll send a reminder email [to confirm: number] days before each renewal. |
| What is your return policy? | You can return your order within 30 days of delivery. Fizz tablets can be returned unopened. The One can be returned in its original condition and packaging. [to confirm: conditions] |
| Who pays for return shipping? | [to confirm] |
| When will I get my refund? | Once your return arrives and has been checked, we refund your original payment method within [to confirm: number] business days. |
| Which payment methods do you accept? | [to confirm: e.g. Visa, Mastercard, American Express, Apple Pay, Google Pay, PayPal] |
| How do I get my first-order discount? | Request your code through the banner at the top of the site, and enter it at checkout. [to confirm: conditions] |
| Do your prices include sales tax? | [to confirm: whether sales tax is added at checkout] |

## Full list of differences from the Lovable design

1. FastShop uses server-rendered documents, native details/forms and browser
   document scrolling. The reference uses React, nested scrolling and Embla.
   Carousels retain arrows, keyboard, touch and looping, but native scroll-snap
   painting and disclosure animation are not pixel-identical in every state.
2. Exact FDA qualifications are added beside the Home hero and mobile starred
   benefit cards, and beside both starred Fizz benefit cards. Editorial pages
   use one fixed qualification when a starred statement is in view; without
   JavaScript it stays visible. This deliberate same-view compliance addition
   changes the visible lower edge while reading claims.
3. Source sample reviews are visibly labelled. The sandbox footer adds
   `Sandbox · Test purchases only`, adding roughly 33 pixels to the footer.
4. FastShop forms perform real CSRF-protected submissions. Newsletter consent
   and confirmation and Contact inbox/email statuses replace the prototype's
   simulated instant success. Contact retains entered topic/order metadata and
   enforces the existing 10-character message minimum, field limits, honeypot
   and rate limit. Native invalid/success responses use FastShop feedback.
5. Purchase controls expose the supported monthly subscription cadence, rather
   than unsupported source cadences. Catalogue prices, stock and integer
   discount calculation remain authoritative. Native cart/drawer/checkout and
   account screens retain FastShop's existing layout and workflows.
6. Application-owned fixed SVG paths and native control affordances can differ
   slightly from the reference's Lucide icons and React controls. The measured
   shared header at 768px is approximately 2.5px taller; 375/1024/1440 heights
   match. Main About/FAQ/Contact heights match at all four widths. Long editorial
   pages retain small cumulative text/diagram spacing differences, below 100px
   over the full roughly 16,000px Hydrogen 101 page.
7. Study design labels can wrap at narrow widths and editorial chapter paint is
   clipped horizontally to avoid document overflow. Reduced motion stops all
   decorative animation and presents the final balanced story illustration.
   Native disclosures and a complete myth list keep content reachable without
   JavaScript; source-only animation/flip timings can differ.
8. Asset filenames/crops are preserved; the M2 progressive-JPEG recompression
   and self-hosted fonts replace remote dependencies. A local logo favicon
   prevents the browser's default missing-favicon request. The source's unused
   hero image/facts helpers remain unused; editable media slots preserve its
   default bubble design.
9. Home includes the requested “More questions? See all FAQs” link beneath its
   benefit qualification. The current MetalPage reference has no such rendered
   link, despite the shared FAQ link component and the roadmap entry. Product
   links match the reference. No policy text or social destinations were invented.
10. Preview URLs use `/sites/h24you-liquid` and preview pages carry noindex.
    `/4` redirects within that base. Public-host canonical and relative links are
    already supported by FastShop's existing site-host middleware.

## Deploy notes

No deployment, push, hostname change or production provisioning was performed.
To serve a public hostname, an operator would need to run the app behind a
TLS-enabled reverse proxy, point DNS at that service, bind the exact hostname to
the `h24you-liquid` Site, and publish through FastShop's existing review/go-live
workflow. Host middleware already maps that hostname to this site's routes and
generates canonical URLs without the preview prefix. Configure the platform
public URL and persistent database/storage, apply Alembic to that explicitly
selected database, and use properly managed session/encryption secrets.

Resolve the register above before any real launch: approved policies and claims,
genuine reviews, final label/media, supported subscription copy, fulfilment,
inventory, shipping and tax review. Configure site-scoped payment credentials
and complete provider acceptance if commerce is to be public. Configure the
contact recipient and authorised Postmark sender/transport plus transactional
and newsletter mail delivery. Keep credentials outside the repository. The
current local sandbox is not live-provider acceptance evidence.

## M3 release verification

Release checks (21 October 2026):

- Full isolated pytest with `PYTHONUTF8=1`: **671 passed, 1 skipped**, 247.94s.
  The existing optional PowerPoint guide test skips without python-pptx; the
  existing Starlette/AnyIO deprecation warning remains.
- M1/M2 browser regression: **191 passed, 0 failed**.
- Full-site/M3 browser checks: **975 passed, 0 failed**, including Back/Forward
  reading-position restoration for every Home Learn card at all four widths,
  FAQ/JSON-LD/no-JavaScript checks, contact rejection/CSRF, six-step balance
  forward/reverse scrolling, index state, myth controls/touch, study filters and
  keyboard flips, source register, focus, reduced motion, images and overflow.
  All **47** crawled internal URLs/fragments resolve; no console/page errors.
- Reference: **48 captures**, no page errors. Storefront: **48 full-page
  captures**, plus retained product/review/cart detail evidence.
- Ruff, compileall, all three Liquid JavaScript syntax checks and
  `git diff --check`: passed.
- Disposable SQLite Alembic upgrade to `20261011_0029` and Alembic check:
  passed, no new upgrade operations. No migration added.
- Contact success/failure tests verify persistence before fake email delivery,
  honest delivery status, rate limiting and rejection without writes/sends.
  No real mail, payment-provider, PostgreSQL or public-host deployment
  acceptance was performed. Existing legacy storefront regression tests pass.

Evidence is under
`output/playwright/h24you-liquid/`: 48 `ref-{page}-{width}.png` captures and 48
matching `{page}-{width}.png` captures. Page names are `home`, `shop`, `one`,
`fizz`, `range`, `about`, `faq`, `contact`, `learn`, `why-hydrogen`,
`why-hydrogen-science` and `science`; widths are 375, 768, 1024 and 1440.
`m3-browser-results.json` contains the full-site and M3 checks;
`results.json` retains the M1/M2 regression browser checks. Reference scratch
remains `%TEMP%/h24you-lovable-ref-m2`, with only local asset-pointer patches.

The local server remains at `http://127.0.0.1:5025/sites/h24you-liquid/` using
the explicit disposable preview database from `run_liquid_local.ps1`.

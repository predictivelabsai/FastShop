# H2 | 4 YOU Liquid Metal, milestones 1 and 2

## Delivered scope

`/sites/h24you-liquid/` and `/sites/h24you-liquid/shop` render through FastShop's
multi-site routes. Home follows MetalPage's order: hero, science figures,
collection, benefits with the exact FDA disclaimer, sample reviews, then Learn.
Shop contains the heading, three product cards, three choice cards and assurances.
Both share the offer bar, sticky header, desktop dropdowns, mobile menu and footer.

The new site has its own `h24you-liquid` tenant and US/USD channel, preview status,
English default locale and sandbox commerce. The existing `h24you` fixture is
unchanged. M2 adds the three product pages described below. No M3 pages,
production credentials, deployment or migrations were added.

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
not this snapshot. The shared shell's copy and future navigation links live in
`settings_json.liquid_shell`, with matching published settings. The implemented
Shop target is stored in native header/footer SiteMenus. Future M2/M3 targets
remain shell links because native menu validation requires an existing page.

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
- Source policy and social links still point to `#`. M3 destinations can
  return 404 until that milestone is implemented.
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

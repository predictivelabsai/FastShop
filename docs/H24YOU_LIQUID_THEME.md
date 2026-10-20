# H2 | 4 YOU Liquid Metal, milestone 1

## Delivered scope

`/sites/h24you-liquid/` and `/sites/h24you-liquid/shop` render through FastShop's
multi-site routes. Home follows MetalPage's order: hero, science figures,
collection, benefits with the exact FDA disclaimer, sample reviews, then Learn.
Shop contains the heading, three product cards, three choice cards and assurances.
Both share the offer bar, sticky header, desktop dropdowns, mobile menu and footer.

The new site has its own `h24you-liquid` tenant and US/USD channel, preview status,
English default locale and sandbox commerce. The existing `h24you` fixture is
unchanged. No M2 or M3 pages, production credentials, deployment or migrations
were added.

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
fixture once and returns immediately thereafter, preserving merchant changes to
pages, products, settings, stock and commerce configuration. Deleting the entire
fixture in a disposable database is the way to obtain a fresh reference seed.
There is no automatic content refresh on restart.

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
links to the future flavour-selection page; One and Taste the Range add directly.
The reference badge and subtitle copy is preserved in the page documents.

## Run and preview on Windows

Run from `C:\Users\Joosep\h24you\fastshop-lovable` in PowerShell. These settings
select a disposable local database without editing environment files:

```powershell
uv sync --extra dev
$env:PYTHONUTF8 = '1'
$env:DB_URL = ''
$env:FASTSHOP_ENV = 'development'
$env:FASTSHOP_AUTO_CREATE_SCHEMA = '1'
$env:FASTSHOP_DATA_DIR = "$PWD/output/playwright/h24you-liquid/local-data"
$env:FASTSHOP_PUBLIC_URL = 'http://127.0.0.1:5025'
$env:XAI_API_KEY = ''
$env:POSTMARK_API_TOKEN = ''
$env:POSTMARK_SERVER_TOKEN = ''
$env:STRIPE_SECRET_KEY = ''
$env:STRIPE_WEBHOOK_SECRET = ''
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 5025
```

Preview [Home](http://127.0.0.1:5025/sites/h24you-liquid/) and
[Shop](http://127.0.0.1:5025/sites/h24you-liquid/shop). From another PowerShell
window with `PYTHONUTF8=1`, run:

```powershell
uv run python scripts/verify_liquid_browser.py
```

The browser script uses installed Chrome, fresh contexts and no saved browser
session. Its default output is `output/playwright/h24you-liquid/`.

## Assets and substitutions

All 44 real `.jpg` files were copied from Lovable's `src/assets` into
`static/h24you/liquid/`, preserving filenames. No `.asset.json` pointer was copied
or treated as image data. Original supplied SVG logos are copied unchanged.

| Missing/reference asset | M1 replacement or reserved local substitute |
| --- | --- |
| `logo-clean-dark.png.asset.json` | Supplied `assets/logo/h24you_must.svg` for light backgrounds |
| `logo-clean-light.png.asset.json` | Supplied `assets/logo/h24you_valge.svg` for the dark header/footer |
| `metal-lifestyle.jpg.asset.json` | `bottle-drink.jpg`, collection lifestyle panel |
| `metal-bottle-square.jpg.asset.json` | `bottle-drink.jpg`, Home One card |
| `studio-front.jpg.asset.json` | `bottle-desk.jpg`, Shop One card |
| `studio-glow.jpg.asset.json` | Not rendered in M1; `bottle-press-close.jpg` is the available future substitute |
| `held-out-face-hidden.jpg.asset.json` | Not rendered in M1; `bottle-drink.jpg` is the available future substitute |
| `lifestyle-sofa.jpg.asset.json` | Not rendered in M1; `bottle-day-evening.jpg` is the available future substitute |
| `helen-founder.jpg.asset.json` | Not rendered in M1; `review-avatar-1.jpg` is a reserved fictional portrait substitute only, with visible and alt-text placeholder labelling. An authentic Helen portrait is still required for M3 |
| Five payment-artwork pointers | Neutral text chips: Visa, Mastercard, PayPal, Apple Pay, Google Pay; no copied payment-brand artwork |

All substituted rendered photos have alt text containing `placeholder`. Source
placeholder alt text is retained for tablet packaging, reading cards and fictional
review portraits. Founder, gallery and sofa sections are outside M1.

Italiana, Quicksand and regular/italic Fraunces are self-hosted from the upstream
Google Fonts repository under `static/fonts/liquid/`, with each family's OFL text
and a source README. The pack does not request third-party fonts or images.
FastHTML's existing application-wide runtime script headers remain unchanged.

## Placeholder register and known gaps

- All eight reviews are source-provided samples with fictional portraits. Their
  copy is unchanged; a visible `Sample reviews` label makes that status explicit.
  Replace or remove them before a real launch.
- Tablet/pack photography and editorial imagery retain the source's placeholder
  status. The three missing M1 photos use the substitutions above, so crops and
  subjects cannot be identical to the CDN-based reference.
- Supplied SVG logos and neutral payment chips intentionally differ from the
  unavailable pointer artwork.
- Source policy and social links still point to `#`. All M2/M3 destinations can
  return 404 until those milestones are implemented.
- Shipping below $75 is unconfirmed in the source. The seed uses a **test-only
  $10 shipping fee**, 100 units per variant, a fictional sandbox origin and generic
  test tax categories. These are not merchant-approved fulfilment/tax settings.
  The $75 free-shipping threshold is copied from source.
- Commerce is enabled in sandbox mode. Real Stripe sandbox hand-off still needs
  operator-configured per-site sandbox credentials. No external payment was
  attempted, and browser checks are not payment-provider acceptance evidence.
- Native FastShop checkout, account and cart screens retain FastShop's existing
  layout. The drawer itself uses the existing implementation.
- FastShop's current subscription cadence is monthly. The prototype's 2/4/6-week
  selector, subscription management presentation and bottle bundle tiers belong
  to M2. M1 preserves subscription eligibility and the 10% tablet price saving.
- Newsletter forms use FastShop's consent and confirmation flow. Actual email
  delivery needs its configured transport. The prototype's simulated instant
  success is not reproduced.
- Price values used by M1 are not marked PLACEHOLDER in `site.ts`. No placeholder
  marker was removed. Supplement Facts and other M2 placeholders are not rendered.
- The comparison was made against the reference source, including its late Home
  Learn/header CSS refinements. The Lovable app was not run and no pixel-diff claim
  is made. Native controls and scroll-snap replace React/Embla behaviour.

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

Confirm final photography, genuine reviews, policy/social destinations, shipping
fee, fulfilment origin and reviewed tax categories before launch. M2 must reconcile
weekly subscription frequencies with the existing monthly commerce model and
implement the advertised bottle bundle tiers. M3 needs an authentic founder image.

# Liquid storefront presentation pass, 23 October 2026

Scope: local `h24you-lovable` only. Lovable source was consulted read-only. The
Google font response supplied by the owner is self-hosted without binary edits.
No deployment, push, provider handoff or real mail delivery is part of this pass.

## Presentation hook

`app/storefront_ui.py` selects Liquid only from the published `theme_pack`.
Checkout, customer and subscription screens call that hook before their existing
platform renderer. `platform_page` is unchanged. Public errors and 404s use the
same hook; merchant routes retain their original renderer. The cart summary
endpoint remains JSON, including its private cache header.

`app/site_ui_liquid.py` accepts presentation content while reusing its original
offer, sticky header, desktop/mobile navigation, footer and CSS/JS stack. Private
screens are noindex and retain customer.js for fragment-based email verification.
The checkout form and review use details on the left and an order-summary card
on the right, collapsing to one column below 900px. Existing form fields,
CSRF/version/request keys, routes, status codes, authentication and checkout
commands remain authoritative. Native cart quantity and Remove forms work
without JavaScript. The iframe drawer uses the same cart document, with its
repeated shell hidden only after the frame loads inside the drawer.

## Font and text-role audit

References consulted: `src/routes/__root.tsx`, `src/styles.css`, the theme-metal
font tokens, shared MetalTopBars/SiteHeader/MetalFooter/SiteFooter, cart.tsx,
bits.tsx, ComingSoonPage.tsx, product purchase/page components, support/editorial
components and ui/input.tsx/ui/select.tsx. Sources are under the adjacent read-only
`lovable` directory. Sizes below are CSS pixels. Normal case, zero letter spacing
and normal style apply unless noted. Page-specific heading scales remain in the
existing scoped product/support/editorial stylesheets.

| Text role | Reference and Liquid treatment |
| --- | --- |
| Desktop navigation | Quicksand 400, 14px; 12px at 768–1023px. This is the body family, including dropdowns. |
| Offer bar | Quicksand, 11px desktop/10px mobile; offer emphasis 600, request link 400. |
| Hero | Italiana 400, 80/72/64/56px through the existing breakpoints; Quicksand supporting copy. |
| Display headings h1–h3 | Italiana 400 through `font-display`; each section keeps its reference scale. New commerce titles use 48px desktop/40px mobile. |
| Product-name exceptions | Quicksand brand text with lining numerals; Fraunces italic suffix, inheriting the role's weight. |
| Buttons | Quicksand 600; existing metal CTAs retain 14px uppercase and product add buttons 12px uppercase. New native commerce actions use 14px and normal case. |
| Product cards | Quicksand brand/body; Fraunces italic suffix; heading 24px with existing responsive overrides, description 13px/12px. |
| Prices and totals | Quicksand, normal style and case; existing product-price scales; cart price/subtotal 16px/600, with 12px/400 struck-through one-time prices on subscription lines. |
| Reviews | Fraunces italic, headline 20px/600 and quotation 16px/400; Quicksand 600 name and 12px location/product. Italic 600 is synthesised, just as in Lovable. Stars are SVG. |
| Footer | Fraunces italic 36px newsletter heading; Quicksand body/navigation, 16px/600 help heading and 12px legal copy. |
| Mobile menu | Italiana 400, 30px top level and 24px submenu. Product-name spans retain the source's family exceptions. Help links use Quicksand 14px. |
| FAQ | Italiana display titles and Quicksand questions/answers/search, preserving the support stylesheet's section-specific sizes. |
| Forms | Quicksand; new input text 16px mobile/14px from 768px, select 14px, labels 14px. Borders, focus rings and 8px corners use Liquid tokens. |
| Cart drawer | Italiana 400 title, 24px; Quicksand 14px shipping line, 16px line names/prices/subtotal, 12px purchase type/Remove. No platform brand. |
| Simple results/404 | ComingSoonPage's full shell, light section, display heading and supporting body pattern. Existing result/error copy is retained. |

The four upstream TTF declarations were replaced by the supplied **31** WOFF2
face declarations, preserving their unicode ranges and private aliases. No
extra Fraunces italic 600 or optical/variation overrides were added. The four
unreferenced TTFs and temporary stylesheet text file were removed. OFL licences
remain. Chrome reports the WOFF2 Quicksand's internal name as `Quicksand Light`;
its CSS family remains `Liquid Quicksand` at every requested weight.

Computed family stacks alone cannot prove the absence of fallback rendering.
The browser verifier also calls Chrome's `CSS.getPlatformFontsForNode` for every
text-bearing node, including the drawer document. This exposed Windows fonts
used for text arrows/stars/play symbols. Fixed SVG replacements now paint these
symbols; accessible names and rating labels are retained. The verifier rejects
any actually rendered family outside Fraunces, Italiana and Quicksand, and checks
loaded FontFace records plus local Google WOFF2 resource requests.

## Existing contract differences

The supplied `cartCopy.title` currently says “Your cart”; the requested drawer
heading “Your bag” takes precedence. The source's empty-state copy and CTA are
reused, while the real checkout CTA retains its existing storefront wording.

The purchase-type select displays the current contract. Changing between
one-time and subscription in place is unsupported by the existing atomic cart
update command, whose subscription field identifies the line being updated.
This presentation pass does not add that commerce operation. Remove the line
and add the desired type from the product page. Monthly delivery remains the
supported frequency, rather than presenting unsupported 2/4/6-week controls.

## Evidence and checks

Screenshots and font records: `output/playwright/h24you-liquid/storefront-pass/`.
The new verifier covers 375px, 768px, 1024px and 1440px, including 61 page/font
records and JavaScript-disabled native cart controls. The 29 requested mobile
and desktop PNGs are retained in git; optional tablet and regression PNGs remain
available locally. `font-results.json` records computed stacks, loaded faces
and actual rendered font families per capture. Subscription captures use a
local, synthetic passwordless-login challenge and the real authentication flow.

The checkout-review screenshot fixture uses a deterministic zero-tax adapter
only in the explicitly selected disposable preview SQLite database. It does not
contact Stripe or change the production gateway. The screenshot is presentation
evidence, not payment or tax acceptance. Fixtures contain synthetic shoppers.


Verification commands and completed results:

- `uv run ruff check .`: passed.
- `PYTHONUTF8=1 uv run python -m pytest tests/test_liquid_storefront.py -ra`:
  12 passed, including category/tag 404s, account verification, authenticated
  subscriptions, native checkout fields and the exact local font declarations.
- `uv run python scripts/verify_liquid_browser.py --output output/playwright/h24you-liquid/storefront-pass/regression`:
  191 passed, 0 failed.
- `uv run python scripts/verify_liquid_m3_browser.py --output output/playwright/h24you-liquid/storefront-pass/regression-m3`:
  975 passed, 0 failed, no page errors.
- `uv run python scripts/verify_liquid_storefront_browser.py`: passed across all
  four widths, with 61 font/page records and native cart mutation checks.
- `uv run python -m compileall -q app tests web_app.py`,
  `node --check static/cart-drawer.js`, `node --check static/site-liquid.js` and
  `git diff --check`: passed.
- `uv run alembic upgrade head` and `uv run alembic check`, with `DB_URL` explicitly
  set to `output/playwright/h24you-liquid/storefront-pass/migration-check.sqlite3`:
  passed; no new upgrade operations. No schema migration was added.

All Python checks use `PYTHONUTF8=1` and explicit disposable storage with payment
and email environment credentials cleared. The local preview started through
`scripts/run_liquid_local.ps1` has been stopped; port 5025 is no longer listening.

Open question: a functional purchase-type switch requires a separately scoped
atomic cart operation. The visual select deliberately displays the current
purchase type; existing Remove and product-page purchase controls remain usable.


Full isolated regression: `PYTHONUTF8=1 uv run python -m pytest -ra` completed with
**702 passed, 1 skipped**, in 803.34 seconds. The existing optional user-guide
test skips because `python-pptx` is absent; the existing Starlette/AnyIO warning
remains. The final category/tag 404 and cart presentation refinements also pass
the **12-test** focused storefront rerun. Test output is retained in
`output/playwright/h24you-liquid/storefront-pytest-final.txt` and
`storefront-focused-pytest.txt`.

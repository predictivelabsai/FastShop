---
version: 1
slug: "app-marketing-routes-py"
primary_target: "app/marketing_routes.py"
related_targets: []
---

# Surface brief — marketing landing (rebrand)

## Scope and visitor mode

`app/marketing_routes.py::marketing_page` + `_header`/`_footer`/`_proof_surface` shared with auth pages, styled by `static/marketing.css`. Mode: **Persuade**. Replaces the warm-paper editorial world everywhere on this surface.

## Constraints carried from PRODUCT.md

- No invented customers, testimonials, logos, prices, benchmarks, or third-party product screenshots.
- Every capability number from code (`app/plans.py`, `app/site_blocks.BLOCK_TYPES`, `app/site_generation.MAX_PAGES`).
- Sandbox-first / review-gated honesty stays in the copy.
- Assets local: photos downloaded once into `static/img/`, no third-party runtime requests.
- Playwright desktop + mobile evidence per repo rules.

## Direction contract (code-led)

THESIS: FastShop is real, current commerce software — the landing proves it with the
actual product interface and real product photography, not a print-style proof sheet.
The category default this surface refuses is the flat editorial specimen that shipped
in D1–D5 (paper, hairline rules, crosshairs, zero imagery).

OWN-WORLD: light neutral SaaS canvas (white / soft green-tinted #f5f7f6), near-black
ink #0d1713, evergreen primary kept from the old brand as the action color, pill
primary buttons, 20–24px radius photo-forward cards with soft layered shadows, a
near-black-green dark band for the commerce section, Archivo (full-width, 700–800)
display over Inter body, 1px #e3e8e5 hairlines for row structure, minimal drawn SVG
iconography in one stroke weight.

STORY: in one viewport the merchant sees the product working — a genuine store build
(the builder framing a live-looking North & Pine storefront with real photography in
its products) — believes the draft-then-review path is how a store gets live, and acts
on Create workspace.

FIRST VIEWPORT: sticky translucent header (brand, four anchors, Sign in, pill CTA).
Left copy column: display headline "From a short description to a live,
checkout-capable store." at display scale; lede; primary pill CTA + text link;
support note. Right: the product composition — a browser-framed builder specimen,
larger and brighter than today's, its product slots filled with real object
photography, a floating AI-update card and a floating go-live checklist card with
soft shadows, over a soft radial green-wash field behind them. Primary action sits
in the left column, above the fold, and the composition proves rather than decorates.

SIGNATURE INTERACTION: the floating proof cards (AI update panel, go-live checklist)
lift with a soft shadow bloom on hover of the hero composition — one authored moment;
page entry is a single gentle rise-and-fade on hero copy and frame, everything else
static, reduced-motion honored. Section rhythm alternates density: airy photo cards
earn a dense ruled table.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish
review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.

## Chosen direction / memorable moment

User-pinned canon: built to the Shopify / BigCommerce / Lovable craft bar ("modern
design, fully optimised, add design elements, pictures"), executed at full fidelity
without smuggled quirk. Bar products named by the operator: those three.

## Imagery provenance

Real free-license photography (downloaded to `static/img/`, no API in page runtime),
chosen to fit the illustrative North & Pine home-objects store and merchant-labor
feature cards; each file keeps an origin note sidecar. UI compositions are authored
HTML/CSS. List for user replacement with real material: storefront product art.

## Unresolved decisions

- Final accent saturation vs. old #087f5b — resolve during build against contrast.
- Whether the capability numbers band stays on the landing — keep, restyled.

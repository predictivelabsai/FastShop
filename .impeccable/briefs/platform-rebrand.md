# Surface brief — platform surfaces (rebrand)

## Scope and visitor mode

Merchant/admin surfaces: dashboard (Sites & content), billing (plan & usage + billing detail),
onboarding wizard, site builder workspace + editor chrome + billing console. Shells live in
`app/platform_ui.py` (`platform_shell`) and the content in `app/site_builder_routes.py`,
`app/billing_routes.py`, related registrars. Styled by `static/platform.css` (token layer)
over `static/site.css` (legacy stylesheet whose merchant/admin classes the platform uses)
plus `static/site-workspace.css`, `static/site-editor.css`, `static/site-preview.css`.

Mode: **Operate**. The visitor completes a task — running their store. Scanability, consistency,
and calm confidence outrank expression. Brand lives in precise details. This surface should feel
like the same product the landing promise: the rebranded token layer carried into the tool.

## Constraints carried from PRODUCT.md

- Every capability number from code; no invented prices (billing pages show plan names and
  limits from `app/plans.py`, never prices — honest "not configured for self-serve billing".
- Sandbox-first / review-gated copy unchanged.
- No AI attribution anywhere.
- The legacy public storefront and generated sites are NOT in scope: `site.css` classes used by
  published public pages keep their current look. Overheads must be scoped so public pages
  (served by `site_ui.py` shells without `platform.css`) are untouched.

## Direction contract (code-led)

THESIS: the operator's daily work should feel like the product they chose on the landing —
the light neutral SaaS world, applied quietly and consistently, with the tool's structure
left untouched.

OWN-WORLD (from DESIGN.md, the shipped landing): white canvas `#ffffff` / wash `#f5f7f6`,
ink trio `#0d1713`/`#4c5a53`/`#5f6b64`, hairlines `#e3e8e5`/`#ccd5d0`, evergreen `#0e7d55`
(+`#0a5f41`) as sole action color, night `#10201a` family for guarded bands only,
focus `#005fcc`, danger `#b42318`. Archivo display carries `h1`/top-level headings in place of
Georgia; Inter remains body. Radii: 999px for buttons/controls, 12px inputs/fields, 20–24px
cards and panels. Layered offset+blur shadows from the marketing tokens (`--shadow-card`,
`--shadow-float`). Translucent sticky header bar. Inputs: 1px `#e3e8e5` border, 12px radius,
white ground, focus ring `#005fcc`.

STORY: an operator opens the dashboard after signing up on the rebranded landing and sees one
continuous product — same canvas, same buttons, same type. Work state is legible at a glance
(plan & usage strip, verify-email banner as a calm card, website cards with soft shadows).

FIRST VIEWPORT examples: dashboard — sticky white header, "Your websites" with Archivo display,
usage strip as a white rounded card with green progress marks; billing — plan card on white with
green accents replacing the mint field; onboarding — white canvas, 12px inputs, a 2×2 choice
grid resting on white cards; builder — existing polished workspace (design-overhaul-5) checked
against the new tokens only where it still shows `--paper`/mint.

SIGNATURE: none — Operate mode. Consistency is the design. No new motion beyond existing
transitions; hover states get the green lift on primary actions where a control exists.

FINISH: replace `static/platform.css` tokens with the shipped world (aliased onto the legacy
variable names so `site.css` merchant classes inherit without markup changes), add scoped
target overrides where legacy hardcodes leak (Georgia headings, 8px radius, beige washes,
mint fields). Verify with `scripts/verify_site_browser.py --design-audit` captures for
dashboard, billing, onboarding, builder, plans console, login/signup (mobile + desktop),
the finish review, DESIGN.md sidecar update, CI order.

## Unresolved decisions

- Whether the verify-email banner deserves a dedicated treatment or the standard card.
- Whether the builder's own accent hues (already close to the final green) need retint or pass.
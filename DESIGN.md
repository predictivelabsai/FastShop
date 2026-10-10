---
name: FastShop
description: Light neutral SaaS rebrand — a working-product, photo-forward proof for the review-led path from business brief to live, checkout-capable store.
colors:
  canvas: "#ffffff"
  wash: "#f5f7f6"
  ink: "#0d1713"
  ink-soft: "#4c5a53"
  ink-faint: "#5f6b64"
  line: "#e3e8e5"
  line-strong: "#ccd5d0"
  green: "#0e7d55"
  green-deep: "#0a5f41"
  green-wash: "#e9f3ee"
  night: "#10201a"
  night-soft: "#a9bab3"
  night-line: "#2c3f37"
  night-accent: "#7fd6ae"
  night-text: "#eef4f1"
  focus: "#005fcc"
  danger: "#b42318"
typography:
  display:
    fontFamily: '"Archivo", "Helvetica Neue", Arial, sans-serif'
    fontSize: "clamp(2.7rem, 4.9vw, 4.35rem)"
    fontWeight: 780
    lineHeight: 1.03
    letterSpacing: "-0.032em"
  headline:
    fontFamily: '"Archivo", "Helvetica Neue", Arial, sans-serif'
    fontSize: "clamp(1.9rem, 3.4vw, 3.1rem)"
    fontWeight: 760
    lineHeight: 1.06
    letterSpacing: "-0.028em"
  title:
    fontFamily: '"Archivo", "Helvetica Neue", Arial, sans-serif'
    fontSize: "clamp(1.4rem, 2.3vw, 2rem)"
    fontWeight: 740
    lineHeight: 1.1
    letterSpacing: "-0.022em"
  body:
    fontFamily: '"Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: '"Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "0.72rem"
    fontWeight: 700
    lineHeight: 1.55
    letterSpacing: "0.12em"
rounded:
  control: "12px"
  panel: "16px"
  card: "20px"
  sheet: "24px"
  pill: "999px"
  circle: "50%"
spacing:
  xs: "12px"
  sm: "16px"
  md: "24px"
  lg: "32px"
  xl: "48px"
  xxl: "64px"
components:
  button-primary:
    backgroundColor: "{colors.green}"
    textColor: "{colors.canvas}"
    typography: "{typography.body}"
    rounded: "{rounded.pill}"
    padding: "13px 26px"
    height: "50px"
  button-primary-hover:
    backgroundColor: "{colors.green-deep}"
    textColor: "{colors.canvas}"
    typography: "{typography.body}"
    rounded: "{rounded.pill}"
    padding: "13px 26px"
    height: "50px"
  button-primary-small:
    backgroundColor: "{colors.green}"
    textColor: "{colors.canvas}"
    typography: "{typography.body}"
    rounded: "{rounded.pill}"
    padding: "9px 20px"
    height: "42px"
  evidence-card:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "22px 24px"
  floating-panel:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "16px 18px"
    width: "236px"
  index-chip:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.pill}"
    padding: "9px 15px"
---

# Design System: FastShop

Scope note: this documents the token layer as shipped on the rebranded marketing
landing and auth pages (`static/marketing.css` + `app/marketing_routes.py`). Its
`:root` block is the rebrand's token source; PR 2 sweeps dashboard, billing,
onboarding, and builder surfaces onto these same tokens.

## Overview

**Creative North Star: "The Working Storefront"**

FastShop's marketing surface is a light neutral SaaS world in which the product is
shown actually working. The proof is no longer a print-style specimen: it is a
browser-framed builder framing a live-looking storefront, its product slots filled
with real photography, with a floating AI-update panel and a go-live checklist
oversailing the frame. White canvas, near-black evergreen ink, and one evergreen
action color carry the page; a near-black-green night band structures commerce,
pricing, and the footer.

The world alternates density on purpose: airy, photo-forward cards earn their place
and are followed by dense, ruled evidence tables. Evergreen green acts — it colors
the primary pill, step labels, status, and verified numerals — and never shades large
background fields. The page demonstrates the same control it promises: reviewed
gates stay visible from draft to go-live.

**Key Characteristics:**

- White canvas and green-tinted wash with near-black evergreen ink and 1px hairlines.
- Evergreen primary carried forward from the old brand, deepened for contrast.
- Browser-framed builder proof with real photography and two floating proof panels.
- Pill CTAs at 999px; photo-forward cards at 16–24px with soft layered shadows.
- Full-width Archivo (700–800) display over self-hosted Inter body.
- Near-black-green night band for guarded commerce, the pricing head, and the footer.
- Conversion pacing: proof layer, workflow, commerce, migration, CMS, pricing, and FAQ.

## Colors

The palette is a restrained neutral SaaS ground: white and a soft green-tinted wash,
near-black evergreen ink in three weights, one evergreen action pair, and a
night-green family reserved for guarded structure.

### Primary

- **Evergreen** (`--green`): The sole action ink — primary pill CTAs, the brand mark,
  step and feature labels, status text, active builder states, and the FAQ toggle fill.
- **Deep Evergreen** (`--green-deep`): The interaction shade for hovers, the capability
  numerals, and quiet green annotations.

### Secondary

- **Night Green:** The near-black-green structural field for the commerce band, the
  pricing head plate, the builder rail, and the footer.
- **Night Accent:** A light mint-green used only on dark — link hovers, the pricing
  note, and green-tinted chips inside the builder rail.
- **Night Soft / Night Line / Night Text:** The dark-band copy, hairline, and text
  counterparts of the ink / line / night system.
- **Green Wash:** A pale evergreen tint identifying green-system moments: input-hover
  fills for the Google buttons and the email-verification notice.

### Neutral

- **Canvas:** The default page ground and the material cue for the whole world.
- **Wash:** The recessive alternate plane — proof chrome, capability band, commerce
  copy plate, and the auth page ground.
- **Ink / Ink Soft / Ink Faint:** Primary copy; secondary copy and ledes; metadata,
  captions, and placeholders.
- **Line / Line Strong:** The 1px hairline structure for rows and tables; the firmer
  outline for inputs, chips, and small marks.
- **Focus Blue:** A deliberately conventional, high-visibility keyboard focus
  indicator, applied globally.
- **Danger:** Semantic error ink and error plates only; it is not a marketing accent.

### Named Rules

**The Green Acts Rule.** Green signals action, active state, or verified status;
night structures guarded surfaces. Nothing else becomes decoration, and no fourth
accent exists.

**The White Ground Rule.** White canvas is the default plane. Wash tints recessive or
supporting fields, and Green Wash appears only where the system itself is green.

## Typography

**Display Font:** Self-hosted Archivo Variable through `--font-display`, used at its
default full width (the variable file declares a 62–125% stretch range; rebranded
surfaces render it uncondensed).

**Body Font:** Self-hosted Inter Variable through `--font-body`, followed by the
native UI sans-serif stack.

**Character:** Archivo at full width and 700–800 weight, with tracking from −0.022em
to −0.032em, reads as confident modern SaaS display rather than compressed editorial
type. Inter keeps body copy familiar and highly legible; labels behave like product
metadata — Step 1, Checkout, Commerce go-live — not miniature slogans.

### Hierarchy

- **Display** (780, `clamp(2.7rem, 4.9vw, 4.35rem)`, 1.03): The hero promise only,
  held to a 13ch measure; auth pages drop the same style to their own scale
  (`clamp(1.7rem, 3vw, 2.2rem)`).
- **Headline** (760, `clamp(1.9rem, 3.4vw, 3.1rem)`, 1.06): Section openings and the
  pricing proposition.
- **Title** (740, `clamp(1.4rem, 2.3vw, 2rem)`, 1.1): Workflow steps and commerce
  proofs; compact display-font entries cover the brand wordmark, plan names,
  migration connectors, and the 2.4rem capability numerals.
- **Body** (400, 1rem, 1.55): Explanations and evidence, generally capped at 60–62ch;
  the hero lede runs `clamp(1.02rem, 1.28vw, 1.18rem)` at 1.6.
- **Label** (700, 0.72rem, 0.12em tracking, uppercase): Step tags, feature tags, proof
  captions, panel headers, and the "Works with" marker.

### Named Rules

**The Local Type Rule.** The marketing page never makes an outbound font request.
`static/fonts.css` loads `static/fonts/Archivo-Variable.woff2` (100–900 weight,
62–125% width) and `static/fonts/Inter-Variable.woff2` (100–900 weight) before
`static/marketing.css`.

**The Uncondensed Display Rule.** Display type renders at Archivo's default width.
The condensed 72% stretch belongs to the retired editorial world and is not carried
onto rebranded surfaces.

**The Metadata Label Rule.** Small type has a job — status, category, step, or proof
caption. Do not use tiny labels as ornamental texture.

## Layout

The desktop shell is `min(1248px, calc(100% - 64px))` — 32px fluid gutters. The hero
pairs a copy column (`minmax(0, 0.88fr)`) with a proof column (`minmax(540px, 1.12fr)`)
at `clamp(40px, 5vw, 72px)` gap, so the working proof outweighs the promise. A soft
radial evergreen tint sits behind the proof stage, and the stage reserves right
padding for the floating AI panel that oversails the browser frame. Long sections run
`clamp(80px, 9vw, 128px)` vertical rhythm with section headings capped at 720px; the
pacing then alternates airy photo cards with dense ruled rows (workflow steps at a
190px minimum row height, migration and plan rows at 28–56px intervals, evidence rows
at 56–68px).

The commerce band is full-bleed night with a sticky intro column (`top: 96px`) beside
a ruled proof list and two photo cards. The footer repeats the night band. Auth pages
center a 540px sheet on the wash ground; signup uses a two-column copy-and-form
composition.

Responsive behavior: at 1180px the hero rebalances; at 980px the shell tightens to
`min(100% - 40px, 760px)`, anchor links disappear, paired columns stack, the sticky
dark intro unsticks, and the capability band falls to 2×2. At 640px gutters are 16px,
the floating panels become static stacked cards beneath the proof, the storefront
product grid drops to two columns, and scrolling stops smoothing. At 400px the header
compacts further. No layout introduces horizontal overflow.

The first viewport must contain the brief-to-live-store promise, the working builder
proof with visible go-live state, and the sign-in and Create workspace routes.

**The Proof Before Promise Rule.** Every major claim meets evidence, workflow, or a
visible review gate before the page asks for more trust.

**The Single Rise Rule.** Page entry is one rise-and-fade animation (640ms,
`cubic-bezier(0.22, 1, 0.36, 1)`, 14px rise) applied to hero copy and proof stage with
a 90ms stagger — everything else is static, and all motion stops under
`prefers-reduced-motion: reduce`.

## Elevation & Depth

The system is a hybrid: structural surfaces (bands, ruled rows, tables) are flat and
carry depth with hairlines and plane changes, while authored product artifacts and
floating panels rise on layered shadows. Every shadow token is a two-layer pair — a
close contact layer plus a tall diffuse layer — so lifts read as physical, not glow.

### Shadow Vocabulary

- **Card** (`--shadow-card: 0 1px 2px rgba(13, 23, 19, 0.05), 0 10px 28px rgba(13, 23, 19, 0.1)`):
  Resting evidence cards, the CMS proof, the auth card, and signup/verification sheets.
- **Float** (`--shadow-float: 0 2px 4px rgba(13, 23, 19, 0.06), 0 20px 44px rgba(13, 23, 19, 0.16)`):
  The browser-framed proof, the AI-update panel, and the go-live checklist chip.
- **Bloom** (`--shadow-bloom: 0 2px 4px rgba(13, 23, 19, 0.07), 0 28px 60px rgba(13, 23, 19, 0.2)`):
  The hover state of the two floating panels only — the system's single authored bloom.
- **Action Lift** (`0 6px 16px rgba(14, 125, 85, 0.24)`): An evergreen-tinted shadow
  that appears under the primary pill on hover, alongside a 1px upward shift.
- **Dark Photo Lift** (`0 14px 34px rgba(0, 0, 0, 0.34)`): Photography cards inside
  the night band, keyed to the dark ground rather than the page ink.

### Named Rules

**The Lifted Artifact Rule.** Shadows attach to product artifacts and floating
panels, never to full-bleed bands or ruled rows; structure stays flat.

**The One Bloom Rule.** Beyond the 1px button lift, the only sanctioned hover
elevation is the floating panels' bloom to `--shadow-bloom` with a 4px lift over
220ms. No glow, bounce, or ambient drift exists anywhere.

## Shapes

Every actionable control is a true pill (999px radius): primary CTAs, small header
CTAs, chips, the pricing note, and the proof's URL field. Containers round on a
ladder — 12px for inputs, notices, and in-proof photo tiles; 16px for floating
panels; 20px for the browser frame and evidence cards; 24px for large sheets (the
auth card, the signup form sheet, the pricing head). Circles are reserved for the
brand mark, browser-chrome dots, checklist dots, and the FAQ toggle.

Internal structure is always a square 1px hairline: `--line` for rows, tables, and
chrome; `--line-strong` for input and chip outlines; `--night-line` on dark. The
sticky header keeps a translucent white field (`rgba(255, 255, 255, 0.86)`) with a
10px backdrop blur above a hairline.

**Iconography.** No drawn icon set ships in this PR. Markers are CSS circles (brand
mark, browser dots, checklist dots, FAQ toggle) and typographic glyphs — an arrow in
the proof CTA and a plus/minus pair in the FAQ indicator. The direction brief
anticipated minimal drawn SVG icons in one stroke weight; that set did not ship, and
if it lands in PR 2 it must hold to a single stroke weight rather than being inferred
from this file.

**The Pill Command Rule.** If it can be clicked, it is a pill. Containers may round;
internal divisions stay square hairlines.

## Components

### Buttons

- **Shape:** A true pill (999px) with a 50px minimum height and 13px by 26px padding.
- **Primary:** Evergreen field, white copy at 0.92rem / weight 650, −0.01em tracking.
- **Hover / Focus:** Deep Evergreen field plus a 1px upward shift and the evergreen
  Action Lift shadow over 180ms; keyboard focus uses the global visible outline —
  2px Focus Blue at a 2px offset.
- **Small:** Header action with a 42px minimum height and 9px by 20px padding; it
  remains an adequate touch target.
- **Text link:** Ink at weight 650 with a 1px Line Strong underline; hover deepens to
  Deep Evergreen on both text and underline.

### Chips

- **Style:** Canvas ground, Line Strong outline, ink text at 0.78rem / weight 620,
  9px by 15px padding, true pill shape.
- **Use:** An index of concrete CMS capabilities, not a removable filter or
  decorative tag cloud.

### Cards / Containers

- **Corner Style:** 20px for evidence cards and the proof frame; 24px for large
  sheets; 16px for floating panels.
- **Background:** Canvas over the white page; Wash for recessive plates and proof
  chrome; Night for guarded surfaces; Green Wash only for green-system moments.
- **Shadow Strategy:** Card and Float at rest per the Elevation section; Bloom only
  on panel hover.
- **Border:** One-pixel Line on artifacts; Line Strong for firmer input outlines;
  Night Line divides dark surfaces.
- **Internal Padding:** Dense rows use 16–28px; evidence cards 22–26px; the pricing
  head uses a fluid `clamp(40px, 5vw, 60px)`.

### Inputs / Fields

- **Style:** 48px minimum height, 12px radius, 1px Line Strong outline on Canvas,
  Evergreen caret, and Ink Faint placeholders.
- **Focus:** Border deepens to Evergreen over 160ms with the outline suppressed;
  the global Focus Blue ring still applies to `:focus-visible`.
- **Error / Disabled:** Field errors are Danger text at 0.74rem / weight 650 with
  `aria-describedby` wiring; form-level errors are a 12px plate mixing Danger at 8%
  into Canvas with a Danger border, announced via `role="alert"`.

### Navigation

The header is a sticky translucent sheet with backdrop blur above a hairline; the nav
row is 72px (64px mobile). The brand pairs a 32px Evergreen circle mark with an
Archivo 750 wordmark. Links sit at 0.86rem / weight 620 and deepen to Deep Evergreen
on hover rather than acquiring accent decoration; Sign in and the small pill CTA stay
visible at every width. The footer repeats the night band with Night Accent hovers.

### Browser-Framed Builder Proof

The signature artifact is an authored, accessible HTML/CSS composition: a browser
chrome on Wash with pastel traffic dots and a pill URL field; a 128px night builder
rail with page list and AI "Prepare update" affordance; and a storefront pane whose
copy plate (Wash ground, night pill badge, Archivo title, ink pill CTA with arrow
glyph) sits beside a real photograph above three product cards using real photography
in 12px-rounded tiles. A 236px AI-update panel and a 244px go-live checklist chip
float over the frame's right edge with check-dot states. A caption below states
"Generated storefront inside the FastShop builder / Illustrative interface", and the
group's `aria-label` names the illustration — it never masquerades as a customer
screenshot.

### Honest Proof Layer

A text-only works-with strip of four ruled plates is followed by a wash capability
band whose numerals (Archivo 2.4rem / 760, Deep Evergreen, tabular-nums) come from
`app/site_blocks.BLOCK_TYPES`, `app/site_generation.MAX_PAGES`, and
`app/plans.PLANS`. Every public number must be traceable to code; illustrative or
rounded marketing numbers are not allowed.

### Commerce Night Band

The full-bleed night band pairs a sticky intro column — headline, copy, light link,
two photography cards with dark labels — with a ruled list of checkout, go-live, and
operations proofs. Feature tags are Evergreen labels on dark (`--night-accent`
adjacency keeps them legible); the night palette does the structuring.

### Migration and CMS Evidence

Migration pairs a ruled 20px evidence card (numbered preview → review → apply path)
with a connector table; CMS pairs capability chips with a ruled "CONTENT WORKSPACE"
evidence card. Rows are 1px-ruled and dense; both artifacts stay labeled
illustrative.

### Pricing Plate

Pricing opens with a 24px-radius night head carrying the proposition and a
"No invented prices" note (Night Line border, Night Accent text). The body lists the
real Free, Basic, and Pro quota values from `app/plans.py` as ruled rows beside the
signup pill; it never invents currency prices.

### FAQ Disclosure

Questions use native `details` and `summary` behavior, one per ruled row. A 30px
circled plus fills Evergreen and becomes a minus when open; content stays in document
flow and remains usable without script.

### Auth Pages

Sign in, signup, and verification center a Canvas sheet (24px radius, Card shadow) on
the Wash ground. Google buttons are 48px pills that fill Green Wash on hover; the
verification notice is a Green Wash plate; closed signup reuses the same two-column
system to state availability honestly.

**The Native First Rule.** Prefer semantic browser behavior for disclosure,
navigation, focus, and reduced motion before introducing custom interaction
machinery.

## Do's and Don'ts

### Do:

- Do keep Canvas white, hairline-ruled rows, and restrained accents as the default
  material; let the proof and the photography carry the color.
- Do pair each product promise with an authored proof, explicit workflow, or visible
  review gate, and keep go-live state visible in the hero composition.
- Do trace every capability number to a code constant, validation invariant, or live
  plan catalog value.
- Do keep photography license-documented in `static/img/` and every page asset
  self-hosted so rendering makes no third-party requests.
- Do use the night band only for guarded commerce, the pricing head, and the footer,
  with Night Accent as the dark-side link color.
- Do preserve global focus-visible rings, semantic disclosure, honest signup
  availability, and the reduced-motion kill switch.

### Don't:

- Don't invent customer logos, testimonials, prices, benchmarks, or third-party
  product screenshots.
- Don't reintroduce the retired warm-paper editorial devices on rebranded surfaces —
  no paper `#f4f1e9`, registration crosshairs, dot-registration fields, condensed
  72% Archivo, or coral. PR 2 removes the remaining warm-paper surfaces onto these
  tokens; the legacy values in `static/platform.css` are not a license.
- Don't cast shadows on full-bleed bands, ruled rows, or tables; elevation belongs to
  floating artifacts and sheets.
- Don't exceed the motion budget: one entry rise, 180ms control feedback, and the
  one hover bloom. No ambient loops or scroll-triggered spectacle.
- Don't let green leave its acting roles for large background fields or body copy,
  and don't add a fourth accent beyond evergreen, night, and semantic danger.
- Don't fetch web fonts, trackers, analytics, or decorative assets from external
  origins.
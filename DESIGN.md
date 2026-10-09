---
name: FastShop Marketing
description: Warm-paper conversion proof for a review-led path from business brief to live store.
colors:
  paper: "#f4f1e9"
  paper-bright: "#fbfaf6"
  ink: "#16201c"
  ink-soft: "#49534e"
  rule: "#c7c8be"
  fastshop-green: "#087f5b"
  fastshop-green-dark: "#05563e"
  mint-wash: "#d9eadf"
  night: "#13221c"
  focus-blue: "#005fcc"
  danger: "#b42318"
  white: "#ffffff"
typography:
  display:
    fontFamily: '"Archivo", "Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: "clamp(3.2rem, 5.6vw, 5.9rem)"
    fontWeight: 760
    lineHeight: 0.96
    letterSpacing: "-0.04em"
  headline:
    fontFamily: '"Archivo", "Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: "clamp(2.45rem, 4.6vw, 4.9rem)"
    fontWeight: 760
    lineHeight: 1.02
    letterSpacing: "-0.04em"
  title:
    fontFamily: '"Archivo", "Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: "clamp(1.55rem, 2.6vw, 2.5rem)"
    fontWeight: 760
    lineHeight: 1.08
    letterSpacing: "-0.025em"
  body:
    fontFamily: '"Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: '"Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "0.72rem"
    fontWeight: 800
    lineHeight: 1.55
    letterSpacing: "0.12em"
rounded:
  mark: "10px"
  control: "12px"
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
    backgroundColor: "{colors.fastshop-green}"
    textColor: "{colors.white}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "13px 22px"
    height: "50px"
  button-primary-hover:
    backgroundColor: "{colors.fastshop-green-dark}"
    textColor: "{colors.white}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "13px 22px"
    height: "50px"
  proof-card:
    backgroundColor: "{colors.paper-bright}"
    textColor: "{colors.ink}"
    rounded: "0"
    padding: "18px"
  index-chip:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "9px 13px"
---

# Design System: FastShop Marketing

## Overview

**Creative North Star: "The Registration Proof"**

FastShop's marketing surface feels like an editorial production proof laid on warm,
uncoated stock: direct, authored, and visibly checked before release. Condensed grotesque
headlines carry the promise while compact specimen annotations, ink rules, registration
crosshairs, and a quiet dot field make the act of review part of the visual language.

The world balances human warmth with operational evidence. Generated-store and CMS
proofs are built from native HTML and CSS rather than borrowed screenshots, so the page
demonstrates the same control it promises. Space is generous, accents are scarce, and the
overall tone stays confident without hiding the gates between draft, review, and go-live.

**Key Characteristics:**

- Warm paper planes with dark botanical ink and hairline rules.
- Self-hosted, width-variable Archivo display type paired with self-hosted Inter body copy.
- Registration crosshairs and dot-registration fields as a carried signature.
- Restrained FastShop green for action and status, with night ink structuring high-trust plates.
- Authored HTML/CSS product proof instead of third-party imagery or page assets.
- Conversion pacing: builder proof, verified capability evidence, workflow, safeguards,
  migration, CMS, pricing, and FAQ.

## Colors

The palette behaves like green and botanical inks on warm stock, with mint reserved for
the generated storefront and night ink reserved for operational or pricing structure.

### Primary

- **FastShop Green:** The sole action ink for primary calls to action, approved status,
  labels, and the compact brand mark.
- **Deep FastShop Green:** The interaction and high-contrast annotation shade for hover,
  step labels, and small status text.

### Secondary

- **Mint Wash:** A calm generated-store canvas that distinguishes the illustrative proof
  from the surrounding paper.
- **Night Ink:** A high-trust structural field for guarded commerce, the builder rail,
  capability evidence, migration review, and the pricing headline row.

### Neutral

- **Warm Uncoated Paper:** The default page ground and the material cue for the entire world.
- **Bright Proof Paper:** Raised proof sheets, panels, and reviewed work surfaces.
- **Botanical Ink:** Primary copy, outlines, rules that need authority, and the inverse footer.
- **Soft Ink:** Secondary copy and specimen metadata.
- **Press Rule:** Hairline separators, table logic, and quiet component boundaries.
- **Danger:** Semantic error ink only; it does not become a marketing accent.
- **Focus Blue:** A deliberately conventional, high-visibility keyboard focus indicator.
- **White:** High-contrast copy inside green controls and proof surfaces.

### Named Rules

**The Accent Restraint Rule.** Green signals action or trusted state. Night ink structures
high-trust evidence and the pricing headline row. Coral is retired; danger red is reserved
for actual error states, and neither night nor danger becomes ambient decoration.

**The Paper Is the Ground Rule.** Warm paper is the default plane. Bright white belongs
inside a proof, not across the whole page.

## Typography

**Display Font:** Self-hosted Archivo Variable, rendered through `--font-display` at a
condensed 72% width for marketing display, headline, and title styles. Narrow system faces
remain fallbacks only.

**Body Font:** Self-hosted Inter Variable through `--font-body`, followed by the native UI
sans-serif stack.

**Label Font:** The same UI sans-serif stack, set compact, heavy, tracked, and uppercase.

**Character:** Headlines are compressed and declarative, like a press proof's principal
line. Body copy remains familiar and highly legible; specimen labels behave like production
notes rather than miniature marketing slogans.

### Hierarchy

- **Display** (760, fluid oversized scale, 0.96 line height on desktop and 0.98 on mobile):
  Hero promises only, held to a narrow measure so the first viewport reads as a composed cover.
- **Headline** (760, fluid section scale, 1.02 line height): Major section openings and
  the pricing proposition.
- **Title** (760, fluid compact scale, 1.08 line height): Workflow steps and commerce proofs.
- **Body** (400, 1rem base, 1.55 line height): Explanations and evidence, generally capped
  near 61–70 characters per line.
- **Label** (800, compact scale, 0.12em tracking, uppercase): Steps, feature tags, launch
  state, proof captions, and specimen annotations.

### Named Rules

**The Local Type Rule.** The marketing page never makes an outbound font request.
`static/fonts.css` loads `static/fonts/Archivo-Variable.woff2` (100–900 weight,
62–125% width) and `static/fonts/Inter-Variable.woff2` (100–900 weight). Marketing
display styles use Archivo; body copy, labels, and controls use Inter.

**The Specimen Scale Rule.** Small type is metadata with a job: status, category, or proof
caption. Do not use tiny labels as ornamental texture.

## Layout

The desktop shell is capped at 1280px with 32px gutters. Hero, CMS, commerce, and signup
surfaces use asymmetric two-column compositions; the hero's builder proof occupies slightly
more weight than the promise. The artifact combines a compact builder rail, a dense generated
storefront, an AI update panel, and an overlapping commerce checklist so the product mechanism
is visible before the visitor scrolls. Long sections favor generous vertical intervals, then
use hairline rules to create dense, inspectable rows inside that space.

At 980px, paired compositions stack, commerce loses its sticky rail, and the shell narrows
to a reading-focused 760px maximum. At 640px, gutters tighten to 16px, the builder rail stays
visible beside a recomposed storefront, the AI side panel yields to the rail affordance, and
the checklist overlaps the artifact's upper-right edge. Navigation keeps brand, sign-in, and the in-view
signup route. No layout may introduce horizontal overflow.

The first viewport must contain the concrete brief-to-live-store promise, the authored builder
and generated-store proof, and visible sign-in and signup routes. Immediately below it, the
works-with strip and quantified capability band establish honest proof before the long-form
path continues through workflow, guarded commerce, reviewed migration, CMS, pricing, and
native FAQ disclosure.

**The Proof Before Promise Rule.** Every major claim should meet evidence, workflow, or a
visible review gate before the page asks for more trust.

## Elevation & Depth

The system is flat by default. Paper color changes, ink borders, and rule structure carry
most depth; shadows are reserved for authored product proofs that behave like physical sheets
placed above the page.

### Shadow Vocabulary

- **Hero Proof Lift** (`0 24px 60px rgba(23, 35, 29, 0.14), 0 6px 16px rgba(23, 35, 29, 0.08)`):
  The largest lift, reserved for the generated-store proof in the hero.
- **Workspace Proof Lift** (`0 22px 50px rgba(23, 35, 29, 0.12)`): Migration,
  CMS, and signup proof panels.
- **Launch Checklist Lift** (`0 16px 36px rgba(23, 35, 29, 0.18)`): The layered
  commerce checklist over the hero proof; its proof-hover state deepens to
  `0 20px 42px rgba(23, 35, 29, 0.22)`.

### Named Rules

**The Flat Evidence Rule.** Structural sections remain flat; only a literal proof artifact
may cast a shadow.

**The Restrained Lift Rule.** Interactive controls move upward by only 2px on hover and
never acquire ornamental glow or bounce.

## Shapes

The form language is primarily rectilinear: proof sheets, pricing plates, dark commerce
fields, tables, and rule-bounded rows keep square corners. Controls soften to a modest 12px
radius, the brand mark uses a tighter 10px corner, and categorical CMS chips become true
pills. Circles are limited to proof-window dots and readiness markers.

Registration crosshairs use two one-pixel ink rules in a compact square. They sit just
outside selected content blocks and proof sheets, behaving like production marks rather
than floating icons.

**The Press Geometry Rule.** Square editorial planes establish the system; rounded shapes
are reserved for controls, small marks, and discrete index items.

## Components

### Buttons

- **Shape:** A compact, gently rounded control with a 12px radius and a 50px default height.
- **Primary:** FastShop Green field, white copy, strong weight, and balanced 13px by 22px padding.
- **Hover / Focus:** Deep green plus a restrained 2px upward shift over 180ms; keyboard focus
  always uses the visible Focus Blue outline with a 4px offset.
- **Text link:** Botanical Ink with an underline that strengthens on hover; use it for
  secondary navigation beside a green primary action.
- **Small:** Header action with a 44px minimum height; it remains an adequate touch target.

### Chips

- **Style:** Transparent paper, Press Rule outline, Botanical Ink text, and a true pill shape.
- **Use:** An index of concrete CMS capabilities, not a removable filter or decorative tag cloud.

### Cards / Containers

- **Corner Style:** Square for proof sheets and editorial plates.
- **Background:** Bright Proof Paper over Warm Uncoated Paper; Mint Wash may identify a
  generated storefront canvas.
- **Shadow Strategy:** Only proof artifacts receive one of the documented proof lifts.
- **Border:** One-pixel Botanical Ink for the outer artifact and Press Rule for internal divisions.
- **Internal Padding:** Dense specimen rows use 16–24px; proposition plates use a fluid 48–82px.

### Navigation

The header is a single ruled line on near-opaque paper. Brand, sign-in, and signup availability
remain visible; descriptive anchor links sit between them on wide screens and disappear below
980px. Links underline more firmly on hover instead of changing into accent-colored decorations.

### Generated Store Proof

The signature hero artifact is an authored, accessible HTML/CSS builder specimen. A dark
builder rail and AI “Prepare update” affordance frame a dense generated storefront with store
navigation, editorial hero, art-direction placeholder, and draft products. A compact update
panel and overlapping commerce go-live checklist create physical depth and make review gates
visible. It is explicitly labeled illustrative and never masquerades as a customer screenshot.

### Honest Proof Layer

The hero is followed by a text-only works-with strip and a night-ink quantified capability
band. Integration names must match shipped provider boundaries. Every number must be derived
from code constants, validation invariants, or the live plan catalog; illustrative or rounded
marketing numbers are not allowed.

### Workflow and Migration Evidence

Each workflow step pairs its existing explanation with a compact state rail, and migration
pairs the connector list with a preview → review → apply artifact. These anchors demonstrate
state and sequence without introducing scripts or pretending to be customer evidence.

### Pricing Plate

Pricing uses a night-ink headline row above bright proof paper. The body lists the real Free,
Basic, and Pro quota values from `app/plans.py`; it never invents currency prices. Coral is not
part of the current marketing palette.

### FAQ Disclosure

Questions use native `details` and `summary` behavior, one per ruled row. A plus changes to
a minus when open; content stays in document flow and remains usable without script.

**The Native First Rule.** Prefer semantic browser behavior for disclosure, navigation,
focus, and reduced motion before introducing custom interaction machinery.

## Do's and Don'ts

### Do:

- Do use Warm Uncoated Paper, Botanical Ink, and hairline rules as the default material system.
- Do pair each product promise with an authored proof, explicit workflow, or visible review gate.
- Do carry registration crosshairs, specimen labels, and dot-registration fields across marketing surfaces.
- Do keep signup availability visible in the first viewport and state honestly when collection is unavailable.
- Do preserve keyboard focus, semantic disclosure, mobile recomposition, and reduced-motion behavior.
- Do keep page assets and self-hosted fonts local so rendering makes no third-party requests.
- Do trace every public capability number to a code constant, validation invariant, or live
  plan catalog value.

### Don't:

- Don't invent customer logos, testimonials, performance claims, third-party product screenshots,
  or capability numbers that cannot be verified in code.
- Don't reintroduce coral as a marketing accent; use green for action, night for structure,
  and danger red only for actual errors.
- Don't apply rounded cards and soft shadows to every section; most structure comes from paper and rules.
- Don't animate registration marks, proof fields, or disclosures as ambient spectacle.
- Don't replace the proof-led path with a generic feature grid or undifferentiated card wall.
- Don't fetch web fonts, trackers, analytics, or decorative assets from external origins.

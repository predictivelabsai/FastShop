---
name: FastShop Marketing
description: Editorial production proof for a review-led path from business brief to live store.
colors:
  paper: "#f4f1e9"
  paper-bright: "#fbfaf6"
  ink: "#16201c"
  ink-soft: "#49534e"
  rule: "#c7c8be"
  fastshop-green: "#087f5b"
  fastshop-green-dark: "#05563e"
  mint-wash: "#d9eadf"
  coral-plate: "#e86f51"
  night: "#13221c"
  focus-blue: "#005fcc"
  white: "#ffffff"
typography:
  display:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: "clamp(3.2rem, 5.6vw, 5.9rem)"
    fontWeight: 760
    lineHeight: 0.98
    letterSpacing: "-0.04em"
  headline:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: "clamp(2.45rem, 4.6vw, 4.9rem)"
    fontWeight: 760
    lineHeight: 1.02
    letterSpacing: "-0.04em"
  title:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: "clamp(1.55rem, 2.6vw, 2.5rem)"
    fontWeight: 760
    lineHeight: 1.08
    letterSpacing: "-0.025em"
  body:
    fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
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
  button-inverse:
    backgroundColor: "{colors.ink}"
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
- Condensed system-grotesque display type paired with compact specimen annotations.
- Registration crosshairs and dot-registration fields as a carried signature.
- Restrained FastShop green for action and status, with one coral pricing plate.
- Authored HTML/CSS product proof instead of third-party imagery or page assets.
- Proof-led pacing: evidence first, then workflow, safeguards, migration, CMS, pricing, and FAQ.

## Colors

The palette behaves like two inks on warm stock, with mint and coral reserved for
specific editorial plates rather than broad decoration.

### Primary

- **FastShop Green:** The sole action ink for primary calls to action, approved status,
  labels, and the compact brand mark.
- **Deep FastShop Green:** The interaction and high-contrast annotation shade for hover,
  step labels, and small status text.

### Secondary

- **Coral Pricing Plate:** A single high-attention editorial field used to interrupt the
  long-form proof sequence at pricing.
- **Mint Wash:** A calm generated-store canvas that distinguishes the illustrative proof
  from the surrounding paper.

### Neutral

- **Warm Uncoated Paper:** The default page ground and the material cue for the entire world.
- **Bright Proof Paper:** Raised proof sheets, panels, and reviewed work surfaces.
- **Botanical Ink:** Primary copy, outlines, rules that need authority, and the inverse footer.
- **Soft Ink:** Secondary copy and specimen metadata.
- **Press Rule:** Hairline separators, table logic, and quiet component boundaries.
- **Night Ink:** The guarded-commerce field where operational risk becomes visually explicit.
- **Focus Blue:** A deliberately conventional, high-visibility keyboard focus indicator.
- **White:** High-contrast copy inside green controls and proof surfaces.

### Named Rules

**The Accent Restraint Rule.** Green signals action or trusted state; coral appears only as
an editorial pricing plate. Neither becomes ambient decoration.

**The Paper Is the Ground Rule.** Warm paper is the default plane. Bright white belongs
inside a proof, not across the whole page.

## Typography

**Display Font:** Arial Narrow, with Aptos Narrow, Helvetica Neue, Arial, and sans-serif fallbacks.

**Body Font:** Inter when locally available, followed by the native UI sans-serif stack.

**Label Font:** The same UI sans-serif stack, set compact, heavy, tracked, and uppercase.

**Character:** Headlines are compressed and declarative, like a press proof's principal
line. Body copy remains familiar and highly legible; specimen labels behave like production
notes rather than miniature marketing slogans.

### Hierarchy

- **Display** (760, fluid oversized scale, 0.98 line height): Hero promises only, held to
  a narrow measure so the first viewport reads as a composed cover.
- **Headline** (760, fluid section scale, 1.02 line height): Major section openings and
  the pricing proposition.
- **Title** (760, fluid compact scale, 1.08 line height): Workflow steps and commerce proofs.
- **Body** (400, 1rem base, 1.55 line height): Explanations and evidence, generally capped
  near 61–70 characters per line.
- **Label** (800, compact scale, 0.12em tracking, uppercase): Steps, feature tags, launch
  state, proof captions, and specimen annotations.

### Named Rules

**The Local Type Rule.** The marketing page never makes an outbound font request. Its
condensed authority and body clarity must survive entirely on system and locally available fonts.

**The Specimen Scale Rule.** Small type is metadata with a job: status, category, or proof
caption. Do not use tiny labels as ornamental texture.

## Layout

The desktop shell is capped at 1280px with 32px gutters. Hero, CMS, commerce, and signup
surfaces use asymmetric two-column compositions; the proof occupies slightly more weight
than the promise. Long sections favor generous vertical intervals, then use hairline rules
to create dense, inspectable rows inside that space.

At 980px, paired compositions stack, commerce loses its sticky rail, and the shell narrows
to a reading-focused 760px maximum. At 640px, gutters tighten to 16px, multi-column proof
details recompose rather than shrink, and navigation reduces to brand plus the in-view signup
route. No layout may introduce horizontal overflow.

The first viewport must contain the concrete brief-to-live-store promise, an authored
generated-store proof, and a visible route to signup availability. The long-form visitor path
is proof, three-step workflow, guarded commerce, reviewed migration, CMS, pricing teaser,
then native FAQ disclosure.

**The Proof Before Promise Rule.** Every major claim should meet evidence, workflow, or a
visible review gate before the page asks for more trust.

## Elevation & Depth

The system is flat by default. Paper color changes, ink borders, and rule structure carry
most depth; shadows are reserved for authored product proofs that behave like physical sheets
placed above the page.

### Shadow Vocabulary

- **Hero Proof Lift** (`0 24px 60px rgba(23, 35, 29, 0.14), 0 6px 16px rgba(23, 35, 29, 0.08)`):
  The largest lift, reserved for the generated-store proof in the hero.
- **Workspace Proof Lift** (`0 22px 50px rgba(23, 35, 29, 0.12)`): CMS and signup proof panels.

### Named Rules

**The Flat Evidence Rule.** Structural sections remain flat; only a literal proof artifact
may cast a shadow.

**The Restrained Lift Rule.** Interactive controls move upward by only 2px on hover and
never acquire ornamental glow or bounce.

## Shapes

The form language is primarily rectilinear: proof sheets, pricing plates, dark commerce
fields, tables, and rule-bounded rows keep square corners. Controls soften to a modest 12px
radius, the brand mark uses a tighter 10px corner, and categorical CMS chips become true
pills. Circles are limited to proof-window dots and numbered progress markers.

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
- **Inverse:** Botanical Ink on the coral pricing plate, preserving the same size and shape.
- **Small:** Header action with a 44px minimum height; it remains an adequate touch target.

### Chips

- **Style:** Transparent paper, Press Rule outline, Botanical Ink text, and a true pill shape.
- **Use:** An index of concrete CMS capabilities, not a removable filter or decorative tag cloud.

### Cards / Containers

- **Corner Style:** Square for proof sheets and editorial plates.
- **Background:** Bright Proof Paper over Warm Uncoated Paper; Mint Wash may identify a
  generated storefront canvas.
- **Shadow Strategy:** Only proof artifacts receive one of the two documented proof lifts.
- **Border:** One-pixel Botanical Ink for the outer artifact and Press Rule for internal divisions.
- **Internal Padding:** Dense specimen rows use 16–24px; proposition plates use a fluid 48–82px.

### Navigation

The header is a single ruled line on near-opaque paper. Brand and signup availability remain
visible; descriptive anchor links sit between them on wide screens and disappear below 980px.
Links underline more firmly on hover instead of changing into accent-colored decorations.

### Generated Store Proof

The signature hero artifact is an authored, accessible HTML/CSS storefront specimen with a
window bar, draft status, store navigation, editorial hero, production notes, and metrics.
It is explicitly labeled illustrative and never masquerades as a customer screenshot.

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
- Do keep page assets local and system-font based so rendering makes no third-party requests.

### Don't:

- Don't invent customer logos, testimonials, performance claims, or third-party product screenshots.
- Don't turn green and coral into a broad multicolor palette or use coral for routine interaction.
- Don't apply rounded cards and soft shadows to every section; most structure comes from paper and rules.
- Don't animate registration marks, proof fields, or disclosures as ambient spectacle.
- Don't replace the proof-led path with a generic feature grid or undifferentiated card wall.
- Don't fetch web fonts, trackers, analytics, or decorative assets from external origins.

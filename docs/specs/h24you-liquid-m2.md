# Liquid M2 implementation specification

Continue the authorised Liquid storefront on h24you-lovable. Restore supplied local
assets first, then implement One, Fizz and Taste the Range as canonical editable
block documents with site-scoped sandbox commerce.

## References consulted

- Lovable src/content/images.ts: clean PNG logos at 144px, supplied payment SVGs,
  precise product photography mapping and retained source placeholder descriptions.
- Lovable product page/components and styles.css: section order, responsive layouts,
  typography, purchase selection, gallery controls and alternating pearl/rose rhythm.
- docs/H24YOU_LIQUID_THEME.md: M1 block architecture, independent fixture and cart flow.

## Work areas

- Assets and shared shell/block validation: primary agent.
- Product rendering, canonical product content and scoped CSS/JS: products task.
- Catalogue bundle representation, server pricing and tests: commerce task.
- Local scratch reference, screenshots and browser verification: reference task.

## Acceptance

Preserve reference copy and supplied imagery. Do not invent health claims or product
facts. Display FDA disclaimer beside starred claim content. Use catalogue minor-unit
prices and validated server-side bundle eligibility. Preserve CSRF and cart drawer.
Capture reference and storefront at 375, 768, 1024 and 1440 for five pages. Run Ruff,
compileall, full isolated pytest, browser verification and git diff --check. Commit
locally only. Update handover with exact results and remaining gaps.

## Boundaries

No M3 content, deployment, push, secrets or changes to the read-only reference.
Weekly cadence requires changes beyond the bounded subscription service allowance;
render supported monthly cadence only. Existing merchant documents remain intact.

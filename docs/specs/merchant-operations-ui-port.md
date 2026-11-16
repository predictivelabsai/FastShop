# FastShop merchant operations UI port

## Goal

Port the proven H24You Products, Orders, Customers and Marketing operating-system UI into
FastShop, together with the reusable admin polish and visual-editor link navigation.

## Design context

- Audience: merchants operating a storefront through FastShop.
- Mode: Operate. Merchants need fast scanning, predictable controls and clear next actions.
- Reference consulted: FastShop `DESIGN.md`, H24You release `ba58622` and the supplied H24You
  captures. Preserve FastShop's white canvas, wash background, hairline structure, compact
  Archivo headings, Inter body copy and evergreen action colour.

## Scope

1. Port the shared Products, Orders, Customers and Marketing layout, semantic hooks and
   responsive operating patterns from H24You where the generic FastShop routes support them.
2. Apply the shared shell corrections: a single Settings destination, fitting desktop
   navigation and accessible, readable assistant conversation titles.
3. Port same-site visual-editor link navigation so the selected destination opens in edit mode.
4. Extend FastShop route and browser evidence for populated and empty desktop/mobile states.

## Files expected to change

- FastShop admin route/UI modules, shared admin shell and platform/admin styles.
- FastShop editor client/route modules and isolated tests.
- FastShop browser verification scripts for merchant operations and editor link navigation.

## Acceptance checks

- The four operational areas use the shared header, actions, tabs, filters, tables and
  empty-state grammar without changing business rules or access boundaries.
- The desktop rail does not scroll by default and has one Settings entry; mobile navigation
  retains its explicit scrollable drawer behaviour.
- Same-site preview links take a merchant directly to the destination page editor, while
  external links and unsaved work stay safe.
- Ruff, focused pytest, compile, disposable Alembic validation, `git diff --check`, desktop and
  mobile browser evidence and a full isolated FastShop regression pass before the feature branch
  is pushed. Do not merge or deploy without explicit authorisation.

## Out of scope

- H24You-specific storefront copy, changes to payments or permissions, new commerce features,
  and FastShop production deployment.

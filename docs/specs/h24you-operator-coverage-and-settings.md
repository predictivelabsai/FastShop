# H24You operator coverage and settings design

## Goal

Give a merchant one dependable assistant surface for every merchant-managed store domain,
while retaining confirmation for consequential commerce and customer actions. Make the
Settings directory feel like a clear Shopify-style control centre rather than a flat list
of destinations.

## Design context

- Audience: H24You merchants managing a US ecommerce store, including non-technical
  operators who need to test and publish changes quickly.
- Jobs: change draft design and content, manage catalogue and marketing settings, find a
  control without knowing its route, and understand which requested change needs review.
- Direction: apply the existing FastShop design system in `DESIGN.md`: white cards on a
  green-tinted wash, evergreen only for action and verified state, pill actions, square
  hairline structure, dense tool typography, and no decorative gradients or card grids.
- Reference consulted: `DESIGN.md`. The Settings surface should reuse its platform-shell
  tokens and "proof before promise" principle, not introduce a second visual language.

## Scope

1. Add an explicit operator capability index to the assistant for all merchant-managed
   domains currently represented in Store Settings: general store data, pages and
   navigation, draft visual theme and shell content, SEO and tracking, catalogue,
   discounts, inventory, shipping, taxes, customers, subscriptions, notifications,
   policies, domains, channels, and integrations.
2. Turn requests that affect orders, refunds, customer records, discounts, taxes,
   shipping, subscriptions, or integrations into bounded review cards. Draft design and
   content remain immediate, reversible changes.
3. Make every unavailable or protected action explain the exact relevant Settings route
   rather than claiming success. Credentials, live payment mode, staff permissions and
   irreversible deletion remain protected and are never exposed to the model.
4. Redesign the Settings directory around clear groups, search, concrete status cues and
   an assistant entry point. Preserve every existing route and all tenant/CSRF guards.
5. Retain assistant token-usage logging and add per-tool output limits where the current
   transport lacks them, so the expanded capability remains measurable and bounded.

## Files expected to change

- `app/store_agent.py`, `app/store_agent_ops.py`, assistant review presentation and
  focused assistant tests.
- `app/admin_routes.py`, `app/admin_ui.py`, `static/platform.css`, Settings tests and
  browser evidence.
- Browser verification scripts and tests only where needed to demonstrate capability and
  responsive Settings navigation.

## Acceptance checks

- Natural-language prompts can discover the correct setting domain and generate either a
  reversible draft update or a review card with an exact before/after summary.
- The assistant never reveals credentials, bypasses confirmation for commerce/customer
  operations, or invents a completed change.
- Every Settings destination remains reachable, searchable and visually grouped at
  desktop and mobile widths without horizontal overflow.
- Focused pytest, Ruff, JavaScript syntax and browser checks pass. Reusable operator
  capability changes are ported to FastShop before the H24You release.
- Each verified milestone is committed and pushed to its working branch. Merge and
  deploy use the release approval in the active request.

## Out of scope

- Processing payments, changing live credentials, granting staff access, publishing
  legal copy without merchant text, or deleting business data without an explicit,
  separate approval.
- Inventing product claims, prices, images or regulated copy.
- Replacing the current visual editor or rebuilding unrelated storefront pages.

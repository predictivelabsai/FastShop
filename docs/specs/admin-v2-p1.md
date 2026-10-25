# Merchant Admin v2 P1

Build one server-rendered merchant shell for stores and site workspaces, with a real
dashboard, scoped global search and a settings directory. Keep mutation handlers,
permissions, CSRF and storefront rendering intact.

References consulted: `docs/admin-v2/01-fastshop-current-state.md` establishes the
route and security boundaries; `02-reference-shopify-woo-elementor.md` informs the
dark frame, rounded canvas, compact navigation and contextual save behaviour;
`03-gap-analysis-and-plan.md` limits this delivery to P1. The user's explicit
Shopify-style brief supplies the audience, workflow and visual direction.

Work areas: shared Python components and route integration; independent CSS and
JavaScript assets; independent dashboard/search/settings routes and tests. Work
stays on `merchant-admin-v2`, without dispatch branches, push or deployment.

Acceptance: membership-scoped data, usable desktop and mobile navigation, keyboard
search, dirty-form protection, existing pages in the shell, required unit and
browser checks, disposable SQLite migration, full lint and tests. Capture and
inspect screenshots at 1440x900 and 390x844.

Out of scope: schema expansion, deep commerce/editor redesign, storefront changes
and functionality planned for P2 onwards. Unavailable navigation uses explicit
release placeholders.

# Merchant Admin v2 functional gaps

Implement the ten P1/P2 functional gaps in section 2 of
`docs/admin-v2/05-post-build-audit.md`, excluding its out-of-scope row.

The read-only reference `docs/admin-v2/02-reference-shopify-woo-elementor.md`
establishes the existing shell, card-based detail pages, saved-view tabs and a
spreadsheet bulk editor. `04-qa-report.md` establishes the isolated browser and
provider-double acceptance patterns. Retain these patterns and the storefront's
visual design. Shared UI changes should be small and additive to accommodate
the separate UX polish worktree.

Implementation partitions:

1. Content: custom data, editor sources, storefront preferences and redirects.
2. Commerce: order editing and sandbox gift-card tender.
3. Catalogue/design: bulk editing and theme snapshots with editor context.
4. Administration/integration: saved views, locations, audit, migration and release checks.

New services, models, route registrars and feature-specific assets hold the bulk
of implementation. Existing routes register these and add owner cards/actions.
Use only migration `20261025_0034_admin_gaps`, chained from
`20261025_0033_merge_admin_v2`. Never change branch, push or deploy.

Acceptance includes scoped service and route tests, CSRF/role checks, checkout
and stock consistency, idempotency, migration upgrade/downgrade/upgrade on
disposable SQLite, full pytest, Ruff, compileall, JavaScript syntax and whitespace
checks. Browser journeys cover all ten features at desktop and mobile sizes,
with inspected screenshots in `output/playwright/admin-v2/gaps/` and no unexpected
console/network errors. Record exact results and remaining limitations in
`docs/admin-v2/06-gap-closure.md`.

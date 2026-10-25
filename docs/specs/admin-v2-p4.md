# Merchant Admin v2 P4

Implement site-owned discounts, subscription management, analytics, content indexes
and merchant settings within the existing FastHTML shell. All money uses integer
minor units. Existing Voucher checkout and Stripe live tax remain compatible.

References consulted: docs/admin-v2/01 describes existing service boundaries and
permissions; 02 establishes compact merchant tables, grouped forms and a summary
sidebar; 03 defines the P4 scope. P1, P2 and P5 verification notes establish shared
shell conventions, isolated SQLite checks and editor/media integration.

Implementation is split across discount, content, reporting/subscription and settings
modules. Existing registrars retain their routes. Shared navigation links to the new
screens. Schema revision 20261025_0032_growth_settings descends from 20261025_0030,
independently of the parallel P3 branch.

Acceptance: rule maths, schedules, limits, checkout and redemption tests; tenant and
CSRF permission checks; shipping/tax selection; escaped notification placeholders;
menu validation; real reporting aggregates; existing subscription service actions.
Run Ruff, compileall, disposable Alembic upgrade/downgrade/upgrade, full pytest,
Node syntax checks, git diff --check and desktop/mobile browser journeys.

Out of scope: P3 order/customer models and routes, live provider operations,
deployment, pushing, branch changes and production data.

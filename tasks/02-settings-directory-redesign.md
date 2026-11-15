# Task 02: settings directory redesign

**Builder:** Codex

Redesign the existing Settings directory as the grouped, searchable control centre in
`docs/specs/h24you-operator-coverage-and-settings.md`.

Own `app/admin_routes.py`, `app/admin_ui.py`, `static/platform.css` and Settings-focused
route tests. Do not edit the assistant registry or operation handlers.

Acceptance: preserve all existing destination routes and access controls; group settings
by merchant task; provide clear search and an assistant entry point; match `DESIGN.md`;
remain usable at desktop and mobile widths with no horizontal overflow.

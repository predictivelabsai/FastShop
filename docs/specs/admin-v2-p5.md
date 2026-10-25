# Merchant Admin v2 P5: visual page editor

Independent work packages, all in this checked-out worktree:

1. Documents: schema registry, strict nested widgets/styles, both renderers and
   backward compatibility tests.
2. API: session/role/CSRF/site boundaries, unsaved rendering, optimistic drafts,
   publication, revisions, theme settings and media, with isolated route tests.
3. Client: standalone editor chrome, canvas bridge, document/history state,
   schema settings, drag/drop, inline editing, device preview and persistence.
4. Integration: shell/page links, recursive publication compliance, browser
   verification, screenshots, whole-project checks and logical commits.

Acceptance follows the user brief, including every registered block having a
valid schema/default, depth and node caps, old seeded pages rendering, API boundary
tests, browser editing/persistence/publish/restore and screenshot inspection at
1440x900, 1280x800 and mobile canvas size. Run Ruff, compileall, disposable SQLite
Alembic upgrade, full pytest, JavaScript syntax checks and git diff --check.

Out of scope: catalogue routes/models, commerce changes, migrations unless strictly
necessary, deployment, pushing and branch switches. No environment files or
credentials are read. Existing classic editor remains reachable.

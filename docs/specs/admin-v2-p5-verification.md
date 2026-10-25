# Visual editor verification

Work stayed in `fastshop-admin-editor` on `merchant-admin-v2-editor`. No branch
switch, push, deployment, commerce-model change or migration was required.

## Delivered

The full-screen editor uses P1 tokens and the real default storefront
renderers. It includes a nested section tree, widget library, drag/drop, inline
text, schema-generated settings, media upload/library, resource links, responsive
canvas, history, keyboard controls, autosave, version conflicts, publication and
revision restoration. Pages and Online Store link to it; Classic editor remains
available. The registry covers all 31 core block types, including 16 new widgets.

Draft design settings are separate from published settings. Publishing promotes
only validated design tokens alongside the selected page; other draft site
settings remain private. Nested and formatted content passes publication wording
checks. Optional theme sections are registered separately by the deploying repository.

## Isolation and commands

Before importing application code, verification disabled environment-file loading,
cleared provider tokens and `DB_URL`, selected development mode, disabled scheduler
startup, and explicitly enabled schema creation in disposable SQLite storage.
The browser verifier sets all of these itself and cleans up its temporary database.

```powershell
$env:PYTHON_DOTENV_DISABLED='1'
$env:PYTHONUTF8='1'
$env:DB_URL=''
$env:FASTSHOP_ENV='development'
$env:FASTSHOP_AUTO_CREATE_SCHEMA='1'
$env:FASTSHOP_ENABLE_SCHEDULER='0'
# FASTSHOP_DATA_DIR points to a new disposable directory for each verification run.
# XAI, Postmark, Stripe and platform billing tokens are empty.
uv run ruff check .
uv run python -m compileall -q app
uv run alembic upgrade head
uv run python -m pytest
node --check static/editor/editor.js
node --check static/editor/canvas.js
git diff --check
uv run python scripts/verify_admin_v2_editor_browser.py
```

Ruff, compilation, JavaScript syntax and whitespace checks passed. Disposable
Alembic migration reached `20261011_0029`. The final full suite used four workers
with a separate application database directory per worker:

```powershell
uv run --with pytest-xdist python -m pytest -n 4 --dist loadfile -p _p5_test_isolation --durations=12
```

The temporary `_p5_test_isolation.py` plugin used `pytest_configure` to set
`FASTSHOP_DATA_DIR` to `output/p5-final-pytest/<workerid>` before test collection.
It imported only `os` and `pathlib` and obtained the worker ID from
`config.workerinput`. The final fresh run used `p5-final-pytest-clean` to keep its
storage separate from earlier attempts. The temporary plugin was deleted after
verification.
No test assertion was skipped or weakened. Focused editor, document, compliance
and shell tests also passed independently after integration.

Final full-suite result: **862 passed, 1 skipped, 4 warnings in 330.45 seconds**.
The warnings repeat the existing Starlette/AnyIO deprecation once per worker.
The final compliance, editor API and original storefront regression subset also
passed: **26 tests**, including the final plain-text angle-bracket regression.

## Browser evidence

All six screenshots were inspected in `output/playwright/admin-v2/p5/`:

The original storefront hash regressions caught unconditional widget CSS and
editor hooks in classic previews. CSS now loads only for documents that need it,
and the visual editor explicitly requests its hooks. Existing golden fixtures were
left unchanged.

## Open questions and follow-ups

The checked-out catalogue uses `Category`, so the collection picker explicitly
labels its adapter as Product category. After P2 merges, adapt the resource picker
and product-grid grouping to real collections while retaining support for stored
category IDs. No catalogue models or routes were changed in this worktree.

Media uploads reuse the existing JPEG/PNG/WebP image library. Video widgets accept
HTTPS embeds or local media-file URLs. No third-party runtime JavaScript was added.

Automatic approval review rejected native PowerShell cleanup of disposable output
directories with the reason `blocked by policy`. The untracked `output/p5-*-data`,
`output/p5-final-pytest` and `output/p5-final-pytest-clean` directories remain excluded
from commits and need cleanup when that restriction permits it. No database,
temporary plugin, cache or environment file is included in the commits.

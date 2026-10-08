# Phase 0b — Site navigation menus

## Schema and validation

`SiteMenu` stores `tenant_id`, `site_id`, a unique per-site `name`, timestamps,
`items_json` (draft) and nullable `published_items_json`. The site foreign key
uses `ON DELETE CASCADE`. Names use up to 40 lowercase letters, numbers and
hyphens. Header and footer are the two rendered locations; other named menus
can be stored and read through the merchant API.

Items are an ordered JSON list, bounded at 40 entries. This keeps ordering and
publication atomic without a separate position column. The typed `MenuItem`
contract in `app/site_menus.py` requires a stable, unique item ID, a localized
label, and one of these targets:

- `page`: a `path` resolving to a SitePage in the same site and tenant.
- `anchor`: a page `path` and visible `block_id` from its canonical document.
- `external`: an HTTPS or mailto `url`, checked with the existing `safe_url` guard.

Unknown fields, duplicate IDs, malformed locale maps, missing default-locale
labels, oversized lists/text and invalid targets are rejected. Shape validation
also runs on ORM assignment. Database-backed target validation runs in the menu
service, shared by forms and the API. All owned menu and page queries include
both site and tenant scope. Membership checks guard merchant access; publication
requires a merchant or administrator. Site.version serializes writes and rejects
stale forms/API requests on SQLite and PostgreSQL.

Labels use Phase 0's plain-string or `{locale: text}` representation and locale
helpers. Seeded labels remain plain strings. The form edits the default locale
and preserves other translations; the JSON API accepts complete locale maps.
Missing requested translations resolve to empty text, as in Phase 0.

## Migration and compatibility

Revision `20261008_0017` follows the repository's model-table/checkfirst migration
style and adapts existing site rows. PostgreSQL uses the existing `fast_shop`
search_path and Alembic version_table_schema setup; no cross-schema writes are
introduced. SQLite supports upgrade from both an empty database and a populated
pre-menu database.

`adapt_navigation(db, site)` imports the existing `navigation` settings into
default header and footer menus, preserving order and labels. Draft settings and
published settings are imported separately. Existing menus are never overwritten.
The helper runs after demo-site creation, after H2 seed creation, when an existing
H2 seed is found, during the migration, and through the explicit merchant import
action. The migration enumerates sites; each adaptation operates within that
site's tenant scope.

If old navigation cannot meet the new target/bounds rules, adaptation leaves it
intact for legacy rendering instead of dropping links. The merchant import action
reports this condition and directs the merchant to select valid page targets.
Legacy settings are retained for compatibility, but current menu editors and
builder navigation commands no longer write navigation into settings.

Preview rendering uses draft menu items; public rendering uses published items.
An absent menu, or a menu without a published snapshot in public mode, falls back
to the corresponding settings navigation. An explicitly published empty menu
renders no links. Header and footer publish independently using the menu screen;
publishing shared settings does not publish menu drafts.

The existing Shop dropdown and footer layout remain. Footer menus replace the
Explore links; the existing legal/help links remain in their separate column.
Anchor links use `#block-{id}`. Only blocks referenced by the rendered menus gain
an HTML ID, so default menus do not change any block markup.

## Builder and API

Both the classical site editor and builder link to `/admin/sites/{site_id}/menus`.
The screen shows named menus, item counts, publication availability, expandable
item forms, page/block selectors, target-type selection, add/remove controls and
Move up/Move down buttons. It reuses the existing platform shell and form styles.
Labels and selectors have accessible names. Form mutations use CSRF checks and
post-redirect-GET; errors are URL-encoded into the `notice` query parameter.

The route registrar follows `register_menu_routes(rt, actor, csrf, check_csrf,
shell, error)`. Its session-authenticated JSON endpoints are:

- `GET /admin/sites/{site_id}/menus.json`: `{version, menus}`, mapping menu names
  to their ordered draft item lists.
- `POST /admin/sites/{site_id}/menus/{name}/api`: `{csrf_token, version, items,
  action}`. Action is `replace` (default) or `publish`; publishing validates both
  draft and published anchor targets. Successful writes return the new version;
  invalid requests return HTTP 400 with an error. Reordering uses the complete
  ordered item list, retaining item IDs.

The existing builder navigation command now writes the draft header menu through
the same service. Matching destinations retain IDs and non-default translations.
Builder snapshots and undo include draft menus. Historical snapshots without
menus continue to compare and restore their original fields; they do not own
new menu state. Undo never changes published menu snapshots and refuses to delete
a published menu.

## Regression and verification

Before changing either renderer, 50 normalized HTML SHA-256 baselines were
captured in `tests/fixtures/phase0b_storefront_sha256.json`: all 17 H2 pages and
8 demo pages in preview and published modes. They matched the existing Phase 0
fixture at capture time. Post-change tests assert actual seeded menus exist and
compare all 50 menu-backed renders against those hashes. A separate test removes
menus and compares all 50 legacy-fallback renders against the same baselines.
Normalization only replaces volatile database/preview block IDs and fixes article
ordering, following the existing harness.

Verification on 2026-10-08:

- Ruff and compile checks: passed.
- Full isolated-database suite: 310 passed, 1 skipped, 0 failures (94.50 seconds).
  The optional presentation test skips because python-pptx is unavailable; the
  existing Starlette/AnyIO BlockingPortal deprecation warning remains.
- Empty disposable SQLite: upgrade through `20261008_0017` passed; `alembic check`
  reported no new upgrade operations. The disposable database was deleted.
- Populated legacy SQLite migration: tested through an actual Alembic subprocess;
  header/footer data persists after the migration transaction ends.
- Menu tests cover localization, validation, bounds, ownership, publication,
  anchor/external rendering, empty-vs-missing fallback, adaptation, undo, cascade
  deletion, forms, JSON API, CSRF, permissions and stale writes.
- Browser evidence: `output/playwright/phase0b-menus/`, produced by the extended
  `scripts/verify_site_browser.py`, including storefront desktop/tablet/mobile and
  desktop/mobile menu editor captures. All 53 checks passed with zero failures,
  including adding an anchor, reordering, publishing, and resolving its public
  target. Desktop/mobile editor and storefront screenshots were visually inspected.
- `git diff --check HEAD`: passed. Changed text files end with one newline.
- No live PostgreSQL instance was used. PostgreSQL isolation follows the existing
  migration environment and foreign-key conventions.

## Follow-ups

- Multi-locale form controls remain outside this slice; complete locale maps can
  already be edited through the API.
- Page/block removal or hiding after menu publication can invalidate an existing
  destination. A future reference-aware page editor can warn before those changes.
- Additional menu placements, nested item trees, and a menu-specific publication
  history are future work. Current menus are flat and draft undo is shared with
  the builder.
- Legacy rows that cannot be imported automatically need merchant correction.
  Legacy settings remain available until that menu is configured and published.

## Changed files

- Model/services/seeding: `app/models.py`, `app/site_menus.py`, `app/content.py`,
  `app/site_seed.py`.
- Migration: `migrations/versions/20261008_0017_site_menus.py`.
- Rendering/editor: `app/site_ui.py`, `app/site_menu_routes.py`,
  `app/site_routes.py`, `app/site_builder_routes.py`,
  `app/site_builder_services.py`, `app/integrations/site_builder_llm.py`.
- Tests/evidence: `tests/test_site_menus.py`, `tests/test_site_builder.py`,
  `tests/test_site_content_render.py`,
  `tests/fixtures/phase0b_storefront_sha256.json`,
  `scripts/verify_site_browser.py`, `output/playwright/phase0b-menus/`.
- Documentation: this file.

No commerce/payment behavior or money representation changed. No commit created.

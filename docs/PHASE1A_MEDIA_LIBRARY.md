# Phase 1a — Site media library

## Existing media mapping

Exploration found a media library stub already in `app/site_routes.py`, backed by
`SiteMedia` in `app/models.py` (introduced by migration `20260921_0002`). Uploads
were not filesystem files: the merchant POST decoded a JPEG, PNG or WebP with
Pillow, rejected input over 8 MiB or 25 megapixels, resized within 1800 × 1800,
converted to RGB WebP at quality 85, and stored bytes in `SiteMedia.data`.
`storage_key` was a generated `.webp` name, not a filesystem location. `size`
measured the resulting blob. The serving route was `/site-media/{site_id}/{id}`.
That URL remains stable; unpublished uploads require merchant membership, while
published page/settings references permit public access under the existing rules.
Responses retain private/no-store caching and nosniff headers.

H2 seed documents instead reference assets under `/static/h24you/`, including
images, video and posters. They had no media rows. Other content can contain
HTTPS media URLs. Page draft/published JSON and revision JSON contain URL strings
or locale maps in image/poster/video fields and gallery lists; nested entries also
have image/alt fields. Site settings and builder change-set snapshots can retain
URLs. The builder service edits this content but has no separate upload backend.
`app/content.py:validate_media_ownership` remains the authoritative check on
`/site-media/` references, including every translation and nested value.

## Schema and storage

This slice extends the existing model rather than replacing it:

- Existing tenant/site ownership, blob, content type, byte size, title, placeholder
  flag and timestamps remain intact. Blank title is supported by the forms.
- `public_url` is a nullable URL for registered static/remote assets, unique per
  site. For existing and new uploads it stays null: `media_url()` derives the
  original serving URL from the unchanged IDs.
- `localized_alt` accepts plain text or a locale map, validated with the Phase 0
  locale/text rules (400 characters per locale). Null falls back to legacy `alt`.
  The form edits the site's default locale and preserves other translations.
  Legacy `alt` stays readable for existing consumers.
- The site foreign key now uses `ON DELETE CASCADE`.

Migration `20261008_0018` follows model-table `checkfirst` conventions, inspects
legacy columns, adds the metadata and uniqueness constraint, and replaces the
site foreign key using Alembic batch alteration for SQLite compatibility. It
preserves existing blob bytes, IDs and alt strings. Fresh installs already create
the current model via the older checkfirst migrations, so the new migration skips
redundant schema operations. PostgreSQL continues using the configured fast_shop
search path; no other schema is accessed.

## Backfill

`app/site_media.py:backfill_media` runs beside navigation adaptation on new demo
creation, new H2 creation, existing H2 seed discovery, and in the new migration
for existing sites. It scans draft/published page documents and historical
revisions, plus settings and undo snapshots. Both legacy sections and canonical
blocks are traversed, including localized URLs, galleries and nested images.

Registration preserves the exact URL and never assigns back to content. Existing
upload rows are reused. Static files remain in their existing location; their byte
size is read only after resolving the path inside the static directory. Remote
references are registered without fetching them; size zero means unknown and MIME
is inferred from the URL. No remote downloads or new storage backend are added.
URLs over the registry's 2048-character limit, unsafe URLs, static traversal paths
and unknown/foreign upload references are skipped. Library alt metadata is bounded
to 400 characters per locale without modifying content alt text. The first
encountered metadata wins; existing records are never overwritten. Per-site URL
uniqueness and the existing-URL set make repeated backfills idempotent.

## References and deletion

All owned queries include tenant scope; site-owned models also include site scope.
Reference checks recursively inspect all translations and nested values in:

- Page drafts and published snapshots.
- Site settings and their published snapshot.
- Page revisions and builder before/after snapshots.
- Draft and published menus.
- Tenant product image URLs (conservatively protecting shared catalog references).

Deletion compares exact stored URLs and the canonical upload URL. It refuses with
a notice identifying the referencing locations. Metadata/delete forms use the
existing site write lock. Content removal never cascades to media: unreferenced
assets remain available for reuse, while historical references still protect
assets needed for restore. Removing a registered static/remote row never deletes
the shared static file or external resource. No automatic cleanup action is added.

## Merchant UI and editor

`register_media_routes` extracts the existing upload/serving routes into
`app/site_media_routes.py`, using the shared actor, CSRF, shell and error helpers.
The library at `/admin/sites/{site_id}/media` has image thumbnails, preview links,
copy-URL controls with a manual-copy fallback, byte sizes, reference status,
editable title/default-locale alt, upload, and guarded delete. Mutations check CSRF
and use post-redirect-GET with notices. The incumbent merchant grid/form styles
provide desktop and mobile layouts.

The classical block editor retains manual URLs and adds accessible library
selectors for image, poster, product gallery and nested entry image fields. A pick
inserts the unchanged URL; gallery picks append a line. Empty alt inputs receive
library alt as a starting point; entered alt remains editable and is saved in the
block's default locale while preserving translations. Nested entry alt is editable
as well. A link opens the full library from the page editor. Builder section
patches now permit alt through the same validated block path. Existing ownership
validation on save remains unchanged; no shopper input is accepted as media URLs.

Menus and their rendering are unchanged. Commerce/payment logic is unchanged.

## Verification

Verification on 2026-10-08:

- Ruff: all checks passed. Compile checks for app, tests, migrations and scripts
  passed; `git diff --check HEAD` passed.
- Full isolated-DB suite: **322 passed, 1 skipped, 1 warning** (181.45 seconds).
  The optional presentation dependency test skips; the existing Starlette/AnyIO
  BlockingPortal deprecation warning remains. DB_URL was cleared, development and
  auto-create enabled, a fresh temporary data directory used, and XAI, Postmark
  and Stripe keys cleared in the same PowerShell session as pytest.
- After the final control-spacing adjustment, all 15 media/render tests passed.
- All 50 pre-existing storefront HTML hashes still match the unchanged Phase 0
  and Phase 0b fixtures. The added golden test asserts the H2 library is populated,
  repeats the backfill, then compares every preview/published render unchanged.
- Empty disposable SQLite: upgrade through `20261008_0018` and `alembic check`
  passed; no new upgrade operations. The temporary database file was deleted.
- Populated pre-media SQLite: an actual old-shape media table with a stored blob,
  plus existing page content, upgrades successfully; original bytes/alt/URL survive,
  static media is registered, and `alembic check` is clean. The test deletes its
  database file after closing the engine.
- Tests cover tenant ownership, unpublished upload privacy, valid/invalid/oversized
  uploads, CSRF rejection, localized metadata editing, deletion with references
  in all supported snapshots, content-removal retention, seed idempotency and
  database cascade deletion.
- Browser evidence is under `output/playwright/phase1a-media/`, generated with
  `scripts/verify_site_browser.py --merchant`. The full run covers 51 storefront
  page/device combinations plus menu and media workflows. Desktop/mobile library
  and picker screenshots are included. Full browser run: **53 checks, zero
  failures** (`verification.json`). After final spacing and accessible-label
  fixes, the merchant-only refresh passed both workflows with zero failures
  (`verification-merchant.json`), including image/poster selection, repeated gallery
  picks, persisted alt text, and referenced-delete refusal on desktop/mobile.
  Desktop/mobile library captures were visually inspected after the refresh.
- No live PostgreSQL instance was used. The migration retains the repository's
  existing PostgreSQL schema isolation conventions.

## Follow-ups

Upload quotas and pagination/search; image variants and dimension metadata;
explicit remote-asset metadata refresh; multilingual UI; replacement/versioning;
reference indexing for large sites; and history-retention controls before offering
bulk unreferenced cleanup. Library metadata edits intentionally do not rewrite
existing block alt text. Exact URL matching does not parse URLs embedded inside
freeform prose; the supported media fields use structured URL values.

## Files changed

- `app/models.py`, `app/site_media.py`, `app/site_media_routes.py`.
- `app/content.py`, `app/site_seed.py`, `app/site_routes.py`,
  `app/site_builder_services.py`.
- `migrations/versions/20261008_0018_media_library.py`.
- `tests/test_site_media.py`, `tests/test_site_content_render.py`.
- `scripts/verify_site_browser.py`, `output/playwright/phase1a-media/`.
- This document.

Changes are left in the working tree. No commit was created.

# Phase 0 — Content model foundations

Page documents now have a canonical JSON envelope:

```json
{
  "version": 1,
  "title": "Our story",
  "description": "About the shop",
  "blocks": [
    {
      "id": "intro",
      "type": "hero",
      "version": 1,
      "role": "introduction",
      "layout": "default",
      "heading": {"en": "Welcome", "et": "Tere"},
      "body": "Our story starts here."
    }
  ]
}
```

`version` on the document is the content format version. It is independent of the
existing optimistic concurrency counters on Site and SitePage. An optional block
version is retained from legacy sections and defaults to 1; this slice does not
introduce block-level concurrency. Page metadata stays at the top level.

## Block schema

`app/site_blocks.py` defines the typed Block contract, immutable BlockSpec registry,
validation, adapters, and editing functions. The 14 registered types map directly
to existing renderers: hero, text, split, products, facts, claims, reviews, articles,
research, faq, team, contact, product, and references. Facts, claims, reviews,
products, and articles retain their existing site-setting/database data sources.

IDs and types are required in canonical blocks. IDs are unique within a page and
remain unchanged through edits and reordering. Optional role and layout strings
are reserved metadata; they do not change rendering in this slice. Text fields are
heading, eyebrow, body, button, image, video, mobile_video, poster, link, category,
and alt. Hidden and subscription_preview are booleans. Gallery is a list of up to
12 localized image URLs. Nested items contain typed heading/body/url/theme/image/
alt/label/value fields: FAQ and team entries require heading; research and reference
entries require url. The registry rejects unknown types, unknown block/entry fields,
invalid field types, duplicate IDs, unsafe URLs in every translation, invalid locale
keys, and unsupported canonical document versions. Pages allow up to 60 blocks;
items allow up to 200 entries. Existing URL and media-ownership safeguards remain.

The pure functions `get_block`, `patch_block`, `reorder_blocks`, `add_block`, and
`remove_block` return detached values and never mutate their input. Patches cannot
change id or type. Reordering requires every current ID exactly once. Adds allocate
a fresh ID when omitted. Both classical forms and chat commands use these functions;
existing section-named form fields, chat commands, routes, and CSRF checks remain.
Chat retains its existing five-type insertion allowlist and 8,000-character copy limit.

## Locales

All block text and URL slots, including nested entries and gallery URLs, accept a
plain string or a nonempty `{locale: string}` map. Page title, description, image,
and category accept the same representation. Structural fields (id/type/role/layout,
booleans, versions, lists) are not locale maps. Locale tags use a bounded language-tag
syntax such as en, et, or en-US; keys are matched exactly.

`default_locale` lives in Site.settings_json and its published settings snapshot.
New demo and H2 sites explicitly use `en`; existing settings fall back to `en`
without a write. ORM settings assignment validates an explicitly supplied locale.
`default_locale(site, preview=...)` chooses the draft or published setting.
`resolve_document` resolves content before rendering and displaying editor fields.
Plain strings remain plain default content; a map missing the requested locale
resolves to empty text instead of selecting an arbitrary translation. Saving requires
a nonempty title in the site's draft default locale.

A default-locale form/chat text edit preserves other translations using
`merge_localized`. Forms retain original nested entry indexes before deletion so
translations stay attached to the correct entry. Programmatic nested list patches
replace the list; when using the locale merge option, entries align by position.
Callers reordering/removing localized nested entries should supply complete entries
without that option. Multi-locale controls and nested-entry identities are future work.

## Storage and compatibility

`BlockDocumentJSON` is a SQLAlchemy JSON TypeDecorator, with matching SitePage
assignment validation. Both draft_json and published_json normalize on reads and
writes. The physical columns remain JSON: no schema change or Alembic revision is
needed. Existing PostgreSQL fast_shop search_path and version_table_schema behavior
is untouched. No commerce or money representation changed.

Legacy `{sections: [...]}` documents and bare section lists normalize on read.
Existing IDs, page metadata, publication timestamps, and block data are retained.
Missing legacy IDs use deterministic position-based UUIDs, scoped by the containing
page. New pages and additions receive fresh IDs. Reading does not dirty the ORM row
or rewrite stored JSON; the next explicit save writes canonical content. Bare lists
carry no page metadata, so callers must supply a title before saving as a page.

New revisions and builder snapshots contain canonical documents. Restore accepts
legacy revisions, and builder stale/undo comparisons normalize historical snapshots.
Seed and catalog helpers may continue supplying legacy-shaped input through the
validated creation boundary. Compliance scanning understands both envelopes and
checks translations; public rendering, article cards, placeholder inventory, builder
selectors, and chat evals now consume blocks. Eval reports explicitly write UTF-8
so their existing symbols work on Windows.

## Regression evidence

The golden JSON fixture in tests/fixtures/phase0_storefront_sha256.json contains
SHA-256 hashes captured from the pre-change renderer for all 17 H2 pages and all
8 demo pages, each in preview and published mode (50 renders). The test fixes article
ordering and replaces generated UUIDs/preview block IDs before comparing HTML bytes.
All other markup must match. Separate tests cover legacy raw SQL rows without read
rewrites, canonical persistence, malformed data, localized URLs, pure operations,
translation preservation, tenant targeting, and historical undo.

Browser evidence is written to output/playwright/phase0-content-model by the existing
verify_site_browser.py script (desktop, tablet, mobile). Verification on 2026-10-08:

- Ruff: all checks passed.
- Full isolated-DB suite: 288 passed, 1 skipped, 1 warning (74.28 seconds).
  The optional user-guide test skips because python-pptx is not installed; the
  warning is the existing Starlette/AnyIO BlockingPortal deprecation.
- All 50 normalized HTML baselines matched.
- Browser verification: 51 checks, zero failures; desktop/mobile home screenshots
  also visually inspected. These are post-change screenshots, not pixel diffs;
  unchanged rendering is established by the pre-change HTML golden comparisons.
- compileall for app, tests, evals, and migrations passed.
- Disposable SQLite Alembic upgrade through 20261007_0016 passed; alembic check
  reported no new upgrade operations. The disposable database was deleted.
- git diff --check passed. No PostgreSQL instance was used for this validation.
- Changes remain in the working tree; no commit was created.

## Phase 0b

Menus are deliberately deferred to keep this slice reviewable. A follow-up should
add SiteMenu, site-scoped ordered items with localized labels and page/block-anchor
targets, migration/default-menu creation from existing navigation settings, and
header/footer integration. Existing navigation continues rendering from settings.
Multi-locale editing UI, new block renderers, and changing role/layout behavior are
also outside this slice.

## Changed files

- Schema/storage: app/site_blocks.py, app/site_block_storage.py, app/models.py,
  app/content.py.
- Editors: app/site_routes.py, app/site_builder_routes.py,
  app/site_builder_services.py, app/integrations/site_builder_llm.py.
- Consumers and seeds: app/site_ui.py, app/site_placeholders.py,
  app/compliance.py, app/site_seed.py.
- Evals/tests: evals/site_builder_chat.py, tests/test_site_builder.py,
  tests/test_site_blocks.py, tests/test_block_builder.py,
  tests/test_site_content_render.py,
  tests/fixtures/phase0_storefront_sha256.json.
- Documentation/evidence: this document and output/playwright/phase0-content-model/.

Pre-existing edits to CLAUDE.md and docs/FASTSHOP_PRODUCT_PLAN.md were preserved.

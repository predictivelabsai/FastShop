# Phase 1b — Blog depth

## Existing article mapping (recorded before implementation)

`app/site_articles.py` contains the three H2 seed article dictionaries, not a
service or a separate article table. `site_seed.py` creates `SitePage` rows of
kind `article` at `/blogs/learn/{slug}`. The blog index is a `kind=blog` page at
`/blogs/learn`, with an introductory text block and an `articles` block. Article
documents contain title, category, image, text blocks and a references block
headed “Studies referenced”. Phase 0 normalizes documents to canonical `blocks`.

`site_routes.public` resolves a site by globally unique slug and eligible status,
then a page by tenant, site and path, requiring `published_json`. `storefront`
renders the published snapshot; authenticated preview renders `draft_json`.
The `articles` block reads tenant/site pages, sorts by published_at (created_at
fallback, ID tie-break), and renders three cards. The old article heading uses
the site's team byline. There is no separate editorial state or author record.

`content.save_page` checks membership (merchant/admin for publish/unpublish),
optimistic page version, document validation and media ownership. Publication
scans content for compliance, copies the draft into `published_json`, preserves
the first publication timestamp, and records a revision. Saving a draft leaves
the public snapshot intact; unpublish clears it. Builder snapshots and restore
already carry page JSON. These boundaries remain the authority.

## Design

Add site-owned `BlogCategory` rows with unique `(site_id, slug)`, a plain name,
tenant ownership, and cascading site deletion. Store a validated `blog` object
inside each article document: `state`, `category_slug`, `tags` (slug list),
`author_name`, and optional `author_bio`. This keeps metadata atomic with page
drafts, publication, revisions and builder undo, with no second publication API.
Article deletion naturally removes its metadata. No category images are added.

Migration `20261008_0019` follows `0018`, creates `blog_categories` with
`checkfirst`, and backfills each site's taxonomy. PostgreSQL continues to use
the existing `fast_shop` search path and Alembic version-table schema. No
cross-schema operations or changes to the menus/media migrations are introduced.
Names are limited to 100 characters, slugs to 80, author names to 160, biographies
to 2,000, and tags to 20 unique slugs. Category labels entered in the existing
article form create/reuse a site category; Unicode labels are ASCII-folded for
URLs, with LEARN/learn as the empty-label fallback. Identical normalized slugs
reuse the first category name. Category removal/renaming is future work; there
are no orphaning delete controls in this slice.

Editorial states are `draft`, `published`, and `archived`. Public visibility
requires both an existing page published snapshot and `published` editorial
state in that snapshot. Saving draft metadata does not alter the live snapshot.
To take a live article out of listings, publish its draft/archived state (or use
the existing Unpublish action). Publishing any state still runs the existing
role, version, media and compliance checks. Authenticated draft preview remains
available. New articles start as editorial drafts. Missing legacy metadata means
published for compatibility, never permission to bypass page publication.

Seed adaptation registers existing category labels: THE BASICS, READING THE
METHODS, A CURIOUS MIND; a missing label falls back to LEARN. Existing documents
are not rewritten. Legacy author display remains unchanged until explicitly
edited. New metadata participates in compliance scanning.

Public `/blog` and `/blog/category/{slug}`, `/blog/tag/{slug}` use the existing
blog page's layout and introduction. The existing `/blogs/learn` path remains
valid. Filter controls and author details belong to the routed listing surface;
the reusable latest-three block and existing article cards remain byte-stable.
Direct legacy renderer calls retain their baseline output. Category/tag slugs
use lowercase ASCII letters, digits and hyphens. Filter pages have self canonical
URLs; tags and empty results are noindex, as are preview sites and editor previews.
Unknown taxonomy URLs return 404. RSS/Atom feeds are outside this slice.

The site prefix is `/sites/{slug}`; custom-host routing uses the existing host
canonical/base closures. `/blog` selects the first published blog page by path
when a site has multiple blog pages; existing blog pages retain their own paths.
The routed archive includes all eligible articles, while embedded articles
blocks remain latest-three. Public filter navigation is derived only from
publicly eligible articles, so draft-only tags and category labels are not listed.
An existing empty category URL shows an empty state with noindex; an unknown or
draft-only tag returns 404. Tags filter independently from categories. Existing
article paths are unchanged. Draft previews remain authenticated and noindex.

Merchant controls extend the existing `register_site_routes` page form and use
its actor/CSRF closures, site lock, optimistic page version, revision recording
and successful post-redirect-GET. The form explains that publishing applies the
editorial state. Article metadata is validated on canonical-document assignment
and at the shared content save boundary. Author and taxonomy text are checked by
the existing compliance scanner on publication, including draft/archived states.
Blank legacy author fields retain the existing team display; explicit authors
and optional biographies render on article pages, with names on archive listings.

## Verification

Verified on 2026-10-08:

- Ruff clean. Compile checks for app, tests, migrations and scripts passed.
- Full isolated SQLite suite: **356 passed, 1 skipped**, exit code 0. The optional
  user-guide module skips because python-pptx is not installed. Ran the supplied
  environment recipe and pytest together in one PowerShell session: DB_URL empty,
  development, auto-create, unique temporary FASTSHOP_DATA_DIR, and empty XAI,
  Postmark and Stripe credentials. The existing Starlette/AnyIO BlockingPortal
  deprecation warning remains. Pytest's repository `-q` plus command `-q`
  suppresses the numerical summary; collection independently confirms 356 tests.
- All existing 50-render golden baselines pass without fixture edits. Additional
  coverage proves author edits do not alter embedded card bytes, archives remove
  ineligible cards and refill latest-three, and full listings include five posts.
- 34 new blog tests cover tenant/site isolation, invalid metadata, duplicate
  taxonomy, seed-style idempotent backfill, state transitions, draft/live snapshot
  separation, restore, role restrictions, banned claims in every state, metadata
  compliance, cascade deletion, public draft/archived 404s, authenticated preview,
  CSRF rejection, form PRG, author rendering, filters and SEO.
- Disposable SQLite empty upgrade to head and populated pre-taxonomy
  `20261008_0018` upgrade both passed, followed by `alembic check` with no new
  operations. The populated test restores the genuine prior table shape despite
  historical checkfirst migrations importing current models; existing documents
  survive unchanged. Both temporary database files are deleted after disposal.
- Browser command: `uv run python scripts/verify_site_browser.py --base
  http://127.0.0.1:51387 --blog --output output/playwright/phase1b-blog`.
  **57 checks, zero failures**: desktop, tablet and mobile across the 17 existing
  pages plus `/blog` and `/blog/category/the-basics`. Checks include overflow,
  image loading/alt, canonical URL, active category, filtered count and byline.
  Desktop/mobile filtered captures were visually inspected; a separate scoped
  visual review found no material issues. Browser checks cover seeded taxonomy;
  tag, empty-state, author-edit and editor mutation behavior is covered by tests.
- Mechanical UI detector reported no findings. `git diff --check HEAD` passed.
- No live PostgreSQL instance was used. No commit was created.

## Files changed

- `app/models.py`, `app/site_blog.py`, `app/site_blocks.py`, `app/content.py`,
  `app/compliance.py`, `app/site_seed.py`, `app/site_routes.py`, `app/site_ui.py`.
- `migrations/versions/20261008_0019_blog_depth.py`, `static/site-blog.css`.
- `tests/test_site_blog.py`, `scripts/verify_site_browser.py`.
- `output/playwright/phase1b-blog/` and this document.

Menus, media-library features, commerce and payments are unchanged.

## Follow-ups

RSS/Atom feed; article scheduling; comment moderation hook; multilingual taxonomy
labels and author biographies; pagination for large archives.

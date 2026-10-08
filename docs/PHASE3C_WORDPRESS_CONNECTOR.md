# Phase 3C — WordPress CMS connector

## Outcome

FastShop now has a bounded WordPress REST API v2 migration connector on the reviewed-plan framework from Phase 3A. It imports posts, pages, taxonomy, authors and referenced media into the target site's real CMS, then re-imports through site-scoped external mappings without duplicates. A separate export produces a downloadable WordPress WXR 1.2 XML bundle; it never writes to a remote WordPress site.

No schema migration was needed. `IntegrationPlan`, site-scoped `ExternalMapping`, `SitePage`, `BlogCategory` and `SiteMedia` already represent the complete slice.

## Existing CMS boundaries recorded before implementation

- Pages are tenant- and site-owned `SitePage` rows. Their draft and published snapshots are canonical version-1 block documents validated by `app/site_blocks.py`; the shared `content.create_page` and `content.save_page` paths remain authoritative for validation, revisions, media ownership, editorial publication and compliance.
- Blog depth is metadata inside article documents, not a second post table. WordPress posts therefore become `kind=article` pages under `/blogs/learn/{slug}` with `blog.state`, `category_slug`, tag slugs, `author_name` and `author_bio`. WordPress pages become `kind=content` pages under `/pages/{slug}`.
- `BlogCategory` is site-owned and uses the Phase 1B ASCII-folded, lowercase taxonomy slug rule. Tags remain bounded slug values inside article metadata. Author names remain article metadata because FastShop has no separate blog-author model.
- `SiteMedia` can own normalized upload bytes or register an existing static/remote URL. WordPress media uses `public_url`, a zero unknown byte size and no blob. The existing `/site-media/` ownership validator remains authoritative for local upload references.
- Menus are independent draft/published `SiteMenu` rows and are not changed by this connector.
- Head, pre-footer and footer snippets are consent-aware `SiteSnippet` snapshots. WordPress scripts, snippets and plugin-injected head/footer markup are deliberately not imported because the source consent and execution policy cannot be transferred safely.

## REST API and credentials

The operator configures each site independently:

```text
FASTSHOP_WORDPRESS_<UPPERCASE_SITE_ID>_ENABLED=true
FASTSHOP_WORDPRESS_<UPPERCASE_SITE_ID>_REST_BASE_URL=https://example.com/wp-json/wp/v2
FASTSHOP_WORDPRESS_<UPPERCASE_SITE_ID>_USERNAME=api-user                 # optional
FASTSHOP_WORDPRESS_<UPPERCASE_SITE_ID>_APPLICATION_PASSWORD=...          # optional
```

There is no global fallback. The exact REST base must be public HTTPS, end in `/wp-json/wp/v2`, and have no URL credentials, query or fragment. Localhost, obvious internal domains and non-global literal IP addresses are refused. Username and application password must be supplied together; otherwise the connector uses public read-only REST endpoints. Public access is not a trust signal: every field is still treated as untrusted text and validated through the block, URL, media, blog and content boundaries.

Application passwords are sent only as backend HTTP Basic authentication. They are never accepted from a merchant/shopper form, stored in an import plan, included in a report, rendered, or logged. HTTP redirects and environment proxy inheritance are disabled; provider bodies, request URLs and secrets are not returned in errors.

`FASTSHOP_WORDPRESS_FIXTURE_PATH` is a development-only offline seam and is ignored in production. It supplies no real secret and exercises the same dry-run/apply UI.

## Bounds

The numbers mirror the WooCommerce and Shopify slices:

- five pages per root resource;
- 50 objects per page;
- 750 normalized objects across categories, tags, users, media, posts and pages;
- 40 HTTP requests;
- 2 MB per response;
- 20-second request timeout;
- 300 KB of source HTML per post/page;
- the existing 60-block page limit, with a visible truncation warning;
- 100 report warnings/unmapped items retained;
- 500 posts/pages per WXR export, refusing rather than silently truncating above the cap.

The gateway reads `categories`, `tags`, `users`, `media`, `posts` and `pages`. Public connections use `context=view` and can therefore see only public provider content. An authenticated connection requests posts/pages with `context=edit` and the bounded `publish,draft,pending,private,future` status set so editorial drafts can migrate when the application-password user has the required WordPress capabilities. A provider-reported pagination count above five refuses the run. The dry run stores the exact normalized snapshot for 30 minutes; applying it never contacts WordPress again.

## HTML-to-block strategy

WordPress HTML is parsed with Python's non-executing `HTMLParser`. The source allowlist is `p`, `h1`–`h6`, `ul`, `ol`, `li`, `a`, `img`, `blockquote`, `strong`, `em` and `br`. The converter is intentionally lossy:

- paragraphs and blockquotes become `text.body` blocks;
- headings become `text.heading` blocks;
- ordered and unordered lists become readable numbered/bulleted text blocks;
- safe links are retained as plain `label (URL)` text; unsafe and `javascript:` URLs are removed with a warning;
- safe images become structured `split` blocks with `image` and `alt` fields;
- `strong`, `em` and line breaks preserve their readable text meaning without retaining raw markup;
- `script` and `style` elements and their contents are removed;
- WordPress shortcodes are removed while surrounding text is retained, with a warning;
- unsupported/complex tags such as `iframe` are flattened to non-executable text where possible and reported as warnings.

No source HTML becomes a FastShop raw HTML or embed block. Event handlers and arbitrary attributes are never copied. The resulting canonical document is validated again before it enters an immutable plan and again on apply. Publication goes through `content.save_page`, including compliance scanning and media ownership checks.

`publish` maps to a published FastShop page and published blog editorial state. Every other source status maps to an unpublished FastShop snapshot and draft blog state. The first category becomes the article category, up to 20 tag slugs are retained, and the referenced REST user supplies `author_name`. Missing referenced taxonomy, authors or featured media degrades to a warning and a deterministic fallback rather than bypassing validation.

Path and category-slug collisions with unrelated FastShop content receive a deterministic `-wordpress-{external_id}` suffix. Existing site media with the identical remote URL can be mapped without overwriting merchant-authored title/alt metadata. Only a site-scoped WordPress mapping may update an imported row.

## Media strategy

REST media entries, featured media and inline `<img>` URLs are deduplicated by HTTPS URL and registered as site-owned `SiteMedia` rows. Inline images without a REST media record use a stable SHA-256-derived external id. MIME type and alt/title metadata are retained within existing field limits.

Remote bytes are not downloaded. `data` stays null, `size=0` means unknown, and page content keeps the original HTTPS URL. `/site-media/` and `/static/` references are accepted only through the existing safe URL and ownership boundaries. An optional bounded download-and-normalize flow into blob storage is a follow-up because it needs explicit byte, image, retry and licensing policy.

## WXR export

The integration screen offers two explicit downloads:

- published snapshots only;
- published snapshots plus pages/articles that currently have no published snapshot, exported from their draft snapshot with `wp:status=draft`.

Published pages continue to export their public snapshot even if a newer private draft exists. Product and collection pages are excluded; the bundle covers CMS page kinds and articles. The response is private, no-store, `nosniff`, and attached as `fastshop-{site-slug}-wordpress-{scope}.xml`.

The WXR 1.2 structure contains:

- RSS channel title, site/blog URL, language, publication date and WXR namespace metadata;
- `wp:author`, `wp:category` and `wp:tag` term structures;
- one item per bounded page/article with `wp:post_type=page|post`, status, slug, dates, creator, GUID and closed comment/ping defaults;
- article category and `post_tag` item terms;
- `content:encoded` and `excerpt:encoded` as sanitized CDATA;
- a media `enclosure` when the document's lead image resolves to a registered `SiteMedia` row.

Block export reconstructs only the same safe HTML subset. Text is escaped, URLs pass the existing safe URL boundary, secure FastShop embeds degrade to a link, and no script/event-handler/raw snippet can enter CDATA. XML is generated with the standard DOM library and parsed in tests.

The round-trip eval uses structural equivalence rather than adding a second WXR import product path: it parses the generated WXR, verifies post/page types, publish/draft states, taxonomy, enclosure and sanitized content, then passes exported `content:encoded` back through the HTML-to-block parser. The production import remains the requested WordPress REST API path.

## Reviewed merchant workflow

`/admin/sites/{site_id}/integrations` registers WordPress beside Shopify and WooCommerce with the same readiness, dry-run, exact-plan confirmation and recent-plan states.

1. **Run import dry run** performs the bounded REST read and writes only an `IntegrationPlan`.
2. The review shows counts for categories, tags, authors, media, posts and pages, plus samples, trust/sanitization warnings and unmapped items.
3. **Apply this reviewed plan** is a separate CSRF-protected POST with explicit confirmation. It checks the creating user, tenant, site, platform, plan id/version, expiry and one-time atomic claim, then uses PRG.
4. WXR downloads are authenticated, site-scoped, read-only GETs because they do not mutate local or provider state. The content scope is explicit in the chosen action.

All owned queries include tenant and site scope. Re-import updates mapped categories, mapping-only tag/author references, media and pages without duplicates. No commerce, payment, menus, snippets or provider state is changed.

## Fixture and eval

`tests/fixtures/wordpress_site.json` contains two categories, two tags, one author, two REST media rows, one published and one draft post, one published page, a featured image, an inline image, paragraphs, a heading, lists, a blockquote, a safe link, a shortcode, a script and an iframe.

`python -m evals.wordpress_connector` runs dry-run → apply → re-import → WXR structural round trip against an in-memory database. Result: **10/10 checks passed**. It verifies preview purity, all resource reports, trust/script policy warnings, first application, blog metadata, HTML sanitation, remote media without a blob, update-only re-import, duplicate prevention, and well-formed structurally equivalent WXR. Reports are in `output/evals/wordpress_connector_results.{json,md}`.

## Verification

Verified on 2026-10-08:

- Ruff passed for the full repository. Compile checks passed for app, tests, evals, scripts and migrations.
- Full fresh isolated-SQLite suite: **427 tests collected and passed**, including the settled 50-render golden baselines. The only warning is the existing Starlette/AnyIO `BlockingPortal` deprecation.
- The three connector suites pass together: **27 tests passed**.
- Empty disposable SQLite upgraded through `20261008_0021`; `alembic check` reported no new upgrade operations. No `0022` migration was necessary.
- Offline WordPress eval: **10/10 checks passed**.
- Browser command: `uv run python scripts/verify_site_browser.py --base http://127.0.0.1:51389 --merchant-only --wordpress --output output/playwright/phase3c-wordpress`. Result: **4 checks, zero failures**, covering the fixture-backed WordPress card and WXR download, dry-run report, exact-plan apply, imported article in the builder, and the existing menu/media regression flows. Desktop/mobile captures are under `output/playwright/phase3c-wordpress/` and were visually inspected. The offline browser harness fulfills the fixture's fake remote image origin with an existing local WebP; production import still retains the original HTTPS URLs and downloads no bytes.
- The mechanical UI detector reported no findings.
- No live WordPress endpoint, PostgreSQL server or remote write was used. No commit was created.

## Files

- Framework and integration UI: `app/connectors.py`, `app/site_integration_routes.py`.
- Connector: `app/integrations/wordpress.py`.
- Tests, fixture and eval: `tests/test_wordpress_connector.py`, `tests/fixtures/wordpress_site.json`, `evals/wordpress_connector.py`, `output/evals/wordpress_connector_results.{json,md}`.
- Browser verification: `scripts/verify_site_browser.py`, `output/playwright/phase3c-wordpress/`.
- Documentation: this file.

## Follow-ups

- Optional bounded download of remote media into FastShop blob storage, with image normalization, byte/pixel caps, licensing review, retry policy and a source URL audit trail.
- WordPress comment import after FastShop has an owned moderation and privacy model.
- WPML/Polylang locale discovery and explicit mapping into FastShop's locale-keyed content and taxonomy labels.
- Scheduled re-sync with operator-owned cursors, reconciliation, conflict policy, retries and audit visibility. One-time reviewed plans must not become background sync jobs.
- Validation against an operator-controlled live WordPress sandbox before production migration acceptance.

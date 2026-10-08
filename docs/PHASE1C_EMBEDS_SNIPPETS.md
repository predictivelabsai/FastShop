# Phase 1c — Embeds and site snippets

## Site snippets

`SiteSnippet` is owned by both tenant and site and has a cascading site foreign
key. A unique `(site_id, placement)` constraint gives each site one global value
for `head`, `pre-footer`, and `foot`. Snippets are not localized content. Each row
has an atomic `draft_json` snapshot and nullable `published_json` snapshot with
`content`, `enabled`, and an optional note. The note is the only locale-aware
field; the merchant screen edits the site's default locale and preserves other
translations.

Migration `20261008_0020` follows `0019` and creates the model table with
`checkfirst`. It has no seed or backfill. Existing sites therefore render exactly
as before until a merchant explicitly saves and publishes a snippet.

The merchant screen is `/admin/sites/{site_id}/snippets`. It follows the menu
editor conventions: the shared platform shell, CSRF on every mutation,
post-redirect-GET notices, a site-version optimistic lock, separate draft and
publish actions, and tenant/site-scoped reads. Because snippet markup is trusted
executable configuration, every read and write requires an administrator or
merchant membership; editors cannot manage it.

## Public injection and consent

Snippets render only from an enabled published snapshot, only through the CMS
storefront renderer, and only when `site.status == "published"`. They never render
in authenticated draft preview, preview-status sites, the platform/admin shell,
or the separate cart, checkout, payment, subscription, and customer-account
surfaces. This preserves the existing no-analytics and `Referrer-Policy:
no-referrer` boundary on checkout/account responses.

Placement is literal: `head` is emitted into the generated document head,
`pre-footer` immediately precedes the storefront footer, and `foot` follows that
footer near the end of the storefront document.

The consent classifier is intentionally conservative:

- Markup without an executable `<script>` renders immediately. JSON and JSON-LD
  script elements are treated as inert structured data.
- Every other script is encoded into an inert template and is absent as
  executable page source until consent. Unknown scripts default to the analytics
  category. Known advertising/remarketing signatures (Meta/Facebook, DoubleClick,
  Google Ads, Pinterest, Snapchat, TikTok, LinkedIn Insight, and related markers)
  use the marketing category.
- `site-snippets.js` reads the same versioned `fastshop-consent:{site_id}` choice
  as GA4. It requires the matching boolean category and honors Global Privacy
  Control. A grant activates each template once. Withdrawing a category after a
  snippet has run reloads the page so arbitrary listeners cannot continue in the
  current document.

This is a classification policy, not a claim that arbitrary third-party code can
be unloaded perfectly. Treating every unknown executable script as optional
analytics prevents an unreviewed script from becoming essential by omission.
Script-free remote markup, including images, follows the explicit product rule
and is not consent-delayed; merchants remain accountable for what they publish.

Snippet URL attributes (`src`, `href`, `action`, and `poster`) must use HTTPS.
`mailto:` is allowed only on anchor `href`. Local, protocol-relative, HTTP,
credential-bearing, JavaScript, and malformed URLs are rejected. Structural
`html`, `head`, `body`, and `base` tags, inline event attributes, JavaScript URLs,
and iframe `srcdoc` are also rejected so code must pass through the visible script
classification path.

## Trust and compliance boundary

Snippets are merchant/operator configuration, not shopper content and not health
or product copy. They deliberately bypass banned-word and benefit-claim scanning.
This permits integration identifiers and vendor scripts that would be meaningless
to scan as editorial prose. The compensating controls are administrator/merchant
membership on every mutation, CSRF, optimistic locking, explicit publication,
strict public-surface gating, consent deferral, URL checks, and the structural
restrictions above. Shopper input is never accepted as snippet content.

## Embed block

The Phase 0 registry now includes `embed`. Its provider-neutral `url` field uses
the existing `safe_url` guard and then requires the final scheme to be HTTPS;
local paths and `mailto:` are not valid embed targets. It participates in the
canonical document, localization resolution, stable IDs, visibility, reorder,
draft/publish snapshots, revision history, and the existing classical block form.

The renderer emits a responsive 16:9 iframe with explicit `1280 × 720` intrinsic
dimensions, lazy loading, `referrerpolicy="no-referrer"`, and
`sandbox="allow-scripts"`. It does not grant `allow-same-origin`, forms, popups,
downloads, top navigation, presentation lock, or storage-access escape. Camera,
microphone, geolocation, and payment permissions are explicitly denied. This
allows isolated provider JavaScript but prevents the especially risky
`allow-scripts` + `allow-same-origin` combination. Because the block is
provider-neutral, there is no provider that justifies `allow-same-origin` in this
slice; a future provider profile must review and grant only the token it needs.

There is no raw HTML page block. Future work may add one only with a versioned
server-side allowlist: inert structural tags, an explicit per-tag attribute map,
HTTPS/mailto URL normalization, removal of scripts/styles/event handlers/forms/
iframes/object/embed/base/meta-refresh, bounded nesting and size, and golden tests
for parser differentials and malformed markup. Merchant snippets remain the
separate trusted-code escape hatch.

## Verification

Verified on 2026-10-08:

- Ruff passed for the full repository. Compile checks for application, tests,
  scripts, and migrations passed.
- The full isolated SQLite suite exited successfully with **378 tests collected**.
  It used the required single-PowerShell-session environment: empty `DB_URL`,
  development, auto-create enabled, a fresh temporary data directory, and empty
  XAI, Postmark, and Stripe credentials. The existing Starlette/AnyIO
  `BlockingPortal` deprecation warning remains.
- All existing 50 storefront golden renders pass without fixture changes. Tests
  cover HTTPS-only embeds, sandbox attributes, snippet validation and consent
  classification, draft/live separation, tenant/site isolation, membership,
  CSRF, stale versions, localized notes, source placement, claim-scan bypass,
  public-only injection, and cascade deletion.
- Empty SQLite upgrade through `20261008_0020` and `alembic check` passed. A
  populated database stamped at `20261008_0019` preserved its existing site while
  creating the snippet table; its post-upgrade `alembic check` also passed. Both
  disposable migration databases were removed.
- Browser command: `uv run python scripts/verify_site_browser.py --base
  http://127.0.0.1:51403 --merchant --embeds --output
  output/playwright/phase1c-embeds`. It reported **54 checks and zero failures**.
  The run verifies the head marker in published source, an executable snippet
  staying inert before analytics consent and activating after consent, strict
  iframe attributes, visible intercepted iframe content, and responsive overflow.
  Desktop/mobile embed and snippet-editor captures were visually inspected.
- The mechanical UI detector reported no findings. `git diff --check HEAD`
  passed. No live PostgreSQL instance was used and no commit was created.

## Follow-ups

- A sanitized allowlist HTML block, following the design sketch above.
- Provider-reviewed oEmbed discovery and cached metadata.
- Snippet start/end scheduling and publication history.
- Per-page or per-route snippet targeting, while preserving the checkout/account
  exclusion as a non-overridable platform policy.
- Explicit provider profiles for the rare embed that needs another sandbox token.

## Files changed

- Model/service/migration: `app/models.py`, `app/site_snippets.py`,
  `migrations/versions/20261008_0020_site_snippets.py`.
- Rendering and consent: `app/site_ui.py`, `static/site-snippets.js`,
  `static/site-theme.css`.
- Merchant UI and block editing: `app/site_snippet_routes.py`,
  `app/site_routes.py`, `app/site_builder_routes.py`, `app/site_blocks.py`.
- Tests/evidence: `tests/test_site_snippets.py`, `tests/test_site_blocks.py`,
  `scripts/verify_site_browser.py`, `output/playwright/phase1c-embeds/`.
- Documentation: this file.

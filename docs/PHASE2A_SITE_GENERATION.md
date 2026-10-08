# Phase 2A — Brief-to-site generation

## Scope and outcome

The `/admin/sites` screen now accepts one bounded merchant brief: business name,
business kind, audience and desired tone. Submission runs synchronously, shows a
loading state, creates a private `draft` site and redirects to the existing builder
workspace with the generated home page selected. No generation path publishes a
site, page, menu or settings snapshot.

When `XAI_API_KEY` is not configured, the same form uses the deterministic guided
preset planner. This is explicit in the success notice and builder mode label. The
fallback is not a reduced persistence path: it emits and validates the same plan
schema and uses the same apply service as provider output.

## Plan schema

`app/site_generation.py` owns plan version 1. The exact top-level fields are:

```json
{
  "version": 1,
  "default_locale": "en",
  "theme": {
    "accent": "#26543d",
    "background": "#fff9ee",
    "surface": "#eee7d8",
    "text": "#27382e",
    "font": "modern",
    "headings": "editorial",
    "spacing": "comfortable",
    "radius": "rounded",
    "width": "wide",
    "hero": "left"
  },
  "settings": {
    "tagline": "...",
    "announcement": "...",
    "footer": "..."
  },
  "pages": [
    {
      "path": "/",
      "title": {"en": "Business name"},
      "kind": "home",
      "description": {"en": "..."},
      "blocks": ["canonical Phase 0 block objects"],
      "product_slug": "optional-product-link"
    }
  ],
  "menus": {"header": ["validated menu items"], "footer": ["validated menu items"]},
  "products": [
    {
      "name": "...",
      "slug": "...",
      "subtitle": "...",
      "description": "...",
      "image_url": "",
      "category_slug": "collection",
      "category_name": "Collection",
      "variants": [{"name": "Standard", "price_minor": null}]
    }
  ],
  "imagery": [
    {
      "page_path": "/",
      "block_id": "home-hero",
      "direction": "Editorial scene direction for a future image provider",
      "placeholder_url": ""
    }
  ]
}
```

Plans contain 4–12 pages. Home, about, contact and blog shell paths are mandatory;
contact and blog pages require their canonical `contact` and `articles` blocks.
Phase 0 validation enforces the 60-block page limit, registered types, fields, IDs,
localized values and URL safety. All generated copy is a map keyed by the site's
default locale. Shop briefs additionally require `/shop`, one or more product seeds
and a one-to-one product-page mapping. Non-shop briefs cannot invent catalog items.

Generated themes must equal an existing preset. `validate_theme` then applies the
existing color and option constraints, including the font, heading, spacing, radius,
width and hero enums. Both menus pass `validate_items`; every generated target must
resolve to a generated page or visible block. External generated menu items are not
accepted.

## Prompt and bounded repair

The prompt includes the merchant brief as quoted untrusted JSON, the exact schema,
required paths, registered block types, theme presets, localization rules, limits,
catalog rules and safety constraints. It directs the provider not to invent people,
testimonials, certifications, outcomes, legal promises, product specifications,
health claims or prices. Prices stay `null` unless supplied by the merchant.

`request_site_plan` in `app/integrations/site_builder_llm.py` requests strict JSON
from the configured provider with a 45-second timeout and a 16,000-token response
cap. Prompts and raw responses are not logged or persisted. `generate_plan` validates
the response and can request at most two full-plan repairs, including only the prior
validation error and bounded prior JSON. After the second failed repair, generation
stops and the merchant receives a notice; no site has been written.

The form warns against passwords, API keys and customer information. Known provider
secret patterns are rejected before a prompt is sent or a brief is stored.

Validation runs before database writes and includes:

- Phase 0 document and block normalization.
- Existing theme and menu item constraints plus plan-level target resolution.
- Existing compliance scanning for every page and shared/product copy.
- Empty gradient placeholders or local `/static/...placeholder...` media only.
- One imagery direction for every hero, split and product block.
- Page, product, menu and total-response bounds.

## Guided preset path

The no-key planner deterministically selects one of the existing Original, Warm,
Minimal or Bold themes from the desired tone. It creates localized home, about,
contact and blog content. Shop-like kinds also receive a collection page, three
clearly draft catalog seeds and matching product pages; service and SaaS plans do
not receive products. Copy avoids testimonials, unverified facts and prices and
reminds the merchant to review catalog and policy details before launch.

The fallback goes through `validate_plan`, including compliance and media checks.
`force_guided=True` exists for offline tests and evals so they never depend on a
developer machine's environment.

## Applying a plan

`create_generated_site` first uses `content.create_site`, preserving the established
tenant, membership, channel and default-site creation boundary. `apply_plan` then:

1. Revalidates the complete plan and checks membership, draft status and expected
   `Site.version` through the existing optimistic lock.
2. Replaces only the just-created starter pages with `content.create_page` calls.
3. Creates catalog items through the shared `create_catalog_product` boundary in
   `app/site_catalog.py`. Products are real tenant catalog items, are never demo
   commerce records, and have no invented price listings.
4. Writes the validated theme and shared copy to draft settings only.
5. Writes header and footer through `site_menus.put_menu`, then leaves their
   published snapshots null.
6. Stores a brief-derived generation key, source and imagery directions in draft
   settings and increments `Site.version`.

Each newly generated site owns a new tenant, so catalog queries remain tenant-scoped.
Page and menu writes are tenant-and-site scoped. The transaction commits only after
the full plan applies. Reapplying the same generation key is a no-op even if the
original caller version is stale. A different plan is rejected unless the service
caller explicitly sets `retry=True`; retry is limited to a site already marked as
generated and still in draft status. Generation metadata records the created page
and product IDs so an explicit retry replaces only prior generated assets, preserving
later merchant-added pages and catalog items.

No database migration is needed. Idempotency metadata and imagery directions fit in
the existing draft `Site.settings_json`; page, menu, theme and catalog storage are
all existing schema.

## Image seam

This slice does not call image generation or stock services. Empty media URLs render
through the storefront's existing themed gradient/placeholder behavior. Approved
local placeholder URLs can also be used. Human-readable imagery directions remain
in `site_generation.imagery`, keyed by page path and block ID.

A future `site_images` service or media-generation provider can consume those
directions, create a site-owned `SiteMedia` row, and patch the target block through
the existing media-ownership and page-save boundaries. It must remain asynchronous,
tenant-scoped and separately reviewable; the plan validator must never accept a
provider URL as a shortcut around media ownership.

## Offline evals

`python -m evals.site_generation` runs without network access. Ten golden briefs
cover wellness, food, SaaS, local services, fashion, coffee, creative services,
consulting, home goods and fitness. Every brief runs twice:

- Forced guided-preset generation with no provider.
- A mocked provider fixture through the JSON-plan and validation path.

Each run scores required pages and valid blocks, menu resolution, compliance,
theme choices and successful application as a private draft. Raw plans and checks
are written to `output/evals/site_generation_results.json`; the summary is in
`output/evals/site_generation_results.md`.

Result on 2026-10-08: **20/20 runs passed**, each scoring **6/6**.

## Verification

Verified on 2026-10-08:

- Ruff passed for the full repository.
- Compile checks passed for app, tests, evals, migrations and scripts.
- Full isolated SQLite suite: **386 tests collected and passed**, with the existing
  Starlette/AnyIO `BlockingPortal` deprecation warning.
- The pre-existing 50 storefront golden renders remain covered by the full suite and
  passed without fixture changes.
- Disposable empty SQLite upgraded through `20261008_0020`; `alembic check` reported
  no new upgrade operations. No migration was added.
- Offline evals: **20/20 runs passed** across 10 briefs and both execution paths.
- Browser command: `uv run python scripts/verify_site_browser.py --base
  http://127.0.0.1:51435 --merchant-only --generation --output
  output/playwright/phase2a-generation`. It reported **3 checks, zero failures**,
  including brief submission, private-draft redirect, generated blocks, existing
  menu workflow and existing media workflow. Desktop and mobile brief/editor
  captures were visually inspected.
- The mechanical interface detector reported no findings.
- `git diff --check HEAD` passed.

## Follow-ups

- Stream plan progress and section creation instead of holding one synchronous
  request open.
- Regenerate or propose a diff for one block/section while preserving builder undo
  and accept/reject history.
- Add the asynchronous `site_images`/media-provider seam described above.
- Expand guided brief presets by vertical and locale after evaluating merchant
  demand; keep the output contract shared with provider generation.
- Add operator-facing explicit retry controls with a before/after summary. The
  service boundary already requires explicit retry intent.

## Files changed

- Generation/provider/catalog: `app/site_generation.py`,
  `app/integrations/site_builder_llm.py`, `app/site_catalog.py`.
- Merchant UI: `app/site_routes.py`, `app/site_builder_routes.py`,
  `static/site-editor.js`.
- Tests/evals: `tests/test_site_generation.py`, `evals/site_generation.py`,
  `output/evals/site_generation_results.json`,
  `output/evals/site_generation_results.md`.
- Browser evidence: `scripts/verify_site_browser.py`,
  `output/playwright/phase2a-generation/`.
- Documentation: this file.

No payment-acceptance behavior, demo-commerce isolation, existing published site
content or database schema changed. No commit was created.

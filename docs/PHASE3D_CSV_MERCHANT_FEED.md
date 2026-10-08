# Phase 3D — CSV catalog migration and Merchant Center feed

## Outcome

FastShop now has a provider-neutral catalog migration path and a Google Merchant Center export on the reviewed-plan framework from Phase 3A. A merchant uploads a bounded UTF-8 CSV, reviews counts and every row's create/update/error status, and explicitly applies the exact normalized snapshot once. Applying never rereads the upload.

The same integration card downloads a bounded RSS 2.0 Google product feed and a CSV validation report. Both exports read the target site's real tenant catalog, product pages, channel listings, and stock. They never read demo-commerce workspaces and never write to a remote service.

No schema migration was needed. The existing site-scoped `ExternalMapping` and one-time `IntegrationPlan` records from migration `20261008_0021` cover this slice.

## CSV contract

The file must be valid UTF-8; an optional UTF-8 BOM and CRLF or LF line endings are accepted. Header matching is case-insensitive, trims surrounding whitespace, and normalizes punctuation/spaces to underscores. One row represents one product with one variant.

Required fields:

| Canonical field | Accepted headers | Rules |
| --- | --- | --- |
| Product name | `name`, `product_name`, `title` | Non-empty, at most 220 characters. |
| Category | `category`, `category_name`, `collection` | Non-empty, at most 160 characters. |
| Price | Exactly one of `price`, `price_minor`, `price_<iso>`, `price_<iso>_minor` | Positive and at most 100,000,000 minor units after normalization. Multiple price columns are refused as ambiguous. |

Optional fields:

| Canonical field | Accepted headers | Rules |
| --- | --- | --- |
| SKU | `sku`, `variant_sku` | At most 100 characters. It is the site-scoped external row identity when present. |
| Description | `description`, `product_description`, `body` | Plain text, at most 20,000 characters. Empty is retained as empty and later reported as a Merchant Center failure. |
| Currency | `currency`, `currency_code` | Three-letter uppercase ISO code after normalization. If absent or blank, the target site's configured channel currency is the explicit default. |
| Stock | `stock`, `inventory`, `quantity` | If the column exists, every row requires a non-negative integer up to 10,000,000. If the column is absent, import does not change stock. Stock cannot be reduced below an allocated quantity. |
| Variant name | `variant_name`, `variant` | At most 180 characters. Without a name, option values become the label; without either, the documented label is `Default`. |
| Variant options | `variant_options`, `options` | Up to ten `Name=Value` pairs separated by `|`; stored in `ProductVariant.attributes_json`. |
| Images | `image_url`, `image`, `image_urls`, `images` | Up to ten HTTPS or owned local URLs, separated by `|` or line breaks. The first is the catalog image. |

Unsupported columns are ignored but named in the dry-run warnings so misspelled mappings are visible. Ambiguous aliases for the same canonical field are refused at the file level. A row with the wrong column count, an oversized cell/row, missing required data, malformed money, invalid URL, invalid stock, duplicate identity, or incompatible currency remains an explicit error row and is never included in the apply payload.

Rows with a SKU use `sku:<case-folded SKU>` as their site-scoped `ExternalMapping` identity. Rows without a SKU use `sha256:<digest>` of the complete normalized supported row. Consequently, a SKU-less row is idempotent while its normalized content is unchanged; changing that content intentionally produces a new identity. Duplicate SKUs or duplicate normalized hash identities make every conflicting row an error.

The import creates or updates tenant-owned categories, physical products, one variant per row, the target site's channel listing, and an isolated site-specific CSV stock warehouse when stock is supplied. Slug and SKU collisions with unrelated catalog records receive deterministic CSV suffixes. The import does not create or publish storefront product pages; the merchant retains the existing site publication workflow.

## Money normalization

`price` and `price_<iso>` are decimal major-unit strings. `price_minor` and `price_<iso>_minor` are integer minor-unit strings. Scientific notation, signs, grouping commas, decimal values in minor-unit columns, floats supplied through programmatic seams, negative values, zero, non-finite values, and oversized values are rejected.

Decimal values use `Decimal`, the source currency exponent, multiplication by `10 ** exponent`, and `ROUND_HALF_UP`. Known zero-decimal currencies such as JPY use exponent 0; BHD/KWD and the other known three-decimal currencies use exponent 3; other three-letter codes use exponent 2, matching the earlier connector policy.

Examples:

- USD `31.005` in `price` becomes `3101` minor units.
- JPY `149.5` in `price_jpy` becomes `150` minor units on a JPY site channel.
- KWD `1.2345` in `price_kwd` becomes `1235` minor units on a KWD site channel.
- USD `3101` in `price_minor` remains `3101` minor units.

FastShop does not perform currency conversion. A header-encoded or row currency that differs from the target site's channel currency is a row error. A header currency and `currency` cell that disagree are also an error.

## Reviewed-plan storage and apply

The upload route is multipart, CSRF-protected, and uses Post/Redirect/Get. It reads no more than the 2 MB file limit plus one refusal byte. Dry run decodes and parses the upload once, validates every row, and stores only the exact normalized valid rows plus the full bounded row report in `IntegrationPlan`. Raw CSV bytes are discarded after preview creation.

The plan expires after 30 minutes and is bound to the creating user, tenant, site, platform, schema version, and plan version. Apply requires the explicit review checkbox and atomically claims the pending version once before any catalog write. The transaction includes both the plan claim and all catalog/stock changes; failure rolls both back. Existing stock rows are locked in stable primary-key order. Apply uses only `payload_json` and never receives or rereads a file.

## Bounds

- 2,097,152 bytes per upload;
- 750 data rows;
- 32 columns;
- 20,000 characters per cell;
- 65,536 aggregate characters per row;
- 10 image URLs and 10 variant option pairs per row;
- 100,000,000 maximum normalized price in minor units;
- 10,000,000 maximum stock quantity;
- 750 candidate variants per Merchant Center export, refusing instead of truncating.

These limits mirror the 750-object and 2 MB magnitudes used by the Phase 3A–3C connectors while adding CSV-specific row, column, and cell controls.

## Google Merchant Center feed

The feed is generated with the standard XML DOM and emitted as UTF-8 RSS 2.0 with `xmlns:g="http://base.google.com/ns/1.0"`. Tests parse the result as XML rather than comparing text fragments.

Candidate products are site-scoped through this site's product pages and channel listings, then tenant-filtered again. A feed item is emitted only when all of the following real catalog state is valid:

- `g:id`: stable variant SKU, with the variant database ID as the stable internal fallback;
- `g:title`: product name;
- `g:description`: product description, or the stored subtitle when description is empty;
- `g:link`: the published site product-page path under `https://<hostname>`, or the same `https://<site-slug>.example.invalid` preview base used by the WordPress WXR export;
- `g:image_link`: an HTTPS image or an owned local image made absolute against the same base;
- `g:price`: exact fixed-point major units plus the three-letter listing currency, derived only from integer minor units;
- `g:condition`: `new`;
- `g:availability`: `in_stock` when tenant-owned stock minus allocations is positive, otherwise `out_of_stock`.

The product must also be published, have a published site-owned product page, and have an active variant with a positive site-channel price. No draft page, unrelated tenant product, demo-commerce record, missing-price product, or invalid item enters the feed.

The companion `fastshop-<site>-merchant-center-review.csv` lists every bounded candidate variant as `ready` or `error`, including product/variant IDs, SKU, title, and all missing required attributes. Invalid products are therefore reviewable and downloadable rather than silently dropped from the XML. Both downloads are authenticated, tenant/site scoped, private/no-store, `nosniff`, and read-only.

## Fixture and eval

`tests/fixtures/csv_catalog_edge_cases.csv` contains quoted commas, a SKU row, a SKU-less row, exact decimal prices, stock, variant options, image URLs, and categories. Tests adapt it to UTF-8 BOM plus CRLF and add bounded cases for negative/zero prices, oversized cells/files, duplicate SKUs, ambiguous price columns, decimal data in a minor-unit column, and bad UTF-8.

`python -m evals.csv_merchant_feed` runs preview → apply → second preview → second apply → published feed and validation-report generation against an in-memory database. Result: **10/10 checks passed**. Reports are stored in `output/evals/csv_merchant_feed_results.{json,md}`.

## Verification

Verified on 2026-10-08:

- Ruff passed for the full repository in the same PowerShell session as the required full test run.
- Full isolated SQLite suite: **435 tests collected and passed**, including the settled 50-render golden baselines. The only warning is the existing Starlette/AnyIO `BlockingPortal` deprecation.
- Compile checks passed for `app`, `tests`, `evals`, `scripts`, and `migrations`.
- All four connector suites passed together: **35 tests passed**.
- Fresh disposable SQLite upgraded through `20261008_0021`; `alembic check` reported no new upgrade operations. No `0022` migration was needed, and the disposable database was deleted.
- Offline CSV/feed eval: **10/10 checks passed**.
- Browser command: `uv run python scripts/verify_site_browser.py --base http://127.0.0.1:51390 --merchant-only --csv-merchant-feed --output output/playwright/phase3d-csv-merchant-feed`. Result: **3 checks, zero failures**, covering both Merchant Center downloads, CSV upload, per-row dry-run report, exact-plan apply, and the existing menu/media merchant regression flows. Desktop/mobile integration, dry-run, and completed-import captures were visually inspected under `output/playwright/phase3d-csv-merchant-feed/`.
- The mechanical UI detector reported no findings.
- No live provider, remote write, or PostgreSQL server was used.

## Files

- Connector registry and workflow: `app/connectors.py`, `app/integrations/csv_catalog.py`.
- Merchant UI: `app/site_integration_routes.py`.
- Tests, fixture, and eval: `tests/test_csv_merchant_feed.py`, `tests/fixtures/csv_catalog_edge_cases.csv`, `evals/csv_merchant_feed.py`, `output/evals/csv_merchant_feed_results.{json,md}`.
- Browser verification: `scripts/verify_site_browser.py`, `output/playwright/phase3d-csv-merchant-feed/`.
- Documentation: this file.

## Follow-ups

- Per-platform downloadable CSV templates and field-mapping presets.
- Scheduled Merchant Center feed regeneration and delivery after an authenticated publishing/reconciliation design exists.
- Google product-category taxonomy mapping and category taxonomy export.

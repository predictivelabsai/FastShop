# Upstream cleanup verification

Date: 25 October 2026. Branch: `remove-customer-store-material`.
Base: `d6bdadc`.

## Changes

- Removed the customer startup seed, editorial articles, branded media, local fonts,
  customer briefs, guide captures, production-guide script and generated guide binaries.
- Removed 774 historical evidence files, including screenshot sets whose folder names
  did not identify the customer. Reviewed and retained generic demonstrations.
- Shared payment adapters now require an explicitly configured primary-site slug
  before using global credentials. Per-site credentials and tenant isolation remain.
- Removed the fixed research-source fallback and customer font and colour defaults.
- Replaced customer examples with fictional Northstar Goods data. Its editorial
  fixture is explicitly provisioned by local scripts and tests, never by app startup.
- Retained claim/disclaimer checks, research references, reviews, subscription
  purchase options, renewals, payment boundaries and the generic demonstration shop.
- Regenerated 50 deterministic HTML hashes covering 17 editorial pages and eight
  starter pages in preview and published modes. Tests also assert mode differences,
  navigation fallback equivalence and unchanged rendering after media registration.
- Replaced the guide with generic source material and retained its structure, link
  and build checks. Optional PDF and PowerPoint regeneration is documented separately.

Compared with the base, without rename detection: **798 files deleted, 55 modified
and nine added**. Five representative evidence files and their ignore rules are
committed under `output/playwright/upstream-cleanup/`; other new captures stay local.

## Verification

Validation used per-process environment settings, disabled dotenv loading, blank
provider credentials and disposable databases. Provider interactions used local
test doubles. Dependencies were installed from the lockfile with development and
documentation extras. Tests retain their own isolated SQLite fixtures where designed.

| Check | Result |
| --- | --- |
| `ruff check .` | Passed |
| `compileall` for app, scripts, tests, migrations and entry point | Passed |
| Full pytest, disposable SQLite | 1,036 passed, zero failed, one warning; 720.95 seconds |
| Full pytest, disposable PostgreSQL 16 | 1,036 passed, zero failed, one warning; 816.55 seconds |
| SQLite `alembic upgrade head` and `alembic check` | Passed; no new upgrade operations |
| PostgreSQL `alembic upgrade head` and `alembic check` | Passed, including after the full suite; no new upgrade operations |
| Fresh app on port 5026 | `/`, `/login`, `/healthz` returned 200 |
| Startup provisioning | Only the generic demo tenant; zero editorial sites |
| `verify_admin_v2_all_browser.py` | Passed: 19 journey entries, 12 downloads, zero browser or server errors |
| Neutral storefront browser acceptance | 51 checks: 17 routes at desktop, tablet and mobile sizes; zero failures |
| `git diff --check` | Passed |
| Migration comparison with base | No changes to any migration file |

The browser results, smoke results, storefront results and desktop/mobile sample
screenshots are in [the evidence directory](../output/playwright/upstream-cleanup/).
The admin runner produced 255 captures locally. Browser servers stopped cleanly.
The disposable PostgreSQL container and its anonymous volume were removed.

## Remaining reference

The requested customer-name and product-term scans, including filenames and binary
artefacts, find one source occurrence: line 26 of
`migrations/versions/20261009_0027_plans_quotas.py`. Its historical tenant-plan update
contains the former customer slug. The released migration's operations and revision
identifier are unchanged because existing databases already applied it. No migration
to remove customer data was added. Environment and secret files were excluded from
the final artefact scan.

## Open questions

None. No push, merge, deployment, remote database or Coolify action was performed.

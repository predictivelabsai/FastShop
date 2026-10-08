# Phase 4a: reviewed publication, domains, and sandbox commerce

Phase 4a provides one merchant-facing path from a reviewed site to a published storefront, a custom-domain binding, and sandbox checkout. The path is deliberately offline and sandbox-only. It does not accept, store, validate, or activate live provider credentials.

## Checklist catalog

`app/site_golive.py` derives every result from current tenant-owned records. Checklist results are not stored as editable booleans and no check makes an external call.

| Check | Publication | Sandbox commerce | Derived source and rule |
| --- | --- | --- | --- |
| Published home page | Required | Inherited | `/` must have a real published snapshot; a draft-only home fails. |
| Hostname validity and uniqueness | Required when present | Inherited | The normalized `Site.hostname` must be syntactically valid and globally unused by another site. No hostname is acceptable for publication because the bounded preview path remains available. |
| Draft compliance | Required | Inherited | Every draft page is scanned with the existing compliance scanner. Banned claims and disease references fail; advisory hype wording is reported without failing. Approved benefit statements are also scanned. |
| Merchant review | Required | Inherited | Existing sample-review metadata must have no pending merchant fields. |
| Published menus | Required | Inherited | Header and footer need published snapshots, valid targets, and published pages behind every internal target. |
| Snippet consent policy | Required when used | Inherited | Enabled analytics or marketing snippets require the built-in consent controls plus a reviewed, published privacy policy. Non-executable snippets do not add this requirement. |
| Published site status | — | Required | `Site.status` must already be `published`. |
| Custom domain bound | — | Required | A normalized, unique custom hostname is required before checkout is enabled. |
| Commerce policies | — | Required | Reviewed, non-placeholder privacy, terms, and returns pages must be published. |
| Published catalog | — | Required | At least one published product must have a published product page owned by the site. |
| Channel prices | — | Required | Every active option of the published site products must have a listing for the site's channel. Catalog and price queries do not run for publication-only reviews. |
| Commerce configuration | — | Required | Fulfilment origin, shipping fee, allowed destinations, reviewed tax registration, and tax codes for published products must be present. |

The merchant surface shows each check as pass or fix, with a human explanation and a link to the relevant editor.

## Transition model

The existing page workflow still moves a draft site into bounded preview when its first page is published. Phase 4a adds reviewed site-level transitions:

1. `draft` or `preview` → `published`: requires all publication checks, a merchant-entered review reason, confirmation, CSRF, and the submitted `Site.version`.
2. `published` → custom-domain bound: validates and normalizes the hostname, enforces uniqueness, and uses the submitted `Site.version`.
3. `published` + custom domain → sandbox commerce: recomputes publication and commerce checks, then changes `SiteCommerceSettings.mode` from `disabled` to `sandbox` using both site and commerce-settings versions.
4. Sandbox commerce → disabled: always allowed to make the storefront read-only again. It remains CSRF and version protected.

Failed readiness decisions do not mutate publication or commerce state, but they are recorded for operator review. Merchants cannot force publication. The configured platform operator account (`FASTSHOP_ADMIN_EMAIL` with an admin membership) can override failed publication checks only when `FASTSHOP_ENV` is not `production`; the failed checks, reason, actor, and override are all recorded. Production rejects the same override request. Commerce enablement has no override.

Every POST follows Post/Redirect/Get. Editors retain draft access but only merchant and admin memberships can use publication, domain, and commerce transitions.

## Domain binding and TLS

The domain form accepts a DNS hostname only: no scheme, credentials, path, port, localhost, platform hostname, or IP address. Unicode hostnames are normalized through IDNA and the database's existing unique constraint remains the final concurrency guard. No DNS API is called.

TLS terminates at the platform. Phase 4a records the binding and documents the operational boundary; automated certificate issuance and DNS ownership verification are follow-up work.

The bounded preview remains `/sites/{slug}/`. `SiteHostMiddleware` is the single custom-host routing gate:

- storefront content on a bound host requires `Site.status == "published"`;
- cart and checkout additionally require sandbox commerce;
- account and unsubscribe recovery retain their existing closed-store exception;
- `/sites/{slug}/` remains the explicit preview route for merchant review.

## Commerce gate

The commerce settings page configures origin, shipping, destinations, tax review, and product classifications, but it can no longer change checkout availability. The go-live action is the only merchant route that changes `SiteCommerceSettings.mode`.

Only `disabled` and `sandbox` are supported. Existing checkout services and provider boundaries continue to reject non-sandbox modes and live keys. Disabling sandbox commerce immediately makes the storefront read-only without removing catalog or configuration state.

## Audit trail

Go-live decisions reuse `SiteChangeSet` with `source="golive"`; no migration or new table is required. Each event records:

- action and decision;
- actor and timestamp;
- review reason;
- site status, hostname, and commerce mode before and after;
- the complete derived check result and explanation set;
- whether a development override was used.

Go-live events are excluded from draft undo history because they are auditable transitions, not reversible content snapshots. The configured platform operator sees the dedicated audit trail; tenant admins and merchant members see the controls and current derived state without gaining operator override authority.

## Offline evals

`scripts/eval_site_golive.py` runs without fixtures, provider keys, or network access and writes `output/evals/phase4a-golive.json`. It verifies:

- a draft-only home is rejected;
- catalog checks are absent until commerce is requested;
- published menu targets can satisfy publication readiness;
- a noncompliant draft is rejected even when its public snapshot is clean;
- a commerce request adds catalog, price, and domain gates.

Result: 5/5 cases passed; external calls: 0.

## Verification

- `ruff check .`: passed.
- Full isolated SQLite pytest suite: passed.
- Python compile check: passed.
- Existing 50-render storefront golden baselines: passed as part of the full suite.
- `scripts/verify_site_browser.py --merchant-only --golive`: passed with no browser failures.
- Desktop and mobile evidence: `output/playwright/phase4a-golive/`.
- No Alembic migration was added; schema upgrade/check validation is therefore unchanged for this slice.
- `git diff --check HEAD`: passed.

## Follow-ups

- Phase 4b: operator-approved live credentials and the separate live-provider acceptance gate.
- Platform TLS certificate provisioning and renewal automation.
- DNS TXT ownership verification before production domain activation.

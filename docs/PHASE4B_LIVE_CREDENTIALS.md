# Phase 4b: operator-approved live Stripe credentials

Phase 4b adds the separate operator gate between sandbox commerce and real payment acceptance. A site cannot enter live mode from environment configuration, a merchant form, a shopper request, or application boot. The configured platform operator must store, verify, and explicitly accept one site's credentials through the protected ceremony.

## Storage and encryption design

`SiteStripeLiveCredential` is a tenant- and site-scoped row with one current credential per site. Alembic revision `20261008_0022` creates the table and adds the acceptance state to `SiteCommerceSettings`:

- `live_accepted_at`
- `live_accepted_by`
- `live_credential_id`

The Stripe secret key and webhook signing secret are stored only as AES-256-GCM ciphertext. `FASTSHOP_STORE_KEY_ENCRYPTION_KEY` supplies at least 32 bytes of operator-managed key material; FastShop derives the AES key with SHA-256 and a versioned application domain separator. Every value uses a random 96-bit nonce and authenticated context containing the site ID, credential ID, and field name, preventing ciphertext from being moved between sites, credential rows, or fields.

The environment key is read only when a credential is stored or used. It is not required for development or production sites that have no live credential. A missing, short, incorrect, or unavailable key fails live commerce closed. Boot never validates a Stripe credential and no environment variable can set `mode="live"`.

Plaintext secrets are never copied to `Site.settings_json`, `published_settings_json`, provider commands, audit JSON, API payloads, or HTML. The operator page renders only `sk_live_****1234`, the credential ID, creator identity, and creation time. Password inputs are always empty and use `autocomplete="new-password"`.

## Acceptance ceremony

The operator-only surface is `/admin/platform/live-credentials`, with a per-site ceremony at `/admin/platform/sites/{site_id}/live-credentials`.

1. The configured platform operator, who must also hold the site's admin membership, submits a Stripe `sk_live_…` secret key and `whsec_…` signing secret through a CSRF-protected form. FastShop encrypts them before persistence, clears prior verification and acceptance, and records only the credential ID in the audit event.
2. A separate operator action decrypts the current credential and performs a read-only `GET /v1/account` against Stripe. The HTTP client has a 10-second attempt timeout and at most one transport replay. Provider errors are sanitized. Validation never runs at boot.
3. Acceptance recomputes the Phase 4a publication and commerce readiness report. The site must be published, have a bound custom domain, have sandbox commerce enabled, have a current verified credential, and pass every derived check.
4. The operator must provide a 10–500 character reason and check the explicit confirmation statement. The submitted `Site.version`, `SiteCommerceSettings.version`, and credential ID must still match.
5. The transition records the operator identity and a `SiteChangeSet` with `source="live-acceptance"`, then changes the mode from `sandbox` to `live` and binds acceptance to the verified credential ID.

Disabling live payments is also operator-only, version-locked, reasoned, CSRF-protected, and audited. It reverts the site to sandbox, clears the acceptance fields, and clears credential verification so a later live activation must repeat verification and acceptance.

## Guard inventory

| Guard | Enforcement | Test coverage |
| --- | --- | --- |
| Operator-only credential access | `app/live_credentials.py:require_operator`; all `/admin/platform/*/live-credentials` handlers call it before credential lookup | `test_operator_storage_is_encrypted_masked_tenant_scoped_and_secret_free`; `test_operator_form_is_csrf_protected_never_echoes_secrets_and_merchant_page_is_badge_only` |
| CSRF and PRG | Every credential, verification, acceptance, and disable POST in `app/site_live_credential_routes.py` checks CSRF and redirects with a bounded notice | Operator form route test |
| No plaintext persistence or rendering | `app/live_credentials.py` encrypts before ORM assignment; audit helpers accept credential IDs only; operator forms never receive stored values | Encryption, storage/audit, route HTML, and browser source assertions |
| Encryption key fails closed | `app/live_credentials.py:_key`, `decrypt_secret`, and `effective_mode` | `test_encryption_round_trip_is_bound_to_context_and_missing_key_fails_closed`; `test_live_mode_fails_closed_without_encryption_key_and_on_draft` |
| Explicit accepted-live state | `effective_mode` requires `mode="live"`, published status, hostname, accepted-at/by, matching verified credential ID, and successful decryption | Acceptance and draft/fail-closed tests |
| Publication, domain, and sandbox prerequisites | `live_credentials.accept` reuses `site_golive.assess(..., commerce_requested=True)` and requires the current mode to be sandbox | Acceptance gate test plus existing Phase 4a readiness suite |
| Version locking | Acceptance and disable compare site and commerce-settings versions; acceptance also compares the credential ID | Stale acceptance test |
| Merchant cannot activate or disable live | No merchant route accepts credentials or an acceptance command; Phase 4a sandbox transition rejects `mode="live"`; merchant integration/go-live pages expose only the approval badge | Storage refusal, merchant badge route test, acceptance/disable test |
| Provider key selection | `StripeGateway(site, db=...)` uses environment test keys in sandbox and the matching encrypted row only in accepted-live mode | Live gateway stored-key test and existing provider tests |
| Quote and storefront checkout | `commerce.payment_mode`, `store_checkout_routes.resolve`, `SiteHostMiddleware`, and `site_ui` all use the shared effective-mode guard | Draft/missing-key test, existing commerce/route/golden-render suites |
| Durable payment handoff | `checkout_payments.py` preserves provider command JSON shape while matching live/test session prefixes and `livemode` to the guarded mode | Existing checkout payment suite plus live gateway test |
| Reconciliation and subscriptions | `checkout_services.py`, subscription setup, recovery, renewal, notification, and mail paths require the shared mode and compare provider `livemode` | Full checkout and subscription suites |
| Webhook mode and signature | `commerce_webhooks.signature_secret` selects the sandbox environment secret only in sandbox and the encrypted stored secret only in accepted-live mode; `verify_webhook` uses `hmac.compare_digest` and rejects unsigned, expired, or mismatched bodies | `test_webhook_secret_dispatches_by_site_mode_and_rejects_mismatch`; existing signed webhook tests |
| Disable/revert behavior | `live_credentials.disable` returns to sandbox and clears acceptance and verification; the merchant sandbox route refuses to alter accepted live mode | Acceptance/disable test and offline eval |

## Webhook dispatch

The site-scoped endpoint remains `/api/v1/commerce/{site_id}/stripe-webhook`. Before JSON parsing or reconciliation, it resolves the tenant-owned site's effective mode and signing secret:

- sandbox: per-site sandbox environment secret, retaining the primary-site compatibility fallback;
- accepted live: the matching encrypted live webhook secret;
- disabled, incomplete live state, missing encryption key, unsigned body, wrong signature, stale signature, or mismatched event `livemode`: rejected.

Both modes use the same constant-time HMAC comparison and raw-body size/timestamp bounds. A live event can never be verified with the sandbox secret, and a sandbox event can never be reconciled through an accepted-live site.

## Offline evals

`python -m scripts.eval_live_credentials` writes `output/evals/phase4b-live-credentials.json` and makes no network calls. Result: 7/7 passed, external calls: 0.

Covered cases: merchant storage refusal, ciphertext/audit secrecy, preview-site refusal, accepted credential resolution, live webhook dispatch, one read-only mocked Stripe validation, and disable/reverification behavior.

## Verification

- `ruff check .`: passed in the same isolated PowerShell session as the full suite.
- Full isolated SQLite `python -m pytest -q`: passed with 100% dots and exit code 0; existing 50-render golden baselines passed.
- `python -m compileall -q app scripts`: passed.
- Alembic upgrade through `20261008_0022` on disposable SQLite: passed.
- `alembic check`: no new upgrade operations detected.
- `scripts/verify_site_browser.py --merchant-only --golive --live-credentials`: 4 checks passed, 0 failures; submitted secret substrings were absent from every checked HTML source.
- Desktop and mobile evidence: `output/playwright/phase4b-live-credentials/`.
- Impeccable UI detector: no findings.
- `git diff --check HEAD`: passed.

## Follow-ups

- Key rotation with overlapping credential versions and in-flight payment reconciliation.
- Stripe account capability and payout-readiness checks beyond authenticated live-account retrieval.
- Tax engine integration and production tax-registration capability validation.

# H24YOU Liquid demo deployment

## Merchant Admin v2 release

25 October 2026. Prepared on `merchant-admin-v2`; nothing was pushed or deployed.
From production revision `20261011_0029`, the normal `alembic upgrade head`
applies catalogue `20261025_0030`, orders/customers
`20261025_0031_orders_customers`, growth/settings
`20261025_0032_growth_settings`, merge `20261025_0033_merge_admin_v2` and
functional gaps `20261025_0034_admin_gaps`, the single head.

No new required runtime environment variables. Preserve `DB_URL`,
`DB_SCHEMA=fast_shop`, existing secrets and the existing optional
`FASTSHOP_H24YOU_LIQUID_HOSTNAME` demo-cutover setting. `FASTSHOP_BROWSER_DB_URL`
and `FASTSHOP_TEST_DB_URL` are optional local validation settings for explicitly
disposable PostgreSQL databases, never production deployment settings.

Back up PostgreSQL and persistent media/storage before upgrading; verify the
backup can be restored and retain the deployed application commit/configuration.
Apply migrations with one release instance before admitting merchant writes.
Populated PostgreSQL 16 (`fast_shop`) and SQLite downgrade to `0029`, upgrade
back to head and schema checks were verified. Downgrade removes v2 feature data;
it is not a lossless application rollback. Prefer a reviewed backup/restore plan
if rollback is needed, with an explicit decision about post-backup writes. Do
not simply run older code against an unknown schema revision.

Liquid boot now preserves higher target roles and ensures existing
`helen@h24you.com` is an admin, creating only a missing membership. No user or
password is created or reset. Repeated boot is idempotent. After deployment,
check Helen's Liquid Settings/Staff access, both merchant logins, tenant
isolation, `/healthz` and `/readyz`, and confirm migration head `0034` in logs.
The detailed commands, PostgreSQL evidence and remaining provider/concurrency
limits are in `docs/admin-v2/04-qa-report.md`, under Release candidate.

The sections below retain the original Liquid deployment preparation; the
release section above supersedes its migration endpoint and membership policy.

Prepared 21 October 2026 for `h24you-lovable`, starting at `add8451`.
Nothing was pushed or deployed. The reference repository was read only.
This remains a sandbox demonstration. Content, policies, photography and
commerce fixtures still have the limitations in `H24YOU_LIQUID_THEME.md`.

## Coolify steps

1. Back up PostgreSQL and persistent storage. Keep the existing Coolify
   configuration available for rollback.
2. After reviewing and publishing the prepared branch to the configured
   `predictivelabsai/fastshop-h24you-demo` repository, select **h24you-lovable**
   in the existing Coolify app. Verify the resolved commit against this local
   branch. Publishing the branch is an operator step; it was not done here.
3. Keep the Dockerfile build pack, `/Dockerfile`, port `5025`, persistent
   storage and reverse-proxy domain `https://h24you.fastsme.com`.
4. Apply these runtime settings and rebuild/redeploy in Coolify:

   | Variable | Demo value or action |
   | --- | --- |
   | `FASTSHOP_H24YOU_LIQUID_HOSTNAME` | `h24you.fastsme.com` |
   | `FASTSHOP_ENV` | `production` |
   | `FASTSHOP_AUTO_CREATE_SCHEMA` | `0` |
   | `FASTSHOP_SIGNUP_OPEN` | `0`, explicitly; the new default is open |
   | `FASTSHOP_ALLOW_PASSWORD_LOGIN` | `1`, to show the existing-users password form |
   | `FASTSHOP_ADMIN_EMAIL` | `joosep.laats@gmail.com`, the existing operator |
   | `FASTSHOP_PUBLIC_URL` | `https://shop.fastsme.com`, or the actual separate platform host; **never the demo hostname** |
   | `FASTSHOP_ENABLE_SCHEDULER` | `0` |
   | `FASTSHOP_IMAGE_PROVIDER` | `placeholders`, the default |
   | `DB_URL`, `DB_SCHEMA` | Preserve the existing PostgreSQL connection and `fast_shop` schema |
   | `FASTSHOP_SESSION_SECRET`, `FASTSHOP_ENCRYPTION_KEY` | Preserve existing values |
   | `FASTSHOP_ADMIN_PASSWORD_HASH` | Preserve if present; existing per-user login needs no new global hash |

   Retain existing Google authentication settings if used. No production
   password is reset or copied. Merchant login is `/login`; `/account` is for
   shoppers. Platform billing and live-store keys are unnecessary for this demo.
   No plan upgrade is needed to boot, render or edit existing content. Creation
   of further sites/products or later publication can encounter Free plan
   quotas. Bootstrap does not change plans or create billing subscriptions.
   Existing migration 0027 assigns Pro to the pre-existing `h24you` and
   `fastshop-demo` fixtures. Because both named users already manage `h24you`,
   their effective account plan is Pro after this legacy upgrade. No paid
   Stripe subscription is created by that fixture assignment.
5. Keep the normal start command:
   `alembic upgrade head && python web_app.py`. No manual seed or terminal
   command is needed for forward deployment. Remove any rollback override.

`.env.coolify.sample` contains non-secret examples. Manage actual credentials
only in Coolify and retain the existing session/encryption values.
The Dockerfile already installs `curl` and `wget`. Its image healthcheck uses
`curl --fail http://127.0.0.1:5025/healthz`; a Coolify wget probe is compatible.
`/healthz` is shared and unrewritten; `/readyz` checks database readiness too.
The existing unprivileged container user remains compatible.

## What boot does

Alembic upgrades the existing `20261007_0016` chain through `20261011_0029`.
No new revision is added here. Migration 0022 now names its added foreign keys,
fixing a real old-schema SQLite batch failure. The page JSON adapter retains
unsupported persisted legacy sections on read, while all writes remain strictly
validated. Kinetic documents/settings are not converted or rewritten.

Normal Liquid seeding creates missing fixture records and pages. With
`FASTSHOP_H24YOU_LIQUID_HOSTNAME` non-empty, the bootstrap transaction:

- Validates and normalises the DNS hostname. Schemes, ports, paths, IPs and the
  configured platform hostname are rejected before cutover.
- Locks matching sites in ID order, clears the hostname from conflicting sites,
  sets those sites to `draft`, and flushes before assigning the unique hostname
  to Liquid. Nothing is deleted.
- Sets Liquid to `published`, which newer hostname middleware requires. This
  is an explicit sandbox demo exception, not a go-live approval. No readiness
  approval, reviewed policies or live acceptance is fabricated. Domain binding
  uses `Site.hostname`; there is no additional domain table. Normal merchant
  go-live still uses `site_golive.py` and its audited readiness transitions.
- Ensures `SiteCommerceSettings` exists, sets sandbox mode and opts into
  customer accounts. Existing sandbox origin/shipping/stock remain fixtures.
- Copies every Kinetic tenant membership to Liquid, including editors. Existing
  target roles can only be upgraded: admin > merchant > editor. Each Liquid seed
  also ensures admin membership for existing users in `H24YOU_LIQUID_ADMIN_EMAILS`
  (`helen@h24you.com`), including when hostname cutover is disabled. Missing users
  are silently skipped. No users are created and no passwords or source
  memberships are changed.
- Marks Liquid as a sandbox demo. Host responses receive
  `X-Robots-Tag: noindex, nofollow`; the sandbox footer remains visible.

Repeated boot does not duplicate memberships/pages or increment cutover
versions when unchanged. Keep the variable set to reconcile binding/access
at boot. Empty/unset performs no hostname, status, commerce or access cutover.
Normal insert-only Liquid seeding still runs. Removing the variable does not
undo a committed cutover. This operator demo switch deliberately restores
sandbox mode and shopper accounts if altered; do not use it for a live store.

The ported `ensure_admin.py` diagnostic/repair helper is available separately,
with `DRY_RUN=1` for no writes. It grants roles only on `h24you`,
`h24you-kinetic` and `h24you-liquid`, preventing the old helper from granting
Helen Northstar access. It is not run at boot. Automatic boot copies Kinetic
memberships without downgrades and ensures the named Liquid administrators
in the H24YOU seed module.

Admin catalogue, orders, stock, promotions, customers, channels, outbox
status/sync and merchant copilot facts now respect membership tenants. Only
the configured operator without memberships retains global legacy access;
other membership-free users receive an empty scope. The redesigned site list
already filtered memberships. Unpriced/inactive-option admin products show
`Price to be confirmed`; a genuine zero price remains zero.

## Verify after deployment

- Logs should complete Alembic through `20261011_0029` and start the web app.
  Health and readiness probes should pass.
- Visit `/`, `/shop`, `/products/h2-4-you-one`, `/products/hydrogen-tablets`,
  `/products/taste-the-range`, `/about`, `/faq`, `/contact`, `/learn`,
  `/why-hydrogen`, `/why-hydrogen/science` and `/science` on
  `https://h24you.fastsme.com`. Confirm Liquid styling and the sandbox notice.
- `/cart` and `/account` should load. Empty-cart `/checkout` redirects to cart;
  adding an item exposes sandbox checkout. Do not make a real payment.
  Shopper account authentication is separate from merchant `/login`.
  Kinetic carts/customer identities are site-scoped and do not transfer to
  Liquid; start new Liquid shopper flows.
- Sign in as both existing users at `https://h24you.fastsme.com/login`.
  Open `/admin/sites` and Liquid's editor/builder. Helen should see her H24YOU
  tenants, including retained Kinetic, without Northstar data. The operator
  may see Northstar because its seed already grants that operator membership.
- `/signup` and `/marketing/` on this host return 404. The platform landing
  page must not replace Liquid home. Check the noindex response header.
- Restart and repeat hostname/login/cart checks. Kinetic site overview/list
  entries must remain readable. This branch retains its unsupported pages
  for rollback and does not provide a Kinetic renderer/editor.

## Rollback to main at bfc5099

A branch-only rollback is insufficient. Old Alembic fails with
`Can't locate revision identified by '20261011_0029'`. Also, the hostname
remains on Liquid. Direct old-app boot on the upgraded database returns 200
at `/` but does not render Kinetic until the binding is restored.

To retain the upgraded schema:

1. Use one instance, with no competing forward/rollback boot. Select `main`
   pinned to `bfc50994a0ab72817f0b1593a530b73dcf7eee51`. Clear the Liquid
   hostname variable. Preserve database, storage and existing secrets.
2. Override the Coolify Docker start command with this single line. It uses
   old models, releases the unique hostname, restores Kinetic atomically,
   then boots the old app without running old Alembic:

   ```sh
   python -c "from sqlalchemy import select, update; from app.db import SessionLocal; from app.models import Site; db=SessionLocal(); target=db.scalar(select(Site).where(Site.slug=='h24you-kinetic')); assert target is not None, 'Kinetic site missing'; db.execute(update(Site).where(Site.hostname=='h24you.fastsme.com').values(hostname=None)); db.flush(); target.hostname='h24you.fastsme.com'; target.status='preview'; db.commit(); db.close()" && python web_app.py
   ```

   This is a Linux container command, not PowerShell. It requires no terminal
   session and is repeatable. New tables/columns and Liquid data remain.
   Do not downgrade or stamp production merely to satisfy old Alembic.
3. Verify Kinetic home/shop, cart/account, both merchant logins and health.
   Keep signup disabled. Restore the normal Docker start command and Liquid
   hostname variable on a later forward deployment.

Alternatively restore the pre-cutover database/storage backup with
`main@bfc5099` and its original start command. That loses post-backup writes
and requires an operator recovery decision. No destructive downgrade was
performed in the rehearsal.

## New environment reads since deploy/main

Comparison covers `app/config.py`, Python `os.getenv`, `os.environ.get`,
`os.environ[...]` and `_bool` reads across application/operator scripts.
Dynamic connector prefixes use the uppercase 32-character Site ID, not slug.

| New read | Requirement and demo behaviour |
| --- | --- |
| `FASTSHOP_H24YOU_LIQUID_HOSTNAME` | Required for automatic demo cutover; empty disables it |
| `FASTSHOP_SIGNUP_OPEN` | Set `0`; otherwise its new default opens platform signup |
| `FASTSHOP_IMAGE_PROVIDER` | Optional; default `placeholders`; no generation key needed |
| `FASTSHOP_STORE_KEY_ENCRYPTION_KEY` | Optional for sandbox; needed only for encrypted live-store credentials |
| `FASTSHOP_BILLING_STRIPE_SECRET_KEY` | Optional; unconfigured; separate from store payments |
| `FASTSHOP_BILLING_STRIPE_WEBHOOK_SECRET` | Optional; unconfigured |
| `FASTSHOP_BILLING_PRICE_BASIC` | Optional; unconfigured |
| `FASTSHOP_BILLING_PRICE_PRO` | Optional; unconfigured |
| `FASTSHOP_BILLING_LIVE_ACCEPTED` | Optional; keep default false or `0` |
| `FASTSHOP_SHOPIFY_FIXTURE_PATH`, `FASTSHOP_WOOCOMMERCE_FIXTURE_PATH`, `FASTSHOP_WORDPRESS_FIXTURE_PATH` | Optional test-only JSON fixtures; unset; not used as fixtures in production |
| `FASTSHOP_SHOPIFY_<SITE_ID>_ENABLED`, `_SHOP`, `_ADMIN_API_ACCESS_TOKEN` | Optional Shopify import connector; unset/disabled |
| `FASTSHOP_WOOCOMMERCE_<SITE_ID>_ENABLED`, `_CURRENCY`, `_CURRENCY_SCALE` | Optional new enable/currency reads; unset/disabled; existing URL/consumer-key settings are unchanged |
| `FASTSHOP_WORDPRESS_<SITE_ID>_ENABLED`, `_REST_BASE_URL`, `_USERNAME`, `_APPLICATION_PASSWORD` | Optional WordPress import connector; unset/disabled |

Billing, plans, onboarding and verification do not block existing login or
boot. Verification can show an informational banner. Bootstrap queues no
verification email. Do not enable schedulers or external connectors for this
preparation. No production credentials or provider services were exercised.

## Rehearsal and evidence

Run `.venv\Scripts\python.exe scripts\verify_liquid_deploy.py` in PowerShell
from this repository. It archives `deploy/main` outside both repositories,
runs an independent old-code `uv sync --extra dev`, creates an old Alembic 0016
SQLite database with auto-creation disabled, boots the old seed, and creates
the two test users/memberships with old models. Known test password hashes
exist only in disposable copies. This branch upgrades a copy, boots in
production mode and checks HTTP with Starlette TestClient and the real Host
header. Playwright renders ASGI-served responses at 390 and 1440 pixels.
Both code versions otherwise call `create_all` unconditionally for SQLite,
despite the environment flag. The rehearsal disables that fallback before
importing each app, so missing migrations cannot be concealed by auto-creation.

The script also tests old Alembic's expected rejection, directly boots old
code against the upgraded copy, and restores Kinetic's binding before testing
its theme. Temporary code, environments and databases remain under the
printed `%TEMP%/h24you-deploy-*` directory. Screenshots are under
`output/playwright/h24you-liquid-deploy/`.

Release checks on 21 October 2026:

- Full isolated pytest with `PYTHONUTF8=1` and dotenv disabled: **692 passed**.
  The optional PowerPoint guide module skips without `python-pptx`. The existing
  Starlette/AnyIO deprecation warning remains. Final process exit code was zero.
- Ruff across the repository, compileall including migrations, and
  `git diff --check`: passed.
- Fresh disposable SQLite Alembic upgrade to `20261011_0029` and Alembic check:
  passed, with no new upgrade operations detected.
- Independent old-code 0016 database creation, legacy bootstrap, copy upgrade,
  Liquid hostname boot, twelve content/product routes plus cart/account,
  actual cart addition and checkout form: passed. No payment was submitted.
- Both named merchant logins, repeat login, eleven admin routes per account,
  Helen's tenant isolation, legacy Kinetic overview, repeated seed, role and
  password preservation: passed.
- Old-code boot on the upgraded copy: schema reads work; unchanged binding
  does not recover Kinetic. The exact documented binding command followed by
  old-code boot restores Kinetic home/shop/cart/account with 200 responses.
  Old Alembic's refusal of revision 0029 was reproduced as expected.
- A column-level comparison with the migration skill's compatibility checker
  reported `fully_compatible`. This does not replace the runtime rollback
  test or establish PostgreSQL constraint/DDL compatibility.
- Six desktop/mobile Playwright captures were generated; catalogue and site
  list evidence was inspected. The legacy catalogue's long mobile navigation
  is existing layout, outside this deployment task.

Retained evidence: `output/playwright/h24you-liquid-deploy/pytest-release.txt`,
`legacy-rehearsal.txt`, and the six PNGs. The final independent old-code
environment/database copies are in
`%TEMP%/h24you-deploy-e4ot1qak`; column comparison evidence is in
`%TEMP%/h24you-deploy-6f3jtx3e/compatibility.json`.

SQLite reproduces actual legacy rows and the revision chain, but does not
prove PostgreSQL search paths, row locks, concurrent starts, PostgreSQL DDL,
reverse-proxy TLS or external provider acceptance. Use one instance for this
demo cutover. No live deployment or provider acceptance is claimed.

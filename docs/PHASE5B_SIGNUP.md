# Phase 5b: self-serve signup and provisioning

Phase 5b replaces the public `/signup` placeholder with a kill-switched account flow that
creates a private merchant workspace without operator intervention. The flow remains part
of the platform marketing surface, not a tenant storefront or `/admin` route. It creates no
hostname: the first site stays available through the bounded `/sites/{slug}/` preview path.

## Launch gate and public states

`FASTSHOP_SIGNUP_OPEN` is read once into frozen `settings.signup_open` and defaults to false.
Both `GET /signup` and `POST /signup` show the same honest closed state while the flag is
off. The page does not predict an opening date and does not accept or retain form data.
Operators set the flag to `1` only when public signup is intentionally launched.

When the flag is on, `GET /signup` establishes the normal anonymous session and CSRF token.
The form accepts a bounded name, normalized email, password, and password confirmation.
Every state-changing signup, resend, and verification action validates CSRF and uses
Post/Redirect/Get. Failure pages retain no password. Duplicate-email, rate-limit, and other
non-field failures use the same response structure and generic message: “We could not
complete signup with these details.” Field guidance is shown only when the submitted email
does not belong to an existing account.

The established marketing design system is extended rather than replaced: warm paper,
botanical ink, ruled proof surfaces, restrained green actions, system fonts, visible focus,
and a responsive two-column task layout. There are no analytics, third-party assets, or
marketing claims on the signup surface.

## Provisioning transaction

Password signup performs the following work in one database transaction:

1. Normalize and validate the email, bound the merchant name, and create a `User` with the
   existing PBKDF2 per-user password-hash format.
2. Derive a lowercase site slug from the merchant name, bound it to the existing 3–61
   character site rule, and choose a globally unique numeric suffix when needed.
3. Call `app.content.create_site` once. That shared clean seeding path creates the `Tenant`,
   admin `Membership`, USD `Channel`, first draft `Site`, eight structured starter pages,
   navigation menus, and media backfill. It does not copy the H2 4 You fixture or legacy demo
   catalog.
4. Create a bounded, expiring, single-use account email-verification record and enqueue the
   matching transactional message in `commerce_mail` in the same transaction.
5. Record the accepted signup attempt and commit once.

After commit, the existing transactional-mail dispatcher may attempt delivery. Missing mail
credentials leave the durable queue row awaiting configuration; they never roll back the
account. The request then establishes the existing `user_id` / `role` / `email` session,
rotates CSRF, and redirects to `/admin/sites/{site_id}`.

Database uniqueness remains the concurrency authority. Slug selection is bounded and a
uniqueness race is retried with the next suffix without leaving partial users, tenants, or
sites.

## Abuse controls and retained data

`signup_attempts` is a small database-backed limiter shared by password and Google signup
and by verification resend. It stores only HMAC-SHA256 values for the direct socket client
address and normalized email; it never stores raw IP addresses, forwarded-IP headers,
emails, user agents, passwords, or form bodies. Stored strings are fixed at 64 characters
and the purpose is bounded. Signup is capped independently by client hash and email hash
within a 15-minute window. Resend has its own tighter bounded window.

Rows older than 24 hours are deleted opportunistically when the limiter is used. This is
long enough to enforce the bounded windows while minimizing retention. A rate-limit refusal
uses the same generic form result as a duplicate account and records no unbounded request
metadata.

## Email verification

`signup_email_verifications` contains tenant-, site-, and user-scoped records. The delivered
code is a bounded HMAC-derived value; only its SHA-256 digest is stored. The code expires,
is consumed atomically once, and is entered on the linked confirmation page, so it is not
sent in the initial HTTP request or normal access logs. Confirmation requires a CSRF-protected POST.
Reuse, expiry, a wrong token, and a cross-record token all fail with the same invalid-link
message.

Verification does not gate the builder in Phase 5b. An unverified merchant sees a workspace
banner with a CSRF-protected resend action. Resend is rate-limited and invalidates older
unused account-verification records before queuing a fresh one. Phase 5b deliberately adds
no terms checkbox; terms and consent UX are a public-launch concern and must be introduced
with the reviewed legal copy.

## Google OIDC

The existing authorization-code flow already provides state, PKCE, verified Google email,
and optional operator domain/email allowlists. Phase 5b therefore reuses it. Starting Google
signup is allowed only while `signup_open` is true. A first verified Google identity is
provisioned through the same clean site transaction, marked email-verified, logged in, and
redirected to its site overview. Existing accounts are signed in without creating another
tenant. Provider calls remain confined to the existing OIDC boundary.

## Migration

Migration `20261009_0025` adds `users.email_verified_at`, the two signup tables, and the
optional user target on `commerce_mail` needed for platform-account transactional mail.
Existing shop-customer messages remain site- and tenant-scoped and retain their current
behavior.

## Browser acceptance

`scripts/verify_site_browser.py --signup` renders the closed and open states at 1440px and
390px into `output/playwright/phase5b-signup/`. It checks the heading, form state, CSRF
presence, absence of overflow, analytics, iframes, inline scripts, and third-party requests.

## Verification record

Run Ruff and pytest in one PowerShell session with the operator-specified isolated
environment (including no `FASTSHOP_ADMIN_EMAIL` override):

```powershell
$env:DB_URL=''
$env:FASTSHOP_ENV='development'
$env:FASTSHOP_AUTO_CREATE_SCHEMA='1'
$env:XAI_API_KEY=''
$env:POSTMARK_API_TOKEN=''
$env:POSTMARK_SERVER_TOKEN=''
$env:STRIPE_SECRET_KEY=''
$env:STRIPE_WEBHOOK_SECRET=''
$env:FASTSHOP_DATA_DIR=(Join-Path ([System.IO.Path]::GetTempPath()) ('fastshop-tests-'+[guid]::NewGuid().ToString('N')))
uv run ruff check .
uv run python -m pytest -q
```

Alembic is checked separately against a disposable SQLite database with `upgrade head` and
`alembic check`; the database is then deleted. Compile validation runs separately with
`uv run python -m compileall -q app scripts`.

### Final results

- `uv run ruff check .`: exit 0, “All checks passed!”.
- `uv run python -m pytest -q`: 475 collected tests and 475 progress dots through
  `[100%]`; no `F`, `E`, `s`, or `x`; exit 0. The only emitted warning is the existing
  Starlette `BlockingPortal` deprecation warning.
- `uv run python -m compileall -q app scripts`: exit 0.
- Disposable SQLite `uv run alembic upgrade head`: exit 0; the final applied revision is
  `20261009_0025`.
- Disposable SQLite `uv run alembic check`: exit 0, “No new upgrade operations detected.”
  The disposable database was deleted after the check.
- `uv run python scripts/verify_site_browser.py --signup`: four desktop/mobile open/closed
  checks, zero failures, exit 0. The checked-in captures and JSON record are under
  `output/playwright/phase5b-signup/`.
- `git diff --check`: exit 0.

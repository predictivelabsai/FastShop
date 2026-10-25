# Retire legacy merchant entry points

## Goal

Every merchant entry point opens Merchant Admin v2. Keep commerce permissions,
tenant isolation, CSRF protection, integration saves and synchronisation intact.
Implement on `merchant-admin-v2`, then port the identical generic patch to
`fastshop-upstream` branch `admin-v2-entry-points` from freshly fetched
`origin/main`. Never push or deploy. Leave the server on port 5025 alone.

## Verified structure and references

- `app/main.py` owns legacy merchant pages and password/OAuth defaults. Defaults
  currently use `/admin`; that route renders `ui.merchant_page`.
- `app/ui.py` owns `MERCHANT_NAV`, legacy merchant presentation and provider guides.
- `app/admin_ui.py` supplies the v2 shell, store switcher and request context.
  Its store discovery uses active users and tenant memberships, including editors.
- `app/site_routes.py` owns `/admin/sites`, store creation and route registration.
- `app/admin_routes.py` owns v2 Home and the Settings directory.
- `app/site_integration_routes.py` supplies existing store connector workflows.
- `app/order_admin_routes.py` supplies v2 orders. Order detail identifiers use
  `SiteOrder`, so legacy `Order` identifiers need a scoped mapping.
- Other non-store routes include billing, onboarding and explicit platform routes.
  Audit their GET and POST handlers rather than applying a wildcard redirect.
- `scripts/verify_admin_v2_browser.py` already isolates configuration before app
  imports, but logs in with an explicit store-list destination and uses direct
  navigation. The new journey must exercise default login and sidebar clicks.
- `docs/admin-v2/02-reference-shopify-woo-elementor.md`: retain the dark frame,
  compact sidebar, white rounded canvas, store switcher and Settings grouping.
- `docs/specs/admin-v2-p1.md`: reuse the shared shell and existing security boundaries.
- `DESIGN.md` and `.impeccable/design.json`: preserve existing tokens and typography;
  this is navigation consolidation, not a redesign or storefront restyle.

## Behaviour and route map

Let `S` be an accessible store, selected by validated session preference, otherwise
the first store in deterministic name/id order. Revalidate membership every time.

| Old entry | Result |
| --- | --- |
| `/admin`, one accessible store | 303 to `/admin/sites/{id}` |
| `/admin`, several accessible stores | 303 to `/admin/sites` |
| `/admin`, no accessible stores | 303 to `/admin/sites`, v2 empty state and create-store action |
| `/admin/products` | 303 to `/admin/sites/S/products` |
| `/admin/orders` | 303 to `/admin/sites/S/orders`, preserving supported filters |
| `/admin/orders/{id}`, if an existing legacy handler is found | Resolve tenant/store-owned `SiteOrder`, then 303 to v2 detail |
| `/admin/customers` | 303 to `/admin/sites/S/customers` |
| `/admin/inventory` | 303 to `/admin/sites/S/inventory` |
| `/admin/promotions` | 303 to `/admin/sites/S/discounts` |
| `/admin/content` | 303 to `/admin/sites/S/content/pages` |
| `/admin/channels` | 303 to `/admin/sites/S/settings/channels` |
| `/admin/integrations` | 303 to `/admin/sites/S/settings/integrations` |
| `/admin/integrations/{stripe,woocommerce,paypal,fasterp}` | Corresponding provider under store Settings integrations |
| `/admin/integrations/fasterp/sync` | Preserve POST compatibility and CSRF; return to store Settings |
| `/admin/assistant` | 303 to `/admin/sites/S/assistant`, reachable from the shell |
| `/developers` | Authenticated merchant entry opens store Settings developers; retain public API documentation where required |
| `/admin/billing` and callbacks | Audit and consolidate the merchant presentation while preserving billing POST and callback semantics |
| `/admin/onboarding/{id}` | Preserve create-store onboarding and its ownership checks |
| `/admin/platform/*` | Keep separate, platform-admin-only views |

Unauthenticated entry points redirect to `/login` with a safely encoded original
path and query in `next`. Explicit safe destinations retain precedence over the
default. Password, OAuth and signup flows retain existing verification/onboarding
requirements and converge on the same merchant destination when complete.

Editors retain existing permissions: access to stores does not grant manager-only
orders, channels, provider settings, synchronisation or developer capabilities.
An invalid or revoked remembered store must never select another tenant implicitly.

## Implementation areas

1. Entry selection, auth defaults, legacy redirects and route audit in
   `app/main.py` and a small shared entry helper module.
2. Store picker, current-store memory, Settings and assistant navigation,
   legacy-only capabilities in the v2 shell, removal of unused legacy shell code.
   Reuse existing provider logic without creating a second persistence model.
3. Replace obsolete portal expectations and add redirect, permission, query,
   tenant-isolation, stale-session, CSRF and integration-save tests.
4. Extend browser verification to start at `/login`, use sidebar links, exercise
   the multi-store picker and crawl rendered shell links without mutating forms.

Task files split ownership of source, tests and verifier files. Integration of
shared registration contracts happens sequentially, after the source tasks.

## Acceptance checks in both trees

- Parameterised HTTP tests for every legacy entry: one, several and zero stores;
  unauthenticated login `next`; editor, merchant and platform-admin permissions.
- Explicit safe/unsafe `next` tests, default password and mocked OAuth/signup tests.
- Current-store memory tested on store entry, switching, revoked membership and
  cross-tenant IDs. Scoped detail mapping must not reveal foreign order existence.
- Integration reads and saves work through the v2 shell; CSRF and tenant checks
  reject invalid requests. Provider calls use local doubles, never live services.
- Browser journeys start at `/login` without `next`; one store reaches Home,
  multiple stores reach the picker, then sidebar clicks exercise the shell.
- Crawl actual rendered internal shell links and require no broken links, browser
  exceptions, console errors or unexpected failed responses.
- Desktop and mobile screenshots under
  `output/playwright/admin-v2/entry-points/`; inspect all, commit only a few.
- Run Ruff, compileall, full pytest, `git diff --check`, and Alembic upgrade/check
  on explicitly disposable SQLite databases. Set `PYTHON_DOTENV_DISABLED=1`,
  empty provider environment variables and disable scheduler before app imports.
  Never read `.env`, `secrets/`, credentials, existing data or session key files.
- Verify the generic patch matches upstream with no h24you or Liquid additions.
- Commit with plain British English messages, no attribution. Report both head
  SHAs, commits, final route map, test counts and open questions.

## Out of scope

Storefront/Liquid changes, schema expansion, provider redesign, live provider
operations, deployment, pushing branches and changing the port-5025 server.
Existing unrelated untracked screenshots remain untouched.

## Planning status

Route and configuration inspection completed. Application code and tests have
not changed or run. Implementation awaits the plan-and-dispatch skill's required
go-ahead. Upstream base: `70da7b1a9b8cd239cd9de63f1fdc405640e9ba93`.

# Merchant Admin v2 entry verification

Implemented on `merchant-admin-v2`, then ported to `admin-v2-entry-points` from
`70da7b1a9b8cd239cd9de63f1fdc405640e9ba93`. All four approved tasks completed
through dispatch. No push or deployment was performed. Port 5025 was untouched.

## Changes and security boundaries

Legacy merchant entries now use active, membership-scoped store discovery in
name/id order. A validated remembered store selects section destinations. A
revoked or foreign preference keeps requiring the picker until an accessible
store is explicitly selected. Platform roles never bypass store memberships.

Settings contains integration guides, channels and developers. Existing connector
persistence and saves remain in use. Assistant and account billing use the v2
shell. The picker retains resume-onboarding links and opens creation controls
when no stores are accessible. The unused legacy merchant shell is removed.

Authentication retains explicit safe return paths, verification and onboarding.
External, backslash and repeatedly encoded control destinations are rejected.
Anonymous admin GET requests preserve their original path and query in login
`next`. Manager-only operations, tenant isolation and CSRF checks remain enforced.

## Final route map

`S` is the validated remembered store, otherwise the first accessible store in
name/id order. A stale preference requires explicit selection. All legacy GET
redirects below return 303 when access is permitted.

| Entry | Destination or preserved behaviour |
| --- | --- |
| `/admin`, one store | `/admin/sites/{id}` |
| `/admin`, zero or several stores | `/admin/sites` |
| `/admin/products` | `/admin/sites/S/products` |
| `/admin/orders` | `/admin/sites/S/orders`, supported filters preserved |
| `/admin/customers` | `/admin/sites/S/customers` |
| `/admin/inventory` | `/admin/sites/S/inventory` |
| `/admin/promotions` | `/admin/sites/S/discounts` |
| `/admin/content` | `/admin/sites/S/content/pages` |
| `/admin/channels` | `/admin/sites/S/settings/channels` |
| `/admin/integrations` | `/admin/sites/S/settings/integrations` |
| `/admin/integrations/{stripe,woocommerce,paypal,fasterp}` | `/admin/sites/S/settings/integrations/{provider}` |
| POST `/admin/integrations/fasterp/sync` | Existing scoped synchronisation and CSRF; return to store Settings |
| `/admin/assistant` | `/admin/sites/S/assistant` |
| `/developers`, authenticated merchant | `/admin/sites/S/settings/developers` |
| `/developers`, public visitor | Public documentation retained |
| `/admin/billing` and callbacks | Existing account billing behaviour in the v2 shell; POST semantics retained |
| `/admin/onboarding/{id}` | Existing owned onboarding and protected mutations retained |
| `/admin/platform/*` | Separate operator-only presentation and permissions retained |
| `/admin/orders/{id}` | No legacy handler existed; no new alias introduced |

V2 detail URLs still use `SiteOrder` identifiers. Regression tests verify that
legacy `Order` IDs and foreign or sibling-store links cannot reveal an order.

## Validation

| Check | Downstream | Upstream |
| --- | --- | --- |
| Full pytest | 1,340 passed; 1 optional documentation skip | 1,158 passed |
| Ruff `check .` | Passed | Passed |
| Compileall: app, tests, scripts and entry point | Passed | Passed |
| Disposable SQLite Alembic `upgrade head` | Passed | Passed |
| Alembic `check` | No new upgrade operations | No new upgrade operations |
| `git diff --check` | Passed | Passed |
| Default-login entry verifier | Desktop and mobile passed; 13 captures | Desktop and mobile passed; 13 captures |
| Rendered entry links | 276 per viewport; 552 total | 276 per viewport; 552 total |
| Foundation verifier | Desktop and mobile passed; 14 captures | Desktop and mobile passed; 14 captures |
| Unified release verifier | 21 journeys; 295 captures; 12 downloads | 19 journeys; 255 captures; 12 downloads |
| Browser exceptions, console errors and unexpected failures | Zero | Zero |
| Server event-loop errors | Zero; clean shutdown | Zero; clean shutdown |
| Provider-isolation sentinel smoke check | Passed | Passed |

The downstream skip is the optional PowerPoint documentation module because
`python-pptx` is absent from that virtual environment. The application suite has
no failures. Validation used Python 3.13.5 downstream and Python 3.12.14 upstream.
The existing AnyIO deprecation warning is unchanged.

Additional visual QA covered the assistant, Settings, integration directory,
Stripe, PayPal, FastERP, channels and developers at both viewport sizes: 16
captures in the primary tree. Shared markup and admin CSS match upstream.

Final verification used explicitly disposable SQLite storage, disabled
dotenv loading and scheduler startup, blank provider configuration and fresh
session secrets. The final profile also blanks the FastERP health URL and inherited
connector fixture configuration before imports. No environment files, credentials, existing databases or saved
sessions were read. Browser provider calls used local doubles or disabled standalone configuration.

An earlier diagnostic profile allowed read-only FastERP health probes through its
pre-existing default URL. This was corrected, and the final checks were rerun
with that URL disabled. No live payment, synchronisation or delivery was performed.

Entry verification starts at `/login` without `next` for zero, one and two stores,
clicks the sidebar and switcher, checks mobile navigation and recursively follows
rendered merchant links without submitting forms. Public preview documents and
successful downloads are checked as such. The crawl fails on broken links,
placeholders, browser exceptions, console errors, failed requests or HTTP errors.

Entry screenshots were opened individually. Matching upstream captures were
compared pixel-for-pixel, and the differing capture was opened separately.
Foundation and release screenshots were reviewed using labelled contact sheets.
Only four generic entry screenshots per tree are selected for the commit.

## Port parity and working files

Twelve changed files have identical contents in both trees. Five other changed
files retain exactly their pre-existing baseline differences, verified by
comparing zero-context differences before and after the patch. All generic
behaviour is identical. The upstream source, tests, scripts, docs and static
assets were checked for prohibited customer references.

The approved planning document is retained only in the downstream tree. No task
working files are committed upstream. The four completed task working files were
removed from the primary tree. The prescribed cleanup command retained task
worktrees containing generated verification artefacts.

## Implementation commits

These lists precede the final verification-record commit.

### Downstream

```text
8e75227 Record approved merchant entry point specification
19fd71b Bring merchant capabilities into the store admin shell
4de72bf Route merchant entry points to store administration
589d802 Align store selection, billing and onboarding navigation
0ccfacc Verify default merchant login and rendered navigation
932bfcd Cover merchant entry routing and security boundaries
785c0ad Merge merchant entry browser verification
47de50a Expose store creation in the empty picker and tidy shell imports
87817c3 Use shared components and isolate connector verification
7afc9f3 Clear inherited connector configuration during verification
```

### Upstream

```text
1ae67b8 Consolidate merchant entry points in store administration
436296a Use shared components and isolate connector verification
81e9212 Clear inherited connector configuration during verification
```

Open questions: none. Remaining task-worktree artefacts are local cleanup only.

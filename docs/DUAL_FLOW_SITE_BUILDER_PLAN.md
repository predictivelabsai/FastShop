# FastShop: classical and conversational site building

Original implementation plan, 2026-09-21. For delivered behavior and verification,
see [implementation status](DUAL_FLOW_IMPLEMENTATION_STATUS.md).

## Direction and scope

Keep FastShop as the commerce/content platform. Offer two interchangeable entry
points to the same site, catalog, settings and revisions:

- **Classical editor:** forms for pages, sections, design, products and commerce.
- **Build with AI:** guided questions and natural-language editing beside a live
  draft preview. Answers progressively build the site, rather than collecting a
  long questionnaire before anything becomes visible.

The user's latest direction authorizes synthetic merchant data for demonstrations,
with manual replacement. Missing real warehouse details and Stripe credentials
must no longer prevent a demo. They still prevent claiming real-provider acceptance.
Do not fabricate provider credentials or quietly represent a simulated order as paid.

## Research and what to adapt

Lovable places chat beside an interactive preview, with history and publication
controls nearby. Adapt that interaction pattern to structured FastShop sites,
not a general-purpose code-generation environment.
[Official editor documentation](https://docs.lovable.dev/features/projects/editor).

Lovable separates planning from execution and uses clarifying questions before
changes. FastShop should distinguish discussion from a requested draft edit,
while making ordinary design edits immediately visible and reversible.
[Official Plan mode documentation](https://docs.lovable.dev/features/plan-mode).

Lovable's preview tools support pointing at elements and editing text directly.
FastShop should identify sections by stable IDs so “make this hero smaller” has
an explicit target. Inline text editing is a subsequent increment; freehand
annotation and arbitrary code editing are not required for the first release.
[Official preview toolbar documentation](https://docs.lovable.dev/features/preview-toolbar).

This is documentation research, not a hands-on audit of an authenticated Lovable project.

## Existing foundations and gaps

- `app/content.py`: section schema, membership checks, media ownership, draft saves
  and page revisions. Reuse and strengthen validation rather than bypassing it.
- `app/site_routes.py`: site creation, settings, page editor and authenticated
  iframe preview already exist. Add a flow selector and conversational workspace.
- `app/site_ui.py`: shared server-rendered storefront. Both editing paths must
  render through it, not through a separate AI-only mockup.
- `static/site-builder.css`: many storefront values are currently fixed. Introduce
  validated design tokens before promising prompt-driven design changes.
- `app/platform_ui.py`: preserve the FastShop logo, HTML shell and CSS identity
  across both editors. Merchant theme tokens must not recolor platform controls.
- `app/commerce_routes.py`: origin and fees already have manual forms. Add demo
  provenance, readiness and replacement controls, not a second settings store.
- `app/ai.py`: existing read-only assistant uses demo-specific copy and queries
  without the site boundary needed here. Do not call its broad context builder
  from the new editor; implement a separate site-scoped builder adapter.

## Merchant experience

At site creation, show **Classical editor** and **Build with AI**, with an optional
**Start with sample data** choice. Existing sites can enter either editor.

Desktop workspace:

```text
FastShop | Site | Classical / AI | Saved draft | Undo | Review & publish
-------------------------------+--------------------------------------
Conversation / settings         | Page selector | Desktop / Mobile
                               |
“What are you selling?”         | Live server-rendered draft preview
“What design language fits?”    | Select a section to focus the prompt
[Minimal] [Editorial] [Bold]     |
                               |
Describe a change…              | Changed: palette, hero, typography
-------------------------------+--------------------------------------
Sample data remaining: company, warehouse, shipping fee… [Review]
```

On mobile, use accessible Chat / Preview / Settings tabs rather than squeezed
columns. Preserve focus, selected page and scroll position when updating.

Ask one relevant question at a time, skipping information already supplied:

1. Business, products, intended audience and site name.
2. Design language: mood, colors, typography, density and optional reference image.
3. Essential pages and content tone.
4. Products, prices, variants and subscription preferences.
5. EU origin, US shipping and merchant details, or explicitly labelled samples.

Example: “Minimal, warm, cream and forest green” updates theme tokens and the
hero draft; “more editorial, less whitespace” changes typography/spacing without
silently changing products, prices or tax settings. Display what actually changed.
An ambiguous request triggers a question instead of an unrelated redesign.

## Synthetic data and commerce environments

Keep these separate from draft/published content status:

| Environment | Behavior |
| --- | --- |
| Demo | Synthetic merchant/catalog/customer data; deterministic simulated checkout, tax, renewals, tracking and local email inbox; no provider calls |
| Stripe sandbox | Real Stripe test credentials and explicitly reviewed test inputs; test provider behavior, not live sales |
| Live | Separate future release gate; never enabled by AI or sample-data setup |

Use a private demo site/data boundary. Seed company and warehouse fields, `.test`
email addresses, sample USD products and illustrative shipping (e.g. $10 below
$75). Label every synthetic field and simulated total; invented state/ZIP examples
are not authoritative tax rates. Users can manually replace each field and mark
it reviewed. Do not overwrite reviewed fields when adding or resetting samples.

Persist field provenance (synthetic, merchant-entered, AI-proposed), review state
and timestamps. Clearing a sample badge requires an explicit merchant review,
not merely an LLM assertion. Keep legal company address distinct from warehouse.

Demo mail goes only to an authenticated local inbox; demo stock, payment IDs and
orders must not reach Stripe, Postmark, FastERP or real fulfillment. Demo records
must remain identifiable and excluded from real revenue/reporting. Moving to
sandbox creates clean operational state; do not promote simulated paid orders,
customers' fake payment methods or subscriptions into real-provider records.

## Architecture and data flow

1. Browser submits a message with site ID, selected page/section, revision and
   unique command ID. The server resolves membership and actual tenant scope.
2. A durable builder turn records pending work. Call the model outside database
   locks with only this site's necessary draft context, never secrets or customers.
3. The model returns a structured response: answer, optional next question,
   allowlisted operations and proposed change summary. No executable HTML/JS/SQL.
4. Validate operation schema, target ownership, URLs, sizes, theme tokens and
   permissions; reject unknown operations and unsafe content.
5. Recheck site/page versions, then atomically save validated draft changes and
   a changeset/revision. Stale model results cannot overwrite manual edits.
6. Refresh the private preview after a committed revision. Stream progress if
   useful, but never render partial/unvalidated model output. Polling fallback
   and reconnect use the durable command ID without applying twice.

Suggested modules: `site_builder_services.py`, `site_builder_routes.py`,
`site_theme.py`, `integrations/site_builder_llm.py`, `demo_commerce.py` and scoped
builder CSS/JS. Continue using FastHTML; a frontend framework rewrite is unnecessary.

Add site/tenant-owned conversation, message/turn and changeset records with
actor, status, expected/result versions, idempotency key and operation summary.
Extend revision coverage to shared settings/theme as well as pages. Undo creates
a new validated revision; it never rewinds payments, stock or customer records.

Theme v1: palette, approved font stacks, type scale, spacing, content width,
corner radius, button style, hero alignment and supported section layouts.
Render bounded values as CSS variables scoped to the storefront root. Preserve
H24YOU's current design as a compatible preset. Do not accept arbitrary CSS.

Supported initial operations: update design tokens; edit/reorder/add supported
sections; update copy/navigation; create a supported page; propose catalog or
merchant-setting changes. Financial/operational proposals show a review card and
require explicit merchant confirmation before applying. Publishing, credentials,
payments, refunds and fulfillment are never autonomous model operations.

Both editors call shared services for validation, saving and revision history.
The preview references the same persisted draft; switching modes does not copy
or fork data. Preview messages must validate origin, frame source and scoped IDs.

Use the configured model behind a provider interface, not a hard-coded provider
decision. Add bounded context, timeout, rate/cost limits, cancellation and generic
errors. Without a key, offer a labelled guided preset wizard—not pretend LLM chat.
On provider failure, preserve the draft and allow continued manual editing.

## Implementation sequence and acceptance gates

1. **Demo foundations and manual replacement.** Add isolated demo environment,
   field provenance, sample seeding, merchant replacement forms and local mail.
   Demonstrate checkout/account/subscription/tracking without credentials, with
   zero external calls and prominent simulation labels.
2. **Shared design/editing foundation.** Tokenize theme, add shared setting
   revisions/undo and route both editing paths through the same services.
   Existing H24YOU pages retain their design; classical editing remains usable.
3. **First conversational slice.** Add flow selector, chat plus private preview,
   progressive questions and validated theme/hero/copy operations. Demonstrate
   “cream and green, more editorial” updating the real draft on desktop/mobile.
   Show this working slice for review before broadening AI commerce operations.
4. **Complete site setup.** Add multipage composition, section targeting, product
   and merchant-setting review cards, resumable conversations and mode switching.
   Demo data stays manually editable; financial changes require confirmation.
5. **Hardening and release review.** Cover cross-tenant access, injected prompts,
   invalid patches, stale versions, double submissions, interrupted turns, undo,
   role boundaries and provider failure. Verify complete demo shopping lifecycle.
   Run Ruff, pytest, compile, Alembic and diff checks, plus desktop/mobile
   Playwright screenshots under `output/playwright/`. Real-provider acceptance
   stays a distinct checklist, not a blocker for the explicitly simulated demo.

First release is accepted when a merchant can create a site through either path,
see progressive design changes, switch to forms and back without loss, replace
sample merchant details, undo an AI edit and preview the same stored site. Demo
commerce must work without secrets, and no AI change may publish or charge.

## Decisions and limits

- Recommended: one FastShop application and data model; no migration to FastCMS
  required for this feature. Reuse content concepts already present in FastShop.
- FastShop platform branding remains fixed; the merchant's storefront design is
  configurable. Both are intentional, separate branding layers.
- Start with editable structured components, not unrestricted app generation.
- No new implementation or deployment is performed by this planning document.

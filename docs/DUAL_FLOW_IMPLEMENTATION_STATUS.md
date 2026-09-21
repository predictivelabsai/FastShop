# Dual-flow implementation status

Implemented locally on 2026-09-21. This records local acceptance, not a production
deployment or real-payment certification.

## Delivered

- Classical/chat creation paths over one persisted draft, private preview,
  responsive workspace and consistent FastShop platform branding.
- Bounded storefront themes, progressive saved brief, copy, navigation, section
  edit/add/reorder and page creation. The configured model supplies structured
  operations; without a key the UI explicitly uses guided presets.
- Section targeting with keyboard support, iframe origin/source checks, AJAX
  refresh, retained selection, page/viewport controls and mode switching.
- Durable idempotent turns, stale-version protection, cancellation, safe provider
  errors, shared classical/AI revision history and current-revision undo.
- Product, price and merchant proposals with before/after review cards, explicit
  merchant confirmation, rejection, stale-data checks and atomic writes. These
  cannot enable payments, publish pages or approve tax registrations.
- Synthetic merchant setup, field provenance and manual replacement. Editing a
  reviewed value invalidates review. Outstanding reviews appear in both editors;
  reviewed sample fields are required before enabling Stripe sandbox.
- Isolated merchant commerce simulator: editable synthetic products/stock, US
  fixture tax, checkout success/decline, newsletter opt-in and offer, account,
  subscription controls/renewals, tracking and local inbox. Demo tables never
  become real customers/orders/payments/mail/outbox.
- Screenshot-led [user guide](USER_GUIDE.md), desktop/mobile captures under
  `output/playwright/dual-flow/`, and reproducible nine-slide
  [productdemo.gif](../static/productdemo.gif).

## Verification

- The 205-test regression suite passes against isolated SQLite fixtures. After
  the final navigation addition, 34 focused builder/sample tests and five route
  tests pass; the repository now collects 206 tests. One upstream Starlette/AnyIO
  deprecation warning remains.
- Playwright Chrome desktop/mobile journey passes: live draft updates, section
  targeting, retained selection, classical controls, undo, sample replacement,
  pending shipping approval and complete simulated commerce lifecycle.
- Browser checks confirm real-commerce row counts do not change and block
  external browser requests. Service tests cover tenant/role boundaries, CSRF,
  duplicate commands, invalid operations, rollback, conflicts and interruption.
- Ruff, Python compilation, JavaScript syntax and `git diff --check` pass.
- Fresh SQLite migration through `20260921_0014` succeeds; Alembic reports no model
  differences. Live PostgreSQL migration/concurrency acceptance is not claimed.
- Model adapter tests use fixtures for scoped context and malformed responses;
  no live model, Stripe, payment or email-provider acceptance is claimed.

## Deliberate first-release limits

- Guided mode records business/audience/pages/tone and applies presets/headlines;
  it is not an LLM or automatic full-site generator. Open-ended composition needs
  the configured model. Inline/freehand editing is deferred.
- History displays the latest 50 changes; only the current unchanged revision can
  be undone. Older page restoration remains in the classical editor.
- Merchant-detail proposals require disabled commerce. Subscription eligibility,
  tax categories, registrations and provider configuration remain explicit forms.
- Demo taxes are illustrative, not authoritative US rates. Provider tax uses the
  existing Stripe sandbox boundary and full delivery address.
- Demo catalog and shopper identities are separate. Sandbox uses clean operational
  state; simulated orders, cards or subscriptions are not migrated.
- Real-provider and launch gates remain in [Phase 2 acceptance](PHASE2_ACCEPTANCE_STATUS.md).
  A pushed commit is not proof of a deployed or live-ready store.

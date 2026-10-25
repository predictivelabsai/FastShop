# Merchant Admin v2 functional gap closure

Date: 25 October 2026. Worktree: `fastshop-admin-editor`, branch
`merchant-admin-v2-gaps`. No branch switch, push or deployment. Environment
files and existing secrets were not read. Provider and mail boundaries use local
test doubles; no real payment or email delivery was performed.

## Functional coverage

| Audit gap | Implementation |
| --- | --- |
| Custom data | Site-scoped definitions for products, variants, collections, customers, orders and pages; typed validation, required/bounds/choices, pinned order, owner cards, product/variant API reads and compatible text/image dynamic sources in product templates. Rich text uses the existing HTML sanitiser. |
| Store preferences | Homepage metadata, sharing image, favicon, hashed password with styled storefront form and merchant bypass, consent-aware Analytics/Meta IDs. The storefront renderer use head metadata without redesigning the theme. Shared admin/auth/webhook/health paths remain available. |
| Redirects | Scoped searchable CRUD, CSV import/export, chain/loop prevention, repeated-handle alias flattening and default-on automatic redirects for product, collection, page and article handle changes. Real storefront routes take precedence. |
| Order editing | Variant/custom additions, removals, quantities and line discounts, required reason, optimistic order fingerprint and idempotent edit session. Stable stock locks and reservation adjustments preserve other orders' commitments, including after partial fulfilment. Payment difference supports queued invoices/manual payment or the existing refund flow. |
| Bulk editor | Selected product variants, prices/compare-at/cost in minor units, SKU/barcode/status/inventory tracking and stock by active location. Keyboard navigation, copy down, column picker, dirty cells and one save with per-row errors and optimistic fingerprints. Product groups use savepoints. |
| Saved views | Per-user/site named filters, sort and columns on product/order/customer indexes, rename/delete and persisted column preferences. Stored filters and columns are allowlisted. |
| Locations | Warehouse name/address/active/online fulfilment/default settings and protected deletion. Committed stock prevents deactivation or disabling fulfilment. Storefront availability, reservations and fulfilment respect location flags. |
| Activity | Transactional append-only actor/site/object/action summaries collected from main admin mutations. No form payloads, secret values or private notes are copied into the log. Filtered activity index and per-object cards. ORM updates, deletes and bulk mutation of log records are rejected. |
| Theme library | Current theme, isolated draft snapshots of design/settings/page documents, editor theme context, detached previews, rename/delete, atomic promotion and previous-live snapshots for rollback. Snapshot versions reject stale edits. |
| Gift cards | Sandbox issue/search/disable, customer/expiry/note, balance history and idempotent adjustments. Checkout reserves balance, caps tender to available funds/order total, sends only the balance to the provider and records separate tender on completion. Fully funded checkout makes no provider call. Full refunds restore the card once, including disabled cards. |

The out-of-scope markets, currencies, translation, purchasing/transfers and POS
features were not added. Existing shipping and tax are retained during order
editing and this is stated on the edit page. Recurring orders cannot use gift
cards. Gift-card checkout remains sandbox-only.

## Persistence and security

One Alembic revision, `20261025_0034_admin_gaps`, follows
`20261025_0033_merge_admin_v2`. It adds the feature tables and Warehouse flags,
backfilling a default location for existing tenants. New route registrars follow
the existing actor/CSRF/shell pattern. Mutation routes check tenant membership
and the appropriate merchant/admin permission; saved views are personal to each
authorised user, including editors. Owned references are site/tenant checked.

Order/provider money remains integer minor units. Gift checkout preserves gross
order totals separately from net provider payable. Reconciliation compares the
immutable provider capture rather than a subsequently edited order total.

## Validation evidence

Current upstream validation, database round trips and browser coverage are
recorded in `04-qa-report.md`. The combined verifier exercises every generic
admin feature with separate desktop and mobile stores and local provider doubles.

## Open issues and operational boundaries

Live provider acceptance and production rollout remain operator tasks. Optional
customer packs register their own blocks, schemas and rendering hooks as described
in `07-theme-pack-integration.md`.

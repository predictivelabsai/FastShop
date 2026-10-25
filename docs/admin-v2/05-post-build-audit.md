# Merchant Admin v2: post-build audit

Date: 25 October 2026. Compared the built admin against the Shopify, WooCommerce and Elementor baseline in
`02-reference-shopify-woo-elementor.md`.

## 1. Coverage now in place

Shell, Home, global search, products with options/variants/media/SEO, collections (manual and
automated), inventory with history, CSV import/export (products, customers, orders export), orders
index and detail with fulfilment, line refunds with restock, returns, comments, tags, packing slip,
draft orders, abandoned checkouts, customers with addresses/consent/tags, discounts (four types,
wired into checkout), subscriptions console, analytics with comparison, pages, blog, menu tree
editor, files, staff invites and roles, payments/checkout/shipping zones/taxes/notifications/domains/
policies/billing settings, and the drag-and-drop visual editor with nesting, inline editing, style
controls, device preview, undo/redo, revisions and publish.

## 2. Remaining functional gaps

| Priority | Gap | Shopify / Woo reference |
|---|---|---|
| P1 | Custom fields (metafields) for products, variants, collections, customers, orders: definitions (type, validation) and values editable on each detail page, usable as dynamic sources in the editor | Settings > Custom data |
| P1 | Online Store Preferences: homepage meta title/description, social sharing image, favicon, store password protection page, analytics/pixel ids | Online Store > Preferences |
| P1 | URL redirects (create, CSV import, automatic redirect when a product/page handle changes) | Content > Navigation > URL redirects |
| P1 | Order editing after placement (add/remove items, adjust quantities, with payment difference recorded) | Orders > Edit |
| P1 | Spreadsheet-style bulk editor for products/variants (price, compare-at, SKU, stock, status) | Products > Bulk edit |
| P2 | Saved views and column picker on index tables (products, orders, customers) | Index filters "Save as" |
| P2 | Locations management (add/edit warehouses/locations, default location, per-location fulfilment) | Settings > Locations |
| P2 | Activity log (who changed what) across admin objects | Store activity |
| P2 | Theme library (duplicate theme draft, preview, publish, rollback) | Online Store > Themes |
| P2 | Gift cards | Products > Gift cards |
| Out of scope | Markets/multi-currency/translations (the initial scope uses English and USD), purchase orders/transfers, POS | |

## 3. Usability and polish gaps (observed in screenshots)

- Native `select`, date and file inputs dominate forms; Shopify uses styled selects, segmented
  controls, date pickers and dropzones. Discount creation uses a type `select` instead of the
  Shopify type picker modal; "Applies to" lacks a product/collection search picker.
- Page header back control renders as a tiny underlined arrow; an underlined "Catalogue" kicker sits
  above titles. Breadcrumb plus back arrow should be one clean pattern.
- Index filters are a large form with an "Apply filters" button. Shopify uses one search field with
  instant results, an "Add filter" popover and removable chips; sort is a compact icon menu.
- Order timeline is noisy and technical: duplicate refund events, "Order Refunded / Delivery state:
  pending; attempts: 0", raw enum text such as `partially_refunded`. Merchant-facing timeline should
  merge duplicates, humanise wording, and hide integration delivery internals behind a details toggle.
- Refund, return and cancel actions hide in `details` disclosures; Shopify uses primary "Fulfil",
  "Refund", "Return", "Edit" buttons in the header and a More actions menu.
- Visual editor tree shows internal lowercase labels ("hero", "research figures", "sample reviews")
  without icons; it should show the schema label, an icon and the section heading as a subtitle.
- Mobile: long index tables scroll horizontally; Shopify collapses rows into stacked cards.
- Empty states, loading states and success toasts are inconsistent across older migrated pages.

Current upstream verification is recorded in `04-qa-report.md`.

# Reference audit: Shopify admin, WooCommerce and Elementor

Observed 2026-10-25 on the Shopify development store `winfluential-theme` (read-only walk-through,
nothing saved), plus WooCommerce/WordPress and Elementor feature sets. This is the target feature and
workflow baseline for FastShop Merchant Admin v2.

## 1. Shell and navigation (Shopify)

- Fixed left sidebar on a near-black frame, white rounded content canvas. Global search (Ctrl K) at the
  top of the sidebar. Primary items with icons: Home, Orders, Products, Customers, Growth/Marketing,
  Discounts, Content, Markets, Finance, Analytics. Section items expand inline into sub-items
  (Products: Collections, Inventory, Purchase orders, Transfers, Gift cards).
- "Sales channels" group (Online Store: Themes, Pages, Preferences), "Apps" group, Settings pinned at
  the bottom, store switcher with avatar.
- Page header: icon + title on the left, secondary buttons (Export, Import, More actions) and one
  primary dark button (Add product) on the right. Detail pages have a back arrow and breadcrumb.
- Contextual save bar: on any unsaved change a sticky top bar appears ("Unsaved changes", Discard,
  Save). Toasts confirm actions.
- Settings is its own full-screen area with a searchable left list: General, Plan, Billing, Users,
  Payments, Checkout, Customer accounts, Shipping and delivery, Taxes and duties, Locations, Markets,
  Apps, Sales channels, Domains, Notifications, Metafields and metaobjects, Languages, Policies.
- An AI assistant bar floats at the bottom of every page.

## 2. Index (list) pages

Pattern used for products, orders, customers, discounts, collections, pages and blog posts:
- Summary metric strip above the table (e.g. sell-through rate, days of inventory).
- Saved-view tabs (All, Active, Draft, Archived, plus custom saved views) and a "Search and filter" bar
  with filter chips (status, vendor, type, tag, collection, inventory) and sort.
- Table with checkbox column, thumbnail, title, status badge (Active green, Draft, Archived),
  inventory, category, channels, type, vendor. Column picker icon. Pagination.
- Bulk actions after selection: set active/draft, archive, delete, add/remove tags, add to collection,
  bulk editor (spreadsheet-style inline edit of price, SKU, stock, etc.).
- Import and Export (CSV) buttons. Empty states with an illustration and primary call to action.

## 3. Product detail (Add/Edit product)

Two-column layout. Main column cards:
- Title; Description (rich text: paragraph styles, bold, italic, underline, colour, alignment, link,
  image, video, table, lists, indent, clear formatting, HTML source view, AI generate).
- Media: drag-and-drop upload or "Select existing" from Files; images, video, 3D; reorder by drag; alt
  text per image.
- Category (standard taxonomy, drives tax and metafields).
- Pricing: price, compare-at price, unit price, charge tax toggle, cost per item (shows profit and margin).
- Inventory: tracked toggle, quantity per location, SKU, barcode, sell when out of stock.
- Shipping: physical product toggle, package, weight, dimensions, country of origin, HS code.
- Variants: "Add options like size or colour"; option names and values, auto-generated variant matrix
  with per-variant price, stock, SKU, image; group by option; bulk edit variants.
- Purchase options (subscriptions, preorders), metafields, search engine listing preview (SEO title,
  meta description, URL handle).
Side column cards: Status (Active, Draft, Archived), Publishing (sales channels, schedule), Product
organisation (type, vendor, collections, tags), Theme template.

## 4. Orders

- Index: order number, date, customer, channel, total, payment status (Paid, Pending, Refunded,
  Partially refunded), fulfilment status (Unfulfilled, Fulfilled, Partially fulfilled), items,
  delivery method, tags. Tabs: All, Unfulfilled, Unpaid, Open, Archived. Drafts and Abandoned checkouts
  as sub-pages.
- Detail: line items grouped by fulfilment, "Fulfil items" (tracking number, carrier, notify customer),
  payment summary (subtotal, discounts, shipping, tax, total, paid by customer), Refund flow (pick
  items and quantities, restock toggle, shipping refund, reason), Return flow, Edit order, Timeline
  with comments and events, side cards for notes, customer, contact, shipping and billing address,
  tags, conversion summary, risk. More actions: duplicate, cancel, archive, print packing slip.
- Draft orders: build an order in admin, send invoice, mark as paid.

## 5. Customers

Index with segments and filters (spend, orders count, subscribed, location, tags), import/export.
Detail: overview, order history, lifetime spend, addresses, marketing consent (email, SMS), tags,
notes, timeline, account status (invite to create account).

## 6. Discounts

Create flow picks a type: Amount off products, Buy X get Y, Amount off order, Free shipping. Form
fields: method (discount code or automatic), code with random generator, value (percentage or fixed),
applies to (collections or products, browse picker), eligibility (all customers, segments, specific
customers), minimum purchase requirement (amount or quantity), maximum uses (total and once per
customer), combinations (product, order, shipping), active dates (start, optional end). Live summary
card on the right. Index shows status (Active, Scheduled, Expired), method, type, usage count.

## 7. Content

Metaobjects, Files (media library with upload, search, type filter, alt text, usage), Menus
(navigation trees with nested drag-and-drop items linking to pages, collections, products, URLs),
Blog posts (title, rich content, excerpt, featured image, author, tags, visibility, SEO), Pages (title,
rich content, template, visibility and publish date, SEO).

## 8. Theme editor (Online Store > Edit theme)

- Full-screen editor. Top bar: exit, three left-rail modes (Sections tree, Theme settings, App
  embeds), theme name with Active badge, page/template switcher (Home page dropdown lists every
  template: product, collection, pages, blog, cart, 404), inspector toggle (click-to-select in
  preview), device toggle (desktop, mobile, full-width), undo, redo, more menu, Save with publish
  dropdown.
- Left panel tree: Header group, Template group (sections in order), Footer group. Each section row has
  a drag handle, hide (eye) and delete actions on hover; sections expand to show their blocks, each
  block draggable and with its own "Add block" entry. "Add section" opens a searchable picker with
  categories and a live thumbnail preview of the section before inserting.
- Selecting a section highlights it in the live preview (blue outline and label) and opens its
  settings panel below or beside the tree: grouped fields (text, textarea, rich text, image picker,
  URL/link picker with search, colour, range slider, select, checkbox, product/collection pickers).
  Fields that accept dynamic sources have a "connect dynamic source" icon.
- Floating toolbar on the selected element in preview: AI "Ask for changes", duplicate, hide, delete.
- Theme settings: colours (schemes), typography, layout widths, buttons, inputs, cards, social media,
  favicon, cart behaviour, custom CSS.
- Changes preview instantly without page reload; nothing is live until Save.

## 9. WooCommerce / WordPress equivalents worth matching

- Products: simple, variable (attributes and variations), grouped, external; short description; sale
  price with schedule; stock status and backorders; upsells and cross-sells; downloadable/virtual;
  product categories and tags; reviews moderation.
- Orders: statuses (pending, processing, on hold, completed, cancelled, refunded, failed), order notes
  (private or to customer), manual order creation, refunds with restock.
- Reports/Analytics: revenue, orders, products, categories, coupons, taxes, downloads, stock, with date
  range comparison.
- Settings: general (address, selling locations, currency), products (units, inventory thresholds),
  tax (classes, rates table), shipping (zones, methods: flat rate, free shipping with minimum, local
  pickup), payments, accounts and privacy, emails (per-email enable, subject, heading, template), advanced.
- WordPress: Gutenberg block inserter, reusable blocks/patterns, revisions with compare and restore,
  scheduled publish, users and roles (administrator, shop manager, editor).

## 10. Elementor editor patterns worth matching

- Left panel: widget library grid (search, categories: layout, basic, general, WooCommerce, site),
  drag any widget onto the canvas; drop indicators show where it will land (before, after, inside a
  container). Containers/columns with flex or grid, nesting.
- Element selected: tabbed settings panel Content / Style / Advanced (margin, padding, background,
  border, motion, responsive visibility, custom CSS class). Per-device values (desktop, tablet, mobile).
- Canvas: hover outlines with element handle (edit, duplicate, delete), inline text editing, right-
  click context menu (copy, paste, paste style, duplicate, delete, navigator).
- Navigator: tree of all elements with drag reordering. History panel with undo/redo and revisions.
- Global colours and fonts (site settings), saved templates and sections, page settings, preview and
  publish with draft state.

## 11. Target experience for FastShop (summary)

1. Shopify-like shell: dark sidebar with grouped navigation, global search, contextual save bar,
   toasts, consistent index pages with filters, tabs, bulk actions, CSV import/export.
2. Full commerce object coverage: products with options/variants/media/SEO/status, collections,
   inventory, orders with fulfil/refund/notes/timeline, draft orders, customers, discounts,
   analytics, settings sub-areas.
3. A visual editor that combines Shopify's section/block tree and settings panel with Elementor's
   drag-from-library onto the canvas, inline text editing, per-element style controls, device
   preview, undo/redo, draft save and publish, and revision restore.

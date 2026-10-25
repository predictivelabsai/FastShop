# Merchant Admin v2: gap analysis and implementation plan

Inputs: `01-fastshop-current-state.md` (current FastShop admin, routes, models, editor) and
`02-reference-shopify-woo-elementor.md` (target baseline). Branch: `merchant-admin-v2`.

## 1. Gap matrix

| Area | Shopify / Woo baseline | FastShop today | Gap to close |
|---|---|---|---|
| Shell | Dark grouped sidebar, global search, save bar, toasts, consistent headers | Three different shells (legacy `merchant_page`, `platform_page`, builder); links scattered on site overview | One Shopify-style admin shell for every merchant page |
| Home | Setup guide, sales snapshot, tasks | Site overview link list | Dashboard with KPIs, to-dos (unfulfilled orders, low stock, drafts), setup checklist |
| Products | Status, rich description, media gallery, options and variant matrix, pricing incl. compare-at and cost, inventory per location, SKU/barcode, shipping, SEO, vendor, type, tags, collections, search/filter/bulk/CSV | Name, single image URL, channel prices, comma-separated variants | Full product form, options/variants, media, status, tags, vendor, collections, bulk editor, CSV import/export |
| Collections | Manual and automated (rules) | Categories only, no merchant UI | Collection model + manual and rule-based collections |
| Inventory | Per-location quantities, adjustments history, low stock | Read-only legacy page | Inventory index with inline adjust, adjustment log, low-stock threshold |
| Orders | Index with tabs, filters, search; detail with fulfil, refund (line-level, restock), return, edit, notes, timeline, tags; draft orders; abandoned checkouts | Good fulfilment/refund core, fulfilment-status filter only | Search/filter/pagination/tabs, notes and timeline comments, tags, line-level refund with restock, returns, draft orders, abandoned checkouts, packing slip |
| Customers | Index with segments, detail with orders, addresses, tags, notes, consent, CSV | Read-only list capped at 100 | Customer index and detail, tags, notes, addresses, consent, CSV |
| Discounts | Four types, code/automatic, conditions, limits, combinations, schedule | Voucher model, no admin | Discount admin (create/edit/schedule/usage) wired into checkout |
| Subscriptions | Contracts list, pause/cancel/skip | Backend only | Merchant subscriptions console |
| Content | Pages, blog posts, menus (nested drag tree), files | Pages on overview, menus form, media library | Content index pages with search, menu tree editor with drag-and-drop, Files page in new shell |
| Online store editor | Section/block tree, add-section picker, settings panel, inline preview, device toggle, undo/redo, save/publish, theme settings | Long form with Up/Down buttons, chat builder, design controls | New visual editor (see section 3) |
| Analytics | Sales, orders, AOV, top products, date ranges | Revenue page | Analytics dashboard with date ranges and comparison |
| Settings | Unified searchable settings area | Spread over overview, commerce, go-live, samples | Settings area: general, users and permissions, payments, checkout, shipping and delivery (zones/rates), taxes, notifications (templates), domains, policies, billing/plan |
| Staff | Invite users, roles and permissions | Coarse Membership roles, no UI | Staff list, invite, role change, remove |

## 2. Architecture decisions

- **Stay server-rendered.** FastHTML remains the rendering layer. Interactivity comes from vanilla JS
  modules in `static/admin/` (no build step, no CDN at runtime; any third-party JS such as SortableJS
  is vendored under `static/vendor/` with its licence noted in `docs/THIRD_PARTY_NOTICES.md`).
- **One shell.** New `app/admin_ui.py` provides the shell and components (sidebar, page header,
  index table with tabs/filters/bulk bar/pagination, cards, badges, empty states, save bar, toasts,
  modals, resource pickers). `static/admin/admin.css` + `admin.js`. All `/admin/sites/{site_id}/...`
  merchant pages render through it. URL scheme is kept so links and tests survive.
- **Data model additions** via Alembic revisions, all tenant-scoped (and site-scoped where storefront
  owned): product status/vendor/tags/product type, `ProductImage`, `ProductOption` +
  `ProductOptionValue`, `Collection` + `CollectionProduct` + rules, `InventoryAdjustment`,
  `OrderNote`/timeline comments, order tags, `ReturnRequest` + lines, `DraftOrder`, customer tags,
  notes, addresses and consent, `Discount` (rule-based, codes/automatic, with usage records),
  `ShippingZone` + `ShippingRate`, `TaxRate`, `NotificationTemplate`, `StaffInvite`.
- **Commerce rules unchanged:** integer minor units, tenant scoping, idempotent checkout, PK-ordered
  stock locks, CSRF on every mutation, demo-simulator isolation, FastERP outbox boundary.
- **Publication compliance stays enforced:** publish-time compliance checks run for the visual editor too.

## 3. Visual editor design (Shopify theme editor + Elementor)

- Route `/admin/sites/{site_id}/editor?page={page_id}`: full-screen, three panes.
  - Top bar: exit, page switcher (all pages incl. product/blog templates), device toggle (desktop,
    tablet, mobile), undo/redo, preview, Save draft, Publish (with revisions dropdown).
  - Left rail tabs: **Sections** (tree: Header, page sections with nested blocks, Footer; drag handles,
    hide/show, duplicate, delete; "Add section" picker with search, categories and thumbnails),
    **Widgets** (Elementor-style grid of draggable elements), **Theme settings** (colours, fonts,
    buttons, layout width, custom CSS), **Navigator**/history.
  - Canvas: iframe preview of the real storefront renderer in editor mode. Hover outline + label;
    click selects (syncs tree and panel); drag widgets from the left rail or move sections inside the
    canvas with drop indicators; inline editing of text fields (`contenteditable` bound to
    `data-field` attributes); floating toolbar (move up/down, duplicate, hide, delete).
  - Right panel: settings for the selection, tabbed **Content / Style / Advanced**, generated from a
    per-block field schema: text, textarea, rich text, image (media library picker + upload), link
    picker (pages, products, collections, URL), select, colour, range, toggle, repeater (add, remove,
    drag-reorder items), product/collection pickers. Style: spacing, background, alignment, text
    colour, per-device visibility. Advanced: anchor id, CSS class.
- **Block schema registry** (`app/site_block_schema.py`): every registered block type (existing blocks
  and new generic ones) declares label, category, icon, fields, defaults and whether it accepts
  children. New generic widgets: section/container (columns, nested), heading, rich text, image,
  button, spacer, divider, video, icon list, image gallery, testimonial, product card, product grid,
  collection list, newsletter signup, FAQ/accordion, HTML/embed. Defaults make every inserted block
  valid immediately (fixes the "specialised section cannot be added" bug).
- **Persistence:** JSON API under `/admin/sites/{site_id}/editor/api/...` (session auth + CSRF header):
  load document + schema, render a block or page fragment for preview, save draft with optimistic
  version, publish, revisions list/restore. Document format stays `{version, blocks}` with optional
  `children` and `style` per block; validators extended, old documents still load.
- Undo/redo in the client (history stack), autosave of drafts, unsaved-change guard.

## 4. Delivery phases

| Phase | Scope | Notes |
|---|---|---|
| P1 Foundation | Admin shell, components, nav, Home dashboard, global search, Settings index skeleton; migrate existing site admin pages into the shell | Sequential, first |
| P2 Catalogue | Models + migration, products index, product editor, variants, media, collections, inventory, CSV | After P1 |
| P3 Orders and customers | Orders index/detail upgrades, notes/timeline/tags, line refunds + restock, returns, draft orders, abandoned checkouts, customers index/detail, CSV | After P2 |
| P4 Growth, content and settings | Discounts, subscriptions console, analytics, content lists, menu tree editor, files, settings sub-pages, staff | After P3 |
| P5 Visual editor | Block schema registry, generic widgets, nesting, editor API, editor client | Parallel worktree after P1, merged after P4 |
| P6 Verification | Full test run, Playwright desktop/mobile screenshots, merchant guide update | Last |

Each phase: tests added, `ruff`, `compileall`, `alembic upgrade head` on a disposable SQLite DB,
`pytest`, `git diff --check`, Playwright screenshots in `output/playwright/admin-v2/`, one or more
commits on the branch. No push or deploy without explicit authorisation.

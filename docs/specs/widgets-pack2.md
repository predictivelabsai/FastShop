# Interactive widget pack

Extend the existing merchant editor and merchant-themed storefront with tabs,
countdown, stats, progress bars, alert, table, map and social links. Preserve the
pack 1 registry, editor field and progressive enhancement patterns. This is an
extension of the existing Operate editor and merchant-controlled storefront.

References consulted: PRODUCT.md (merchant review and honest content), DESIGN.md
(preserve the established visual system), app/site_widgets_pack1.py,
static/widgets-pack1.css and static/editor/editor.js (theme inheritance, spacing,
SVG picker icons, repeaters and preview replacement). No new visual identity.

Implementation boundaries:

1. Registry definitions and validation in app/site_widgets_pack2_definitions.py,
   registered through app/site_widgets.py. Definitions remain the sole source of
   editor fields, defaults, descriptions, categories and item bounds.
2. Renderers in app/site_widgets_pack2.py and conditional assets in
   static/widgets-pack2.css/.js. Assets recursively detect container children;
   enhancement is idempotent when editor previews replace blocks.
3. Storefront asset integration in app/site_ui.py and picker SVGs in editor.js.
4. tests/test_site_widgets_pack2.py and isolated browser verification in
   scripts/verify_widgets_pack2_browser.py, with committed desktop/mobile images.

Countdowns use a fixed merchant-supplied date with an offset, or a local datetime
and validated IANA timezone. They never reset per visitor and are never evergreen.
The server computes the initial state; scripts update once per second while the
page is visible. Hidden units do not change the deadline. Ticking digits are not
live announcements. Stats default to unambiguous placeholders and count-up is off.

Maps accept only HTTPS Google Maps embed or OpenStreetMap export embed URLs.
No map frame or request is created until the visitor explicitly selects Load map.
Consent choices never trigger automatic loading. A normal maps link is the
no-JavaScript fallback. Alert dismissal is scoped to site and block, and storage
failure leaves the alert visible on load; dismissal still works for the current page.

Data model: tabs contain 2-8 label/body entries, stats contain 1-6
value/label/source entries, and progress bars contain 1-8 label/percentage entries.
Tables use a columns count (1-6), header_1 through header_6 and up to 50 row
entries with cell_1 through cell_6. Only the selected columns render. Captions,
striping and first-column row headers are optional. Social links contain up to
12 platform/URL/optional-label entries. Empty labels derive the platform name;
HTTPS is required, with mailto additionally permitted for email.

Tabs enhance from visible headed panels to a roving-tabindex WAI-ARIA tablist.
Vertical tabs become horizontal on narrow screens. Countdown digits have
aria-live off and a readable end-date/remaining-time summary. Hidden larger
units roll into the next displayed unit. Nonexistent IANA local times are
rejected; ambiguous local times use the first occurrence, so merchants can use
an explicit offset to select the second. Count-up overlays never remove the
final merchant value and are disabled under reduced motion.

Existing generation, refinement and LLM adapter behaviour is retained, with
compatibility tests for documents containing every widget. This does not add
new AI insertion operations. Registry definitions are split from rendering to
keep input validation independent of storefront imports. A shared recursive
asset predicate removes the small duplication between pack asset checks.

Acceptance: bounded registry validation, sanitised rich text, semantic tables,
keyboard tabs, accessible names and values, readable no-JavaScript output,
44px controls, reduced-motion behaviour, theme variables, container support and
no page overflow at 360px. CSRF-protected draft/publish round trip and AI document
compatibility tests. Run isolated SQLite ruff, compileall, Alembic and full pytest,
browser journey with remote map requests blocked, and git diff --check.

Out of scope: migrations, expanded AI insertion features, deployment, PR creation,
other widget packs and the two existing untracked screenshot directories.

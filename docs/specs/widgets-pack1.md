# Widget registry and layout/media pack

Extend the merchant editor's established interface and merchant-themed storefront
with eight widgets, without changing existing document rendering or schema fields.

The registry owns labels, categories, insertion defaults, field constraints and
repeater schemas. Validation and editor metadata consume those definitions.
Existing legacy section validation, sanitisation, tenant ownership and CSRF remain.

`WIDGETS` maps names to `WidgetDefinition(label, category, defaults, fields,
item_schema)`. Each field declares its kind and constraints, such as integer
min/max/unit, choice options or text length. An item schema declares its own
fields, insertion defaults and entry bounds. Legacy schemas retain their historical
allowed fields separately from their visible editor fields. The tuple access used
by existing trusted extensions remains supported.

The pack's data and renderers live in `site_widgets_pack1.py`, registered once by
`site_widgets.py`. Existing widget CSS is unchanged. New CSS and interactive
JavaScript load conditionally, including widgets nested inside containers. The
shared `site_ui.render_section` dispatch handles the core storefront and preview;
optional external theme adapters continue to use their existing registration API.

Implementation tasks:

1. Registry foundation: `app/site_widgets.py`, `app/site_blocks.py`,
   `app/site_block_schema.py`.
2. Layout/media definitions, rendering and progressive enhancement:
   `app/site_widgets_pack1.py`, `static/widgets-pack1.css`,
   `static/widgets-pack1.js`.
3. Integration: storefront assets and canvas controls; picker icons and repeater
   limits in the existing editor; no new AI insertion capabilities.
4. Focused tests and isolated browser journey with committed screenshots.

Widgets: slideshow, image banner, before/after, logo cloud, icon box,
image hotspots, section heading and privacy-enhanced video embed.
All may be nested in containers, subject to existing depth and node limits.

References consulted: `PRODUCT.md` establishes merchant users, reviewed publishing
and honest placeholders. `DESIGN.md` requires preserving storefront theme inheritance
and existing editor chrome. `site_widgets.py`, `editor.js`, `canvas.js` and the
existing editor browser script establish spacing, SVG picker icons, repeater
interactions and in-place preview replacement. These are read-only design references.

Acceptance: existing tests unchanged, focused validation/render/schema/CSRF tests;
isolated SQLite ruff, compileall, Alembic and complete pytest checks; diff whitespace
check; desktop/mobile browser journey without errors or overflow, including real
picker drag, interaction screenshots and successful save/publish.

Out of scope: migrations, AI feature expansion, canvas click-to-place hotspots,
other widget packs, deployment, PR creation and unrelated screenshot directories.

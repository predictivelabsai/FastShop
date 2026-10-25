# Optional theme pack integration

FastShop's core registry includes only its generic blocks and widgets. The
application does not import or seed any optional customer pack. A deploying
repository owns its pack modules, assets, seed data and startup import.

Before importing `app.main`, the deploy entry point imports its own registration
module. That module imports `app.site_block_schema` first so the core schemas are
initialised, then registers each additional block with
`app.site_blocks.register_block_type(kind, BlockSpec(label, required_item_fields),
validator=validate)`. The validator receives the normalised block after the common
field, URL, nesting and item validation. It raises `CommerceError` on invalid data.

Next call `app.site_block_schema.register_block_schema(kind, schema)` with the
pack's complete editor metadata, groups, fields and valid defaults. Metadata is
copied and the defaults are validated. The classical editor's section labels are
updated too. Core block names cannot be replaced. Existing specialised pack
validators and defaults belong in the pack module, rather than the core registry.

Call `app.site_theme.register_theme_pack(name, storefront=render_storefront,
block=render_block, commerce=render_commerce)` for the pack's rendering adapters.
The storefront adapter accepts the same positional arguments as
`site_ui.storefront`, plus `preview`, `editor` and `message`. The block adapter
accepts `(db, site, page, config, block, base, csrf, preview=True)`. The optional
commerce adapter accepts `(site, request, title, *children)` and wraps cart,
checkout, account and subscription pages. Adapters can wrap existing pack
functions to match these signatures.

The site selects a registered pack through its `theme_pack` setting. Unregistered
packs use the default storefront. Register once during startup, before accepting
requests. The deploying repository should also retain its own specialised cart
presentation and asset enhancement tests. Customer pack assets and bootstrap
imports remain local to that repository. After core schema preparation, the deploy
entry point can run its own idempotent seed in an explicit database transaction.

Alembic revisions 0029 to 0034 have identical filenames and identifiers across
the product and deploy repositories. Do not renumber them when merging the core
work into the deploy repository.

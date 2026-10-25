"""Merchant catalogue, options, collections and inventory audit history."""
from html import escape

import sqlalchemy as sa
from alembic import op

from app.models import (
    CatalogueSettings,
    Collection,
    CollectionProduct,
    InventoryAdjustment,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductTag,
    ProductVariant,
)

revision = '20261025_0030'
down_revision = '20261011_0029'
branch_labels = None
depends_on = None

PRODUCT_COLUMNS = (
    'status', 'vendor', 'product_type', 'tags_json', 'description_html', 'short_description',
    'template', 'published_at', 'requires_shipping', 'weight_grams', 'dimensions_json',
    'country_of_origin', 'hs_code', 'charge_tax', 'tax_code', 'version',
)
VARIANT_COLUMNS = ('barcode', 'track_inventory', 'continue_selling_when_out_of_stock', 'image_id')
TABLES = (ProductImage, ProductOption, ProductOptionValue, ProductTag, Collection,
          CollectionProduct, InventoryAdjustment, CatalogueSettings)


def upgrade():
    bind = op.get_bind()
    # The repository's initial revision imports current metadata on fresh installs.
    # Check column presence so both existing installations and fresh installs work.
    for model in TABLES:
        model.__table__.create(bind, checkfirst=True)
    if 'collection_id' not in {c['name'] for c in sa.inspect(bind).get_columns('site_pages')}:
        with op.batch_alter_table('site_pages') as batch:
            batch.add_column(sa.Column('collection_id', sa.String(32), nullable=True))
            batch.create_foreign_key('fk_site_page_collection', 'collections',
                                     ['collection_id'], ['id'], ondelete='SET NULL')
            batch.create_index('ix_site_pages_collection_id', ['collection_id'])
    for model, names in ((Product, PRODUCT_COLUMNS), (ProductVariant, VARIANT_COLUMNS)):
        present = {c['name'] for c in sa.inspect(bind).get_columns(model.__tablename__)}
        missing = [name for name in names if name not in present]
        if missing:
            with op.batch_alter_table(model.__tablename__) as batch:
                for name in missing:
                    column = model.__table__.c[name]._copy()
                    # Batch copies must not create indexes named after SQLite's
                    # temporary table. Stable model indexes are created below.
                    column.index = False
                    if name == 'image_id':
                        column.foreign_keys.clear()
                    batch.add_column(column)
                if 'image_id' in missing:
                    batch.create_foreign_key('fk_catalog_variant_image', 'product_images',
                                             ['image_id'], ['id'], ondelete='SET NULL')
        for index in model.__table__.indexes:
            if any(c.name in names for c in index.columns):
                index.create(bind, checkfirst=True)
    op.execute(sa.text("UPDATE products SET status = CASE WHEN is_published THEN 'active' "
                       "ELSE 'draft' END"))
    for row in bind.execute(sa.text("SELECT id, description FROM products WHERE description_html = ''")):
        bind.execute(sa.text('UPDATE products SET description_html=:html WHERE id=:id'),
                     {'id': row.id, 'html': escape(row.description or '')})
    # Existing single-image products keep their original URL and gain a gallery row.
    for row in bind.execute(sa.text('SELECT id, tenant_id, image_url FROM products '
                                    "WHERE image_url <> ''")):
        exists = bind.execute(sa.text('SELECT id FROM product_images WHERE product_id=:id'),
                              {'id': row.id}).first()
        if not exists:
            from app.models import new_id
            bind.execute(ProductImage.__table__.insert().values(
                id=new_id(), product_id=row.id, tenant_id=row.tenant_id,
                image_url=row.image_url, alt='', position=0))


def downgrade():
    bind = op.get_bind()
    with op.batch_alter_table('site_pages') as batch:
        for fk in sa.inspect(bind).get_foreign_keys('site_pages'):
            if 'collection_id' in fk['constrained_columns'] and fk.get('name'):
                batch.drop_constraint(fk['name'], type_='foreignkey')
        batch.drop_index('ix_site_pages_collection_id')
        batch.drop_column('collection_id')
    for model, names in ((ProductVariant, VARIANT_COLUMNS), (Product, PRODUCT_COLUMNS)):
        present = {c['name'] for c in sa.inspect(bind).get_columns(model.__tablename__)}
        with op.batch_alter_table(model.__tablename__) as batch:
            for fk in sa.inspect(bind).get_foreign_keys(model.__tablename__):
                if 'image_id' in fk['constrained_columns'] and fk.get('name'):
                    batch.drop_constraint(fk['name'], type_='foreignkey')
            for index in sa.inspect(bind).get_indexes(model.__tablename__):
                if any(name in names for name in index['column_names']):
                    batch.drop_index(index['name'])
            for name in reversed(names):
                if name in present:
                    batch.drop_column(name)
    for model in reversed(TABLES):
        model.__table__.drop(bind, checkfirst=True)

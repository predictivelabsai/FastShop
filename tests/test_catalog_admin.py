from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app import catalog_admin as catalog
from app.models import (
    Base,
    Channel,
    InventoryAdjustment,
    Product,
    ProductVariant,
    Stock,
    Tenant,
    VariantChannelListing,
    Warehouse,
)
from app.services import CommerceError


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    for suffix in ('a', 'b'):
        tenant = Tenant(id=suffix, slug=suffix, name=suffix)
        session.add(tenant)
        session.flush()
        channel = Channel(id=suffix, tenant_id=suffix, slug=suffix, name=suffix, currency='EUR')
        session.add(channel)
        session.add(Warehouse(id=suffix, tenant_id=suffix, code=suffix, name=suffix))
    session.commit()
    yield session
    session.close()


def site(tenant='a'):
    return SimpleNamespace(tenant_id=tenant, channel_id=tenant, id=tenant)


def product(db, **data):
    return catalog.create_product(db, site(), {'name': 'Test product', **data})


def test_matrix_preserves_variant_prices_stock_sku_and_reactivates(db):
    p = product(db, options=[{'name': 'Size', 'values': ['S', 'M']}])
    small = p.variants[0]
    catalog.update_product(db, site(), p.id, {'variants': [{
        'id': small.id, 'sku': 'S-1', 'price_minor': 1234, 'cost_minor': 500,
        'quantities': {'a': 7},
    }]}, expected_version=p.version)
    catalog.regenerate_variants(db, site(), p, [{'name': 'Size', 'values': ['S', 'L']}])
    assert next(v for v in p.variants if v.is_active and v.attributes_json == {'Size': 'S'}).id == small.id
    assert small.sku == 'S-1'
    assert db.scalar(select(VariantChannelListing.price_minor).where(
        VariantChannelListing.variant_id == small.id)) == 1234
    assert db.scalar(select(Stock.quantity).where(Stock.variant_id == small.id)) == 7
    catalog.regenerate_variants(db, site(), p, [{'name': 'Size', 'values': ['M']}])
    assert len([v for v in p.variants if v.is_active]) == 1


def test_options_limits_and_optimistic_versions(db):
    p = product(db)
    catalog.update_product(db, site(), p.id, {'name': 'Changed'}, expected_version=1)
    with pytest.raises(CommerceError, match='changed'):
        catalog.update_product(db, site(), p.id, {'name': 'Stale'}, expected_version=1)
    with pytest.raises(CommerceError, match='100 variants'):
        catalog.regenerate_variants(db, site(), p, [
            {'name': 'One', 'values': [str(i) for i in range(11)]},
            {'name': 'Two', 'values': [str(i) for i in range(10)]},
        ])


def test_bulk_validates_every_tenant_before_mutating(db):
    p = product(db)
    foreign = catalog.create_product(db, site('b'), {'name': 'Foreign'})
    with pytest.raises(CommerceError, match='not found'):
        catalog.bulk_products(db, site(), [p.id, foreign.id], 'status', value='active')
    assert p.status == 'draft'
    catalog.bulk_products(db, site(), [p.id], 'tags_add', value='new, sale')
    assert p.tags_json == ['new', 'sale']
    catalog.bulk_products(db, site(), [p.id], 'tags_remove', value='new')
    assert p.tags_json == ['sale']
    catalog.bulk_products(db, site(), [p.id], 'status', value='active')
    assert p.status == 'active' and p.is_published


def test_inventory_history_reasons_and_lock_query_order(db):
    p = product(db)
    variant = p.variants[0]
    statements = []
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', record)
    catalog.adjust_inventory(db, site(), variant.id, 'a', 10, reason='received', note='Delivery')
    catalog.adjust_inventory(db, site(), variant.id, 'a', -2, reason='damaged')
    adjustment = catalog.adjust_inventory(db, site(), variant.id, 'a', 5, mode='set')
    assert adjustment.delta == -3 and adjustment.resulting_quantity == 5
    assert db.scalar(select(func.count()).select_from(InventoryAdjustment)) == 3
    assert any('ORDER BY stocks.id' in statement for statement in statements)
    with pytest.raises(CommerceError):
        catalog.adjust_inventory(db, site(), variant.id, 'b', 1)
    with pytest.raises(CommerceError):
        catalog.adjust_inventory(db, site(), variant.id, 'a', -99)
    event.remove(db.bind, 'before_cursor_execute', record)


def test_automated_collections_all_any_and_manual_order(db):
    p = product(db, tags=['sale'], vendor='Acme', product_type='Shoes',
                variants=[{'price_minor': 1500}])
    other = product(db, name='Other', handle='other', vendor='Other')
    collection = catalog.save_collection(db, site(), {
        'title': 'Sale', 'kind': 'automated', 'rules_json': {'match': 'all', 'rules': [
            {'field': 'tag', 'value': 'sale'}, {'field': 'price', 'operator': 'gt', 'value': 1000},
        ]},
    })
    assert [x.id for x in catalog.collection_products(db, site(), collection)] == [p.id]
    catalog.save_collection(db, site(), {'rules_json': {'match': 'any', 'rules': [
        {'field': 'vendor', 'value': 'Other'}, {'field': 'title', 'operator': 'contains', 'value': 'test'},
    ]}}, collection_id=collection.id, expected_version=collection.version)
    assert len(catalog.collection_products(db, site(), collection)) == 2
    manual = catalog.save_collection(db, site(), {'title': 'Manual', 'product_ids': [other.id, p.id]})
    assert [x.id for x in catalog.collection_products(db, site(), manual)] == [other.id, p.id]


def test_csv_round_trip_dry_run_idempotent_and_row_errors(db):
    p = product(db, options=[{'name': 'Size', 'values': ['S', 'M']}],
                description_html='<p>Text <strong>bold</strong></p>', tags=['sale'],
                variants=[{'price_minor': 1234}, {'price_minor': 2345}])
    before_ids = [v.id for v in p.variants]
    text = catalog.export_csv(db, site(), [p])
    preview = catalog.import_csv(db, site(), text)
    assert preview['errors'] == [] and preview['updated'] == 1
    assert catalog.get_product(db, site(), p.id).version == 1
    imported = catalog.import_csv(db, site(), text, dry_run=False)
    assert imported['errors'] == [] and imported['updated'] == 1
    assert [v.id for v in p.variants] == before_ids
    assert db.scalar(select(func.count()).select_from(Product)) == 1
    invalid = text.replace('12.34', 'bad')
    errors = catalog.import_csv(db, site(), invalid, dry_run=False)['errors']
    assert errors[0]['row'] == 2


def test_sanitiser_removes_executable_markup_preserves_formatting(db):
    p = product(db, description_html='<p onclick="x()">Hello <b>bold</b></p>'
        '<script>alert(1)</script><a href="javascript:alert(1)">bad</a>'
        '<a href="https://example.com">good</a><img src=x onerror=x()>')
    assert p.description_html == '<p>Hello <b>bold</b></p><a>bad</a><a href="https://example.com">good</a>'


def test_duplicate_keeps_prices_but_new_sku_and_no_stock(db):
    p = product(db, variants=[{'price_minor': 2200, 'sku': 'original', 'quantities': {'a': 5}}])
    copy = catalog.duplicate_product(db, site(), p.id)
    assert copy.status == 'draft' and copy.variants[0].sku != 'original'
    assert db.scalar(select(VariantChannelListing.price_minor).where(
        VariantChannelListing.variant_id == copy.variants[0].id)) == 2200
    assert catalog.inventory_quantity(db, site(), copy) == 0


def test_rename_option_preserves_variant_and_legacy_csv_preserves_skus(db):
    p = product(db, options=[{'name': 'Size', 'values': ['S']}])
    variant_id = p.variants[0].id
    catalog.regenerate_variants(db, site(), p, [{'name': 'Fit', 'values': ['S']}])
    assert p.variants[0].id == variant_id and p.variants[0].attributes_json == {'Fit': 'S'}
    legacy = product(db, name='Legacy', handle='legacy')
    legacy.variants[0].attributes_json = {'minimum_quantity': 3, 'subscription': True,
                                        'metadata': 'x', 'fourth': 'x'}
    other = ProductVariant(tenant_id='a', product_id=legacy.id, sku='legacy-second',
                           name='Second', attributes_json={'minimum_quantity': 4})
    db.add(other)
    db.flush()
    db.expire(legacy, ['variants'])
    ids = {v.id for v in legacy.variants}
    csv_text = catalog.export_csv(db, site(), [legacy])
    result = catalog.import_csv(db, site(), csv_text, dry_run=False)
    assert result['errors'] == []
    assert {v.id for v in legacy.variants if v.is_active} == ids


def test_model_status_compatibility_and_product_page_association(db):
    from app.models import SitePage
    p = product(db, status='active')
    page = db.scalar(select(SitePage).where(SitePage.product_id == p.id))
    assert page and page.published_json is not None
    p.status = 'archived'
    assert not p.is_published
    draft = Product(tenant_id='a', name='Draft', slug='draft',
                    category_id=p.category_id, product_type_id=p.product_type_id)
    db.add(draft)
    db.flush()
    assert draft.status == 'draft'
    published = Product(tenant_id='a', name='Published', slug='published', is_published=True,
                        category_id=p.category_id, product_type_id=p.product_type_id)
    db.add(published)
    db.flush()
    assert published.status == 'active'


def test_delete_cleans_associations_and_requires_current_version(db):
    p = product(db, tags=['sale'], variants=[{'price_minor': 100}])
    catalog.save_collection(db, site(), {'title': 'Manual', 'product_ids': [p.id]})
    with pytest.raises(CommerceError, match='changed'):
        catalog.delete_product(db, site(), p.id, expected_version=0)
    catalog.delete_product(db, site(), p.id, expected_version=p.version)
    assert db.get(Product, p.id) is None


def test_failed_bulk_and_update_are_atomic_if_caller_commits(db):
    first = product(db, variants=[{'price_minor': 100}])
    second = product(db, name='Second', handle='second', variants=[{'price_minor': 10}])
    db.commit()
    with pytest.raises(CommerceError):
        catalog.bulk_products(db, site(), [first.id, second.id], 'price_adjust', value=-50)
    db.commit()
    assert db.scalar(select(VariantChannelListing.price_minor).where(
        VariantChannelListing.variant_id == first.variants[0].id)) == 100
    assert first.version == 1
    with pytest.raises(CommerceError):
        catalog.update_product(db, site(), first.id, {'name': 'Partial', 'variants': [
            {'id': first.variants[0].id, 'price_minor': -1},
        ]}, expected_version=first.version)
    db.commit()
    assert first.name == 'Test product' and first.version == 1


def test_csv_upserts_by_sku_after_handle_change_and_clears_metadata(db):
    p = product(db, vendor='Vendor', tags=['sale'], seo_title='Old SEO',
                options=[{'name': 'Size', 'values': ['S', 'L']}],
                variants=[{'sku': 'small', 'price_minor': 110}, {'sku': 'large', 'price_minor': 220}])
    p.variants[0].attributes_json = {**p.variants[0].attributes_json, 'legacy': True}
    text = catalog.export_csv(db, site(), [p]).replace('test-product,', 'renamed,')
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(text)))
    for row in rows:
        row['Vendor'], row['Tags'], row['SEO Title'] = '', '', ''
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=catalog.CSV_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    result = catalog.import_csv(db, site(), output.getvalue(), dry_run=False)
    assert result['errors'] == [] and result['updated'] == 1 and result['created'] == 0
    assert p.slug == 'renamed' and p.vendor == '' and p.tags_json == [] and p.seo_title == ''
    assert p.variants[0].attributes_json['legacy'] is True
    assert db.scalar(select(func.count()).select_from(Product)) == 1


def test_page_handle_title_and_seo_change_preserve_custom_blocks(db):
    from app.models import SitePage
    p = product(db, status='active')
    page = db.scalar(select(SitePage).where(SitePage.product_id == p.id))
    document = dict(page.draft_json)
    document['blocks'] = [dict(document['blocks'][0], heading='Custom merchant content')]
    page.draft_json = document
    page.published_json = document
    catalog.update_product(db, site(), p.id, {
        'name': 'New title', 'handle': 'new-handle', 'seo_title': 'Search title',
        'seo_description': 'Search description',
    }, expected_version=p.version)
    assert page.path == '/products/new-handle' and page.title == 'New title'
    assert page.draft_json['title'] == 'Search title'
    assert page.published_json['description'] == 'Search description'
    assert page.draft_json['blocks'][0]['heading'] == 'Custom merchant content'


def test_multi_variant_edit_prelocks_global_stock_order(db):
    p = product(db, options=[{'name': 'Size', 'values': ['S', 'L']}])
    for variant in p.variants:
        catalog.adjust_inventory(db, site(), variant.id, 'a', 1)
    statements = []
    def record(conn, cursor, statement, parameters, context, many):
        if statement.startswith('SELECT stocks.'):
            statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', record)
    catalog.update_product(db, site(), p.id, {'variants': [
        {'id': v.id, 'quantities': {'a': 2}} for v in reversed(p.variants)
    ]}, expected_version=p.version)
    event.remove(db.bind, 'before_cursor_execute', record)
    assert 'JOIN product_variants' in statements[0] and 'ORDER BY stocks.id' in statements[0]


def test_bulk_collection_append_trimmed_tags_and_validation_lengths(db):
    p = product(db, tags=['new', 'sale'])
    second = product(db, name='Second', handle='second')
    collection = catalog.save_collection(db, site(), {'title': 'Manual', 'product_ids': [p.id]})
    catalog.bulk_products(db, site(), [second.id], 'add_to_collection', value=collection.id)
    assert [x.id for x in catalog.collection_products(db, site(), collection)] == [p.id, second.id]
    catalog.bulk_products(db, site(), [p.id], 'tags_remove', value='new, sale')
    assert p.tags_json == []
    with pytest.raises(CommerceError, match='at most 200'):
        catalog.update_product(db, site(), p.id, {'vendor': 'x' * 201}, expected_version=p.version)
    with pytest.raises(CommerceError, match='at most 100'):
        catalog.update_product(db, site(), p.id, {'variants': [
            {'id': p.variants[0].id, 'barcode': 'x' * 101},
        ]}, expected_version=p.version)
    assert catalog._csv_decimal(999999999) == '9999999.99'


def test_successful_command_obeys_callers_outer_rollback_on_sqlite(db):
    p = product(db)
    product_id = p.id
    db.rollback()
    assert db.get(Product, product_id) is None


def test_legacy_multi_variant_edit_and_duplicate_preserve_catalogue_data(db):
    p = product(db, image_url='https://example.com/legacy.png')
    first = p.variants[0]
    first.name, first.attributes_json = 'Small bundle', {'metadata': {'units': 2}}
    second = ProductVariant(tenant_id='a', product_id=p.id, sku='legacy-large',
                            name='Large bundle', attributes_json={'units': 4}, barcode='12345678')
    db.add(second)
    db.flush()
    db.add(VariantChannelListing(variant_id=second.id, channel_id='a', currency='EUR', price_minor=450))
    db.flush()
    db.expire(p, ['variants'])
    before_ids = {v.id for v in p.variants}
    catalog.update_product(db, site(), p.id, {'options': [], 'vendor': 'Supplier'},
                           expected_version=p.version)
    assert {v.id for v in p.variants if v.is_active} == before_ids
    assert first.name == 'Small bundle'
    duplicate = catalog.duplicate_product(db, site(), p.id)
    assert len(duplicate.variants) == 2
    assert {v.name for v in duplicate.variants} == {'Small bundle', 'Large bundle'}
    assert duplicate.variants[0].attributes_json == {'metadata': {'units': 2}}
    copied = next(v for v in duplicate.variants if v.name == 'Large bundle')
    assert copied.barcode == '12345678' and copied.sku != 'legacy-large'
    assert db.scalar(select(VariantChannelListing.price_minor).where(
        VariantChannelListing.variant_id == copied.id)) == 450
    assert duplicate.image_url == p.image_url

"""Commerce widgets use real, scoped resources and honest merchant copy."""

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fasthtml.common import to_xml
from sqlalchemy import func, select

from app import content
from app.integrations.site_builder_llm import guided
from app.models import (
    Collection,
    CollectionProduct,
    Discount,
    DiscountCode,
    Review,
    Site,
    SitePage,
    VariantChannelListing,
)
from app.services import CommerceError
from app.site_block_schema import default_block, editor_schema
from app.site_blocks import normalize_document, validate_block
from app.site_catalog import create_catalog_product
from app.site_generation import MerchantBrief, guided_plan, validate_plan
from app.site_refinement import apply_operation, derive_operations
from app.site_ui import render_section, storefront
from app.site_widgets import WIDGETS
from tests.test_site_editor_blocks import context as context
from tests.test_site_editor_routes import load
from tests.test_site_editor_routes import workspace as workspace

PACK = ('featured_collection', 'pricing_table', 'comparison_table', 'trust_badges',
        'announcement_bar', 'promo_banner', 'steps', 'star_rating', 'contact_card')


def document(*blocks):
    return {'version': 1, 'title': 'Your page', 'blocks': list(blocks)}


def render(context, block, *, preview=True):
    db, site, page = context
    return to_xml(render_section(db, site, page, {}, block, '', 'csrf', preview))


def seed_catalog(db, site, suffix='own'):
    product = create_catalog_product(db, site, {
        'name': 'Real product ' + suffix, 'slug': 'real-product-' + suffix,
        'subtitle': '', 'description': 'Merchant product description', 'image_url': '',
        'category_slug': 'catalogue', 'category_name': 'Catalogue',
        'variants': [{'name': 'Standard', 'price_minor': 1999}],
    })
    product_page = content.create_page(db, site, product.name, '/products/' + product.slug, 'product')
    product_page.product_id = product.id
    product_page.published_json = copy.deepcopy(product_page.draft_json)
    collection = Collection(tenant_id=site.tenant_id, title='Real collection ' + suffix,
                            handle='real-collection-' + suffix, description='Collection description')
    db.add(collection)
    db.flush()
    db.add(CollectionProduct(tenant_id=site.tenant_id, collection_id=collection.id, product_id=product.id))
    collection_page = content.create_page(db, site, collection.title, '/collections/' + collection.handle, 'collection')
    collection_page.collection_id = collection.id
    collection_page.published_json = copy.deepcopy(collection_page.draft_json)
    discount = Discount(tenant_id=site.tenant_id, site_id=site.id, title='Merchant promotion', value=10)
    db.add(discount)
    db.flush()
    code = DiscountCode(tenant_id=site.tenant_id, site_id=site.id, discount_id=discount.id, code='CODE' + suffix.upper())
    db.add(code)
    db.flush()
    return SimpleNamespace(product=product, product_page=product_page, collection=collection,
                           collection_page=collection_page, discount=discount, code=code)


def add_review(db, site, product, rating, approved=True):
    row = Review(tenant_id=site.tenant_id, product_id=product.id, author_name='Customer',
                 title='Customer feedback', rating=rating, is_approved=approved)
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize('kind', PACK)
def test_defaults_validate_render_and_inline_hooks(context, kind):
    block = default_block(kind)
    assert validate_block(copy.deepcopy(block)) == block
    assert normalize_document(document(block))['blocks'] == [block]
    markup = render(context, block)
    assert 'fs-widget-' + kind in markup
    assert f'data-block-id="{block["id"]}"' in markup
    if kind not in {'featured_collection', 'star_rating'}:
        assert 'data-field="' in markup


@pytest.mark.parametrize(('kind', 'patch'), [
    ('featured_collection', {'limit': 0}), ('featured_collection', {'limit': 13}),
    ('featured_collection', {'limit': True}), ('featured_collection', {'columns': 5}),
    ('featured_collection', {'layout': 'carousel'}), ('featured_collection', {'show_image': 1}),
    ('comparison_table', {'options': 0}), ('comparison_table', {'options': 5}),
    ('trust_badges', {'layout': 'carousel'}), ('announcement_bar', {'interval': 2}),
    ('announcement_bar', {'interval': 16}), ('announcement_bar', {'dismissible': 'true'}),
    ('announcement_bar', {'scheme': 'danger'}), ('star_rating', {'scope': 'invented'}),
    ('star_rating', {'rating': 5}), ('contact_card', {'phone': 'call me'}),
    ('contact_card', {'phone': '+372 5555<script>'}), ('contact_card', {'email': 'not-an-email'}),
    ('contact_card', {'email': 'hello@example.test\r\nBcc:other@example.test'}),
])
def test_ranges_choices_and_contact_validation(kind, patch):
    with pytest.raises(CommerceError):
        validate_block(default_block(kind) | patch)


@pytest.mark.parametrize(('kind', 'minimum', 'maximum'), [
    ('pricing_table', 1, 4), ('comparison_table', 1, 20), ('trust_badges', 2, 6),
    ('announcement_bar', 1, 3), ('steps', 2, 8), ('contact_card', 0, 7),
])
def test_item_limits_and_unknown_fields(kind, minimum, maximum):
    block = default_block(kind)
    item = block['items'][0] if block['items'] else {'day': 'Monday', 'hours': 'Your hours'}
    for size in {minimum, maximum}:
        assert validate_block(block | {'items': [copy.deepcopy(item) for _ in range(size)]})
    for items in ([item] * (maximum + 1), [item | {'unexpected': 'value'}]):
        with pytest.raises(CommerceError):
            validate_block(block | {'items': items})
    if minimum:
        with pytest.raises(CommerceError):
            validate_block(block | {'items': [item] * (minimum - 1)})


def test_pricing_features_and_highlight_validation(context):
    block = default_block('pricing_table')
    item = block['items'][0] | {'price': 'EUR 19 / month', 'highlighted': True, 'badge': 'Your chosen plan'}
    markup = render(context, validate_block(block | {'items': [item]}))
    assert 'EUR 19 / month' in markup and 'Your chosen plan' in markup
    for patch in ({'features': '\n'.join('Feature' for _ in range(13))}, {'highlighted': 1}):
        with pytest.raises(CommerceError):
            validate_block(block | {'items': [item | patch]})


@pytest.mark.parametrize('url', ['http://example.test', '//example.test', 'javascript:alert(1)',
                                  'https://user:password@example.test'])
def test_contact_map_requires_safe_https(url):
    with pytest.raises(CommerceError):
        validate_block(default_block('contact_card') | {'map_link': url})


@pytest.mark.parametrize('url', ['http://example.test', '//example.test', 'javascript:alert(1)',
                                  'https://user:password@example.test'])
@pytest.mark.parametrize('kind', ['pricing_table', 'announcement_bar', 'promo_banner', 'star_rating'])
def test_marketing_links_reject_unsafe_and_non_https_urls(kind, url):
    block = default_block(kind)
    if kind in {'pricing_table', 'announcement_bar'}:
        block['items'][0]['link'] = url
    else:
        block['link'] = url
    with pytest.raises(CommerceError):
        validate_block(block)


@pytest.mark.parametrize('kind', ['trust_badges', 'steps'])
def test_icons_must_come_from_the_owned_icon_set(kind):
    block = default_block(kind)
    block['items'][0]['icon'] = '<svg onload="alert(1)">'
    with pytest.raises(CommerceError):
        validate_block(block)


def test_contact_address_links_and_ordered_steps(context):
    markup = render(context, validate_block(default_block('contact_card') | {
        'address': 'Your street\nYour town', 'phone': '+372 5555 1234',
        'email': 'hello@example.test', 'map_link': 'https://example.test/map'}))
    assert '<address' in markup and 'href="tel:' in markup
    assert 'href="mailto:hello@example.test"' in markup and 'https://example.test/map' in markup
    assert '<ol' in render(context, default_block('steps'))


def test_comparison_has_accessible_table_and_escaped_text(context):
    block = default_block('comparison_table') | {'options': 3, 'items': [
        {'feature': 'Your feature', 'cell_1': 'yes', 'cell_2': 'no', 'cell_3': '<script>unsafe</script>', 'cell_4': ''}]}
    markup = render(context, validate_block(block))
    for expected in ('<table', '<caption', 'scope="row"', 'scope="col"', 'Included', 'Not included'):
        assert expected in markup
    assert '<script' not in markup and '&lt;script&gt;' in markup


def test_featured_collection_uses_real_minor_unit_prices_and_public_products(context):
    db, site, page = context
    catalogue = seed_catalog(db, site)
    block = default_block('featured_collection') | {'collection': catalogue.collection.id}
    markup = render(context, block, preview=False)
    assert catalogue.collection.title in markup and catalogue.product.name in markup
    assert '19.99' in markup and '1999.00' not in markup
    assert 'View all' in markup and catalogue.collection_page.path in markup
    catalogue.product.is_published = False
    db.flush()
    assert catalogue.product.name not in render(context, block, preview=False)
    catalogue.collection.is_published = False
    db.flush()
    assert catalogue.collection.title not in render(context, block, preview=False)


def test_collection_and_rating_exclude_products_without_this_channel_listing(context):
    db, site, page = context
    catalogue = seed_catalog(db, site)
    add_review(db, site, catalogue.product, 5)
    listing = db.scalar(select(VariantChannelListing).where(
        VariantChannelListing.variant_id == catalogue.product.variants[0].id,
        VariantChannelListing.channel_id == site.channel_id))
    db.delete(listing)
    db.flush()
    featured = default_block('featured_collection') | {'collection': catalogue.collection.id}
    assert catalogue.product.name not in render(context, featured, preview=False)
    assert not render(context, default_block('star_rating'), preview=False).strip()


def test_star_rating_only_approved_real_reviews_and_no_manual_rating(context):
    db, site, page = context
    catalogue = seed_catalog(db, site)
    block = default_block('star_rating') | {'scope': 'product', 'product': catalogue.product.id}
    assert not render(context, block, preview=False).strip()
    assert 'review' in render(context, block).lower()
    add_review(db, site, catalogue.product, 1, approved=False)
    assert not render(context, block, preview=False).strip()
    add_review(db, site, catalogue.product, 4)
    add_review(db, site, catalogue.product, 5)
    for values in (block, default_block('star_rating')):
        assert 'Rated 4.5 out of 5 from 2 reviews' in render(context, values, preview=False)


@pytest.mark.parametrize('state', ['inactive', 'expired', 'future', 'deleted', 'exhausted'])
def test_promo_hides_codes_that_are_no_longer_active(context, state):
    db, site, page = context
    catalogue = seed_catalog(db, site)
    block = default_block('promo_banner') | {'discount_code': catalogue.code.id}
    markup = render(context, block, preview=False)
    assert catalogue.code.code in markup and 'Copy code' in markup and 'aria-live="polite"' in markup
    now = datetime.now(UTC)
    if state == 'inactive':
        catalogue.discount.is_active = False
    elif state == 'expired':
        catalogue.discount.ends_at = now - timedelta(days=1)
    elif state == 'future':
        catalogue.discount.starts_at = now + timedelta(days=1)
    elif state == 'deleted':
        catalogue.discount.deleted_at = now
    else:
        catalogue.discount.usage_limit = 0
    db.flush()
    assert catalogue.code.code not in render(context, block, preview=False)


def test_editor_only_offers_current_codes_without_writing_discounts(workspace):
    w = workspace
    load(w)
    with w.sessions() as db:
        own = seed_catalog(db, w.site)
        inactive = Discount(tenant_id=w.site.tenant_id, site_id=w.site.id,
                            title='Inactive promotion', value=10, is_active=False)
        expired = Discount(tenant_id=w.site.tenant_id, site_id=w.site.id,
                           title='Expired promotion', value=10, ends_at=datetime.now(UTC) - timedelta(days=1))
        future = Discount(tenant_id=w.site.tenant_id, site_id=w.site.id,
                          title='Future promotion', value=10, starts_at=datetime.now(UTC) + timedelta(days=1))
        db.add_all([inactive, expired, future])
        db.flush()
        hidden = []
        for index, discount in enumerate((inactive, expired, future)):
            code = DiscountCode(tenant_id=w.site.tenant_id, site_id=w.site.id,
                                discount_id=discount.id, code=f'HIDDEN{index}')
            db.add(code)
            hidden.append(code)
        db.commit()
        before = {model: db.scalar(select(func.count()).select_from(model)) for model in (Discount, DiscountCode)}
        resources = w.client.get(w.base + '/resources').json()
        assert own.code.id in str(resources)
        assert all(code.id not in str(resources) for code in hidden)
        schema = load(w)['schema']['promo_banner']
        code_field = next(field for field in schema['fields'] if field['id'] == 'discount_code')
        assert code_field['type'] == 'select'
        assert code_field['options'] == [
            {'value': '', 'label': 'No discount code'}, {'value': own.code.id, 'label': own.code.code}]
        assert own.code.id in str(w.client.get(w.base + '/resources?q=CODEOWN').json())
        assert own.code.id not in str(w.client.get(w.base + '/resources?q=not-found').json())
        for model, count in before.items():
            assert db.scalar(select(func.count()).select_from(model)) == count


def test_all_widgets_support_containers_and_conditional_assets(context):
    db, site, page = context
    container = default_block('container') | {'children': [default_block(kind) for kind in PACK]}
    assert validate_block(container) == container
    assert set(PACK) <= set(editor_schema()['container']['allowed_children'])
    page.draft_json = document(container)
    markup = to_xml(storefront(db, site, page, '', 'csrf', '/your-page', preview=True))
    assert 'widgets-pack3.css' in markup and 'widgets-pack3.js' in markup
    page.draft_json = document(default_block('heading'))
    markup = to_xml(storefront(db, site, page, '', 'csrf', '/your-page', preview=True))
    assert 'widgets-pack3.css' not in markup and 'widgets-pack3.js' not in markup


def test_editor_schema_save_and_publish_all_widgets_with_csrf(workspace):
    w = workspace
    loaded = load(w)
    with w.sessions() as db:
        catalogue = seed_catalog(db, w.site)
        collection_id, product_id, code_id = catalogue.collection.id, catalogue.product.id, catalogue.code.id
        db.commit()
    categories = {kind: 'Content' for kind in PACK} | {
        'featured_collection': 'Commerce', 'pricing_table': 'Commerce', 'comparison_table': 'Commerce',
        'trust_badges': 'Marketing', 'announcement_bar': 'Marketing', 'promo_banner': 'Marketing',
        'star_rating': 'Social proof'}
    icons = []
    for kind, category in categories.items():
        schema = loaded['schema'][kind]
        assert schema['category'] == category
        assert schema['insertable'] and schema['icon'] and schema['description']
        assert schema['description'] == WIDGETS[kind].description
        icons.append(schema['icon'])
    assert len(icons) == len(set(icons))
    for kind, field, resource_type in [('featured_collection', 'collection', 'collection'),
                                      ('star_rating', 'product', 'product'),
                                      ('promo_banner', 'discount_code', 'select')]:
        metadata = next(item for item in loaded['schema'][kind]['fields'] if item['id'] == field)
        assert metadata['type'] == resource_type
    doc = document(*(default_block(kind, kind) for kind in PACK))
    doc['blocks'][0]['collection'] = collection_id
    doc['blocks'][5]['discount_code'] = code_id
    doc['blocks'][7].update(scope='product', product=product_id)
    payload = {'document': doc, 'version': loaded['version']}
    params = {'page': w.page.id}
    assert w.client.put(w.base + '/draft', params=params, json=payload).status_code == 403
    saved = w.client.put(w.base + '/draft', params=params, json=payload, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()['document'] == doc
    payload['version'] = saved.json()['version']
    assert w.client.post(w.base + '/publish', params=params, json=payload).status_code == 403
    published = w.client.post(w.base + '/publish', params=params, json=payload, headers=w.headers)
    assert published.status_code == 200, published.text
    assert published.json()['published'] is True
    assert load(w)['document'] == doc
    with w.sessions() as db:
        stored = db.get(SitePage, w.page.id).published_json
        assert stored['published_at']
        assert {key: stored[key] for key in doc} == doc


def test_foreign_resources_reviews_and_demo_data_never_render_or_appear_in_picker(workspace):
    from app.demo_commerce import initial_state
    from app.models import DemoWorkspace

    w = workspace
    load(w)
    with w.sessions() as db:
        own = seed_catalog(db, w.site)
        foreign = seed_catalog(db, w.other, 'foreign')
        sibling = Site(tenant_id=w.site.tenant_id, channel_id=w.site.channel_id,
                       name='Sibling store', slug='sibling-store')
        db.add(sibling)
        db.flush()
        hidden = seed_catalog(db, sibling, 'sibling')
        add_review(db, w.other, foreign.product, 1)
        add_review(db, w.site, hidden.product, 1)
        add_review(db, w.site, own.product, 5)
        db.add(DemoWorkspace(tenant_id=w.site.tenant_id, site_id=w.site.id, state_json=initial_state()))
        db.commit()
        ctx = (db, w.site, w.page)
        for kind, field, value in [('featured_collection', 'collection', foreign.collection.id),
                                   ('featured_collection', 'collection', hidden.collection.id),
                                   ('star_rating', 'product', foreign.product.id),
                                   ('star_rating', 'product', hidden.product.id),
                                   ('promo_banner', 'discount_code', foreign.code.id),
                                   ('promo_banner', 'discount_code', hidden.code.id)]:
            block = default_block(kind) | {field: value}
            if kind == 'star_rating':
                block['scope'] = 'product'
            markup = render(ctx, block, preview=False)
            for forbidden in (foreign.collection.title, foreign.product.name, foreign.code.code,
                              hidden.collection.title, hidden.product.name, hidden.code.code):
                assert forbidden not in markup
            response = w.client.put(w.base + '/draft', json={
                'document': document(block), 'version': load(w)['version']}, headers=w.headers)
            assert response.status_code == 422, response.text
        assert 'Rated 5.0 out of 5 from 1 review' in render(ctx, default_block('star_rating'), preview=False)
        all_markup = ''.join(render(ctx, default_block(kind)) for kind in PACK)
        assert not any(item['name'] in all_markup for item in initial_state()['catalog'].values())
        resources = w.client.get(w.base + '/resources').json()
        assert own.product.id in str(resources) and own.collection.id in str(resources) and own.code.id in str(resources)
        for excluded in (foreign.product.id, foreign.collection.id, foreign.code.id,
                         hidden.product.id, hidden.collection.id, hidden.code.id):
            assert excluded not in str(resources)
        code_field = next(field for field in load(w)['schema']['promo_banner']['fields']
                          if field['id'] == 'discount_code')
        assert code_field['options'] == [
            {'value': '', 'label': 'No discount code'}, {'value': own.code.id, 'label': own.code.code}]


@pytest.mark.parametrize(('kind', 'field'), [('featured_collection', 'collection'),
                                          ('star_rating', 'product'), ('promo_banner', 'discount_code')])
def test_unknown_resource_ids_rejected_by_editor(workspace, kind, field):
    w = workspace
    values = load(w)
    block = default_block(kind) | {field: 'missing-resource'}
    if kind == 'star_rating':
        block['scope'] = 'product'
    payload = {'document': document(block), 'version': values['version']}
    for method, path in ((w.client.put, '/draft'), (w.client.post, '/render'), (w.client.post, '/publish')):
        response = method(w.base + path, json=payload, headers=w.headers)
        assert response.status_code == 422, response.text


def test_ai_generation_and_refinement_preserve_pack3():
    blocks = [default_block(kind, kind) for kind in PACK]
    original = document(*blocks, default_block('heading', 'editable'))
    snapshot = {'settings': {'default_locale': 'en'}, 'pages': {'page': {'document': original}}}
    immediate, operations = derive_operations(snapshot, [
        {'op': 'patch', 'page_id': 'page', 'block_id': 'editable', 'after': {'heading': 'Updated heading'}}])
    assert not immediate and len(operations) == 1
    assert apply_operation(original, operations[0])['blocks'][:-1] == blocks
    assert derive_operations(snapshot, [{'op': 'document', 'page_id': 'page', 'document': original}]) == ([], [])
    response = guided('swap the first two blocks', {'snapshot': snapshot, 'page_id': 'page'})
    assert response['operations'][0]['ids'] == [blocks[1]['id'], blocks[0]['id'],
                                              *[b['id'] for b in original['blocks'][2:]]]
    plan = guided_plan(MerchantBrief('Your studio', 'studio', 'your audience', 'minimal'))
    generated = copy.deepcopy(blocks)
    for block in generated:
        for key in {'heading', 'eyebrow', 'body', 'button', 'alt'} & block.keys():
            if block[key]:
                block[key] = {'en': block[key]}
        for item in block.get('items', []):
            for key in {'heading', 'body', 'label', 'value'} & item.keys():
                if item[key]:
                    item[key] = {'en': item[key]}
                else:
                    del item[key]
    plan['pages'][0]['blocks'].extend(generated)
    assert validate_plan(plan)['pages'][0]['blocks'][-len(PACK):] == generated

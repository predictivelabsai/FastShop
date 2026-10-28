"""Showcase widgets preserve accessibility, scoped commerce and editor contracts."""

import copy
from uuid import uuid4

import pytest
from fasthtml.common import fast_app, to_xml
from sqlalchemy import select
from starlette.testclient import TestClient

from app import db as app_db
from app import site_context, site_widget_routes
from app.integrations.site_builder_llm import guided
from app.models import DemoWorkspace, Site, SitePage, VariantChannelListing
from app.services import CommerceError
from app.site_block_schema import default_block, editor_schema
from app.site_blocks import normalize_document, validate_block
from app.site_generation import MerchantBrief, guided_plan, validate_plan
from app.site_refinement import apply_operation, derive_operations
from app.site_ui import render_section, storefront
from app.site_widgets import WIDGETS
from tests.test_site_editor_blocks import context as context
from tests.test_site_editor_routes import load
from tests.test_site_editor_routes import workspace as workspace
from tests.test_site_widgets_pack3 import seed_catalog

PACK = ('marquee', 'collage', 'flip_card', 'cta_box', 'video_lightbox', 'team_members',
        'pull_quote', 'button_group', 'shop_the_look', 'recently_viewed')


def document(*blocks):
    return {'version': 1, 'title': 'Your showcase', 'blocks': list(blocks)}


def render(context, block, *, preview=True):
    db, site, page = context
    return to_xml(render_section(db, site, page, {}, block, '', 'csrf', preview))


@pytest.mark.parametrize('kind', PACK)
def test_defaults_validate_render_and_round_trip(context, kind):
    block = default_block(kind)
    assert validate_block(copy.deepcopy(block)) == block
    assert normalize_document(document(block))['blocks'] == [block]
    markup = render(context, block)
    assert 'fs-widget-' + kind in markup
    assert f'data-block-id="{block["id"]}"' in markup
    if kind not in {'shop_the_look', 'recently_viewed'}:
        assert 'data-field="' in markup


@pytest.mark.parametrize(('kind', 'patch'), [
    ('marquee', {'speed': 'instant'}), ('marquee', {'direction': 'up'}),
    ('marquee', {'separator': '<svg>'}), ('collage', {'layout': 'random'}),
    ('team_members', {'columns': 1}), ('team_members', {'columns': 5}), ('team_members', {'columns': True}),
    ('cta_box', {'layout': 'grid'}), ('cta_box', {'box_style': 'unbounded'}),
    ('cta_box', {'overlay_opacity': -1}), ('cta_box', {'overlay_opacity': 101}),
    ('pull_quote', {'quote_style': 'review'}), ('button_group', {'alignment': 'diagonal'}),
    ('button_group', {'stack_mobile': 'yes'}), ('button_group', {'full_width_mobile': 1}),
    ('recently_viewed', {'limit': 0}), ('recently_viewed', {'limit': 9}),
    ('recently_viewed', {'limit': True}), ('recently_viewed', {'columns': 5}),
])
def test_invalid_ranges_layouts_and_choices(kind, patch):
    with pytest.raises(CommerceError):
        validate_block(default_block(kind) | patch)


@pytest.mark.parametrize(('kind', 'minimum', 'maximum'), [
    ('marquee', 1, 10), ('collage', 2, 8), ('flip_card', 1, 6),
    ('team_members', 1, 12), ('button_group', 1, 4), ('shop_the_look', 0, 8),
])
def test_item_counts_and_unknown_fields(kind, minimum, maximum):
    block = default_block(kind)
    item = block['items'][0]
    for size in {minimum, maximum}:
        assert validate_block(block | {'items': [copy.deepcopy(item) for _ in range(size)]})
    for items in ([item] * (maximum + 1), [item | {'unexpected': 'value'}]):
        with pytest.raises(CommerceError):
            validate_block(block | {'items': items})
    if minimum:
        with pytest.raises(CommerceError):
            validate_block(block | {'items': [item] * (minimum - 1)})


@pytest.mark.parametrize('url', ['http://example.test', '//example.test', 'javascript:alert(1)',
                                  'https://user:password@example.test'])
@pytest.mark.parametrize('kind', ['collage', 'flip_card', 'cta_box', 'button_group'])
def test_links_require_safe_https_or_local_paths(kind, url):
    block = default_block(kind)
    if kind == 'cta_box':
        block['link'] = url
    else:
        block['items'][0]['link'] = url
    with pytest.raises(CommerceError):
        validate_block(block)


@pytest.mark.parametrize('url', ['https://evil.test/video.mp4', 'http://youtube.com/watch?v=abcdefghijk',
                                  '//youtube.com/embed/abcdefghijk', 'javascript:alert(1)',
                                  '/static/video.html', 'https://youtube.com.evil.test/watch?v=abcdefghijk'])
def test_video_rejects_unsafe_and_disallowed_hosts(url):
    with pytest.raises(CommerceError):
        validate_block(default_block('video_lightbox') | {'url': url})


@pytest.mark.parametrize('url', ['https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                                  'https://vimeo.com/123456789', '/static/videos/your-video.mp4'])
def test_video_is_deferred_and_has_link_fallback(context, url):
    block = validate_block(default_block('video_lightbox') | {'url': url})
    markup = render(context, block, preview=False)
    assert '<iframe' not in markup and '<video' not in markup
    assert '<dialog' in markup and 'href="' + url in markup


def test_local_video_asset_does_not_receive_site_page_prefix(context):
    db, site, page = context
    block = default_block('video_lightbox') | {'url': '/static/videos/your-video.mp4'}
    markup = to_xml(render_section(db, site, page, {}, block, '/sites/showcase', 'csrf', False))
    assert 'data-video-src="/static/videos/your-video.mp4"' in markup
    assert 'href="/static/videos/your-video.mp4"' in markup
    assert '/sites/showcase/static/' not in markup


def test_accessible_server_markup_and_generic_people(context):
    marquee = render(context, default_block('marquee'), preview=False)
    assert 'aria-hidden="true"' in marquee and '<button' in marquee
    flip = default_block('flip_card')
    flip['items'][0]['body'] = 'Readable back content'
    markup = render(context, flip, preview=False)
    assert 'Readable back content' in markup and 'aria-expanded="false"' in markup and '<button' in markup
    quote = render(context, default_block('pull_quote') | {'attribution': 'Your attribution'}, preview=False)
    assert '<figure' in quote and '<blockquote' in quote and '<figcaption' in quote
    assert 'star' not in quote.lower()
    team = render(context, default_block('team_members'))
    assert 'Team member name' in team and 'Role' in team
    block = default_block('team_members')
    for patch in ({'social_1_platform': 'invented'}, {'social_1_url': 'http://example.test'}):
        with pytest.raises(CommerceError):
            validate_block(block | {'items': [block['items'][0] | patch]})


@pytest.mark.parametrize('patch', [{'x': -1}, {'x': 101}, {'y': -1}, {'y': 101}, {'x': True}])
def test_hotspot_coordinates_are_bounded(patch):
    block = default_block('shop_the_look')
    with pytest.raises(CommerceError):
        validate_block(block | {'items': [block['items'][0] | patch]})


def test_shop_uses_channel_minor_units_and_plain_product_links(context):
    db, site, page = context
    catalogue = seed_catalog(db, site)
    block = default_block('shop_the_look')
    block['items'][0]['product'] = catalogue.product.id
    markup = render(context, block, preview=False)
    assert catalogue.product.name in markup and '19.99' in markup and '1999.00' not in markup
    assert 'View product' in markup and catalogue.product_page.path in markup
    assert 'add-to-cart' not in markup
    listing = db.scalar(select(VariantChannelListing).where(
        VariantChannelListing.variant_id == catalogue.product.variants[0].id,
        VariantChannelListing.channel_id == site.channel_id))
    db.delete(listing)
    db.flush()
    assert catalogue.product.name not in render(context, block, preview=False)


def test_editor_schema_containers_assets_and_csrf_round_trip(workspace):
    w = workspace
    loaded = load(w)
    categories = dict.fromkeys(PACK, 'Content') | {'marquee': 'Marketing', 'cta_box': 'Marketing',
        'collage': 'Media', 'video_lightbox': 'Media', 'shop_the_look': 'Commerce', 'recently_viewed': 'Commerce'}
    icons = []
    for kind, category in categories.items():
        schema = loaded['schema'][kind]
        assert schema['category'] == category and schema['insertable']
        assert schema['description'] == WIDGETS[kind].description and schema['description']
        icons.append(schema['icon'])
    assert len(set(icons)) == len(PACK)
    assert set(PACK) <= set(editor_schema()['container']['allowed_children'])
    container = default_block('container') | {'children': [default_block(kind, kind) for kind in PACK]}
    doc = document(container)
    assert validate_block(container) == container
    payload = {'document': doc, 'version': loaded['version']}
    assert w.client.put(w.base + '/draft', json=payload).status_code == 403
    saved = w.client.put(w.base + '/draft', json=payload, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()['document'] == doc
    payload['version'] = saved.json()['version']
    assert w.client.post(w.base + '/publish', json=payload).status_code == 403
    published = w.client.post(w.base + '/publish', json=payload, headers=w.headers)
    assert published.status_code == 200, published.text
    with w.sessions() as db:
        page = db.get(SitePage, w.page.id)
        assert page.published_json['blocks'] == doc['blocks']
        markup = to_xml(storefront(db, w.site, page, '', 'csrf', '/showcase'))
        assert 'widgets-pack4.css' in markup and 'widgets-pack4.js' in markup
        page.draft_json = document(default_block('heading'))
        markup = to_xml(storefront(db, w.site, page, '', 'csrf', '/showcase', preview=True))
        assert 'widgets-pack4.css' not in markup and 'widgets-pack4.js' not in markup


@pytest.mark.parametrize('source', ['foreign', 'sibling', 'unpublished', 'unknown', 'demo'])
def test_shop_editor_rejects_unavailable_products_and_never_renders_them(workspace, source):
    from app.demo_commerce import initial_state

    w = workspace
    loaded = load(w)
    with w.sessions() as db:
        sibling = Site(tenant_id=w.site.tenant_id, channel_id=w.site.channel_id, name='Sibling', slug='sibling')
        db.add(sibling)
        db.flush()
        own = seed_catalog(db, w.site)
        foreign = seed_catalog(db, w.other, 'foreign')
        hidden = seed_catalog(db, sibling, 'sibling')
        own.product.is_published = False
        demo = initial_state()
        db.add(DemoWorkspace(tenant_id=w.site.tenant_id, site_id=w.site.id, state_json=demo))
        identifier = {'foreign': foreign.product.id, 'sibling': hidden.product.id,
            'unpublished': own.product.id, 'unknown': str(uuid4()), 'demo': next(iter(demo['catalog']))}[source]
        db.commit()
        block = default_block('shop_the_look')
        block['items'][0]['product'] = identifier
        markup = render((db, w.site, w.page), block, preview=False)
        for product in (foreign.product, hidden.product, own.product):
            assert product.name not in markup
        assert all(item['name'] not in markup for item in demo['catalog'].values())
    payload = {'document': document(block), 'version': loaded['version']}
    for method, path in ((w.client.put, '/draft'), (w.client.post, '/render'), (w.client.post, '/publish')):
        response = method(w.base + path, json=payload, headers=w.headers)
        assert response.status_code == 422, response.text


def public_client(w, monkeypatch):
    monkeypatch.setattr(site_widget_routes, 'SessionLocal', w.sessions)
    monkeypatch.setattr(site_context, 'SessionLocal', w.sessions)
    monkeypatch.setattr(app_db, 'SessionLocal', w.sessions)
    app, rt = fast_app(secret_key='widget-public-test')
    site_widget_routes.register_site_widget_routes(rt, lambda session: None, lambda session: None,
        lambda session, form: None, lambda *args: args, lambda exc: str(exc))
    app.add_middleware(site_context.SiteHostMiddleware)
    return TestClient(app)


def test_recent_route_scopes_site_tenant_channel_and_excludes_demo(workspace, monkeypatch):
    from app.demo_commerce import initial_state

    w = workspace
    with w.sessions() as db:
        own = seed_catalog(db, w.site)
        foreign = seed_catalog(db, w.other, 'foreign')
        sibling = Site(tenant_id=w.site.tenant_id, channel_id=w.site.channel_id, name='Sibling', slug='recent-sibling')
        db.add(sibling)
        db.flush()
        hidden = seed_catalog(db, sibling, 'sibling')
        unpublished = seed_catalog(db, w.site, 'unpublished')
        unpublished.product_page.published_json = None
        no_channel = seed_catalog(db, w.site, 'no-channel')
        listing = db.scalar(select(VariantChannelListing).where(
            VariantChannelListing.variant_id == no_channel.product.variants[0].id,
            VariantChannelListing.channel_id == w.site.channel_id))
        db.delete(listing)
        demo = initial_state()
        db.add(DemoWorkspace(tenant_id=w.site.tenant_id, site_id=w.site.id, state_json=demo))
        site = db.get(Site, w.site.id)
        site.hostname, site.status = 'showcase.example.test', 'published'
        ids = [own.product.id, foreign.product.id, hidden.product.id, unpublished.product.id, no_channel.product.id]
        db.commit()
    with public_client(w, monkeypatch) as client:
        path = '/sites/' + w.site.slug + '/widgets/recently-viewed'
        for target, headers in ((path, {}), ('/widgets/recently-viewed', {'Host': 'showcase.example.test'})):
            response = client.get(target, params={'ids': ','.join(ids)}, headers=headers)
            assert response.status_code == 200, response.text
            assert own.product.name in response.text and '19.99' in response.text
            assert 'no-store' in response.headers['cache-control']
            for product in (foreign.product, hidden.product, unpublished.product, no_channel.product):
                assert product.name not in response.text
            assert all(item['name'] not in response.text for item in demo['catalog'].values())
        for ids_value in ('', str(uuid4())):
            response = client.get(path, params={'ids': ids_value})
            assert response.status_code == 200 and not response.text.strip()
        for ids_value in ('not-a-uuid', '../admin', ','.join(str(uuid4()) for _ in range(9))):
            assert client.get(path, params={'ids': ids_value}).status_code in {400, 422}


def test_recent_empty_storefront_and_clear_editor_placeholder(context):
    block = default_block('recently_viewed')
    markup = render(context, block, preview=False)
    assert 'hidden' in markup
    assert 'data-required-consent="preferences"' in markup
    assert 'viewed' in render(context, block).lower()


def test_generation_and_refinement_preserve_showcase_documents():
    blocks = [default_block(kind, kind) for kind in PACK]
    original = document(*blocks, default_block('heading', 'editable'))
    snapshot = {'settings': {'default_locale': 'en'}, 'pages': {'page': {'document': original}}}
    immediate, operations = derive_operations(snapshot, [
        {'op': 'patch', 'page_id': 'page', 'block_id': 'editable', 'after': {'heading': 'Updated heading'}}])
    assert not immediate and len(operations) == 1
    assert apply_operation(original, operations[0])['blocks'][:-1] == blocks
    assert derive_operations(snapshot, [{'op': 'document', 'page_id': 'page', 'document': original}]) == ([], [])
    response = guided('swap the first two blocks', {'snapshot': snapshot, 'page_id': 'page'})
    assert response['operations'][0]['ids'][:2] == [blocks[1]['id'], blocks[0]['id']]
    plan = guided_plan(MerchantBrief('Your studio', 'studio', 'your audience', 'minimal'))
    generated = copy.deepcopy(blocks)
    for block in generated:
        for key in {'heading', 'eyebrow', 'body', 'button', 'alt'} & block.keys():
            if block[key]:
                block[key] = {'en': block[key]}
        for item in block.get('items', []):
            for key in {'heading', 'body', 'label', 'value'} & item.keys():
                item[key] = {'en': item[key]}
    plan['pages'][0]['blocks'].extend(generated)
    assert validate_plan(plan)['pages'][0]['blocks'][-len(PACK):] == generated


def test_shop_rejects_localised_or_structured_product_identifiers():
    block = default_block('shop_the_look')
    for value in ({'en': str(uuid4())}, [str(uuid4())], 7):
        with pytest.raises(CommerceError):
            validate_block(block | {'items': [block['items'][0] | {'product': value}]})


def test_legacy_team_document_retains_original_markup(context):
    block = {'id': 'legacy-team', 'type': 'team', 'version': 1, 'heading': 'Your existing team',
        'items': [{'heading': 'Your member', 'body': 'Existing biography', 'image': '', 'url': ''}]}
    assert normalize_document(document(block))['blocks'] == [block]
    markup = render(context, block, preview=False)
    assert 'h-team' in markup and 'Your member' in markup and 'Existing biography' in markup
    assert 'team-grid' not in markup


def test_legacy_people_and_team_members_share_a_page(context):
    db, site, page = context
    legacy = {'id': 'legacy', 'type': 'team', 'version': 1, 'heading': 'Existing people',
        'items': [{'heading': 'Existing member', 'body': 'Existing biography'}]}
    team = default_block('team_members', 'new-team')
    team.pop('columns')
    doc = normalize_document(document(legacy, team))
    assert doc['blocks'] == [legacy, team]
    page.published_json = doc
    markup = to_xml(storefront(db, site, page, '', 'csrf', '/'))
    assert 'h-team' in markup and 'Existing member' in markup
    assert 'fs-widget-team_members' in markup and 'Team member name' in markup
    assert editor_schema()['team']['label'] == 'People'
    assert editor_schema()['team_members']['label'] == 'Team'
    with pytest.raises(CommerceError):
        validate_block(legacy | {'columns': 3})


def test_recent_recording_requires_widget_on_published_site_page(context):
    db, site, page = context
    own = seed_catalog(db, site)
    site.status = 'published'
    widget_doc = document(default_block('container') | {
        'children': [default_block('recently_viewed')]})
    page.draft_json = widget_doc
    page.published_json = document(default_block('heading'))

    def markup(preview=False):
        return to_xml(storefront(db, site, own.product_page, '', 'csrf', '/', preview=preview))

    def assert_recording(expected, preview=False):
        html = markup(preview)
        assert ('data-recent-product=' in html) is expected
        assert ('widgets-pack4.js' in html) is expected
        assert ('data-required-consent="preferences"' in html) is expected

    assert_recording(False)  # A draft-only widget does not opt the site in.
    sibling = Site(tenant_id=site.tenant_id, channel_id=site.channel_id, name='Sibling', slug='record-sibling')
    db.add(sibling)
    db.flush()
    sibling_page = SitePage(tenant_id=site.tenant_id, site_id=sibling.id, title='Other',
        path='/', kind='page', draft_json=widget_doc, published_json=widget_doc)
    db.add(sibling_page)
    db.flush()
    assert_recording(False)
    # Matching text in a published heading is not a widget.
    page.published_json = document(default_block('heading') | {'heading': 'recently_viewed'})
    db.flush()
    assert_recording(False)
    page.published_json = widget_doc
    db.flush()
    assert_recording(True)
    assert_recording(False, preview=True)
    site.status = 'preview'
    assert_recording(False)
    site.status = 'published'
    page.published_json = None
    db.flush()
    assert_recording(False)


@pytest.mark.parametrize('status', ['brief', 'draft', 'preview', 'published'])
def test_recent_route_requires_publication(workspace, monkeypatch, status):
    w = workspace
    with w.sessions() as db:
        own = seed_catalog(db, w.site)
        site = db.get(Site, w.site.id)
        site.status = status
        site.hostname = 'gated.example.test'
        db.commit()
    with public_client(w, monkeypatch) as client:
        for path, headers in ((f'/sites/{w.site.slug}/widgets/recently-viewed', {}),
                ('/widgets/recently-viewed', {'Host': 'gated.example.test'})):
            response = client.get(path, params={'ids': own.product.id}, headers=headers)
            assert response.status_code == (200 if status == 'published' else 404)
            assert (own.product.name in response.text) is (status == 'published')


def test_recent_route_respects_storefront_password_gate(workspace, monkeypatch):
    from app import store_preferences

    w = workspace
    with w.sessions() as db:
        own = seed_catalog(db, w.site)
        site = db.get(Site, w.site.id)
        site.status = 'published'
        site.hostname = 'private.example.test'
        preferences = store_preferences.save_preferences(db, site, {
            'password_enabled': True, 'password': 'widget-store-pass-123'})
        token = store_preferences.access_token(site, preferences)
        db.commit()
    with public_client(w, monkeypatch) as client:
        for path, headers in ((f'/sites/{w.site.slug}/widgets/recently-viewed', {}),
                ('/widgets/recently-viewed', {'Host': 'private.example.test'})):
            denied = client.get(path, params={'ids': own.product.id}, headers=headers, follow_redirects=False)
            assert denied.status_code == 303
            assert denied.headers['location'].endswith('/password')
            assert own.product.name not in denied.text
            allowed = client.get(path, params={'ids': own.product.id}, headers=headers | {
                'Cookie': f'fastshop_store_{w.site.id}={token}'})
            assert allowed.status_code == 200 and own.product.name in allowed.text


def test_storefront_banner_offers_preferences_without_preselecting(context):
    db, site, page = context
    page.published_json = document(default_block('recently_viewed'))
    markup = to_xml(storefront(db, site, page, '', 'csrf', '/'))
    from bs4 import BeautifulSoup

    banner = BeautifulSoup(markup, 'html.parser').select_one('#h-cookie')
    preferences = banner.select_one('#consent-preferences')
    assert preferences['type'] == 'checkbox' and not preferences.has_attr('checked')
    assert 'products you viewed on this device' in preferences.parent.get_text()
    assert {button['data-consent'] for button in banner.select('button[data-consent]')} == {'all', 'none', 'custom'}

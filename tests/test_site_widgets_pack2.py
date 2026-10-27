"""Interactive widget registry, safety, progressive rendering and editor contracts."""

import copy
from datetime import UTC, datetime

import pytest
from fasthtml.common import to_xml

from app.integrations.site_builder_llm import guided
from app.models import SitePage
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

PACK = ('tabs', 'countdown', 'stats', 'progress_bars', 'alert', 'table', 'map', 'social_links')


def document(*blocks):
    return {'version': 1, 'title': 'Your page', 'blocks': list(blocks)}


def render(context, block):
    db, site, page = context
    return to_xml(render_section(db, site, page, {}, block, '', 'csrf', True))


@pytest.mark.parametrize('kind', PACK)
def test_defaults_validate_render_and_inline_hooks(context, kind):
    block = default_block(kind)
    assert validate_block(copy.deepcopy(block)) == block
    assert normalize_document(document(block))['blocks'] == [block]
    markup = render(context, block)
    assert 'fs-widget-' + kind in markup
    assert f'data-block-id="{block["id"]}"' in markup
    assert 'data-field="' in markup


@pytest.mark.parametrize(('kind', 'patch'), [
    ('tabs', {'layout': 'diagonal'}), ('countdown', {'show_days': 'true'}),
    ('countdown', {'expired_behaviour': 'restart'}), ('stats', {'animate': 1}),
    ('progress_bars', {'show_percentage': 'true'}), ('alert', {'notice_style': 'danger'}),
    ('alert', {'dismissible': 1}), ('table', {'columns': 0}), ('table', {'columns': 7}),
    ('table', {'columns': True}), ('social_links', {'link_style': 'buttons'}),
    ('social_links', {'size': 'huge'}), ('social_links', {'alignment': 'justify'}),
])
def test_ranges_choices_and_booleans_rejected(kind, patch):
    with pytest.raises(CommerceError):
        validate_block(default_block(kind) | patch)


@pytest.mark.parametrize(('kind', 'minimum', 'maximum'), [
    ('tabs', 2, 8), ('stats', 1, 6), ('progress_bars', 1, 8),
    ('table', 0, 50), ('social_links', 0, 12),
])
def test_item_bounds_and_allowlists(kind, minimum, maximum):
    block = default_block(kind)
    item = block['items'][0]
    validate_block(block | {'items': [copy.deepcopy(item) for _ in range(maximum)]})
    validate_block(block | {'items': [copy.deepcopy(item) for _ in range(minimum)]})
    for items in ([item] * (maximum + 1), [item | {'unexpected': 'value'}]):
        with pytest.raises(CommerceError):
            validate_block(block | {'items': items})
    if minimum:
        with pytest.raises(CommerceError):
            validate_block(block | {'items': [item] * (minimum - 1)})


@pytest.mark.parametrize('percentage', [-1, 101, True, '50'])
def test_progress_percentage_rejected(percentage):
    block = default_block('progress_bars')
    with pytest.raises(CommerceError):
        validate_block(block | {'items': [block['items'][0] | {'percentage': percentage}]})


@pytest.mark.parametrize('end_at,timezone', [
    ('not a date', ''), ('2027-02-30T12:00:00+00:00', ''),
    ('2027-01-01T12:00:00', ''), ('2027-01-01T12:00:00', 'Invalid/Zone'),
    ('2027-01-01T12:00:00+99:00', ''),
])
def test_countdown_rejects_bad_dates_and_timezones(end_at, timezone):
    with pytest.raises(CommerceError):
        validate_block(default_block('countdown') | {'end_at': end_at, 'timezone': timezone})


def test_countdown_accepts_offset_and_iana_dates():
    for end_at, timezone in [('2027-01-01T12:00:00+02:00', ''),
                             ('2027-01-01T12:00:00', 'Europe/Tallinn')]:
        validate_block(default_block('countdown') | {'end_at': end_at, 'timezone': timezone})


@pytest.mark.parametrize('url', [
    'http://www.google.com/maps/embed?pb=example', 'https://example.test/maps/embed?x=1',
    'https://www.google.com.evil.test/maps/embed?x=1', 'https://www.google.com/maps/search?q=address',
    'https://www.openstreetmap.org/other?x=1', 'javascript:alert(1)',
    '//www.google.com/maps/embed?x=1', 'https://user:password@www.google.com/maps/embed?x=1',
])
def test_map_rejects_unapproved_urls(url):
    with pytest.raises(CommerceError):
        validate_block(default_block('map') | {'embed_url': url})


@pytest.mark.parametrize('url', [
    'https://www.google.com/maps/embed?pb=example',
    'https://www.openstreetmap.org/export/embed.html?bbox=0%2C0%2C1%2C1',
])
def test_map_is_click_to_load_with_link_fallback(context, url):
    block = validate_block(default_block('map') | {'embed_url': url})
    markup = render(context, block)
    assert '<iframe' not in markup
    assert 'Load map' in markup and 'Open in maps' in markup
    assert 'third party' in markup.lower()
    assert url.split('?')[0] in markup


@pytest.mark.parametrize('url', ['http://example.test', '//example.test', 'javascript:alert(1)',
                                  'mailto:hello@example.test', 'https://user:password@example.test'])
def test_social_links_reject_unsafe_or_non_https_urls(url):
    block = default_block('social_links')
    with pytest.raises(CommerceError):
        validate_block(block | {'items': [{'platform': 'instagram', 'url': url, 'label': ''}]})


def test_social_email_and_owned_svg_accessibility(context):
    block = default_block('social_links') | {'items': [
        {'platform': 'email', 'url': 'mailto:hello@example.test', 'label': ''},
        {'platform': 'instagram', 'url': 'https://example.test/profile', 'label': ''},
    ]}
    markup = render(context, validate_block(block))
    assert '<svg' in markup and 'Instagram, opens in new tab' in markup
    assert 'noopener noreferrer' in markup
    assert 'mailto:hello@example.test' in markup
    assert '<script' not in markup and 'fonts.googleapis' not in markup


def test_tabs_rich_text_sanitised(context):
    block = default_block('tabs')
    block['items'][0]['body'] = '<p>Safe <strong>body</strong></p><script>alert(1)</script>'
    clean = validate_block(block)
    assert '<script' not in clean['items'][0]['body']
    markup = render(context, clean)
    assert '<strong>body</strong>' in markup and '<script' not in markup
    assert 'hidden' not in markup or '<section hidden' not in markup


def test_table_semantics_and_escaped_cells(context):
    block = default_block('table') | {'columns': 2, 'caption': 'Your comparison',
        'header_1': 'Item', 'header_2': 'Detail', 'first_column_header': True,
        'items': [{'cell_1': 'Your item', 'cell_2': '<script>alert(1)</script>'}]}
    markup = render(context, validate_block(block))
    assert '<table' in markup and '<caption' in markup and 'Your comparison' in markup
    assert 'scope="col"' in markup and 'scope="row"' in markup
    assert '<script' not in markup and '&lt;script&gt;' in markup


def test_progress_semantics_and_stats_final_values(context):
    block = default_block('progress_bars') | {'items': [{'label': 'Your progress', 'percentage': 37}]}
    markup = render(context, block)
    assert 'role="progressbar"' in markup or '<meter' in markup
    assert 'aria-valuenow="37"' in markup and 'aria-valuemin="0"' in markup and 'aria-valuemax="100"' in markup
    stats = default_block('stats') | {'animate': True, 'items': [{'value': '1,200+', 'label': 'Your figure', 'source': 'Your source'}]}
    markup = render(context, stats)
    assert '1,200+' in markup and 'Your source' in markup
    assert default_block('stats')['animate'] is False


def test_all_widgets_support_containers_and_conditional_assets(context):
    db, site, page = context
    container = default_block('container') | {'children': [default_block(kind) for kind in PACK]}
    assert validate_block(container) == container
    assert set(PACK) <= set(editor_schema()['container']['allowed_children'])
    page.draft_json = document(container)
    markup = to_xml(storefront(db, site, page, '', 'csrf', '/your-page', preview=True))
    assert 'widgets-pack2.css' in markup and 'widgets-pack2.js' in markup
    page.draft_json = document(default_block('heading'))
    markup = to_xml(storefront(db, site, page, '', 'csrf', '/your-page', preview=True))
    assert 'widgets-pack2.css' not in markup and 'widgets-pack2.js' not in markup


def test_ai_builder_generation_and_refinement_preserve_pack2():
    blocks = [default_block(kind, kind) for kind in PACK]
    original = document(*blocks, default_block('heading', 'editable'))
    snapshot = {'settings': {'default_locale': 'en'}, 'pages': {'page': {'document': original}}}
    immediate, operations = derive_operations(snapshot, [
        {'op': 'patch', 'page_id': 'page', 'block_id': 'editable', 'after': {'heading': 'Updated heading'}}])
    assert not immediate and len(operations) == 1
    updated = apply_operation(original, operations[0])
    assert updated['blocks'][:-1] == blocks
    assert derive_operations(snapshot, [{'op': 'document', 'page_id': 'page', 'document': original}]) == ([], [])
    response = guided('swap the first two blocks', {'snapshot': snapshot, 'page_id': 'page'})
    assert response['operations'][0]['ids'] == [blocks[1]['id'], blocks[0]['id'], *[b['id'] for b in original['blocks'][2:]]]
    plan = guided_plan(MerchantBrief('Your studio', 'studio', 'your audience', 'minimal'))
    generated = copy.deepcopy(blocks)
    # Generation requires default-locale maps for its copy-bearing fields.
    for block in generated:
        for key in {'heading', 'eyebrow', 'body', 'button', 'alt'} & block.keys():
            if block[key]:
                block[key] = {'en': block[key]}
        for item in block.get('items', []):
            for key in {'heading', 'body', 'label', 'value'} & item.keys():
                if item[key]:
                    item[key] = {'en': item[key]}
                else:
                    # Generated copy is non-empty; optional platform labels may be omitted.
                    del item[key]
    plan['pages'][0]['blocks'].extend(generated)
    assert validate_plan(plan)['pages'][0]['blocks'][-len(PACK):] == generated


def test_editor_schema_save_and_publish_all_widgets_with_csrf(workspace):
    w = workspace
    loaded = load(w)
    categories = {kind: 'Content' for kind in PACK} | {'tabs': 'Interactive', 'countdown': 'Marketing'}
    icons = []
    for kind, category in categories.items():
        schema = loaded['schema'][kind]
        assert schema['category'] == category
        assert schema['icon'] and schema['description'] and schema['insertable']
        assert schema['description'] == WIDGETS[kind].description
        icons.append(schema['icon'])
    assert len(icons) == len(set(icons))
    doc = document(*(default_block(kind, kind) for kind in PACK))
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


def test_countdown_server_clock_remaining_and_expiry():
    from app.site_widgets_pack2 import render_pack_widget

    clock = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
    block = default_block('countdown') | {'end_at': '2027-01-02T02:03:04+00:00', 'timezone': ''}
    markup = to_xml(render_pack_widget(block, now=lambda: clock))
    for unit, value in [('days', '01'), ('hours', '02'), ('minutes', '03'), ('seconds', '04')]:
        assert f'data-countdown-unit="{unit}" aria-hidden="true">{value}' in markup
    assert '1 days, 2 hours, 3 minutes, 4 seconds remaining' in markup
    assert 'aria-live="off"' in markup
    block['end_at'] = '2026-12-31T23:59:59+00:00'
    block['expired_message'] = 'This event has ended.'
    message = to_xml(render_pack_widget(block, now=clock))
    assert 'This event has ended.' in message
    assert 'data-countdown-expired="" hidden' not in message
    block['expired_behaviour'] = 'hide'
    hidden = to_xml(render_pack_widget(block, now=clock))
    assert 'hidden' in hidden.split('>')[0]

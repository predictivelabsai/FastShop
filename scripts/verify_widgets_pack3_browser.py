"""Verify commerce and marketing widgets in a disposable editor and storefront.

Run: uv run python scripts/verify_widgets_pack3_browser.py
No environment files, external services or stored browser sessions are used.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate
from verify_admin_v2_editor_browser import drag_to_canvas
from verify_widgets_pack1_browser import dismiss_consent, open_item, setting

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/playwright/widgets-pack3'
KINDS = ('featured_collection', 'pricing_table', 'comparison_table', 'trust_badges',
         'announcement_bar', 'promo_banner', 'steps', 'star_rating', 'contact_card')
PHOTO = '/static/img/storefront-object.jpg'


def fixtures():
    from sqlalchemy import select

    from app import content
    from app.db import SessionLocal
    from app.models import Collection, CollectionProduct, Discount, DiscountCode, Review, User
    from app.site_block_schema import default_block
    from app.site_catalog import create_catalog_product

    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == EMAIL))
        site = content.create_site(db, owner.id, 'Your studio', 'widgets-pack3')
        site.status = 'preview'
        page = content.create_page(db, site, 'Your shop information', '/pages/commerce-marketing',
                                   document={'version': 1, 'title': 'Your shop information', 'blocks': []})
        collection = Collection(tenant_id=site.tenant_id, title='Your collection', handle='your-collection',
                                description='A selection from your real catalogue.', image_url=PHOTO,
                                is_published=True)
        db.add(collection)
        db.flush()
        collection_page = content.create_page(db, site, collection.title, '/collections/your-collection',
                                              kind='collection')
        collection_page.collection_id = collection.id
        collection_page.published_json = collection_page.draft_json
        products = []
        for index, (name, price, image) in enumerate((
            ('Your everyday object', 1995, PHOTO),
            ('Your interior object', 3250, '/static/img/product-interior.jpg'),
            ('Your next object', 4705, PHOTO),
        )):
            product = create_catalog_product(db, site, {
                'name': name, 'slug': f'your-object-{index + 1}', 'subtitle': '',
                'description': 'Isolated browser verification catalogue item.', 'image_url': image,
                'category_slug': 'your-objects', 'category_name': 'Your objects',
                'variants': [{'name': 'Standard', 'price_minor': price}],
            })
            db.add(CollectionProduct(tenant_id=site.tenant_id, collection_id=collection.id,
                                     product_id=product.id, position=index))
            product_page = content.create_page(db, site, product.name, '/products/' + product.slug,
                                               kind='product')
            product_page.product_id = product.id
            product_page.published_json = product_page.draft_json
            products.append(product)
        for index, rating in enumerate((5, 4)):
            db.add(Review(tenant_id=site.tenant_id, product_id=products[0].id,
                          author_name=f'Browser reviewer {index + 1}', rating=rating,
                          title='Isolated approved review', body='Browser verification fixture.',
                          is_approved=True))
        db.add(Review(tenant_id=site.tenant_id, product_id=products[0].id, author_name='Pending reviewer',
                      rating=1, title='Unapproved fixture', body='', is_approved=False))
        discount = Discount(tenant_id=site.tenant_id, site_id=site.id, title='Browser verification offer',
                            value=10, is_active=True)
        db.add(discount)
        db.flush()
        code = DiscountCode(tenant_id=site.tenant_id, site_id=site.id,
                            discount_id=discount.id, code='YOURCODE')
        db.add(code)
        db.flush()
        nested = content.create_page(db, site, 'Your collection row', '/pages/nested-collection',
            document={'version': 1, 'title': 'Your collection row', 'blocks': [
                default_block('container') | {'children': [
                    default_block('featured_collection') | {'collection': collection.id, 'layout': 'scroll'},
                    default_block('steps'),
                ]},
            ]})
        nested.published_json = nested.draft_json
        result = site.id, page.id, {'collection': collection.id, 'product': products[0].id,
                                   'discount_code': code.id}
        db.commit()
        return result


def capture(page, name, *, full_page=False):
    page.evaluate('document.fonts.ready')
    if full_page:
        page.evaluate('window.scrollTo({top: 0, left: 0, behavior: "instant"})')
    if 'storefront' in name:
        page.locator('.fs-pack3-comparison-scroll').evaluate_all('elements => elements.forEach(element => { element.scrollLeft = 0; })')
    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'), 'Page horizontal overflow'
    page.screenshot(path=str(OUTPUT / (name + '.png')), full_page=full_page)


def touch_targets(page):
    for button in page.locator('.fs-pack3 button:visible, .fs-pack3 a:visible').all():
        bounds = button.bounding_box()
        assert bounds and bounds['width'] >= 44 and bounds['height'] >= 44, bounds


def journey(browser, base, site_id, page_id, resources):
    errors = []

    def watch(page):
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)

    desktop = browser.new_context(base_url=base, viewport={'width': 1440, 'height': 900},
                                  reduced_motion='reduce', permissions=['clipboard-read', 'clipboard-write'])
    page = desktop.new_page()
    watch(page)
    page.goto('/login?next=/admin/sites')
    page.get_by_label('Email address', exact=True).fill(EMAIL)
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign in', exact=True).click()
    page.wait_for_url('**/admin/sites')
    page.goto(f'/admin/sites/{site_id}/editor?page={page_id}')
    frame = page.locator('#editor-canvas').content_frame
    expect(frame.locator('.fs-editor-hint')).to_be_attached()
    page.locator('[data-mode="widgets"]').click()
    search = page.get_by_role('searchbox', name='Search widgets')
    for category in ('Commerce', 'Marketing'):
        page.locator('.widget-category', has_text=category).evaluate('element => element.scrollIntoView({block: "start"})')
        capture(page, 'desktop-picker-' + category.lower())
    search.fill('featured collection')
    tile = page.locator('[data-widget-type="featured_collection"]')
    expect(tile).to_be_visible()
    drag_to_canvas(page, tile)
    expect(frame.locator('[data-block-type="featured_collection"]')).to_be_attached()
    page.get_by_role('tab', name='Content', exact=True).click()
    setting(page, 'collection', resources['collection'])
    for kind in KINDS[1:]:
        page.locator('[data-mode="widgets"]').click()
        search.fill(kind.replace('_', ' '))
        tile = page.locator(f'[data-widget-type="{kind}"]')
        expect(tile).to_be_visible()
        tile.click()
        expect(frame.locator(f'[data-block-type="{kind}"]')).to_be_attached()
        page.get_by_role('tab', name='Content', exact=True).click()
        if kind == 'pricing_table':
            for index, (name, price) in enumerate((('Your first plan', 'EUR 19 / month'),
                                                  ('Your next plan', 'EUR 39 / month'))):
                open_item(page, index)
                setting(page, f'items.{index}.name', name)
                setting(page, f'items.{index}.price', price)
                setting(page, f'items.{index}.link', '/shop')
        elif kind == 'comparison_table':
            setting(page, 'options', 4)
            open_item(page)
            setting(page, 'items.0.feature', 'Your first feature')
            setting(page, 'items.0.cell_3', 'Your detail')
            setting(page, 'items.0.cell_4', 'yes')
        elif kind == 'announcement_bar':
            setting(page, 'dismissible', True)
            setting(page, 'interval', 3)
            open_item(page)
            setting(page, 'items.0.text', 'Your first announcement')
            page.locator('[data-repeat-add="items"]').click()
            open_item(page, 1)
            setting(page, 'items.1.text', 'Your next announcement')
            setting(page, 'items.1.link', '/shop')
        elif kind == 'promo_banner':
            setting(page, 'image', '/static/img/product-interior.jpg')
            setting(page, 'image_alt', 'Your promotional image')
            setting(page, 'link', '/shop')
            setting(page, 'discount_code', resources['discount_code'])
        elif kind == 'steps':
            open_item(page)
            setting(page, 'items.0.icon', 'bag')
            open_item(page, 1)
            setting(page, 'items.1.icon', 'box')
        elif kind == 'star_rating':
            setting(page, 'product', resources['product'])
            setting(page, 'scope', 'product')
            setting(page, 'link', '/shop')
        elif kind == 'contact_card':
            setting(page, 'address', 'Your street address\nYour town and postcode')
            setting(page, 'phone', '+372 5555 0101')
            setting(page, 'email', 'hello@example.test')
            setting(page, 'map_link', 'https://www.openstreetmap.org/')
            open_item(page)
            setting(page, 'items.0.day', 'Monday to Friday')
            setting(page, 'items.0.hours', '09:00 to 17:00')
    page.locator('#save').click()
    expect(page.locator('#save-status')).to_contain_text('Saved')
    page.reload()
    expect(frame.locator('[data-block-type]')).to_have_count(len(KINDS))
    expect(frame.locator('.fs-widget-star_rating')).to_contain_text('4.5')
    page.locator('#publish').click()
    page.locator('#confirm-publish').click()
    expect(page.locator('#save-status')).to_contain_text('Published')
    live_path = '/sites/widgets-pack3/pages/commerce-marketing'
    live = desktop.new_page()
    watch(live)
    live.goto(live_path)
    dismiss_consent(live)
    for kind in KINDS:
        expect(live.locator('.fs-widget-' + kind)).to_have_count(1)
    expect(live.locator('script[src*="widgets-pack3.js"]')).to_have_count(1)
    expect(live.locator('.fs-widget-featured_collection')).to_contain_text('19.95')
    expect(live.locator('.fs-widget-star_rating')).to_contain_text('4.5')
    expect(live.locator('.fs-widget-star_rating')).to_contain_text('2 reviews')
    expect(live.locator('.fs-pack3-plan[data-highlighted="true"]')).to_have_count(1)
    expect(live.locator('.fs-widget-comparison_table table')).to_be_visible()
    expect(live.locator('.fs-widget-comparison_table')).to_contain_text('Included')
    expect(live.locator('.fs-widget-comparison_table')).to_contain_text('Not included')
    expect(live.locator('.fs-widget-steps ol')).to_have_count(1)
    expect(live.locator('.fs-widget-contact_card address')).to_have_count(1)
    expect(live.locator('[data-announcement-pause]')).to_be_hidden()
    capture(live, 'desktop-storefront-all-widgets', full_page=True)
    for kind in ('featured_collection', 'pricing_table', 'comparison_table', 'trust_badges',
                 'steps', 'star_rating', 'contact_card'):
        live.locator('.fs-widget-' + kind).scroll_into_view_if_needed()
        capture(live, 'desktop-' + kind.replace('_', '-'))
    promo = live.locator('.fs-widget-promo_banner')
    promo.scroll_into_view_if_needed()
    promo.get_by_role('button', name='Copy code', exact=True).click()
    expect(promo.locator('[data-copy-status]')).to_contain_text('copied', ignore_case=True)
    expect(promo.locator('[data-copy-status]')).to_have_attribute('aria-live', 'polite')
    assert live.evaluate('navigator.clipboard.readText()') == 'YOURCODE'
    capture(live, 'desktop-promo-code-copied')
    # Denied Clipboard API access must leave a selected, readable code fallback.
    live.evaluate('Object.defineProperty(navigator, "clipboard", {value: undefined, configurable: true})')
    promo.get_by_role('button', name='Copy code', exact=True).click()
    expect(promo.locator('[data-copy-status]')).not_to_have_text('')
    assert live.evaluate('getSelection().toString()') == 'YOURCODE'
    mobile = browser.new_context(base_url=base, viewport={'width': 390, 'height': 844},
                                 reduced_motion='reduce', is_mobile=True, has_touch=True)
    small = mobile.new_page()
    watch(small)
    small.goto(live_path)
    dismiss_consent(small)
    capture(small, 'mobile-storefront-all-widgets', full_page=True)
    touch_targets(small)
    for kind in ('featured_collection', 'pricing_table', 'steps', 'star_rating', 'contact_card'):
        small.locator('.fs-widget-' + kind).scroll_into_view_if_needed()
        capture(small, 'mobile-' + kind.replace('_', '-'))
    table = small.locator('.fs-pack3-comparison-scroll')
    table.scroll_into_view_if_needed()
    assert table.evaluate('element => element.scrollWidth > element.clientWidth')
    table.evaluate('element => { element.scrollLeft = element.scrollWidth; }')
    assert table.evaluate('element => element.scrollLeft > 0')
    capture(small, 'mobile-comparison-table-scrolled')
    small.set_viewport_size({'width': 360, 'height': 800})
    assert small.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1')
    touch_targets(small)
    capture(small, 'mobile-360-storefront-all-widgets', full_page=True)
    small.set_viewport_size({'width': 390, 'height': 844})
    small.goto('/sites/widgets-pack3/pages/nested-collection')
    expect(small.locator('.fs-widget-container .fs-widget-featured_collection')).to_have_count(1)
    collection_row = small.locator('.fs-pack3-products[data-layout="scroll"]')
    assert collection_row.evaluate('element => element.scrollWidth > element.clientWidth')
    collection_row.evaluate('element => { element.scrollLeft = element.scrollWidth; }')
    assert collection_row.evaluate('element => element.scrollLeft > 0')
    capture(small, 'mobile-nested-collection-row-scrolled', full_page=True)
    small.set_viewport_size({'width': 360, 'height': 800})
    assert small.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1')
    plain_context = browser.new_context(base_url=base, java_script_enabled=False,
                                       viewport={'width': 390, 'height': 844})
    plain = plain_context.new_page()
    watch(plain)
    plain.goto(live_path)
    expect(plain.locator('[data-announcement-message]:visible')).to_have_count(2)
    expect(plain.locator('[data-announcement-pause]')).to_be_hidden()
    expect(plain.locator('[data-promo-code]')).to_have_text('YOURCODE')
    capture(plain, 'mobile-storefront-without-javascript', full_page=True)
    motion = browser.new_context(base_url=base, viewport={'width': 1440, 'height': 900},
                                 reduced_motion='no-preference')
    animated = motion.new_page()
    watch(animated)
    animated.goto(live_path)
    dismiss_consent(animated)
    animated.mouse.move(0, 0)
    announcement = animated.locator('[data-announcement]')
    announcement.scroll_into_view_if_needed()
    expect(announcement).to_have_attribute('data-active-message', '1', timeout=6500)
    capture(animated, 'desktop-announcement-rotating')
    announcement.get_by_role('link', name='Your next announcement', exact=True).focus()
    animated.mouse.move(0, 0)
    # A mouseleave must not restart rotation while keyboard focus remains inside.
    animated.wait_for_function('deadline => Date.now() >= deadline', arg=int(time.time() * 1000) + 3500)
    expect(announcement).to_have_attribute('data-active-message', '1')
    animated.locator('header a').first.focus()
    expect(announcement).to_have_attribute('data-active-message', '0', timeout=6500)
    pause = announcement.locator('[data-announcement-pause]')
    pause.click()
    expect(pause).to_have_attribute('aria-pressed', 'true')
    capture(animated, 'desktop-announcement-paused')
    animated.emulate_media(reduced_motion='reduce')
    expect(pause).to_be_hidden()
    animated.emulate_media(reduced_motion='no-preference')
    expect(pause).to_be_visible()
    announcement.locator('[data-announcement-dismiss]').click()
    expect(announcement).to_be_hidden()
    capture(animated, 'desktop-announcement-dismissed')
    animated.reload()
    expect(animated.locator('[data-announcement]')).to_be_hidden()
    # Storage denial must not break dismissal for this page.
    blocked = motion.new_page()
    watch(blocked)
    blocked.add_init_script('Object.defineProperty(window, "localStorage", {get() { throw new Error("Storage unavailable"); }});')
    blocked.goto(live_path)
    blocked.locator('[data-announcement-dismiss]').click()
    expect(blocked.locator('[data-announcement]')).to_be_hidden()
    plain_context.close()
    motion.close()
    mobile.close()
    desktop.close()
    assert not errors, errors
    return errors


def main():
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='fastshop-widgets-pack3-') as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, page_id, resources = fixtures()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                               log_level='error', access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError('Isolated widget server failed to start')
                thread.join(.05)
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel='chrome', headless=True)
                try:
                    errors = journey(browser, f'http://127.0.0.1:{port}', site_id, page_id, resources)
                finally:
                    browser.close()
            print(json.dumps({'status': 'passed', 'browser_errors': errors, 'screenshots': str(OUTPUT)}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == '__main__':
    main()

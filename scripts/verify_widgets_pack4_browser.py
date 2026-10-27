"""Verify showcase widgets with an isolated SQLite catalogue and browser workspace.

Run: uv run python scripts/verify_widgets_pack4_browser.py
No environment files, external services or browser session files are used.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate
from verify_admin_v2_editor_browser import drag_to_canvas
from verify_widgets_pack1_browser import dismiss_consent, open_item, setting

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/playwright/widgets-pack4'
KINDS = ('marquee', 'collage', 'flip_card', 'cta_box', 'video_lightbox', 'team',
         'pull_quote', 'button_group', 'shop_the_look', 'recently_viewed')
PHOTO = '/static/img/storefront-object.jpg'
SECOND_PHOTO = '/static/img/product-interior.jpg'


def fixtures():
    from sqlalchemy import select

    from app import content
    from app.db import SessionLocal
    from app.models import User
    from app.site_catalog import create_catalog_product

    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == EMAIL))
        site = content.create_site(db, owner.id, 'Your studio', 'widgets-pack4')
        site.status = 'preview'
        page = content.create_page(db, site, 'Your showcase', '/pages/showcase',
            document={'version': 1, 'title': 'Your showcase', 'blocks': []})
        products = []
        for index, (name, price, image) in enumerate((
                ('Your everyday object', 1995, PHOTO), ('Your interior object', 3250, SECOND_PHOTO))):
            product = create_catalog_product(db, site, {
                'name': name, 'slug': f'your-object-{index + 1}', 'subtitle': '',
                'description': 'Isolated browser verification catalogue item.', 'image_url': image,
                'category_slug': 'your-objects', 'category_name': 'Your objects',
                'variants': [{'name': 'Standard', 'price_minor': price}],
            })
            product_page = content.create_page(db, site, product.name, '/products/' + product.slug, 'product')
            product_page.product_id = product.id
            product_page.published_json = product_page.draft_json
            products.append(product.id)
        db.commit()
        return site.id, page.id, products


def capture(page, name, *, full_page=False):
    page.evaluate('document.fonts.ready')
    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'), 'Page horizontal overflow'
    if full_page:
        page.evaluate('window.scrollTo({top: 0, left: 0, behavior: "instant"})')
    page.screenshot(path=str(OUTPUT / (name + '.png')), full_page=full_page)


def journey(browser, base, site_id, page_id, products):
    errors, video_requests = [], []

    def watch(page):
        page.on('pageerror', lambda error: errors.append(str(error)))
        def console(message):
            # Only the deliberate video-host abort may emit this network diagnostic.
            expected = ('ERR_BLOCKED_BY_CLIENT' in message.text and any(
                host in message.location.get('url', '') for host in ('youtube', 'vimeo')))
            if message.type == 'error' and not expected:
                errors.append(message.text)
        page.on('console', console)

    def video_route(route):
        if urlsplit(route.request.url).hostname == '127.0.0.1':
            route.continue_()
        else:
            video_requests.append(route.request.url)
            route.abort(error_code='blockedbyclient')

    desktop = browser.new_context(base_url=base, viewport={'width': 1440, 'height': 900}, reduced_motion='reduce', service_workers='block')
    desktop.route('**/*', video_route)
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
    for index, kind in enumerate(KINDS):
        page.locator('[data-mode="widgets"]').click()
        page.get_by_role('searchbox', name='Search widgets').fill('call to action box' if kind == 'cta_box' else kind.replace('_', ' '))
        tile = page.locator(f'[data-widget-type="{kind}"]')
        expect(tile).to_be_visible()
        if index == 0:
            drag_to_canvas(page, tile)
        else:
            tile.click()
        expect(frame.locator(f'[data-block-type="{kind}"]')).to_be_attached()
        page.get_by_role('tab', name='Content', exact=True).click()
        if kind == 'collage':
            for item in range(3):
                open_item(page, item)
                setting(page, f'items.{item}.image', PHOTO if item == 0 else SECOND_PHOTO)
                setting(page, f'items.{item}.alt', 'Your collection image')
        elif kind == 'flip_card':
            open_item(page)
            setting(page, 'items.0.image', PHOTO)
            setting(page, 'items.0.body', 'Add a short description for the back of this card.')
        elif kind == 'cta_box':
            setting(page, 'image', SECOND_PHOTO)
            setting(page, 'link', '/shop')
            setting(page, 'secondary_button', 'Learn more')
            setting(page, 'secondary_link', '/pages/showcase')
        elif kind == 'video_lightbox':
            setting(page, 'url', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
            setting(page, 'poster', PHOTO)
        elif kind == 'team':
            open_item(page)
            setting(page, 'items.0.image', SECOND_PHOTO)
        elif kind == 'button_group':
            setting(page, 'stack_mobile', True)
            setting(page, 'full_width_mobile', True)
        elif kind == 'shop_the_look':
            setting(page, 'image', PHOTO)
            open_item(page)
            setting(page, 'items.0.product', products[0])
            setting(page, 'items.0.x', 50)
            setting(page, 'items.0.y', 45)
    page.locator('#save').click()
    expect(page.locator('#save-status')).to_contain_text('Saved')
    page.reload()
    expect(frame.locator('[data-block-type]')).to_have_count(len(KINDS))
    page.locator('#publish').click()
    page.locator('#confirm-publish').click()
    expect(page.locator('#save-status')).to_contain_text('Published')
    live_path = '/sites/widgets-pack4/pages/showcase'
    live = desktop.new_page()
    watch(live)
    live.goto(live_path)
    dismiss_consent(live)
    assert not video_requests, video_requests
    expect(live.locator('iframe')).to_have_count(0)
    expect(live.locator('.fs-widget-recently_viewed')).to_be_hidden()
    for kind in KINDS[:-1]:
        expect(live.locator('.fs-widget-' + kind)).to_be_visible()
    for index in (1, 2):
        live.goto(f'/sites/widgets-pack4/products/your-object-{index}')
    live.goto(live_path)
    expect(live.locator('.fs-widget-recently_viewed')).to_be_visible()
    capture(live, 'desktop-storefront-all-widgets', full_page=True)
    for kind in ('collage', 'cta_box', 'team', 'pull_quote', 'button_group', 'shop_the_look'):
        live.locator('.fs-widget-' + kind).scroll_into_view_if_needed()
        capture(live, 'desktop-' + kind.replace('_', '-'))
    flip = live.locator('.fs-widget-flip_card button').first
    flip.focus()
    flip.press('Enter')
    expect(flip).to_have_attribute('aria-expanded', 'true')
    capture(live, 'desktop-flip-card-keyboard')
    flip.press('Space')
    expect(flip).to_have_attribute('aria-expanded', 'false')
    video = live.locator('.fs-widget-video_lightbox a').first
    with live.expect_event('requestfailed', predicate=lambda request: 'youtube' in request.url) as blocked_video:
        video.click()
    assert blocked_video.value.failure == 'net::ERR_BLOCKED_BY_CLIENT'
    dialog = live.locator('.fs-widget-video_lightbox dialog')
    expect(dialog).to_be_visible()
    expect(dialog.locator('iframe')).to_have_count(1)
    assert video_requests, 'Video should load only after opening'
    close = dialog.locator('[data-video-close]')
    expect(close).to_be_focused()
    close.press('Shift+Tab')
    expect(dialog.locator('iframe')).to_be_focused()
    # The isolated aborted iframe owns its keyboard events; the native modal
    # still keeps the surrounding page inert while focus is inside that frame.
    close.focus()
    capture(live, 'desktop-video-lightbox-open')
    live.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(dialog.locator('iframe,video')).to_have_count(0)
    expect(video).to_be_focused()
    video.click()
    close.click()
    expect(dialog).not_to_be_visible()
    expect(video).to_be_focused()
    mobile = browser.new_context(base_url=base, viewport={'width': 390, 'height': 844},
                                 reduced_motion='reduce', is_mobile=True, has_touch=True, service_workers='block')
    mobile.route('**/*', video_route)
    small = mobile.new_page()
    watch(small)
    for index in (1, 2):
        small.goto(f'/sites/widgets-pack4/products/your-object-{index}')
        dismiss_consent(small)
    small.goto(live_path)
    expect(small.locator('.fs-widget-recently_viewed')).to_be_visible()
    expect(small.locator('.fs-widget-recently_viewed')).to_contain_text('Your everyday object')
    expect(small.locator('.fs-widget-recently_viewed')).to_contain_text('Your interior object')
    capture(small, 'mobile-storefront-all-widgets', full_page=True)
    for kind in ('collage', 'cta_box', 'team', 'pull_quote', 'button_group', 'recently_viewed'):
        small.locator('.fs-widget-' + kind).scroll_into_view_if_needed()
        capture(small, 'mobile-' + kind.replace('_', '-'))
    buttons = small.locator('.fs-widget-button_group a').all()
    first, second = buttons[0].bounding_box(), buttons[1].bounding_box()
    assert first and second and second['y'] >= first['y'] + first['height']
    hotspot = small.locator('.fs-widget-shop_the_look button').first
    hotspot.click()
    expect(hotspot).to_have_attribute('aria-expanded', 'true')
    capture(small, 'mobile-shop-the-look-popover')
    for button in small.locator('.fs-pack4 button:visible, .fs-pack4 a:visible').all():
        bounds = button.bounding_box()
        assert bounds and bounds['width'] >= 44 and bounds['height'] >= 44, bounds
    small.set_viewport_size({'width': 360, 'height': 800})
    capture(small, 'mobile-360-storefront-all-widgets', full_page=True)
    plain_context = browser.new_context(base_url=base, java_script_enabled=False, viewport={'width': 390, 'height': 844}, service_workers='block')
    plain_context.route('**/*', video_route)
    plain = plain_context.new_page()
    watch(plain)
    plain.goto(live_path)
    expect(plain.locator('.fs-widget-flip_card')).to_contain_text('Add a short description for the back of this card.')
    expect(plain.locator('.fs-widget-shop_the_look')).to_contain_text('Your everyday object')
    expect(plain.locator('.fs-widget-video_lightbox a')).to_have_attribute('href', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
    capture(plain, 'mobile-storefront-without-javascript', full_page=True)
    motion = browser.new_context(base_url=base, viewport={'width': 1440, 'height': 900}, reduced_motion='no-preference', service_workers='block')
    motion.route('**/*', video_route)
    animated = motion.new_page()
    watch(animated)
    animated.goto(live_path)
    dismiss_consent(animated)
    marquee = animated.locator('.fs-widget-marquee')
    marquee.scroll_into_view_if_needed()
    animated.mouse.move(0, 0)
    track = marquee.locator('.marquee-track')
    assert track.evaluate('el => getComputedStyle(el).animationPlayState') == 'running'
    capture(animated, 'desktop-marquee-running')
    marquee.hover()
    assert track.evaluate('el => getComputedStyle(el).animationPlayState') == 'paused'
    pause = marquee.locator('button').first
    pause.focus()
    animated.mouse.move(0, 0)
    assert track.evaluate('el => getComputedStyle(el).animationPlayState') == 'paused'
    pause.click()
    expect(pause).to_have_attribute('aria-pressed', 'true')
    assert track.evaluate('el => getComputedStyle(el).animationPlayState') == 'paused'
    capture(animated, 'desktop-marquee-paused')
    animated.emulate_media(reduced_motion='reduce')
    assert marquee.locator('[aria-hidden="true"]').count()
    assert track.evaluate('el => getComputedStyle(el).animationName') == 'none'
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
    with tempfile.TemporaryDirectory(prefix='fastshop-widgets-pack4-') as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, page_id, products = fixtures()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False))
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
                    errors = journey(browser, f'http://127.0.0.1:{port}', site_id, page_id, products)
                finally:
                    browser.close()
            print(json.dumps({'status': 'passed', 'browser_errors': errors, 'screenshots': str(OUTPUT)}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == '__main__':
    main()

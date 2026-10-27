"""Verify interactive widgets in a disposable editor and storefront.

Run: uv run python scripts/verify_widgets_pack2_browser.py
No environment files, external services or stored browser sessions are used.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate
from verify_admin_v2_editor_browser import drag_to_canvas
from verify_widgets_pack1_browser import dismiss_consent, open_item, setting

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output/playwright/widgets-pack2'
KINDS = ('tabs', 'countdown', 'stats', 'progress_bars', 'alert', 'table', 'map', 'social_links')
MAP_URL = 'https://www.openstreetmap.org/export/embed.html?bbox=0%2C0%2C1%2C1'


def fixtures():
    from sqlalchemy import select

    from app import content
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == EMAIL))
        site = content.create_site(db, owner.id, 'Your studio', 'widgets-pack2')
        site.status = 'preview'
        page = content.create_page(db, site, 'Your information', '/pages/interactive',
                                   document={'version': 1, 'title': 'Your information', 'blocks': []})
        db.commit()
        return site.id, page.id


def capture(page, name, *, full_page=False):
    page.evaluate('document.fonts.ready')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Page horizontal overflow'
    page.screenshot(path=str(OUTPUT / (name + '.png')), full_page=full_page)


def journey(browser, base, site_id, page_id):
    errors, map_requests = [], []

    def watch(page):
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text)
                if message.type == 'error' and 'ERR_FAILED' not in message.text else None)

    def maps(route):
        map_requests.append(route.request.url)
        route.abort()

    def context(**kwargs):
        result = browser.new_context(base_url=base, reduced_motion='reduce', **kwargs)
        result.route('https://www.openstreetmap.org/**', maps)
        result.route('https://www.google.com/maps/**', maps)
        return result

    desktop = context(viewport={'width': 1440, 'height': 900})
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
    search.fill('tabs')
    tile = page.locator('[data-widget-type="tabs"]')
    expect(tile).to_be_visible()
    capture(page, 'desktop-picker-filtered-by-search')
    drag_to_canvas(page, tile)
    expect(frame.locator('[data-block-type="tabs"]')).to_be_attached()
    for kind in KINDS[1:]:
        page.locator('[data-mode="widgets"]').click()
        search.fill({'stats': 'Statistics', 'alert': 'Notice'}.get(kind, kind.replace('_', ' ')))
        tile = page.locator(f'[data-widget-type="{kind}"]')
        expect(tile).to_be_visible()
        tile.click()
        expect(frame.locator(f'[data-block-type="{kind}"]')).to_be_attached()
        page.get_by_role('tab', name='Content', exact=True).click()
        if kind == 'countdown':
            setting(page, 'end_at', (datetime.now(UTC) + timedelta(days=2, minutes=3)).isoformat(timespec='seconds'))
        elif kind == 'alert':
            setting(page, 'dismissible', True)
        elif kind == 'table':
            setting(page, 'columns', 6)
            setting(page, 'caption', 'Your comparison table')
            for index in range(1, 7):
                setting(page, f'header_{index}', f'Your column {index}')
            open_item(page)
            for index in range(1, 7):
                setting(page, f'items.0.cell_{index}', f'Your comparison detail {index}')
            setting(page, 'first_column_header', True)
        elif kind == 'map':
            setting(page, 'embed_url', MAP_URL)
            setting(page, 'address', 'Your address')
        search.fill('')
    page.locator('[data-mode="widgets"]').click()
    search.fill('countdown')
    page.locator('[data-widget-type="countdown"]').click()
    page.get_by_role('tab', name='Content', exact=True).click()
    setting(page, 'end_at', '2020-01-01T00:00:00+00:00')
    setting(page, 'heading', 'Your past event')
    setting(page, 'expired_message', 'This event has ended.')
    page.locator('#save').click()
    expect(page.locator('#save-status')).to_contain_text('Saved')
    page.reload()
    expect(frame.locator('[data-block-type]')).to_have_count(len(KINDS) + 1)
    expect(frame.locator('[data-tab]').first).to_have_attribute('role', 'tab')
    frame.locator('[data-tab]').first.focus()
    frame.locator('[data-tab]').first.press('ArrowRight')
    expect(frame.locator('[data-tab]').nth(1)).to_have_attribute('aria-selected', 'true')
    page.locator('#publish').click()
    page.locator('#confirm-publish').click()
    expect(page.locator('#save-status')).to_contain_text('Published')
    live_path = '/sites/widgets-pack2/pages/interactive'
    live = desktop.new_page()
    watch(live)
    live.goto(live_path)
    dismiss_consent(live)
    for kind in KINDS:
        expect(live.locator('.fs-widget-' + kind)).to_have_count(2 if kind == 'countdown' else 1)
    expect(live.locator('script[src*="widgets-pack2.js"]')).to_have_count(1)
    capture(live, 'desktop-storefront-all-widgets', full_page=True)
    tabs = live.locator('[data-tab]')
    tabs.first.focus()
    tabs.first.press('ArrowRight')
    expect(tabs.nth(1)).to_be_focused()
    expect(tabs.nth(1)).to_have_attribute('aria-selected', 'true')
    expect(tabs.first).to_have_attribute('tabindex', '-1')
    tabs.nth(1).press('Home')
    expect(tabs.first).to_be_focused()
    tabs.first.press('End')
    expect(tabs.last).to_be_focused()
    capture(live, 'desktop-tabs-keyboard-switched')
    countdown = live.locator('[data-countdown]').first
    digits = countdown.locator('[data-countdown-unit="seconds"]')
    before = digits.inner_text()
    expect(digits).not_to_have_text(before, timeout=3000)
    countdown.scroll_into_view_if_needed()
    capture(live, 'desktop-countdown-running')
    expired = live.locator('[data-countdown]').last
    expect(expired.locator('[data-countdown-expired]')).to_be_visible()
    expired.scroll_into_view_if_needed()
    capture(live, 'desktop-countdown-expired')
    assert not map_requests, map_requests
    map_widget = live.locator('[data-map]')
    expect(map_widget.locator('iframe')).to_have_count(0)
    map_widget.scroll_into_view_if_needed()
    capture(live, 'desktop-map-before-load')
    map_widget.get_by_role('button', name='Load map', exact=True).click()
    expect(map_widget.locator('iframe')).to_have_count(1)
    live.wait_for_function("document.querySelector('[data-map] iframe').src.includes('openstreetmap.org')")
    assert map_requests, 'Click must request the map provider'
    iframe = map_widget.locator('iframe')
    expect(iframe).to_have_attribute('loading', 'lazy')
    assert iframe.get_attribute('title') and iframe.get_attribute('sandbox')
    capture(live, 'desktop-map-after-load')
    notice = live.locator('[data-alert]')
    notice.get_by_role('button', name='Dismiss notice').click()
    expect(notice).to_be_hidden()
    capture(live, 'desktop-alert-dismissed')
    live.reload()
    expect(live.locator('[data-alert]')).to_be_hidden()
    mobile = context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    small = mobile.new_page()
    watch(small)
    small.goto(live_path)
    dismiss_consent(small)
    capture(small, 'mobile-storefront-all-widgets', full_page=True)
    for button in small.locator('[data-tab], [data-map-load], [data-alert-dismiss]').all():
        bounds = button.bounding_box()
        assert bounds and bounds['width'] >= 44 and bounds['height'] >= 44, bounds
    table = small.locator('.fs-pack2-table-scroll')
    table.scroll_into_view_if_needed()
    assert table.evaluate('element => element.scrollWidth > element.clientWidth')
    table.evaluate('element => { element.scrollLeft = element.scrollWidth; }')
    assert table.evaluate('element => element.scrollLeft > 0')
    capture(small, 'mobile-table-scrolled')
    small.set_viewport_size({'width': 360, 'height': 800})
    assert small.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    plain_context = context(java_script_enabled=False, viewport={'width': 390, 'height': 844})
    plain = plain_context.new_page()
    watch(plain)
    before_plain = len(map_requests)
    plain.goto(live_path)
    expect(plain.locator('[data-tab-panel]:visible')).to_have_count(2)
    expect(plain.locator('[data-map] iframe')).to_have_count(0)
    expect(plain.get_by_role('link', name='Open in maps, opens in new tab')).to_be_visible()
    assert len(map_requests) == before_plain
    capture(plain, 'mobile-storefront-without-javascript', full_page=True)
    plain_context.close()
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
    with tempfile.TemporaryDirectory(prefix='fastshop-widgets-pack2-') as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, page_id = fixtures()
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
                    errors = journey(browser, f'http://127.0.0.1:{port}', site_id, page_id)
                finally:
                    browser.close()
            print(json.dumps({'status': 'passed', 'browser_errors': errors, 'screenshots': str(OUTPUT)}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == '__main__':
    main()

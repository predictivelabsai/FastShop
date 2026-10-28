"""Verify Preferences consent using the isolated pack 4 browser harness."""

from __future__ import annotations

import json

import verify_widgets_pack4_browser as harness
from playwright.sync_api import expect

OUTPUT = harness.ROOT / 'output/playwright/consent-preferences'
original_fixtures = harness.fixtures


def fixtures():
    site_id, page_id, products = original_fixtures()
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import SitePage
    from app.site_block_schema import default_block

    with SessionLocal() as db:
        page = db.get(SitePage, page_id)
        page.published_json = {'version': 1, 'title': 'Your showcase', 'blocks': [
            default_block('heading') | {'heading': 'Your recently viewed products'},
            default_block('recently_viewed'),
        ]}
        home = db.scalar(select(SitePage).where(SitePage.site_id == site_id, SitePage.path == '/'))
        home.published_json = home.draft_json
        db.commit()
    return site_id, page_id, products


def journey(browser, base, site_id, page_id, products):
    errors = []
    recent = f'fastshop:recently-viewed:{site_id}'
    consent = f'fastshop-consent:{site_id}'
    live = '/sites/widgets-pack4/pages/showcase'
    product = '/sites/widgets-pack4/products/your-object-1'
    for device, viewport in [('desktop', {'width': 1440, 'height': 1000}),
                             ('mobile', {'width': 390, 'height': 844})]:
        context = browser.new_context(base_url=base, viewport=viewport, reduced_motion='reduce', service_workers='block')
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script('''
          window.recentAccess = [];
          for (const method of ['getItem', 'setItem']) {
            const original = Storage.prototype[method];
            Storage.prototype[method] = function(key, ...args) {
              if (key.startsWith('fastshop:recently-viewed:')) window.recentAccess.push(method);
              return original.call(this, key, ...args);
            };
          }
        ''')
        page.goto(live)
        banner = page.locator('#h-cookie')
        expect(banner).to_be_visible()
        banner.locator('summary').click()
        expect(page.locator('#consent-preferences')).not_to_be_checked()
        expect(page.locator('[data-recently-viewed]')).to_be_hidden()
        assert page.evaluate('window.recentAccess') == []
        harness.capture(page, device + '-banner-before-consent', full_page=True)
        # Legacy consent grants no Preferences, even with existing view history.
        page.evaluate('''([key, recent, id]) => {
          localStorage.setItem(key, JSON.stringify({version:1, analytics:false, marketing:false}));
          localStorage.setItem(recent, JSON.stringify([id]));
        }''', [consent, recent, products[1]])
        page.goto(product)
        expect(banner).to_be_hidden()
        assert page.evaluate('window.recentAccess') == []
        page.locator('[data-cookie-open]').click()
        banner.locator('details').evaluate('el => { el.open = true; }')
        expect(page.locator('#consent-preferences')).not_to_be_checked()
        page.locator('#consent-preferences').check()
        page.locator('button[data-consent="custom"]').click()
        assert page.evaluate('key => JSON.parse(localStorage.getItem(key))', recent)[0] == products[0]
        page.goto(live)
        widget = page.locator('[data-recently-viewed]')
        expect(widget).to_be_visible()
        expect(widget).to_contain_text('Your everyday object')
        widget.scroll_into_view_if_needed()
        harness.capture(page, device + '-recently-viewed-granted')
        page.locator('[data-cookie-open]').click()
        banner.locator('details').evaluate('el => { el.open = true; }')
        expect(page.locator('#consent-preferences')).to_be_checked()
        page.locator('#consent-preferences').uncheck()
        page.locator('button[data-consent="custom"]').click()
        expect(widget).to_be_hidden()
        assert page.evaluate('key => localStorage.getItem(key)', recent) is None
        assert widget.locator('a').count() == 0
        harness.capture(page, device + '-recently-viewed-withdrawn', full_page=True)
        # Accept all grants Preferences; Decline withdraws it and removes history.
        page.goto(product)
        page.locator('[data-cookie-open]').click()
        banner.locator('details').evaluate('el => { el.open = true; }')
        page.locator('button[data-consent="all"]').click()
        assert page.evaluate('key => JSON.parse(localStorage.getItem(key)).preferences', consent) is True
        assert page.evaluate('key => JSON.parse(localStorage.getItem(key))', recent)[0] == products[0]
        page.locator('[data-cookie-open]').click()
        banner.locator('details').evaluate('el => { el.open = true; }')
        page.locator('button[data-consent="none"]').click()
        assert page.evaluate('key => JSON.parse(localStorage.getItem(key)).preferences', consent) is False
        assert page.evaluate('key => localStorage.getItem(key)', recent) is None
        # Withdrawal must clear history even on a page without the widget script.
        page.locator('[data-cookie-open]').click()
        page.locator('button[data-consent="all"]').click()
        page.goto('/sites/widgets-pack4/')
        assert page.locator('script[src*="widgets-pack4.js"]').count() == 0
        page.locator('[data-cookie-open]').click()
        page.locator('button[data-consent="none"]').click()
        assert page.evaluate('key => localStorage.getItem(key)', recent) is None
        page.goto(product)
        # A second tab can withdraw consent while a populated widget is visible.
        page.locator('[data-cookie-open]').click()
        banner.locator('details').evaluate('el => { el.open = true; }')
        page.locator('button[data-consent="custom"]').click()
        page.locator('[data-cookie-open]').click()
        banner.locator('details').evaluate('el => { el.open = true; }')
        page.locator('#consent-preferences').check()
        page.locator('button[data-consent="custom"]').click()
        page.goto(live)
        expect(widget).to_be_visible()
        other = context.new_page()
        other.goto(live)
        other.locator('[data-cookie-open]').click()
        other.locator('button[data-consent="none"]').click()
        expect(widget).to_be_hidden()
        assert page.evaluate('key => localStorage.getItem(key)', recent) is None
        other.close()
        # A fragment that resolves after withdrawal must never restore product cards.
        page.evaluate('''([key, id]) => {
          localStorage.setItem(key, JSON.stringify([id]));
          window.originalFetch = window.fetch;
          window.fetch = (...args) => String(args[0]).includes('/widgets/recently-viewed')
            ? new Promise(resolve => { window.releaseRecent = () => resolve({ok:true,
                text:() => Promise.resolve('<a href="/late">Late product</a>')}); })
            : window.originalFetch(...args);
        }''', [recent, products[0]])
        page.locator('[data-cookie-open]').click()
        banner.locator('details').evaluate('el => { el.open = true; }')
        page.locator('#consent-preferences').check()
        page.locator('button[data-consent="custom"]').click()
        assert page.evaluate('typeof window.releaseRecent') == 'function'
        page.locator('[data-cookie-open]').click()
        page.locator('button[data-consent="none"]').click()
        page.evaluate('''async () => {
          window.releaseRecent();
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          window.fetch = window.originalFetch;
        }''')
        expect(widget).to_be_hidden()
        assert widget.locator('a').count() == 0
        assert page.evaluate('key => localStorage.getItem(key)', recent) is None
        # A page with no banner stays inert. The existing fallback remains supported.
        minimal = f'''<html><body><div data-recent-product="{products[0]}" data-site-id="{site_id}" data-required-consent="preferences"></div>
          <section data-recently-viewed data-site-id="{site_id}" data-required-consent="preferences"
          data-recently-viewed-url="/sites/widgets-pack4/widgets/recently-viewed" hidden>
          <div data-recently-viewed-content></div></section>
          <script src="/static/widgets-pack4.js" defer></script>{{fallback}}</body></html>'''
        page.route('**/consent-no-banner', lambda route, request, markup=minimal: route.fulfill(content_type='text/html', body=markup.replace('{fallback}', '')))
        page.goto('/consent-no-banner')
        assert page.locator('#h-cookie,[aria-label="Cookie preferences"]').count() == 0
        assert page.evaluate('window.recentAccess') == []
        fallback = f'<script src="/static/store-preferences.js" data-site="{site_id}" defer></script>'
        page.route('**/consent-fallback', lambda route, request, markup=minimal, script=fallback: route.fulfill(content_type='text/html', body=markup.replace('{fallback}', script)))
        page.goto('/consent-fallback')
        panel = page.locator('aside')
        expect(panel).to_be_hidden()
        page.get_by_role('button', name='Cookie preferences', exact=True).click()
        page.locator('#consent-preferences').check()
        panel.locator('[data-choice="custom"]').click()
        assert page.evaluate('key => JSON.parse(localStorage.getItem(key))', recent)[0] == products[0]
        page.get_by_role('button', name='Cookie preferences', exact=True).click()
        expect(page.locator('#consent-preferences')).to_be_checked()
        panel.locator('[data-choice="none"]').click()
        assert page.evaluate('key => localStorage.getItem(key)', recent) is None
        context.close()
    assert not errors, errors
    print(json.dumps({'consent_journeys': 2, 'scenarios_per_device': 10}))
    return errors


if __name__ == '__main__':
    harness.OUTPUT = OUTPUT
    harness.fixtures = fixtures
    harness.journey = journey
    harness.main()

"""Capture H24YOU guide evidence using only an isolated local fixture database."""

import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from app.config import settings


def main():
    if settings.db_url or not str(settings.data_dir).startswith('/tmp/fastshop-guide-') or settings.public_url != 'http://127.0.0.1:5047' or settings.xai_api_key:
        raise SystemExit('Use an isolated /tmp/fastshop-guide-* database, empty DB_URL/XAI_API_KEY and local port 5047.')
    from app.db import SessionLocal
    from app.main import app
    from app.models import Site
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == 'h24you'))
        site_id = site.id
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=5047, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(.1)
    if not server.started:
        raise RuntimeError('Local capture server did not start')
    out = Path('output/playwright/h24you-guide')
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome')
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(settings.public_url + '/') else route.abort())
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))

            def capture(name):
                page.evaluate('document.fonts.ready')
                page.screenshot(path=str(out / (name + '.png')))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), name

            page.goto(settings.public_url + '/sites/h24you/')
            page.locator('[data-consent=none]').click()
            for name, route in [('home', '/'), ('tablets', '/products/hydrogen-tablets'),
                ('science', '/pages/science'), ('learn', '/blogs/learn'), ('about', '/pages/about-us')]:
                page.goto(settings.public_url + '/sites/h24you' + route)
                capture(name)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(settings.public_url + '/sites/h24you/')
            capture('mobile-home')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.goto(settings.public_url + '/login')
            capture('login')
            page.locator('[name=email]').fill(settings.admin_email)
            page.locator('[name=password]').fill(settings.admin_password)
            page.get_by_role('button', name='Sign in', exact=True).click()
            page.goto(settings.public_url + '/admin/sites')
            capture('sites')
            base = settings.public_url + f'/admin/sites/{site_id}'
            for name, route in [('settings', ''), ('catalog', '/products'), ('media', '/media'), ('commerce', '/commerce')]:
                page.goto(base + route)
                capture(name)
            page.goto(base)
            page.locator('.e-page-row a').first.click()
            capture('page-editor')
            # Make only this disposable fixture private before demonstrating samples.
            with SessionLocal() as db:
                site = db.get(Site, site_id)
                site.status = 'draft'
                db.commit()
            page.goto(base + '/samples')
            page.get_by_role('button', name='Fill missing fields with sample data', exact=True).click()
            capture('merchant-details')
            page.goto(base + '/build')
            page.locator('[name=prompt]').fill('Business: H2 4 You sells hydrogen tablets and a branded Hydroxy Go bottle to the US market.')
            page.get_by_role('button', name='Update draft', exact=True).click()
            expect(page.locator('.b-brief')).to_contain_text('Hydroxy Go')
            page.locator('[name=prompt]').fill('Keep H24YOU light and airy: warm off-white #FAFAF7, near-black #0E1116, restrained logo blue, editorial typography. Change the selected hero draft only; do not publish.')
            capture('chat')
            page.set_viewport_size({'width': 390, 'height': 844})
            capture('mobile-chat')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.locator('[name=prompt]').fill('Shipping: 1000')
            page.get_by_role('button', name='Update draft', exact=True).click()
            expect(page.get_by_role('heading', name='Review merchant changes', exact=True)).to_be_visible()
            page.locator('.b-review').scroll_into_view_if_needed()
            capture('merchant-review')
            page.goto(base + '/build?view=design')
            capture('design')
            page.goto(base + '/demo')
            page.get_by_role('button', name='Start private demo', exact=True).click()
            card = page.locator('.e-card').filter(has=page.get_by_role('heading', name='Sample tea · Original', exact=True))
            card.locator('summary').click()
            card.locator('[name=name]').fill('H24YOU training tablets — PLACEHOLDER')
            card.get_by_role('button', name='Save sample product', exact=True).click()
            card = page.locator('.e-card').filter(has=page.get_by_role('heading', name='H24YOU training tablets — PLACEHOLDER', exact=True))
            card.locator('[name=subscription]').select_option('on')
            card.get_by_role('button', name='Set bag quantity', exact=True).click()
            page.get_by_role('link', name='Checkout', exact=True).click()
            page.get_by_label('I authorize simulated monthly deliveries for recurring items').check()
            page.get_by_role('button', name='Calculate simulated total', exact=True).click()
            page.get_by_role('heading', name='Review simulated order', exact=True).scroll_into_view_if_needed()
            capture('demo-checkout')
            page.get_by_role('button', name='Simulate approved payment', exact=True).click()
            page.get_by_role('button', name='Send local demo sign-in', exact=True).click()
            page.get_by_role('button', name='Confirm demo login', exact=True).click()
            page.get_by_role('link', name='My account', exact=True).click()
            page.get_by_role('heading', name='Order history', exact=True).scroll_into_view_if_needed()
            capture('demo-account')
            page.get_by_role('heading', name='Demo subscriptions', exact=True).scroll_into_view_if_needed()
            capture('demo-subscriptions')
            page.get_by_role('link', name='Tracking', exact=True).click()
            capture('demo-tracking')
            page.get_by_role('link', name='Local inbox', exact=True).click()
            capture('demo-inbox')
            browser.close()
        assert not errors, errors
        print({'status': 'passed', 'captures': len(list(out.glob('*.png'))), 'output': str(out), 'mode': 'local guided fixture'})
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == '__main__':
    main()

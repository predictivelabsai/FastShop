"""Production guide acceptance: read-only H24YOU plus an opt-in private test site.

Loads credentials only inside this process. No browser storage state is saved.
Never publishes the private fixture or invokes checkout/email provider actions.
"""

import argparse
import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://shop.fastsme.com'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-private-fixture', action='store_true')
    parser.add_argument('--exercise-chat', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'creds/fastshop-admin.json'
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise SystemExit('Requires a non-symlink 0600 credential file.')
    creds = json.loads(path.read_text())
    if creds['login_url'] != BASE + '/login':
        raise SystemExit('Credential target mismatch.')
    out = ROOT / 'output/playwright/production-guide-verification'
    out.mkdir(parents=True, exist_ok=True)
    report = {'checked_at': datetime.now(UTC).isoformat(), 'base': BASE,
        'public_routes': [], 'admin_routes': [], 'checks': [], 'errors': []}
    routes = ['/', '/shop', '/collections/hydrogen-tablets', '/collections/hydrogen-water-bottles',
        '/products/hydrogen-tablets', '/products/hydroxy-go', '/pages/science', '/blogs/learn',
        '/blogs/learn/what-is-molecular-hydrogen', '/blogs/learn/timing-and-consistency',
        '/blogs/learn/how-to-read-hydrogen-research', '/pages/about-us', '/pages/contact',
        '/pages/terms-and-conditions', '/pages/privacy-policy', '/pages/faq', '/pages/returns-and-refunds']
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for route in ['/', '/healthz', '/readyz', '/static/site-workspace.js', '/static/site-preview.js', '/static/site-theme.css']:
            response = client.get(BASE + route)
            assert response.status_code == 200, route
            report['checks'].append(route + ': 200')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome')
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        page = context.new_page()
        page.set_default_timeout(20000)
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        for device, width, height in [('desktop', 1440, 1000), ('tablet', 834, 1112), ('mobile', 390, 844)]:
            page.set_viewport_size({'width': width, 'height': height})
            for route in routes:
                response = page.goto(BASE + '/sites/h24you' + route)
                assert response.status == 200, route
                if page.locator('[data-consent=none]').is_visible():
                    page.locator('[data-consent=none]').click()
                assert page.locator('h1').count() == 1, route
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (device, route)
                report['public_routes'].append({'device': device, 'path': route, 'status': response.status})
                if route in ['/', '/products/hydrogen-tablets', '/pages/science', '/blogs/learn']:
                    filename = (route.strip('/').replace('/', '-') or 'home') + '.png'
                    page.screenshot(path=str(out / (device + '-' + filename)))
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.goto(BASE + '/login?next=/admin/sites')
        expect(page.get_by_role('link', name='Continue with Google', exact=True)).to_be_visible()
        expect(page.locator('[name=password]')).to_be_visible()
        page.screenshot(path=str(out / 'admin-login-options.png'))
        page.locator('[name=email]').fill(creds['email'])
        page.locator('[name=password]').fill(creds['password'])
        page.get_by_role('button', name='Sign in', exact=True).click()
        page.wait_for_url(BASE + '/admin/sites')
        report['checks'].append('Admin password login succeeded; Google option retained')
        card = page.locator('.e-card').filter(has=page.get_by_role('heading', name='H2 4 You', exact=True))
        card.get_by_role('link', name='Open editor →', exact=True).click()
        site_base = page.url
        for route in ['', '/build', '/build?view=design', '/products', '/commerce', '/samples']:
            response = page.goto(site_base + route)
            assert response.status == 200, route
            assert 'FastShop' in page.inner_text('body')
            report['admin_routes'].append({'path': route, 'status': response.status})
            if route == '/build':
                expect(page.frame_locator('.b-preview').locator('.h-site')).to_be_visible()
                page.screenshot(path=str(out / 'h24you-builder.png'))
        if args.write_private_fixture:
            slug = 'prod-check-' + uuid4().hex[:10]
            page.goto(BASE + '/admin/sites')
            page.locator('[name=name]').fill('Private production check — ' + slug)
            page.locator('[name=slug]').fill(slug)
            page.locator('[name=flow]').select_option('chat')
            page.locator('[name=samples]').check()
            page.get_by_role('button', name='Create site', exact=True).click()
            expect(page.locator('.b-preview')).to_be_visible()
            fixture = page.url.removesuffix('/build')
            report['private_fixture'] = {'slug': slug, 'editor': fixture, 'published': False}
            page.get_by_role('link', name='Design controls', exact=True).click()
            page.locator('[name=accent]').fill('#26543d')
            page.get_by_role('button', name='Save design', exact=True).click()
            expect(page.locator('[name=accent]')).to_have_value('#26543d')
            page.get_by_role('button', name='Undo latest change', exact=True).click()
            report['checks'].append('Private fixture: classical design save and undo')
            if args.exercise_chat:
                page.goto(fixture + '/build')
                page.locator('[name=prompt]').fill('Set only the brand tagline to "Private production verification" using a brand draft operation. Do not change anything else, propose commerce changes, or publish.')
                page.get_by_role('button', name='Update draft', exact=True).click()
                expect(page.locator('[data-builder-status]')).to_have_text('Draft updated. Preview refreshed.', timeout=60000)
                page.goto(fixture)
                expect(page.locator('[name=tagline]')).to_have_value('Private production verification')
                report['checks'].append('Configured production LLM: requested tagline persisted in private draft')
            for device, width, height in [('desktop', 1440, 1000), ('mobile', 390, 844)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.goto(fixture + '/build')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                page.screenshot(path=str(out / (device + '-private-builder.png')))
            response = context.request.get(BASE + '/sites/' + slug + '/')
            assert response.status == 404, 'Test fixture became public'
            report['checks'].append('Private test site remains inaccessible at public storefront URL')
        browser.close()
    assert not report['errors'], report['errors']
    report['status'] = 'passed'
    (out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

"""Public Liquid hooks preserve native commerce and isolate the platform shell."""
import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app import (
    customer_routes,
    site_routes,
    store_checkout_routes,
    storefront_ui,
    subscription_routes,
)
from app.models import Site
from tests.test_checkout_services import Gateway
from tests.test_liquid_bundle_commerce import variants_for
from tests.test_site_seed_liquid import liquid  # noqa: F401
from tests.test_us_commerce import DESTINATION


@pytest.fixture
def browser(liquid, monkeypatch):
    from app.main import app

    db, site, _ = liquid
    sessions = sessionmaker(bind=db.bind, expire_on_commit=False)
    for module in (site_routes, store_checkout_routes, customer_routes,
                   subscription_routes, storefront_ui):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(store_checkout_routes, "StripeGateway", lambda site: Gateway())
    with TestClient(app) as client:
        yield client, db, site


def token(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]


def shell(response, status=200):
    assert response.status_code == status
    for marker in ('data-pack="liquid"', 'metal-top-bars', 'metal-header', 'metal-footer',
                   'site-theme-liquid.css', 'site-liquid.js'):
        assert marker in response.text
    assert "FastShop" not in response.text
    assert 'platform.css' not in response.text
    assert response.text.count('<h1') == 1


@pytest.mark.parametrize("path,status", [("/cart", 200), ("/account", 200),
    ("/account/subscriptions", 200), ("/missing", 404),
    ("/checkout/missing", 404), ("/blog/category/missing", 404), ("/blog/tag/missing", 404), ("/account/verify/missing", 200)])
def test_public_liquid_shell(browser, path, status):
    client, _, site = browser
    shell(client.get('/sites/' + site.slug + path), status)


def test_cart_checkout_native_forms_and_error_shell(browser):
    client, db, site = browser
    base = '/sites/' + site.slug
    response = client.get(base + '/cart')
    for slug, subscription in [('hydrogen-tablets', 'on'), ('h2-4-you-one', 'off')]:
        variant = variants_for(db, site, slug)[0]
        response = client.post(base + '/cart/add', data={
            'csrf_token': token(response), 'variant_id': variant.id,
            'quantity': '1', 'subscription': subscription})
        shell(response)
    assert 'cart-stepper' in response.text and 'cart-shipping-track' in response.text
    assert 'Remove' in response.text and 'Subtotal' in response.text
    assert '<del>$28.00</del>' in response.text
    assert response.headers['content-security-policy'] == "frame-ancestors 'self'"
    summary = client.get(base + '/cart/summary')
    assert summary.json() == {'count': 2}
    response = client.get(base + '/checkout')
    shell(response)
    version = re.search(r'name="version" value="(\d+)"', response.text)[1]
    response = client.post(base + '/checkout', data=DESTINATION | {
        'csrf_token': token(response), 'version': version, 'full_name': 'Test Shopper',
        'email': 'shopper@example.test', 'subscription_consent': 'on'})
    shell(response)
    assert 'Review your order' in response.text
    assert response.headers['cache-control'] == 'private, no-store'
    shell(client.post(base + '/cart/update', data={}), 400)


def test_non_liquid_platform_is_unchanged(browser):
    client, db, site = browser
    site.published_settings_json = site.published_settings_json | {'theme_pack': 'kinetic'}
    db.commit()
    response = client.get('/sites/' + site.slug + '/cart')
    assert response.status_code == 200
    assert 'FastShop' in response.text and 'platform.css' in response.text
    assert 'data-pack="liquid"' not in response.text
    assert client.get('/sites/' + site.slug + '/missing').text == 'Page not found'
    assert db.scalar(select(Site).where(Site.id == site.id)) is not None


def test_authenticated_subscriptions_and_email_fragment_script(browser):
    from app import customer_services
    from app.models import CustomerChallenge

    client, db, site = browser
    base = '/sites/' + site.slug
    customer_services.request_email_challenge(db, site, 'reader@example.test', 'login', '127.0.0.1')
    db.commit()
    challenge = db.scalar(select(CustomerChallenge).where(CustomerChallenge.site_id == site.id))
    verify = client.get(base + '/account/verify/' + challenge.id)
    shell(verify)
    assert 'customer.js' in verify.text
    response = client.post(base + '/account/verify/' + challenge.id, data={
        'csrf_token': token(verify), 'token': customer_services.challenge_token(challenge)})
    shell(response)
    response = client.get(base + '/account/subscriptions')
    shell(response)
    assert 'Your subscriptions' in response.text and 'No subscriptions yet.' in response.text


def test_google_faces_are_exactly_scoped_and_local():
    from pathlib import Path

    css = Path('static/site-theme-liquid.css').read_text(encoding='utf-8')
    assert css.count('@font-face') == 31
    assert 'format("truetype")' not in css
    assert 'font-optical-sizing' not in css
    faces = re.findall(r'@font-face\s*\{([^}]+)\}', css)
    for face in faces:
        assert "Liquid " in face
        path = re.search(r'url\(([^)]+)\)', face)[1]
        assert path.startswith('/static/fonts/liquid/google/')
        assert Path(path.lstrip('/')).is_file()
        if "Liquid Fraunces" in face and 'font-style: italic' in face:
            assert 'font-weight: 600' not in face
    assert not list(Path('static/fonts/liquid').glob('*.ttf'))

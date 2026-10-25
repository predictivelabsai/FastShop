"""Public Liquid hooks preserve native commerce and isolate the platform shell."""
import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app import (
    customer_routes,
    site_context,
    site_routes,
    store_checkout_routes,
    storefront_ui,
    subscription_routes,
)
from app.models import CheckoutAttempt, CommerceQuote, Site
from tests.test_checkout_services import Gateway
from tests.test_liquid_bundle_commerce import variants_for
from tests.test_site_seed_liquid import liquid  # noqa: F401
from tests.test_us_commerce import DESTINATION


@pytest.fixture
def browser(liquid, monkeypatch):
    from app.main import app

    db, site, _ = liquid
    sessions = sessionmaker(bind=db.bind, expire_on_commit=False)
    for module in (site_context, site_routes, store_checkout_routes, customer_routes,
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


@pytest.mark.parametrize("mapped_host", [False, True])
def test_liquid_404_message_and_recovery_links(browser, mapped_host):
    client, db, site = browser
    site.hostname, site.status = 'liquid.example.test', 'published'
    site.published_settings_json = site.published_settings_json | {'sandbox_demo': True}
    db.commit()
    base = '' if mapped_host else '/sites/' + site.slug
    response = client.get(
        ('https://liquid.example.test' if mapped_host else '') + base + '/missing')
    shell(response, 404)
    main = re.search(r'<main\b.*?</main>', response.text, re.S)[0]
    assert main.count('Page not found') == 1
    assert 'The page you were looking for has moved or no longer exists.' in main
    assert 'Sandbox · No live payments' not in main
    assert 'href="' + base + '/shop" class="e-button metal-cta">Shop all</a>' in main
    assert 'href="' + base + '/" class="liquid-message-home">Back to home</a>' in main
    assert 'name="robots" content="noindex,nofollow"' in response.text
    assert response.headers['cache-control'] == 'private, no-store'
    if mapped_host:
        assert response.headers['x-robots-tag'] == 'noindex, nofollow'
    # The payment notice still belongs on commerce pages.
    assert 'Sandbox · No live payments' in client.get('/sites/' + site.slug + '/cart').text


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
    assert response.text.count('aria-label="Purchase type"') == 1
    assert 'action="' + base + '/cart/purchase"' in response.text
    assert "\u2014" not in response.text
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
    assert 'Quantity: 1' in response.text and 'text-muted-foreground' in response.text
    assert '\u2014' not in response.text
    assert response.headers['cache-control'] == 'private, no-store'
    # Existing snapshots keep their stored labels but render without em dashes.
    quote = db.scalar(select(CommerceQuote).where(CommerceQuote.site_id == site.id))
    quote.snapshot_json = quote.snapshot_json | {"lines": [line | {
        "name": line["name"].replace(" · ", " \u2014 ")}
        for line in quote.snapshot_json["lines"]]}
    db.commit()
    attempt = db.scalar(select(CheckoutAttempt).where(CheckoutAttempt.quote_id == quote.id))
    for state in ("prepared", "paid", "expired"):
        attempt.state = state
        db.commit()
        summary = client.get(base + '/checkout/' + attempt.id)
        shell(summary)
        assert '\u2014' not in summary.text
        assert 'Quantity: 1' in summary.text
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

    from app.models import Order, OrderLine, SiteOrder

    order = Order(tenant_id=site.tenant_id, channel_id=site.channel_id,
        number="LIQUID-LEGACY", idempotency_key="liquid-legacy-order",
        email="reader@example.test", currency="USD", subtotal_minor=2800, total_minor=2800)
    order.lines = [OrderLine(sku="LEGACY", product_name="H2 | 4 YOU Fizz \u2014 Unflavored",
        variant_name="Unflavored", quantity=1, unit_price_minor=2800, total_minor=2800)]
    db.add(order)
    db.flush()
    link = SiteOrder(tenant_id=site.tenant_id, site_id=site.id,
                     customer_id=challenge.customer_id, order_id=order.id)
    db.add(link)
    db.commit()
    response = client.get(base + '/account/orders/' + link.id)
    shell(response)
    assert '\u2014' not in response.text and 'Quantity: 1' in response.text
    assert '$28.00' in response.text and 'text-muted-foreground' in response.text


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


def test_custom_data_leaves_synthetic_cart_and_checkout_pages_intact(browser):
    from app import custom_data, store_preferences

    client, db, site = browser
    custom_data.save_definition(db, site, {"owner_type": "product", "namespace": "custom",
        "key": "caption", "name": "Product caption", "value_type": "single_line_text"})
    variant = variants_for(db, site, "h2-4-you-one")[0]
    custom_data.save_values(db, site, "product", variant.product_id,
                            {"custom.caption": "Product-only dynamic value"})
    from app.api import product_metafields
    from app.models import Product, SitePage
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id,
        SitePage.path == "/products/h2-4-you-one"))
    assert page.product_id is None and page.kind == "content"
    assert custom_data.product_id_for_page(db, site, page) == variant.product_id
    resolved = custom_data.resolve_dynamic_document(db, site, page,
        {"title": "Product template", "heading": "metafield:custom.caption"})
    assert resolved["heading"] == "Product-only dynamic value"
    product = db.get(Product, variant.product_id)
    assert product_metafields(db, product)[site.slug]["custom.caption"] == "Product-only dynamic value"
    db.commit()
    site.status = "published"
    store_preferences.save_preferences(db, site, {"analytics_id": "G-ABC1234567", "meta_pixel_id": "123456789"})
    db.commit()
    base = "/sites/" + site.slug
    cart = client.get(base + "/cart")
    shell(cart)
    assert "Product-only dynamic value" not in cart.text
    assert "store-preferences.js" not in cart.text
    cart = client.post(base + "/cart/add", data={"csrf_token": token(cart),
        "variant_id": variant.id, "quantity": "1", "subscription": "off"})
    shell(cart)
    checkout = client.get(base + "/checkout")
    shell(checkout)
    assert "US delivery &amp; checkout" in checkout.text
    assert "Product-only dynamic value" not in checkout.text
    assert "store-preferences.js" not in checkout.text
    assert "store-preferences.js" in client.get(base + "/products/h2-4-you-one").text

"""Inline-card checkout JSON endpoints: quotes, session secrets and hosted fallback."""
from functools import partial

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app import checkout_payments, store_checkout_routes
from app.checkout_payments import matches_quote
from app.main import app
from app.models import CheckoutAttempt, CommerceQuote, ShippingRate, ShippingZone, SiteCart
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_customer_routes import csrf
from tests.test_store_checkout_routes import HostedGateway
from tests.test_us_commerce import DESTINATION, shop  # noqa: F401


@pytest.fixture
def storefront(ready, monkeypatch):
    ready.site.status = "preview"
    ready.db.commit()
    sessions = sessionmaker(bind=ready.db.bind, expire_on_commit=False)
    gateway = HostedGateway()
    monkeypatch.setattr(store_checkout_routes, "SessionLocal", sessions)
    monkeypatch.setattr(store_checkout_routes, "StripeGateway", lambda site: gateway)
    monkeypatch.setattr(checkout_payments, "embedded_session", partial(
        checkout_payments.embedded_session, sessions=sessions,
        gateway_factory=lambda site: gateway))
    return ready, TestClient(app), "/sites/" + ready.site.slug, gateway


def start_cart(client, base, r, *, subscription=False):
    page = client.get(base + "/cart")
    page = client.post(base + "/cart/add", data={"csrf_token": csrf(page),
        "variant_id": r.variant.id, "quantity": "1", "subscription": "on" if subscription else "off"})
    return page


def quote_payload(page, email="embedded@example.test", **values):
    return {"csrf_token": csrf(page), "email": email, "full_name": "Embedded Buyer",
        "line1": DESTINATION["line1"], "line2": "", "city": DESTINATION["city"],
        "state": DESTINATION["state"], "postal_code": DESTINATION["postal_code"], **values}


def test_quote_endpoint_returns_json_totals_and_attempt(storefront):
    r, client, base, _ = storefront
    page = start_cart(client, base, r)
    response = client.post(base + "/checkout/quote", json=quote_payload(page))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["attempt_id"]
    assert body["currency"] == "USD" and body["total"] == 4200
    assert body["lines"][0]["name"] == "Test tablets · Original"
    assert body["lines"][0]["quantity"] == 1
    assert body["lines"][0]["subscription"] is False
    assert isinstance(body["shipping_rates"], list)


def test_quote_validation_and_csrf_rejections_are_json(storefront):
    r, client, base, _ = storefront
    page = start_cart(client, base, r)
    invalid = client.post(base + "/checkout/quote", json=quote_payload(page, postal_code="bad"))
    assert invalid.status_code == 422 and set(invalid.json()) == {"error"}
    rejected = client.post(base + "/checkout/quote", json=quote_payload(page, csrf_token="bad"))
    assert rejected.status_code == 422 and "session expired" in rejected.json()["error"].lower()


def test_subscription_quote_requires_consent(storefront):
    r, client, base, _ = storefront
    page = start_cart(client, base, r, subscription=True)
    response = client.post(base + "/checkout/quote", json=quote_payload(page))
    assert response.status_code == 422
    assert "consent" in response.json()["error"].lower()
    assert r.db.scalar(select(CheckoutAttempt)) is None


def test_requote_replaces_prepared_attempt_and_releases_reservation(storefront):
    r, client, base, _ = storefront
    zone = ShippingZone(tenant_id=r.site.tenant_id, site_id=r.site.id, name="California",
        countries_json=["US"], states_json=["CA"], is_active=True)
    r.db.add(zone)
    r.db.flush()
    cheap = ShippingRate(tenant_id=r.site.tenant_id, site_id=r.site.id, zone_id=zone.id,
        name="Standard", amount_minor=500)
    express = ShippingRate(tenant_id=r.site.tenant_id, site_id=r.site.id, zone_id=zone.id,
        name="Express", amount_minor=1200)
    r.db.add_all([cheap, express])
    r.db.commit()
    page = start_cart(client, base, r)
    first = client.post(base + "/checkout/quote", json=quote_payload(page)).json()
    first_quote = r.db.get(CheckoutAttempt, first["attempt_id"])
    assert r.db.get(CommerceQuote, first_quote.quote_id).shipping_minor == 500
    second = client.post(base + "/checkout/quote", json=quote_payload(page, shipping_rate_id=express.id)).json()
    assert first["attempt_id"] != second["attempt_id"]
    old = r.db.get(CheckoutAttempt, first["attempt_id"])
    new_attempt = r.db.get(CheckoutAttempt, second["attempt_id"])
    assert r.db.get(CommerceQuote, new_attempt.quote_id).shipping_minor == 1200
    cart = r.db.scalar(select(SiteCart))
    r.db.refresh(old)
    r.db.refresh(r.stock)
    assert old.state == "expired" and cart.checkout_id == second["attempt_id"]
    assert r.stock.allocated == 1


def test_session_endpoint_returns_client_secret_but_not_secret_key(storefront, monkeypatch):
    r, client, base, gateway = storefront
    page = start_cart(client, base, r)
    quote = client.post(base + "/checkout/quote", json=quote_payload(page)).json()
    with r.db.begin():
        attempt = r.db.get(CheckoutAttempt, quote["attempt_id"])
        saved_quote = r.db.get(CommerceQuote, attempt.quote_id)
        expected = {"id": "cs_test_embedded", "livemode": False, "mode": "payment",
            "ui_mode": "custom", "return_url": "https://us-test-brand.example/checkout/" + attempt.id,
            "client_reference_id": attempt.id, "metadata": {"site_id": r.site.id,
            "quote_id": saved_quote.id, "checkout_id": attempt.id}, "currency": "usd",
            "amount_total": saved_quote.total_minor, "status": "open", "payment_status": "unpaid",
            "client_secret": "cs_test_embedded_secret_fixture", "automatic_tax": {"status": "complete"},
            "total_details": {"amount_tax": saved_quote.tax_minor, "amount_shipping": saved_quote.shipping_minor}}
    prefix = "FASTSHOP_STRIPE_" + r.site.id.upper() + "_"
    monkeypatch.setenv(prefix + "SECRET_KEY", "sk_test_fake_fixture")
    monkeypatch.setenv(prefix + "PUBLISHABLE_KEY", "pk_test_fixture")
    gateway.create_checkout = lambda attempt_id, command: expected
    response = client.post(base + "/checkout/" + quote["attempt_id"] + "/session",
        headers={"X-CSRF-Token": csrf(page)})
    assert response.status_code == 200, response.text
    assert response.json() == {"client_secret": "cs_test_embedded_secret_fixture", "publishable_key": "pk_test_fixture"}
    assert "sk_test" not in response.text
    attempt = r.db.get(CheckoutAttempt, quote["attempt_id"])
    saved_quote = r.db.get(CommerceQuote, attempt.quote_id)
    assert matches_quote(expected, attempt, saved_quote)


def test_custom_session_matches_quote_requires_return_url(storefront):
    r, client, base, _ = storefront
    page = start_cart(client, base, r)
    quote_data = client.post(base + "/checkout/quote", json=quote_payload(page)).json()
    attempt = r.db.get(CheckoutAttempt, quote_data["attempt_id"])
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    result = {"livemode": False, "mode": "payment", "ui_mode": "custom", "return_url": "https://store.test/return",
        "client_reference_id": attempt.id, "metadata": {"site_id": r.site.id, "quote_id": quote.id},
        "currency": "usd", "amount_total": quote.total_minor,
        "automatic_tax": {"status": "complete"},
        "total_details": {"amount_tax": quote.tax_minor, "amount_shipping": quote.shipping_minor}}
    assert matches_quote(result, attempt, quote)
    result.pop("return_url")
    assert not matches_quote(result, attempt, quote)


def test_other_tenant_attempt_is_rejected(storefront):
    r, client, base, _ = storefront
    stranger = TestClient(app)
    response = stranger.post(base + "/checkout/foreign-attempt/session", headers={"X-CSRF-Token": csrf(stranger.get(base + "/cart"))})
    assert response.status_code == 422 and "not found" in response.json()["error"].lower()


def test_session_endpoint_falls_back_to_hosted_without_publishable_key(storefront):
    r, client, base, _ = storefront
    page = start_cart(client, base, r)
    quote = client.post(base + "/checkout/quote", json=quote_payload(page)).json()
    response = client.post(base + "/checkout/" + quote["attempt_id"] + "/session",
        headers={"X-CSRF-Token": csrf(page)})
    assert response.status_code == 200, response.text
    assert response.json() == {"fallback_url": base + "/checkout/" + quote["attempt_id"] + "/pay"}

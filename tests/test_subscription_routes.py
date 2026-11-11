import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app import commerce, customer_routes, customer_services, subscription_routes
from app.main import app
from app.models import CustomerChallenge, Product, ProductVariant, VariantChannelListing
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_customer_routes import csrf
from tests.test_subscriptions import contract  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


@pytest.fixture
def customer_session(contract, monkeypatch):
    r, sub = contract
    commerce.settings_for(r.db, r.site).customer_accounts_enabled = True
    r.db.commit()
    sessions = sessionmaker(bind=r.db.bind, expire_on_commit=False)
    monkeypatch.setattr(subscription_routes, "SessionLocal", sessions)
    monkeypatch.setattr(customer_routes, "SessionLocal", sessions)
    monkeypatch.setattr(customer_routes, "dispatch_mail", lambda message_id: False)
    client = TestClient(app)
    base = "/sites/" + r.site.slug
    customer_services.request_email_challenge(r.db, r.site, r.customer.email, "login", "fixture")
    r.db.commit()
    challenge = r.db.scalar(select(CustomerChallenge).where(CustomerChallenge.customer_id == r.customer.id))
    token = customer_services.challenge_token(challenge)
    page = client.get(base + "/account/verify/" + challenge.id)
    client.post(base + "/account/verify/" + challenge.id, data={"csrf_token": csrf(page), "token": token})
    return r, sub, client, base


def fields(page):
    return {"csrf_token": csrf(page), **{key: re.search(r'name="' + key + r'" value="([^"]+)"', page.text).group(1) for key in ("version", "request_key")}}


def test_customer_can_skip_pause_resume_and_cancel_own_subscription(customer_session):
    r, sub, client, base = customer_session
    path = base + "/account/subscriptions/" + sub.id
    page = client.get(path)
    assert page.status_code == 200 and "FastShop" in page.text
    assert page.headers["cache-control"] == "private, no-store"
    assert TestClient(app).get(path).status_code == 404
    assert client.post(path + "/pause", data={}).status_code == 400
    page = client.post(path + "/skip", data=fields(page))
    assert "2026-03-31" in page.text
    page = client.post(path + "/pause", data=fields(page))
    assert "Status: paused" in page.text
    page = client.post(path + "/resume", data=fields(page))
    assert "Status: active" in page.text
    page = client.post(path + "/cancel", data=fields(page) | {"confirm_cancel": "on"})
    assert "Status: cancelled" in page.text and "Update payment method" not in page.text


def test_customer_portal_tabs_copy_recommendations_and_csrf(customer_session):
    r, sub, client, base = customer_session
    path = base + "/account/subscriptions"
    r.site.published_settings_json = {**(r.site.published_settings_json or {}),
        "portal_copy": {"next_order": "Coming up", "welcome": "Hello, {name}"}}
    r.db.commit()
    page = client.get(path)
    assert page.status_code == 200
    for label in ("Coming up", "Subscriptions", "Order history", "Account", "Hello, Subscriber"):
        assert label in page.text
    assert "Recommended for you" not in page.text
    assert "Upcoming orders" not in page.text and "Send now" not in page.text
    assert "subscription-portal.css" in page.text
    for tab in ("subscriptions", "orders", "account"):
        assert f"?tab={tab}" in client.get(path + "?tab=" + tab).text

    source = r.variant.product
    recommended = Product(tenant_id=r.site.tenant_id, product_type_id=source.product_type_id,
        category_id=source.category_id, name="Extra eligible", slug="extra-eligible",
        is_published=True, status="active")
    r.db.add(recommended)
    r.db.flush()
    variant = ProductVariant(tenant_id=r.site.tenant_id, product_id=recommended.id,
        sku="EXTRA-ELIGIBLE", name="Extra option")
    r.db.add(variant)
    r.db.flush()
    r.db.add(VariantChannelListing(variant_id=variant.id, channel_id=r.site.channel_id,
        currency="USD", price_minor=3200))
    config = commerce.settings_for(r.db, r.site)
    config.subscription_product_ids_json = [*config.subscription_product_ids_json, recommended.id]
    config.product_tax_codes_json = {**config.product_tax_codes_json, recommended.id: "txcd_00000000"}
    r.db.commit()
    page = client.get(path)
    assert "Recommended for you" in page.text and "Extra eligible" in page.text

    endpoint = path + "/" + sub.id + "/skip"
    assert client.post(endpoint, data={"version": sub.version,
        "request_key": "missing-csrf-portal-1234"}).status_code == 400


def test_controls_remain_available_when_store_disabled(customer_session, monkeypatch):
    from app import site_context
    r, sub, client, base = customer_session
    commerce.settings_for(r.db, r.site).mode = "disabled"
    r.site.status = "draft"
    r.site.hostname = "offline-fixture.example.test"
    r.db.commit()
    monkeypatch.setattr(site_context, "SessionLocal", sessionmaker(bind=r.db.bind, expire_on_commit=False))
    host_client = TestClient(app, base_url="https://offline-fixture.example.test")
    assert "Send login code" in host_client.get("/account").text
    assert host_client.get("/cart").status_code == 404
    page = client.get(base + "/account")
    assert page.status_code == 200 and "Manage your subscriptions" in page.text
    path = base + "/account/subscriptions/" + sub.id
    page = client.get(path)
    page = client.post(path + "/cancel", data=fields(page) | {"confirm_cancel": "on"})
    assert page.status_code == 200 and "Status: cancelled" in page.text


def test_missed_delivery_review_requires_csrf_and_owned_cycle(customer_session, monkeypatch):
    from functools import partial

    from app import store_checkout_routes, subscription_recovery, subscription_renewals
    from tests.test_subscription_renewals import RenewalGateway, options, prepare

    r, sub, client, base = customer_session
    r.site.status = "preview"
    r.db.commit()
    gateway = RenewalGateway()
    gateway.outcome = "requires_action"
    cycle = prepare(r, sub, gateway)
    subscription_renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway))
    sessions = sessionmaker(bind=r.db.bind, expire_on_commit=False)
    monkeypatch.setattr(store_checkout_routes, "SessionLocal", sessions)
    monkeypatch.setattr(subscription_recovery, "start", partial(subscription_recovery.start,
        sessions=sessions, gateway_factory=lambda site: gateway))
    path = base + "/account/subscriptions/" + sub.id
    page = client.get(path)
    assert "Review missed delivery" in page.text
    form = fields(page) | {"cycle_id": cycle.id}
    assert client.post(path + "/recover", data=form | {"csrf_token": "wrong"}).status_code == 400
    assert client.post(path + "/recover", data=form | {"cycle_id": "foreign-cycle"}).status_code == 400
    response = client.post(path + "/recover", data=form)
    assert response.status_code == 200 and "One-time delivery recovery" in response.text
    assert "does not resume" in response.text and "FastShop" in response.text
    assert TestClient(app).get(str(response.url)).status_code == 404
    assert "Continue delivery recovery" in client.get(path).text

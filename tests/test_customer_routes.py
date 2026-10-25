import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app import commerce, customer_services
from app.config import settings
from app.content import create_site, site_pages
from app.db import SessionLocal
from app.main import app
from app.models import (
    CustomerChallenge,
    MarketingConsent,
    Order,
    ShipmentEvent,
    ShopCustomer,
    Site,
    SiteOrder,
    User,
)


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


@pytest.fixture
def customer_site(monkeypatch):
    import app.customer_routes as routes
    monkeypatch.setattr(routes, "dispatch_mail", lambda message_id: False)
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = create_site(db, owner.id, "Customer route test", "customer-" + uuid4().hex[:10])
        site.status = "preview"
        home = next(page for page in site_pages(db, site) if page.path == "/")
        home.published_json = home.draft_json
        commerce.settings_for(db, site, create=True).mode = "sandbox"
        db.commit()
    return site, TestClient(app), "/sites/" + site.slug


def verified_login(site, client, base, email="shopper@example.test"):
    with SessionLocal() as db:
        commerce.settings_for(db, site).customer_accounts_enabled = True
        db.commit()
    response = client.get(base + "/account")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    # The retained endpoint supports in-flight login challenges without a public form.
    response = client.post(base + "/account/login", data={"csrf_token": csrf(client.get(base + "/")), "email": email})
    assert response.status_code == 200
    with SessionLocal() as db:
        challenge = db.scalar(select(CustomerChallenge).where(CustomerChallenge.site_id == site.id, CustomerChallenge.purpose == "login"))
        challenge_id, token = challenge.id, customer_services.challenge_token(challenge)
    verification = client.get(base + "/account/verify/" + challenge_id)
    with SessionLocal() as db:
        assert db.get(CustomerChallenge, challenge_id).consumed_at is None
    response = client.post(base + "/account/verify/" + challenge_id, data={"csrf_token": csrf(verification), "token": token})
    assert response.status_code == 200
    assert email in response.text
    return response


def test_accounts_default_off_with_informational_404(customer_site):
    _, client, base = customer_site
    response = client.get(base + "/account")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "Customer accounts are not available for this store" in response.text
    assert f'href="{base}/"' in response.text and "Back to store" in response.text
    assert "<form" not in response.text
    assert "/account/login" not in response.text
    assert "Sign in with an email link" not in response.text
    assert "Email me a sign-in link" not in response.text


@pytest.mark.parametrize("mode", ["sandbox", "disabled"])
def test_storefront_header_has_only_bag_action(customer_site, mode):
    site, client, base = customer_site
    with SessionLocal() as db:
        commerce.settings_for(db, site).mode = mode
        db.commit()
    response = client.get(base + "/")
    assert response.status_code == 200
    actions = re.search(r'<div class="h-header-actions">(.*?)</div>', response.text, re.S).group(1)
    assert "Account" not in actions and "/account" not in actions
    if mode == "sandbox":
        assert f'href="{base}/cart"' in actions and "Bag" in actions
    else:
        assert "Bag (0)" in actions and "data-commerce-notice" in actions


def test_customer_login_does_not_grant_merchant_access_and_logout_revokes_session(customer_site):
    site, client, base = customer_site
    response = verified_login(site, client, base)
    for label in ("Your orders", "Subscriptions", "Manage your subscriptions", "Email preferences", "Sign out"):
        assert label in response.text
    assert "Customer accounts are not available" not in response.text
    admin = client.get("/admin", follow_redirects=False)
    assert admin.status_code == 303 and "/login" in admin.headers["location"]
    response = client.post(base + "/account/logout", data={"csrf_token": csrf(response)})
    assert "Sign in with an email link" in response.text


def test_newsletter_requires_consent_and_confirmation_without_logging_customer_in(customer_site):
    site, client, base = customer_site
    page = client.get(base + "/")
    assert 'name="marketing_consent"' in page.text
    form = {"csrf_token": csrf(page), "email": "subscriber@example.test"}
    assert client.post(base + "/newsletter", data=form).status_code == 400
    assert client.post(base + "/newsletter", data=form | {"marketing_consent": "on"}).status_code == 200
    with SessionLocal() as db:
        assert not db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site.id))
        challenge = db.scalar(select(CustomerChallenge).where(CustomerChallenge.site_id == site.id))
        challenge_id, token = challenge.id, customer_services.challenge_token(challenge)
    page = client.get(base + "/account/verify/" + challenge_id)
    assert client.post(base + "/account/verify/" + challenge_id, data={"csrf_token": csrf(page), "token": token}).status_code == 200
    assert "Customer accounts are not available for this store" in client.get(base + "/account").text
    with SessionLocal() as db:
        commerce.settings_for(db, site).customer_accounts_enabled = True
        db.commit()
    assert "Sign in with an email link" in client.get(base + "/account").text
    with SessionLocal() as db:
        consent = db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site.id))
        customer = db.get(ShopCustomer, consent.customer_id)
        customer_id, optout = customer.id, customer_services.unsubscribe_token(site, customer)
        assert consent.status == "subscribed"
        # Withdrawing consent remains possible after the merchant takes a store offline.
        db.get(Site, site.id).status = "draft"
        commerce.settings_for(db, site).mode = "disabled"
        db.commit()
    page = client.get(base + "/unsubscribe/" + customer_id)
    with SessionLocal() as db:
        assert db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site.id)).status == "subscribed"
    response = client.post(base + "/unsubscribe/" + customer_id, data={"csrf_token": csrf(page), "token": optout})
    assert response.status_code == 200 and "Unsubscribed" in response.text


def test_order_tracking_is_owned_by_customer_not_guessed_order_number(customer_site):
    site, client, base = customer_site
    verified_login(site, client, base)
    with SessionLocal() as db:
        customer = customer_services.customer_for(db, site, "shopper@example.test")
        other = customer_services.customer_for(db, site, "other@example.test", create=True)
        other.verified_at = datetime.now(UTC)
        links = []
        for owner in [customer, other]:
            order = Order(tenant_id=site.tenant_id, channel_id=site.channel_id, number="TEST-" + uuid4().hex[:10],
                idempotency_key=uuid4().hex, email=owner.email, currency="USD", subtotal_minor=2995, total_minor=3200,
                tax_minor=205, payment_status="paid")
            db.add(order)
            db.flush()
            link = SiteOrder(tenant_id=site.tenant_id, site_id=site.id, customer_id=owner.id, order_id=order.id)
            db.add(link)
            db.flush()
            links.append(link.id)
        db.add(ShipmentEvent(tenant_id=site.tenant_id, site_id=site.id, site_order_id=links[0],
            carrier="Test carrier", tracking_number="TEST123", status="in_transit", note="Fixture only"))
        db.commit()
    response = client.get(base + "/account/orders/" + links[0])
    assert response.status_code == 200 and "TEST123" in response.text
    assert "Sales tax: $2.05" in response.text
    assert client.get(base + "/account/orders/" + links[1]).status_code == 404
    account = client.get(base + "/account")
    assert base + "/account/orders/" + links[0] in account.text
    assert base + "/account/orders/" + links[1] not in account.text


@pytest.mark.parametrize("method,path", [
    ("GET", "/account"),
    ("POST", "/account/login"),
    ("POST", "/account/logout"),
    ("GET", "/account/orders/unknown"),
    ("GET", "/account/verify/unknown"),
    ("POST", "/account/verify/unknown"),
    ("GET", "/account/subscriptions"),
    ("GET", "/account/subscriptions/unknown"),
    ("POST", "/account/subscriptions/unknown/cancel"),
    ("POST", "/account/subscriptions/unknown/payment"),
    ("POST", "/account/subscriptions/unknown/recover"),
    ("GET", "/account/subscriptions/unknown/payment/unknown"),
    ("POST", "/account/subscriptions/unknown/payment/unknown"),
])
def test_all_account_surfaces_are_gated_before_session_or_csrf(customer_site, method, path):
    _, client, base = customer_site
    response = client.request(method, base + path, follow_redirects=False)
    assert response.status_code == 404
    assert "Customer accounts are not available for this store" in response.text
    assert "<form" not in response.text


def test_existing_login_challenge_is_not_consumed_when_accounts_off(customer_site):
    site, client, base = customer_site
    with SessionLocal() as db:
        mail = customer_services.request_email_challenge(db, site, "pending@example.test", "login", "fixture")
        challenge = db.get(CustomerChallenge, mail.challenge_id)
        challenge_id = challenge.id
        token = customer_services.challenge_token(challenge)
        db.commit()
    path = base + "/account/verify/" + challenge_id
    assert client.get(path).status_code == 404
    response = client.post(path, data={"token": token, "csrf_token": csrf(client.get(base + "/"))})
    assert response.status_code == 404
    with SessionLocal() as db:
        assert db.get(CustomerChallenge, challenge_id).consumed_at is None


def test_header_and_anonymous_login_form_return_only_when_opted_in(customer_site):
    site, client, base = customer_site
    with SessionLocal() as db:
        commerce.settings_for(db, site).customer_accounts_enabled = True
        db.commit()
    assert f'href="{base}/account"' in client.get(base + "/").text
    page = client.get(base + "/account")
    assert page.status_code == 200
    assert "Sign in with an email link" in page.text
    assert f'action="{base}/account/login"' in page.text


def test_disabling_accounts_also_gates_existing_customer_session(customer_site):
    site, client, base = customer_site
    verified_login(site, client, base)
    with SessionLocal() as db:
        commerce.settings_for(db, site).customer_accounts_enabled = False
        db.commit()
    assert client.get(base + "/account").status_code == 404
    assert client.get(base + "/account/orders/unknown").status_code == 404


def test_newsletter_challenge_for_another_site_cannot_bypass_gate(customer_site):
    site, client, base = customer_site
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        other = create_site(db, owner.id, "Other newsletter", "newsletter-" + uuid4().hex[:10])
        commerce.settings_for(db, other, create=True).mode = "sandbox"
        mail = customer_services.request_email_challenge(
            db, other, "foreign@example.test", "newsletter", "fixture", consent=True
        )
        challenge_id = mail.challenge_id
        db.commit()
    assert client.get(base + "/account/verify/" + challenge_id).status_code == 404
    assert client.post(base + "/account/verify/" + challenge_id, data={}).status_code == 404

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import commerce, customer_services
from app.db import SessionLocal
from app.integrations.commerce_email import render_message
from app.models import CommerceMail, CustomerLoginCodeChallenge, ShopCustomer
from app.services import CommerceError
from tests.test_customer_routes import csrf, customer_site  # noqa: F401


def request_code(site, client, base, email="code@example.test", next_path=""):
    with SessionLocal() as db:
        commerce.settings_for(db, site).customer_accounts_enabled = True
        db.commit()
    page = client.get(base + "/account")
    return client.post(base + "/account/login/code", data={
        "csrf_token": csrf(page), "email": email, "next": next_path,
    })


def challenge_for(site, email):
    with SessionLocal() as db:
        return db.scalar(select(CustomerLoginCodeChallenge).where(
            CustomerLoginCodeChallenge.site_id == site.id,
            CustomerLoginCodeChallenge.email == email).order_by(CustomerLoginCodeChallenge.created_at.desc()))


def test_code_queued_encrypted_and_correct_code_signs_in(customer_site):
    site, client, base = customer_site
    with SessionLocal() as db:
        commerce.settings_for(db, site).customer_accounts_enabled = True
        db.commit()
    response = request_code(site, client, base, next_path="/account/subscriptions")
    assert response.status_code == 200 and "sign-in code will arrive" in response.text
    challenge = challenge_for(site, "code@example.test")
    with SessionLocal() as db:
        mail = db.scalar(select(CommerceMail).where(CommerceMail.kind == "login_code",
            CommerceMail.site_id == site.id))
        serialised = str(mail.reference_json) + challenge.token_hash + challenge.code_cipher
        assert customer_services.utc(challenge.expires_at) <= datetime.now(UTC) + timedelta(minutes=10, seconds=1)
        code = customer_services.decrypt_login_code(site, challenge)
        assert code.isdigit() and len(code) == 6 and code not in serialised
        assert mail.kind == "login_code"
        assert mail.customer_id is None
        assert db.scalar(select(ShopCustomer).where(ShopCustomer.site_id == site.id,
            ShopCustomer.email == "code@example.test")) is None
        subject, body = render_message(db, mail, site)
        assert code in body and site.name in subject
    verify = client.get(base + "/account/login/verify?email=code%40example.test&next=%2Faccount%2Fsubscriptions")
    result = client.post(base + "/account/login/verify", data={
        "csrf_token": csrf(verify), "email": "code@example.test", "code": code,
        "next": "/account/subscriptions",
    }, follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"] == base + "/account/subscriptions"
    assert client.get(base + "/account").status_code == 200
    with SessionLocal() as db:
        assert db.get(CustomerLoginCodeChallenge, challenge.id).consumed_at is not None
        with pytest.raises(CommerceError):
            customer_services.consume_login_code(db, site, "code@example.test", code)


def test_wrong_code_lockout_expiry_single_use_and_cross_site(customer_site):
    site, client, base = customer_site
    request_code(site, client, base)
    challenge = challenge_for(site, "code@example.test")
    with SessionLocal() as db:
        for _attempt in range(5):
            assert customer_services.consume_login_code(db, site, "code@example.test", "000000") is None
            db.commit()
        stored = db.get(CustomerLoginCodeChallenge, challenge.id)
        assert stored.attempts == 5 and stored.consumed_at is not None
        with pytest.raises(CommerceError):
            customer_services.consume_login_code(db, site, "code@example.test", "000000")


def test_expired_and_cross_site_code_rejected(customer_site):
    site, client, base = customer_site
    request_code(site, client, base)
    challenge = challenge_for(site, "code@example.test")
    with SessionLocal() as db:
        stored = db.get(CustomerLoginCodeChallenge, challenge.id)
        code = customer_services.decrypt_login_code(site, stored)
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        with pytest.raises(CommerceError):
            customer_services.consume_login_code(db, site, "code@example.test", code)
        other_site = type("OtherSite", (), {"id": "other", "tenant_id": site.tenant_id})()
        with pytest.raises(CommerceError):
            customer_services.consume_login_code(db, other_site, "code@example.test", code)


def test_enumeration_csrf_and_safe_next(customer_site):
    site, client, base = customer_site
    with SessionLocal() as db:
        commerce.settings_for(db, site).customer_accounts_enabled = True
        customer_services.customer_for(db, site, "known@example.test", create=True)
        db.commit()
    existing = request_code(site, client, base, "known@example.test")
    unknown = request_code(site, client, base, "unknown@example.test")
    assert existing.status_code == unknown.status_code == 200
    assert "<form" in existing.text and "<form" in unknown.text
    assert client.post(base + "/account/login/code", data={"email": "csrf@example.test"}).status_code == 400
    verify = client.get(base + "/account/login/verify?next=https%3A%2F%2Fevil.test")
    assert "/sites/" + site.slug + "/account/subscriptions" in verify.text
    assert re.search(r'name="next" value="/sites/' + site.slug + r'/account/subscriptions"', verify.text)


def test_login_code_forms_render_in_liquid_shell(customer_site):
    site, client, base = customer_site
    with SessionLocal() as db:
        stored = db.get(type(site), site.id)
        stored.published_settings_json = {"theme_pack": "liquid"}
        commerce.settings_for(db, stored).customer_accounts_enabled = True
        db.commit()
    login = client.get(base + "/account")
    assert login.status_code == 200 and "account-login-card" in login.text
    verify = request_code(site, client, base, "liquid@example.test")
    assert verify.status_code == 200 and "account-login-card" in verify.text
    assert "--store-accent" in verify.text and "--store-radius" in verify.text

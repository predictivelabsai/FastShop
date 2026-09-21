import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app import commerce, customer_routes, customer_services, subscription_routes
from app.main import app
from app.models import CustomerChallenge
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_customer_routes import csrf
from tests.test_subscriptions import contract  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


@pytest.fixture
def customer_session(contract, monkeypatch):
    r, sub = contract
    sessions = sessionmaker(bind=r.db.bind, expire_on_commit=False)
    monkeypatch.setattr(subscription_routes, "SessionLocal", sessions)
    monkeypatch.setattr(customer_routes, "SessionLocal", sessions)
    monkeypatch.setattr(customer_routes, "dispatch_mail", lambda message_id: False)
    client = TestClient(app)
    base = "/sites/" + r.site.slug
    page = client.get(base + "/account")
    client.post(base + "/account/login", data={"csrf_token": csrf(page), "email": r.customer.email})
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


def test_controls_remain_available_when_store_disabled(customer_session, monkeypatch):
    from app import site_context
    r, sub, client, base = customer_session
    commerce.settings_for(r.db, r.site).mode = "disabled"
    r.site.status = "draft"
    r.site.hostname = "offline-fixture.example.test"
    r.db.commit()
    monkeypatch.setattr(site_context, "SessionLocal", sessionmaker(bind=r.db.bind, expire_on_commit=False))
    host_client = TestClient(app, base_url="https://offline-fixture.example.test")
    assert "Email me a sign-in link" in host_client.get("/account").text
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

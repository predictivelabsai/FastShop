import re
import time
from functools import partial

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app import checkout_payments, store_checkout_routes
from app.main import app
from app.models import CommerceQuote, Order, ShopCustomer, SiteCart, SubscriptionContract
from tests.test_checkout_services import Gateway, ready  # noqa: F401
from tests.test_customer_routes import csrf
from tests.test_us_commerce import DESTINATION, shop  # noqa: F401


class HostedGateway(Gateway):
    def __init__(self):
        self.responses = {}
        self.commands = {}

    def create_checkout(self, attempt_id, command):
        body = command["session"]
        self.commands[attempt_id] = command
        shipping = body["shipping_options"][0]["shipping_rate_data"]["fixed_amount"]["amount"]
        subtotal = sum(line["quantity"] * line["price_data"]["unit_amount"] for line in body["line_items"])
        session_id = "cs_test_" + attempt_id
        self.responses[session_id] = {"id": session_id, "livemode": False, "mode": "payment",
            "client_reference_id": attempt_id, "metadata": body["metadata"], "currency": "usd", "customer": "cus_fixture",
            "amount_total": subtotal + shipping + 205, "total_details": {"amount_tax": 205, "amount_shipping": shipping},
            "automatic_tax": {"status": "complete"}, "status": "open", "payment_status": "unpaid",
            "url": "https://checkout.stripe.com/c/pay/" + session_id}
        return self.responses[session_id]

    def checkout_status(self, session_id):
        return self.responses[session_id]

    def expire_checkout(self, session_id):
        self.responses[session_id]["status"] = "expired"

    def payment_intent_status(self, intent_id):
        response = next(row for row in self.responses.values() if row.get("payment_intent") == intent_id)
        command = self.commands[response["client_reference_id"]]["session"]
        return {"id": intent_id, "livemode": False, "status": "succeeded", "customer": "cus_fixture",
            "payment_method": "pm_fixture", "currency": "usd", "amount_received": response["amount_total"],
            "setup_future_usage": command["payment_intent_data"].get("setup_future_usage"), "created": int(time.time())}


@pytest.fixture
def storefront(ready, monkeypatch):
    r = ready
    r.site.status = "preview"
    r.db.commit()
    sessions = sessionmaker(bind=r.db.bind, expire_on_commit=False)
    gateway = HostedGateway()
    monkeypatch.setattr(store_checkout_routes, "SessionLocal", sessions)
    monkeypatch.setattr(store_checkout_routes, "StripeGateway", lambda site: gateway)
    monkeypatch.setattr(checkout_payments, "handoff", partial(checkout_payments.handoff,
        sessions=sessions, gateway_factory=lambda site: gateway))
    monkeypatch.setattr(checkout_payments, "cancel", partial(checkout_payments.cancel,
        sessions=sessions, gateway_factory=lambda site: gateway))
    return r, TestClient(app), "/sites/" + r.site.slug, gateway


def add_and_quote(r, client, base):
    page = client.get(base + "/cart")
    assert page.status_code == 200 and "FastShop" in page.text
    assert page.headers["content-security-policy"] == "frame-ancestors 'self'"
    page = client.post(base + "/cart/add", data={"csrf_token": csrf(page), "variant_id": r.variant.id, "quantity": "1"})
    assert page.status_code == 200 and "Test tablets" in page.text
    page = client.get(base + "/checkout")
    version = re.search(r'name="version" value="(\d+)"', page.text).group(1)
    data = DESTINATION | {"version": version, "csrf_token": csrf(page), "email": "guest@example.test", "full_name": "Guest Fixture"}
    review = client.post(base + "/checkout", data=data)
    assert review.status_code == 200 and "Review your order" in review.text
    return review, data


def test_guest_checkout_quote_handoff_and_verified_payment(storefront):
    r, client, base, gateway = storefront
    review, data = add_and_quote(r, client, base)
    assert "Sales tax (CA): $2.05" in review.text and "Total: $42.00" in review.text
    assert review.headers["cache-control"] == "private, no-store"
    attempt_url = review.url.path
    # Retrying the original form returns the same quote and reservation.
    assert client.post(base + "/checkout", data=data).url.path == attempt_url
    r.db.refresh(r.stock)
    assert r.stock.allocated == 1
    stranger = TestClient(app)
    assert stranger.get(attempt_url).status_code == 404
    assert client.post(attempt_url + "/pay", data={}).status_code == 400
    response = client.post(attempt_url + "/pay", data={"csrf_token": csrf(review)}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("https://checkout.stripe.com/")
    pending = client.get(attempt_url)
    assert "Payment pending" in pending.text and "Payment confirmed" not in pending.text
    provider = next(iter(gateway.responses.values()))
    provider.update(status="complete", payment_status="paid", payment_intent="pi_route_fixture")
    confirmed = client.post(attempt_url + "/refresh", data={"csrf_token": csrf(pending)})
    assert "Payment confirmed" in confirmed.text
    assert r.db.scalar(select(func.count(Order.id))) == 1
    customer = r.db.scalar(select(ShopCustomer).where(ShopCustomer.email == "guest@example.test"))
    assert customer.verified_at is None  # Payment does not silently authenticate a guest.
    new_bag = client.post(attempt_url + "/new", data={"csrf_token": csrf(confirmed)})
    assert "Your bag is empty" in new_bag.text


def test_cart_mutation_validation_and_unstarted_cancellation(storefront):
    r, client, base, gateway = storefront
    review, data = add_and_quote(r, client, base)
    assert client.post(base + "/cart/add", data={"csrf_token": csrf(review), "variant_id": r.variant.id, "quantity": 1}).status_code == 400
    page = client.post(review.url.path + "/cancel", data={"csrf_token": csrf(review)})
    assert "Checkout closed" in page.text
    r.db.refresh(r.stock)
    assert r.stock.allocated == 0
    bag = client.post(review.url.path + "/new", data={"csrf_token": csrf(page)})
    token = csrf(bag)
    assert client.post(base + "/cart/update", data={"csrf_token": token, "version": "999", "variant_id": r.variant.id, "quantity": 0}).status_code == 400
    version = re.search(r'name="version" value="(\d+)"', bag.text).group(1)
    bag = client.post(base + "/cart/update", data={"csrf_token": token, "version": version, "variant_id": r.variant.id, "quantity": 0})
    assert "Your bag is empty" in bag.text
    assert client.post(base + "/cart/add", data={"csrf_token": token, "variant_id": "foreign", "quantity": 1}).status_code == 400


def test_subscription_purchase_requires_consent_and_activates_only_recurring_lines(storefront):
    r, client, base, gateway = storefront
    page = client.get(base + "/cart")
    for option in ("off", "on"):
        page = client.post(base + "/cart/add", data={"csrf_token": csrf(page), "variant_id": r.variant.id,
            "quantity": "1", "subscription": option})
        assert page.status_code == 200
    assert "One-time purchase" in page.text and "Monthly subscription" in page.text
    assert len(r.db.scalar(select(SiteCart)).lines_json) == 2
    page = client.get(base + "/checkout")
    assert "Recurring merchandise: $26.96" in page.text
    checkbox = re.search(r'<input[^>]+name="subscription_consent"[^>]*>', page.text).group(0)
    assert "checked" not in checkbox
    version = re.search(r'name="version" value="(\d+)"', page.text).group(1)
    data = DESTINATION | {"csrf_token": csrf(page), "version": version, "email": "subscriber@example.test", "full_name": "Subscriber Fixture"}
    assert client.post(base + "/checkout", data=data).status_code == 400
    r.db.refresh(r.stock)
    assert r.stock.allocated == 0 and r.db.scalar(select(func.count(CommerceQuote.id))) == 0
    review = client.post(base + "/checkout", data=data | {"subscription_consent": "on"})
    assert review.status_code == 200 and "Recurring merchandise: $26.96" in review.text
    assert "Total: $68.96" in review.text
    quote = r.db.scalar(select(CommerceQuote))
    assert quote.snapshot_json["subscription_consent"]["accepted"] is True
    response = client.post(review.url.path + "/pay", data={"csrf_token": csrf(review)}, follow_redirects=False)
    assert response.status_code == 303
    provider = next(iter(gateway.responses.values()))
    provider.update(status="complete", payment_status="paid", payment_intent="pi_subscriberfixture")
    for _ in range(2):
        confirmed = client.post(review.url.path + "/refresh", data={"csrf_token": csrf(review)})
        assert "Payment confirmed" in confirmed.text
    assert r.db.scalar(select(func.count(SubscriptionContract.id))) == 1
    sub = r.db.scalar(select(SubscriptionContract))
    assert len(sub.lines_json) == 1 and sub.lines_json[0]["quantity"] == 1
    assert sub.lines_json[0]["unit_minor"] == 2696


def test_cart_updates_preserve_purchase_option_and_reject_ineligible_subscriptions(storefront):
    from app.commerce import settings_for
    r, client, base, gateway = storefront
    page = client.get(base + "/cart")
    for option in ("off", "on"):
        page = client.post(base + "/cart/add", data={"csrf_token": csrf(page), "variant_id": r.variant.id,
            "quantity": "1", "subscription": option})
    version = re.search(r'name="version" value="(\d+)"', page.text).group(1)
    page = client.post(base + "/cart/update", data={"csrf_token": csrf(page), "version": version,
        "variant_id": r.variant.id, "quantity": "0", "subscription": "on"})
    assert "Monthly subscription" not in page.text and "One-time purchase" in page.text
    settings_for(r.db, r.site).subscription_product_ids_json = []
    r.db.commit()
    assert client.post(base + "/cart/add", data={"csrf_token": csrf(page), "variant_id": r.variant.id,
        "quantity": "1", "subscription": "on"}).status_code == 400


def test_cart_summary_and_draft_discount_are_browser_scoped_and_versioned(storefront):
    r, client, base, gateway = storefront
    assert client.get(base + "/cart/summary").json() == {"count": 0}
    page = client.get(base + "/cart")
    page = client.post(base + "/cart/add", data={"csrf_token": csrf(page), "variant_id": r.variant.id, "quantity": 2})
    assert client.get(base + "/cart/summary").json() == {"count": 2}
    assert TestClient(app).get(base + "/cart/summary").json() == {"count": 0}
    version = re.search(r'name="version" value="(\d+)"', page.text).group(1)
    data = {"csrf_token": csrf(page), "version": version, "code": "welcome-fixture"}
    assert client.post(base + "/cart/discount", data=data | {"csrf_token": "bad"}).status_code == 400
    saved = client.post(base + "/cart/discount", data=data)
    assert saved.status_code == 200 and 'value="WELCOME-FIXTURE"' in saved.text
    assert client.post(base + "/cart/discount", data=data).status_code == 400
    delivery = client.get(base + "/checkout")
    assert 'value="WELCOME-FIXTURE"' in delivery.text
    assert "Merchandise: $59.90" in saved.text  # Saving a draft is not applying it.


def test_saved_valid_discount_redeems_at_checkout_and_paid_cart_count_clears(storefront):
    from datetime import UTC, datetime, timedelta

    from app.models import CustomerOffer
    r, client, base, gateway = storefront
    r.db.add(CustomerOffer(tenant_id=r.site.tenant_id, site_id=r.site.id, customer_id=r.customer.id,
        code="WELCOME-FIXTURE", expires_at=datetime.now(UTC) + timedelta(days=1)))
    r.db.commit()
    page = client.get(base + "/cart")
    page = client.post(base + "/cart/add", data={"csrf_token": csrf(page), "variant_id": r.variant.id, "quantity": 1})
    version = re.search(r'name="version" value="(\d+)"', page.text).group(1)
    page = client.post(base + "/cart/discount", data={"csrf_token": csrf(page), "version": version, "code": "WELCOME-FIXTURE"})
    page = client.get(base + "/checkout")
    version = re.search(r'name="version" value="(\d+)"', page.text).group(1)
    review = client.post(base + "/checkout", data=DESTINATION | {"csrf_token": csrf(page), "version": version,
        "email": r.customer.email, "full_name": "Discount Fixture", "code": "WELCOME-FIXTURE"})
    assert "Savings: $2.99" in review.text and "Total: $39.01" in review.text
    assert client.post(base + "/cart/discount", data={"csrf_token": csrf(review), "version": version, "code": ""}).status_code == 400
    client.post(review.url.path + "/pay", data={"csrf_token": csrf(review)}, follow_redirects=False)
    next(iter(gateway.responses.values())).update(status="complete", payment_status="paid", payment_intent="pi_discountfixture")
    client.post(review.url.path + "/refresh", data={"csrf_token": csrf(review)})
    assert client.get(base + "/cart/summary").json() == {"count": 0}
    assert "Your bag is empty" in client.get(base + "/cart").text

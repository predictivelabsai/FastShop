import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app import checkout_services as checkout
from app import customer_services
from app.commerce_webhooks import process_event
from app.integrations.stripe_commerce import TaxResult
from app.models import (
    CheckoutAttempt,
    CommerceQuote,
    CustomerOffer,
    InventoryReservation,
    Order,
    OutboxEvent,
    PaymentTransaction,
    Site,
    SiteOrder,
    Stock,
    Warehouse,
)
from app.services import CommerceError
from tests.test_us_commerce import DESTINATION, shop  # noqa: F401


class Gateway:
    def calculate_tax(self, lines, destination, origin, shipping_minor, **kwargs):
        return TaxResult("taxcalc_fixture", "USD", 205,
            sum(line.amount_minor for line in lines) + shipping_minor + 205,
            datetime.now(UTC) + timedelta(hours=1), [{"amount": 205}])

    def checkout_status(self, session_id):
        return self.result


@pytest.fixture
def ready(shop):
    db, site, config, variant = shop
    customer = customer_services.customer_for(db, site, "checkout@example.test", create=True)
    customer.verified_at = datetime.now(UTC)
    warehouse = Warehouse(tenant_id=site.tenant_id, code="EE", name="Fixture", country_code="EE")
    db.add(warehouse)
    db.flush()
    stock = Stock(warehouse_id=warehouse.id, variant_id=variant.id, quantity=5)
    db.add(stock)
    db.commit()
    return SimpleNamespace(db=db, site=site, customer=customer, stock=stock, variant=variant,
        gateway=Gateway(), selections=[{"variant_id": variant.id, "quantity": 2}])


def prepare(r, **kwargs):
    return checkout.prepare(r.db, r.site, r.customer.id, r.selections, DESTINATION,
        r.gateway, request_key=kwargs.pop("request_key", "first-request-key-01"), **kwargs)


def open_session(r, attempt, *, status="complete", payment_status="paid"):
    checkout.begin_provider(r.db, r.site, attempt.id)
    attempt.stripe_session_id = "cs_test_fixture"
    attempt.state = "open"
    r.db.commit()
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    r.gateway.result = {"id": attempt.stripe_session_id, "livemode": False,
        "client_reference_id": attempt.id, "metadata": {"site_id": r.site.id},
        "currency": "usd", "amount_total": quote.total_minor, "status": status,
        "payment_status": payment_status, "mode": "payment", "payment_intent": "pi_fixture"}


def test_retry_reserves_once_and_rejects_changed_payload(ready):
    r = ready
    attempt = prepare(r)
    r.db.commit()
    assert prepare(r).id == attempt.id
    assert r.stock.allocated == 2
    assert r.db.scalar(select(func.count(InventoryReservation.id))) == 1
    r.selections[0]["quantity"] = 3
    with pytest.raises(CommerceError, match="different request"):
        prepare(r)
    assert r.stock.allocated == 2


def test_stock_failure_rolls_back_partial_allocations_even_if_outer_transaction_commits(ready):
    r = ready
    r.selections[0]["quantity"] = 6
    with pytest.raises(CommerceError, match="not enough stock"):
        prepare(r)
    r.db.commit()
    r.db.refresh(r.stock)
    assert r.stock.allocated == 0
    assert r.db.scalar(select(func.count(CheckoutAttempt.id))) == 0
    assert r.db.scalar(select(func.count(CommerceQuote.id))) == 0


def test_customer_scope_and_origin_stock(ready):
    r = ready
    r.customer.tenant_id = "another-tenant"
    r.db.flush()
    with pytest.raises(CommerceError, match="Customer not found"):
        prepare(r)
    r.customer.tenant_id = r.site.tenant_id
    r.db.get(Warehouse, r.stock.warehouse_id).country_code = "US"
    r.db.flush()
    with pytest.raises(CommerceError, match="not enough stock"):
        prepare(r)


def test_unknown_provider_outcome_cannot_release_inventory_or_start_second_checkout(ready):
    r = ready
    attempt = prepare(r)
    checkout.begin_provider(r.db, r.site, attempt.id)
    r.db.commit()
    assert checkout.begin_provider(r.db, r.site, attempt.id).id == attempt.id
    with pytest.raises(CommerceError, match="Stripe's payment status"):
        checkout.cancel_unstarted(r.db, r.site, attempt.id)
    with pytest.raises(CommerceError, match="existing checkout"):
        prepare(r, request_key="second-request-key-02")
    assert r.stock.allocated == 2


def test_unstarted_cancel_and_provider_expiry_release_once(ready):
    r = ready
    attempt = prepare(r)
    checkout.cancel_unstarted(r.db, r.site, attempt.id)
    checkout.cancel_unstarted(r.db, r.site, attempt.id)
    assert r.stock.allocated == 0
    second = prepare(r, request_key="second-request-key-02")
    open_session(r, second, status="expired", payment_status="unpaid")
    checkout.reconcile(r.db, r.site, second.id, r.gateway)
    checkout.reconcile(r.db, r.site, second.id, r.gateway)
    assert r.stock.allocated == 0
    assert r.db.scalar(select(func.count(Order.id))) == 0


def test_paid_reconciliation_is_idempotent_and_discount_redeems_once(ready):
    r = ready
    offer = CustomerOffer(tenant_id=r.site.tenant_id, site_id=r.site.id,
        customer_id=r.customer.id, code="WELCOME-FIXTURE", expires_at=datetime.now(UTC) + timedelta(days=1))
    r.db.add(offer)
    r.db.commit()
    attempt = prepare(r, code=offer.code)
    open_session(r, attempt)
    checkout.reconcile(r.db, r.site, attempt.id, r.gateway)
    checkout.reconcile(r.db, r.site, attempt.id, r.gateway)
    r.db.commit()
    assert attempt.state == "paid" and offer.redeemed_order_id == attempt.order_id
    assert r.stock.allocated == 2
    for model in [Order, SiteOrder, PaymentTransaction, OutboxEvent]:
        assert r.db.scalar(select(func.count(model.id))) == 1
    order = r.db.get(Order, attempt.order_id)
    assert (order.subtotal_minor, order.discount_minor, order.tax_minor, order.total_minor) == (5990, 599, 205, 6596)
    with pytest.raises(CommerceError, match="first-order code"):
        prepare(r, request_key="second-request-key-02", code=offer.code)


@pytest.mark.parametrize("change", [{"amount_total": 1}, {"currency": "eur"}, {"livemode": True},
    {"metadata": {"site_id": "another-site"}}, {"client_reference_id": "another-checkout"}])
def test_mismatched_provider_payment_never_marks_paid(ready, change):
    r = ready
    attempt = prepare(r)
    open_session(r, attempt)
    r.gateway.result.update(change)
    with pytest.raises(CommerceError, match="do not match"):
        checkout.reconcile(r.db, r.site, attempt.id, r.gateway)
    assert r.db.scalar(select(func.count(Order.id))) == 0
    assert r.stock.allocated == 2


def test_complete_but_unpaid_and_late_payment_after_local_expiry(ready):
    r = ready
    attempt = prepare(r)
    open_session(r, attempt, payment_status="unpaid")
    attempt.expires_at = datetime.now(UTC) - timedelta(hours=1)
    checkout.reconcile(r.db, r.site, attempt.id, r.gateway)
    assert attempt.state == "open" and r.stock.allocated == 2
    r.gateway.result["payment_status"] = "paid"
    checkout.reconcile(r.db, r.site, attempt.id, r.gateway)
    assert attempt.state == "paid" and r.stock.allocated == 2


def test_webhook_recovers_session_after_creation_response_is_lost(ready):
    r = ready
    attempt = prepare(r)
    open_session(r, attempt)
    attempt.stripe_session_id, attempt.state = None, "creating"
    r.db.commit()
    event = {"livemode": False, "type": "checkout.session.completed", "data": {"object": {
        "object": "checkout.session", "id": "cs_test_fixture", "client_reference_id": attempt.id,
        "metadata": {"site_id": r.site.id}}}}
    assert process_event(r.db, r.site, event, r.gateway) == "processed"
    assert process_event(r.db, r.site, event, r.gateway) == "processed"
    assert attempt.state == "paid"
    assert r.db.scalar(select(func.count(Order.id))) == 1
    event["data"]["object"]["metadata"]["site_id"] = "another-store"
    assert process_event(r.db, r.site, event, r.gateway) == "ignored"
    event["livemode"] = True
    with pytest.raises(CommerceError, match="Live events"):
        process_event(r.db, r.site, event, r.gateway)


def test_expired_quote_cannot_start_payment(ready):
    r = ready
    attempt = prepare(r)
    r.db.get(CommerceQuote, attempt.quote_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    r.db.commit()
    with pytest.raises(CommerceError, match="quote expired"):
        checkout.begin_provider(r.db, r.site, attempt.id)
    checkout.cancel_unstarted(r.db, r.site, attempt.id)
    assert r.stock.allocated == 0


def test_http_webhook_verifies_signature_and_reconciles_duplicate_deliveries(ready, monkeypatch):
    from app import commerce_webhooks
    from app.api import api
    r = ready
    monkeypatch.setattr(commerce_webhooks, "SessionLocal", sessionmaker(bind=r.db.bind))
    monkeypatch.setattr(commerce_webhooks, "StripeGateway", lambda site: r.gateway)
    monkeypatch.setattr(commerce_webhooks, "credentials", lambda site: ("sk_test_fixture", "whsec_fixture"))
    attempt = prepare(r)
    open_session(r, attempt)
    event = {"id": "evt_fixture", "livemode": False, "type": "checkout.session.completed",
        "data": {"object": {"object": "checkout.session", "id": "cs_test_fixture",
            "client_reference_id": attempt.id, "metadata": {"site_id": r.site.id}}}}
    payload = json.dumps(event).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(b"whsec_fixture", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    client = TestClient(api)
    endpoint = f"/v1/commerce/{r.site.id}/stripe-webhook"
    assert client.post(endpoint, content=payload).status_code == 400
    headers = {"stripe-signature": f"t={stamp},v1={signature}"}
    assert client.post(endpoint, content=payload + b" ", headers=headers).status_code == 400
    assert client.post(endpoint, content=payload, headers=headers).status_code == 200
    assert client.post(endpoint, content=payload, headers=headers).status_code == 200
    r.db.expire_all()
    assert r.db.scalar(select(func.count(Order.id))) == 1


def test_concurrent_retries_create_one_reservation(ready):
    r = ready
    sessions = sessionmaker(bind=r.db.bind, expire_on_commit=False)
    site_id, customer_id = r.site.id, r.customer.id
    barrier = Barrier(2)

    def retry():
        with sessions() as db:
            site = db.get(Site, site_id)
            barrier.wait(timeout=10)
            attempt = checkout.prepare(db, site, customer_id, r.selections, DESTINATION,
                Gateway(), request_key="concurrent-request-01")
            db.commit()
            return attempt.id

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(retry) for _ in range(2)]
        ids = [future.result(timeout=20) for future in futures]
    assert ids[0] == ids[1]
    r.db.expire_all()
    assert r.stock.allocated == 2
    assert r.db.scalar(select(func.count(InventoryReservation.id))) == 1

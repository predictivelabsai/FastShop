import re
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import checkout_payments, commerce, content, order_management
from app.config import settings
from app.integrations.stripe_commerce import StripeGateway
from app.models import (
    Base,
    DemoCommand,
    DemoWorkspace,
    Membership,
    Order,
    OrderLine,
    PaymentTransaction,
    RefundCommand,
    ShipmentEvent,
    ShopCustomer,
    SiteOrder,
    User,
)
from app.services import CommerceError


@contextmanager
def workspace():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="orders-owner@example.test", name="Order owner")
        merchant = User(email="orders-merchant@example.test", name="Merchant")
        editor = User(email="orders-editor@example.test", name="Editor")
        db.add_all([owner, merchant, editor])
        db.flush()
        site = content.create_site(db, owner.id, "Order test", "orders-" + uuid4().hex[:8])
        db.add_all([
            Membership(tenant_id=site.tenant_id, user_id=merchant.id, role="merchant"),
            Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"),
        ])
        commerce.settings_for(db, site, create=True).mode = "sandbox"
        customer = ShopCustomer(
            tenant_id=site.tenant_id,
            site_id=site.id,
            email="buyer@example.test",
            name="Buyer",
            verified_at=datetime.now(UTC),
        )
        db.add(customer)
        db.flush()
        order = Order(
            tenant_id=site.tenant_id,
            channel_id=site.channel_id,
            number="FS-ORDER-TEST",
            idempotency_key="order-" + uuid4().hex,
            email=customer.email,
            currency="USD",
            subtotal_minor=9000,
            shipping_minor=500,
            tax_minor=500,
            total_minor=10000,
            payment_status="paid",
            shipping_address_json={"line1": "1 Main St", "city": "Austin", "state": "TX", "postal_code": "78701", "country": "US"},
        )
        db.add(order)
        db.flush()
        db.add(OrderLine(
            order_id=order.id,
            sku="ORDER-SKU",
            product_name="Order product",
            variant_name="Standard",
            quantity=1,
            unit_price_minor=9000,
            total_minor=9000,
        ))
        link = SiteOrder(
            tenant_id=site.tenant_id,
            site_id=site.id,
            order_id=order.id,
            customer_id=customer.id,
        )
        db.add(link)
        db.flush()
        db.add(PaymentTransaction(
            order_id=order.id,
            provider="stripe:" + site.id,
            external_id="pi_order_fixture",
            kind="charge",
            status="succeeded",
            currency="USD",
            amount_minor=10000,
        ))
        db.commit()
        yield SimpleNamespace(
            db=db,
            engine=engine,
            sessions=sessionmaker(bind=engine, expire_on_commit=False),
            owner=owner,
            merchant=merchant,
            editor=editor,
            site=site,
            customer=customer,
            order=order,
            link=link,
        )


def add_order(db, site, customer, number, amount, currency="USD", created_at=None, payment_status="paid"):
    order = Order(
        tenant_id=site.tenant_id,
        channel_id=site.channel_id,
        number=number,
        idempotency_key="report-" + uuid4().hex,
        email=customer.email,
        currency=currency,
        subtotal_minor=amount,
        total_minor=amount,
        payment_status=payment_status,
    )
    db.add(order)
    db.flush()
    if created_at:
        order.created_at = created_at
    link = SiteOrder(
        tenant_id=site.tenant_id,
        site_id=site.id,
        order_id=order.id,
        customer_id=customer.id,
    )
    db.add(link)
    db.flush()
    return order, link


def test_fulfillment_transition_legality_tracking_and_roles():
    with workspace() as w:
        with pytest.raises(CommerceError, match="cannot move"):
            order_management.transition_fulfillment(
                w.db, w.site, w.merchant.id, w.link.id, "delivered", "unfulfilled"
            )
        with pytest.raises(CommerceError, match="Carrier"):
            order_management.transition_fulfillment(
                w.db, w.site, w.merchant.id, w.link.id, "fulfilled", "unfulfilled"
            )
        with pytest.raises(CommerceError, match="access denied"):
            order_management.transition_fulfillment(
                w.db, w.site, w.editor.id, w.link.id, "fulfilled", "unfulfilled",
                carrier="UPS", tracking_number="1ZTEST",
            )
        event = order_management.transition_fulfillment(
            w.db,
            w.site,
            w.merchant.id,
            w.link.id,
            "fulfilled",
            "unfulfilled",
            carrier="UPS",
            tracking_number="1ZTEST",
            tracking_url="https://www.ups.com/track?loc=en_US",
            note="Packed as one parcel.",
        )
        assert event.status == "fulfilled" and w.order.status == "fulfilled"
        with pytest.raises(CommerceError, match="changed"):
            order_management.transition_fulfillment(
                w.db, w.site, w.merchant.id, w.link.id, "delivered", "unfulfilled"
            )
        order_management.transition_fulfillment(
            w.db, w.site, w.merchant.id, w.link.id, "delivered", "fulfilled"
        )
        assert w.order.status == "delivered"
        assert w.db.scalar(select(func.count(ShipmentEvent.id))) == 2


def test_refund_is_bounded_exact_and_idempotent_through_mocked_transport(monkeypatch):
    with workspace() as w:
        calls = []

        def respond(request):
            calls.append(request)
            body = parse_qs(request.content.decode())
            assert body["payment_intent"] == ["pi_order_fixture"]
            assert body["amount"] == ["2500"]
            assert request.headers["idempotency-key"].startswith("fastshop-refund-")
            return httpx.Response(200, json={
                "id": "re_exact_fixture",
                "payment_intent": "pi_order_fixture",
                "amount": 2500,
                "currency": "usd",
                "status": "succeeded",
                "livemode": False,
            })

        key = f"FASTSHOP_STRIPE_{w.site.id.upper()}_SECRET_KEY"
        monkeypatch.setenv(key, "sk_test_order_fixture")
        transport = httpx.MockTransport(respond)
        factory = lambda site: StripeGateway(site, transport=transport)  # noqa: E731
        command = checkout_payments.refund(
            w.site.id,
            w.merchant.id,
            w.link.id,
            2500,
            "Customer returned the unopened order.",
            "refund-request-0001",
            sessions=w.sessions,
            gateway_factory=factory,
        )
        repeated = checkout_payments.refund(
            w.site.id,
            w.merchant.id,
            w.link.id,
            2500,
            "Customer returned the unopened order.",
            "refund-request-0001",
            sessions=w.sessions,
            gateway_factory=factory,
        )
        assert repeated.id == command.id and len(calls) == 1
        with Session(w.engine) as db:
            order = db.get(Order, w.order.id)
            link = db.get(SiteOrder, w.link.id)
            assert order.payment_status == "partially_refunded"
            assert order_management.refund_totals(db, w.site, link, order) == (10000, 2500, 7500)
            assert db.scalar(select(func.count(RefundCommand.id))) == 1
            assert db.scalar(select(func.count(PaymentTransaction.id)).where(
                PaymentTransaction.kind == "refund"
            )) == 1
        with pytest.raises(CommerceError, match="exceeds"):
            checkout_payments.refund(
                w.site.id,
                w.merchant.id,
                w.link.id,
                7501,
                "Attempt above the exact remaining capture.",
                "refund-request-0002",
                sessions=w.sessions,
                gateway_factory=factory,
            )
        assert len(calls) == 1


def test_refund_mismatch_is_reserved_and_guard_fails_closed(monkeypatch):
    with workspace() as w:
        monkeypatch.setenv(f"FASTSHOP_STRIPE_{w.site.id.upper()}_SECRET_KEY", "sk_test_order_fixture")

        def respond(_request):
            return httpx.Response(200, json={
                "id": "re_wrong_fixture",
                "payment_intent": "pi_order_fixture",
                "amount": 2499,
                "currency": "usd",
                "status": "succeeded",
                "livemode": False,
            })

        factory = lambda site: StripeGateway(site, transport=httpx.MockTransport(respond))  # noqa: E731
        with pytest.raises(CommerceError, match="does not exactly match"):
            checkout_payments.refund(
                w.site.id,
                w.owner.id,
                w.link.id,
                2500,
                "Exact mismatch must require reconciliation.",
                "refund-mismatch-001",
                sessions=w.sessions,
                gateway_factory=factory,
            )
        with Session(w.engine) as db:
            command = db.scalar(select(RefundCommand))
            link = db.get(SiteOrder, w.link.id)
            order = db.get(Order, w.order.id)
            assert command.state == "reconciliation_required"
            assert order_management.refund_totals(db, w.site, link, order) == (10000, 0, 7500)
            commerce.settings_for(db, w.site).mode = "disabled"
            db.commit()
        with pytest.raises(CommerceError, match="not enabled"):
            checkout_payments.refund(
                w.site.id,
                w.owner.id,
                w.link.id,
                1000,
                "The mode guard must fail before provider I/O.",
                "refund-disabled-001",
                sessions=w.sessions,
                gateway_factory=factory,
            )


def test_revenue_math_currency_bounds_empty_range_and_demo_isolation():
    with workspace() as w:
        report_day = datetime(2026, 10, 5, 12, tzinfo=UTC)
        w.order.created_at = report_day
        w.order.total_minor = 1000
        w.order.subtotal_minor = 1000
        second, _ = add_order(w.db, w.site, w.customer, "FS-REPORT-2", 3000, created_at=report_day)
        add_order(w.db, w.site, w.customer, "FS-REPORT-EUR", 500, currency="EUR", created_at=report_day)
        old, _ = add_order(w.db, w.site, w.customer, "FS-REPORT-OLD", 9000, created_at=report_day - timedelta(days=60))
        refund = PaymentTransaction(
            order_id=old.id,
            provider="stripe:" + w.site.id,
            external_id="re_report_usd",
            kind="refund",
            status="succeeded",
            currency="USD",
            amount_minor=400,
        )
        mismatch = PaymentTransaction(
            order_id=second.id,
            provider="stripe:" + w.site.id,
            external_id="re_report_eur",
            kind="refund",
            status="succeeded",
            currency="EUR",
            amount_minor=50,
        )
        w.db.add_all([refund, mismatch])
        w.db.flush()
        refund.created_at = report_day
        mismatch.created_at = report_day
        demo = DemoWorkspace(
            tenant_id=w.site.tenant_id,
            site_id=w.site.id,
            state_json={"orders": [{"total_minor": 99999999, "currency": "USD"}]},
        )
        w.db.add(demo)
        w.db.flush()
        w.db.add(DemoCommand(
            tenant_id=w.site.tenant_id,
            site_id=w.site.id,
            workspace_id=demo.id,
            command_id="demo-report-command",
            fingerprint="demo",
            result_json={"revenue_minor": 99999999},
        ))
        w.db.commit()
        report = order_management.revenue_report(w.db, w.site, date(2026, 10, 1), date(2026, 10, 9))
        assert (report.gross_sales_minor, report.refunds_minor, report.net_revenue_minor) == (4000, 400, 3600)
        assert (report.order_count, report.average_order_value_minor) == (2, 2000)
        assert (report.excluded_currency_orders, report.excluded_currency_refunds) == (1, 1)
        empty = order_management.revenue_report(w.db, w.site, date(2025, 1, 1), date(2025, 1, 2))
        assert (empty.gross_sales_minor, empty.refunds_minor, empty.order_count) == (0, 0, 0)
        assert order_management.report_dates("", "2026-10-09") == (date(2026, 9, 10), date(2026, 10, 9))
        with pytest.raises(CommerceError, match="limited"):
            order_management.report_dates("2025-01-01", "2026-10-09")


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def test_order_routes_are_tenant_scoped_csrf_protected_and_prg():
    from app.db import SessionLocal
    from app.main import app

    client = TestClient(app)
    login = client.get("/login")
    token = csrf(login)
    assert client.post("/login", data={
        "csrf_token": token,
        "email": settings.admin_email,
        "password": settings.admin_password,
    }).status_code == 200
    with SessionLocal() as db:
        operator = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, operator.id, "Order route", "order-route-" + uuid4().hex[:8])
        commerce.settings_for(db, site, create=True).mode = "sandbox"
        customer = ShopCustomer(
            tenant_id=site.tenant_id,
            site_id=site.id,
            email="route-buyer@example.test",
            name="Route buyer",
            verified_at=datetime.now(UTC),
        )
        db.add(customer)
        db.flush()
        order, link = add_order(db, site, customer, "FS-ROUTE-ORDER", 4200)
        other_owner = User(email="route-other@example.test", name="Other")
        db.add(other_owner)
        db.flush()
        other_site = content.create_site(db, other_owner.id, "Other route", "other-route-" + uuid4().hex[:8])
        other_customer = ShopCustomer(
            tenant_id=other_site.tenant_id,
            site_id=other_site.id,
            email="other@example.test",
            name="Other",
        )
        db.add(other_customer)
        db.flush()
        _, other_link = add_order(db, other_site, other_customer, "FS-OTHER-ORDER", 9900)
        site_id, link_id, order_id, other_link_id = site.id, link.id, order.id, other_link.id
        db.commit()

    list_page = client.get(f"/admin/sites/{site_id}/orders")
    assert list_page.status_code == 200
    assert "FS-ROUTE-ORDER" in list_page.text and "FS-OTHER-ORDER" not in list_page.text
    assert client.get(f"/admin/sites/{site_id}/orders/{other_link_id}").status_code == 400
    detail = client.get(f"/admin/sites/{site_id}/orders/{link_id}")
    form = {
        "csrf_token": "wrong",
        "expected_status": "unfulfilled",
        "target": "fulfilled",
        "carrier": "UPS",
        "tracking_number": "1ZROUTE",
    }
    rejected = client.post(f"/admin/sites/{site_id}/orders/{link_id}/fulfillment", data=form, follow_redirects=False)
    assert rejected.status_code == 303
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "unfulfilled"
    form["csrf_token"] = csrf(detail)
    accepted = client.post(f"/admin/sites/{site_id}/orders/{link_id}/fulfillment", data=form, follow_redirects=False)
    assert accepted.status_code == 303 and "notice=" in accepted.headers["location"]
    refund = client.post(f"/admin/sites/{site_id}/orders/{link_id}/refund", data={
        "csrf_token": form["csrf_token"],
        "amount_minor": "100",
        "reason": "Missing explicit confirmation must not refund.",
        "request_key": "route-refund-request-01",
    }, follow_redirects=False)
    assert refund.status_code == 303
    forged_retry = client.post(f"/admin/sites/{site_id}/orders/{link_id}/refund", data={
        "csrf_token": form["csrf_token"],
        "amount_minor": "100",
        "reason": "A forged retry must not skip confirmation.",
        "request_key": "route-refund-request-02",
        "retry": "on",
    }, follow_redirects=False)
    assert forged_retry.status_code == 303
    with SessionLocal() as db:
        assert not db.scalar(select(RefundCommand).where(RefundCommand.site_order_id == link_id))
    report = client.get(f"/admin/sites/{site_id}/revenue?start=2026-01-01&end=2026-12-31")
    assert report.status_code == 200 and "Demo-commerce activity" not in report.text

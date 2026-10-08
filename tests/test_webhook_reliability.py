from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import commerce, commerce_webhooks, content, order_management
from app.integrations import fasterp
from app.models import (
    Base,
    Membership,
    Order,
    OutboxEvent,
    PaymentTransaction,
    ShopCustomer,
    SiteChangeSet,
    SiteOrder,
    StripeWebhookEvent,
    User,
)
from app.seed import seed
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
        seed(db, "seed-webhooks@example.test")
        owner = User(email="reliability-owner@example.test", name="Reliability owner")
        merchant = User(email="reliability-merchant@example.test", name="Merchant")
        editor = User(email="reliability-editor@example.test", name="Editor")
        db.add_all([owner, merchant, editor])
        db.flush()
        site = content.create_site(
            db, owner.id, "Reliability test", "reliability-" + uuid4().hex[:8]
        )
        db.add_all([
            Membership(tenant_id=site.tenant_id, user_id=merchant.id, role="merchant"),
            Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"),
        ])
        commerce.settings_for(db, site, create=True).mode = "sandbox"
        customer = ShopCustomer(
            tenant_id=site.tenant_id,
            site_id=site.id,
            email="reliability-buyer@example.test",
            name="Reliability buyer",
            verified_at=datetime.now(UTC),
        )
        db.add(customer)
        db.flush()
        order = Order(
            tenant_id=site.tenant_id,
            channel_id=site.channel_id,
            number="FS-RELIABILITY",
            idempotency_key="reliability-" + uuid4().hex,
            email=customer.email,
            currency="USD",
            subtotal_minor=4200,
            total_minor=4200,
            payment_status="pending",
        )
        db.add(order)
        db.flush()
        link = SiteOrder(
            tenant_id=site.tenant_id,
            site_id=site.id,
            order_id=order.id,
            customer_id=customer.id,
            stripe_checkout_id="cs_test_reliability",
        )
        db.add(link)
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


def test_outbox_due_backoff_dead_letter_and_topic_dispatch(monkeypatch):
    with workspace() as w:
        now = datetime(2026, 10, 9, 12, tzinfo=UTC)
        missing = OutboxEvent(
            tenant_id=w.site.tenant_id,
            topic="order.confirmed",
            aggregate_id="missing-order",
            payload_json={"site_id": w.site.id},
        )
        noop = OutboxEvent(
            tenant_id=w.site.tenant_id,
            topic="order.refunded",
            aggregate_id=w.order.id,
            payload_json={"site_id": w.site.id},
        )
        later = OutboxEvent(
            tenant_id=w.site.tenant_id,
            topic="order.refunded",
            aggregate_id=w.order.id,
            payload_json={"site_id": w.site.id},
            next_attempt_at=now + timedelta(minutes=1),
        )
        w.db.add_all([missing, noop, later])
        w.db.flush()
        missing.created_at = now - timedelta(minutes=2)
        noop.created_at = now - timedelta(minutes=1)
        w.db.commit()

        delivered, failed = fasterp.deliver_pending(w.db, limit=2, now=now)
        assert (delivered, failed) == (1, 1)
        assert noop.status == "delivered" and noop.attempts == 1
        assert missing.status == "pending" and missing.attempts == 1
        assert missing.next_attempt_at == now + timedelta(seconds=30)
        assert later.status == "pending" and later.attempts == 0

        assert fasterp.deliver_pending(w.db, now=now + timedelta(seconds=29)) == (0, 0)
        missing.attempts = fasterp.MAX_ATTEMPTS - 1
        missing.next_attempt_at = now
        w.db.flush()
        assert fasterp.deliver_pending(w.db, limit=1, now=now) == (0, 1)
        assert missing.status == "dead" and missing.attempts == fasterp.MAX_ATTEMPTS
        assert missing.next_attempt_at is None
        assert fasterp.retry_delay(99) == timedelta(hours=1)


def test_fasterp_delivery_keeps_event_idempotency_key_and_tenant_scope(monkeypatch):
    with workspace() as w:
        event = OutboxEvent(
            tenant_id=w.site.tenant_id,
            topic="order.confirmed",
            aggregate_id=w.order.id,
            payload_json={"site_id": w.site.id},
        )
        w.db.add(event)
        w.db.commit()
        monkeypatch.setattr(fasterp, "settings", SimpleNamespace(
            fasterp_api_token="fixture-token",
            fasterp_base_url="https://erp.example.test",
            fasterp_company_id="fixture-company",
        ))
        requests = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"id": "erp-order-1"}

        def post(url, **kwargs):
            requests.append((url, kwargs))
            return Response()

        monkeypatch.setattr(fasterp.httpx, "post", post)
        assert fasterp.deliver_pending(w.db, now=datetime.now(UTC)) == (1, 0)
        assert requests[0][1]["headers"]["Idempotency-Key"] == event.id
        assert requests[0][1]["json"]["total_minor"] == 4200
        assert w.order.fasterp_order_id == "erp-order-1"


def test_delivery_requeue_is_site_scoped_and_rejects_editors():
    with workspace() as w:
        event = OutboxEvent(
            tenant_id=w.site.tenant_id,
            topic="order.confirmed",
            aggregate_id=w.order.id,
            payload_json={"site_id": w.site.id},
            status="dead",
            attempts=8,
            last_error="token=secret\ntransport failed",
        )
        wrong_site = OutboxEvent(
            tenant_id=w.site.tenant_id,
            topic="order.refunded",
            aggregate_id=w.order.id,
            payload_json={"site_id": "another-site"},
            status="dead",
            attempts=8,
        )
        w.db.add_all([event, wrong_site])
        w.db.commit()
        assert order_management.delivery_failures(w.db, w.site) == [event]
        safe_error = order_management.safe_delivery_error(event)
        assert "\n" not in safe_error and "token=secret" not in safe_error
        with pytest.raises(CommerceError, match="access denied"):
            order_management.requeue_delivery(w.db, w.site, w.editor.id, event.id)
        with pytest.raises(CommerceError, match="not found"):
            order_management.requeue_delivery(w.db, w.site, w.merchant.id, wrong_site.id)
        requeued = order_management.requeue_delivery(
            w.db, w.site, w.merchant.id, event.id
        )
        assert (requeued.status, requeued.attempts, requeued.last_error) == ("pending", 0, "")
        assert w.db.scalar(select(func.count(SiteChangeSet.id)).where(
            SiteChangeSet.site_id == w.site.id,
            SiteChangeSet.tenant_id == w.site.tenant_id,
            SiteChangeSet.source == "delivery-requeue",
        )) == 1


def test_one_order_stripe_resync_is_exact_idempotent_and_audited():
    with workspace() as w:
        calls = []

        class Gateway:
            live_mode = False

            def checkout_status(self, session_id):
                calls.append(("checkout", session_id))
                return {
                    "id": session_id,
                    "livemode": False,
                    "metadata": {"site_id": w.site.id},
                    "currency": "usd",
                    "amount_total": 4200,
                    "payment_intent": "pi_reliability_1",
                }

            def payment_intent_status(self, intent_id):
                calls.append(("intent", intent_id))
                return {
                    "id": intent_id,
                    "livemode": False,
                    "metadata": {"site_id": w.site.id},
                    "currency": "usd",
                    "amount": 4200,
                    "amount_received": 4200,
                    "status": "succeeded",
                }

        factory = lambda _site: Gateway()  # noqa: E731
        first = order_management.resync_order(
            w.site.id,
            w.merchant.id,
            w.link.id,
            sessions=w.sessions,
            gateway_factory=factory,
        )
        second = order_management.resync_order(
            w.site.id,
            w.merchant.id,
            w.link.id,
            sessions=w.sessions,
            gateway_factory=factory,
        )
        assert first == second == "paid"
        with Session(w.engine) as db:
            assert db.scalar(select(func.count(PaymentTransaction.id)).where(
                PaymentTransaction.order_id == w.order.id,
                PaymentTransaction.provider == "stripe:" + w.site.id,
                PaymentTransaction.external_id == "pi_reliability_1",
            )) == 1
            assert db.scalar(select(func.count(SiteChangeSet.id)).where(
                SiteChangeSet.site_id == w.site.id,
                SiteChangeSet.tenant_id == w.site.tenant_id,
                SiteChangeSet.source == "stripe-resync",
            )) == 2
        assert calls == [
            ("checkout", "cs_test_reliability"),
            ("intent", "pi_reliability_1"),
            ("checkout", "cs_test_reliability"),
            ("intent", "pi_reliability_1"),
        ]
        with pytest.raises(CommerceError, match="access denied"):
            order_management.resync_order(
                w.site.id,
                w.editor.id,
                w.link.id,
                sessions=w.sessions,
                gateway_factory=factory,
            )


def test_webhook_ledger_short_circuits_processed_redelivery(monkeypatch):
    with workspace() as w:
        calls = []

        def reconcile(_db, _site, event, _gateway):
            calls.append(event["id"])
            return "processed"

        monkeypatch.setattr(commerce_webhooks, "process_event", reconcile)
        event = {
            "id": "evt_reliability_1",
            "type": "checkout.session.completed",
            "livemode": False,
        }
        assert commerce_webhooks.process_verified_event(w.db, w.site, event, object()) == "processed"
        w.db.commit()
        assert commerce_webhooks.process_verified_event(
            w.db, w.site, event, object()
        ) == "processed (duplicate)"
        assert calls == ["evt_reliability_1"]
        assert w.db.scalar(select(func.count(StripeWebhookEvent.id)).where(
            StripeWebhookEvent.site_id == w.site.id,
            StripeWebhookEvent.tenant_id == w.site.tenant_id,
        )) == 1

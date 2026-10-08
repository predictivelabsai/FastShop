"""Offline Phase 4d reliability evaluation; performs no provider network calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import commerce, commerce_webhooks, content, order_management
from app.integrations import fasterp
from app.models import (
    Base,
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


def evaluate() -> dict:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    checks = {}
    with Session(engine, expire_on_commit=False) as db:
        seed(db, "eval-seed@example.test")
        owner = User(email="eval-reliability@example.test", name="Eval owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Eval reliability", "eval-" + uuid4().hex[:8])
        commerce.settings_for(db, site, create=True).mode = "sandbox"
        customer = ShopCustomer(
            tenant_id=site.tenant_id,
            site_id=site.id,
            email="eval-buyer@example.test",
            name="Eval buyer",
        )
        db.add(customer)
        db.flush()
        order = Order(
            tenant_id=site.tenant_id,
            channel_id=site.channel_id,
            number="FS-EVAL-4D",
            idempotency_key="eval-" + uuid4().hex,
            email=customer.email,
            currency="USD",
            subtotal_minor=7300,
            total_minor=7300,
            payment_status="pending",
        )
        db.add(order)
        db.flush()
        link = SiteOrder(
            tenant_id=site.tenant_id,
            site_id=site.id,
            order_id=order.id,
            customer_id=customer.id,
            stripe_checkout_id="cs_test_eval4d",
        )
        due = OutboxEvent(
            tenant_id=site.tenant_id,
            topic="order.confirmed",
            aggregate_id="missing",
            payload_json={"site_id": site.id},
            attempts=7,
        )
        noop = OutboxEvent(
            tenant_id=site.tenant_id,
            topic="order.refunded",
            aggregate_id=order.id,
            payload_json={"site_id": site.id},
        )
        db.add_all([link, due, noop])
        db.commit()

        delivered, failed = fasterp.deliver_pending(db, now=datetime.now(UTC))
        checks["bounded_outbox"] = (
            delivered == 1
            and failed == 1
            and due.status == "dead"
            and due.attempts == 8
            and noop.status == "delivered"
        )
        checks["tenant_delivery_scope"] = order_management.delivery_failures(db, site) == [due]

        class Gateway:
            live_mode = False

            def checkout_status(self, session_id):
                return {
                    "id": session_id,
                    "livemode": False,
                    "metadata": {"site_id": site.id},
                    "currency": "usd",
                    "amount_total": order.total_minor,
                    "payment_intent": "pi_eval4d",
                }

            def payment_intent_status(self, intent_id):
                return {
                    "id": intent_id,
                    "livemode": False,
                    "metadata": {"site_id": site.id},
                    "currency": "usd",
                    "amount": order.total_minor,
                    "amount_received": order.total_minor,
                    "status": "succeeded",
                }

        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        factory = lambda _site: Gateway()  # noqa: E731
        order_management.resync_order(
            site.id, owner.id, link.id, sessions=sessions, gateway_factory=factory
        )
        order_management.resync_order(
            site.id, owner.id, link.id, sessions=sessions, gateway_factory=factory
        )
        db.expire_all()
        checks["idempotent_provider_resync"] = (
            db.scalar(select(func.count(PaymentTransaction.id)).where(
                PaymentTransaction.order_id == order.id,
                PaymentTransaction.external_id == "pi_eval4d",
            )) == 1
            and db.get(Order, order.id).payment_status == "paid"
            and db.scalar(select(func.count(SiteChangeSet.id)).where(
                SiteChangeSet.site_id == site.id,
                SiteChangeSet.tenant_id == site.tenant_id,
                SiteChangeSet.source == "stripe-resync",
            )) == 2
        )

        event = {
            "id": "evt_eval4d",
            "type": "customer.updated",
            "livemode": False,
        }
        first = commerce_webhooks.process_verified_event(db, site, event, object())
        db.commit()
        second = commerce_webhooks.process_verified_event(db, site, event, object())
        checks["webhook_dedup"] = (
            first == "ignored"
            and second == "processed (duplicate)"
            and db.scalar(select(func.count(StripeWebhookEvent.id)).where(
                StripeWebhookEvent.site_id == site.id,
                StripeWebhookEvent.tenant_id == site.tenant_id,
            )) == 1
        )

    return {
        "phase": "4d-webhook-reliability",
        "offline": True,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main():
    report = evaluate()
    output = Path("output/evals/phase4d-webhook-reliability.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

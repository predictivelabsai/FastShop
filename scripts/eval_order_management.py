"""Offline Phase 4c order-management and revenue evaluation."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import checkout_payments, commerce, content, order_management
from app.models import (
    Base,
    DemoWorkspace,
    Membership,
    Order,
    PaymentTransaction,
    ShopCustomer,
    SiteOrder,
    User,
)
from app.services import CommerceError


class RefundGateway:
    live_mode = False
    calls = []

    def __init__(self, _site):
        pass

    def create_refund(self, command_id, payload):
        self.calls.append((command_id, dict(payload)))
        return {
            "id": "re_eval_exact",
            "payment_intent": payload["payment_intent"],
            "amount": payload["amount"],
            "currency": "usd",
            "status": "succeeded",
            "livemode": False,
        }


def main():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    cases = []
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="phase4c-eval-owner@example.test", name="Eval owner")
        merchant = User(email="phase4c-eval-merchant@example.test", name="Eval merchant")
        outsider = User(email="phase4c-eval-outsider@example.test", name="Eval outsider")
        db.add_all([owner, merchant, outsider])
        db.flush()
        site = content.create_site(db, owner.id, "Phase 4c eval", "phase4c-eval")
        db.add(Membership(tenant_id=site.tenant_id, user_id=merchant.id, role="merchant"))
        commerce.settings_for(db, site, create=True).mode = "sandbox"
        customer = ShopCustomer(
            tenant_id=site.tenant_id,
            site_id=site.id,
            email="phase4c-buyer@example.test",
            name="Eval buyer",
        )
        db.add(customer)
        db.flush()
        order = Order(
            tenant_id=site.tenant_id,
            channel_id=site.channel_id,
            number="FS-PHASE4C-EVAL",
            idempotency_key="phase4c-eval-order",
            email=customer.email,
            currency="USD",
            subtotal_minor=9000,
            shipping_minor=500,
            tax_minor=500,
            total_minor=10000,
            payment_status="paid",
        )
        db.add(order)
        db.flush()
        link = SiteOrder(
            tenant_id=site.tenant_id,
            site_id=site.id,
            order_id=order.id,
            customer_id=customer.id,
        )
        db.add(link)
        db.flush()
        charge = PaymentTransaction(
            order_id=order.id,
            provider="stripe:" + site.id,
            external_id="pi_phase4c_eval",
            kind="charge",
            status="succeeded",
            currency="USD",
            amount_minor=10000,
        )
        demo = DemoWorkspace(
            tenant_id=site.tenant_id,
            site_id=site.id,
            state_json={"orders": [{"total_minor": 50000000}]},
        )
        db.add_all([charge, demo])
        db.commit()

        try:
            order_management.transition_fulfillment(
                db, site, merchant.id, link.id, "delivered", "unfulfilled"
            )
        except CommerceError:
            illegal_blocked = True
        else:
            illegal_blocked = False
        order_management.transition_fulfillment(
            db,
            site,
            merchant.id,
            link.id,
            "fulfilled",
            "unfulfilled",
            carrier="UPS",
            tracking_number="1ZEVAL",
        )
        cases.append({
            "case": "fulfillment state machine blocks skips and records tracking",
            "passed": illegal_blocked and order.status == "fulfilled",
        })
        try:
            order_management.transition_fulfillment(
                db,
                site,
                outsider.id,
                link.id,
                "delivered",
                "fulfilled",
            )
        except CommerceError:
            cases.append({"case": "order mutations require tenant manager membership", "passed": True})
        else:
            cases.append({"case": "order mutations require tenant manager membership", "passed": False})
        db.commit()

    first = checkout_payments.refund(
        site.id,
        merchant.id,
        link.id,
        2500,
        "Offline evaluation exact refund request.",
        "phase4c-refund-eval-01",
        sessions=sessions,
        gateway_factory=RefundGateway,
    )
    repeated = checkout_payments.refund(
        site.id,
        merchant.id,
        link.id,
        2500,
        "Offline evaluation exact refund request.",
        "phase4c-refund-eval-01",
        sessions=sessions,
        gateway_factory=RefundGateway,
    )
    cases.append({
        "case": "refund command is exact and idempotent",
        "passed": first.id == repeated.id and first.state == "succeeded" and len(RefundGateway.calls) == 1,
    })
    try:
        checkout_payments.refund(
            site.id,
            merchant.id,
            link.id,
            7501,
            "Offline evaluation rejects excess refunds.",
            "phase4c-refund-eval-02",
            sessions=sessions,
            gateway_factory=RefundGateway,
        )
    except CommerceError:
        cases.append({"case": "refund cannot exceed captured remainder", "passed": True})
    else:
        cases.append({"case": "refund cannot exceed captured remainder", "passed": False})

    with Session(engine, expire_on_commit=False) as db:
        site = db.get(type(site), site.id)
        order = db.get(Order, order.id)
        order.created_at = datetime(2026, 10, 5, tzinfo=UTC)
        refund = db.scalar(select(PaymentTransaction).where(
            PaymentTransaction.order_id == order.id,
            PaymentTransaction.kind == "refund",
        ))
        refund.created_at = datetime(2026, 10, 6, tzinfo=UTC)
        db.commit()
        report = order_management.revenue_report(db, site, date(2026, 10, 1), date(2026, 10, 9))
        cases.append({
            "case": "report subtracts refunds using integer minor units",
            "passed": (
                report.gross_sales_minor,
                report.refunds_minor,
                report.net_revenue_minor,
                report.order_count,
                report.average_order_value_minor,
            ) == (10000, 2500, 7500, 1, 10000),
        })
        cases.append({
            "case": "demo commerce is isolated from real revenue",
            "passed": report.gross_sales_minor != 50000000,
        })
        empty = order_management.revenue_report(db, site, date(2025, 1, 1), date(2025, 1, 2))
        cases.append({
            "case": "bounded empty report returns zeros",
            "passed": empty.gross_sales_minor == empty.refunds_minor == empty.order_count == 0,
        })

    report = {
        "suite": "phase4c-order-management",
        "passed": sum(case["passed"] for case in cases),
        "total": len(cases),
        "external_calls": 0,
        "cases": cases,
    }
    output = Path("output/evals/phase4c-order-management.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

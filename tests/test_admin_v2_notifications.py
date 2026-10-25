"""P3 transactional emails retain P4 template controls and exact references."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app import order_management
from app.integrations.commerce_email import render_message
from app.models import CommerceMail, NotificationTemplate, RefundCommand
from app.services import money
from tests.test_order_admin import RefundGateway
from tests.test_order_management import workspace


def test_refund_emails_use_each_command_amount_and_merchant_override():
    with workspace() as w:
        for amount, key in ((2500, "first-refund-request"), (1000, "second-refund-request")):
            order_management.refund(w.site.id, w.owner.id, w.link.id, amount,
                "Customer returned an unopened item.", key, sessions=w.sessions,
                gateway_factory=RefundGateway)
        w.db.expire_all()
        mails = list(w.db.scalars(select(CommerceMail).where(CommerceMail.kind == "refund")
                                 .order_by(CommerceMail.created_at)))
        assert len(mails) == 2
        first = render_message(w.db, mails[0], w.site, w.customer)
        second = render_message(w.db, mails[1], w.site, w.customer)
        assert "25.00" in first[1] and "35.00" not in first[1]
        assert "10.00" in second[1] and "35.00" not in second[1]
        template = NotificationTemplate(tenant_id=w.site.tenant_id, site_id=w.site.id,
            event="refund", subject="Refund {{ order_number }}", body="Amount {{ total }}\n{{ message }}")
        w.db.add(template)
        w.db.flush()
        subject, body = render_message(w.db, mails[0], w.site, w.customer)
        assert subject == "Refund " + w.order.number
        assert body.startswith("Amount " + money(2500, "USD") + "\n")
        template.enabled = False
        w.db.flush()
        assert render_message(w.db, mails[0], w.site, w.customer) is None


@pytest.mark.parametrize("field", ["site_id", "tenant_id", "site_order_id", "state"])
def test_refund_override_cannot_bypass_command_scope_or_success(field):
    with workspace() as w:
        command = order_management.refund(w.site.id, w.owner.id, w.link.id, 2500,
            "Customer returned an unopened item.", "scoped-refund-request", sessions=w.sessions,
            gateway_factory=RefundGateway)
        w.db.expire_all()
        mail = w.db.scalar(select(CommerceMail).where(CommerceMail.kind == "refund"))
        command = w.db.get(RefundCommand, command.id)
        setattr(command, field, "pending" if field == "state" else "another-owner")
        w.db.flush()
        w.db.add(NotificationTemplate(tenant_id=w.site.tenant_id, site_id=w.site.id,
            event="refund", subject="Override", body="Private order {{ order_number }}"))
        w.db.flush()
        assert render_message(w.db, mail, w.site, w.customer) is None


def test_legacy_refund_reference_still_renders():
    with workspace() as w:
        order_management.refund(w.site.id, w.owner.id, w.link.id, 2500,
            "Customer returned an unopened item.", "legacy-refund-request", sessions=w.sessions,
            gateway_factory=RefundGateway)
        w.db.expire_all()
        mail = CommerceMail(tenant_id=w.site.tenant_id, site_id=w.site.id,
            customer_id=w.customer.id, kind="refund", dedupe_key="legacy-refund",
            available_at=datetime.now(UTC), reference_json={"site_order_id": w.link.id})
        assert "25.00" in render_message(w.db, mail, w.site, w.customer)[1]

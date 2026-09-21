from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import checkout_services as checkout
from app import customer_services as customers
from app.integrations.commerce_email import dispatch_mail, render_message
from app.models import CommerceMail, MarketingConsent, ShipmentEvent, SiteOrder
from app.services import CommerceError, money
from tests.test_checkout_services import open_session, prepare, ready  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


def paid(r):
    attempt = prepare(r)
    open_session(r, attempt)
    # sqlite3 legacy mode does not BEGIN for SELECT/SAVEPOINT. Establish the
    # caller's real transaction explicitly before testing its rollback boundary.
    r.db.connection().exec_driver_sql("BEGIN")
    checkout.reconcile(r.db, r.site, attempt.id, r.gateway)
    r.db.flush()
    link = r.db.scalar(select(SiteOrder))
    mail = r.db.scalar(select(CommerceMail).where(CommerceMail.kind == "order_confirmation"))
    return attempt, link, mail


def test_confirmation_is_atomic_idempotent_and_independent_of_consent(ready):
    r = ready
    attempt, link, mail = paid(r)
    checkout.reconcile(r.db, r.site, attempt.id, r.gateway)
    assert customers.queue_order_mail(r.db, r.site, link).id == mail.id
    assert r.db.scalar(select(func.count(CommerceMail.id))) == 1
    assert r.db.scalar(select(MarketingConsent)) is None
    subject, body = render_message(r.db, mail, r.site, r.customer)
    assert "Sandbox order confirmation" in subject
    assert "TEST ORDER" in body and "Powered by FastShop" in body
    assert "Sales tax: " + money(205, "USD") in body
    assert "/account/orders/" + link.id in body and "#token=" not in body
    assert mail.reference_json == {"site_order_id": link.id}
    r.db.rollback()
    assert r.db.scalar(select(func.count(CommerceMail.id))) == 0
    assert r.db.scalar(select(func.count(SiteOrder.id))) == 0


def test_transactional_dispatch_retries_and_does_not_require_verified_marketing_email(ready):
    r = ready
    _, _, mail = paid(r)
    r.customer.verified_at = None  # Guest purchasers also receive receipts.
    r.db.commit()
    sessions = sessionmaker(bind=r.db.get_bind(), expire_on_commit=False)
    captured = []

    def sender(recipient, subject, body, **kwargs):
        captured.append((recipient, body))
        return ("failed", "") if len(captured) == 1 else ("sent", "fixture-message-id")

    assert not dispatch_mail(mail.id, sessions=sessions, sender=sender)
    r.db.refresh(mail)
    mail.available_at = datetime.now(UTC) - timedelta(seconds=1)
    r.db.commit()
    assert dispatch_mail(mail.id, sessions=sessions, sender=sender)
    assert not dispatch_mail(mail.id, sessions=sessions, sender=sender)
    assert len(captured) == 2 and captured[0][0] == r.customer.email


def test_failed_order_settlement_cannot_leave_an_orphan_receipt(ready, monkeypatch):
    r = ready
    attempt = prepare(r)
    open_session(r, attempt)
    original = customers.queue_order_mail

    def fail_after_queue(*args, **kwargs):
        original(*args, **kwargs)
        raise CommerceError("Simulated settlement failure")

    monkeypatch.setattr(customers, "queue_order_mail", fail_after_queue)
    with pytest.raises(CommerceError, match="Simulated"):
        checkout.reconcile(r.db, r.site, attempt.id, r.gateway)
    # Even a caller that catches the error and commits must not send a receipt.
    r.db.commit()
    assert r.db.scalar(select(func.count(CommerceMail.id))) == 0
    assert r.db.scalar(select(func.count(SiteOrder.id))) == 0


def test_shipment_email_is_scoped_to_exact_event_and_order(ready):
    r = ready
    _, link, _ = paid(r)
    event = ShipmentEvent(tenant_id=r.site.tenant_id, site_id=r.site.id, site_order_id=link.id,
        carrier="DHL", tracking_number="TEST123", status="in_transit", note="Test parcel update")
    r.db.add(event)
    r.db.flush()
    mail = customers.queue_order_mail(r.db, r.site, link, shipment=event)
    assert customers.queue_order_mail(r.db, r.site, link, shipment=event).id == mail.id
    subject, body = render_message(r.db, mail, r.site, r.customer)
    assert "delivery update" in subject and "In Transit" in body and "TEST123" in body
    event.site_id = "different-site"
    r.db.flush()
    assert render_message(r.db, mail, r.site, r.customer) is None
    with pytest.raises(CommerceError, match="Shipment"):
        customers.queue_order_mail(r.db, r.site, link, shipment=event)


@pytest.mark.parametrize("field", ["customer_id", "site_id", "tenant_id"])
def test_confirmation_references_cannot_expose_another_owners_order(ready, field):
    r = ready
    _, link, mail = paid(r)
    setattr(link, field, "different-owner")
    r.db.flush()
    assert render_message(r.db, mail, r.site, r.customer) is None

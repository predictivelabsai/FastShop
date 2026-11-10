from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import checkout_services as checkout
from app import commerce
from app import customer_services as customers
from app.integrations.commerce_email import dispatch_mail, process_due_mail, render_message
from app.models import (
    Base,
    Channel,
    CommerceMail,
    MarketingConsent,
    ShipmentEvent,
    Site,
    SiteOrder,
    Tenant,
)
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


@pytest.mark.parametrize("accounts_enabled", [False, True])
def test_confirmation_is_atomic_idempotent_and_independent_of_consent(ready, accounts_enabled):
    r = ready
    attempt, link, mail = paid(r)
    commerce.settings_for(r.db, r.site).customer_accounts_enabled = accounts_enabled
    checkout.reconcile(r.db, r.site, attempt.id, r.gateway)
    assert customers.queue_order_mail(r.db, r.site, link).id == mail.id
    assert r.db.scalar(select(func.count(CommerceMail.id))) == 1
    assert r.db.scalar(select(MarketingConsent)) is None
    subject, body = render_message(r.db, mail, r.site, r.customer)
    assert "Sandbox order confirmation" in subject
    assert "TEST ORDER" in body and "Powered by FastShop" in body
    assert "Sales tax: " + money(205, "USD") in body
    assert ("/account/orders/" + link.id in body) == accounts_enabled
    assert ("sign-in required" in body) == accounts_enabled
    assert "#token=" not in body
    assert "transactional message about your order, not a marketing subscription" in body
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


@pytest.mark.parametrize("status", ["pending", "awaiting_configuration", "sending"])
def test_due_mail_worker_delivers_queued_order_confirmation(ready, status):
    r = ready
    _, _, mail = paid(r)
    mail.status = status
    if status != "pending":
        mail.attempts = 1
        mail.available_at = datetime.now(UTC) - timedelta(seconds=1)
    r.db.commit()
    sessions = sessionmaker(bind=r.db.get_bind(), expire_on_commit=False)
    captured = []

    def sender(recipient, subject, body, **kwargs):
        captured.append((recipient, subject, body))
        return "sent", "fixture-worker-message-id"

    result = process_due_mail(limit=10, sessions=sessions, sender=sender)
    r.db.refresh(mail)
    assert result == {"processed": 1, "accepted_by_provider": 1}
    assert mail.status == "sent"
    assert captured[0][0] == r.customer.email
    assert "order confirmation" in captured[0][1].lower()


def test_default_sessions_dispatch_only_after_mail_commit(monkeypatch):
    from app import db as database
    from app.integrations import commerce_email

    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    old_bind = database.SessionLocal.kw["bind"]
    database.SessionLocal.configure(bind=engine)
    sent = []
    monkeypatch.setattr(commerce_email, "dispatch_mail", sent.append)
    try:
        with database.SessionLocal() as db:
            db.add_all([
                Tenant(id="mail-hook-tenant", slug="mail-hook", name="Mail hook"),
                Channel(id="mail-hook-channel", tenant_id="mail-hook-tenant",
                        slug="mail-hook", name="Mail hook"),
                Site(id="mail-hook-site", tenant_id="mail-hook-tenant",
                     channel_id="mail-hook-channel", slug="mail-hook", name="Mail hook"),
                CommerceMail(id="mail-hook-message", tenant_id="mail-hook-tenant",
                    site_id="mail-hook-site", kind="account_verification",
                    dedupe_key="mail-hook-message", available_at=datetime.now(UTC)),
            ])
            db.flush()
            assert sent == []
            db.commit()
        assert sent == ["mail-hook-message"]

        with database.SessionLocal() as db:
            # Production rows rely on the generated id, which exists only after the INSERT.
            generated = CommerceMail(tenant_id="mail-hook-tenant", site_id="mail-hook-site",
                kind="account_verification", dedupe_key="mail-hook-generated",
                available_at=datetime.now(UTC))
            db.add(generated)
            db.commit()
        assert sent == ["mail-hook-message", generated.id] and generated.id

        with database.SessionLocal() as db:
            db.add(CommerceMail(tenant_id="mail-hook-tenant", site_id="mail-hook-site",
                kind="account_verification", dedupe_key="mail-hook-rollback",
                available_at=datetime.now(UTC)))
            db.flush()
            db.rollback()
        assert sent == ["mail-hook-message", generated.id]
    finally:
        database.SessionLocal.configure(bind=old_bind)
        engine.dispose()


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


@pytest.mark.parametrize("accounts_enabled", [False, True])
def test_shipment_email_is_scoped_to_exact_event_and_order(ready, accounts_enabled):
    r = ready
    _, link, _ = paid(r)
    commerce.settings_for(r.db, r.site).customer_accounts_enabled = accounts_enabled
    event = ShipmentEvent(tenant_id=r.site.tenant_id, site_id=r.site.id, site_order_id=link.id,
        carrier="DHL", tracking_number="TEST123", status="in_transit", note="Test parcel update")
    r.db.add(event)
    r.db.flush()
    mail = customers.queue_order_mail(r.db, r.site, link, shipment=event)
    assert customers.queue_order_mail(r.db, r.site, link, shipment=event).id == mail.id
    subject, body = render_message(r.db, mail, r.site, r.customer)
    assert "delivery update" in subject and "In Transit" in body and "TEST123" in body
    assert "DHL" in body and "Test parcel update" in body
    assert ("/account/orders/" + link.id in body) == accounts_enabled
    assert ("sign-in required" in body) == accounts_enabled
    assert "transactional message about your order, not a marketing subscription" in body
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

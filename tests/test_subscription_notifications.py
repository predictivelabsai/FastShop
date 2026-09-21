from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import commerce, subscription_renewals, subscriptions
from app import subscription_notifications as notices
from app.integrations.commerce_email import dispatch_mail, render_message
from app.models import CommerceMail, MarketingConsent
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_subscription_recovery import failed  # noqa: F401
from tests.test_subscription_renewals import RenewalGateway, options, prepare
from tests.test_subscriptions import contract  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


def test_failed_notice_is_transactional_and_idempotent(failed):
    r, sub, cycle, gateway = failed
    mail = r.db.scalar(select(CommerceMail).where(CommerceMail.kind == "renewal_failed"))
    assert mail and notices.queue_failed(r.db, r.site, sub, cycle).id == mail.id
    subscription_renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway))
    assert len(r.db.scalars(select(CommerceMail).where(CommerceMail.kind == "renewal_failed")).all()) == 1
    assert r.db.scalar(select(MarketingConsent)) is None
    subject, body = render_message(r.db, mail, r.site, r.customer)
    assert "Sandbox" in subject and "Future deliveries are paused" in body
    assert "/account/subscriptions/" + sub.id in body
    assert "Powered by FastShop" in body and "pm_fixture" not in body and "pi_" not in body


def test_stale_failure_notice_is_not_sent_after_recovery_or_resume(failed):
    r, sub, cycle, gateway = failed
    mail = r.db.scalar(select(CommerceMail).where(CommerceMail.kind == "renewal_failed"))
    sub.state = "active"
    r.db.commit()
    assert render_message(r.db, mail, r.site, r.customer) is None
    sub.state = "paused"
    cycle.state = "recovered"
    r.db.commit()
    assert render_message(r.db, mail, r.site, r.customer) is None


def test_precharge_expiry_queues_failure_without_charging(contract):
    r, sub = contract
    gateway = RenewalGateway()
    cycle = prepare(r, sub, gateway)
    from app.models import CheckoutAttempt, CommerceQuote
    attempt = r.db.get(CheckoutAttempt, cycle.attempt_id)
    r.db.get(CommerceQuote, attempt.quote_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    r.db.commit()
    assert subscription_renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway)) == "failed"
    assert r.db.scalar(select(CommerceMail).where(CommerceMail.kind == "renewal_failed"))
    assert gateway.created == 0


def test_processing_does_not_send_failure_notification(contract):
    r, sub = contract
    gateway = RenewalGateway()
    gateway.outcome = "processing"
    cycle = prepare(r, sub, gateway)
    assert subscription_renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway)) == "processing"
    assert not r.db.scalar(select(CommerceMail).where(CommerceMail.kind == "renewal_failed"))


def upcoming(r, sub):
    r.site.status = "preview"
    sub.next_due_at = datetime.now(UTC) + timedelta(days=2)
    r.db.commit()
    sessions = sessionmaker(bind=r.db.bind, expire_on_commit=False)
    assert notices.queue_upcoming(sessions=sessions) == 1
    mail = r.db.scalar(select(CommerceMail).where(CommerceMail.kind == "renewal_upcoming"))
    return sessions, mail


def test_upcoming_reminder_uses_recurring_price_not_intro_offer_and_sends_once(contract):
    r, sub = contract
    sessions, mail = upcoming(r, sub)
    assert notices.queue_upcoming(sessions=sessions) == 0
    captured = []

    def sender(recipient, subject, body, **kwargs):
        captured.append(body)
        return "sent", "fixture-message"

    assert dispatch_mail(mail.id, sessions=sessions, sender=sender)
    assert not dispatch_mail(mail.id, sessions=sessions, sender=sender)
    assert len(captured) == 1 and "$53.92" in captured[0] and "$48.53" not in captured[0]
    assert "not a final payment quote" in captured[0]


@pytest.mark.parametrize("change", ["paused", "cancelled", "version", "due", "disabled", "foreign"])
def test_upcoming_notice_is_cancelled_when_state_or_ownership_changes(contract, change):
    r, sub = contract
    _, mail = upcoming(r, sub)
    if change in ("paused", "cancelled"):
        sub.state = change
    elif change == "version":
        sub.version += 1
    elif change == "due":
        sub.next_due_at += timedelta(days=1)
    elif change == "disabled":
        commerce.settings_for(r.db, r.site).mode = "disabled"
    else:
        mail.reference_json = {"contract_id": "foreign-contract"}
    r.db.commit()
    assert render_message(r.db, mail, r.site, r.customer) is None


def test_changed_subscription_can_receive_corrected_reminder(contract):
    r, sub = contract
    sessions, old = upcoming(r, sub)
    subscriptions.change(r.db, r.site, r.customer.id, sub.id, "frequency", {"months": 2},
        version=sub.version, request_key="notice-frequency-change")
    r.db.commit()
    assert notices.queue_upcoming(sessions=sessions) == 1
    assert render_message(r.db, old, r.site, r.customer) is None
    assert notices.queue_upcoming(sessions=sessions) == 0

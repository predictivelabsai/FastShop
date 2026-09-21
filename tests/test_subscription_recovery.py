from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app import (
    checkout_payments,
    checkout_services,
    subscription_recovery,
    subscription_renewals,
    subscriptions,
)
from app.models import CommerceQuote, Order, SubscriptionContract, SubscriptionRecovery
from app.services import CommerceError
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_subscription_renewals import RenewalGateway, options, prepare
from tests.test_subscriptions import contract  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


@pytest.fixture
def failed(contract):
    r, sub = contract
    gateway = RenewalGateway()
    gateway.outcome = "requires_action"
    cycle = prepare(r, sub, gateway)
    assert subscription_renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway)) == "failed"
    r.db.expire_all()
    assert sub.state == "paused" and r.stock.allocated == 2
    return r, sub, cycle, gateway


def retry(r, sub, cycle, gateway, **kwargs):
    return subscription_recovery.prepare(r.db, r.site, r.customer.id, sub.id, cycle.id, gateway,
        version=kwargs.pop("version", sub.version), request_key=kwargs.pop("request_key", "recovery-request-0001"), **kwargs)


def test_recovery_is_fresh_stock_backed_one_time_payment_with_no_welcome_offer(failed):
    r, sub, cycle, gateway = failed
    version = sub.version
    attempt = retry(r, sub, cycle, gateway)
    assert retry(r, sub, cycle, gateway, version=version).id == attempt.id
    assert retry(r, sub, cycle, gateway, request_key="another-request-0002").id == attempt.id
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    assert quote.total_minor == 6597 and attempt.offer_id is None and r.stock.allocated == 4
    assert attempt.id != cycle.attempt_id and cycle.payment_intent_id == gateway.result["id"]
    command = checkout_payments.payment_command(r.site, attempt, quote, r.customer)
    assert command["session"]["mode"] == "payment"
    assert "setup_future_usage" not in command["session"]["payment_intent_data"]
    assert r.db.scalar(select(func.count(SubscriptionRecovery.id))) == 1
    assert sub.state == "paused"


@pytest.mark.parametrize("cancel_future", [False, True])
def test_paid_recovery_settles_once_without_new_contract_or_resuming(failed, cancel_future):
    r, sub, cycle, gateway = failed
    attempt = retry(r, sub, cycle, gateway)
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    r.db.commit()
    response = {"id": "cs_test_recovery1", "livemode": False, "mode": "payment",
        "client_reference_id": attempt.id, "metadata": {"site_id": r.site.id, "quote_id": quote.id},
        "currency": "usd", "amount_total": quote.total_minor, "status": "open", "payment_status": "unpaid",
        "automatic_tax": {"status": "complete"}, "total_details": {"amount_tax": quote.tax_minor, "amount_shipping": quote.shipping_minor},
        "url": "https://checkout.stripe.com/c/pay/cs_test_recovery1"}

    class HostedGateway:
        def create_checkout(self, attempt_id, payload):
            return response

        def checkout_status(self, session_id):
            return response

    hosted = HostedGateway()
    assert checkout_payments.handoff(r.site.id, r.customer.id, attempt.id, **options(r, hosted)) == response["url"]
    if cancel_future:
        subscriptions.change(r.db, r.site, r.customer.id, sub.id, "cancel", {},
            version=sub.version, request_key="cancel-during-payment-01")
        r.db.commit()
    response.update(status="complete", payment_status="paid", payment_intent="pi_recovery1")
    for _ in range(2):
        checkout_services.reconcile(r.db, r.site, attempt.id, hosted)
        r.db.commit()
    assert r.db.scalar(select(func.count(Order.id))) == 2
    assert r.db.scalar(select(func.count(SubscriptionContract.id))) == 1
    assert cycle.state == "recovered" and sub.state == ("cancelled" if cancel_future else "paused")
    assert sub.payment_method_id == "pm_fixture"
    assert checkout_services.utc(sub.next_due_at) > datetime.now(UTC)
    assert subscription_renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway)) == "recovered"
    subscription_renewals.settle(r.db, r.site, cycle.id, gateway.result)
    assert r.stock.allocated == 4 and cycle.state == "recovered"


def test_recovery_blocks_schedule_changes_but_not_future_cancellation(failed):
    r, sub, cycle, gateway = failed
    retry(r, sub, cycle, gateway)
    with pytest.raises(CommerceError, match="already processing"):
        subscriptions.change(r.db, r.site, r.customer.id, sub.id, "resume", {},
            version=sub.version, request_key="resume-recovery-0001")
    subscriptions.change(r.db, r.site, r.customer.id, sub.id, "cancel", {},
        version=sub.version, request_key="cancel-recovery-0001")
    assert sub.state == "cancelled"


def test_expired_recovery_can_be_requoted_without_reusing_attempt(failed):
    r, sub, cycle, gateway = failed
    first = retry(r, sub, cycle, gateway)
    checkout_services.cancel_unstarted(r.db, r.site, first.id)
    second = retry(r, sub, cycle, gateway, request_key="fresh-recovery-0002")
    assert first.id != second.id and r.stock.allocated == 4
    assert r.db.scalar(select(func.count(SubscriptionRecovery.id))) == 2


@pytest.mark.parametrize("state", ["processing", "requires_action", "paid", "cancelled"])
def test_unresolved_or_other_terminal_deliveries_cannot_be_retried(failed, state):
    r, sub, cycle, gateway = failed
    cycle.state = state
    r.db.commit()
    with pytest.raises(CommerceError, match="current failed"):
        retry(r, sub, cycle, gateway)
    assert r.stock.allocated == 2


def test_recovery_requires_owned_verified_customer(failed):
    r, sub, cycle, gateway = failed
    with pytest.raises(CommerceError, match="Subscription not found"):
        subscription_recovery.prepare(r.db, r.site, "other-customer", sub.id, cycle.id, gateway,
            version=sub.version, request_key="foreign-recovery-0001")
    r.customer.verified_at = None
    r.db.commit()
    with pytest.raises(CommerceError, match="Subscription not found"):
        retry(r, sub, cycle, gateway)

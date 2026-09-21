from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import subscription_renewals as renewals
from app import subscriptions
from app.commerce_webhooks import process_event
from app.integrations.stripe_commerce import StripeGateway
from app.models import (
    CheckoutAttempt,
    CommerceQuote,
    Order,
    SubscriptionCycle,
    VariantChannelListing,
)
from app.services import CommerceError
from tests.test_checkout_services import Gateway, ready  # noqa: F401
from tests.test_subscriptions import contract  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


class RenewalGateway(Gateway):
    def __init__(self):
        self.created, self.confirmed, self.tax_records = 0, 0, 0
        self.result = None
        self.outcome = "succeeded"
        self.tax_fails = False

    def create_renewal(self, cycle_id, payload):
        self.created += 1
        if self.result is None:
            self.result = payload | {"id": "pi_" + cycle_id, "status": "requires_confirmation",
                "livemode": False, "amount_received": 0}
        return self.result

    def payment_intent_status(self, intent_id):
        assert intent_id == self.result["id"]
        return self.result

    def confirm_renewal(self, cycle_id, intent_id):
        self.confirmed += 1
        self.result["status"] = self.outcome
        if self.outcome == "succeeded":
            self.result["amount_received"] = self.result["amount"]
        return self.result

    def cancel_renewal(self, intent_id):
        self.result["status"] = "canceled"
        return self.result

    def record_renewal_tax(self, cycle_id, calculation_id):
        self.tax_records += 1
        if self.tax_fails:
            raise CommerceError("Tax provider unavailable")
        return "tax_" + cycle_id


def prepare(r, sub, gateway):
    cycle = renewals.prepare_cycle(r.db, r.site, sub.id, gateway)
    r.db.commit()
    return cycle


def options(r, gateway):
    return {"sessions": sessionmaker(bind=r.db.bind, expire_on_commit=False), "gateway_factory": lambda site: gateway}


def test_renewal_discount_stock_payment_and_tax_posting_are_idempotent(contract):
    r, sub = contract
    gateway = RenewalGateway()
    cycle = prepare(r, sub, gateway)
    assert prepare(r, sub, gateway).id == cycle.id
    assert cycle.provider_payload_json["amount"] == 6597  # 2 × $26.96 + fixture shipping/tax, no welcome discount.
    assert r.stock.allocated == 4
    kwargs = options(r, gateway)
    assert renewals.run_cycle(r.site.id, cycle.id, **kwargs) == "paid"
    assert renewals.run_cycle(r.site.id, cycle.id, **kwargs) == "paid"
    assert (gateway.created, gateway.confirmed, gateway.tax_records) == (1, 1, 1)
    r.db.expire_all()
    assert r.db.scalar(select(func.count(Order.id))) == 2
    assert r.stock.allocated == 4
    assert subscriptions.checkout.utc(sub.next_due_at) > datetime.now(UTC)
    assert cycle.tax_transaction_id.startswith("tax_")


def test_paused_and_future_contracts_do_not_prepare_or_charge(contract):
    r, sub = contract
    gateway = RenewalGateway()
    sub.state = "paused"
    r.db.commit()
    assert prepare(r, sub, gateway) is None
    sub.state, sub.next_due_at = "active", datetime.now(UTC) + timedelta(days=30)
    r.db.commit()
    assert prepare(r, sub, gateway) is None
    assert gateway.created == 0 and r.stock.allocated == 2


@pytest.mark.parametrize("problem", ["stock", "price"])
def test_unavailable_stock_or_changed_price_prevents_renewal_charge(contract, problem):
    r, sub = contract
    if problem == "stock":
        r.stock.quantity = r.stock.allocated
    else:
        listing = r.db.scalar(select(VariantChannelListing).where(VariantChannelListing.variant_id == r.variant.id))
        listing.price_minor = 4000
    r.db.commit()
    gateway = RenewalGateway()
    with pytest.raises(CommerceError):
        prepare(r, sub, gateway)
    assert gateway.created == 0
    assert r.db.scalar(select(func.count(SubscriptionCycle.id))) == 0


@pytest.mark.parametrize("outcome", ["requires_action", "requires_payment_method"])
def test_decline_or_auth_requirement_cancels_before_release_and_pauses(contract, outcome):
    r, sub = contract
    gateway = RenewalGateway()
    gateway.outcome = outcome
    cycle = prepare(r, sub, gateway)
    assert renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway)) == "failed"
    r.db.expire_all()
    assert gateway.result["status"] == "canceled"
    assert r.stock.allocated == 2 and sub.state == "paused"
    assert r.db.scalar(select(func.count(Order.id))) == 1


def test_pause_before_confirmation_stops_charge_and_releases_stock(contract):
    r, sub = contract
    gateway = RenewalGateway()
    cycle = prepare(r, sub, gateway)
    subscriptions.change(r.db, r.site, r.customer.id, sub.id, "pause", {},
        version=sub.version, request_key="pause-before-charge-01")
    r.db.commit()
    assert renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway)) == "cancelled"
    r.db.refresh(r.stock)
    assert r.stock.allocated == 2 and gateway.created == gateway.confirmed == 0


def test_processing_payment_holds_stock_until_later_success(contract):
    r, sub = contract
    gateway = RenewalGateway()
    gateway.outcome = "processing"
    cycle = prepare(r, sub, gateway)
    kwargs = options(r, gateway)
    assert renewals.run_cycle(r.site.id, cycle.id, **kwargs) == "processing"
    r.db.expire_all()
    assert r.stock.allocated == 4 and r.db.scalar(select(func.count(Order.id))) == 1
    gateway.result.update(status="succeeded", amount_received=gateway.result["amount"])
    assert renewals.run_cycle(r.site.id, cycle.id, **kwargs) == "paid"
    assert gateway.confirmed == 1


def test_tax_posting_failure_does_not_repeat_charge_or_lose_paid_order(contract):
    r, sub = contract
    gateway = RenewalGateway()
    gateway.tax_fails = True
    cycle = prepare(r, sub, gateway)
    kwargs = options(r, gateway)
    with pytest.raises(CommerceError, match="Tax provider"):
        renewals.run_cycle(r.site.id, cycle.id, **kwargs)
    r.db.expire_all()
    assert cycle.state == "paid" and r.db.scalar(select(func.count(Order.id))) == 2
    gateway.tax_fails = False
    assert renewals.run_cycle(r.site.id, cycle.id, **kwargs) == "paid"
    assert gateway.confirmed == 1 and gateway.tax_records == 2


def test_expired_unstarted_quote_cannot_charge(contract):
    r, sub = contract
    gateway = RenewalGateway()
    cycle = prepare(r, sub, gateway)
    attempt = r.db.get(CheckoutAttempt, cycle.attempt_id)
    r.db.get(CommerceQuote, attempt.quote_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    r.db.commit()
    assert renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway)) == "failed"
    assert gateway.created == 0


def test_worker_prepares_due_cycle_and_does_not_backbill_missed_periods(contract):
    from scripts.process_subscription_renewals import process
    r, sub = contract
    gateway = RenewalGateway()
    kwargs = options(r, gateway)
    assert process(**kwargs)["paid"] == 1
    assert process(**kwargs)["prepared"] == 0
    assert gateway.confirmed == 1
    r.db.expire_all()
    assert r.db.scalar(select(func.count(Order.id))) == 2


def test_signed_boundary_processor_uses_current_intent_and_ignores_duplicate_events(contract):
    r, sub = contract
    gateway = RenewalGateway()
    gateway.outcome = "processing"
    cycle = prepare(r, sub, gateway)
    renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway))
    gateway.result.update(status="succeeded", amount_received=gateway.result["amount"])
    event = {"livemode": False, "type": "payment_intent.processing", "data": {"object": {
        "object": "payment_intent", "id": gateway.result["id"], "metadata": gateway.result["metadata"]}}}
    # Even an older 'processing' event resolves the current succeeded provider state.
    assert process_event(r.db, r.site, event, gateway) == "processed"
    assert process_event(r.db, r.site, event, gateway) == "processed"
    r.db.commit()
    assert r.db.scalar(select(func.count(Order.id))) == 2
    r.db.refresh(cycle)
    assert cycle.state == "paid"


def test_initial_checkout_handoff_and_worker_cannot_charge_a_renewal(contract):
    from app import checkout_payments
    from scripts.reconcile_checkouts import recover
    r, sub = contract
    gateway = RenewalGateway()
    cycle = prepare(r, sub, gateway)
    kwargs = options(r, gateway)
    for action in (checkout_payments.handoff, checkout_payments.cancel):
        with pytest.raises(CommerceError, match="subscription payment recovery"):
            action(r.site.id, r.customer.id, cycle.attempt_id, **kwargs)
    assert recover(**kwargs)["checked"] == 0
    assert gateway.created == 0


def test_lost_creation_response_replays_same_cycle_without_duplicate_charge(contract):
    r, sub = contract
    class LostResponse(RenewalGateway):
        def create_renewal(self, cycle_id, payload):
            result = super().create_renewal(cycle_id, payload)
            if self.created == 1:
                raise CommerceError("Creation response lost")
            return result
    gateway = LostResponse()
    cycle = prepare(r, sub, gateway)
    kwargs = options(r, gateway)
    with pytest.raises(CommerceError, match="lost"):
        renewals.run_cycle(r.site.id, cycle.id, **kwargs)
    r.db.refresh(r.stock)
    assert r.stock.allocated == 4 and gateway.confirmed == 0
    assert renewals.run_cycle(r.site.id, cycle.id, **kwargs) == "paid"
    assert gateway.confirmed == 1


def test_lost_confirmation_response_is_retrieved_not_charged_again(contract):
    r, sub = contract
    class LostConfirmation(RenewalGateway):
        def confirm_renewal(self, cycle_id, intent_id):
            super().confirm_renewal(cycle_id, intent_id)
            raise CommerceError("Confirmation response lost")
    gateway = LostConfirmation()
    cycle = prepare(r, sub, gateway)
    kwargs = options(r, gateway)
    with pytest.raises(CommerceError, match="lost"):
        renewals.run_cycle(r.site.id, cycle.id, **kwargs)
    assert renewals.run_cycle(r.site.id, cycle.id, **kwargs) == "paid"
    assert gateway.confirmed == 1


def test_mismatched_intent_is_not_confirmed_or_settled(contract):
    r, sub = contract
    class WrongCustomer(RenewalGateway):
        def create_renewal(self, cycle_id, payload):
            result = super().create_renewal(cycle_id, payload)
            result["customer"] = "cus_another"
            return result
    gateway = WrongCustomer()
    cycle = prepare(r, sub, gateway)
    with pytest.raises(CommerceError, match="do not match"):
        renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway))
    assert gateway.confirmed == 0 and r.db.scalar(select(func.count(Order.id))) == 1


def test_merchant_disabling_commerce_prevents_prepared_renewal_charge(contract):
    from app import commerce
    r, sub = contract
    gateway = RenewalGateway()
    cycle = prepare(r, sub, gateway)
    commerce.settings_for(r.db, r.site).mode = "disabled"
    r.db.commit()
    assert renewals.run_cycle(r.site.id, cycle.id, **options(r, gateway)) == "failed"
    assert gateway.created == gateway.confirmed == 0


def test_stripe_renewal_adapter_handles_card_error_by_fetching_known_intent(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fixture")
    calls = []
    def reply(request):
        calls.append((request.method, request.url.path, request.headers.get("idempotency-key")))
        fields = parse_qs(request.content.decode())
        if request.url.path.endswith("/confirm"):
            assert fields["off_session"] == ["true"]
            return httpx.Response(402, json={"error": {"message": "Fixture card needs authentication"}})
        if request.method == "GET":
            return httpx.Response(200, json={"id": "pi_fixture", "livemode": False, "status": "requires_action"})
        if request.url.path.endswith("/create_from_calculation"):
            assert fields["calculation"] == ["taxcalc_fixture"]
            assert fields["reference"] == ["fastshop-renewal-cycle1"]
            return httpx.Response(200, json={"id": "tax_fixture", "livemode": False})
        assert fields["confirm"] == ["false"]
        return httpx.Response(200, json={"id": "pi_fixture", "livemode": False, "status": "requires_confirmation"})
    gateway = StripeGateway(SimpleNamespace(id="fixture", slug="h24you"), transport=httpx.MockTransport(reply))
    gateway.create_renewal("cycle1", {"amount": 2696, "currency": "usd", "confirm": False})
    assert gateway.confirm_renewal("cycle1", "pi_fixture")["status"] == "requires_action"
    assert gateway.record_renewal_tax("cycle1", "taxcalc_fixture") == "tax_fixture"
    assert calls[0][2] == "fastshop-renewal-create-cycle1"
    assert calls[1][2] == "fastshop-renewal-confirm-cycle1"
    assert calls[2][:2] == ("GET", "/v1/payment_intents/pi_fixture")

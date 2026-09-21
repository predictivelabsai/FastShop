from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import checkout_payments, checkout_services, commerce, subscriptions
from app.models import (
    CommerceQuote,
    CustomerOffer,
    ProductVariant,
    SubscriptionContract,
    SubscriptionCycle,
    SubscriptionEvent,
    VariantChannelListing,
)
from app.services import CommerceError
from tests.test_checkout_services import open_session, ready  # noqa: F401
from tests.test_us_commerce import DESTINATION, shop  # noqa: F401


@pytest.fixture
def contract(ready):
    r = ready
    r.customer.name = "Subscriber Fixture"
    r.selections[0]["subscription"] = True
    offer = CustomerOffer(tenant_id=r.site.tenant_id, site_id=r.site.id, customer_id=r.customer.id,
        code="WELCOME-SUBSCRIPTION", expires_at=datetime.now(UTC) + timedelta(days=1))
    r.db.add(offer)
    r.db.flush()
    attempt = checkout_services.prepare(r.db, r.site, r.customer.id, r.selections, DESTINATION,
        r.gateway, request_key="subscription-initial-01", subscription_consent=True, code=offer.code)
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    assert quote.subtotal_minor - quote.discount_minor == 4853  # First order only: both savings.
    command = checkout_payments.payment_command(r.site, attempt, quote, r.customer)
    assert command["session"]["payment_intent_data"]["setup_future_usage"] == "off_session"
    open_session(r, attempt)
    r.gateway.result["customer"] = "cus_fixture"
    r.gateway.payment_intent_status = lambda intent_id: {"id": "pi_fixture", "status": "succeeded",
        "livemode": False, "customer": "cus_fixture", "payment_method": "pm_fixture",
        "setup_future_usage": "off_session", "currency": "usd", "amount_received": quote.total_minor,
        "created": int(datetime(2026, 1, 31, 12, tzinfo=UTC).timestamp())}
    checkout_services.reconcile(r.db, r.site, attempt.id, r.gateway)
    r.db.commit()
    return r, r.db.scalar(select(SubscriptionContract))


def change(r, sub, action, values=None, *, key=None, version=None, now=None):
    return subscriptions.change(r.db, r.site, r.customer.id, sub.id, action, values or {},
        version=sub.version if version is None else version, request_key=key or ("request-" + action + "-12345678"), now=now)


def test_initial_payment_activates_one_contract_and_keeps_renewal_price(contract):
    r, sub = contract
    assert sub.lines_json[0]["unit_minor"] == 2696
    assert sub.lines_json[0]["quantity"] == 2
    assert subscriptions.checkout.utc(sub.next_due_at) == datetime(2026, 2, 28, 12, tzinfo=UTC)
    checkout_services.reconcile(r.db, r.site, sub.initial_attempt_id, r.gateway)
    assert r.db.scalar(select(func.count(SubscriptionContract.id))) == 1
    assert sub.consent_json["version"] == subscriptions.CONSENT_VERSION


def test_subscription_checkout_requires_explicit_recurring_consent(ready):
    r = ready
    r.selections[0]["subscription"] = True
    with pytest.raises(CommerceError, match="recurring-payment consent"):
        checkout_services.prepare(r.db, r.site, r.customer.id, r.selections, DESTINATION,
            r.gateway, request_key="missing-consent-1234")
    assert r.stock.allocated == 0


def test_calendar_skip_restores_original_month_end_and_is_idempotent(contract):
    r, sub = contract
    version = sub.version
    change(r, sub, "skip", key="same-skip-request-01", version=version)
    change(r, sub, "skip", key="same-skip-request-01", version=version)
    assert subscriptions.checkout.utc(sub.next_due_at) == datetime(2026, 3, 31, 12, tzinfo=UTC)
    assert sub.version == version + 1
    assert r.db.scalar(select(func.count(SubscriptionEvent.id))) == 2
    with pytest.raises(CommerceError, match="different change"):
        change(r, sub, "pause", key="same-skip-request-01", version=version)


def test_pause_resume_no_backbilling_and_frequency_keeps_next_date(contract):
    r, sub = contract
    change(r, sub, "pause")
    assert sub.state == "paused"
    change(r, sub, "frequency", {"months": 2})
    change(r, sub, "resume", now=datetime(2026, 7, 10, tzinfo=UTC))
    assert sub.state == "active"
    assert subscriptions.checkout.utc(sub.next_due_at) == datetime(2026, 8, 31, 12, tzinfo=UTC)
    assert sub.interval_months == 2


def test_stale_versions_and_cross_customer_ids_cannot_mutate_contract(contract):
    r, sub = contract
    with pytest.raises(CommerceError, match="Reload"):
        change(r, sub, "pause", version=999)
    with pytest.raises(CommerceError, match="not found"):
        subscriptions.change(r.db, r.site, "another-customer", sub.id, "cancel", {},
            version=sub.version, request_key="foreign-cancel-123")
    assert sub.state == "active"


def test_flavour_same_product_and_price_only_and_address_is_us_only(contract):
    r, sub = contract
    new = ProductVariant(tenant_id=r.site.tenant_id, product_id=r.variant.product_id, sku="BERRY", name="Berry")
    r.db.add(new)
    r.db.flush()
    listing = VariantChannelListing(variant_id=new.id, channel_id=r.site.channel_id, currency="USD", price_minor=2995)
    r.db.add(listing)
    r.db.flush()
    change(r, sub, "flavour", {"from_variant_id": r.variant.id, "variant_id": new.id})
    assert sub.lines_json[0]["variant_id"] == new.id
    assert sub.lines_json[0]["unit_minor"] == 2696
    with pytest.raises(CommerceError, match="US states"):
        change(r, sub, "address", DESTINATION | {"country": "CA", "recipient_name": "Test"})
    change(r, sub, "address", DESTINATION | {"state": "NY", "postal_code": "10001", "recipient_name": "Changed Recipient"}, key="valid-address-12345")
    assert sub.destination_json["state"] == "NY"
    config = commerce.settings_for(r.db, r.site)
    config.subscription_product_ids_json = []
    with pytest.raises(CommerceError, match="not eligible"):
        change(r, sub, "flavour", {"from_variant_id": new.id, "variant_id": r.variant.id}, key="ineligible-flavour-01")


def test_cancel_during_charge_stops_future_cycles_but_does_not_claim_refund(contract):
    r, sub = contract
    cycle = SubscriptionCycle(tenant_id=r.site.tenant_id, site_id=r.site.id,
        contract_id=sub.id, due_at=sub.next_due_at, state="charging")
    r.db.add(cycle)
    r.db.flush()
    with pytest.raises(CommerceError, match="already processing"):
        change(r, sub, "skip")
    change(r, sub, "cancel")
    assert sub.state == "cancelled" and cycle.state == "charging"
    event = r.db.scalar(select(SubscriptionEvent).where(SubscriptionEvent.action == "cancel"))
    assert event.details_json["current_delivery_already_processing"] is True
    with pytest.raises(CommerceError, match="cancelled"):
        change(r, sub, "resume")


def test_wrong_customer_or_unsaved_payment_cannot_authorize_subscription(ready):
    r = ready
    quote = type("Quote", (), {"total_minor": 3000})()
    r.gateway.payment_intent_status = lambda intent: {"id": "pi_fixture", "livemode": False,
        "status": "succeeded", "customer": "cus_wrong", "payment_method": "pm_fixture",
        "setup_future_usage": "off_session", "currency": "usd", "amount_received": 3000,
        "created": int(datetime.now(UTC).timestamp())}
    with pytest.raises(CommerceError, match="could not be verified"):
        subscriptions.saved_payment(r.gateway, {"payment_intent": "pi_fixture", "customer": "cus_expected"}, quote)

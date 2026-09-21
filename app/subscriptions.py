"""FastShop owns delivery scheduling; Stripe holds and charges payment methods.

This is not a mirror of a native Stripe Subscription. A renewal is an explicit,
stock-backed cycle, never an automatically fulfilled copy of the first order.
"""

import calendar
import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import select

from app import checkout_services as checkout
from app import commerce
from app.models import ShopCustomer, SubscriptionContract, SubscriptionCycle, SubscriptionEvent
from app.services import CommerceError

CONSENT_VERSION = "monthly-deliveries-v1"
CONSENT_TEXT = (
    "I authorize recurring charges for the selected tablet deliveries, monthly at the displayed "
    "subscription merchandise price plus applicable shipping and destination tax. The first-order "
    "offer applies only once. I can skip, pause, change future deliveries or cancel in My account. "
    "Changes do not reverse a delivery whose payment is already processing."
)


def advance_months(value, months, anchor_day):
    value = checkout.utc(value)
    year, month = divmod(value.year * 12 + value.month - 1 + months, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(anchor_day, calendar.monthrange(year, month)[1]))


def consent_snapshot(accepted):
    if accepted is not True:
        raise CommerceError("Confirm recurring-payment consent before subscribing.")
    return {"accepted": True, "version": CONSENT_VERSION, "text": CONSENT_TEXT,
        "accepted_at": datetime.now(UTC).isoformat()}


def saved_payment(gateway, provider, quote):
    """Verify customer and saved payment method against Stripe, not posted identifiers."""
    intent = gateway.payment_intent_status(provider.get("payment_intent", ""))
    if (intent.get("status") != "succeeded" or intent.get("livemode") is not False or
            intent.get("id") != provider.get("payment_intent") or intent.get("customer") != provider.get("customer") or
            not re.fullmatch(r"cus_[A-Za-z0-9]+", str(intent.get("customer", ""))) or
            not re.fullmatch(r"pm_[A-Za-z0-9]+", str(intent.get("payment_method", ""))) or
            intent.get("setup_future_usage") != "off_session" or intent.get("currency") != "usd" or
            type(intent.get("created")) is not int or not 0 < intent["created"] <= int(datetime.now(UTC).timestamp()) + 300 or
            type(intent.get("amount_received")) is not int or intent["amount_received"] != quote.total_minor):
        raise CommerceError("Recurring payment authorization could not be verified.")
    return intent


def activate(db, site, attempt, quote, intent):
    """Called only after the initial checkout payment and saved-card consent are verified."""
    existing = db.scalar(select(SubscriptionContract).where(SubscriptionContract.site_id == site.id,
        SubscriptionContract.tenant_id == site.tenant_id, SubscriptionContract.initial_attempt_id == attempt.id))
    if existing:
        return existing
    consent = quote.snapshot_json.get("subscription_consent") or {}
    if consent.get("accepted") is not True or consent.get("version") != CONSENT_VERSION:
        raise CommerceError("Recurring-payment consent is missing.")
    lines = [{"variant_id": line["variant_id"], "product_id": line["product_id"], "quantity": line["quantity"],
        "unit_minor": line["unit_minor"], "name": line["name"], "subscription": True}
        for line in quote.snapshot_json["lines"] if line["subscription"]]
    if not lines or attempt.state != "paid":
        raise CommerceError("A paid subscription order is required.")
    started = datetime.fromtimestamp(intent["created"], UTC)
    contract = SubscriptionContract(tenant_id=site.tenant_id, site_id=site.id, customer_id=attempt.customer_id,
        initial_attempt_id=attempt.id, stripe_customer_id=intent["customer"], payment_method_id=intent["payment_method"],
        anchor_day=started.day, next_due_at=advance_months(started, 1, started.day), lines_json=lines,
        destination_json=quote.snapshot_json["destination"], recipient_name=quote.snapshot_json.get("recipient_name", ""),
        consent_json=consent)
    db.add(contract)
    db.flush()
    db.add(SubscriptionEvent(tenant_id=site.tenant_id, site_id=site.id, customer_id=attempt.customer_id,
        contract_id=contract.id, request_key="initial-" + attempt.id, request_hash=attempt.request_hash,
        action="started", details_json={"order_id": attempt.order_id, "next_due_at": contract.next_due_at.isoformat()}))
    return contract


def owned(db, site, customer_id, contract_id):
    contract = db.scalar(select(SubscriptionContract).join(ShopCustomer, ShopCustomer.id == SubscriptionContract.customer_id)
        .where(SubscriptionContract.id == contract_id, SubscriptionContract.site_id == site.id,
            SubscriptionContract.tenant_id == site.tenant_id, SubscriptionContract.customer_id == customer_id,
            ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id,
            ShopCustomer.is_active.is_(True), ShopCustomer.verified_at.is_not(None))
        .with_for_update(of=SubscriptionContract).execution_options(populate_existing=True))
    if not contract:
        raise CommerceError("Subscription not found.")
    return contract


def change(db, site, customer_id, contract_id, action, values, *, version, request_key, now=None):
    """Customer commands are versioned, idempotent and audited. No provider charge occurs here."""
    now = now or datetime.now(UTC)
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_key):
        raise CommerceError("Invalid subscription request key.")
    fingerprint = hashlib.sha256(json.dumps({"action": action, "values": values, "version": version}, sort_keys=True).encode()).hexdigest()
    with db.begin_nested():
        checkout.lock_site(db, site)
        contract = owned(db, site, customer_id, contract_id)
        previous = db.scalar(select(SubscriptionEvent).where(SubscriptionEvent.contract_id == contract.id,
            SubscriptionEvent.site_id == site.id, SubscriptionEvent.tenant_id == site.tenant_id,
            SubscriptionEvent.request_key == request_key))
        if previous:
            if previous.request_hash != fingerprint:
                raise CommerceError("This request key was already used for a different change.")
            return contract
        if contract.version != version:
            raise CommerceError("Your subscription changed. Reload before trying again.")
        if contract.state == "cancelled":
            raise CommerceError("This subscription has been cancelled.")
        processing = db.scalar(select(SubscriptionCycle.id).where(SubscriptionCycle.contract_id == contract.id,
            SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id,
            SubscriptionCycle.state.in_(["prepared", "creating", "charging", "requires_action", "processing"])))
        from app.subscription_recovery import pending
        processing = processing or pending(db, site, contract.id)
        if processing and action not in ("pause", "cancel"):
            raise CommerceError("This delivery is already processing. Changes can apply after it is resolved.")
        before = {"state": contract.state, "next_due_at": checkout.utc(contract.next_due_at).isoformat(), "version": contract.version}
        if action == "pause":
            if contract.state != "active":
                raise CommerceError("Only an active subscription can be paused.")
            contract.state = "paused"
        elif action == "resume":
            if contract.state != "paused":
                raise CommerceError("Only a paused subscription can be resumed.")
            contract.state = "active"
            while checkout.utc(contract.next_due_at) <= now:
                contract.next_due_at = advance_months(contract.next_due_at, contract.interval_months, contract.anchor_day)
        elif action == "skip":
            if contract.state != "active":
                raise CommerceError("Only an active subscription can skip a delivery.")
            contract.next_due_at = advance_months(contract.next_due_at, contract.interval_months, contract.anchor_day)
        elif action == "cancel":
            contract.state = "cancelled"
        elif action == "frequency":
            months = values.get("months")
            if type(months) is not int or not 1 <= months <= 12:
                raise CommerceError("Choose a delivery interval of 1 to 12 months.")
            contract.interval_months = months  # Keeps the already displayed next delivery date.
        elif action == "address":
            destination = commerce.address(values)
            config = commerce.settings_for(db, site)
            if not config or destination["state"] not in config.allowed_states_json:
                raise CommerceError("Shipping is not enabled for this state.")
            recipient = str(values.get("recipient_name", "")).strip()
            if not recipient or len(recipient) > 160:
                raise CommerceError("Enter the recipient's full name.")
            contract.destination_json, contract.recipient_name = destination, recipient
        elif action == "flavour":
            lines = [dict(line) for line in contract.lines_json]
            old = next((line for line in lines if line["variant_id"] == values.get("from_variant_id")), None)
            if not old:
                raise CommerceError("Subscription item not found.")
            config = commerce.settings_for(db, site)
            if not config:
                raise CommerceError("Commerce settings are unavailable.")
            new = commerce.price_lines(db, site, config, [{"variant_id": values.get("variant_id"),
                "quantity": old["quantity"], "subscription": True}])[0]
            if new.product_id != old["product_id"] or new.unit_minor != old["unit_minor"]:
                raise CommerceError("Choose a flavour of the same product and subscription price.")
            if any(line is not old and line["variant_id"] == new.variant_id for line in lines):
                raise CommerceError("That flavour is already included in this subscription.")
            old.update(variant_id=new.variant_id, name=new.name)
            contract.lines_json = lines
        else:
            raise CommerceError("Unknown subscription change.")
        contract.version += 1
        db.add(SubscriptionEvent(tenant_id=site.tenant_id, site_id=site.id, customer_id=customer_id,
            contract_id=contract.id, request_key=request_key, request_hash=fingerprint, action=action,
            details_json={"before": before, "after": {"state": contract.state,
                "next_due_at": checkout.utc(contract.next_due_at).isoformat(), "version": contract.version,
                "interval_months": contract.interval_months}, "values": values,
                "current_delivery_already_processing": bool(processing)}))
        db.flush()
        return contract

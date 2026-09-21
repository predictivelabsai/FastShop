"""Customer-requested, one-time hosted checkout for a confirmed failed delivery.

The original off-session attempt is never mutated or reused. An unknown charge
cannot enter this path; its stock must already have been safely released.
"""

import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import select

from app import checkout_services as checkout
from app import commerce, subscriptions
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.models import (
    CheckoutAttempt,
    Site,
    SubscriptionCycle,
    SubscriptionEvent,
    SubscriptionRecovery,
)
from app.services import CommerceError


def start(site_id, customer_id, contract_id, cycle_id, version, request_key, *, sessions=SessionLocal, gateway_factory=StripeGateway):
    with sessions() as db:
        site = db.scalar(select(Site).where(Site.id == site_id))
        if not site or site.status not in ("preview", "published"):
            raise CommerceError("The store must be open before retrying a delivery.")
        attempt = prepare(db, site, customer_id, contract_id, cycle_id, gateway_factory(site),
            version=version, request_key=request_key)
        db.commit()
        return attempt.id


def pending(db, site, contract_id):
    return db.scalar(select(CheckoutAttempt).join(SubscriptionRecovery,
        SubscriptionRecovery.attempt_id == CheckoutAttempt.id).join(SubscriptionCycle,
        SubscriptionCycle.id == SubscriptionRecovery.cycle_id).where(
        SubscriptionRecovery.site_id == site.id, SubscriptionRecovery.tenant_id == site.tenant_id,
        SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id,
        SubscriptionCycle.contract_id == contract_id,
        CheckoutAttempt.site_id == site.id, CheckoutAttempt.tenant_id == site.tenant_id,
        CheckoutAttempt.state.in_(checkout.ACTIVE)))


def prepare(db, site, customer_id, contract_id, cycle_id, gateway, *, version, request_key):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,48}", request_key):
        raise CommerceError("Invalid recovery request key.")
    fingerprint = hashlib.sha256(json.dumps({"cycle_id": cycle_id, "version": version}, sort_keys=True).encode()).hexdigest()
    key = "recover-" + request_key
    with db.begin_nested():
        checkout.lock_site(db, site)
        contract = subscriptions.owned(db, site, customer_id, contract_id)
        previous = db.scalar(select(SubscriptionEvent).where(SubscriptionEvent.contract_id == contract.id,
            SubscriptionEvent.site_id == site.id, SubscriptionEvent.tenant_id == site.tenant_id,
            SubscriptionEvent.request_key == key))
        if previous:
            if previous.action != "recovery_prepared" or previous.request_hash != fingerprint:
                raise CommerceError("This recovery key was already used for a different request.")
            return checkout.owned_attempt(db, site, previous.details_json["attempt_id"])
        if contract.version != version or contract.state != "paused":
            raise CommerceError("Reload this paused subscription before retrying a delivery.")
        cycle = db.scalar(select(SubscriptionCycle).where(SubscriptionCycle.id == cycle_id,
            SubscriptionCycle.contract_id == contract.id, SubscriptionCycle.site_id == site.id,
            SubscriptionCycle.tenant_id == site.tenant_id).with_for_update())
        if not cycle or cycle.state != "failed" or checkout.utc(cycle.due_at) != checkout.utc(contract.next_due_at):
            raise CommerceError("Only the current failed delivery can be retried.")
        original = checkout.owned_attempt(db, site, cycle.attempt_id)
        if original.state != "expired" or original.order_id:
            raise CommerceError("Reconcile the original payment before retrying.")
        current = pending(db, site, contract.id)
        if current:
            return current
        config = commerce.settings_for(db, site)
        if not config or config.mode != "sandbox" or contract.consent_json.get("accepted") is not True:
            raise CommerceError("Sandbox commerce and existing subscription consent are required.")
        selections = [{"variant_id": line["variant_id"], "quantity": line["quantity"], "subscription": True}
            for line in contract.lines_json]
        prices = {line.variant_id: line.unit_minor for line in commerce.price_lines(db, site, config, selections)}
        if any(prices[line["variant_id"]] != line["unit_minor"] for line in contract.lines_json):
            raise CommerceError("The agreed subscription price changed; merchant review is required.")
        attempt = checkout.prepare(db, site, customer_id, selections, contract.destination_json, gateway,
            request_key=key, recipient_name=contract.recipient_name, renewal_contract=contract, recovery_cycle=cycle)
        db.add(SubscriptionRecovery(tenant_id=site.tenant_id, site_id=site.id,
            cycle_id=cycle.id, attempt_id=attempt.id))
        db.add(SubscriptionEvent(tenant_id=site.tenant_id, site_id=site.id, customer_id=customer_id,
            contract_id=contract.id, request_key=key, request_hash=fingerprint, action="recovery_prepared",
            details_json={"cycle_id": cycle.id, "attempt_id": attempt.id, "resumes_subscription": False}))
        contract.version += 1
        db.flush()
        return attempt


def settle_recovery(db, site, attempt, quote):
    # Called inside checkout reconciliation's transaction, never from browser data.
    recovery = db.scalar(select(SubscriptionRecovery).where(SubscriptionRecovery.attempt_id == attempt.id,
        SubscriptionRecovery.site_id == site.id, SubscriptionRecovery.tenant_id == site.tenant_id))
    cycle = db.scalar(select(SubscriptionCycle).where(SubscriptionCycle.id == quote.snapshot_json.get("recovery_cycle_id"),
        SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id).with_for_update())
    if not recovery or not cycle or recovery.cycle_id != cycle.id or cycle.state != "failed":
        raise CommerceError("This recovery payment requires merchant reconciliation.")
    from app.subscription_renewals import load_contract
    contract = load_contract(db, site, cycle.contract_id)
    if contract.customer_id != attempt.customer_id or quote.snapshot_json.get("renewal_contract_id") != contract.id:
        raise CommerceError("Recovery customer does not match the subscription.")
    cycle.state = "recovered"
    due = subscriptions.advance_months(cycle.due_at, contract.interval_months, contract.anchor_day)
    while due <= datetime.now(UTC):
        due = subscriptions.advance_months(due, contract.interval_months, contract.anchor_day)
    contract.next_due_at = due
    contract.version += 1
    # Do not resume, change the stored card, or undo cancellation of future cycles.
    db.add(SubscriptionEvent(tenant_id=site.tenant_id, site_id=site.id, customer_id=attempt.customer_id,
        contract_id=contract.id, request_key="recovered-" + cycle.id, request_hash=attempt.request_hash,
        action="delivery_recovered", details_json={"cycle_id": cycle.id, "attempt_id": attempt.id,
            "order_id": attempt.order_id, "resumes_subscription": False}))

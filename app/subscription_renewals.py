"""Stock-backed renewal charges with durable provider commands and separate tax posting."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from app import checkout_services as checkout
from app import commerce, subscriptions
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.models import (
    CommerceQuote,
    ShopCustomer,
    Site,
    SiteCommerceSettings,
    SubscriptionContract,
    SubscriptionCycle,
    SubscriptionEvent,
)
from app.services import CommerceError
from app.subscription_notifications import queue_failed


def load_contract(db, site, contract_id):
    contract = db.scalar(select(SubscriptionContract).where(SubscriptionContract.id == contract_id,
        SubscriptionContract.site_id == site.id, SubscriptionContract.tenant_id == site.tenant_id)
        .with_for_update().execution_options(populate_existing=True))
    if not contract:
        raise CommerceError("Subscription not found.")
    return contract


def prepare_cycle(db, site, contract_id, gateway, *, now=None):
    now = now or datetime.now(UTC)
    with db.begin_nested():
        checkout.lock_site(db, site)
        contract = load_contract(db, site, contract_id)
        if contract.state != "active" or checkout.utc(contract.next_due_at) > now:
            return None
        existing = db.scalar(select(SubscriptionCycle).where(SubscriptionCycle.contract_id == contract.id,
            SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id,
            SubscriptionCycle.due_at == contract.next_due_at))
        if existing:
            return existing
        config = commerce.settings_for(db, site)
        if not config or config.mode != "sandbox":
            raise CommerceError("Sandbox commerce must be enabled before a renewal.")
        if contract.consent_json.get("accepted") is not True:
            raise CommerceError("Recurring-payment consent is missing.")
        selections = [{"variant_id": line["variant_id"], "quantity": line["quantity"], "subscription": True} for line in contract.lines_json]
        priced = commerce.price_lines(db, site, config, selections)
        prices = {line.variant_id: line.unit_minor for line in priced}
        if any(prices[line["variant_id"]] != line["unit_minor"] for line in contract.lines_json):
            raise CommerceError("The subscription price changed; customer review is required before charging.")
        cycle = SubscriptionCycle(tenant_id=site.tenant_id, site_id=site.id,
            contract_id=contract.id, due_at=contract.next_due_at)
        db.add(cycle)
        db.flush()
        attempt = checkout.prepare(db, site, contract.customer_id, selections, contract.destination_json, gateway,
            request_key="renewal-" + cycle.id, recipient_name=contract.recipient_name, renewal_contract=contract)
        quote = db.scalar(select(CommerceQuote).where(CommerceQuote.id == attempt.quote_id,
            CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id))
        cycle.attempt_id = attempt.id
        cycle.provider_payload_json = {"amount": quote.total_minor, "currency": "usd",
            "customer": contract.stripe_customer_id, "payment_method": contract.payment_method_id,
            "payment_method_types": ["card"], "confirm": False,
            "shipping": {"name": contract.recipient_name, "address": contract.destination_json},
            "metadata": {"site_id": site.id, "subscription_id": contract.id, "cycle_id": cycle.id, "quote_id": quote.id}}
        db.add(SubscriptionEvent(tenant_id=site.tenant_id, site_id=site.id, customer_id=contract.customer_id,
            contract_id=contract.id, request_key="prepared-" + cycle.id, request_hash=attempt.request_hash,
            action="renewal_prepared", details_json={"cycle_id": cycle.id, "quote_id": quote.id,
                "total_minor": quote.total_minor, "shipping_minor": quote.shipping_minor, "tax_minor": quote.tax_minor}))
        db.flush()
        return cycle


def load_cycle(db, site, cycle_id):
    # Site write lock is always acquired by caller first, so both workers and
    # customer commands share a consistent lock ordering.
    cycle = db.scalar(select(SubscriptionCycle).where(SubscriptionCycle.id == cycle_id,
        SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id)
        .with_for_update().execution_options(populate_existing=True))
    if not cycle:
        raise CommerceError("Renewal not found.")
    contract = load_contract(db, site, cycle.contract_id)
    attempt = checkout.owned_attempt(db, site, cycle.attempt_id)
    quote = db.scalar(select(CommerceQuote).where(CommerceQuote.id == attempt.quote_id,
        CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id))
    return cycle, contract, attempt, quote


def verify_intent(intent, cycle):
    payload = cycle.provider_payload_json
    if (not str(intent.get("id", "")).startswith("pi_") or intent.get("livemode") is not False or
            (cycle.payment_intent_id and intent["id"] != cycle.payment_intent_id) or
            intent.get("currency") != "usd" or type(intent.get("amount")) is not int or
            intent["amount"] != payload["amount"] or intent.get("customer") != payload["customer"] or
            intent.get("payment_method") != payload["payment_method"] or
            any((intent.get("metadata") or {}).get(key) != value for key, value in payload["metadata"].items())):
        raise CommerceError("Stripe renewal details do not match the reserved delivery.")


def settle(db, site, cycle_id, intent, *, now=None):
    """Only accepts provider-fetched responses at internal gateway/webhook boundaries."""
    now = now or datetime.now(UTC)
    checkout.lock_site(db, site)
    cycle, contract, attempt, quote = load_cycle(db, site, cycle_id)
    verify_intent(intent, cycle)
    if cycle.state in ("paid", "failed", "cancelled", "recovered"):
        return cycle
    status = intent.get("status")
    if status == "succeeded":
        if type(intent.get("amount_received")) is not int or intent["amount_received"] != quote.total_minor:
            raise CommerceError("The renewal payment amount requires reconciliation.")
        # Reuse order/inventory/outbox settlement, not initial subscription activation.
        checkout._paid_order(db, site, attempt, quote, {"payment_intent": intent["id"]})
        cycle.state = "paid"
        due = subscriptions.advance_months(cycle.due_at, contract.interval_months, contract.anchor_day)
        skipped = 0
        while due <= now:
            due = subscriptions.advance_months(due, contract.interval_months, contract.anchor_day)
            skipped += 1
        contract.next_due_at = due
        contract.version += 1
        db.add(SubscriptionEvent(tenant_id=site.tenant_id, site_id=site.id, customer_id=contract.customer_id,
            contract_id=contract.id, request_key="paid-" + cycle.id, request_hash=attempt.request_hash,
            action="renewed", details_json={"cycle_id": cycle.id, "order_id": attempt.order_id,
                "next_due_at": due.isoformat(), "missed_periods_not_charged": skipped}))
    elif status == "canceled":
        checkout._release(db, site, attempt)
        cycle.state = "cancelled" if contract.state != "active" else "failed"
        if contract.state == "active":
            contract.state = "paused"  # No silent repeated charges after a decline or expired quote.
            contract.version += 1
        db.add(SubscriptionEvent(tenant_id=site.tenant_id, site_id=site.id, customer_id=contract.customer_id,
            contract_id=contract.id, request_key="closed-" + cycle.id, request_hash=attempt.request_hash,
            action="renewal_" + cycle.state, details_json={"cycle_id": cycle.id, "payment_status": "canceled", "stock_released": True}))
        queue_failed(db, site, contract, cycle)
    elif status == "processing":
        cycle.state = "processing"
    elif status in ("requires_action", "requires_payment_method"):
        cycle.state = "requires_action"
    db.flush()
    return cycle


def run_cycle(site_id, cycle_id, *, sessions=SessionLocal, gateway_factory=StripeGateway):
    with sessions() as db:
        site = db.scalar(select(Site).where(Site.id == site_id))
        if not site:
            raise CommerceError("Store not found.")
        checkout.lock_site(db, site)
        cycle, contract, attempt, quote = load_cycle(db, site, cycle_id)
        gateway = gateway_factory(site)
        if cycle.state in ("failed", "cancelled", "recovered"):
            return cycle.state
        if cycle.state == "paid":
            tax_missing = not cycle.tax_transaction_id
            db.commit()
            if tax_missing:
                record_tax(site_id, cycle_id, sessions=sessions, gateway_factory=gateway_factory)
            return "paid"
        if cycle.state == "prepared":
            config = commerce.settings_for(db, site)
            if not config or config.mode != "sandbox" or contract.state != "active" or checkout.utc(quote.expires_at) <= datetime.now(UTC):
                checkout._release(db, site, attempt)
                cycle.state = "failed" if contract.state == "active" else "cancelled"
                if contract.state == "active":
                    contract.state = "paused"
                    contract.version += 1
                queue_failed(db, site, contract, cycle)
                db.commit()
                return cycle.state
            customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == contract.customer_id,
                ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.is_active.is_(True)))
            if not customer:
                raise CommerceError("The customer account is unavailable.")
            cycle.state, attempt.state, attempt.provider_started_at = "creating", "creating", datetime.now(UTC)
        if not cycle.payment_intent_id and (not attempt.provider_started_at or
                checkout.utc(attempt.provider_started_at) < datetime.now(UTC) - timedelta(hours=23)):
            raise CommerceError("This renewal requires provider reconciliation before retrying.")
        intent_id, payload = cycle.payment_intent_id, cycle.provider_payload_json
        db.commit()
        intent = gateway.payment_intent_status(intent_id) if intent_id else gateway.create_renewal(cycle_id, payload)
        checkout.lock_site(db, site)
        cycle, contract, attempt, quote = load_cycle(db, site, cycle_id)
        verify_intent(intent, cycle)
        cycle.payment_intent_id = intent["id"]
        enabled = db.scalar(select(SiteCommerceSettings.id).where(SiteCommerceSettings.site_id == site.id,
            SiteCommerceSettings.tenant_id == site.tenant_id, SiteCommerceSettings.mode == "sandbox"))
        customer_active = db.scalar(select(ShopCustomer.id).where(ShopCustomer.id == contract.customer_id,
            ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.is_active.is_(True)))
        # Claim before confirmation. A later pause/cancel applies to future cycles;
        # the customer UI must say this delivery is already processing.
        should_confirm = (intent.get("status") == "requires_confirmation" and contract.state == "active" and enabled and customer_active
            and checkout.utc(quote.expires_at) > datetime.now(UTC))
        if should_confirm:
            cycle.state = "charging"
        db.commit()
        if should_confirm:
            intent = gateway.confirm_renewal(cycle_id, intent["id"])
        # Failed/auth-required or unconfirmed stale payments are cancelled before
        # releasing stock. Unknown/processing outcomes keep reservations held.
        if intent.get("status") in ("requires_action", "requires_payment_method", "requires_confirmation"):
            intent = gateway.cancel_renewal(intent["id"])
        checkout.lock_site(db, site)
        cycle = settle(db, site, cycle_id, intent)
        state = cycle.state
        db.commit()
    if state == "paid":
        record_tax(site_id, cycle_id, sessions=sessions, gateway_factory=gateway_factory)
    return state


def record_tax(site_id, cycle_id, *, sessions=SessionLocal, gateway_factory=StripeGateway):
    with sessions() as db:
        site = db.scalar(select(Site).where(Site.id == site_id))
        if not site:
            raise CommerceError("Store not found.")
        checkout.lock_site(db, site)
        cycle, contract, attempt, quote = load_cycle(db, site, cycle_id)
        if cycle.state != "paid" or cycle.tax_transaction_id:
            return
        gateway = gateway_factory(site)
        calculation_id = quote.provider_id
        db.commit()
        transaction_id = gateway.record_renewal_tax(cycle_id, calculation_id)
        checkout.lock_site(db, site)
        cycle, contract, attempt, quote = load_cycle(db, site, cycle_id)
        if cycle.tax_transaction_id and cycle.tax_transaction_id != transaction_id:
            raise CommerceError("Tax transaction reference conflict.")
        cycle.tax_transaction_id = transaction_id
        db.commit()


def process_due(*, limit=50, sessions=SessionLocal, gateway_factory=StripeGateway):
    """Prepare due renewal cycles and advance any in-flight ones. Sandbox-gated by the
    gateway; safe to call repeatedly from a scheduler or CLI."""
    limit = max(1, min(limit, 500))
    counts = {"prepared": 0, "paid": 0, "failed": 0, "cancelled": 0, "pending": 0, "needs_attention": 0}
    now = datetime.now(UTC)
    with sessions() as db:
        due = db.execute(select(SubscriptionContract.id, SubscriptionContract.site_id)
            .where(SubscriptionContract.state == "active", SubscriptionContract.next_due_at <= now)
            .order_by(SubscriptionContract.updated_at, SubscriptionContract.id).limit(limit)).all()
    for contract_id, site_id in due:
        try:
            with sessions() as db:
                site = db.scalar(select(Site).where(Site.id == site_id))
                if not site:
                    raise CommerceError("Store not found.")
                cycle = prepare_cycle(db, site, contract_id, gateway_factory(site))
                db.commit()
                counts["prepared"] += int(cycle is not None)
        except CommerceError:
            counts["needs_attention"] += 1
        finally:
            with sessions() as db:
                site = db.scalar(select(Site).where(Site.id == site_id))
                if site:
                    db.execute(update(SubscriptionContract).where(SubscriptionContract.id == contract_id,
                        SubscriptionContract.site_id == site.id, SubscriptionContract.tenant_id == site.tenant_id)
                        .values(updated_at=datetime.now(UTC)))
                    db.commit()
    with sessions() as db:
        pending = db.execute(select(SubscriptionCycle.id, SubscriptionCycle.site_id).where(or_(
            SubscriptionCycle.state.in_(["prepared", "creating", "charging", "processing", "requires_action"]),
            (SubscriptionCycle.state == "paid") & (SubscriptionCycle.tax_transaction_id == "")))
            .order_by(SubscriptionCycle.updated_at, SubscriptionCycle.id).limit(limit)).all()
    for cycle_id, site_id in pending:
        try:
            state = run_cycle(site_id, cycle_id, sessions=sessions, gateway_factory=gateway_factory)
            counts[state if state in ("paid", "failed", "cancelled") else "pending"] += 1
        except CommerceError:
            counts["needs_attention"] += 1
        finally:
            with sessions() as db:
                site = db.scalar(select(Site).where(Site.id == site_id))
                if site:
                    db.execute(update(SubscriptionCycle).where(SubscriptionCycle.id == cycle_id,
                        SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id)
                        .values(updated_at=datetime.now(UTC)))
                    db.commit()
    return counts

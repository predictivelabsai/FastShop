"""Transactional checkout state, separate from the legacy demo payment path.

Callers commit prepare/begin_provider before network creation of a payment session.
They must derive the customer identity from a trusted session (or server-side guest
email lookup), never an arbitrary posted customer ID. Gateway reconciliation fetches
Stripe itself: a browser return, local timer or unverified JSON cannot settle an order.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app import commerce
from app.models import (
    CheckoutAttempt,
    CommerceQuote,
    CustomerOffer,
    InventoryReservation,
    Order,
    OrderLine,
    OutboxEvent,
    PaymentTransaction,
    ProductVariant,
    ShopCustomer,
    Site,
    SiteOrder,
    Stock,
    Warehouse,
)
from app.services import CommerceError

ACTIVE = ("prepared", "creating", "open")


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def lock_site(db, site):
    # A write lock also serializes SQLite, which ignores SELECT FOR UPDATE.
    # All checkout paths take site, then customer/offer, then stock in PK order.
    result = db.execute(update(Site).where(Site.id == site.id, Site.tenant_id == site.tenant_id)
        .values(updated_at=datetime.now(UTC)).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise CommerceError("Store not found.")


def owned_attempt(db, site, attempt_id):
    attempt = db.scalar(select(CheckoutAttempt).where(CheckoutAttempt.id == attempt_id,
        CheckoutAttempt.site_id == site.id, CheckoutAttempt.tenant_id == site.tenant_id)
        .with_for_update().execution_options(populate_existing=True))
    if not attempt:
        raise CommerceError("Checkout not found.")
    return attempt


def eligible_offer(db, site, customer, code):
    if not code:
        return None
    offer = db.scalar(select(CustomerOffer).where(CustomerOffer.site_id == site.id,
        CustomerOffer.tenant_id == site.tenant_id, CustomerOffer.customer_id == customer.id,
        CustomerOffer.code == code).with_for_update())
    paid = db.scalar(select(SiteOrder.id).join(Order, Order.id == SiteOrder.order_id).where(
        SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
        SiteOrder.customer_id == customer.id, Order.tenant_id == site.tenant_id,
        Order.payment_status == "paid"))
    if (not customer.verified_at or not offer or offer.percent != 10 or
            offer.redeemed_order_id or utc(offer.expires_at) <= datetime.now(UTC) or paid):
        raise CommerceError("This first-order code is unavailable for this customer.")
    return offer


def prepare(db, site, customer_id, selections, destination, gateway, *, request_key, code="", recipient_name="", subscription_consent=False, renewal_contract=None, recovery_cycle=None):
    """Atomically quote and reserve. Retrying the same command returns the same attempt.

    A savepoint prevents partially allocated stock even if the caller catches errors
    and commits its outer transaction. No provider payment session is created here.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_key):
        raise CommerceError("Invalid checkout request key.")
    destination = commerce.address(destination)
    code = str(code).strip().upper()
    fingerprint = hashlib.sha256(json.dumps({"customer": customer_id, "lines": selections,
        "destination": destination, "code": code, "recipient_name": recipient_name,
        "subscription_consent": subscription_consent,
        "renewal_contract": renewal_contract.id if renewal_contract else None,
        "recovery_cycle": recovery_cycle.id if recovery_cycle else None}, sort_keys=True).encode()).hexdigest()
    with db.begin_nested():
        lock_site(db, site)
        previous = db.scalar(select(CheckoutAttempt).where(CheckoutAttempt.site_id == site.id,
            CheckoutAttempt.tenant_id == site.tenant_id, CheckoutAttempt.request_key == request_key))
        if previous:
            if previous.request_hash != fingerprint:
                raise CommerceError("This checkout key was already used for a different request.")
            return previous
        customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == customer_id,
            ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id,
            ShopCustomer.is_active.is_(True)).with_for_update())
        if not customer:
            raise CommerceError("Customer not found.")
        # Serialize overlapping checkouts, including discounted and full-price attempts.
        # An unknown provider outcome must be reconciled before another attempt starts.
        pending = db.scalar(select(CheckoutAttempt.id).where(CheckoutAttempt.site_id == site.id,
            CheckoutAttempt.tenant_id == site.tenant_id, CheckoutAttempt.customer_id == customer.id,
            CheckoutAttempt.state.in_(ACTIVE)))
        if pending:
            raise CommerceError("Resume or cancel your existing checkout before starting another.")
        offer = eligible_offer(db, site, customer, code)
        config = commerce.settings_for(db, site)
        if not config:
            raise CommerceError("Commerce is not configured.")
        quote = commerce.quote_order(db, site, config, selections, destination, gateway,
            first_order_discount=offer is not None)
        quote.snapshot_json = quote.snapshot_json | {"recipient_name": recipient_name or customer.name}
        if any(line["subscription"] for line in quote.snapshot_json["lines"]):
            from app.subscriptions import consent_snapshot
            if renewal_contract:
                if recovery_cycle and (recovery_cycle.site_id != site.id or recovery_cycle.tenant_id != site.tenant_id or
                        recovery_cycle.contract_id != renewal_contract.id or recovery_cycle.state != "failed"):
                    raise CommerceError("Invalid recovery delivery.")
                if (renewal_contract.site_id != site.id or renewal_contract.tenant_id != site.tenant_id or
                        renewal_contract.customer_id != customer.id or
                        renewal_contract.state != ("paused" if recovery_cycle else "active") or code):
                    raise CommerceError("Invalid renewal contract.")
                quote.snapshot_json = quote.snapshot_json | {"subscription_consent": renewal_contract.consent_json,
                    "renewal_contract_id": renewal_contract.id}
                if recovery_cycle:
                    quote.snapshot_json = quote.snapshot_json | {"recovery_cycle_id": recovery_cycle.id}
            else:
                quote.snapshot_json = quote.snapshot_json | {"subscription_consent": consent_snapshot(subscription_consent)}
        attempt = CheckoutAttempt(tenant_id=site.tenant_id, site_id=site.id, customer_id=customer.id,
            quote_id=quote.id, offer_id=offer.id if offer else None, request_key=request_key,
            request_hash=fingerprint, expires_at=datetime.now(UTC) + timedelta(minutes=35))
        db.add(attempt)
        db.flush()
        quantities = Counter()
        for line in quote.snapshot_json["lines"]:
            quantities[line["variant_id"]] += line["quantity"]
        # Lock the entire relevant stock set in stable primary-key order, not cart order.
        stocks = db.scalars(select(Stock).join(Warehouse, Warehouse.id == Stock.warehouse_id)
            .join(ProductVariant, ProductVariant.id == Stock.variant_id).where(
                Warehouse.tenant_id == site.tenant_id, ProductVariant.tenant_id == site.tenant_id,
                Warehouse.country_code == quote.snapshot_json["origin"]["country"],
                Stock.variant_id.in_(quantities)).order_by(Stock.id).with_for_update(of=Stock)
            .execution_options(populate_existing=True)).all()
        for stock in stocks:
            quantity = min(quantities[stock.variant_id], max(0, stock.quantity - stock.allocated))
            if quantity:
                changed = db.execute(update(Stock).where(Stock.id == stock.id,
                    Stock.quantity - Stock.allocated >= quantity).values(allocated=Stock.allocated + quantity)
                    .execution_options(synchronize_session="fetch"))
                if changed.rowcount != 1:
                    raise CommerceError("Inventory changed. Please retry checkout.")
                db.add(InventoryReservation(tenant_id=site.tenant_id, site_id=site.id,
                    attempt_id=attempt.id, stock_id=stock.id, quantity=quantity))
                quantities[stock.variant_id] -= quantity
        if any(quantities.values()):
            raise CommerceError("There is not enough stock at the configured fulfilment origin.")
        db.flush()
        return attempt


def begin_provider(db, site, attempt_id):
    """Persist 'creating' BEFORE provider I/O; timeout leaves inventory held for retry."""
    lock_site(db, site)
    attempt = owned_attempt(db, site, attempt_id)
    if attempt.state == "creating":
        return attempt  # Retry the same provider idempotency key, never release on timeout.
    if attempt.state != "prepared":
        raise CommerceError("Checkout cannot start a new payment session in this state.")
    quote = db.scalar(select(CommerceQuote).where(CommerceQuote.id == attempt.quote_id,
        CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id))
    if not quote or utc(quote.expires_at) <= datetime.now(UTC):
        raise CommerceError("Your quote expired. Cancel this checkout and request a fresh quote.")
    attempt.state = "creating"
    db.flush()
    return attempt


def _release(db, site, attempt):
    reservations = db.scalars(select(InventoryReservation).where(
        InventoryReservation.attempt_id == attempt.id, InventoryReservation.site_id == site.id,
        InventoryReservation.tenant_id == site.tenant_id, InventoryReservation.state == "held")
        .order_by(InventoryReservation.stock_id)).all()
    for reservation in reservations:
        stock = db.scalar(select(Stock).join(Warehouse, Warehouse.id == Stock.warehouse_id).where(
            Stock.id == reservation.stock_id, Warehouse.tenant_id == site.tenant_id).with_for_update(of=Stock))
        if not stock:
            raise CommerceError("Reserved stock is missing; merchant reconciliation is required.")
        changed = db.execute(update(Stock).where(Stock.id == stock.id, Stock.allocated >= reservation.quantity)
            .values(allocated=Stock.allocated - reservation.quantity).execution_options(synchronize_session="fetch"))
        if changed.rowcount != 1:
            raise CommerceError("Reserved stock is inconsistent; merchant reconciliation is required.")
        reservation.state = "released"
    attempt.state = "expired"


def cancel_unstarted(db, site, attempt_id):
    with db.begin_nested():
        lock_site(db, site)
        attempt = owned_attempt(db, site, attempt_id)
        if attempt.state == "expired":
            return attempt
        if attempt.state != "prepared":
            raise CommerceError("Check Stripe's payment status before releasing this checkout.")
        _release(db, site, attempt)
        db.flush()
        return attempt


def reconcile(db, site, attempt_id, gateway):
    """Fetch authoritative provider state. Safe to call after duplicate webhooks.

    Expiration must be confirmed by Stripe. Local expiry cannot prove a payment
    failed: the customer may have paid while our webhook endpoint was unavailable.
    """
    with db.begin_nested():
        lock_site(db, site)
        attempt = owned_attempt(db, site, attempt_id)
        if attempt.state in ("paid", "expired"):
            return attempt
        if attempt.state not in ("creating", "open") or not attempt.stripe_session_id:
            raise CommerceError("Checkout has no provider session to reconcile.")
        result = gateway.checkout_status(attempt.stripe_session_id)
        quote = db.scalar(select(CommerceQuote).where(CommerceQuote.id == attempt.quote_id,
            CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id))
        if (result.get("id") != attempt.stripe_session_id or result.get("livemode") is not False or
                result.get("client_reference_id") != attempt.id or
                (result.get("metadata") or {}).get("site_id") != site.id):
            raise CommerceError("Stripe checkout details do not match the reserved order.")
        if result.get("status") == "expired" and result.get("payment_status") == "unpaid":
            _release(db, site, attempt)
            db.flush()
            return attempt
        if (result.get("currency") != quote.currency.lower() or
                type(result.get("amount_total")) is not int or result["amount_total"] != quote.total_minor):
            raise CommerceError("Stripe checkout details do not match the reserved order.")
        if attempt.provider_payload_json:
            from app.checkout_payments import matches_quote
            if not matches_quote(result, attempt, quote):
                raise CommerceError("Stripe's final tax details require merchant reconciliation.")
        if result.get("status") == "complete" and result.get("payment_status") == "paid":
            if result.get("mode") != "payment" or not str(result.get("payment_intent", "")).startswith("pi_"):
                raise CommerceError("This payment requires subscription reconciliation.")
            recovery = bool(quote.snapshot_json.get("recovery_cycle_id"))
            recurring = any(line["subscription"] for line in quote.snapshot_json["lines"]) and not recovery
            if recurring:
                from app import subscriptions
                intent = subscriptions.saved_payment(gateway, result, quote)
            _paid_order(db, site, attempt, quote, result)
            if recovery:
                from app.subscription_recovery import settle_recovery
                settle_recovery(db, site, attempt, quote)
            if recurring:
                subscriptions.activate(db, site, attempt, quote, intent)
        db.flush()
        return attempt


def _paid_order(db, site, attempt, quote, provider):
    customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == attempt.customer_id,
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id))
    if not customer:
        raise CommerceError("Customer reconciliation is required.")
    order = Order(tenant_id=site.tenant_id, channel_id=site.channel_id,
        number="FS-" + attempt.id.upper(), idempotency_key="stripe-" + attempt.id,
        email=customer.email, currency=quote.currency, subtotal_minor=quote.subtotal_minor,
        discount_minor=quote.discount_minor, shipping_minor=quote.shipping_minor,
        tax_minor=quote.tax_minor, total_minor=quote.total_minor, payment_status="paid",
        shipping_address_json=quote.snapshot_json["destination"] | {"name":
            attempt.provider_payload_json.get("customer", {}).get("shipping", {}).get("name", quote.snapshot_json.get("recipient_name") or customer.name)})
    db.add(order)
    db.flush()
    for line in quote.snapshot_json["lines"]:
        variant = db.scalar(select(ProductVariant).where(ProductVariant.id == line["variant_id"],
            ProductVariant.tenant_id == site.tenant_id))
        if not variant:
            raise CommerceError("Purchased variant is missing; merchant reconciliation is required.")
        db.add(OrderLine(order_id=order.id, variant_id=variant.id, sku=line.get("sku", variant.sku),
            product_name=line["name"], variant_name=line.get("variant_name", variant.name), quantity=line["quantity"],
            unit_price_minor=line["unit_minor"], total_minor=line["amount_minor"]))
    link = SiteOrder(tenant_id=site.tenant_id, site_id=site.id, order_id=order.id,
        customer_id=customer.id, quote_id=quote.id, stripe_checkout_id=attempt.stripe_session_id)
    db.add(link)
    db.flush()
    from app.customer_services import queue_order_mail
    queue_order_mail(db, site, link)
    db.add(PaymentTransaction(order_id=order.id, provider="stripe:" + site.id,
        external_id=provider["payment_intent"], status="succeeded", currency=quote.currency,
        amount_minor=quote.total_minor))
    if attempt.offer_id:
        offer = db.scalar(select(CustomerOffer).where(CustomerOffer.id == attempt.offer_id,
            CustomerOffer.site_id == site.id, CustomerOffer.tenant_id == site.tenant_id,
            CustomerOffer.customer_id == customer.id).with_for_update())
        if not offer or offer.redeemed_order_id:
            raise CommerceError("Discount redemption needs merchant reconciliation.")
        offer.redeemed_order_id = order.id
    db.execute(update(InventoryReservation).where(InventoryReservation.attempt_id == attempt.id,
        InventoryReservation.site_id == site.id, InventoryReservation.tenant_id == site.tenant_id,
        InventoryReservation.state == "held").values(state="committed"))
    # Allocated stock remains committed until fulfilment, as in the existing order model.
    db.add(OutboxEvent(tenant_id=site.tenant_id, topic="order.confirmed", aggregate_id=order.id,
        payload_json={"order_id": order.id, "site_id": site.id, "currency": quote.currency,
            "total_minor": quote.total_minor}))
    attempt.order_id, attempt.state = order.id, "paid"

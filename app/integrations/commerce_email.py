"""Retryable Postmark messages; the queue stores references, never bearer tokens."""

import os
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

from app import customer_services as customers
from app.db import SessionLocal
from app.models import (
    CommerceMail,
    CustomerChallenge,
    CustomerOffer,
    MarketingConsent,
    Order,
    ShipmentEvent,
    ShopCustomer,
    Site,
    SiteOrder,
    SubscriptionContract,
    SubscriptionCycle,
)
from app.services import money


def send_email(recipient, subject, body, *, message_id):
    token = os.getenv("POSTMARK_SERVER_TOKEN") or os.getenv("POSTMARK_API_TOKEN", "")
    sender = os.getenv("FASTSHOP_CONTACT_FROM") or os.getenv("FROM_EMAIL", "")
    if not token or not sender:
        return "awaiting_configuration", ""
    try:
        response = httpx.post("https://api.postmarkapp.com/email", timeout=15,
            headers={"X-Postmark-Server-Token": token}, json={"From": sender, "To": recipient,
                "Subject": subject, "TextBody": body, "MessageStream": "outbound",
                "TrackOpens": False, "TrackLinks": "None", "Metadata": {"fastshop_message_id": message_id}})
        response.raise_for_status()
        result = response.json()
        if result.get("ErrorCode") == 0 and result.get("MessageID"):
            return "sent", str(result["MessageID"])
    except (httpx.HTTPError, ValueError):
        pass
    return "failed", ""


def render_message(db, row, site, customer):
    if row.kind in {"renewal_failed", "renewal_upcoming"}:
        return render_subscription_message(db, row, site, customer)
    if row.kind in {"order_confirmation", "shipment_update"}:
        return render_order_message(db, row, site, customer)
    if row.kind in {"login", "newsletter"}:
        challenge = db.scalar(select(CustomerChallenge).where(CustomerChallenge.id == row.challenge_id,
            CustomerChallenge.site_id == site.id, CustomerChallenge.tenant_id == site.tenant_id,
            CustomerChallenge.customer_id == customer.id))
        if not challenge or challenge.consumed_at or customers.utc(challenge.expires_at) <= datetime.now(UTC):
            return None
        action = "Sign in to your account" if row.kind == "login" else "Confirm your email and marketing consent"
        body = f"{action} for {site.name}:\n\n{customers.confirmation_url(site, challenge)}\n\n"
        if row.kind == "newsletter":
            body += challenge.consent_json["text"] + "\n\n"
        return action + " — " + site.name, body + "This link expires and can be used once. If you did not request it, ignore this email.\n\nPowered by FastShop."
    if row.kind == "offer":
        offer = db.scalar(select(CustomerOffer).where(CustomerOffer.site_id == site.id,
            CustomerOffer.tenant_id == site.tenant_id, CustomerOffer.customer_id == customer.id))
        consent = db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site.id,
            MarketingConsent.tenant_id == site.tenant_id, MarketingConsent.customer_id == customer.id))
        if not offer or not consent or consent.status != "subscribed" or customers.utc(offer.expires_at) <= datetime.now(UTC):
            return None
        url = customers.customer_base(site) + f"/unsubscribe/{customer.id}#token=" + customers.unsubscribe_token(site, customer)
        return "Your first-order code — " + site.name, (
            f"Your 10% first-order merchandise code is {offer.code}. It expires {offer.expires_at:%Y-%m-%d}. "
            "It is tied to this email address and can be used once. On an eligible subscription it applies after the subscription saving, to the first delivery only.\n\n"
            f"Manage preferences or unsubscribe: {url}\n\nPowered by FastShop.")
    return None


def render_order_message(db, row, site, customer):
    # Do not consult marketing consent: these messages service a purchase.
    reference = row.reference_json or {}
    link = db.scalar(select(SiteOrder).where(SiteOrder.id == reference.get("site_order_id"),
        SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
        SiteOrder.customer_id == customer.id))
    if not link:
        return None
    order = db.scalar(select(Order).where(Order.id == link.order_id, Order.tenant_id == site.tenant_id))
    if not order or order.payment_status != "paid":
        return None
    # Phase 2 currently permits test payments only; never imply real fulfilment.
    heading = "Sandbox order confirmation"
    body = f"{site.name}\nOrder {order.number}\n\nTEST ORDER — no real payment or shipment.\n\n"
    if row.kind == "shipment_update":
        shipment = db.scalar(select(ShipmentEvent).where(ShipmentEvent.id == reference.get("shipment_id"),
            ShipmentEvent.site_order_id == link.id, ShipmentEvent.site_id == site.id,
            ShipmentEvent.tenant_id == site.tenant_id))
        if not shipment:
            return None
        heading = "Sandbox delivery update"
        body += f"Status: {shipment.status.replace('_', ' ').title()}\n"
        if shipment.carrier or shipment.tracking_number:
            body += f"Carrier: {shipment.carrier}\nTracking number: {shipment.tracking_number}\n"
        if shipment.note:
            body += shipment.note + "\n"
        # The account page validates ownership and presents the vetted carrier link.
    else:
        for line in order.lines:
            body += f"{line.product_name} · {line.variant_name} × {line.quantity}: {money(line.total_minor, order.currency)}\n"
        body += (f"\nMerchandise: {money(order.subtotal_minor, order.currency)}\n"
            f"Savings: {money(order.discount_minor, order.currency)}\n"
            f"Shipping: {money(order.shipping_minor, order.currency)}\n"
            f"Sales tax: {money(order.tax_minor, order.currency)}\n"
            f"Total paid: {money(order.total_minor, order.currency)}\n")
    url = customers.customer_base(site) + "/account/orders/" + link.id
    body += (f"\nView your order and delivery updates (sign-in required): {url}\n\n"
        "This is a transactional message about your order, not a marketing subscription.\n\nPowered by FastShop.")
    return f"{heading} — {order.number} — {site.name}", body


def render_subscription_message(db, row, site, customer):
    from app import commerce
    reference = row.reference_json or {}
    contract = db.scalar(select(SubscriptionContract).where(SubscriptionContract.id == reference.get("contract_id"),
        SubscriptionContract.site_id == site.id, SubscriptionContract.tenant_id == site.tenant_id,
        SubscriptionContract.customer_id == customer.id))
    if not contract:
        return None
    body = f"{site.name}\n\nSANDBOX SUBSCRIPTION — test mode, no real charges.\n\n"
    if row.kind == "renewal_failed":
        cycle = db.scalar(select(SubscriptionCycle).where(SubscriptionCycle.id == reference.get("cycle_id"),
            SubscriptionCycle.contract_id == contract.id, SubscriptionCycle.site_id == site.id,
            SubscriptionCycle.tenant_id == site.tenant_id))
        if not cycle or cycle.state != "failed" or contract.state != "paused" or customers.utc(contract.next_due_at) != customers.utc(cycle.due_at):
            return None
        from app.subscription_recovery import pending
        if pending(db, site, contract.id):
            return None
        subject = "Sandbox delivery needs attention"
        body += (f"Your delivery scheduled for {cycle.due_at:%Y-%m-%d} UTC was not completed. "
            "Future deliveries are paused. No successful payment was recorded for this delivery.\n\n"
            "Sign in to review a fresh one-time checkout for the missed delivery, update your saved card, "
            "or manage future deliveries. Retrying the missed delivery does not automatically resume the subscription.\n")
    else:
        config = commerce.settings_for(db, site)
        if (contract.state != "active" or contract.version != reference.get("version") or
                customers.utc(contract.next_due_at).isoformat() != reference.get("due_at") or
                customers.utc(contract.next_due_at) <= datetime.now(UTC) or
                site.status not in ("preview", "published") or not config or config.mode != "sandbox"):
            return None
        subject = "Upcoming sandbox delivery"
        amount = sum(line["unit_minor"] * line["quantity"] for line in contract.lines_json)
        body += (f"Your next delivery is scheduled for {contract.next_due_at:%Y-%m-%d} UTC.\n"
            f"Recurring merchandise: {money(amount, 'USD')}. Shipping and destination sales tax are calculated per delivery; "
            "this notice is not a final payment quote. The first-order offer does not repeat.\n\n"
            "You can skip, pause, change or cancel future deliveries in My account before payment processing begins.\n")
    url = customers.customer_base(site) + "/account/subscriptions/" + contract.id
    body += f"\nManage subscription (sign-in required): {url}\n\nThis is a transactional subscription notice, not marketing.\n\nPowered by FastShop."
    return subject + " — " + site.name, body


def dispatch_mail(message_id, *, sessions=SessionLocal, sender=send_email):
    now = datetime.now(UTC)
    with sessions() as db:
        claimed = db.execute(update(CommerceMail).where(CommerceMail.id == message_id,
            CommerceMail.status.in_(["pending", "failed", "awaiting_configuration", "sending"]),
            CommerceMail.available_at <= now, CommerceMail.attempts < 5).values(
                status="sending", available_at=now + timedelta(minutes=5), attempts=CommerceMail.attempts + 1))
        if claimed.rowcount != 1:
            db.rollback()
            return False
        db.commit()
        row = db.get(CommerceMail, message_id)
        site = db.scalar(select(Site).where(Site.id == row.site_id, Site.tenant_id == row.tenant_id))
        customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == row.customer_id,
            ShopCustomer.site_id == row.site_id, ShopCustomer.tenant_id == row.tenant_id, ShopCustomer.is_active.is_(True)))
        message = render_message(db, row, site, customer) if site and customer else None
        if not message:
            row.status = "cancelled"
            db.commit()
            return False
        subject, body = message
        status, provider_id = sender(customer.email, subject, body, message_id=row.id)
        row.status, row.provider_id = status, provider_id
        row.available_at = datetime.now(UTC) + timedelta(minutes=min(60, 2 ** row.attempts))
        db.commit()
        return status == "sent"

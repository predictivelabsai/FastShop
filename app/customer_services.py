"""Tenant/site-owned customer identity, consent and first-order offers."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
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
    new_id,
)
from app.services import CommerceError

CONSENT_VERSION = "2026-09-21-v1"


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def normalized_email(value):
    email = str(value).strip().lower()
    if len(email) > 320 or not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", email):
        raise CommerceError("Enter a valid email address.")
    return email


def consent_text(site):
    return f"I agree to receive marketing emails from {site.name}. Unsubscribe anytime."


def customer_for(db, site, email, *, create=False):
    email = normalized_email(email)
    query = select(ShopCustomer).where(ShopCustomer.site_id == site.id,
        ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.email == email)
    customer = db.scalar(query)
    if not customer and create:
        try:
            with db.begin_nested():
                customer = ShopCustomer(tenant_id=site.tenant_id, site_id=site.id, email=email)
                db.add(customer)
                db.flush()
        except IntegrityError:
            customer = db.scalar(query)
    return customer


def signed_in_customer(db, site, session):
    customer_id = session.get("customer:" + site.id)
    if not customer_id:
        return None
    return db.scalar(select(ShopCustomer).where(ShopCustomer.id == customer_id,
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id,
        ShopCustomer.is_active.is_(True), ShopCustomer.verified_at.is_not(None)))


def challenge_token(challenge):
    message = f"fastshop-customer-v1:{challenge.site_id}:{challenge.id}:{challenge.purpose}"
    return hmac.new(settings.session_secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def customer_base(site):
    return "https://" + site.hostname if site.hostname else settings.public_url + "/sites/" + site.slug


def confirmation_url(site, challenge):
    # The fragment is never sent to the HTTP server or included in access logs.
    return customer_base(site) + f"/account/verify/{challenge.id}#token=" + challenge_token(challenge)


def queue_mail(db, site, customer, kind, dedupe_key, *, challenge=None, reference=None):
    row = db.scalar(select(CommerceMail).where(CommerceMail.dedupe_key == dedupe_key,
        CommerceMail.site_id == site.id, CommerceMail.tenant_id == site.tenant_id))
    if not row:
        row = CommerceMail(tenant_id=site.tenant_id, site_id=site.id, customer_id=customer.id,
            kind=kind, challenge_id=challenge.id if challenge else None, dedupe_key=dedupe_key,
            available_at=datetime.now(UTC), reference_json=reference or {})
        db.add(row)
        db.flush()
    return row


def queue_order_mail(db, site, link, *, shipment=None):
    """Queue transactionally; callers commit the order/event and message together."""
    if link.site_id != site.id or link.tenant_id != site.tenant_id:
        raise CommerceError("Order does not belong to this store.")
    customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == link.customer_id,
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id))
    if not customer:
        raise CommerceError("Order customer is missing.")
    reference = {"site_order_id": link.id}
    kind, key = "order_confirmation", f"order:{site.id}:{link.id}"
    if shipment:
        if shipment.site_order_id != link.id or shipment.site_id != site.id or shipment.tenant_id != site.tenant_id:
            raise CommerceError("Shipment does not belong to this order.")
        reference["shipment_id"] = shipment.id
        kind, key = "shipment_update", f"shipment:{site.id}:{shipment.id}"
    return queue_mail(db, site, customer, kind, key, reference=reference)


def request_email_challenge(db, site, email, purpose, client_address, *, consent=False):
    if purpose not in {"login", "newsletter"}:
        raise CommerceError("Unknown email verification request.")
    if purpose == "newsletter" and not consent:
        raise CommerceError("Tick the marketing consent box to request the offer.")
    email = normalized_email(email)
    now = datetime.now(UTC)
    client_hash = hmac.new(settings.session_secret.encode(), f"{site.id}:{client_address}".encode(), hashlib.sha256).hexdigest()
    # Serialize issuance for this site so concurrent requests cannot bypass quotas.
    db.scalar(select(Site.id).where(Site.id == site.id, Site.tenant_id == site.tenant_id).with_for_update())
    ip_count = db.scalar(select(func.count()).select_from(CustomerChallenge).where(
        CustomerChallenge.site_id == site.id, CustomerChallenge.tenant_id == site.tenant_id,
        CustomerChallenge.created_at >= now - timedelta(hours=1), CustomerChallenge.client_hash == client_hash))
    if ip_count >= 10:
        return None
    customer = customer_for(db, site, email, create=True)
    if not customer.is_active:
        return None
    hour = now - timedelta(hours=1)
    count = db.scalar(select(func.count()).select_from(CustomerChallenge).where(
        CustomerChallenge.site_id == site.id, CustomerChallenge.tenant_id == site.tenant_id,
        CustomerChallenge.created_at >= hour,
        or_(CustomerChallenge.client_hash == client_hash, CustomerChallenge.customer_id == customer.id)))
    recent = db.scalar(select(CustomerChallenge.id).where(CustomerChallenge.site_id == site.id,
        CustomerChallenge.tenant_id == site.tenant_id, CustomerChallenge.customer_id == customer.id,
        CustomerChallenge.purpose == purpose, CustomerChallenge.created_at >= now - timedelta(seconds=60)))
    if recent or count >= 10:
        return None  # Same public response; do not disclose account or subscription existence.
    challenge = CustomerChallenge(id=new_id(), tenant_id=site.tenant_id, site_id=site.id,
        customer_id=customer.id, purpose=purpose, client_hash=client_hash,
        expires_at=now + timedelta(minutes=20 if purpose == "login" else 60),
        consent_json={"version": CONSENT_VERSION, "text": consent_text(site), "source": "first-order-offer",
                      "requested_at": now.isoformat()} if purpose == "newsletter" else {})
    challenge.token_hash = hashlib.sha256(challenge_token(challenge).encode()).hexdigest()
    db.add(challenge)
    db.flush()
    return queue_mail(db, site, customer, purpose, "challenge:" + challenge.id, challenge=challenge)


def consume_challenge(db, site, challenge_id, token):
    challenge = db.scalar(select(CustomerChallenge).where(CustomerChallenge.id == challenge_id,
        CustomerChallenge.site_id == site.id, CustomerChallenge.tenant_id == site.tenant_id))
    now = datetime.now(UTC)
    if (not challenge or challenge.consumed_at or utc(challenge.expires_at) <= now or
            not hmac.compare_digest(challenge.token_hash, hashlib.sha256(str(token).encode()).hexdigest())):
        raise CommerceError("This link is invalid, expired or already used. Request a new email.")
    customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == challenge.customer_id,
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id,
        ShopCustomer.is_active.is_(True)).with_for_update().execution_options(populate_existing=True))
    if not customer:
        raise CommerceError("This account is unavailable.")
    claimed = db.execute(update(CustomerChallenge).where(CustomerChallenge.id == challenge.id,
        CustomerChallenge.site_id == site.id, CustomerChallenge.tenant_id == site.tenant_id,
        CustomerChallenge.consumed_at.is_(None), CustomerChallenge.expires_at > now).values(consumed_at=now).execution_options(synchronize_session="fetch"))
    if claimed.rowcount != 1:
        raise CommerceError("This verification link has already been used.")
    customer.verified_at = customer.verified_at or now
    if challenge.purpose == "newsletter":
        record = db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site.id,
            MarketingConsent.tenant_id == site.tenant_id, MarketingConsent.customer_id == customer.id))
        if not record:
            record = MarketingConsent(tenant_id=site.tenant_id, site_id=site.id, customer_id=customer.id)
            db.add(record)
        record.status, record.confirmed_at, record.unsubscribed_at = "subscribed", now, None
        record.consent_json = {**challenge.consent_json, "confirmed_at": now.isoformat(), "challenge_id": challenge.id}
        offer = db.scalar(select(CustomerOffer).where(CustomerOffer.site_id == site.id,
            CustomerOffer.tenant_id == site.tenant_id, CustomerOffer.customer_id == customer.id))
        already_paid = db.scalar(select(SiteOrder.id).join(Order, Order.id == SiteOrder.order_id).where(
            SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
            SiteOrder.customer_id == customer.id, Order.tenant_id == site.tenant_id, Order.payment_status == "paid"))
        if not offer and not already_paid:
            offer = CustomerOffer(tenant_id=site.tenant_id, site_id=site.id, customer_id=customer.id,
                code="WELCOME-" + secrets.token_hex(5).upper(), expires_at=now + timedelta(days=30))
            db.add(offer)
            db.flush()
            queue_mail(db, site, customer, "offer", "offer:" + offer.id)
    db.flush()
    return customer, challenge.purpose


def unsubscribe(db, site, customer):
    if customer.site_id != site.id or customer.tenant_id != site.tenant_id:
        raise CommerceError("Account not found.")
    db.scalar(select(ShopCustomer.id).where(ShopCustomer.id == customer.id,
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id).with_for_update())
    now = datetime.now(UTC)
    record = db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site.id,
        MarketingConsent.tenant_id == site.tenant_id, MarketingConsent.customer_id == customer.id))
    if record:
        record.status, record.unsubscribed_at = "unsubscribed", now
    # An older confirmation email must not reverse a later withdrawal.
    db.execute(update(CustomerChallenge).where(CustomerChallenge.site_id == site.id,
        CustomerChallenge.tenant_id == site.tenant_id, CustomerChallenge.customer_id == customer.id,
        CustomerChallenge.purpose == "newsletter", CustomerChallenge.consumed_at.is_(None)).values(consumed_at=now))


def unsubscribe_token(site, customer):
    return hmac.new(settings.session_secret.encode(), f"unsubscribe:{site.id}:{customer.id}".encode(), hashlib.sha256).hexdigest()


def orders_for(db, site, customer):
    return list(db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
        SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id, SiteOrder.customer_id == customer.id,
        Order.tenant_id == site.tenant_id).order_by(Order.created_at.desc())))


def tracking_for(db, site, site_order_id):
    return list(db.scalars(select(ShipmentEvent).where(ShipmentEvent.site_id == site.id,
        ShipmentEvent.tenant_id == site.tenant_id, ShipmentEvent.site_order_id == site_order_id).order_by(ShipmentEvent.created_at.desc())))

"""Consent-gated abandoned checkout email automation and cart recovery tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app import checkout_services, marketing_email
from app.config import settings
from app.customer_services import customer_base
from app.db import SessionLocal
from app.models import (
    CheckoutAttempt,
    CommerceMail,
    CommerceQuote,
    MarketingAbandonedEmail,
    MarketingAutomation,
    MarketingSubscriber,
    Order,
    ShopCustomer,
    Site,
    SiteCart,
    SiteOrder,
)
from app.services import CommerceError

RECOVERY_LIFETIME = timedelta(days=7)
ABANDONED_BATCH_SIZE = 50


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def automation_for(db, site, *, lock=False):
    query = select(MarketingAutomation).where(
        MarketingAutomation.site_id == site.id,
        MarketingAutomation.tenant_id == site.tenant_id,
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def save_automation(db, site, values):
    try:
        delay_hours = int(values.get("delay_hours", 4))
    except (TypeError, ValueError) as exc:
        raise CommerceError("Choose a delay from 1 to 72 hours.") from exc
    if not 1 <= delay_hours <= 72:
        raise CommerceError("Choose a delay from 1 to 72 hours.")
    subject = str(values.get("subject", "")).strip()[:200]
    if not subject or any(char in subject for char in "\r\n"):
        raise CommerceError("Enter a one-line email subject.")
    body_html = marketing_email.sanitize_email_html(values.get("body_html", ""))
    body_text = marketing_email.plain_text_from_html(body_html)
    if not body_text:
        raise CommerceError("Enter an email body.")
    checkout_services.lock_site(db, site)
    automation = automation_for(db, site, lock=True)
    if not automation:
        automation = MarketingAutomation(
            tenant_id=site.tenant_id, site_id=site.id,
        )
        db.add(automation)
    automation.enabled = values.get("enabled") == "on"
    automation.delay_hours = delay_hours
    automation.subject = subject
    automation.body_html = body_html
    automation.body_text = body_text
    automation.include_recovery_link = values.get("include_recovery_link") == "on"
    db.flush()
    return automation


def recovered_order_count(db, site) -> int:
    return int(db.scalar(select(func.count(func.distinct(MarketingAbandonedEmail.recovered_order_id))).where(
        MarketingAbandonedEmail.tenant_id == site.tenant_id,
        MarketingAbandonedEmail.site_id == site.id,
        MarketingAbandonedEmail.recovered_order_id.is_not(None),
    )) or 0)


def record_recovered_order(db, site, attempt_id, order_id) -> bool:
    cart = db.scalar(select(SiteCart).where(
        SiteCart.checkout_id == attempt_id,
        SiteCart.site_id == site.id,
        SiteCart.tenant_id == site.tenant_id,
    ).with_for_update())
    if not cart or not cart.recovery_email_id:
        return False
    record = db.scalar(select(MarketingAbandonedEmail).where(
        MarketingAbandonedEmail.id == cart.recovery_email_id,
        MarketingAbandonedEmail.site_id == site.id,
        MarketingAbandonedEmail.tenant_id == site.tenant_id,
    ).with_for_update())
    if not record or record.recovered_order_id is not None:
        return False
    record.recovered_order_id = order_id
    record.recovered_at = datetime.now(UTC)
    db.flush()
    return True


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def create_recovery_token(site, attempt_id, *, expires_at=None) -> tuple[str, datetime]:
    expires_at = expires_at or datetime.now(UTC) + RECOVERY_LIFETIME
    expires_at = _utc(expires_at)
    expires_at = datetime.fromtimestamp(int(expires_at.timestamp()), UTC)
    payload = _b64(f"{attempt_id}.{int(expires_at.timestamp())}".encode("ascii"))
    message = f"fastshop-cart-recovery-v1:{site.id}:{payload}".encode()
    signature = hmac.new(settings.session_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return payload + "." + signature, expires_at


def verify_recovery_token(site, token, *, now=None) -> tuple[str, datetime] | None:
    parts = str(token or "").split(".")
    if len(parts) != 2 or len(parts[1]) != 64:
        return None
    payload, signature = parts
    message = f"fastshop-cart-recovery-v1:{site.id}:{payload}".encode()
    expected = hmac.new(settings.session_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("ascii")
        attempt_id, expiry_value = decoded.rsplit(".", 1)
        expires_at = datetime.fromtimestamp(int(expiry_value), UTC)
    except (ValueError, UnicodeDecodeError, OverflowError):
        return None
    now = _utc(now or datetime.now(UTC))
    if len(attempt_id) != 32 or expires_at <= now:
        return None
    return attempt_id, expires_at


def recovery_url(site, token):
    return customer_base(site) + "/cart/recover?token=" + token


def _completed_order_since(db, site, attempt):
    return db.scalar(select(Order.id).join(SiteOrder, SiteOrder.order_id == Order.id).where(
        SiteOrder.site_id == site.id,
        SiteOrder.tenant_id == site.tenant_id,
        SiteOrder.customer_id == attempt.customer_id,
        Order.tenant_id == site.tenant_id,
        Order.payment_status.in_(("paid", "partially_refunded", "refunded")),
        Order.created_at > attempt.created_at,
    ).limit(1)) is not None


def _eligible_subscriber(db, site, customer):
    return db.scalar(select(MarketingSubscriber).where(
        MarketingSubscriber.site_id == site.id,
        MarketingSubscriber.tenant_id == site.tenant_id,
        MarketingSubscriber.email == customer.email,
        MarketingSubscriber.status == "subscribed",
    ))


def _queue_abandoned(db, site, automation, attempt, customer, subscriber, *, now):
    token, token_expiry = create_recovery_token(site, attempt.id, expires_at=now + RECOVERY_LIFETIME)
    record = MarketingAbandonedEmail(
        tenant_id=site.tenant_id, site_id=site.id, automation_id=automation.id,
        attempt_id=attempt.id, subscriber_id=subscriber.id,
        recovery_expires_at=token_expiry,
    )
    db.add(record)
    db.flush()
    mail = CommerceMail(
        tenant_id=site.tenant_id, site_id=site.id, customer_id=customer.id, user_id=None,
        kind="marketing_abandoned",
        dedupe_key=f"marketing-abandoned:{attempt.id}",
        available_at=now,
        reference_json={
            "abandoned_email_id": record.id,
            "attempt_id": attempt.id,
            "subscriber_id": subscriber.id,
            "recipient_email": customer.email,
            "recovery_token": token,
        },
    )
    db.add(mail)
    db.flush()
    record.message_id = mail.id
    return True


def queue_due_abandoned(*, sessions=SessionLocal, now=None, limit=ABANDONED_BATCH_SIZE):
    """Queue eligible recovery notices once per checkout, under the site write lock."""
    now = _utc(now or datetime.now(UTC))
    with sessions() as db:
        site_ids = list(db.scalars(select(MarketingAutomation.site_id).where(
            MarketingAutomation.enabled.is_(True),
        ).order_by(MarketingAutomation.site_id)))
    queued = 0
    scanned = 0
    for site_id in site_ids:
        if queued >= limit:
            break
        with sessions() as db:
            automation = db.scalar(select(MarketingAutomation).where(
                MarketingAutomation.site_id == site_id,
                MarketingAutomation.enabled.is_(True),
            ))
            site = db.scalar(select(Site).where(
                Site.id == site_id, Site.tenant_id == automation.tenant_id,
            )) if automation else None
            if not site or not automation:
                continue
            checkout_services.lock_site(db, site)
            automation = automation_for(db, site, lock=True)
            if not automation or not automation.enabled:
                db.rollback()
                continue
            cutoff = now - timedelta(hours=automation.delay_hours)
            candidates = db.execute(select(CheckoutAttempt, ShopCustomer)
                .join(CommerceQuote, CommerceQuote.id == CheckoutAttempt.quote_id)
                .join(ShopCustomer, ShopCustomer.id == CheckoutAttempt.customer_id)
                .where(
                    CheckoutAttempt.site_id == site.id,
                    CheckoutAttempt.tenant_id == site.tenant_id,
                    CheckoutAttempt.state.in_(checkout_services.ACTIVE),
                    CheckoutAttempt.order_id.is_(None),
                    CheckoutAttempt.created_at <= cutoff,
                    CommerceQuote.site_id == site.id,
                    CommerceQuote.tenant_id == site.tenant_id,
                    ShopCustomer.site_id == site.id,
                    ShopCustomer.tenant_id == site.tenant_id,
                    ~select(MarketingAbandonedEmail.id).where(
                        MarketingAbandonedEmail.attempt_id == CheckoutAttempt.id,
                        MarketingAbandonedEmail.site_id == site.id,
                        MarketingAbandonedEmail.tenant_id == site.tenant_id,
                    ).exists(),
                ).order_by(CheckoutAttempt.created_at, CheckoutAttempt.id)
                .limit(max(1, limit - queued)).with_for_update(of=CheckoutAttempt)).all()
            for attempt, customer in candidates:
                scanned += 1
                subscriber = _eligible_subscriber(db, site, customer)
                if not subscriber or _completed_order_since(db, site, attempt):
                    continue
                if _queue_abandoned(db, site, automation, attempt, customer, subscriber, now=now):
                    queued += 1
            db.commit()
    return {"scanned": scanned, "queued": queued}


def render_abandoned_message(db, row, site, customer):
    reference = row.reference_json or {}
    record = db.scalar(select(MarketingAbandonedEmail).where(
        MarketingAbandonedEmail.id == reference.get("abandoned_email_id"),
        MarketingAbandonedEmail.site_id == site.id,
        MarketingAbandonedEmail.tenant_id == site.tenant_id,
    ))
    automation = db.scalar(select(MarketingAutomation).where(
        MarketingAutomation.id == record.automation_id,
        MarketingAutomation.site_id == site.id,
        MarketingAutomation.tenant_id == site.tenant_id,
    )) if record else None
    attempt = db.scalar(select(CheckoutAttempt).where(
        CheckoutAttempt.id == reference.get("attempt_id"),
        CheckoutAttempt.site_id == site.id,
        CheckoutAttempt.tenant_id == site.tenant_id,
        CheckoutAttempt.customer_id == customer.id,
        CheckoutAttempt.state.in_(checkout_services.ACTIVE),
        CheckoutAttempt.order_id.is_(None),
    )) if customer else None
    subscriber = db.scalar(select(MarketingSubscriber).where(
        MarketingSubscriber.id == reference.get("subscriber_id"),
        MarketingSubscriber.site_id == site.id,
        MarketingSubscriber.tenant_id == site.tenant_id,
        MarketingSubscriber.email == customer.email,
        MarketingSubscriber.status == "subscribed",
    )) if customer else None
    if (not record or not automation or not automation.enabled or not attempt or
            not subscriber or _completed_order_since(db, site, attempt)):
        return None
    unsubscribe = marketing_email._unsubscribe_url(site, subscriber)
    extra_html = ""
    extra_text = ""
    if automation.include_recovery_link:
        if _utc(record.recovery_expires_at) <= datetime.now(UTC):
            return None
        token = str(reference.get("recovery_token", ""))
        if not verify_recovery_token(site, token):
            return None
        url = recovery_url(site, token)
        extra_html = '<p><a href="' + html.escape(url, quote=True) + '">Return to your basket</a></p>'
        extra_text = "Return to your basket: " + url
    return (
        automation.subject,
        marketing_email._text_email(site, automation.body_text, extra_text=extra_text,
                                    unsubscribe_url=unsubscribe),
        marketing_email._html_email(site, automation.body_html, extra_html=extra_html,
                                    unsubscribe_url=unsubscribe),
        {
            "List-Unsubscribe": "<" + unsubscribe + ">",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )


def sync_mail_result(db, mail):
    reference = mail.reference_json or {}
    if mail.kind != "marketing_abandoned":
        return
    record = db.scalar(select(MarketingAbandonedEmail).where(
        MarketingAbandonedEmail.id == reference.get("abandoned_email_id"),
        MarketingAbandonedEmail.site_id == mail.site_id,
        MarketingAbandonedEmail.tenant_id == mail.tenant_id,
    ))
    if not record:
        return
    if mail.status == "sent":
        record.status = "sent"
        record.sent_at = datetime.now(UTC)
    elif mail.status == "cancelled":
        record.status = "cancelled"
    elif mail.status in {"failed", "awaiting_configuration"} and mail.attempts >= 5:
        record.status = "failed"


def restore_cart(db, site, token, session):
    verified = verify_recovery_token(site, token)
    if not verified:
        raise CommerceError("This basket recovery link has expired or is invalid.")
    attempt_id, expiry = verified
    record = db.scalar(select(MarketingAbandonedEmail).where(
        MarketingAbandonedEmail.attempt_id == attempt_id,
        MarketingAbandonedEmail.site_id == site.id,
        MarketingAbandonedEmail.tenant_id == site.tenant_id,
        MarketingAbandonedEmail.status == "sent",
    ))
    attempt = db.scalar(select(CheckoutAttempt).where(
        CheckoutAttempt.id == attempt_id,
        CheckoutAttempt.site_id == site.id,
        CheckoutAttempt.tenant_id == site.tenant_id,
    ))
    if (not record or not attempt or _utc(record.recovery_expires_at) != expiry or
            attempt.state == "paid" or attempt.order_id):
        raise CommerceError("This basket recovery link is expired or no longer available.")
    quote = db.scalar(select(CommerceQuote).where(
        CommerceQuote.id == attempt.quote_id,
        CommerceQuote.site_id == site.id,
        CommerceQuote.tenant_id == site.tenant_id,
    ))
    if not quote:
        raise CommerceError("The basket could not be restored.")
    cart = db.scalar(select(SiteCart).where(
        SiteCart.checkout_id == attempt.id,
        SiteCart.site_id == site.id,
        SiteCart.tenant_id == site.tenant_id,
    ).with_for_update())
    if not cart:
        lines = quote.snapshot_json.get("lines", [])
        cart = SiteCart(
            tenant_id=site.tenant_id, site_id=site.id,
            lines_json=[{"variant_id": line["variant_id"], "quantity": line["quantity"],
                         "subscription": bool(line.get("subscription"))} for line in lines],
            checkout_id=attempt.id,
        )
        db.add(cart)
        db.flush()
    cart.recovery_email_id = record.id
    expired_unstarted = attempt.state == "prepared" and _utc(quote.expires_at) <= datetime.now(UTC)
    if expired_unstarted:
        checkout_services.cancel_unstarted(db, site, attempt.id)
    if expired_unstarted or attempt.state == "expired":
        cart.checkout_id = None
        cart.version += 1
        cart.request_key = secrets.token_hex(16)
    session["cart:" + site.id] = cart.id
    db.flush()
    return cart

"""Site-scoped subscriber records, consent sync and portable CSV data."""

from __future__ import annotations

import csv
import io
import re
import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models import MarketingConsent, MarketingSubscriber, ShopCustomer
from app.services import CommerceError

STATUSES = {"pending", "subscribed", "unsubscribed"}
SOURCES = {"footer", "checkout", "account", "admin", "import"}
MAX_CSV_BYTES = 1_000_000
EMAIL_PATTERN = re.compile(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+")


def normalized_email(value):
    email = str(value or "").strip().lower()
    if len(email) > 320 or not EMAIL_PATTERN.fullmatch(email):
        raise CommerceError("Enter a valid email address.")
    return email


def consent_wording(site):
    return f"I agree to receive marketing emails from {site.name}. Unsubscribe anytime."


def _row(db, site, email, *, lock=False):
    query = select(MarketingSubscriber).where(
        MarketingSubscriber.tenant_id == site.tenant_id,
        MarketingSubscriber.site_id == site.id,
        MarketingSubscriber.email == email,
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def _sync_customer(db, site, subscriber, status):
    customer = db.scalar(select(ShopCustomer).where(
        ShopCustomer.tenant_id == site.tenant_id,
        ShopCustomer.site_id == site.id,
        ShopCustomer.email == subscriber.email,
    ).with_for_update())
    if not customer:
        return
    now = datetime.now(UTC)
    subscribed = status == "subscribed"
    customer.email_marketing_consent = subscribed
    customer.email_consent_at = subscriber.consented_at if subscribed else now
    customer.email_consent_source = subscriber.source
    consent = db.scalar(select(MarketingConsent).where(
        MarketingConsent.tenant_id == site.tenant_id,
        MarketingConsent.site_id == site.id,
        MarketingConsent.customer_id == customer.id,
    ).with_for_update())
    if not consent:
        consent = MarketingConsent(
            tenant_id=site.tenant_id,
            site_id=site.id,
            customer_id=customer.id,
        )
        db.add(consent)
    consent.status = status
    consent.consent_json = {
        "source": subscriber.source,
        "text": subscriber.consent_evidence,
        "confirmed_at": subscriber.consented_at.isoformat(),
    }
    if subscribed:
        consent.confirmed_at = subscriber.consented_at
        consent.unsubscribed_at = None
    else:
        consent.unsubscribed_at = now


def upsert_subscriber(db, site, email, *, source, status, evidence, consented_at=None):
    """Record explicit consent without creating duplicate rows for repeat signups."""
    email = normalized_email(email)
    if source not in SOURCES or status not in STATUSES:
        raise CommerceError("Choose a valid subscriber status and source.")
    if not str(evidence or "").strip():
        raise CommerceError("Consent wording is required.")
    now = consented_at or datetime.now(UTC)
    row = _row(db, site, email, lock=True)
    created = row is None
    if created:
        row = MarketingSubscriber(
            tenant_id=site.tenant_id,
            site_id=site.id,
            email=email,
            status=status,
            source=source,
            consented_at=now,
            consent_evidence=str(evidence).strip()[:4000],
            unsubscribe_token=secrets.token_urlsafe(32),
        )
        db.add(row)
        db.flush()
    elif status == "unsubscribed":
        if row.status != "unsubscribed":
            row.status = "unsubscribed"
            db.flush()
    elif status == "subscribed":
        if row.status != "subscribed":
            row.status = "subscribed"
            row.source = source
            row.consented_at = now
            row.consent_evidence = str(evidence).strip()[:4000]
            db.flush()
    elif row.status != "subscribed":
        row.status = "pending"
        row.source = source
        row.consented_at = now
        row.consent_evidence = str(evidence).strip()[:4000]
        db.flush()
    if row.status in {"subscribed", "unsubscribed"}:
        _sync_customer(db, site, row, row.status)
    return row, created


def set_status(db, site, subscriber_id, status, *, consent=False):
    if status not in {"subscribed", "unsubscribed"}:
        raise CommerceError("Choose a valid subscriber status.")
    row = db.scalar(select(MarketingSubscriber).where(
        MarketingSubscriber.id == subscriber_id,
        MarketingSubscriber.site_id == site.id,
        MarketingSubscriber.tenant_id == site.tenant_id,
    ).with_for_update())
    if not row:
        raise CommerceError("Subscriber not found.")
    now = datetime.now(UTC)
    if status == "subscribed":
        if not consent:
            raise CommerceError("Confirm that you have consent before resubscribing this email.")
        row.status = "subscribed"
        row.source = "admin"
        row.consented_at = now
        row.consent_evidence = "I have consent (merchant resubscription)."
    else:
        row.status = "unsubscribed"
    _sync_customer(db, site, row, status)
    db.flush()
    return row


def subscribers_for(db, site, *, query="", status="", source=""):
    stmt = select(MarketingSubscriber).where(
        MarketingSubscriber.tenant_id == site.tenant_id,
        MarketingSubscriber.site_id == site.id,
    )
    query = str(query or "").strip()[:160]
    if query:
        stmt = stmt.where(MarketingSubscriber.email.ilike(f"%{query}%"))
    if status:
        if status not in STATUSES:
            raise CommerceError("Choose a valid subscriber status.")
        stmt = stmt.where(MarketingSubscriber.status == status)
    if source:
        if source not in SOURCES:
            raise CommerceError("Choose a valid subscriber source.")
        stmt = stmt.where(MarketingSubscriber.source == source)
    return list(db.scalars(stmt.order_by(
        MarketingSubscriber.created_at.desc(), MarketingSubscriber.id.desc())))


def export_csv(db, site, *, query="", status="", source=""):
    rows = subscribers_for(db, site, query=query, status=status, source=source)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("email", "status", "source", "consented_at"))
    for row in rows:
        writer.writerow(tuple(_safe_cell(value) for value in (
            row.email, row.status, row.source,
            row.consented_at.isoformat() if row.consented_at else "",
        )))
    return output.getvalue()


def _safe_cell(value):
    value = str(value or "")
    if re.match(r"^[\s\u0000-\u001f]*[=+\-@]", value):
        return "'" + value
    return value


def import_csv(db, site, payload, *, consent_confirmed=False):
    if not consent_confirmed:
        raise CommerceError("Confirm that everyone in this file gave consent before importing.")
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if len(raw) > MAX_CSV_BYTES:
        raise CommerceError("CSV files must be no larger than 1 MB.")
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise CommerceError("Upload a valid UTF-8 CSV file.") from exc
    if not reader.fieldnames:
        raise CommerceError("The CSV file must include an email column.")
    email_column = next((name for name in reader.fieldnames if name and name.strip().lower() == "email"), None)
    if not email_column:
        raise CommerceError("The CSV file must include an email column.")
    counts = {"added": 0, "updated": 0, "duplicates": 0, "invalid": 0}
    seen = set()
    try:
        for values in reader:
            try:
                email = normalized_email(values.get(email_column, ""))
            except CommerceError:
                counts["invalid"] += 1
                continue
            if email in seen:
                counts["duplicates"] += 1
                continue
            seen.add(email)
            existing = _row(db, site, email, lock=True)
            if existing and existing.status == "subscribed":
                counts["duplicates"] += 1
                continue
            evidence = "I have consent (merchant-confirmed CSV import)."
            _, created = upsert_subscriber(
                db, site, email, source="import", status="subscribed", evidence=evidence,
            )
            counts["added" if created else "updated"] += 1
    except csv.Error as exc:
        raise CommerceError("Upload a valid CSV file.") from exc
    db.flush()
    return counts


def counts(db, site):
    rows = db.execute(select(MarketingSubscriber.status, func.count()).where(
        MarketingSubscriber.site_id == site.id,
        MarketingSubscriber.tenant_id == site.tenant_id,
    ).group_by(MarketingSubscriber.status))
    result = {status: 0 for status in STATUSES}
    result.update({status: count for status, count in rows})
    result["total"] = sum(result.values())
    return result

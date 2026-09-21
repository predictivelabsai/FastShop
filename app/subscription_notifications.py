"""Transactional subscription notices. Queuing does not charge or send email."""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select

from app import checkout_services as checkout
from app import commerce, customer_services
from app.db import SessionLocal
from app.models import CommerceMail, ShopCustomer, Site, SiteCommerceSettings, SubscriptionContract
from app.services import CommerceError


def queue_failed(db, site, contract, cycle):
    if (contract.site_id != site.id or contract.tenant_id != site.tenant_id or
            cycle.site_id != site.id or cycle.tenant_id != site.tenant_id or cycle.contract_id != contract.id):
        raise CommerceError("Subscription notice ownership does not match.")
    if cycle.state != "failed":
        return None
    customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == contract.customer_id,
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id))
    if not customer:
        raise CommerceError("Subscription customer is unavailable.")
    return customer_services.queue_mail(db, site, customer, "renewal_failed", f"renewal-failed:{site.id}:{cycle.id}",
        reference={"contract_id": contract.id, "cycle_id": cycle.id})


def queue_upcoming(*, days_ahead=3, limit=100, sessions=SessionLocal, now=None):
    if type(days_ahead) is not int or not 1 <= days_ahead <= 30:
        raise CommerceError("Choose a reminder window from 1 to 30 days.")
    now = now or datetime.now(UTC)
    horizon = now + timedelta(days=days_ahead)
    count = 0
    # Exclude already-queued versions before limiting, so repeatedly running a
    # small batch cannot starve the rest of the due window.
    with sessions() as db:
        already_queued = exists(select(CommerceMail.id).where(
            CommerceMail.site_id == SubscriptionContract.site_id,
            CommerceMail.tenant_id == SubscriptionContract.tenant_id,
            CommerceMail.kind == "renewal_upcoming",
            CommerceMail.reference_json["contract_id"].as_string() == SubscriptionContract.id,
            CommerceMail.reference_json["version"].as_integer() == SubscriptionContract.version))
        candidates = db.execute(select(SubscriptionContract.id, SubscriptionContract.site_id,
            SubscriptionContract.version).where(
            SubscriptionContract.state == "active", SubscriptionContract.next_due_at > now,
            SubscriptionContract.next_due_at <= horizon, ~already_queued).join(Site, Site.id == SubscriptionContract.site_id)
            .join(SiteCommerceSettings, SiteCommerceSettings.site_id == Site.id)
            .join(ShopCustomer, ShopCustomer.id == SubscriptionContract.customer_id)
            .where(Site.tenant_id == SubscriptionContract.tenant_id, Site.status.in_(["preview", "published"]),
                SiteCommerceSettings.tenant_id == Site.tenant_id, SiteCommerceSettings.mode == "sandbox",
                ShopCustomer.site_id == Site.id, ShopCustomer.tenant_id == Site.tenant_id, ShopCustomer.is_active.is_(True))
            .order_by(SubscriptionContract.next_due_at, SubscriptionContract.id).limit(max(1, min(limit, 500)))).all()
    for contract_id, site_id, version in candidates:
        if count >= max(1, min(limit, 500)):
            break
        with sessions() as db:
            site = db.scalar(select(Site).where(Site.id == site_id))
            if not site or site.status not in ("preview", "published"):
                continue
            checkout.lock_site(db, site)
            contract = db.scalar(select(SubscriptionContract).where(SubscriptionContract.id == contract_id,
                SubscriptionContract.site_id == site.id, SubscriptionContract.tenant_id == site.tenant_id))
            config = commerce.settings_for(db, site)
            if not config or config.mode != "sandbox" or not contract or contract.state != "active" or contract.version != version:
                continue
            if not now < checkout.utc(contract.next_due_at) <= horizon:
                continue
            due = checkout.utc(contract.next_due_at).isoformat()
            digest = hashlib.sha256(f"{contract.id}:{version}:{due}".encode()).hexdigest()
            key = f"renewal-upcoming:{site.id}:{digest}"
            if db.scalar(select(CommerceMail.id).where(CommerceMail.dedupe_key == key,
                    CommerceMail.site_id == site.id, CommerceMail.tenant_id == site.tenant_id)):
                continue
            customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == contract.customer_id,
                ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.is_active.is_(True)))
            if not customer:
                continue
            customer_services.queue_mail(db, site, customer, "renewal_upcoming", key,
                reference={"contract_id": contract.id, "version": version, "due_at": due})
            db.commit()
            count += 1
    return count

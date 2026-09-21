"""Sandbox renewal worker. Configure scheduling only after provider acceptance tests."""

import argparse
from datetime import UTC, datetime

from sqlalchemy import or_, select, update

from app import subscription_renewals as renewals
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.models import Site, SubscriptionContract, SubscriptionCycle
from app.services import CommerceError


def process(*, limit=50, sessions=SessionLocal, gateway_factory=StripeGateway):
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
                cycle = renewals.prepare_cycle(db, site, contract_id, gateway_factory(site))
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
            state = renewals.run_cycle(site_id, cycle_id, sessions=sessions, gateway_factory=gateway_factory)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    print(process(limit=args.limit))


if __name__ == "__main__":
    main()

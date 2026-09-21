"""Run periodically in the app environment; outputs counts, never payment URLs or PII."""

import argparse
from datetime import UTC, datetime

from sqlalchemy import select, update

from app import checkout_payments
from app import checkout_services as checkout
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.models import CheckoutAttempt, Site, SubscriptionCycle
from app.services import CommerceError


def recover(*, limit=50, sessions=SessionLocal, gateway_factory=StripeGateway):
    counts = {"checked": 0, "paid": 0, "expired": 0, "pending": 0, "needs_attention": 0}
    with sessions() as db:
        rows = db.execute(select(CheckoutAttempt.id, CheckoutAttempt.site_id, CheckoutAttempt.customer_id)
            .where(CheckoutAttempt.state.in_(checkout.ACTIVE), ~select(SubscriptionCycle.id).where(
                SubscriptionCycle.attempt_id == CheckoutAttempt.id, SubscriptionCycle.site_id == CheckoutAttempt.site_id,
                SubscriptionCycle.tenant_id == CheckoutAttempt.tenant_id).exists()).order_by(CheckoutAttempt.updated_at, CheckoutAttempt.id)
            .limit(max(1, min(limit, 500)))).all()
    for attempt_id, site_id, customer_id in rows:
        counts["checked"] += 1
        try:
            with sessions() as db:
                site = db.scalar(select(Site).where(Site.id == site_id))
                if not site:
                    raise CommerceError("Store no longer exists.")
                attempt = db.scalar(select(CheckoutAttempt).where(CheckoutAttempt.id == attempt_id,
                    CheckoutAttempt.site_id == site.id, CheckoutAttempt.tenant_id == site.tenant_id))
                if not attempt:
                    raise CommerceError("Checkout no longer exists.")
                state = attempt.state
                expired = checkout.utc(attempt.expires_at) <= datetime.now(UTC)
                session_id = attempt.stripe_session_id
                if state == "prepared" and expired:
                    checkout.cancel_unstarted(db, site, attempt.id)
                    state = "expired"
                elif state in ("creating", "open") and session_id and not expired:
                    state = checkout.reconcile(db, site, attempt.id, gateway_factory(site)).state
                # Rotate every checked row so a waiting attempt cannot starve the queue.
                attempt.updated_at = datetime.now(UTC)
                db.commit()
            if state in ("creating", "open") and not session_id:
                # Only replay a durable provider command; never start a prepared checkout.
                checkout_payments.handoff(site_id, customer_id, attempt_id,
                    sessions=sessions, gateway_factory=gateway_factory)
                state = "open"
            if state in ("creating", "open") and expired:
                state = checkout_payments.cancel(site_id, customer_id, attempt_id,
                    sessions=sessions, gateway_factory=gateway_factory)
            counts[state if state in ("paid", "expired") else "pending"] += 1
        except CommerceError:
            # No automatic reallocation or assumption of failed payment on errors.
            counts["needs_attention"] += 1
            with sessions() as db:
                site = db.scalar(select(Site).where(Site.id == site_id))
                if site:
                    db.execute(update(CheckoutAttempt).where(CheckoutAttempt.id == attempt_id,
                        CheckoutAttempt.site_id == site.id, CheckoutAttempt.tenant_id == site.tenant_id)
                        .values(updated_at=datetime.now(UTC)))
                    db.commit()
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    print(recover(limit=args.limit))


if __name__ == "__main__":
    main()

"""Customer review display and merchant moderation for a site's catalog.

Only approved reviews reach the storefront, so the brief's "no invented reviews" rule
holds: the sample placeholder shows until real reviews are collected and approved.
Public submission is intentionally not built yet (needs anti-abuse); moderation is ready
for reviews arriving through seeding, import or the API.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.content import owned_site
from app.models import Review
from app.services import CommerceError


def approved_reviews(db, site, product_id: str | None = None, limit: int = 6) -> list[Review]:
    query = select(Review).where(Review.tenant_id == site.tenant_id, Review.is_approved.is_(True))
    if product_id:
        query = query.where(Review.product_id == product_id)
    return list(db.scalars(query.order_by(Review.created_at.desc()).limit(limit)))


def review_summary(db, site, product_id: str | None = None) -> tuple[int, float]:
    query = select(func.count(Review.id), func.avg(Review.rating)).where(
        Review.tenant_id == site.tenant_id, Review.is_approved.is_(True))
    if product_id:
        query = query.where(Review.product_id == product_id)
    count, average = db.execute(query).one()
    return int(count or 0), round(float(average), 1) if average else 0.0


def all_reviews(db, site) -> list[Review]:
    """Every review for the moderation queue, unapproved first."""
    return list(db.scalars(select(Review).where(Review.tenant_id == site.tenant_id)
                           .order_by(Review.is_approved, Review.created_at.desc())))


def moderate_review(db, site, user_id: str, review_id: str, action: str) -> None:
    owned_site(db, site.id, user_id, publish=True)
    review = db.scalar(select(Review).where(
        Review.id == review_id, Review.tenant_id == site.tenant_id)
        .with_for_update().execution_options(populate_existing=True))
    if not review:
        raise CommerceError("Review not found.")
    if action == "approve":
        review.is_approved = True
    elif action == "hide":
        review.is_approved = False
    elif action == "delete":
        db.delete(review)
    else:
        raise CommerceError("Unknown moderation action.")
    db.flush()

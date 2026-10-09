"""Durable, tenant-scoped state for the first-site onboarding funnel."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import (
    Membership,
    OnboardingState,
    Product,
    Site,
    SiteMenu,
    SitePage,
)
from app.services import CommerceError
from app.site_generation import MerchantBrief, generation_key

BUSINESS_DESCRIPTION_MAX_LENGTH = 500
PRODUCT_CONTEXT_MAX_LENGTH = 120
DESIGN_DIRECTION_MAX_LENGTH = 160
DESIGN_DIRECTIONS = (
    "Warm and natural",
    "Minimal and precise",
    "Bold and energetic",
    "Editorial and thoughtful",
)
TERMINAL_STATUSES = {"complete", "skipped"}
GENERATION_STALE_AFTER = timedelta(minutes=5)


def create_state(db, site: Site, user_id: str) -> OnboardingState:
    state = OnboardingState(
        tenant_id=site.tenant_id,
        site_id=site.id,
        user_id=user_id,
        status="brief",
    )
    db.add(state)
    db.flush()
    return state


def owned_state(
    db,
    site_id: str,
    user_id: str,
    *,
    for_update: bool = False,
) -> tuple[OnboardingState, Site]:
    query = (
        select(OnboardingState, Site)
        .join(
            Site,
            (Site.id == OnboardingState.site_id)
            & (Site.tenant_id == OnboardingState.tenant_id),
        )
        .join(Membership, Membership.tenant_id == OnboardingState.tenant_id)
        .where(
            OnboardingState.site_id == site_id,
            Membership.user_id == user_id,
            Membership.role.in_(["admin", "merchant"]),
        )
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.execute(query).one_or_none()
    if not row:
        raise CommerceError("Site not found or access denied.")
    return row[0], row[1]


def validate_brief_parts(
    business_description: str,
    product_context: str,
    design_direction: str,
) -> tuple[str, str, str]:
    business_description = " ".join(str(business_description).strip().split())
    product_context = " ".join(str(product_context).strip().split())
    design_direction = " ".join(str(design_direction).strip().split())
    if not business_description or len(business_description) > BUSINESS_DESCRIPTION_MAX_LENGTH:
        raise CommerceError(
            f"Describe your business in {BUSINESS_DESCRIPTION_MAX_LENGTH} characters or fewer."
        )
    if len(product_context) > PRODUCT_CONTEXT_MAX_LENGTH:
        raise CommerceError(
            f"Describe what you sell in {PRODUCT_CONTEXT_MAX_LENGTH} characters or fewer."
        )
    if design_direction not in DESIGN_DIRECTIONS:
        raise CommerceError("Choose one of the available look-and-feel directions.")
    return business_description, product_context, design_direction


def merchant_brief(state: OnboardingState, site: Site) -> MerchantBrief:
    brief = MerchantBrief(
        business_name=site.name,
        kind=state.product_context or "business or service",
        audience=state.business_description,
        tone=state.design_direction,
    )
    return brief


def brief_generation_key(state: OnboardingState, site: Site) -> str:
    return generation_key(merchant_brief(state, site))


def site_fingerprint(db, site: Site) -> str:
    pages = list(
        db.scalars(
            select(SitePage)
            .where(
                SitePage.site_id == site.id,
                SitePage.tenant_id == site.tenant_id,
            )
            .order_by(SitePage.id)
        )
    )
    menus = list(
        db.scalars(
            select(SiteMenu)
            .where(
                SiteMenu.site_id == site.id,
                SiteMenu.tenant_id == site.tenant_id,
            )
            .order_by(SiteMenu.id)
        )
    )
    products = list(
        db.scalars(
            select(Product)
            .where(Product.tenant_id == site.tenant_id)
            .order_by(Product.id)
        )
    )
    payload = {
        "site": {
            "id": site.id,
            "version": site.version,
            "settings": site.settings_json,
            "status": site.status,
        },
        "pages": [
            {
                "id": page.id,
                "path": page.path,
                "version": page.version,
                "draft": page.draft_json,
            }
            for page in pages
        ],
        "menus": [
            {"id": menu.id, "name": menu.name, "items": menu.items_json}
            for menu in menus
        ],
        "products": [
            {
                "id": product.id,
                "slug": product.slug,
                "name": product.name,
                "description": product.description,
            }
            for product in products
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def generation_is_stale(state: OnboardingState, now: datetime | None = None) -> bool:
    if state.status != "generating" or state.generation_started_at is None:
        return False
    started = state.generation_started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started <= (now or datetime.now(UTC)) - GENERATION_STALE_AFTER

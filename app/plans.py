"""Phase 5d plans, quotas, and metering.

The plan catalog lives here in code as frozen data (never DB rows, never
prices — Stripe billing is Phase 5e). Enforcement happens at service and
route boundaries through ``ensure_*`` functions that raise
:class:`QuotaExceeded`, a merchant-readable :class:`CommerceError` subclass
with the stable ``quota_exceeded`` code. Reads are never blocked.

Scope decision: a FastShop tenant and its first site are created 1:1 by
``content.create_site``, and every account-level flow (site creation, AI
generation) starts before any tenant exists. Therefore all four quotas are
resolved per *actor account* — across the tenants the actor helps manage —
with the account's plan being the highest tier held by those tenants. For
self-serve merchants (tenant == site == account) this is exactly a per-tenant
quota; for operator fixture accounts it degrades gracefully instead of
inventing a separate per-tenant notion. Metering always records against the
tenant the consumption happened in.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models import Membership, Product, Site, Tenant, UsageEvent
from app.services import CommerceError

DEFAULT_PLAN = "free"

# Roles that count a user as helping manage a tenant's workspace. Matches the
# ``owned_site`` / dashboard membership convention (site_routes.py, content.py).
PLAN_MEMBER_ROLES = ("admin", "merchant", "editor")


@dataclass(frozen=True)
class Plan:
    """Structural limits only. No money: pricing arrives with Phase 5e."""

    id: str
    name: str
    sites: int
    ai_generations_per_month: int
    products: int
    published_sites: int


PLANS: dict[str, Plan] = {
    plan.id: plan
    for plan in (
        Plan("free", "Free", 2, 3, 10, 2),
        Plan("basic", "Basic", 10, 25, 250, 5),
        Plan("pro", "Pro", 50, 100, 1000, 25),
    )
}

_PLAN_TIER = {plan.id: index for index, plan in enumerate(
    (PLANS["free"], PLANS["basic"], PLANS["pro"])
)}


class QuotaExceeded(CommerceError):
    """A plan limit blocked a mutating action. Stable code: ``quota_exceeded``."""

    code = "quota_exceeded"


# ---------------------------------------------------------------------------
# Windows (UTC month windows for metered credits; ``now`` is injectable so
# tests can freeze or advance time without patching tables).
# ---------------------------------------------------------------------------


def naive_utc(moment: datetime) -> datetime:
    """Normalize a datetime to the naive-UTC form used by stored timestamps."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.replace(tzinfo=None)


def month_window(now: datetime) -> tuple[datetime, datetime]:
    """Return (start, reset_at) for the UTC calendar month containing ``now``."""
    now = naive_utc(now)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        reset = datetime(start.year + 1, 1, 1)
    else:
        reset = datetime(start.year, start.month + 1, 1)
    return start, reset


def human_reset(reset_at: datetime) -> str:
    """Merchant-facing reset date, e.g. "1 November 2026"."""
    return reset_at.strftime("%d %B %Y").lstrip("0")


# ---------------------------------------------------------------------------
# Account resolution and live counters.
# ---------------------------------------------------------------------------


def account_tenant_ids(db, user_id: str) -> list[str]:
    return list(db.scalars(select(Membership.tenant_id).where(
        Membership.user_id == user_id,
        Membership.role.in_(PLAN_MEMBER_ROLES),
    )))


def account_plan(db, user_id: str) -> Plan:
    """Highest-tier plan held across the actor's managed tenants (default free)."""
    tenant_ids = account_tenant_ids(db, user_id)
    tenant_plans = (
        list(db.scalars(select(Tenant.plan).where(Tenant.id.in_(tenant_ids))))
        if tenant_ids else []
    )
    best = PLANS[DEFAULT_PLAN]
    for value in tenant_plans:
        plan = PLANS.get(value or DEFAULT_PLAN)
        if plan and _PLAN_TIER[plan.id] > _PLAN_TIER[best.id]:
            best = plan
    return best


def _tenant_ids_or_sentinel(db, user_id: str) -> list[str] | None:
    ids = account_tenant_ids(db, user_id)
    return ids or ["__none__"]


def count_sites(db, user_id: str) -> int:
    return int(db.scalar(select(func.count(Membership.tenant_id)).where(
        Membership.user_id == user_id,
        Membership.role.in_(PLAN_MEMBER_ROLES),
    )) or 0)


def count_products(db, user_id: str) -> int:
    return int(db.scalar(select(func.count(Product.id)).where(
        Product.tenant_id.in_(_tenant_ids_or_sentinel(db, user_id))
    )) or 0)


def count_published(db, user_id: str) -> int:
    return int(db.scalar(select(func.count(Site.id)).where(
        Site.tenant_id.in_(_tenant_ids_or_sentinel(db, user_id)),
        Site.status == "published",
    )) or 0)


def ai_credits_used(db, user_id: str, *, now: datetime | None = None) -> tuple[int, datetime]:
    """Sum of AI-generation quantities inside the current UTC month window."""
    now = naive_utc(now or datetime.now(UTC))
    start, reset = month_window(now)
    used = int(db.scalar(select(func.sum(UsageEvent.quantity)).where(
        UsageEvent.tenant_id.in_(_tenant_ids_or_sentinel(db, user_id)),
        UsageEvent.kind == "ai_generation",
        UsageEvent.created_at >= start,
    )) or 0)
    return used, reset


# ---------------------------------------------------------------------------
# Enforcement. Each call reads only; no write ever precedes the raise, and
# callers keep their prior behavior on other CommerceError types.
# ---------------------------------------------------------------------------


def _quota(
    plan: Plan,
    used: int,
    limit: int,
    *,
    subject: str,
    reset_at: datetime | None = None,
) -> str:
    message = (
        f"Your {plan.name} plan allows {limit} {subject}; you are now using {used} of {limit}."
    )
    if reset_at is not None:
        message += f" Credits reset on {human_reset(reset_at)}."
    else:
        message += " Free up space or move to a higher plan to continue."
    return message


def ensure_sites(db, user_id: str, *, now: datetime | None = None) -> Plan:
    plan = account_plan(db, user_id)
    used = count_sites(db, user_id)
    if used >= plan.sites:
        raise QuotaExceeded(_quota(plan, used, plan.sites, subject="sites"))
    return plan


def ensure_ai_generations(db, user_id: str, *, now: datetime | None = None) -> Plan:
    plan = account_plan(db, user_id)
    used, reset = ai_credits_used(db, user_id, now=now)
    if used >= plan.ai_generations_per_month:
        raise QuotaExceeded(_quota(
            plan, used, plan.ai_generations_per_month,
            subject="AI generation credits this month", reset_at=reset,
        ))
    return plan


def ensure_products(db, user_id: str, *, additional: int = 1) -> Plan:
    plan = account_plan(db, user_id)
    if additional <= 0:
        return plan
    used = count_products(db, user_id)
    if used + additional > plan.products:
        raise QuotaExceeded(_quota(
            plan, used, plan.products,
            subject=f"products across your sites ({additional} more requested)"
            if additional > 1 else "products across your sites",
        ))
    return plan


def ensure_published(db, user_id: str) -> Plan:
    plan = account_plan(db, user_id)
    used = count_published(db, user_id)
    if used >= plan.published_sites:
        raise QuotaExceeded(_quota(plan, used, plan.published_sites, subject="published sites"))
    return plan


# ---------------------------------------------------------------------------
# Metering (durable, tenant-scoped ledger). Separate from OutboxEvent, the
# FastERP delivery outbox — quota accounting must not be coupled to ERP
# delivery status, retries, or reconciliation.
# ---------------------------------------------------------------------------


def record(
    db,
    tenant_id: str,
    kind: str,
    *,
    site_id: str | None = None,
    user_id: str | None = None,
    quantity: int = 1,
) -> UsageEvent:
    event = UsageEvent(
        tenant_id=tenant_id, site_id=site_id, user_id=user_id,
        kind=kind, quantity=quantity,
    )
    db.add(event)
    return event


# ---------------------------------------------------------------------------
# Usage views for merchant and operator surfaces.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotaUsage:
    key: str
    label: str
    used: int
    limit: int
    window: str
    resets_at: str


def account_usage(db, user_id: str, *, now: datetime | None = None) -> tuple[Plan, list[QuotaUsage]]:
    plan = account_plan(db, user_id)
    ai_used, reset = ai_credits_used(db, user_id, now=now)
    products = count_products(db, user_id)
    published = count_published(db, user_id)
    sites = count_sites(db, user_id)
    return plan, [
        QuotaUsage("sites", "Sites", sites, plan.sites, "", ""),
        QuotaUsage(
            "ai_generations", "AI generation credits",
            ai_used, plan.ai_generations_per_month,
            "This month", human_reset(reset),
        ),
        QuotaUsage("products", "Products", products, plan.products, "", ""),
        QuotaUsage("published", "Published sites", published, plan.published_sites, "", ""),
    ]


def tenant_usage(db, tenant_id: str) -> dict[str, int]:
    """Ledger aggregates for the operator console: {kind: summed quantity}."""
    rows = db.execute(
        select(UsageEvent.kind, func.sum(UsageEvent.quantity))
        .where(UsageEvent.tenant_id == tenant_id)
        .group_by(UsageEvent.kind)
    ).all()
    return {kind: int(quantity or 0) for kind, quantity in rows}


# ---------------------------------------------------------------------------
# Platform operator gate (mirrors site_live_credential_routes.py). Non-enumerable:
# the same generic error shows for unknown users and non-operator accounts.
# ---------------------------------------------------------------------------


def require_platform_operator(db, user_id: str) -> None:
    from app.config import settings
    from app.models import User

    user = db.get(User, user_id)
    admin_email = str(settings.admin_email or "").strip().lower()
    if (
        not user or not admin_email
        or not hmac.compare_digest(user.email.strip().lower(), admin_email)
    ):
        raise CommerceError("Platform operator access is required.")


def set_tenant_plan(db, tenant_id: str, plan_id: str) -> Tenant:
    """Operator action: move a tenant onto a catalog plan tier.

    A conflicting operator assignment is authoritative. It disables local
    self-serve reconciliation so later Stripe deliveries cannot silently
    restore a different tier; provider cancellation remains an operator task.
    """
    plan = PLANS.get(str(plan_id).strip().lower())
    if not plan:
        raise CommerceError("Choose a plan from the platform catalog.")
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise CommerceError("Tenant not found.")
    from app.models import BillingSubscription

    subscription = db.scalar(select(BillingSubscription).where(
        BillingSubscription.tenant_id == tenant.id,
    ))
    if (
        subscription
        and not subscription.operator_disabled_at
        and subscription.plan_id != plan.id
    ):
        subscription.operator_disabled_at = datetime.now(UTC)
    tenant.plan = plan.id
    return tenant

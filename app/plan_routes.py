"""Phase 5d operator console: platform plan and per-tenant quota overview.

Platform-operator-only surface, gated by the same non-enumerable operator
check as the live-credential ceremony (``plans.require_platform_operator``).
Reads list every tenant with its assigned plan and metered usage; the single
state change is a plan reassignment, guarded by CSRF and applied through
Post/Redirect/Get.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fasthtml.common import H2, A, Button, Div, Form, Label, Option, P, Select, Span
from sqlalchemy import func, select
from starlette.responses import RedirectResponse

from app import plans
from app.db import SessionLocal
from app.models import Product, Site, Tenant
from app.services import CommerceError


def register_plan_routes(rt, actor, csrf, check_csrf, shell, error):
    def redirect(notice: str):
        return RedirectResponse(
            "/admin/platform/plans?" + urlencode({"notice": str(notice)[:300]}),
            status_code=303,
        )

    @rt("/admin/platform/plans", methods=["GET"])
    def get(session, notice: str = ""):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                plans.require_platform_operator(db, user_id)
                rows = []
                for tenant in db.scalars(select(Tenant).order_by(Tenant.created_at)):
                    site = db.scalar(select(Site).where(Site.tenant_id == tenant.id).order_by(Site.created_at))
                    product_count = int(db.scalar(
                        select(func.count(Product.id)).where(Product.tenant_id == tenant.id)) or 0)
                    usage = plans.tenant_usage(db, tenant.id)
                    usage_text = ", ".join(
                        f"{kind}: {quantity}" for kind, quantity in sorted(usage.items())
                    ) or "no metered usage yet"
                    options = [
                        Option(
                            f"{plan.name} (sites {plan.sites}, AI/mo {plan.ai_generations_per_month}, "
                            f"products {plan.products}, published {plan.published_sites})",
                            value=plan.id, selected=plan.id == tenant.plan,
                        )
                        for plan in plans.PLANS.values()
                    ]
                    rows.append(Div(
                        H2(tenant.name, " ", Span(f"({tenant.slug})", cls="i-subtle")),
                        P(f"Plan: {plans.PLANS.get(tenant.plan, plans.PLANS[plans.DEFAULT_PLAN]).name}"
                          f" · Site: {site.slug if site else 'none'} ·"
                          f" Products: {product_count} · Ledger — {usage_text}"),
                        Form(csrf(session),
                            Label("Assign plan", Select(*options, name="plan")),
                            Button("Apply plan change", cls="e-button"),
                            method="post",
                            action=f"/admin/platform/tenants/{tenant.id}/plan",
                            cls="e-form"),
                        cls="e-card"),
                    )
                return shell("Plans & quotas",
                    A("← Sites & content", href="/admin/sites"),
                    P("Tenant plans come from the app/plans.py catalog. Changes apply immediately "
                      "and never appear on public or marketing pages. A conflicting operator "
                      "change disables self-serve reconciliation for that tenant."),
                    P(notice[:300], role="status", cls="e-note") if notice else None,
                    Div(*rows, cls="e-grid") if rows else P("No tenants yet."))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/platform/tenants/{tenant_id}/plan", methods=["POST"])
    async def post(session, request, tenant_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                plans.require_platform_operator(db, user_id)
                plans.set_tenant_plan(db, tenant_id, str(form.get("plan", "")))
                tenant = db.get(Tenant, tenant_id)
                db.commit()
                name = plans.PLANS.get(tenant.plan, plans.PLANS[plans.DEFAULT_PLAN]).name
            return redirect(f"Plan updated: {name}.")
        except (CommerceError, ValueError) as exc:
            return redirect(exc)

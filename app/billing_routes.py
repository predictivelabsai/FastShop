"""Merchant self-serve subscription routes for the FastShop platform."""

from __future__ import annotations

import re
from urllib.parse import urlencode

from fasthtml.common import H2, H3, A, Button, Div, Form, Input, P, Small, Span
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app import billing, plans
from app.config import settings
from app.db import SessionLocal
from app.models import BillingSubscription, User
from app.services import CommerceError


def register_billing_routes(rt, actor, csrf, check_csrf, shell, error):
    def redirect(notice: str):
        return RedirectResponse(
            "/admin/billing?" + urlencode({"notice": str(notice)[:300]}),
            status_code=303,
        )

    @rt("/admin/billing", methods=["GET"])
    def get(session, notice: str = ""):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                current_plan = plans.account_plan(db, user_id)
                row = billing.account_billing_row(db, user_id)
                config = billing.configuration()
                available = billing.available_plans(config)
                if row and row.operator_disabled_at:
                    state = (
                        "Your plan is managed by the platform operator. Self-serve changes "
                        "are disabled so Stripe events cannot override that decision."
                    )
                elif row:
                    state = (
                        f"Stripe subscription: {row.status.replace('_', ' ')} · "
                        f"requested tier: {plans.PLANS[row.plan_id].name}."
                    )
                    if row.cancel_at_period_end and row.current_period_end:
                        state += (
                            " Access remains active until Stripe ends the subscription on "
                            f"{row.current_period_end:%d %B %Y}."
                        )
                elif not any(available.values()):
                    state = (
                        f"Billing setup is incomplete. Your {current_plan.name} plan remains "
                        "active, all read-only workspace access stays available, and no action "
                        "is required from you."
                    )
                else:
                    state = "Choose a paid tier. Checkout opens on Stripe in a new request."

                choices = []
                for plan_id in billing.PAID_PLANS:
                    plan = plans.PLANS[plan_id]
                    enabled = bool(
                        available[plan_id]
                        and not (row and row.operator_disabled_at)
                        and not (row and row.status in billing.ENTITLED_STATUSES)
                    )
                    price_configured = bool(
                        re.fullmatch(r"price_[A-Za-z0-9_]+", config.prices.get(plan_id, ""))
                    )
                    unavailable = (
                        "This tier is not configured for self-serve billing."
                        if not price_configured
                        else "Billing setup is incomplete."
                        if not available[plan_id]
                        else "Self-serve changes are unavailable for this subscription."
                    )
                    choices.append(
                        Div(
                            Div(
                                H3(plan.name),
                                Span(
                                    "Current plan"
                                    if current_plan.id == plan.id
                                    else "Available"
                                    if enabled
                                    else "Unavailable",
                                    cls=(
                                        "b-state"
                                        if enabled or current_plan.id == plan.id
                                        else "b-state b-state-unavailable"
                                    ),
                                ),
                                cls="b-heading",
                            ),
                            P(
                                f"{plan.sites} sites · {plan.ai_generations_per_month} AI "
                                f"generations per month · {plan.products} products · "
                                f"{plan.published_sites} published sites"
                            ),
                            Form(
                                csrf(session),
                                Input(type="hidden", name="plan", value=plan.id),
                                Button(
                                    f"Continue with {plan.name}"
                                    if enabled
                                    else "Currently unavailable",
                                    cls="e-button",
                                    disabled=not enabled,
                                ),
                                method="post",
                                action="/admin/billing/checkout",
                                cls="e-form b-action",
                            ),
                            Small(unavailable, cls="b-unavailable") if not enabled else None,
                            cls="e-card b-plan",
                        )
                    )
                return shell(
                    "Billing",
                    A("Back to plan & usage", href="/admin/sites"),
                    P(
                        f"Current account plan: {current_plan.name}. Prices and payment "
                        "collection are owned by Stripe Checkout; FastShop stores no card data.",
                        cls="b-intro",
                    ),
                    P(notice[:300], role="status", cls="e-note") if notice else None,
                    Div(H2("Subscription status"), P(state), cls="e-card b-summary"),
                    Div(*choices, cls="e-grid b-plans"),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/billing/checkout", methods=["POST"])
    async def post(session, request):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                user = db.get(User, user_id)
                if not user:
                    raise CommerceError("Sign in to manage billing.")
                row = billing.begin_checkout(db, user_id, str(form.get("plan", "")))
                row_id, tenant_id, email = row.id, row.tenant_id, user.email
                db.commit()

            success_url = (
                settings.public_url
                + "/admin/billing/success?session_id={CHECKOUT_SESSION_ID}"
            )
            cancel_url = settings.public_url + "/admin/billing/cancel"

            def create_session():
                return billing.StripeBillingGateway().create_checkout_session(
                    row,
                    email,
                    success_url=success_url,
                    cancel_url=cancel_url,
                )

            stripe_session = await run_in_threadpool(create_session)
            with SessionLocal() as db:
                stored = db.scalar(
                    select(BillingSubscription).where(
                        BillingSubscription.id == row_id,
                        BillingSubscription.tenant_id == tenant_id,
                    )
                )
                if not stored:
                    raise CommerceError("Billing checkout no longer belongs to this workspace.")
                billing.record_checkout_session(db, stored, stripe_session["id"])
                db.commit()
            return RedirectResponse(stripe_session["url"], status_code=303)
        except CommerceError as exc:
            return redirect(exc)

    @rt("/admin/billing/success", methods=["GET"])
    def get(session, session_id: str = ""):
        try:
            actor(session)
            return redirect(
                "Stripe received the subscription. Plan access updates after the signed "
                "billing webhook is reconciled."
            )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/billing/cancel", methods=["GET"])
    def get(session):
        try:
            actor(session)
            return redirect("Stripe checkout was cancelled; your plan was not changed.")
        except CommerceError as exc:
            return error(exc)

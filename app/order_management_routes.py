"""Server-rendered merchant order operations and per-site revenue reports."""

from __future__ import annotations

from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    P,
    Small,
    Span,
    Strong,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
)
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app import checkout_payments, content
from app import order_management as orders
from app.config import settings
from app.db import SessionLocal
from app.models import Membership, RefundCommand, Site, User, new_id
from app.services import CommerceError, money


def register_order_management_routes(rt, actor, csrf, check_csrf, shell, error):
    def order_url(site_id, site_order_id=""):
        return f"/admin/sites/{site_id}/orders" + (f"/{site_order_id}" if site_order_id else "")

    def delivery_url(site_id):
        return f"/admin/sites/{site_id}/deliveries"

    def redirect_notice(url, notice):
        return RedirectResponse(url + "?" + urlencode({"notice": str(notice)[:300]}), status_code=303)

    def status(value):
        tone = " o-state-positive" if value in {"fulfilled", "delivered", "paid", "refunded", "succeeded"} else ""
        tone = " o-state-negative" if value in {"dead", "failed", "cancelled"} else tone
        return Span(value.replace("_", " ").title(), cls="o-state" + tone)

    def metrics(report):
        return Div(
            Div(Small("Gross sales"), Strong(money(report.gross_sales_minor, report.currency)), cls="o-metric"),
            Div(Small("Refunds"), Strong("−" + money(report.refunds_minor, report.currency)), cls="o-metric"),
            Div(Small("Net revenue"), Strong(money(report.net_revenue_minor, report.currency)), cls="o-metric"),
            Div(Small("Orders"), Strong(str(report.order_count)), cls="o-metric"),
            Div(Small("Average order"), Strong(money(report.average_order_value_minor, report.currency)), cls="o-metric"),
            cls="o-metrics",
        )

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/fulfillment", methods=["POST"])
    async def post(session, request, site_id: str, site_order_id: str):
        target_url = order_url(site_id, site_order_id)
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                orders.transition_fulfillment(
                    db,
                    site,
                    user_id,
                    site_order_id,
                    str(form.get("target", "")),
                    str(form.get("expected_status", "")),
                    carrier=str(form.get("carrier", "")),
                    tracking_number=str(form.get("tracking_number", "")),
                    tracking_url=str(form.get("tracking_url", "")),
                    note=str(form.get("note", "")),
                )
                db.commit()
            return redirect_notice(target_url, "Fulfillment status updated.")
        except (CommerceError, ValueError) as exc:
            return redirect_notice(target_url, exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/resync", methods=["POST"])
    async def post(session, request, site_id: str, site_order_id: str):
        target_url = order_url(site_id, site_order_id)
        try:
            form = await request.form()
            check_csrf(session, form)
            payment_status = await run_in_threadpool(
                orders.resync_order,
                site_id,
                actor(session),
                site_order_id,
            )
            return redirect_notice(
                target_url,
                "Stripe status re-synced. Order payment status: " + payment_status.replace("_", " ") + ".",
            )
        except CommerceError as exc:
            return redirect_notice(target_url, exc)

    @rt("/admin/sites/{site_id}/deliveries", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                failures = orders.delivery_failures(db, site)
                rows = [Tr(
                    Td(event.topic),
                    Td(status(event.status)),
                    Td(str(event.attempts), cls="o-money"),
                    Td(orders.safe_delivery_error(event), cls="o-error-detail"),
                    Td(Form(
                        csrf(session),
                        Button("Requeue", cls="e-button"),
                        method="post",
                        action=delivery_url(site.id) + f"/{event.id}/requeue",
                        cls="o-compact-action",
                    )),
                ) for event in failures]
                return shell(
                    "Delivery issues",
                    Div(
                        A("← Orders", href=order_url(site.id)),
                        A("Revenue report", href=f"/admin/sites/{site.id}/revenue"),
                        cls="e-actions",
                    ),
                    P(
                        f"{site.name} · Terminal and legacy failed deliveries only. "
                        "Requeue resets the bounded retry schedule for one event.",
                        cls="o-intro",
                    ),
                    P(notice, role="status", cls="e-note") if notice else None,
                    Div(Table(
                        Thead(Tr(Th("Topic"), Th("State"), Th("Attempts"), Th("Last error"), Th("Action"))),
                        Tbody(*rows),
                    ), cls="o-table-wrap") if rows else Div(
                        H2("No delivery issues"),
                        P("No dead-letter or legacy failed events need attention for this site."),
                        cls="e-card o-empty",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/deliveries/{event_id}/requeue", methods=["POST"])
    async def post(session, request, site_id: str, event_id: str):
        target_url = delivery_url(site_id)
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                event = orders.requeue_delivery(db, site, user_id, event_id)
                topic = event.topic
                db.commit()
            return redirect_notice(target_url, topic + " delivery requeued for an immediate bounded retry.")
        except CommerceError as exc:
            return redirect_notice(target_url, exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/refund", methods=["GET"])
    def get(session, site_id: str, site_order_id: str, amount_minor: str = "", reason: str = ""):
        try:
            amount = int(amount_minor)
            reason = reason.strip()
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                link, order, _ = orders.owned_order(db, site, site_order_id)
                _, _, available = orders.refund_totals(db, site, link, order)
                if amount <= 0 or amount > available:
                    raise CommerceError("Refund amount is outside the currently available captured amount.")
                if not 10 <= len(reason) <= 500:
                    raise CommerceError("Provide a refund reason between 10 and 500 characters.")
                action = order_url(site.id, link.id)
                return shell(
                    "Confirm refund",
                    A("← Order " + order.number, href=action),
                    Div(
                        H2("Review the exact provider command"),
                        P(f"Refund {money(amount, order.currency)} to the original Stripe payment method."),
                        P("Reason: " + reason),
                        P("This may be a partial refund. FastShop will never increase or reduce this amount silently, and total refunds cannot exceed captured funds."),
                        Form(
                            csrf(session),
                            Input(type="hidden", name="amount_minor", value=amount),
                            Input(type="hidden", name="reason", value=reason),
                            Input(type="hidden", name="request_key", value=new_id()),
                            Label(Input(type="checkbox", name="confirm", required=True), " I confirm this exact amount and understand the provider action cannot be undone here."),
                            Button("Issue exact refund", cls="e-button"),
                            method="post",
                            action=action + "/refund",
                            cls="e-form",
                        ),
                        cls="e-card o-confirm",
                    ),
                )
        except (CommerceError, ValueError) as exc:
            return redirect_notice(order_url(site_id, site_order_id), exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}/refund", methods=["POST"])
    async def post(session, request, site_id: str, site_order_id: str):
        target_url = order_url(site_id, site_order_id)
        try:
            form = await request.form()
            check_csrf(session, form)
            user_id = actor(session)
            if form.get("confirm") != "on":
                if form.get("retry") != "on":
                    raise CommerceError("Confirm the exact refund before issuing it.")
                with SessionLocal() as db:
                    site = content.owned_site(db, site_id, user_id, publish=True)
                    retry = db.scalar(select(RefundCommand).where(
                        RefundCommand.site_id == site.id,
                        RefundCommand.tenant_id == site.tenant_id,
                        RefundCommand.site_order_id == site_order_id,
                        RefundCommand.request_key == str(form.get("request_key", "")),
                        RefundCommand.state.in_(orders.REFUND_STATES_HOLDING_FUNDS),
                    ))
                    if not retry:
                        raise CommerceError("Only a persisted unresolved refund can be reconciled without a new confirmation.")
            command = await run_in_threadpool(
                checkout_payments.refund,
                site_id,
                user_id,
                site_order_id,
                int(form.get("amount_minor", "0")),
                str(form.get("reason", "")),
                str(form.get("request_key", "")),
            )
            message = {
                "succeeded": "Exact refund completed.",
                "pending": "Stripe accepted the exact refund; settlement is pending.",
                "failed": "Stripe reported that the refund failed; no refund was recorded.",
            }.get(command.state, "Refund requires provider reconciliation.")
            return redirect_notice(target_url, message)
        except (CommerceError, ValueError) as exc:
            return redirect_notice(target_url, exc)

    @rt("/admin/sites/{site_id}/revenue", methods=["GET"])
    def get(session, site_id: str, start: str = "", end: str = "", notice: str = ""):
        try:
            start_date, end_date = orders.report_dates(start, end)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                report = orders.revenue_report(db, site, start_date, end_date)
                warning = report.excluded_currency_orders + report.excluded_currency_refunds
                return shell(
                    "Revenue report",
                    Div(A("← Site", href=f"/admin/sites/{site.id}"), A("Orders", href=order_url(site.id)), cls="e-actions"),
                    P(f"{site.name} · {report.currency} · committed real order and refund records only.", cls="o-intro"),
                    P(notice, role="status", cls="e-note") if notice else None,
                    Form(
                        Label("Start date", Input(name="start", type="date", value=report.start_date.isoformat(), required=True)),
                        Label("End date", Input(name="end", type="date", value=report.end_date.isoformat(), required=True)),
                        Button("Run report", cls="e-button"),
                        method="get",
                        cls="e-form o-date-filter",
                    ),
                    metrics(report),
                    P("Gross sales count committed orders placed in the selected UTC dates. Refunds count successful refund transactions completed in those UTC dates, even when the original order was earlier. Net is gross minus refunds; average order value is gross divided by order count, rounded to the nearest minor unit.", cls="e-card o-method"),
                    P(f"{warning} record(s) were excluded because their currency does not match the site's {report.currency} channel. FastShop never combines currencies.", role="status", cls="e-note") if warning else None,
                    Div(H2("No revenue in this range"), P("Try a different bounded date range. Demo-commerce activity is intentionally excluded."), cls="e-card o-empty") if not report.order_count and not report.refunds_minor else None,
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/platform/revenue", methods=["GET"])
    def get(session, start: str = "", end: str = ""):
        try:
            start_date, end_date = orders.report_dates(start, end)
            with SessionLocal() as db:
                user_id = actor(session)
                user = db.get(User, user_id)
                if not user or user.email.lower() != settings.admin_email:
                    raise CommerceError("Platform operator access is required.")
                sites = list(db.scalars(select(Site).join(Membership, Membership.tenant_id == Site.tenant_id).where(
                    Membership.user_id == user_id,
                    Membership.role == "admin",
                ).order_by(Site.name)))
                rows = []
                for site in sites:
                    report = orders.revenue_report(db, site, start_date, end_date)
                    rows.append(Tr(
                        Td(A(site.name, href=f"/admin/sites/{site.id}/revenue?" + urlencode({"start": start_date.isoformat(), "end": end_date.isoformat()}))),
                        Td(report.currency),
                        Td(money(report.gross_sales_minor, report.currency)),
                        Td(money(report.refunds_minor, report.currency)),
                        Td(money(report.net_revenue_minor, report.currency)),
                        Td(str(report.order_count)),
                    ))
                return shell(
                    "Per-site revenue",
                    Div(
                        A("← Sites", href="/admin/sites"),
                        A("Platform delivery issues", href="/admin/platform/deliveries"),
                        cls="e-actions",
                    ),
                    P("Operator totals remain separated by site and channel currency. Only tenants where this operator has an admin membership are visible."),
                    Form(
                        Label("Start date", Input(name="start", type="date", value=start_date.isoformat(), required=True)),
                        Label("End date", Input(name="end", type="date", value=end_date.isoformat(), required=True)),
                        Button("Run summaries", cls="e-button"),
                        method="get",
                        cls="e-form o-date-filter",
                    ),
                    Div(Table(Thead(Tr(Th("Site"), Th("Currency"), Th("Gross"), Th("Refunds"), Th("Net"), Th("Orders"))), Tbody(*rows)), cls="o-table-wrap") if rows else Div(H2("No operator sites"), P("No tenant-scoped admin memberships are available."), cls="e-card o-empty"),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/platform/deliveries", methods=["GET"])
    def get(session, notice: str = ""):
        try:
            with SessionLocal() as db:
                user_id = actor(session)
                user = db.get(User, user_id)
                if not user or user.email.lower() != settings.admin_email:
                    raise CommerceError("Platform operator access is required.")
                sites = list(db.scalars(select(Site)
                    .join(Membership, Membership.tenant_id == Site.tenant_id)
                    .where(Membership.user_id == user_id, Membership.role == "admin")
                    .order_by(Site.name, Site.id)))
                rows = []
                for site in sites:
                    for event in orders.delivery_failures(db, site):
                        rows.append(Tr(
                            Td(A(site.name, href=delivery_url(site.id))),
                            Td(event.topic),
                            Td(status(event.status)),
                            Td(str(event.attempts), cls="o-money"),
                            Td(orders.safe_delivery_error(event), cls="o-error-detail"),
                        ))
                return shell(
                    "Platform delivery issues",
                    Div(
                        A("← Sites", href="/admin/sites"),
                        A("Per-site revenue", href="/admin/platform/revenue"),
                        cls="e-actions",
                    ),
                    P(
                        "Only sites where this operator holds an explicit admin membership are included.",
                        cls="o-intro",
                    ),
                    P(notice, role="status", cls="e-note") if notice else None,
                    Div(Table(
                        Thead(Tr(Th("Site"), Th("Topic"), Th("State"), Th("Attempts"), Th("Last error"))),
                        Tbody(*rows),
                    ), cls="o-table-wrap") if rows else Div(
                        H2("No platform delivery issues"),
                        P("No explicitly administered site has a dead-letter or legacy failed event."),
                        cls="e-card o-empty",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

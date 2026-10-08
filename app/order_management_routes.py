"""Server-rendered merchant order operations and per-site revenue reports."""

from __future__ import annotations

from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    H3,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    Option,
    P,
    Select,
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

    def redirect_notice(url, notice):
        return RedirectResponse(url + "?" + urlencode({"notice": str(notice)[:300]}), status_code=303)

    def status(value):
        tone = " o-state-positive" if value in {"fulfilled", "delivered", "paid", "refunded", "succeeded"} else ""
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

    @rt("/admin/sites/{site_id}/orders", methods=["GET"])
    def get(session, site_id: str, status_filter: str = "all", notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                rows = orders.orders_for(db, site, status_filter)
                body = [Tr(
                    Td(A(order.number, href=order_url(site.id, link.id))),
                    Td(order.email),
                    Td(status(order.status)),
                    Td(status(order.payment_status)),
                    Td(money(order.total_minor, order.currency), cls="o-money"),
                    Td(order.created_at.strftime("%Y-%m-%d")),
                ) for link, order, _ in rows]
                return shell(
                    "Orders",
                    Div(A("← Site", href=f"/admin/sites/{site.id}"), A("Revenue report", href=f"/admin/sites/{site.id}/revenue"), cls="e-actions"),
                    P(f"{site.name} · Real site orders only. Demo-commerce simulations never appear here.", cls="o-intro"),
                    P(notice, role="status", cls="e-note") if notice else None,
                    Form(
                        Label("Fulfillment status", Select(
                            Option("All orders", value="all", selected=status_filter == "all"),
                            *[Option(value.title(), value=value, selected=status_filter == value) for value in orders.ORDER_STATES],
                            name="status_filter",
                        )),
                        Button("Filter orders", cls="e-button"),
                        method="get",
                        cls="e-form o-filter",
                    ),
                    Div(
                        Table(
                            Thead(Tr(Th("Order"), Th("Customer"), Th("Fulfillment"), Th("Payment"), Th("Total"), Th("Placed"))),
                            Tbody(*body),
                        ),
                        cls="o-table-wrap",
                    ) if body else Div(H2("No matching orders"), P("Confirmed storefront orders will appear here. Try another fulfillment filter."), cls="e-card o-empty"),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/orders/{site_order_id}", methods=["GET"])
    def get(session, site_id: str, site_order_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                link, order, customer = orders.owned_order(db, site, site_order_id)
                captured, refunded, available = orders.refund_totals(db, site, link, order)
                events = orders.timeline(db, site, link, order)
                allowed = orders.TRANSITIONS.get(order.status, ())
                address = order.shipping_address_json or {}
                action = order_url(site.id, link.id)
                fulfillment = Div(
                    H2("Update fulfillment"),
                    P("Fulfillment changes are sequential and customer-visible. Cancelling fulfillment never issues a refund."),
                    Form(
                        csrf(session),
                        Input(type="hidden", name="expected_status", value=order.status),
                        Label("Next status", Select(*[Option(value.title(), value=value) for value in allowed], name="target")),
                        Label("Carrier", Input(name="carrier", maxlength=100, autocomplete="off")),
                        Label("Tracking number", Input(name="tracking_number", maxlength=100, autocomplete="off")),
                        Label("Carrier tracking URL", Input(name="tracking_url", type="url", maxlength=500, placeholder="https://…")),
                        Label("Fulfillment or cancellation note", Input(name="note", maxlength=500)),
                        Small("Carrier and tracking number are required for fulfillment. A cancellation requires a reason."),
                        Button("Save fulfillment update", cls="e-button"),
                        method="post",
                        action=action + "/fulfillment",
                        cls="e-form",
                    ),
                    cls="e-card",
                ) if allowed else Div(H2("Fulfillment complete"), P(f"This order is {order.status}; no further fulfillment transitions are available."), cls="e-card")
                refund_card = Div(
                    H2("Refund payment"),
                    P("Refunds use the site's current guarded Stripe mode. The exact confirmed amount is persisted before any provider request."),
                    P(f"Captured: {money(captured, order.currency)} · completed refunds: {money(refunded, order.currency)} · available: {money(available, order.currency)}", cls="o-money"),
                    Form(
                        Label("Refund amount (minor units)", Input(name="amount_minor", type="number", min=1, max=available, value=available, required=True)),
                        Label("Reason", Input(name="reason", minlength=10, maxlength=500, required=True, placeholder="Why this refund is being issued")),
                        Button("Review refund", cls="e-button", disabled=available <= 0),
                        method="get",
                        action=action + "/refund",
                        cls="e-form",
                    ) if available else P("No captured amount remains available for another refund."),
                    cls="e-card",
                ) if captured else Div(H2("Refund payment"), P("No supported captured Stripe payment is attached to this order."), cls="e-card")
                timeline_rows = []
                for event in events:
                    retry = None
                    command = event.get("command")
                    if command and command.state in orders.REFUND_STATES_HOLDING_FUNDS:
                        retry = Form(
                            csrf(session),
                            Input(type="hidden", name="amount_minor", value=command.amount_minor),
                            Input(type="hidden", name="reason", value=command.reason),
                            Input(type="hidden", name="request_key", value=command.request_key),
                            Input(type="hidden", name="retry", value="on"),
                            Button("Reconcile exact refund", cls="e-button"),
                            method="post",
                            action=action + "/refund",
                            cls="e-form o-inline-form",
                        )
                    timeline_rows.append(Div(
                        Div(status(event["kind"]), Small(event["at"].strftime("%Y-%m-%d %H:%M UTC"))),
                        H3(event["title"]),
                        P(event["detail"] or "No additional detail."),
                        A("Open carrier tracking ↗", href=event["tracking_url"], target="_blank", rel="noopener noreferrer") if event.get("tracking_url") else None,
                        retry,
                        cls="o-timeline-row",
                    ))
                return shell(
                    order.number,
                    Div(A("← Orders", href=order_url(site.id)), A("Revenue report", href=f"/admin/sites/{site.id}/revenue"), cls="e-actions"),
                    P(notice, role="status", cls="e-note") if notice else None,
                    Div(
                        Div(H2("Customer"), P(customer.name or "Name not supplied"), P(order.email),
                            P(", ".join(str(address.get(key, "")) for key in ("line1", "line2", "city", "state", "postal_code", "country") if address.get(key))), cls="e-card"),
                        Div(H2("Order state"), P(status(order.status)), P(status(order.payment_status)),
                            P("Placed " + order.created_at.strftime("%Y-%m-%d %H:%M UTC")), cls="e-card"),
                        cls="e-grid",
                    ),
                    Div(H2("Items"),
                        Div(*[Div(Span(f"{line.product_name} · {line.variant_name} × {line.quantity}"), Strong(money(line.total_minor, order.currency)), cls="o-line") for line in order.lines], cls="o-lines"),
                        Div(P("Subtotal"), Strong(money(order.subtotal_minor, order.currency)), cls="o-total-row"),
                        Div(P("Discount"), Strong("−" + money(order.discount_minor, order.currency)), cls="o-total-row"),
                        Div(P("Shipping"), Strong(money(order.shipping_minor, order.currency)), cls="o-total-row"),
                        Div(P("Tax"), Strong(money(order.tax_minor, order.currency)), cls="o-total-row"),
                        Div(P("Total"), Strong(money(order.total_minor, order.currency)), cls="o-total-row o-grand-total"),
                        cls="e-card o-summary",
                    ),
                    Div(fulfillment, refund_card, cls="e-grid"),
                    Div(H2("Order timeline"), *timeline_rows, cls="e-card o-timeline"),
                )
        except CommerceError as exc:
            return error(exc)

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
                    A("← Sites", href="/admin/sites"),
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

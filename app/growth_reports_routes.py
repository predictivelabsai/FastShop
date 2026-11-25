"""Merchant analytics and subscription scheduling in the shared admin shell."""

import secrets
from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Option,
    P,
    Select,
    Span,
    Strong,
    Summary,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse, Response

from app import content, growth_reports, subscriptions
from app.admin_charts import line_chart, short_money
from app.admin_icons import lucide
from app.admin_ui import (
    badge,
    card,
    date_range_picker,
    delta_badge,
    empty_state,
    index_table,
    key_value_list,
    page_header,
)
from app.db import SessionLocal
from app.models import ShopCustomer, SubscriptionContract, SubscriptionCycle, SubscriptionEvent
from app.services import CommerceError, money


def _comparison(current, previous):
    if previous == 0:
        return "No previous activity" if current == 0 else "New this period"
    delta = (current - previous) * 100 / previous
    return f"{delta:+.1f}% compared with previous period"


def _contracts(db, site):
    return select(SubscriptionContract, ShopCustomer).join(
        ShopCustomer, ShopCustomer.id == SubscriptionContract.customer_id).where(
        SubscriptionContract.site_id == site.id,
        SubscriptionContract.tenant_id == site.tenant_id,
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id)


def register_growth_reports_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/analytics", methods=["GET"])
    def get(session, site_id: str, preset: str = "30", start: str = "", end: str = "",
            format: str = "html"):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                start_date, end_date = growth_reports.date_range(preset, start, end)
                data = growth_reports.report(db, site, start_date, end_date)
                if format == "csv":
                    return Response(growth_reports.report_csv(data), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="analytics.csv"',
                                 "Cache-Control": "private, no-store"})
                current, previous = data["summary"], data["previous"]["summary"]
                prefix = f"/admin/sites/{site.id}"
                export = prefix + "/analytics?" + urlencode({"start": start_date.isoformat(),
                    "end": end_date.isoformat(), "format": "csv"})
                currency = current.currency
                metrics = []
                for label, value, old, kind in (
                    ("Total sales", current.gross_sales_minor, previous.gross_sales_minor, "money"),
                    ("Net revenue", current.net_revenue_minor, previous.net_revenue_minor, "money"),
                    ("Orders", current.order_count, previous.order_count, "count"),
                    ("Average order value", current.average_order_value_minor,
                     previous.average_order_value_minor, "money"),
                    ("Returning customer rate", data["returning_rate"],
                     data["previous"]["returning_rate"], "rate"),
                    ("Refunds", current.refunds_minor, previous.refunds_minor, "money"),
                ):
                    display = (money(value, currency) if kind == "money" else
                               f"{value / 100:.1f}%" if kind == "rate" else f"{value:,}")
                    metrics.append(Div(Span(label, cls="p-metric-label"),
                                       Div(Strong(display), delta_badge(value, old), cls="p-metric-value"),
                                       cls="p-metric"))
                daily = data["daily"]
                chart = line_chart([amount for _, amount in daily],
                                   [f"{day:%-d %b}" if hasattr(day, "strftime") else str(day) for day, _ in daily],
                                   previous=[amount for _, amount in data["previous"]["daily"]],
                                   label="Total sales",
                                   format_value=lambda minor: short_money(minor, currency))

                def table_card(title, columns, rows, empty):
                    return card(
                        Div(Table(Thead(Tr(*[Th(column, scope="col") for column in columns])),
                                  Tbody(*[Tr(*[Td(cell) for cell in row]) for row in rows]),
                                  cls="p-report-table"), cls="a-table-wrap") if rows
                        else P(empty, cls="p-card-empty"),
                        title=title, cls="p-report-card")

                return shell("Analytics",
                    page_header("Analytics", secondary_actions=[
                        A(lucide("download", 16), Span("Export"), href=export, cls="a-button")]),
                    Div(date_range_picker(prefix + "/analytics", preset=preset if not (start or end) else "custom",
                                          start=start_date.isoformat(), end=end_date.isoformat(),
                                          label=None if not (start or end) and preset != "custom"
                                          else f"{start_date:%-d %b} to {end_date:%-d %b %Y}"),
                        Span(f"Compared with {previous.start_date:%-d %b} to {previous.end_date:%-d %b %Y}",
                             cls="p-compare"),
                        cls="p-report-toolbar"),
                    Div(*metrics, cls="p-metrics"),
                    card(Div(H2("Total sales over time"),
                             Div(Span(cls="p-legend-swatch"), Span(f"{start_date:%-d %b} to {end_date:%-d %b}"),
                                 Span(cls="p-legend-swatch is-previous"),
                                 Span(f"{previous.start_date:%-d %b} to {previous.end_date:%-d %b}"),
                                 cls="p-legend"), cls="p-chart-head"),
                         chart, cls="p-chart-card"),
                    Div(table_card("Top products by revenue", ["Product", "Revenue", "Units"],
                                   [[p["name"], money(p["revenue_minor"], currency), p["units"]]
                                    for p in data["top_products"][:10]],
                                   "Product sales will appear after your first paid order."),
                        table_card("Top products by units sold", ["Product", "Units", "Revenue"],
                                   [[p["name"], p["units"], money(p["revenue_minor"], currency)]
                                    for p in data["top_units"][:10]],
                                   "No units sold in this period."),
                        cls="p-report-grid"),
                    Div(table_card("Sales by channel", ["Channel", "Sales", "Orders"],
                                   [[name, money(values[0], currency), values[1]] for name, values in data["channels"]],
                                   "No sales in this period."),
                        table_card("Sales by discount", ["Discount", "Sales", "Savings", "Orders"],
                                   [[name, money(values[0], currency), money(values[1], currency), values[2]]
                                    for name, values in data["discounts"]],
                                   "No discounted orders in this period."),
                        cls="p-report-grid"),
                    Details(Summary("How these numbers are calculated"),
                        P("Total sales include shipping and tax. Product revenue uses historical line totals. "
                          "Refunds use their payment transaction date. Returning customers have an earlier paid "
                          "order at this store. Dates are in UTC."),
                        P(f"Orders in other currencies are reported separately: {current.excluded_currency_orders} "
                          f"orders and {current.excluded_currency_refunds} refunds were left out of these totals.")
                        if current.excluded_currency_orders or current.excluded_currency_refunds else None,
                        cls="p-report-notes"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/subscriptions", methods=["GET"])
    def get(session, site_id: str, status: str = "all", q: str = "", sort: str = "due"):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                if status not in {"all", "active", "paused", "cancelled"}:
                    raise CommerceError("Choose a valid subscription status.")
                query = _contracts(db, site)
                if status != "all":
                    query = query.where(SubscriptionContract.state == status)
                rows = list(db.execute(query.order_by(SubscriptionContract.next_due_at)))
                if sort in {"name", "name_desc"}:
                    rows.sort(key=lambda row: (row[1].name or row[1].email).casefold(),
                              reverse=sort == "name_desc")
                q = q.strip()[:100]
                if q:
                    rows = [(contract, customer) for contract, customer in rows
                            if q.casefold() in (customer.name + " " + customer.email + " " +
                                " ".join(str(line.get("name", "")) for line in contract.lines_json)).casefold()]
                root = f"/admin/sites/{site.id}/subscriptions"
                return shell("Subscriptions", Form(
                    Label("Search subscriptions", Input(type="search", name="q", value=q)),
                    Label("Status", Select(*[Option(value.title(), value=value,
                        selected=status == value) for value in ("all", "active", "paused", "cancelled")],
                        name="status")),
                    Label("Sort", Select(*[Option(label, value=value, selected=sort == value)
                        for value, label in (("due", "Next billing date"), ("name", "Customer A to Z"),
                                             ("name_desc", "Customer Z to A"))], name="sort")),
                    Button("Filter", cls="e-button"),
                    method="get", cls="e-actions"),
                    index_table(["Customer", "Products", "Status", "Next billing", "Interval"], [
                        [A(customer.name or customer.email, href=root + "/" + contract.id),
                         ", ".join(str(line.get("name", "Product")) for line in contract.lines_json),
                         badge(contract.state.title(), "success" if contract.state == "active" else "neutral"),
                         contract.next_due_at.strftime("%d %b %Y") if contract.state == "active" else "Paused" if contract.state == "paused" else "Cancelled",
                         f"Every {contract.interval_months} month(s)"] for contract, customer in rows])
                    if rows else empty_state("No subscriptions found",
                        "Subscription contracts appear when customers complete a subscription checkout."))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/subscriptions/{contract_id}", methods=["GET"])
    def get(session, site_id: str, contract_id: str, notice: str = ""):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                result = db.execute(_contracts(db, site).where(
                    SubscriptionContract.id == contract_id)).first()
                if not result:
                    raise CommerceError("Subscription not found.")
                contract, customer = result
                root = f"/admin/sites/{site.id}/subscriptions"
                actions = []
                if contract.state == "active":
                    actions = [("pause", "Pause subscription"), ("skip", "Skip next delivery")]
                elif contract.state == "paused":
                    actions = [("resume", "Resume subscription")]
                if contract.state != "cancelled":
                    actions.append(("cancel", "Cancel subscription"))
                cycles = list(db.scalars(select(SubscriptionCycle).where(
                    SubscriptionCycle.contract_id == contract.id, SubscriptionCycle.site_id == site.id,
                    SubscriptionCycle.tenant_id == site.tenant_id)
                    .order_by(SubscriptionCycle.due_at.desc()).limit(30)))
                events = list(db.scalars(select(SubscriptionEvent).where(
                    SubscriptionEvent.contract_id == contract.id, SubscriptionEvent.site_id == site.id,
                    SubscriptionEvent.tenant_id == site.tenant_id)
                    .order_by(SubscriptionEvent.created_at.desc()).limit(20)))
                return shell("Subscription", A("Back to subscriptions", href=root, cls="e-button"),
                    P(notice, role="status") if notice else None,
                    card(key_value_list([("Customer", customer.name or customer.email),
                        ("Email", customer.email), ("Status", badge(contract.state.title())),
                        ("Next billing date", contract.next_due_at.strftime("%d %b %Y UTC")),
                        ("Interval", f"Every {contract.interval_months} month(s)")]),
                        title="Contract details"),
                    card(index_table(["Product", "Quantity"], [
                        [line.get("name", "Product"), line.get("quantity", 0)]
                        for line in contract.lines_json]), title="Products"),
                    card(Div(*[Form(csrf(session),
                        Input(type="hidden", name="version", value=contract.version),
                        Input(type="hidden", name="request_key", value=secrets.token_hex(16)),
                        Button(label, cls="e-button"), method="post",
                        action=root + "/" + contract.id + "/" + action)
                        for action, label in actions], cls="e-actions"),
                        P("Changes affect future deliveries. Payments already processing are not "
                          "cancelled or refunded. Resuming skips missed periods."), title="Schedule"),
                    card(index_table(["Due date", "Status"], [
                        [cycle.due_at.strftime("%d %b %Y"), badge(cycle.state.replace("_", " ").title())]
                        for cycle in cycles]) if cycles else P("No renewal deliveries yet."),
                        title="Recent deliveries"),
                    card(*[P(event.created_at.strftime("%d %b %Y %H:%M") + " · " +
                        event.action.replace("_", " ").title()) for event in events], title="Activity"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/subscriptions/{contract_id}/{action}", methods=["POST"])
    async def post(session, request, site_id: str, contract_id: str, action: str):
        try:
            user_id = actor(session)
            form = await request.form()
            check_csrf(session, form)
            try:
                version = int(str(form.get("version", "")))
            except ValueError as exc:
                raise CommerceError("Reload the subscription before trying again.") from exc
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                subscriptions.merchant_change(db, site, user_id, contract_id, action,
                    version=version, request_key=str(form.get("request_key", "")))
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/subscriptions/{contract_id}?" +
                urlencode({"notice": "Subscription updated."}), status_code=303)
        except CommerceError as exc:
            return error(exc)

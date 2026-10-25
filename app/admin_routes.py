"""Merchant Admin v2 foundation: home, discovery and settings navigation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fasthtml.common import (
    H2,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    Li,
    NotStr,
    Option,
    P,
    Select,
    Small,
    Strong,
    Ul,
)
from sqlalchemy import func, or_, select
from starlette.responses import JSONResponse

from app import content, order_management, site_golive
from app.admin_ui import badge, card, empty_state, index_table
from app.db import SessionLocal
from app.models import (
    CatalogueSettings,
    Membership,
    Order,
    Product,
    ProductVariant,
    RefundCommand,
    ShopCustomer,
    SiteOrder,
    SitePage,
    Stock,
    User,
)
from app.services import CommerceError, money

FEATURES = {
    "drafts": "Draft orders", "abandoned-checkouts": "Abandoned checkouts",
    "collections": "Collections", "inventory": "Inventory", "discounts": "Discounts",
    "blog": "Blog posts", "blog-posts": "Blog posts", "users": "Users and permissions",
    "policies": "Policies", "notifications": "Notifications", "markets": "Markets",
}


def search_results(db, site, user_id: str, query: str) -> list[dict]:
    """Search only resources visible through existing merchant permissions."""
    query = query.strip()[:100]
    if not query:
        return []
    # Treat %, _ and the escape character as literal search terms.
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    prefix = f"/admin/sites/{site.id}"
    rows = []

    def add(kind, title, href, subtitle=""):
        rows.append({"type": kind, "title": title, "href": href, "subtitle": subtitle})

    products = db.scalars(select(Product).where(
        Product.tenant_id == site.tenant_id,
        Product.name.ilike(pattern, escape="\\"),
    ).order_by(Product.name).limit(6))
    for product in products:
        add("Product", product.name, prefix + f"/products/{product.id}", product.slug)
    role = db.scalar(select(Membership.role).where(
        Membership.tenant_id == site.tenant_id, Membership.user_id == user_id,
    ))
    if role in {"admin", "merchant"}:
        orders = db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id)
            .where(SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
                   Order.tenant_id == site.tenant_id,
                   or_(Order.number.ilike(pattern, escape="\\"),
                       Order.email.ilike(pattern, escape="\\")))
            .order_by(Order.created_at.desc()).limit(6))
        for link, order in orders:
            add("Order", order.number, prefix + f"/orders/{link.id}", order.email)
    customers = db.scalars(select(ShopCustomer).where(
        ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id,
        or_(ShopCustomer.name.ilike(pattern, escape="\\"),
            ShopCustomer.email.ilike(pattern, escape="\\")),
    ).order_by(ShopCustomer.email).limit(6))
    for customer in customers:
        add("Customer", customer.name or customer.email, prefix + "/customers", customer.email)
    pages = db.scalars(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
        or_(SitePage.title.ilike(pattern, escape="\\"), SitePage.path.ilike(pattern, escape="\\")),
    ).order_by(SitePage.title).limit(6))
    for page in pages:
        add("Page", page.title, prefix + f"/pages/{page.id}", page.path)
    return rows


def sales_sparkline(values):
    """Render the actual daily sales series, including days without orders."""
    maximum = max(values, default=0) or 1
    points = " ".join(
        f"{8 + index * 584 / max(1, len(values) - 1):.1f},{112 - value * 96 / maximum:.1f}"
        for index, value in enumerate(values)
    )
    return NotStr(
        '<svg viewBox="0 0 600 128" role="img" aria-label="Daily total sales" '
        'style="width:100%;height:128px;color:var(--admin-accent,#005bd3)">'
        '<path d="M8 112H592" stroke="#dedede" fill="none"/>'
        f'<polyline points="{points}" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linejoin="round"/></svg>'
    )


def register_admin_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/search", methods=["GET"])
    def get(session, site_id: str, q: str = "", format: str = "html"):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id)
                results = search_results(db, site, user_id, q)
                if format == "json":
                    return JSONResponse({"q": q[:100], "results": results},
                                        headers={"Cache-Control": "no-store"})
                return shell("Search", Form(
                    Label("Search your store", Input(name="q", value=q[:100], type="search")),
                    Button("Search", cls="e-button"), method="get", cls="e-form"),
                    card(*[Div(A(row["title"], href=row["href"]),
                               Small(f'{row["type"]} · {row["subtitle"]}'),
                               cls="e-page-row") for row in results]) if results else
                    empty_state("No results" if q else "Search your store",
                                "Find products, orders, customers and pages."))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}", methods=["GET"])
    def get(session, site_id: str, days: str = "7"):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id)
                role = db.scalar(select(Membership.role).where(
                    Membership.tenant_id == site.tenant_id, Membership.user_id == user_id))
                manager = role in {"admin", "merchant"}
                user = db.get(User, user_id)
                prefix = f"/admin/sites/{site.id}"
                count_days = int(days) if days in {"1", "7", "30"} else 7
                end = datetime.now(UTC).date()
                start = end - timedelta(days=count_days - 1)
                pages = content.site_pages(db, site)
                report = site_golive.assess(db, site)
                rows = order_management.orders_for(db, site) if manager else []
                revenue = order_management.revenue_report(db, site, start, end) if manager else None
                daily = [0] * count_days
                if revenue:
                    for _, order, _ in rows:
                        index = (order.created_at.date() - start).days
                        if (0 <= index < count_days
                                and order.payment_status in order_management.REPORT_PAYMENT_STATES
                                and order.currency.upper() == revenue.currency):
                            daily[index] += order.total_minor
                tasks = []
                if manager:
                    from app.discounts import status
                    from app.models import Discount, SubscriptionContract
                    active_discounts = sum(status(rule) == "Active" for rule in db.scalars(
                        select(Discount).where(Discount.tenant_id == site.tenant_id,
                                               Discount.site_id == site.id)))
                    if active_discounts:
                        tasks.append((f"{active_discounts} active discounts", prefix + "/discounts"))
                    due_subscriptions = db.scalar(select(func.count()).select_from(
                        SubscriptionContract).where(SubscriptionContract.site_id == site.id,
                            SubscriptionContract.tenant_id == site.tenant_id,
                            SubscriptionContract.state == "active",
                            SubscriptionContract.next_due_at <= datetime.now(UTC) + timedelta(days=7)))
                    if due_subscriptions:
                        tasks.append((f"{due_subscriptions} subscriptions due within 7 days", prefix + "/subscriptions"))
                    unfulfilled = sum(order.status == "unfulfilled" for _, order, _ in rows)
                    pending = db.scalar(select(func.count()).select_from(RefundCommand).where(
                        RefundCommand.site_id == site.id, RefundCommand.tenant_id == site.tenant_id,
                        RefundCommand.state.in_(order_management.REFUND_STATES_HOLDING_FUNDS)))
                    if unfulfilled:
                        tasks.append((f"{unfulfilled} {'order' if unfulfilled == 1 else 'orders'} to fulfil", prefix + "/orders?status_filter=unfulfilled"))
                    if pending:
                        tasks.append((f"{pending} {'refund' if pending == 1 else 'refunds'} pending", prefix + "/orders"))
                catalogue_settings = db.scalar(select(CatalogueSettings).where(
                    CatalogueSettings.tenant_id == site.tenant_id, CatalogueSettings.site_id == site.id))
                threshold = catalogue_settings.low_stock_threshold if catalogue_settings else 5
                stockout_variants = (select(ProductVariant.id)
                    .outerjoin(Stock, Stock.variant_id == ProductVariant.id)
                    .where(ProductVariant.tenant_id == site.tenant_id,
                           ProductVariant.is_active.is_(True), ProductVariant.track_inventory.is_(True))
                    .group_by(ProductVariant.id)
                    .having(func.coalesce(func.sum(Stock.quantity - Stock.allocated), 0) <= threshold).subquery())
                stockouts = db.scalar(select(func.count()).select_from(stockout_variants))
                if stockouts:
                    tasks.append((f"{stockouts} {'variant' if stockouts == 1 else 'variants'} low in stock", prefix + "/inventory?low_stock=1"))
                drafts = sum(page.published_json is None for page in pages)
                if drafts:
                    tasks.append((f"{drafts} unpublished {'draft' if drafts == 1 else 'drafts'}", prefix + "/pages"))
                tasks.extend((check.label, prefix + "/pages" if check.action_url == prefix else check.action_url)
                             for check in report.failures)
                completed = sum(check.passed for check in report.checks)
                date_control = Form(Label("Date range", Select(
                    *[Option(label, value=value, selected=count_days == int(value))
                      for value, label in (("1", "Today"), ("7", "Last 7 days"), ("30", "Last 30 days"))],
                    name="days")), Button("Apply", cls="e-button"), method="get", cls="e-actions")
                metrics = Div(*[Div(Small(label), Strong(value), cls="admin-metric") for label, value in (
                    ("Total sales", money(revenue.gross_sales_minor, revenue.currency)),
                    ("Orders", str(revenue.order_count)),
                    ("Average order value", money(revenue.average_order_value_minor, revenue.currency)),
                )], cls="admin-metrics") if revenue else None
                return shell("Home", P(f"Hello, {(user.name or 'there').split()[0]}"),
                    Div(date_control, cls="admin-dashboard-controls"), metrics,
                    Div(card(sales_sparkline(daily),
                             Small(f"{start:%d %b} to {end:%d %b} · {revenue.currency}"),
                             title="Sales over time") if revenue else None,
                        card(P(f"{completed} of {len(report.checks)} steps complete"),
                             Ul(*[Li(A(check.label, href=prefix + "/pages" if check.action_url == prefix else check.action_url), " ",
                                     badge("Done", "success") if check.passed else badge("To do"))
                                  for check in report.checks], cls="admin-task-list"),
                             A("Review setup", href=prefix + "/golive", cls="e-button"),
                             title="Setup guide"), cls="admin-two-column"),
                    card(Ul(*[Li(A(label, href=href)) for label, href in tasks], cls="admin-task-list")
                         if tasks else P("You are all caught up."), title="To do"),
                    card(index_table(["Order", "Customer", "Payment", "Total"], [
                        [A(order.number, href=prefix + f"/orders/{link.id}"), order.email,
                         badge(order.payment_status.replace("_", " ").title(),
                               "success" if order.payment_status == "paid" else "neutral"),
                         money(order.total_minor, order.currency)]
                        for link, order, _ in rows[:5]]) if rows else
                        empty_state("Your orders will appear here", "Orders from your store are shown as they arrive."),
                        title="Recent orders") if manager else None,
                    card(Div(A("Products", href=prefix + "/products"),
                             A("Pages", href=prefix + "/pages"),
                             A("View store", href=f"/sites/{site.slug}/"),
                             A("Settings", href=prefix + "/settings"), cls="e-actions"), title="Quick links"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                prefix = f"/admin/sites/{site.id}"
                entries = [
                    ("General", "/settings/general", "Store details and preferences"),
                    ("Plan and billing", "/settings/billing", "Your plan, usage and billing"),
                    ("Users and permissions", "/settings/users", "Staff access and roles"),
                    ("Payments", "/settings/payments", "Payment providers and commerce mode"),
                    ("Checkout", "/settings/checkout", "Checkout and customer accounts"),
                    ("Shipping and delivery", "/settings/shipping", "Destinations and delivery settings"),
                    ("Taxes and duties", "/settings/taxes", "Tax configuration and previews"),
                    ("Notifications", "/settings/notifications", "Customer email and delivery status"),
                    ("Domains", "/settings/domains", "Domain and go-live checks"),
                    ("Integrations", "/integrations", "Connect services to your store"),
                    ("Policies", "/settings/policies", "Store and customer policies"),
                ]
                return shell("Settings", Div(
                    Div(Label("Find a setting", Input(type="search", placeholder="Search settings",
                        data_settings_search=True)),
                        Div(*[A(label, href=prefix + href, data_settings_item=label.lower())
                              for label, href, _ in entries], cls="admin-settings-nav")),
                    card(*[Div(A(label, href=prefix + href), P(description),
                               cls="e-page-row", data_settings_item=label.lower())
                           for label, href, description in entries], title=site.name),
                    cls="admin-settings-layout"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/billing", methods=["GET"])
    def get(session, site_id: str):
        try:
            from app import plans
            with SessionLocal() as db:
                user_id = actor(session)
                content.owned_site(db, site_id, user_id)
                plan = plans.account_plan(db, user_id)
                return shell("Plan and billing", card(H2(plan.name),
                    P("Manage your account plan and billing in one place."),
                    A("Manage plan and billing", href="/admin/billing", cls="e-button")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/coming-soon/{feature}", methods=["GET"])
    def get(session, site_id: str, feature: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                destinations = {"discounts": "/discounts", "blog": "/content/blog-posts",
                    "blog-posts": "/content/blog-posts", "users": "/settings/users",
                    "policies": "/settings/policies", "notifications": "/settings/notifications"}
                if feature in destinations:
                    from starlette.responses import RedirectResponse
                    return RedirectResponse(f"/admin/sites/{site.id}" + destinations[feature], status_code=303)
                title = FEATURES.get(feature, feature.replace("-", " ").capitalize()[:80])
                return shell(title, empty_state("Coming in this release",
                    f"{title} is being prepared for your store. Your existing tools are ready to use.",
                    A("Back to Home", href=f"/admin/sites/{site.id}", cls="e-button")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/pages", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                prefix = f"/admin/sites/{site.id}"
                pages = content.site_pages(db, site)
                return shell("Pages",
                    Div(*[A(label, href=prefix + "/" + key) for label, key in (
                        ("Snippets", "snippets"), ("Reviews", "reviews"),
                        ("Placeholders", "placeholders"), ("Inbox", "inbox"),
                        ("Open visual editor", "editor"))], cls="e-actions"),
                    card(index_table(["Title", "Path", "Status", "Edit"], [
                    [A(page.title, href=prefix + f"/editor?page={page.id}"), page.path,
                     badge("Published", "success") if page.published_json is not None else badge("Draft"),
                     Div(A("Visual editor", href=prefix + f"/editor?page={page.id}"),
                         A("Classic editor", href=prefix + f"/pages/{page.id}"), cls="e-actions")]
                    for page in pages]) if pages else empty_state("Add your first page",
                    "Share your story, store details and policies.")),
                    card(Form(csrf(session), Label("Title", Input(name="title", required=True)),
                        Label("Path", Input(name="path", placeholder="/pages/our-story", required=True)),
                        Label("Type", Select(*[Option(kind.title(), value=kind)
                            for kind in sorted(content.PAGE_KINDS)], name="kind")),
                        Button("Add page", cls="e-button"), method="post", action=prefix + "/pages",
                        cls="e-form", data_dirty_guard=True), title="Add a page"))
        except CommerceError as exc:
            return error(exc)

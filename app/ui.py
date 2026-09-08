"""FastHTML storefront, merchant shell, and assistant components."""

from __future__ import annotations

from fasthtml.common import (
    H1,
    H2,
    H3,
    H4,
    A,
    Article,
    Button,
    Code,
    Div,
    Footer,
    Form,
    Header,
    Img,
    Input,
    Label,
    Link,
    Main,
    Meta,
    Nav,
    Option,
    P,
    Script,
    Section,
    Select,
    Small,
    Span,
    Strong,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Title,
    Tr,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.integrations import fasterp
from app.models import (
    Category,
    Channel,
    Order,
    OutboxEvent,
    Product,
    ProductVariant,
    Review,
    Stock,
    VariantChannelListing,
    Voucher,
)
from app.services import CartSummary, ProductCard, available_stock, money, products, tenant_channel


def csrf_input(token: str):
    return Input(type="hidden", name="csrf_token", value=token)


def document_head(title: str, description: str = "Open, AI-assisted commerce for modern teams."):
    canonical = settings.public_url
    return (
        Title(f"{title} — FastShop"),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="description", content=description),
        Meta(property="og:title", content=f"{title} — FastShop"),
        Meta(property="og:description", content=description),
        Meta(property="og:type", content="website"),
        Meta(property="og:url", content=canonical),
        Link(rel="canonical", href=canonical),
        Link(rel="icon", href="/static/favicon.svg", type="image/svg+xml"),
        Link(rel="stylesheet", href="/static/site.css"),
        Script(src="/static/app.js", defer=True),
    )


def brand():
    return A(Span("F", cls="brand-mark"), Span("FastShop"), href="/", cls="brand")


def public_header(user: dict | None, cart_count: int, active: str = ""):
    return (
        Div("Free delivery on orders over €75 · Stripe test checkout", cls="announcement"),
        Header(
            brand(),
            Nav(
                A("AI Assistant", href="/assistant", cls="active" if active == "assistant" else ""),
                A("Shop", href="/products", cls="active" if active == "products" else ""),
                A("Categories", href="/categories", cls="active" if active == "categories" else ""),
                A("About", href="/pages/about"),
                cls="nav",
            ),
            Div(
                Form(
                    Input(type="search", name="q", placeholder="Search products…", aria_label="Search products"),
                    action="/products",
                    method="get",
                    cls="search",
                ),
                A(user["name"] if user else "Sign in", href="/account" if user else "/login", cls="icon-link account"),
                A("Wishlist", href="/wishlist", cls="icon-link"),
                A("Bag", Span(str(cart_count), cls="cart-count"), href="/cart", cls="icon-link"),
                cls="header-actions",
            ),
            cls="site-header",
        ),
    )


def public_footer():
    return Footer(
        Div(
            Div(
                Div(brand(), P("Independent commerce infrastructure for teams that want control.")),
                Div(H4("Shop"), A("All products", href="/products"), A("Categories", href="/categories"), A("Wishlist", href="/wishlist")),
                Div(H4("Company"), A("About", href="/pages/about"), A("Delivery & returns", href="/pages/delivery"), A("Privacy", href="/pages/privacy")),
                Div(H4("Build"), A("Developers", href="/developers"), A("FastERP", href="https://erp.fastsme.com"), A("Source", href="https://github.com/predictivelabsai/FastShop")),
                cls="footer-grid",
            ),
            Div(Span("© 2026 FastShop · MIT licensed"), Span("Demo photography: Unsplash contributors"), cls="footer-bottom"),
            cls="container",
        ),
        cls="site-footer",
    )


def assistant(surface: str, csrf: str, route: str, embedded: bool = False):
    form_id = "copilot-form" if embedded else "ai-form"
    samples = (
        ("/orders", "/stock", "/sync") if surface == "merchant" else ("Recommend something", "/products", "How does delivery work?")
    )
    content = (
        Div(
            Div(
                Strong("Merchant copilot" if surface == "merchant" else "Shopping assistant"),
                Small("Grounded in this page and live commerce data"),
            ),
            Button("×" if not embedded else "›", type="button", cls="ai-close", onclick="toggleAssistant(false)" if not embedded else "toggleCopilot()", aria_label="Close assistant"),
            cls="ai-head",
        ),
        Div(
            Div(
                "Ask about products, variants, delivery, stock, orders, or FastERP sync. "
                "I never process payment details.",
                cls="ai-msg",
            ),
            cls="ai-messages",
        ),
        Div(*[Button(text, type="button", onclick=f"askSample({text!r}, {form_id!r})") for text in samples], cls="ai-samples"),
        Form(
            csrf_input(csrf),
            Input(type="hidden", name="surface", value=surface),
            Input(type="hidden", name="route", value=route),
            Input(name="message", placeholder="Ask FastShop…", maxlength="1000", autocomplete="off"),
            Button("Send", type="submit", cls="button small"),
            id=form_id,
            cls="ai-form",
        ),
    )
    if embedded:
        return Div(*content, cls="copilot")
    return (
        Button("✦ Ask FastShop", type="button", cls="ai-launch", onclick="toggleAssistant(true)"),
        Div(*content, id="ai-drawer", cls="ai-drawer", aria_hidden="true"),
    )


def public_page(
    title: str,
    *content,
    user: dict | None,
    cart_count: int,
    csrf: str,
    route: str,
    active: str = "",
    description: str = "Open, AI-assisted commerce for modern teams.",
):
    return (
        *document_head(title, description),
        *public_header(user, cart_count, active),
        Main(*content),
        public_footer(),
        *assistant("shopper", csrf, route),
    )


def product_card(card: ProductCard):
    product = card.product
    return Article(
        A(
            Div(
                Img(src=product.image_url, alt=product.name, loading="lazy"),
                Span("Sale" if card.compare_at_minor else "Featured", cls="badge") if product.is_featured else None,
                cls="product-image",
            ),
            Div(
                Span(product.category.name, cls="product-category"),
                H3(product.name),
                Div(
                    Span(money(card.price_minor, card.currency), cls="price"),
                    Span(money(card.compare_at_minor, card.currency), cls="compare") if card.compare_at_minor else None,
                ),
                Div(f"{card.available} available", cls="stock"),
                cls="product-meta",
            ),
            href=f"/products/{product.slug}",
        ),
        cls="product-card",
    )


def home(session: Session):
    featured = products(session, featured=True)
    tenant, _ = tenant_channel(session)
    categories = session.scalars(select(Category).where(Category.tenant_id == tenant.id).order_by(Category.sort_order))
    symbols = {"footwear": "↗", "apparel": "◫", "technology": "◉", "accessories": "◇", "home": "⌂"}
    return (
        Section(
            Div(
                Span("Open commerce, thoughtfully made", cls="eyebrow"),
                H1("Objects for better everyday work."),
                P("A curated storefront running on FastShop: multi-channel catalog, auditable checkout, and an assistant that understands what is actually in stock."),
                Div(A("Shop the collection", href="/products", cls="button"), A("Ask the assistant", href="/assistant", cls="button secondary"), cls="hero-actions"),
                cls="hero-copy",
            ),
            Div(cls="hero-art", role="img", aria_label="Independent design store interior"),
            cls="hero",
        ),
        Section(
            Div(
                Div(H2("Featured products"), A("View all →", href="/products"), cls="section-heading"),
                Div(*[product_card(card) for card in featured], cls="product-grid"),
                cls="container",
            ),
            cls="section",
        ),
        Section(
            Div(
                Div(H2("Shop by category"), P("Useful things, organised simply."), cls="section-heading"),
                Div(
                    *[
                        A(Span(symbols.get(category.slug, "□"), cls="category-symbol"), H3(category.name), P(category.description), href=f"/categories/{category.slug}", cls="category-card")
                        for category in categories
                    ],
                    cls="category-grid",
                ),
                cls="container",
            ),
            cls="section soft",
        ),
        Section(
            Div(
                Div(H3("Curated quality"), P("Products selected for usefulness, repairability, and lasting materials."), cls="proof"),
                Div(H3("Fast fulfilment"), P("Stock-aware checkout and tracked delivery from our Tallinn warehouse."), cls="proof"),
                Div(H3("Easy returns"), P("A clear 30-day return window with order history and human support."), cls="proof"),
                cls="container proof-grid",
            ),
            cls="section dark",
        ),
    )


def products_page(session: Session, query: str = "", category: str = ""):
    cards = products(session, query=query, category=category)
    tenant, _ = tenant_channel(session)
    categories = list(session.scalars(select(Category).where(Category.tenant_id == tenant.id).order_by(Category.name)))
    return Div(
        Div(Span("Home / Shop", cls="crumbs"), H1("All products"), P("Considered essentials with transparent prices and live availability."), cls="page-head"),
        Form(
            Input(type="search", name="q", value=query, placeholder="Search the catalog"),
            Select(Option("All categories", value=""), *[Option(item.name, value=item.slug, selected=item.slug == category) for item in categories], name="category"),
            Button("Apply", cls="button small", type="submit"),
            action="/products",
            method="get",
            cls="filters",
        ),
        Div(*[product_card(card) for card in cards], cls="product-grid") if cards else Div("No products match those filters.", cls="empty"),
        cls="container",
        style="padding-bottom:80px",
    )


def categories_page(session: Session):
    tenant, _ = tenant_channel(session)
    rows = list(session.scalars(select(Category).where(Category.tenant_id == tenant.id).order_by(Category.sort_order)))
    return Div(
        Div(Span("Home / Categories", cls="crumbs"), H1("Shop by category"), P("Browse the catalog through a small, useful taxonomy."), cls="page-head"),
        Div(*[A(H2(row.name), P(row.description), href=f"/categories/{row.slug}", cls="category-card") for row in rows], cls="category-grid"),
        cls="container",
        style="padding-bottom:80px",
    )


def product_detail(session: Session, product: Product, csrf: str, notice: str = ""):
    _, channel = tenant_channel(session)
    variants = []
    first_price = 0
    compare = None
    total_available = 0
    for index, variant in enumerate(product.variants):
        listing = session.scalar(select(VariantChannelListing).where(VariantChannelListing.variant_id == variant.id, VariantChannelListing.channel_id == channel.id))
        if not listing:
            continue
        available = available_stock(session, variant.id)
        total_available += available
        if index == 0:
            first_price, compare = listing.price_minor, listing.compare_at_minor
        variants.append(Label(Input(type="radio", name="variant_id", value=variant.id, checked=index == 0, disabled=available <= 0), Div(Span(variant.name), Small(f"{available} available")), cls="variant"))
    reviews = session.scalars(select(Review).where(Review.product_id == product.id, Review.is_approved.is_(True)).order_by(Review.created_at.desc()))
    return Div(
        Div(
            Div(Img(src=product.image_url, alt=product.name), cls="pdp-image"),
            Div(
                Span(product.category.name, cls="eyebrow"),
                H1(product.name),
                P(product.subtitle, cls="subtitle"),
                Div(Span(money(first_price, channel.currency), cls="price"), Span(money(compare, channel.currency), cls="compare") if compare else None, cls="pdp-price"),
                Div(notice, cls="notice") if notice else None,
                Form(
                    csrf_input(csrf),
                    P("Choose an option", style="font-weight:800;font-size:12px"),
                    Div(*variants, cls="variant-list"),
                    Input(type="number", name="quantity", value="1", min="1", max="25", style="width:70px;padding:12px;border:1px solid var(--line);margin-right:8px"),
                    Button("Add to bag", cls="button", type="submit"),
                    action="/cart/add",
                    method="post",
                ),
                Div(Span(f"● {total_available} in stock"), Span("Free delivery over €75"), Span("30-day returns"), cls="availability"),
                P(product.description, cls="description"),
                Div(H2("Customer reviews"), *[Div(Div("★" * review.rating, cls="stars"), H3(review.title), P(review.body), Small(f"— {review.author_name}"), cls="review") for review in reviews], cls="reviews"),
                cls="pdp-info",
            ),
            cls="pdp",
        ),
        cls="container",
    )


def summary_panel(summary: CartSummary, csrf: str, checkout: bool = False):
    return Div(
        H2("Order summary", style="font-family:Georgia,serif;font-weight:500"),
        Div(Span("Subtotal"), Span(money(summary.subtotal_minor, summary.currency)), cls="summary-row"),
        Div(Span("Discount"), Span("−" + money(summary.discount_minor, summary.currency)), cls="summary-row") if summary.discount_minor else None,
        Div(Span("Delivery"), Span("Free" if not summary.shipping_minor else money(summary.shipping_minor, summary.currency)), cls="summary-row"),
        Div(Span("Included VAT"), Span(money(summary.tax_minor, summary.currency)), cls="summary-row"),
        Div(Span("Total"), Span(money(summary.total_minor, summary.currency)), cls="summary-row total"),
        Form(csrf_input(csrf), Input(name="code", placeholder="Voucher code"), Button("Apply", cls="button small", type="submit"), action="/cart/voucher", method="post", cls="voucher") if not checkout else None,
        A("Continue to checkout", href="/checkout", cls="button", style="width:100%") if not checkout and summary.lines else None,
        Small("Payments run in test mode. No card details are collected by this demo.", style="display:block;color:var(--muted);margin-top:14px;line-height:1.5"),
        cls="summary-card",
    )


def cart_page(summary: CartSummary, csrf: str, error: str = ""):
    return Div(
        Div(Span("Home / Bag", cls="crumbs"), H1("Your bag"), P("Review quantities before secure checkout."), cls="page-head"),
        Div(error, cls="error") if error else None,
        Div(
            Div(*[Div(Img(src=item["product"].image_url, alt=item["product"].name), Div(H3(item["product"].name), P(item["variant"].name), P(item["variant"].sku), Span(money(item["total_minor"], summary.currency), cls="price")), Form(csrf_input(csrf), Input(type="hidden", name="line_id", value=item["line"].id), Input(type="number", name="quantity", value=str(item["line"].quantity), min="0", max="25"), Button("Update", cls="button secondary small", type="submit"), action="/cart/update", method="post", cls="qty-form"), cls="cart-line") for item in summary.lines]) if summary.lines else Div(H2("Your bag is empty"), P("Explore the collection and add something useful."), A("Shop products", href="/products", cls="button"), cls="empty"),
            summary_panel(summary, csrf),
            cls="cart-layout",
        ),
        cls="container",
    )


def checkout_page(summary: CartSummary, csrf: str, idempotency_key: str, user: dict | None, error: str = ""):
    return Div(
        Div(Span("Bag / Checkout", cls="crumbs"), H1("Checkout"), P("Delivery details and an auditable order confirmation."), cls="page-head"),
        Div(error, cls="error") if error else None,
        Div(
            Div(
                H2("Delivery details", style="font-family:Georgia,serif;font-weight:500"),
                Form(
                    csrf_input(csrf),
                    Input(type="hidden", name="idempotency_key", value=idempotency_key),
                    Div(Label("Email"), Input(type="email", name="email", value=user["email"] if user else "", required=True), cls="field wide"),
                    Div(Label("Full name"), Input(name="full_name", required=True), cls="field"),
                    Div(Label("Address"), Input(name="address_line", required=True), cls="field"),
                    Div(Label("City"), Input(name="city", required=True), cls="field"),
                    Div(Label("Postcode"), Input(name="postcode", required=True), cls="field"),
                    Div(Label("Country"), Select(Option("Estonia", value="EE"), Option("Finland", value="FI"), Option("Germany", value="DE"), Option("United Kingdom", value="GB"), name="country"), cls="field wide"),
                    Button("Place test order", cls="button wide", type="submit"),
                    action="/checkout",
                    method="post",
                    cls="checkout-form",
                ),
                cls="form-card",
            ),
            summary_panel(summary, csrf, checkout=True),
            cls="checkout-layout",
        ),
        cls="container",
    )


def success_page(order: Order):
    return Div(
        Div(
            Span("Order confirmed", cls="eyebrow"),
            H1("Thank you."),
            P(f"Order {order.number} is paid in test mode and queued for FastERP reconciliation."),
            Div(Span("Total"), Span(money(order.total_minor, order.currency)), cls="summary-row total"),
            A("Continue shopping", href="/products", cls="button"),
            cls="login-card",
        ),
        cls="login-wrap",
    )


def login_page(csrf: str, error: str = "", google_enabled: bool = False):
    return Div(
        Div(
            H1("Welcome back"),
            P("Sign in to see orders, wishlists, and merchant tools."),
            Div(error, cls="error") if error else None,
            A("Continue with Google", href="/auth/google", cls="button", style="width:100%") if google_enabled else Div("Google sign-in is not configured locally.", cls="notice"),
            Div("or use the local development account", cls="or"),
            Form(csrf_input(csrf), Input(type="email", name="email", placeholder="Email", required=True), Input(type="password", name="password", placeholder="Password", required=True), Button("Sign in", cls="button", type="submit"), action="/login", method="post"),
            cls="login-card",
        ),
        cls="login-wrap",
    )


MERCHANT_NAV = (
    ("Overview", (("dashboard", "Dashboard", "/admin"), ("assistant", "AI Assistant", "/admin/assistant"))),
    ("Commerce", (("products", "Products", "/admin/products"), ("orders", "Orders", "/admin/orders"), ("customers", "Customers", "/admin/customers"), ("promotions", "Promotions", "/admin/promotions"))),
    ("Operations", (("inventory", "Inventory", "/admin/inventory"), ("channels", "Channels", "/admin/channels"), ("content", "Content & menus", "/admin/content"), ("integrations", "FastERP integration", "/admin/integrations/fasterp"))),
    ("Build", (("developers", "Developers", "/developers"), ("store", "View storefront", "/"))),
)


def merchant_page(title: str, active: str, *content, user: dict, csrf: str, route: str):
    navigation = Div(
        brand(),
        *[Div(Div(label, cls="nav-label"), *[A(name, href=href, cls="active" if key == active else "") for key, name, href in items], cls="nav-group") for label, items in MERCHANT_NAV],
        cls="merchant-nav",
    )
    return (
        *document_head(title),
        Div(
            navigation,
            Main(Div(H1(title), Div(Button("Copilot", type="button", cls="button secondary small", onclick="toggleCopilot()"), Span(user["email"]), A("Sign out", href="/logout"), cls="merchant-actions"), cls="merchant-top"), *content, cls="merchant-main"),
            assistant("merchant", csrf, route, embedded=True),
            cls="merchant-shell",
        ),
    )


def dashboard(session: Session):
    revenue = session.scalar(select(func.coalesce(func.sum(Order.total_minor), 0)).where(Order.payment_status == "paid")) or 0
    order_count = session.scalar(select(func.count(Order.id))) or 0
    product_count = session.scalar(select(func.count(Product.id)).where(Product.is_published.is_(True))) or 0
    stock_units = session.scalar(select(func.coalesce(func.sum(Stock.quantity - Stock.allocated), 0))) or 0
    orders = list(session.scalars(select(Order).order_by(Order.created_at.desc()).limit(8)))
    return (
        Div(Div(Small("Paid revenue"), Strong(money(revenue)), cls="kpi"), Div(Small("Orders"), Strong(str(order_count)), cls="kpi"), Div(Small("Published products"), Strong(str(product_count)), cls="kpi"), Div(Small("Available units"), Strong(str(stock_units)), cls="kpi"), cls="kpis"),
        panel("Recent orders", order_table(orders)),
    )


def panel(title: str, content, action=None):
    return Div(Div(H2(title), action, cls="panel-head"), content, cls="panel")


def order_table(orders: list[Order]):
    return Div(Table(Thead(Tr(Th("Order"), Th("Customer"), Th("Status"), Th("Payment"), Th("Total"))), Tbody(*[Tr(Td(A(order.number, href=f"/account/orders/{order.token}")), Td(order.email), Td(Span(order.status, cls="pill amber")), Td(Span(order.payment_status, cls="pill green")), Td(money(order.total_minor, order.currency))) for order in orders])), cls="table-scroll")


def admin_products(session: Session):
    rows = products(session)
    return panel("Published catalog", Div(Table(Thead(Tr(Th("Product"), Th("Category"), Th("Price"), Th("Available"), Th("Status"))), Tbody(*[Tr(Td(A(card.product.name, href=f"/products/{card.product.slug}")), Td(card.product.category.name), Td(money(card.price_minor, card.currency)), Td(str(card.available)), Td(Span("Published", cls="pill green"))) for card in rows])), cls="table-scroll"), A("View storefront", href="/products", cls="button secondary small"))


def admin_inventory(session: Session):
    rows = session.execute(select(ProductVariant, Stock).join(Stock, Stock.variant_id == ProductVariant.id).order_by(ProductVariant.sku)).all()
    return panel("Warehouse availability", Div(Table(Thead(Tr(Th("SKU"), Th("Variant"), Th("On hand"), Th("Allocated"), Th("Available"))), Tbody(*[Tr(Td(variant.sku), Td(variant.name), Td(str(stock.quantity)), Td(str(stock.allocated)), Td(Span(str(stock.quantity - stock.allocated), cls="pill green" if stock.quantity - stock.allocated >= 8 else "pill amber"))) for variant, stock in rows])), cls="table-scroll"))


def admin_promotions(session: Session):
    vouchers = list(session.scalars(select(Voucher).order_by(Voucher.created_at)))
    return panel("Vouchers and promotions", Div(Table(Thead(Tr(Th("Code"), Th("Type"), Th("Value"), Th("Minimum"), Th("Used"), Th("Status"))), Tbody(*[Tr(Td(Code(v.code)), Td(v.kind), Td(f"{v.value}%" if v.kind == "percentage" else money(v.value)), Td(money(v.minimum_minor)), Td(str(v.used_count)), Td(Span("Active" if v.is_active else "Paused", cls="pill green" if v.is_active else "pill"))) for v in vouchers])), cls="table-scroll"))


def admin_channels(session: Session):
    tenant, _ = tenant_channel(session)
    channels = list(session.scalars(select(Channel).where(Channel.tenant_id == tenant.id)))
    return panel("Sales channels", Div(Table(Thead(Tr(Th("Channel"), Th("Currency"), Th("Country"), Th("Locale"), Th("Tax display"), Th("Status"))), Tbody(*[Tr(Td(channel.name), Td(channel.currency), Td(channel.country_code), Td(channel.locale), Td("Tax inclusive" if channel.prices_include_tax else "Tax exclusive"), Td(Span("Active", cls="pill green"))) for channel in channels])), cls="table-scroll"))


def integration_page(session: Session, message: str = ""):
    state = fasterp.status()
    pending = session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.status == "pending")) or 0
    failed = session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.status == "failed")) or 0
    return (
        Div(message, cls="notice") if message else None,
        panel("FastERP connection", Div(Div(H3("erp.fastsme.com"), P(state.message), Small("FastShop sends confirmed orders through a signed, idempotent API boundary; it never writes into FastERP tables.")), Span("Reachable" if state.reachable else "Unavailable", cls="pill green" if state.reachable else "pill amber"), cls="integration-state")),
        Div(Div(Small("Pending events"), Strong(str(pending)), cls="kpi"), Div(Small("Failed events"), Strong(str(failed)), cls="kpi"), Div(Small("Connector token"), Strong("Ready" if state.configured else "Not set", style="font-size:18px"), cls="kpi"), cls="kpis"),
    )


def developers_page():
    return Div(
        Div(Span("Build with FastShop", cls="eyebrow"), H1("Composable where it matters."), P("Use the versioned API for storefronts, operations, and FastERP integrations. HTML and API routes share the same commerce rules."), cls="page-head"),
        Div(Div(H3("OpenAPI"), P("Typed catalog and integration contracts."), Code("GET /api/openapi.json"), A("Open Swagger →", href="/api/docs"), cls="developer-card"), Div(H3("Catalog"), P("Products, variants, categories, prices, and availability."), Code("GET /api/v1/products"), cls="developer-card"), Div(H3("Safe writes"), P("Bearer-gated, idempotent integration commands."), Code("POST /api/v1/inventory"), cls="developer-card"), cls="developer-grid"),
        cls="container",
    )


def content_page(slug: str):
    pages = {
        "about": ("Independent commerce", "FastShop is an open-source commerce platform designed for small and growing teams that want their storefront, operations, and data to remain understandable."),
        "delivery": ("Delivery & returns", "Orders ship from Tallinn with tracking. Delivery is free over €75. Unused items may be returned within 30 days."),
        "privacy": ("Privacy", "FastShop stores only the customer and order information needed to fulfil purchases. The shopping assistant never receives payment details."),
    }
    title, body = pages.get(slug, ("Page not found", "That content page does not exist."))
    return Div(Div(Span("FastShop", cls="eyebrow"), H1(title), P(body), cls="page-head"), cls="container", style="min-height:55vh")

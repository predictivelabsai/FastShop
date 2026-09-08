"""FastShop FastHTML storefront, merchant console, and mounted commerce API."""

from __future__ import annotations

import secrets

from fasthtml.common import RedirectResponse, fast_app
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.staticfiles import StaticFiles

from app import __version__, ai, auth, ui
from app.api import api
from app.config import settings
from app.db import SessionLocal, health, prepare_schema
from app.integrations import fasterp
from app.models import (
    Cart,
    CartLine,
    Membership,
    Order,
    Product,
    ProductVariant,
    User,
    WishlistItem,
)
from app.seed import seed
from app.services import (
    CommerceError,
    add_to_cart,
    apply_voucher,
    cart_summary,
    complete_checkout,
    find_or_create_user,
    get_or_create_cart,
    product_by_slug,
    products,
    toggle_wishlist,
    update_cart_line,
)

prepare_schema()
with SessionLocal() as bootstrap_session:
    seed(bootstrap_session, settings.admin_email)
    bootstrap_session.commit()

app, rt = fast_app(
    live=False,
    pico=False,
    secret_key=settings.session_secret,
    sess_https_only=settings.is_production,
    same_site="lax",
)
# FastHTML's catch-all file route would otherwise shadow `/robots.txt`,
# `/sitemap.xml`, and `/swagger.json`; assets are served by the explicit mount.
app.routes[:] = [
    route for route in app.routes if getattr(route, "path", "") != "/{fname:path}.{ext:static}"
]
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/api", api)


def csrf_token(session: dict) -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def safe_next_path(value: str, fallback: str = "") -> str:
    """Accept only same-origin absolute paths after authentication."""
    return value if value.startswith("/") and not value.startswith("//") else fallback


def require_csrf(session: dict, supplied: str) -> None:
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        raise CommerceError("Your session expired. Refresh the page and try again.")


def current_user(session: dict) -> dict | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not user or not user.is_active:
            session.clear()
            return None
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name or user.email.split("@", 1)[0],
            "role": session.get("role", "customer"),
        }


def current_cart(db, session: dict, *, create: bool = False) -> Cart | None:
    token = session.get("cart_token")
    cart = None
    if token:
        cart = db.scalar(
            select(Cart)
            .options(selectinload(Cart.lines))
            .where(Cart.token == token, Cart.status == "open")
        )
    if not cart and create:
        cart = get_or_create_cart(db, None, session.get("user_id"))
        session["cart_token"] = cart.token
    return cart


def cart_count(db, session: dict) -> int:
    cart = current_cart(db, session)
    if not cart:
        return 0
    return int(
        db.scalar(select(func.coalesce(func.sum(CartLine.quantity), 0)).where(CartLine.cart_id == cart.id))
        or 0
    )


def public_response(session: dict, route: str, title: str, content, active: str = ""):
    with SessionLocal() as db:
        count = cart_count(db, session)
    return ui.public_page(
        title,
        content,
        user=current_user(session),
        cart_count=count,
        csrf=csrf_token(session),
        route=route,
        active=active,
    )


def admin_guard(session: dict):
    user = current_user(session)
    if not user:
        return None, RedirectResponse("/login?next=/admin", status_code=303)
    if user["role"] not in {"admin", "merchant"}:
        return None, RedirectResponse("/account", status_code=303)
    return user, None


def establish_session(session: dict, user: User, role: str) -> None:
    session["user_id"] = user.id
    session["role"] = role
    session["email"] = user.email


@rt("/")
def get(session):
    with SessionLocal() as db:
        content = ui.home(db)
    return public_response(session, "/", "Independent commerce", content)


@rt("/products")
def get(session, q: str = "", category: str = ""):
    with SessionLocal() as db:
        content = ui.products_page(db, q, category)
    return public_response(session, "/products", "Shop", content, "products")


@rt("/categories")
def get(session):
    with SessionLocal() as db:
        content = ui.categories_page(db)
    return public_response(session, "/categories", "Categories", content, "categories")


@rt("/categories/{slug}")
def get(session, slug: str):
    with SessionLocal() as db:
        content = ui.products_page(db, category=slug)
    return public_response(session, f"/categories/{slug}", "Category", content, "categories")


@rt("/products/{slug}")
def get(session, slug: str, added: str = ""):
    with SessionLocal() as db:
        product = product_by_slug(db, slug)
        if not product:
            return Response("Product not found", status_code=404)
        content = ui.product_detail(db, product, csrf_token(session), "Added to your bag." if added else "")
        title = product.name
    return public_response(session, f"/products/{slug}", title, content, "products")


@rt("/cart/add")
def post(session, variant_id: str, quantity: int = 1, csrf_token: str = ""):
    try:
        require_csrf(session, csrf_token)
        with SessionLocal() as db:
            cart = current_cart(db, session, create=True)
            add_to_cart(db, cart, variant_id, quantity)
            product_slug = db.scalar(
                select(Product.slug)
                .join(ProductVariant, ProductVariant.product_id == Product.id)
                .where(ProductVariant.id == variant_id)
            )
            db.commit()
        return RedirectResponse(f"/products/{product_slug}?added=1", status_code=303)
    except CommerceError:
        return RedirectResponse("/cart?error=Could+not+add+that+item", status_code=303)


@rt("/cart")
def get(session, error: str = ""):
    with SessionLocal() as db:
        cart = current_cart(db, session, create=True)
        summary = cart_summary(db, cart)
        content = ui.cart_page(summary, csrf_token(session), error)
        db.commit()
    return public_response(session, "/cart", "Your bag", content)


@rt("/cart/update")
def post(session, line_id: str, quantity: int, csrf_token: str = ""):
    try:
        require_csrf(session, csrf_token)
        with SessionLocal() as db:
            cart = current_cart(db, session)
            if not cart:
                raise CommerceError("Your bag is empty.")
            update_cart_line(db, cart, line_id, quantity)
            db.commit()
        return RedirectResponse("/cart", status_code=303)
    except CommerceError as exc:
        return RedirectResponse(f"/cart?error={str(exc).replace(' ', '+')}", status_code=303)


@rt("/cart/voucher")
def post(session, code: str, csrf_token: str = ""):
    try:
        require_csrf(session, csrf_token)
        with SessionLocal() as db:
            cart = current_cart(db, session)
            if not cart:
                raise CommerceError("Your bag is empty.")
            apply_voucher(db, cart, code)
            db.commit()
        return RedirectResponse("/cart", status_code=303)
    except CommerceError as exc:
        return RedirectResponse(f"/cart?error={str(exc).replace(' ', '+')}", status_code=303)


@rt("/checkout")
def get(session, error: str = ""):
    with SessionLocal() as db:
        cart = current_cart(db, session)
        if not cart or not cart.lines:
            return RedirectResponse("/cart", status_code=303)
        summary = cart_summary(db, cart)
        key = session.setdefault("checkout_idempotency_key", secrets.token_urlsafe(24))
        content = ui.checkout_page(summary, csrf_token(session), key, current_user(session), error)
    return public_response(session, "/checkout", "Checkout", content)


@rt("/checkout")
def post(
    session,
    email: str,
    full_name: str,
    address_line: str,
    city: str,
    postcode: str,
    country: str,
    idempotency_key: str,
    csrf_token: str = "",
):
    try:
        require_csrf(session, csrf_token)
        if idempotency_key != session.get("checkout_idempotency_key"):
            raise CommerceError("Checkout session expired.")
        with SessionLocal() as db:
            cart = current_cart(db, session)
            if not cart:
                raise CommerceError("Your bag is empty.")
            order = complete_checkout(
                db,
                cart,
                email=email,
                full_name=full_name,
                address_line=address_line,
                city=city,
                postcode=postcode,
                country=country,
                idempotency_key=idempotency_key,
            )
            token = order.token
            db.commit()
        session.pop("cart_token", None)
        session.pop("checkout_idempotency_key", None)
        return RedirectResponse(f"/checkout/success/{token}", status_code=303)
    except CommerceError as exc:
        return RedirectResponse(f"/checkout?error={str(exc).replace(' ', '+')}", status_code=303)


@rt("/checkout/success/{token}")
def get(session, token: str):
    with SessionLocal() as db:
        order = db.scalar(select(Order).where(Order.token == token))
        if not order:
            return Response("Order not found", status_code=404)
        content = ui.success_page(order)
    return public_response(session, f"/checkout/success/{token}", "Order confirmed", content)


@rt("/login")
def get(session, error: str = "", next: str = ""):
    destination = safe_next_path(next)
    if destination:
        session["login_next"] = destination
    if current_user(session):
        return RedirectResponse(destination or "/account", status_code=303)
    return (
        *ui.document_head("Sign in"),
        ui.login_page(csrf_token(session), error, auth.google_enabled()),
    )


@rt("/login")
def post(session, email: str, password: str, csrf_token: str = ""):
    try:
        require_csrf(session, csrf_token)
    except CommerceError:
        return RedirectResponse("/login?error=Session+expired", status_code=303)
    if not auth.valid_local_credentials(email, password):
        return RedirectResponse("/login?error=Invalid+email+or+password", status_code=303)
    with SessionLocal() as db:
        user = find_or_create_user(db, email, "FastShop Admin")
        establish_session(session, user, "admin")
        db.commit()
    return RedirectResponse(
        safe_next_path(session.pop("login_next", ""), "/admin"), status_code=303
    )


@rt("/auth/google")
def get(session):
    if not auth.google_enabled():
        return RedirectResponse("/login?error=Google+sign-in+is+not+configured", status_code=303)
    state = auth.new_state()
    verifier = auth.new_code_verifier()
    session["google_oauth_state"] = state
    session["google_oauth_verifier"] = verifier
    return RedirectResponse(
        auth.authorize_url(state, auth.code_challenge(verifier)), status_code=303
    )


@rt("/auth/google/callback")
def get(session, code: str = "", state: str = "", error: str = ""):
    expected = session.pop("google_oauth_state", None)
    verifier = session.pop("google_oauth_verifier", "")
    if error or not code or not state or not expected or not secrets.compare_digest(state, expected):
        return RedirectResponse("/login?error=Google+sign-in+failed", status_code=303)
    identity = auth.exchange_code(code, verifier)
    if not identity:
        return RedirectResponse("/login?error=Google+account+is+not+authorised", status_code=303)
    with SessionLocal() as db:
        user = find_or_create_user(db, identity["email"], identity["name"])
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
        role = membership.role if membership else "customer"
        establish_session(session, user, role)
        db.commit()
    default = "/admin" if role in {"admin", "merchant"} else "/account"
    return RedirectResponse(
        safe_next_path(session.pop("login_next", ""), default), status_code=303
    )


@rt("/logout")
def get(session):
    session.clear()
    return RedirectResponse("/", status_code=303)


@rt("/account")
def get(session):
    user = current_user(session)
    if not user:
        return RedirectResponse("/login?next=/account", status_code=303)
    with SessionLocal() as db:
        orders = list(db.scalars(select(Order).where(Order.user_id == user["id"]).order_by(Order.created_at.desc())))
        content = ui.order_table(orders) if orders else ui.Div("No orders yet.", cls="empty")
        wrapper = ui.Div(ui.Div(ui.Span("Your account", cls="eyebrow"), ui.H1(f"Hello, {user['name']}"), ui.P("Review orders and saved products."), cls="page-head"), content, cls="container", style="padding-bottom:80px")
    return public_response(session, "/account", "Your account", wrapper)


@rt("/account/orders/{token}")
def get(session, token: str):
    user = current_user(session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with SessionLocal() as db:
        order = db.scalar(select(Order).options(selectinload(Order.lines)).where(Order.token == token, Order.user_id == user["id"]))
        if not order:
            return Response("Order not found", status_code=404)
        content = ui.Div(ui.Div(ui.Span("Account / Order", cls="crumbs"), ui.H1(order.number), ui.P(f"{order.status.title()} · {order.payment_status.title()}"), cls="page-head"), ui.order_table([order]), cls="container", style="padding-bottom:80px")
    return public_response(session, f"/account/orders/{token}", order.number, content)


@rt("/wishlist")
def get(session):
    user = current_user(session)
    if not user:
        return RedirectResponse("/login?next=/wishlist", status_code=303)
    with SessionLocal() as db:
        ids = list(db.scalars(select(WishlistItem.product_id).where(WishlistItem.user_id == user["id"])))
        cards = [card for card in products(db) if card.product.id in ids]
        content = ui.Div(ui.Div(ui.Span("Account / Wishlist", cls="crumbs"), ui.H1("Wishlist"), ui.P("Products saved for later."), cls="page-head"), ui.Div(*[ui.product_card(card) for card in cards], cls="product-grid") if cards else ui.Div("Your wishlist is empty.", cls="empty"), cls="container", style="padding-bottom:80px")
    return public_response(session, "/wishlist", "Wishlist", content)


@rt("/wishlist/toggle")
def post(session, product_id: str, csrf_token: str = ""):
    user = current_user(session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    try:
        require_csrf(session, csrf_token)
        with SessionLocal() as db:
            toggle_wishlist(db, user["id"], product_id)
            db.commit()
    except CommerceError:
        pass
    return RedirectResponse("/wishlist", status_code=303)


@rt("/assistant")
def get(session):
    content = ui.Div(ui.Div(ui.Span("AI shopping assistant", cls="eyebrow"), ui.H1("Find the right product, faster."), ui.P("Ask about products, variants, stock, delivery, and returns. Public tools are read-only and never receive payment details."), ui.Button("Open assistant", type="button", cls="button", onclick="toggleAssistant(true)"), cls="page-head"), cls="container", style="min-height:55vh")
    return public_response(session, "/assistant", "AI Assistant", content, "assistant")


@rt("/chat")
def post(session, message: str, route: str = "/", surface: str = "shopper", csrf_token: str = ""):
    try:
        require_csrf(session, csrf_token)
    except CommerceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    user = current_user(session)
    merchant = bool(user and user["role"] in {"admin", "merchant"} and (surface == "merchant" or route.startswith("/admin")))
    with SessionLocal() as db:
        response = ai.answer(db, message, route, merchant)
    return JSONResponse({"answer": response, "surface": "merchant" if merchant else "shopper"})


@rt("/admin")
def get(session):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    with SessionLocal() as db:
        content = ui.dashboard(db)
    return ui.merchant_page("Commerce overview", "dashboard", *content, user=user, csrf=csrf_token(session), route="/admin")


@rt("/admin/products")
def get(session):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    with SessionLocal() as db:
        content = ui.admin_products(db)
    return ui.merchant_page("Products", "products", content, user=user, csrf=csrf_token(session), route="/admin/products")


@rt("/admin/orders")
def get(session):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    with SessionLocal() as db:
        orders = list(db.scalars(select(Order).order_by(Order.created_at.desc())))
        content = ui.panel("All orders", ui.order_table(orders))
    return ui.merchant_page("Orders", "orders", content, user=user, csrf=csrf_token(session), route="/admin/orders")


@rt("/admin/inventory")
def get(session):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    with SessionLocal() as db:
        content = ui.admin_inventory(db)
    return ui.merchant_page("Inventory", "inventory", content, user=user, csrf=csrf_token(session), route="/admin/inventory")


@rt("/admin/promotions")
def get(session):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    with SessionLocal() as db:
        content = ui.admin_promotions(db)
    return ui.merchant_page("Promotions", "promotions", content, user=user, csrf=csrf_token(session), route="/admin/promotions")


@rt("/admin/channels")
def get(session):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    with SessionLocal() as db:
        content = ui.admin_channels(db)
    return ui.merchant_page("Channels", "channels", content, user=user, csrf=csrf_token(session), route="/admin/channels")


@rt("/admin/integrations/fasterp")
def get(session, message: str = ""):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    with SessionLocal() as db:
        content = ui.integration_page(db, message)
    return ui.merchant_page("FastERP integration", "integrations", *content, user=user, csrf=csrf_token(session), route="/admin/integrations/fasterp")


@rt("/admin/integrations/fasterp/sync")
def post(session, csrf_token: str = ""):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    try:
        require_csrf(session, csrf_token)
        with SessionLocal() as db:
            delivered, failed = fasterp.deliver_pending(db)
            db.commit()
        message = f"Sync finished: {delivered} delivered, {failed} deferred or failed."
    except CommerceError as exc:
        message = str(exc)
    return RedirectResponse(f"/admin/integrations/fasterp?message={message.replace(' ', '+')}", status_code=303)


@rt("/admin/assistant")
def get(session):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    content = ui.panel("Permission-aware assistant", ui.Div(ui.P("Use the copilot rail for grounded operational questions."), ui.P("Deterministic commands: /orders, /stock, /sync."), style="padding:20px"))
    return ui.merchant_page("AI Assistant", "assistant", content, user=user, csrf=csrf_token(session), route="/admin/assistant")


@rt("/admin/customers")
def get(session):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    with SessionLocal() as db:
        rows = list(db.scalars(select(User).order_by(User.created_at.desc())))
        content = ui.panel("Customers and staff", ui.Div(ui.Table(ui.Thead(ui.Tr(ui.Th("Name"), ui.Th("Email"), ui.Th("Status"))), ui.Tbody(*[ui.Tr(ui.Td(row.name), ui.Td(row.email), ui.Td(ui.Span("Active", cls="pill green"))) for row in rows])), cls="table-scroll"))
    return ui.merchant_page("Customers", "customers", content, user=user, csrf=csrf_token(session), route="/admin/customers")


@rt("/admin/content")
def get(session):
    user, redirect = admin_guard(session)
    if redirect:
        return redirect
    content = ui.panel("Content and navigation", ui.Div(ui.P("Store pages, menus, SEO records, and translations share the channel publication model."), ui.Ul(ui.Li("About"), ui.Li("Delivery & returns"), ui.Li("Privacy")), style="padding:20px"))
    return ui.merchant_page("Content & menus", "content", content, user=user, csrf=csrf_token(session), route="/admin/content")


@rt("/developers")
def get(session):
    return public_response(session, "/developers", "Developers", ui.developers_page())


@rt("/swagger.json")
def get():
    return JSONResponse(api.openapi())


@rt("/pages/{slug}")
def get(session, slug: str):
    return public_response(session, f"/pages/{slug}", slug.replace("-", " ").title(), ui.content_page(slug))


@rt("/healthz")
def get():
    return JSONResponse({**health(), "product": "FastShop", "version": __version__})


@rt("/readyz")
def get():
    return JSONResponse({**health(), "ready": True})


@rt("/robots.txt")
def get():
    return PlainTextResponse(f"User-agent: *\nAllow: /\nSitemap: {settings.public_url}/sitemap.xml\n")


@rt("/sitemap.xml")
def get():
    paths = ["/", "/products", "/categories", "/assistant", "/developers", "/pages/about", "/pages/delivery", "/pages/privacy"]
    with SessionLocal() as db:
        paths.extend(f"/products/{card.product.slug}" for card in products(db))
    body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + "".join(f"<url><loc>{settings.public_url}{path}</loc></url>" for path in paths) + "</urlset>"
    return Response(body, media_type="application/xml")


@rt("/llms.txt")
def get():
    return PlainTextResponse(
        "# FastShop\n\nOpen, multi-channel commerce built with FastHTML.\n\n"
        "- Storefront: /products\n- API: /api/docs\n- Assistant: /assistant\n"
    )

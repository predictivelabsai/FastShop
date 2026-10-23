"""Presentation hooks for public Liquid commerce and customer pages."""
import secrets
from types import SimpleNamespace

from fasthtml.common import H1, H2, A, Div, P, Section, Small
from fasthtml.core import FtResponse
from sqlalchemy import select
from starlette.responses import Response

from app.db import SessionLocal
from app.models import CheckoutAttempt, Site, SiteCart
from app.site_theme import theme_pack


def is_liquid(site):
    return theme_pack(site.published_settings_json or {}) == "liquid"


def liquid_page(site, request, title, *children, message_page=False):
    from app.site_ui_liquid import storefront

    request.session.setdefault("csrf_token", secrets.token_urlsafe(32))
    root = request.scope.get("site_base", "/sites/" + site.slug)
    document = {"title": title, "sections": []}
    page = SimpleNamespace(published_json=document, path=request.url.path, id="")
    children = list(children)
    summary = next((node for node in children if node is not None and
                    "liquid-order-summary" in getattr(node, "attrs", {}).get("class", "")), None)
    if title == "US delivery & checkout":
        from app import commerce
        from app.services import money

        with SessionLocal() as db:
            cart = db.scalar(select(SiteCart).where(
                SiteCart.id == request.session.get("cart:" + site.id, ""),
                SiteCart.site_id == site.id, SiteCart.tenant_id == site.tenant_id))
            lines = commerce.price_lines(db, site, commerce.settings_for(db, site), cart.lines_json)
            summary = Div(H2("Order summary"),
                *[liquid_order_line(line.name, line.variant_name, line.quantity,
                                     line.amount_minor, "USD") for line in lines],
                P("Subtotal: " + money(sum(line.amount_minor for line in lines), "USD")),
                P("Shipping and destination sales tax are calculated next."),
                cls="e-card liquid-order-summary", aria_live="polite")
    if summary is not None:
        left, right = [], [summary]
        for node in children:
            if node is summary or node is None:
                continue
            if str(getattr(node, "attrs", {}).get("action", "")).endswith("/pay"):
                right.append(node)
            else:
                left.append(node)
        children = [Div(Div(*left, cls="liquid-checkout-details"),
                        Div(*right, cls="liquid-checkout-summary"), cls="liquid-checkout-grid")]
    content = Section(
        Div(H1(title, cls="font-display"),
            Small("Sandbox · No live payments")
            if getattr(site, "_payment_mode", "sandbox") != "live" else None,
            Div(*children, cls="liquid-commerce-content", aria_live="polite"),
            cls="metal-container"), cls="metal-section liquid-commerce")
    if message_page:
        content = Section(
            Div(H1(title, cls="font-display"), *children, cls="metal-container"),
            cls="metal-section liquid-message-page")
    with SessionLocal() as db:
        cart = db.scalar(select(SiteCart).where(
            SiteCart.id == request.session.get("cart:" + site.id, ""),
            SiteCart.site_id == site.id, SiteCart.tenant_id == site.tenant_id))
        count = sum(line["quantity"] for line in cart.lines_json) if cart else 0
        if cart and cart.checkout_id and db.scalar(select(CheckoutAttempt.state).where(
                CheckoutAttempt.id == cart.checkout_id, CheckoutAttempt.site_id == site.id,
                CheckoutAttempt.tenant_id == site.tenant_id)) == "paid":
            count = 0
        return storefront(db, site, page, root, request.session.get("csrf_token", ""),
                          str(request.url), content=[content], cart_count=count)


def public_message(request, slug, message, *, status_code=400, plain=True):
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == slug))
        if site and is_liquid(site):
            root = request.scope.get("site_base", "/sites/" + slug)
            title = "Page not found" if status_code == 404 else "Please check your details"
            body = str(message)
            if body.rstrip(".") == title:
                body = "The page you were looking for has moved or no longer exists."
            return FtResponse(liquid_page(site, request,
                title, P(body, cls="text-muted-foreground"),
                Div(A("Shop all", href=root + "/shop", cls="e-button metal-cta"),
                    A("Back to home", href=root + "/", cls="liquid-message-home"),
                    cls="liquid-message-actions"), message_page=True),
                status_code=status_code, headers={"Cache-Control": "private, no-store"})
    return Response(str(message), status_code=status_code, media_type="text/plain" if plain else None)


def product_title(name, variant):
    # Support existing checkout/subscription snapshots as well as new labels.
    for separator in (" \u2014 ", " · "):
        name = name.removesuffix(separator + variant) if variant else name
    return name.replace("\u2014", "·")


def liquid_order_line(name, variant, quantity, amount, currency, purchase=""):
    from fasthtml.common import Span

    from app.services import money
    from app.site_ui_liquid import product_name

    return Div(Div(H2(product_name(product_title(name, variant))),
                   P(money(amount, currency)), cls="liquid-cart-line-title"),
               P(variant, cls="text-muted-foreground") if variant else None,
               P(Span("Quantity: " + str(quantity)), " · " + purchase if purchase else ""),
               cls="liquid-cart-line")


def liquid_cart(root, cart, lines, csrf):
    from fasthtml.common import H2, Button, Del, Form, Input, Label, Option, Select, Span

    from app.services import money
    from app.site_ui_liquid import product_name

    total = sum(line.amount_minor for line in lines)
    progress = min(100, round(total / 7500 * 100))
    def controls(line):
        def fields():
            return [csrf, Input(type="hidden", name="version", value=cart.version),
                    Input(type="hidden", name="variant_id", value=line.variant_id),
                    Input(type="hidden", name="subscription", value="on" if line.subscription else "off")]
        def step(quantity, text, label, disabled=False):
            return Form(*fields(), Input(type="hidden", name="quantity", value=quantity),
                        Button(text, aria_label=label, disabled=disabled),
                        method="post", action=root + "/cart/update")
        return Div(
            Div(step(line.quantity - 1, "−", "Decrease quantity", line.quantity <= 1),
                Form(*fields(), Label(Span("Quantity (0 removes)", cls="sr-only"),
                     Input(type="number", name="quantity", value=line.quantity,
                           min=0, max=25, required=True)),
                     Button("Update quantity", cls="liquid-cart-update"),
                     method="post", action=root + "/cart/update"),
                step(line.quantity + 1, "+", "Increase quantity", line.quantity >= 25),
                cls="cart-stepper"),
            Form(*fields(),
                 Select(Option("Subscribe & save 10%", value="on", selected=line.subscription),
                        Option("One-time purchase", value="off", selected=not line.subscription),
                        name="target_subscription", aria_label="Purchase type", cls="cart-select"),
                 Button("Update", cls="liquid-cart-update"), method="post",
                 action=root + "/cart/purchase", cls="liquid-cart-purchase")
            if line.subscription_eligible else Span("One-time purchase", cls="text-muted-foreground"),
            Form(*fields(), Input(type="hidden", name="quantity", value=0),
                 Button("Remove", cls="liquid-cart-remove"), method="post", action=root + "/cart/update"),
            cls="liquid-cart-controls")
    return Div(
        Div(P("You've got free shipping" if total >= 7500 else
              "Spend " + money(7500 - total, "USD") + " more for free shipping", aria_live="polite"),
            Div(Span(style=f"width:{progress}%"), cls="cart-shipping-track",
                role="progressbar", aria_label="Free shipping progress", aria_valuemin=0,
                aria_valuemax=100, aria_valuenow=progress), cls="cart-shipping"),
        Div(*[Div(Div(H2(product_name(product_title(line.name, line.variant_name))),
                         Div(Del(money(line.subtotal_minor, "USD")) if line.subscription else None,
                             P(money(line.amount_minor, "USD")), cls="liquid-cart-line-price"),
                         cls="liquid-cart-line-title"),
                       P(line.variant_name, cls="text-muted-foreground"), controls(line),
                       cls="liquid-cart-line") for line in lines], cls="liquid-cart-lines") if lines else
        Div(Div(*[Span(style=f"left:{20 + i * 22}%;animation-delay:{i * .6}s") for i in range(3)],
                cls="liquid-empty-glass", aria_hidden="true"),
            P("Your cart is still waiting for its first fizz. Have a look at the tablets or the bottle, and we will keep a spot warm here."),
            A("Browse the shop", href=root + "/shop", cls="e-button"), cls="liquid-cart-empty"),
        Div(P("Monthly subscription · Deliver every month", cls="tablet-pills") if any(line.subscription for line in lines) else None,
            Div(Span("Subtotal"), Span(money(total, "USD")), cls="liquid-cart-subtotal", aria_live="polite"),
            P("Shipping and destination sales tax are calculated next."),
            Form(csrf, Input(type="hidden", name="version", value=cart.version),
                 Label("Discount code", Input(name="code", value=cart.discount_code, maxlength=40)),
                 P("Save your code here. Eligibility and savings are checked at checkout using your email. An empty code removes it."),
                 Button("Save discount code", cls="e-button"), action=root + "/cart/discount",
                 method="post", cls="e-form"),
            A("Continue to checkout", href=root + "/checkout", cls="e-button liquid-checkout-button"),
            cls="liquid-cart-summary e-card") if lines else None,
        A("Resume existing checkout", href=root + "/checkout/" + cart.checkout_id) if cart.checkout_id else None,
        cls="liquid-cart-page")

"""FastShop-branded US basket, quote review and hosted Stripe payment handoff."""

import secrets

from fasthtml.common import (
    H2,
    H3,
    A,
    Aside,
    Button,
    Details,
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
    Option,
    P,
    Script,
    Select,
    Span,
    Strong,
    Summary,
    Title,
)
from fasthtml.core import FtResponse
from sqlalchemy import select
from starlette.responses import JSONResponse, RedirectResponse

from app import checkout_payments, commerce, customer_services, subscriptions
from app import checkout_services as checkout
from app.checkout_copy import DEFAULT_CHECKOUT_COPY
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway, credentials, make_gateway
from app.models import CheckoutAttempt, CommerceQuote, Product, Site, SiteCart, SubscriptionCycle
from app.platform_ui import platform_page
from app.services import CommerceError, money
from app.site_theme import theme_style
from app.storefront_ui import commerce_page, public_message
from app.ui import static_asset


def register_store_checkout_routes(rt, csrf, check_csrf, error):
    def resolve(db, slug):
        site = db.scalar(select(Site).where(Site.slug == slug, Site.status.in_(["preview", "published"])))
        config = commerce.settings_for(db, site) if site else None
        if not site or not config:
            raise CommerceError("Commerce is not enabled for this store.")
        site._payment_mode = commerce.payment_mode(db, site, config)
        return site, config

    def base(site, request):
        return request.scope.get("site_base", "/sites/" + site.slug)

    def cart_for(db, site, session):
        key = "cart:" + site.id
        cart = db.scalar(select(SiteCart).where(SiteCart.id == session.get(key, ""),
            SiteCart.site_id == site.id, SiteCart.tenant_id == site.tenant_id))
        if not cart:
            cart = SiteCart(site_id=site.id, tenant_id=site.tenant_id)
            db.add(cart)
            db.flush()
            session[key] = cart.id
        return cart

    def screen(site, request, title, *children):
        root = base(site, request)
        mode_label = "Live payments" if site._payment_mode == "live" else "Sandbox · No live payments"
        if (rendered := commerce_page(site, request, title, *children)) is not None:
            return FtResponse(rendered, headers={"Cache-Control": "private, no-store"})
        return FtResponse(platform_page(title, P(site.name + " · " + mode_label), *children,
            navigation=[A("Store", href=root + "/"), A("Bag", href=root + "/cart")], customer=True),
            headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "frame-ancestors 'self'"})

    def allowed_attempt(db, site, session, attempt_id):
        cart = cart_for(db, site, session)
        attempt = db.scalar(select(CheckoutAttempt).where(CheckoutAttempt.id == attempt_id,
            CheckoutAttempt.site_id == site.id, CheckoutAttempt.tenant_id == site.tenant_id))
        customer = customer_services.signed_in_customer(db, site, session)
        if not attempt or (cart.checkout_id != attempt.id and (not customer or customer.id != attempt.customer_id)):
            raise CommerceError("Checkout not found.")
        if db.scalar(select(SubscriptionCycle.id).where(SubscriptionCycle.attempt_id == attempt.id,
                SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id)):
            raise CommerceError("Use the subscription controls for this delivery.")
        return attempt, cart

    async def json_input(session, request):
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        token = request.headers.get("X-CSRF-Token") or data.get("csrf_token", "")
        check_csrf(session, {"csrf_token": token})
        return data

    def json_error(exc):
        return JSONResponse({"error": str(exc)}, status_code=422)

    @rt("/sites/{slug}/cart", methods=["GET"])
    def get(session, request, slug: str):
        try:
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                root = base(site, request)
                checkout.lock_site(db, site)
                cart = cart_for(db, site, session)
                if cart.checkout_id and checkout.owned_attempt(db, site, cart.checkout_id).state == "paid" and cart.lines_json:
                    cart.lines_json, cart.discount_code = [], ""
                    cart.version += 1
                lines = commerce.price_lines(db, site, config, cart.lines_json) if cart.lines_json else []
                from app import discounts
                applied = []
                if lines:
                    customer = customer_services.signed_in_customer(db, site, session)
                    rule = discounts.code_rule(db, site, cart.discount_code) if cart.discount_code else None
                    lines, _, applied = discounts.apply(db, site, lines, config.shipping_minor or 0,
                        code=cart.discount_code if rule else "", email=customer.email if customer else "", preview=True)
                db.commit()
                return screen(site, request, "Your bag", csrf(session), *[Div(H2(line.name), P(money(line.amount_minor, "USD")),
                    P("Monthly subscription · 10% merchandise saving" if line.subscription else "One-time purchase"),
                    Form(csrf(session), Input(type="hidden", name="version", value=cart.version),
                        Input(type="hidden", name="variant_id", value=line.variant_id),
                        Input(type="hidden", name="subscription", value="on" if line.subscription else "off"),
                        Label("Quantity (0 removes)", Input(type="number", name="quantity", value=line.quantity, min=0, max=25, required=True)),
                        Button("Update quantity", cls="e-button"), method="post", action=root + "/cart/update", cls="e-form"), cls="e-card") for line in lines],
                    P("Discount savings: " + money(sum(r["amount_minor"] for r in applied), "USD")) if applied else None,
                    P("Your bag is empty.") if not lines else P("Merchandise: " + money(sum(line.amount_minor for line in lines), "USD") + ". Shipping and destination sales tax are calculated next."),
                    Form(csrf(session), Input(type="hidden", name="version", value=cart.version),
                        Label("Discount code", Input(name="code", value=cart.discount_code, maxlength=40)),
                        P("Save your code here. Eligibility and savings are checked at checkout using your email. An empty code removes it."),
                        Button("Save discount code", cls="e-button"), action=root + "/cart/discount", method="post", cls="e-form e-card") if lines else None,
                    A("Continue to checkout", href=root + "/checkout", cls="e-button") if lines else A("Browse the shop", href=root + "/shop"),
                    A("Resume existing checkout", href=root + "/checkout/" + cart.checkout_id) if cart.checkout_id else None)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/cart/summary", methods=["GET"])
    def get(session, request, slug: str):
        try:
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                cart = db.scalar(select(SiteCart).where(SiteCart.id == session.get("cart:" + site.id, ""),
                    SiteCart.site_id == site.id, SiteCart.tenant_id == site.tenant_id))
                count = sum(line["quantity"] for line in cart.lines_json) if cart else 0
                if cart and cart.checkout_id and checkout.owned_attempt(db, site, cart.checkout_id).state == "paid":
                    count = 0
                return JSONResponse({"count": count}, headers={"Cache-Control": "private, no-store"})
        except CommerceError:
            return JSONResponse({"error": "Cart unavailable"}, status_code=404, headers={"Cache-Control": "private, no-store"})

    @rt("/sites/{slug}/cart/discount", methods=["POST"])
    async def post(session, request, slug: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            code = str(form.get("code", "")).strip().upper()
            if len(code) > 40 or any(char.isspace() for char in code):
                raise CommerceError("Enter a discount code of up to 40 characters without spaces.")
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                checkout.lock_site(db, site)
                cart = cart_for(db, site, session)
                if str(cart.version) != form.get("version"):
                    raise CommerceError("Your bag changed. Reload before updating the code.")
                if cart.checkout_id:
                    raise CommerceError("Finish or cancel the existing checkout and start a fresh bag before changing its code.")
                cart.discount_code, cart.version, cart.request_key = code, cart.version + 1, secrets.token_hex(16)
                db.commit()
                return RedirectResponse(base(site, request) + "/cart", status_code=303)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    async def change_cart(session, request, slug, *, add, purchase=False):
        form = await request.form()
        try:
            check_csrf(session, form)
            quantity = int(form.get("quantity", "1"))
            if not 0 <= quantity <= 25:
                raise CommerceError("Choose a quantity from 0 to 25.")
            if form.get("subscription", "off") not in ("on", "off"):
                raise CommerceError("Choose a valid purchase option.")
            subscription = form.get("subscription") == "on"
            if purchase and form.get("target_subscription") not in ("on", "off"):
                raise CommerceError("Choose a valid purchase option.")
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                checkout.lock_site(db, site)
                cart = cart_for(db, site, session)
                paid_reset = False
                if cart.checkout_id:
                    attempt = checkout.owned_attempt(db, site, cart.checkout_id)
                    if attempt.state in checkout.ACTIVE:
                        raise CommerceError("Cancel or finish the existing checkout before changing this bag.")
                    cart.checkout_id = None
                    if attempt.state == "paid":
                        paid_reset = True
                        cart.lines_json = []
                        cart.discount_code = ""
                if not add and str(cart.version) != form.get("version"):
                    raise CommerceError("Your bag changed in another tab. Reload before updating.")
                variant_id = str(form.get("variant_id", ""))
                lines = [dict(line) for line in cart.lines_json]
                if purchase:
                    # A late form must never restore items from a paid checkout.
                    if not paid_reset:
                        lines = commerce.convert_cart_purchase(lines, variant_id, subscription,
                            form.get("target_subscription") == "on")
                else:
                    match = next((line for line in lines if line["variant_id"] == variant_id and line["subscription"] == subscription), None)
                    if add and match:
                        quantity += match["quantity"]
                    lines = [line for line in lines if not (line["variant_id"] == variant_id and line["subscription"] == subscription)]
                    if quantity:
                        lines.append({"variant_id": variant_id, "quantity": quantity, "subscription": subscription})
                if lines:
                    commerce.price_lines(db, site, config, lines)
                cart.lines_json, cart.version, cart.request_key = lines, cart.version + 1, secrets.token_hex(16)
                db.commit()
                return RedirectResponse(base(site, request) + "/cart", status_code=303)
        except (CommerceError, ValueError) as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/cart/add", methods=["POST"])
    async def post(session, request, slug: str):
        return await change_cart(session, request, slug, add=True)

    @rt("/sites/{slug}/cart/update", methods=["POST"])
    async def post(session, request, slug: str):
        return await change_cart(session, request, slug, add=False)

    @rt("/sites/{slug}/cart/purchase", methods=["POST"])
    async def post(session, request, slug: str):
        return await change_cart(session, request, slug, add=False, purchase=True)

    @rt("/sites/{slug}/checkout", methods=["GET"])
    def get(session, request, slug: str):
        try:
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                root = base(site, request)
                cart = cart_for(db, site, session)
                db.commit()
                if cart.checkout_id:
                    return RedirectResponse(root + "/checkout/" + cart.checkout_id, status_code=303)
                if not cart.lines_json:
                    return RedirectResponse(root + "/cart", status_code=303)
                customer = customer_services.signed_in_customer(db, site, session)
                lines = commerce.price_lines(db, site, config, cart.lines_json)
                recurring_lines = [line for line in lines if line.subscription]
                count = sum(line.quantity for line in lines)
                total = sum(line.amount_minor for line in lines)
                copy = {**DEFAULT_CHECKOUT_COPY, **(site.published_settings_json or {}).get("checkout_copy", {})}
                policies = (site.published_settings_json or {}).get("policies") or {}
                policy_paths = (("privacy", copy["privacy_policy_link"], "/pages/privacy-policy"),
                    ("terms", copy["terms_policy_link"], "/pages/terms-and-conditions"),
                    ("refunds", copy["returns_policy_link"], "/pages/returns-and-refunds"),
                    ("shipping", copy["shipping_policy_link"], "/pages/shipping-policy"))
                config_states = [state for state in config.allowed_states_json if state in commerce.US_STATES]
                settings = site.published_settings_json or {}
                logo = settings.get("logo")
                def has_newsletter(node):
                    if isinstance(node, dict):
                        return node.get("kind") == "newsletter" or any(has_newsletter(value) for value in node.values())
                    if isinstance(node, list):
                        return any(has_newsletter(value) for value in node)
                    return False
                from app.models import SitePage
                newsletter_enabled = any(has_newsletter(page.published_json) for page in db.scalars(
                    select(SitePage).where(SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
                        SitePage.published_json.is_not(None))))
                def field(label, name, **kwargs):
                    kwargs.setdefault("placeholder", label)
                    kwargs.setdefault("aria_label", label)
                    return Label(Span(label, cls="checkout-sr-only"), Input(name=name, **kwargs), cls="checkout-field")
                def row(*children, cls=""):
                    return Div(*children, cls=("checkout-row " + cls).strip())
                def summary_line(line):
                    product = db.get(Product, line.product_id)
                    image_url = product.image_url if product and product.image_url else ""
                    display_name = line.name
                    for separator in (" · ", " — "):
                        if line.variant_name:
                            display_name = display_name.removesuffix(separator + line.variant_name)
                    return Div(Div(Img(src=image_url, alt="", cls="checkout-thumb") if image_url else Div(cls="checkout-thumb checkout-thumb-empty"),
                        Span(str(line.quantity), cls="checkout-quantity"), cls="checkout-thumb-wrap"),
                        Div(H3(display_name), P(line.variant_name or (copy["subscription_line"] if line.subscription else "")), cls="checkout-line-copy"),
                        Strong(money(line.amount_minor, "USD")), cls="checkout-line", data_checkout_line="")
                page = (
                    Title("Checkout · " + site.name),
                    Meta(name="viewport", content="width=device-width, initial-scale=1"),
                    Link(rel="stylesheet", href=static_asset("fonts.css")),
                    Link(rel="stylesheet", href=static_asset("checkout.css")),
                    Script(src=static_asset("checkout.js"), defer=True),
                    Div(
                    Header(A(Img(src=logo, alt=site.name, cls="checkout-logo") if logo else Span(site.name, cls="checkout-store-name"),
                             href=root + "/", aria_label=site.name + " home"),
                        A(Span(cls="checkout-bag-icon", aria_hidden="true"), aria_label="Bag", href=root + "/cart", cls="checkout-bag"), cls="checkout-header"),
                    Details(Summary(Span(copy["summary_toggle"]), Strong("USD " + money(total, "USD")), cls="checkout-summary-toggle"),
                        Div(*[summary_line(line) for line in lines], id="checkout-mobile-lines"), cls="checkout-mobile-summary"),
                    Main(Div(Form(csrf(session), Input(type="hidden", name="version", value=cart.version),
                        Input(type="hidden", name="country", value="US"), Input(type="hidden", name="full_name", value="", data_full_name=""),
                        Div(H2(copy["contact_heading"]), A(copy["sign_in"], href=root + "/account", cls="checkout-link")
                            if not customer and config.customer_accounts_enabled else None, cls="checkout-section-heading"),
                        field(copy["email_label"], "email", type="email", required=True, maxlength=320,
                            value=customer.email if customer else "", autocomplete="email", data_checkout_email=""),
                        Label(Input(type="checkbox", name="marketing_consent"), " " + copy["newsletter_label"], cls="checkout-checkout")
                            if newsletter_enabled else None,
                        H2(copy["delivery_heading"], cls="checkout-section-title"),
                        Label(copy["country_label"], Select(Option(copy["country_value"], value="US", selected=True), disabled=True), cls="checkout-select-field"),
                        row(field(copy["first_name_label"], "first_name", required=True, maxlength=80, autocomplete="shipping given-name", data_first_name=""),
                            field(copy["last_name_label"], "last_name", required=True, maxlength=80, autocomplete="shipping family-name", data_last_name="")),
                        field(copy["company_label"], "company", maxlength=120, autocomplete="shipping organization"),
                        field(copy["address_label"], "line1", required=True, maxlength=200, autocomplete="shipping address-line1"),
                        field(copy["apartment_label"], "line2", maxlength=200, autocomplete="shipping address-line2"),
                        row(field(copy["city_label"], "city", required=True, maxlength=200, autocomplete="shipping address-level2"),
                            Label(copy["state_label"], Select(Option(copy["state_label"], value="", selected=True),
                                *[Option(commerce.US_STATES[state], value=state) for state in config_states], name="state", required=True,
                                autocomplete="shipping address-level1"), cls="checkout-field checkout-select-field"),
                            field(copy["zip_label"], "postal_code", required=True, pattern=r"\d{5}(-\d{4})?", autocomplete="shipping postal-code")),
                        H3(copy["shipping_heading"], cls="checkout-subheading"),
                        Div(copy["shipping_prompt"], id="checkout-shipping-rates", cls="checkout-shipping-prompt", aria_live="polite",
                            data_no_rates=copy["no_shipping_rates"]),
                        Div(H2(copy["payment_heading"], cls="checkout-section-title"),
                            Div(Span("VISA"), Span("Mastercard"), Span("AMEX"), cls="checkout-card-marks"),
                            cls="checkout-payment-heading"),
                        P(copy["payment_security"], cls="checkout-help"),
                        Div(id="payment-element", cls="checkout-payment-element"),
                        Label(Input(type="checkbox", name="billing_same", checked=True), " " + copy["billing_address"], cls="checkout-checkout"),
                        Label(Input(type="checkbox", name="subscription_consent", required=True), " " + subscriptions.CONSENT_TEXT, cls="checkout-checkout checkout-subscription-consent") if recurring_lines else None,
                        Div(id="checkout-errors", role="alert", aria_live="assertive", hidden=True),
                        Button(copy["pay_button"], type="submit", cls="checkout-pay-button", data_checkout_pay=""),
                        Input(name="website", tabindex="-1", autocomplete="off", style="display:none"),
                        cls="checkout-form", action=root + "/checkout", method="post", data_checkout_form=""),
                        Footer(*[A(label, href=root + path) for key, label, path in policy_paths if policies.get(key)],
                            cls="checkout-footer"), cls="checkout-main-inner"),
                        Aside(Div(H2(copy["summary_heading"]), Div(*[summary_line(line) for line in lines], id="checkout-lines"),
                            Form(Input(type="hidden", name="csrf_token", value=session.get("csrf_token", "")),
                                Select(Option(copy["discount_code_option"], value="discount", selected=True),
                                    Option(copy["gift_card_option"], value="gift_card"), name="promo_type", aria_label=copy["discount_code_option"] + " or " + copy["gift_card_option"]),
                                Input(name="checkout_code", placeholder=copy["discount_label"], maxlength=64, value=cart.discount_code),
                                Input(type="hidden", name="code", value=cart.discount_code),
                                Input(type="hidden", name="gift_card_code", value=""),
                                Button(copy["apply_button"], type="button", data_checkout_apply=""), cls="checkout-discount-form"),
                            Div(Span(copy["subtotal_label"] + " · " + str(count) + " " + (copy["item_plural"] if count != 1 else copy["item_singular"])),
                                Strong(money(total, "USD")), cls="checkout-total-row", data_summary_subtotal=""),
                            Div(Span(copy["shipping_label"]), Span(copy["shipping_unknown"], data_summary_shipping=""), cls="checkout-total-row"),
                            Div(Span(copy["tax_label"]), Span("—", data_summary_tax=""), cls="checkout-total-row", data_summary_tax_row="", hidden=True),
                            Div(Span(copy["total_label"]), Strong("USD " + money(total, "USD"), data_summary_total=""), cls="checkout-total-row checkout-grand-total"),
                            Div(Span(copy["recurring_label"]), Strong(money(sum(line.unit_minor * line.quantity for line in recurring_lines), "USD") + " every month"),
                                cls="checkout-total-row checkout-recurring", data_summary_recurring="") if recurring_lines else None,
                            cls="checkout-summary-inner"), cls="checkout-summary"),
                        cls="checkout-main"),
                    cls="checkout-page", style=theme_style(settings) or None, data_checkout_root=root,
                        data_quote_url=root + "/checkout/quote", data_csrf=session.get("csrf_token", ""),
                    data_session_url_template=root + "/checkout/{attempt_id}/session",
                    data_detail_error=copy["checkout_details_error"], data_subscription_error=copy["subscription_consent_error"],
                    data_payment_load_error=copy["payment_load_error"], data_payment_prepare_error=copy["payment_prepare_error"],
                    data_quote_error=copy["quote_update_error"], data_payment_confirm_error=copy["payment_confirm_error"]),
                )
                return FtResponse(page, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy": "frame-ancestors 'self'"})
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/checkout", methods=["POST"])
    async def post(session, request, slug: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            promo_type = str(form.get("promo_type", "discount"))
            entered_code = str(form.get("checkout_code", "")).strip()
            code = (entered_code if promo_type == "discount" and entered_code else form.get("code", ""))
            gift_card_code = (entered_code if promo_type == "gift_card" and entered_code else form.get("gift_card_code", ""))
            name = str(form.get("full_name", "")).strip() or " ".join(
                part.strip() for part in (str(form.get("first_name", "")), str(form.get("last_name", ""))) if part.strip())
            if not name or len(name) > 160 or form.get("website"):
                raise CommerceError("Enter the recipient's full name.")
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                checkout.lock_site(db, site)
                cart = cart_for(db, site, session)
                if cart.checkout_id:
                    return RedirectResponse(base(site, request) + "/checkout/" + cart.checkout_id, status_code=303)
                if str(cart.version) != form.get("version"):
                    raise CommerceError("Your bag changed. Reload checkout before continuing.")
                customer = customer_services.signed_in_customer(db, site, session)
                email = customer_services.normalized_email(str(form.get("email", "")))
                if customer and email != customer.email:
                    raise CommerceError("Use your signed-in email, or sign out to check out as a guest.")
                customer = customer or customer_services.customer_for(db, site, email, create=True)
                attempt = checkout.prepare(db, site, customer.id, cart.lines_json, dict(form),
                    make_gateway(StripeGateway, site, db),
                    request_key=cart.request_key, code=code, recipient_name=name,
                    gift_card_code=gift_card_code,
                    subscription_consent=form.get("subscription_consent") == "on",
                    shipping_rate_id=form.get("shipping_rate_id") or None)
                cart.checkout_id = attempt.id
                db.commit()
                return RedirectResponse(base(site, request) + "/checkout/" + attempt.id, status_code=303)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/checkout/quote", methods=["POST"])
    async def quote_json(session, request, slug: str):
        try:
            data = await json_input(session, request)
            name = str(data.get("full_name", "")).strip()
            if not name or len(name) > 160:
                raise CommerceError("Enter the recipient's full name.")
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                checkout.lock_site(db, site)
                cart = cart_for(db, site, session)
                if not cart.lines_json:
                    raise CommerceError("Your bag is empty.")
                if (any(line.get("subscription") for line in cart.lines_json) and
                        data.get("subscription_consent") not in (True, "true", "on", 1)):
                    raise CommerceError("Confirm recurring-payment consent before subscribing.")
                email = customer_services.normalized_email(str(data.get("email", "")))
                customer = customer_services.signed_in_customer(db, site, session)
                if customer and email != customer.email:
                    raise CommerceError("Use your signed-in email, or sign out to check out as a guest.")
                customer = customer or customer_services.customer_for(db, site, email, create=True)
                if cart.checkout_id:
                    previous = checkout.owned_attempt(db, site, cart.checkout_id)
                    if previous.state != "prepared":
                        raise CommerceError("Resume or cancel your existing checkout before changing the quote.")
                    checkout.cancel_unstarted(db, site, previous.id)
                    cart.checkout_id = None
                cart.request_key = secrets.token_hex(16)
                destination = {key: data.get(key, "") for key in
                    ("line1", "line2", "city", "state", "postal_code")}
                destination["country"] = "US"
                company = str(data.get("company", "")).strip()
                if len(company) > 200:
                    raise CommerceError("Company name must be 200 characters or fewer.")
                attempt = checkout.prepare(db, site, customer.id, cart.lines_json, destination,
                    make_gateway(StripeGateway, site, db), request_key=cart.request_key,
                    code=data.get("code", ""), recipient_name=name,
                    gift_card_code=data.get("gift_card_code", ""),
                    subscription_consent=data.get("subscription_consent") in (True, "true", "on", 1),
                    shipping_rate_id=data.get("shipping_rate_id") or None)
                cart.checkout_id = attempt.id
                quote = db.scalar(select(CommerceQuote).where(CommerceQuote.id == attempt.quote_id,
                    CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id))
                if company:
                    quote.snapshot_json = quote.snapshot_json | {
                        "destination": quote.snapshot_json["destination"] | {"company": company}}
                snapshot = quote.snapshot_json
                from app.growth_settings import shipping_options
                subtotal = sum(line["amount_minor"] for line in snapshot["lines"])
                weight = sum((db.get(Product, line["product_id"]).weight_grams or 0) * line["quantity"]
                    for line in snapshot["lines"])
                rates = shipping_options(db, site, snapshot["destination"], subtotal, weight_grams=weight)
                lines = []
                for line in snapshot["lines"]:
                    product = db.get(Product, line["product_id"])
                    image_url = product.image_url if product and product.image_url else ""
                    lines.append({"name": line["name"], "variant": line.get("variant_name", ""),
                        "quantity": line["quantity"], "amount": line["amount_minor"],
                        "subscription": bool(line["subscription"]), "image_url": image_url})
                db.commit()
                return JSONResponse({"lines": lines, "subtotal": quote.subtotal_minor,
                    "discount": quote.discount_minor, "shipping": quote.shipping_minor,
                    "tax": quote.tax_minor,
                    "gift_card": snapshot.get("gift_card", {}).get("amount_minor", 0),
                    "total": quote.total_minor, "currency": quote.currency,
                    "recurring_subtotal_per_month": sum(line["unit_minor"] * line["quantity"]
                        for line in snapshot["lines"] if line["subscription"]),
                    "shipping_rates": [{"id": rate["id"], "name": rate["name"], "amount": rate["amount_minor"]}
                        for rate in rates], "attempt_id": attempt.id})
        except CommerceError as exc:
            return json_error(exc)

    @rt("/sites/{slug}/checkout/{attempt_id}/session", methods=["POST"])
    async def session_json(session, request, slug: str, attempt_id: str):
        try:
            await json_input(session, request)
            with SessionLocal() as db:
                site, _ = resolve(db, slug)
                attempt, _ = allowed_attempt(db, site, session, attempt_id)
                if attempt.state not in checkout.ACTIVE:
                    raise CommerceError("This checkout is already closed.")
                site_id, customer_id = site.id, attempt.customer_id
                _, _, publishable_key = credentials(site)
                # Inline card fields need a matching publishable key. Live keys are not stored
                # for browser use yet, so live mode keeps the hosted Stripe page.
                hosted_only = site._payment_mode != "sandbox" or not publishable_key
                fallback = base(site, request) + "/checkout/" + attempt.id + "/pay"
            if hosted_only:
                return JSONResponse({"fallback_url": fallback})
            return JSONResponse(checkout_payments.embedded_session(site_id, customer_id, attempt_id))
        except CommerceError as exc:
            return json_error(exc)

    def review_line(site, line, quote):
        purchase = ("One-time delivery recovery" if quote.snapshot_json.get("recovery_cycle_id") else
                    "Monthly subscription" if line["subscription"] else "One-time purchase")
        return P(f"{line['name']} × {line['quantity']} · "
                 + money(line["amount_minor"], quote.currency) + " · " + purchase)

    @rt("/sites/{slug}/checkout/{attempt_id}", methods=["GET"])
    def get(session, request, slug: str, attempt_id: str):
        try:
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                attempt, cart = allowed_attempt(db, site, session, attempt_id)
                quote = db.scalar(select(CommerceQuote).where(CommerceQuote.id == attempt.quote_id,
                    CommerceQuote.site_id == site.id, CommerceQuote.tenant_id == site.tenant_id))
                root = base(site, request)
                action = root + "/checkout/" + attempt.id
                states = {"prepared": "Review your order", "creating": "Payment setup pending", "open": "Payment pending",
                    "paid": "Payment confirmed", "expired": "Checkout closed"}
                return screen(site, request, states[attempt.state],
                    Div(H2("Monthly subscription"), P("Recurring merchandise: " + money(sum(
                        line["unit_minor"] * line["quantity"] for line in quote.snapshot_json["lines"] if line["subscription"]), quote.currency)
                        + " per month, plus shipping and destination tax. Only subscription items renew; the first-order offer does not repeat."),
                        P(subscriptions.CONSENT_TEXT), cls="e-card") if any(line["subscription"] for line in quote.snapshot_json["lines"]) and not quote.snapshot_json.get("renewal_contract_id") else None,
                    Div(H2("One-time delivery recovery"),
                        P("Pay only for this missed delivery. This payment does not resume or reactivate future deliveries, or replace your saved subscription card."),
                        A("Manage subscription", href=root + "/account/subscriptions/" + quote.snapshot_json["renewal_contract_id"]), cls="e-card") if quote.snapshot_json.get("recovery_cycle_id") else None,
                    Div(H2("Deliver to"), P(quote.snapshot_json.get("recipient_name", "")),
                        P(", ".join(str(quote.snapshot_json["destination"].get(key, "")) for key in
                            ("line1", "line2", "city", "state", "postal_code", "country") if quote.snapshot_json["destination"].get(key))), cls="e-card"),
                    Div(*[review_line(site, line, quote) for line in quote.snapshot_json["lines"]],
                        P("Merchandise: " + money(quote.subtotal_minor, quote.currency)),
                        P("Savings: " + money(quote.discount_minor, quote.currency)),
                        P("Shipping: " + money(quote.shipping_minor, quote.currency)),
                        P("Sales tax (" + quote.snapshot_json["destination"]["state"] + "): " + money(quote.tax_minor, quote.currency)),
                        P("Gift card: " + money(quote.snapshot_json["gift_card"]["amount_minor"], quote.currency))
                            if quote.snapshot_json.get("gift_card") else None,
                        H2("Total: " + money(quote.total_minor, quote.currency)),
                        cls="e-card"),
                    P("Your payment has been verified.") if attempt.state == "paid" else P("A return from Stripe is not proof of payment. Payment is confirmed only after verification."),
                    Form(csrf(session), Button("Pay with gift card" if quote.total_minor == 0 and quote.snapshot_json.get("gift_card") else "Continue to Stripe test checkout", cls="e-button"), method="post", action=action + "/pay") if attempt.state in checkout.ACTIVE else None,
                    Form(csrf(session), Button("Check payment status", cls="e-button"), method="post", action=action + "/refresh") if attempt.stripe_session_id and attempt.state in checkout.ACTIVE else None,
                    Form(csrf(session), Button("Cancel checkout", cls="e-button"), method="post", action=action + "/cancel") if attempt.state in checkout.ACTIVE else None,
                    Form(csrf(session), Button("Start a new bag", cls="e-button"), method="post", action=action + "/new") if attempt.state in ("paid", "expired") and cart.checkout_id == attempt.id else None,
                    A("Back to store", href=root + "/"))
        except CommerceError:
            return public_message(request, slug, "Checkout not found.", status_code=404, plain=False)

    @rt("/sites/{slug}/checkout/{attempt_id}/{action}", methods=["POST"])
    async def post(session, request, slug: str, attempt_id: str, action: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                checkout.lock_site(db, site)
                attempt, cart = allowed_attempt(db, site, session, attempt_id)
                site_id, customer_id, root = site.id, attempt.customer_id, base(site, request)
                if action == "new":
                    if attempt.state not in ("paid", "expired") or cart.checkout_id != attempt.id:
                        raise CommerceError("Finish or cancel this checkout first.")
                    cart.checkout_id = None
                    cart.request_key = secrets.token_hex(16)
                    cart.version += 1
                    if attempt.state == "paid":
                        cart.lines_json = []
                        cart.discount_code = ""
                    db.commit()
                    return RedirectResponse(root + "/cart", status_code=303)
                if action == "refresh":
                    checkout.reconcile(db, site, attempt.id, make_gateway(StripeGateway, site, db))
                    db.commit()
            if action == "pay":
                return RedirectResponse(checkout_payments.handoff(site_id, customer_id, attempt_id), status_code=303)
            if action == "cancel":
                checkout_payments.cancel(site_id, customer_id, attempt_id)
            if action not in ("pay", "cancel", "refresh"):
                raise CommerceError("Unknown checkout action.")
            return RedirectResponse(root + "/checkout/" + attempt_id, status_code=303)
        except CommerceError as exc:
            return public_message(request, slug, exc)

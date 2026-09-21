"""FastShop-branded US basket, quote review and hosted Stripe payment handoff."""

import secrets

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, Option, P, Select
from fasthtml.core import FtResponse
from sqlalchemy import select
from starlette.responses import JSONResponse, RedirectResponse, Response

from app import checkout_payments, commerce, customer_services, subscriptions
from app import checkout_services as checkout
from app.db import SessionLocal
from app.integrations.stripe_commerce import StripeGateway
from app.models import CheckoutAttempt, CommerceQuote, Site, SiteCart, SubscriptionCycle
from app.platform_ui import platform_page
from app.services import CommerceError, money


def register_store_checkout_routes(rt, csrf, check_csrf, error):
    def resolve(db, slug):
        site = db.scalar(select(Site).where(Site.slug == slug, Site.status.in_(["preview", "published"])))
        config = commerce.settings_for(db, site) if site else None
        if not site or not config or config.mode != "sandbox":
            raise CommerceError("Sandbox commerce is not enabled for this store.")
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
        return FtResponse(platform_page(title, P(site.name + " · Sandbox · No live payments"), *children,
            navigation=[A("Store", href=root + "/"), A("Bag", href=root + "/cart"), A("My account", href=root + "/account")], customer=True),
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
                db.commit()
                return screen(site, request, "Your bag", csrf(session), *[Div(H2(line.name), P(money(line.amount_minor, "USD")),
                    P("Monthly subscription · 10% merchandise saving" if line.subscription else "One-time purchase"),
                    Form(csrf(session), Input(type="hidden", name="version", value=cart.version),
                        Input(type="hidden", name="variant_id", value=line.variant_id),
                        Input(type="hidden", name="subscription", value="on" if line.subscription else "off"),
                        Label("Quantity (0 removes)", Input(type="number", name="quantity", value=line.quantity, min=0, max=25, required=True)),
                        Button("Update quantity", cls="e-button"), method="post", action=root + "/cart/update", cls="e-form"), cls="e-card") for line in lines],
                    P("Your bag is empty.") if not lines else P("Merchandise: " + money(sum(line.amount_minor for line in lines), "USD") + ". Shipping and destination sales tax are calculated next."),
                    Form(csrf(session), Input(type="hidden", name="version", value=cart.version),
                        Label("Discount code", Input(name="code", value=cart.discount_code, maxlength=40)),
                        P("Save your code here. Eligibility and savings are checked at checkout using your email. An empty code removes it."),
                        Button("Save discount code", cls="e-button"), action=root + "/cart/discount", method="post", cls="e-form e-card") if lines else None,
                    A("Continue to checkout", href=root + "/checkout", cls="e-button") if lines else A("Browse the shop", href=root + "/shop"),
                    A("Resume existing checkout", href=root + "/checkout/" + cart.checkout_id) if cart.checkout_id else None)
        except CommerceError as exc:
            return error(exc)

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
            return error(exc)

    async def change_cart(session, request, slug, *, add):
        form = await request.form()
        try:
            check_csrf(session, form)
            quantity = int(form.get("quantity", "1"))
            if not 0 <= quantity <= 25:
                raise CommerceError("Choose a quantity from 0 to 25.")
            if form.get("subscription", "off") not in ("on", "off"):
                raise CommerceError("Choose a valid purchase option.")
            subscription = form.get("subscription") == "on"
            with SessionLocal() as db:
                site, config = resolve(db, slug)
                checkout.lock_site(db, site)
                cart = cart_for(db, site, session)
                if cart.checkout_id:
                    attempt = checkout.owned_attempt(db, site, cart.checkout_id)
                    if attempt.state in checkout.ACTIVE:
                        raise CommerceError("Cancel or finish the existing checkout before changing this bag.")
                    cart.checkout_id = None
                    if attempt.state == "paid":
                        cart.lines_json = []
                        cart.discount_code = ""
                if not add and str(cart.version) != form.get("version"):
                    raise CommerceError("Your bag changed in another tab. Reload before updating.")
                variant_id = str(form.get("variant_id", ""))
                lines = [dict(line) for line in cart.lines_json]
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
            return error(CommerceError(str(exc)))

    @rt("/sites/{slug}/cart/add", methods=["POST"])
    async def post(session, request, slug: str):
        return await change_cart(session, request, slug, add=True)

    @rt("/sites/{slug}/cart/update", methods=["POST"])
    async def post(session, request, slug: str):
        return await change_cart(session, request, slug, add=False)

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
                recurring_lines = [line for line in commerce.price_lines(db, site, config, cart.lines_json) if line.subscription]
                def field(label, name, **kwargs):
                    return Label(label, Input(name=name, **kwargs))
                return screen(site, request, "US delivery & checkout", P("Review shipping and sales tax before going to Stripe. No payment is taken on this form."),
                    Form(csrf(session), Input(type="hidden", name="version", value=cart.version),
                        field("Recipient's full name", "full_name", required=True, maxlength=160, autocomplete="shipping name"),
                        field("Email", "email", type="email", required=True, maxlength=320, value=customer.email if customer else "", autocomplete="email"),
                        field("Street address", "line1", required=True, maxlength=200, autocomplete="shipping address-line1"),
                        field("Apartment / suite", "line2", maxlength=200, autocomplete="shipping address-line2"),
                        field("City", "city", required=True, maxlength=200, autocomplete="shipping address-level2"),
                        Label("State", Select(Option("Choose your state", value="", selected=True, disabled=True),
                            *[Option(commerce.US_STATES[state], value=state) for state in config.allowed_states_json if state in commerce.US_STATES], name="state", required=True, autocomplete="shipping address-level1")),
                        field("ZIP code", "postal_code", required=True, pattern=r"\d{5}(-\d{4})?", autocomplete="shipping postal-code"),
                        Input(type="hidden", name="country", value="US"),
                        field("First-order discount code (optional)", "code", maxlength=40, value=cart.discount_code),
                        Input(name="website", tabindex="-1", autocomplete="off", style="display:none"),
                        P("US delivery only. This does not subscribe you to marketing."),
                        Div(H2("Monthly delivery subscription"),
                            P("Recurring merchandise: " + money(sum(line.amount_minor for line in recurring_lines), "USD") + " per month, plus shipping and destination tax calculated per delivery. One-time items do not renew."),
                            Label(Input(type="checkbox", name="subscription_consent", required=True), " " + subscriptions.CONSENT_TEXT),
                            P("No payment is taken until you review the first delivery total and complete Stripe checkout.")) if recurring_lines else None,
                        Button("Review shipping & tax", cls="e-button"), method="post", action=root + "/checkout", cls="e-form e-card"))
        except CommerceError as exc:
            return error(exc)

    @rt("/sites/{slug}/checkout", methods=["POST"])
    async def post(session, request, slug: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            name = str(form.get("full_name", "")).strip()
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
                attempt = checkout.prepare(db, site, customer.id, cart.lines_json, dict(form), StripeGateway(site),
                    request_key=cart.request_key, code=form.get("code", ""), recipient_name=name,
                    subscription_consent=form.get("subscription_consent") == "on")
                cart.checkout_id = attempt.id
                db.commit()
                return RedirectResponse(base(site, request) + "/checkout/" + attempt.id, status_code=303)
        except CommerceError as exc:
            return error(exc)

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
                    Div(*[P(f"{line['name']} × {line['quantity']} · " + money(line["amount_minor"], quote.currency)
                        + (" · One-time delivery recovery" if quote.snapshot_json.get("recovery_cycle_id") else
                            " · Monthly subscription" if line["subscription"] else " · One-time purchase")) for line in quote.snapshot_json["lines"]],
                        P("Merchandise: " + money(quote.subtotal_minor, quote.currency)),
                        P("Savings: " + money(quote.discount_minor, quote.currency)),
                        P("Shipping: " + money(quote.shipping_minor, quote.currency)),
                        P("Sales tax (" + quote.snapshot_json["destination"]["state"] + "): " + money(quote.tax_minor, quote.currency)),
                        H2("Total: " + money(quote.total_minor, quote.currency)), cls="e-card"),
                    P("Your payment has been verified. You can sign in by email to view this order.") if attempt.state == "paid" else P("A return from Stripe is not proof of payment. Payment is confirmed only after verification."),
                    Form(csrf(session), Button("Continue to Stripe test checkout", cls="e-button"), method="post", action=action + "/pay") if attempt.state in checkout.ACTIVE else None,
                    Form(csrf(session), Button("Check payment status", cls="e-button"), method="post", action=action + "/refresh") if attempt.stripe_session_id and attempt.state in checkout.ACTIVE else None,
                    Form(csrf(session), Button("Cancel checkout", cls="e-button"), method="post", action=action + "/cancel") if attempt.state in checkout.ACTIVE else None,
                    Form(csrf(session), Button("Start a new bag", cls="e-button"), method="post", action=action + "/new") if attempt.state in ("paid", "expired") and cart.checkout_id == attempt.id else None,
                    A("My account", href=root + "/account"))
        except CommerceError:
            return Response("Checkout not found.", status_code=404)

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
                    checkout.reconcile(db, site, attempt.id, StripeGateway(site))
                    db.commit()
            if action == "pay":
                return RedirectResponse(checkout_payments.handoff(site_id, customer_id, attempt_id), status_code=303)
            if action == "cancel":
                checkout_payments.cancel(site_id, customer_id, attempt_id)
            if action not in ("pay", "cancel", "refresh"):
                raise CommerceError("Unknown checkout action.")
            return RedirectResponse(root + "/checkout/" + attempt_id, status_code=303)
        except CommerceError as exc:
            return error(exc)

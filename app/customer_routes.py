"""Customer accounts, double opt-in offers and owned order tracking."""

from __future__ import annotations

import secrets
from unicodedata import category
from urllib.parse import unquote

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, Li, NotStr, P, Small, Ul
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app import commerce
from app import customer_services as customers
from app.customer_accounts import (
    CustomerAccountsUnavailable,
    require_customer_accounts,
    unavailable_response,
)
from app.db import SessionLocal
from app.integrations.commerce_email import dispatch_mail
from app.models import (
    CommerceMail,
    CustomerChallenge,
    CustomerOffer,
    MarketingConsent,
    Order,
    ShopCustomer,
    Site,
    SiteOrder,
)
from app.platform_ui import platform_page
from app.services import CommerceError, money
from app.storefront_ui import commerce_page, public_message


def register_customer_routes(rt, actor, csrf, check_csrf, merchant_shell, error):
    def resolve(db, slug, *, enabled=True, require_commerce=True):
        query = select(Site).where(Site.slug == slug, Site.status != "deleted")
        if enabled:
            query = query.where(Site.status.in_(["preview", "published"]))
        site = db.scalar(query)
        if not site:
            raise CommerceError("Store not found.")
        config = commerce.settings_for(db, site)
        if enabled and require_commerce and (not config or not commerce.payments_enabled(db, site, config)):
            raise CommerceError("Customer services are not enabled for this store yet.")
        return site

    def base(site, request):
        return request.scope.get("site_base", "/sites/" + site.slug)

    def account_site(db, slug, *, accounts=True):
        site = resolve(db, slug, enabled=False)
        if not commerce.settings_for(db, site):
            raise CommerceError("Customer services are not configured for this store.")
        if accounts:
            require_customer_accounts(db, site)
        return site

    def verification_site(db, slug, challenge_id):
        site = account_site(db, slug, accounts=False)
        purpose = db.scalar(select(CustomerChallenge.purpose).where(
            CustomerChallenge.id == challenge_id,
            CustomerChallenge.site_id == site.id,
            CustomerChallenge.tenant_id == site.tenant_id,
        ))
        if purpose != "newsletter":
            require_customer_accounts(db, site)
        return site

    def screen(db, site, request, title, *children):
        root = base(site, request)
        config = commerce.settings_for(db, site)
        accounts_enabled = bool(config and config.customer_accounts_enabled)
        if (rendered := commerce_page(site, request, title, *children)) is not None:
            return rendered
        return platform_page(title, P(site.name + " · Powered by FastShop"), *children,
            navigation=[A("Back to store", href=root + "/"), *([A("My account", href=root + "/account")] if accounts_enabled else [])], customer=True)

    def account_copy(site):
        from app.account_copy import DEFAULT_ACCOUNT_COPY
        return {**DEFAULT_ACCOUNT_COPY, **(site.published_settings_json or {}).get("account_copy", {})}

    def safe_local_next(value, fallback):
        if not isinstance(value, str):
            return fallback
        decoded = value
        while True:
            if (not decoded.startswith("/") or decoded.startswith("//")
                    or "\\" in decoded or any(category(char) == "Cc" for char in decoded)):
                return fallback
            expanded = unquote(decoded)
            if expanded == decoded:
                return value
            decoded = expanded

    def login_card(site, request, session, *, verify=False, next_path="", email=""):
        root = base(site, request)
        copy = account_copy(site)
        fields = [csrf(session), Input(type="hidden", name="next", value=next_path)]
        if verify:
            fields.extend([Label(copy["email_label"], Input(name="email", type="email", required=True,
                autocomplete="email", maxlength=320, value=email)),
                Label(copy["code_label"], Input(name="code", inputmode="numeric", pattern="[0-9]{6}",
                    required=True, autocomplete="one-time-code", maxlength=6))])
            action, button = root + "/account/login/verify", copy["verify_code"]
        else:
            fields.append(Label(copy["email_label"], Input(name="email", type="email", required=True,
                autocomplete="email", maxlength=320)))
            fields.append(Input(name="website", tabindex="-1", autocomplete="off", style="display:none"))
            action, button = root + "/account/login/code", copy["send_code"]
        card = Div(H2(copy["heading"]), Form(*fields, Button(button, cls="e-button account-login-primary"), method="post",
            action=action, cls="e-form account-login-form"), cls="e-card account-login-card")
        style = "<style>.account-login-card{width:min(100%,440px);margin:clamp(2rem,10vh,6rem) auto;background:#fff;padding:clamp(1.5rem,5vw,2.5rem);border-radius:var(--store-radius,12px);box-shadow:0 8px 32px rgba(13,23,19,.08)}.account-login-form{display:grid;gap:1.1rem}.account-login-form label{display:grid;gap:.45rem}.account-login-form input:not([type=hidden]){width:100%;box-sizing:border-box}.account-login-primary{width:100%;background:var(--store-accent,#0e7d55);border-color:var(--store-accent,#0e7d55);border-radius:var(--store-radius,12px)}.account-login-card h2{margin-top:0}</style>"
        return [card, NotStr(style)]

    def private_response(response):
        from fasthtml.core import FtResponse
        return FtResponse(response, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})

    @rt("/sites/{slug}/account", methods=["GET"])
    def get(session, request, slug: str):
        try:
            with SessionLocal() as db:
                site = account_site(db, slug)
                root = base(site, request)
                customer = customers.signed_in_customer(db, site, session)
                if not customer:
                    return private_response(screen(db, site, request, "Login", *login_card(site, request, session)))
                orders = customers.orders_for(db, site, customer)
                consent = db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site.id,
                    MarketingConsent.tenant_id == site.tenant_id, MarketingConsent.customer_id == customer.id))
                offer = db.scalar(select(CustomerOffer).where(CustomerOffer.site_id == site.id,
                    CustomerOffer.tenant_id == site.tenant_id, CustomerOffer.customer_id == customer.id))
                return private_response(screen(db, site, request, "My account", P(customer.email),
                    Div(H2("Subscriptions"), A("Manage your subscriptions", href=root + "/account/subscriptions", cls="e-button"), cls="e-card"),
                    Div(H2("Your orders"), *[P(A(order.number, href=root + "/account/orders/" + link.id),
                        " · " + money(order.total_minor, order.currency) + " · " + order.payment_status) for link, order in orders],
                        P("No orders yet. Your confirmed purchases will appear here.") if not orders else None, cls="e-card"),
                    Div(H2("Email preferences"), P("Marketing: " + (consent.status if consent else "not subscribed")),
                        Form(csrf(session), Button("Unsubscribe from marketing", cls="e-button"), method="post", action=root + "/account/unsubscribe") if consent and consent.status == "subscribed" else None,
                        P("Your first-order code: " + offer.code + " · expires " + offer.expires_at.strftime("%Y-%m-%d")) if offer and not offer.redeemed_order_id else None,
                        P("Transactional order and security messages are separate from marketing."), cls="e-card"),
                    Form(csrf(session), Button("Sign out", cls="e-button"), method="post", action=root + "/account/logout")))
        except CustomerAccountsUnavailable as exc:
            return unavailable_response(exc, request)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    async def request_link(session, request, slug, purpose):
        form = await request.form()
        newsletter_source = "footer" if form.get("newsletter_source") == "footer" else "account"
        footer_signup = purpose == "newsletter" and newsletter_source == "footer"
        try:
            with SessionLocal() as db:
                if purpose == "login":
                    site = account_site(db, slug)
                else:
                    site = resolve(db, slug, require_commerce=purpose != "newsletter")
                check_csrf(session, form)
                mail = None if form.get("website") else customers.request_email_challenge(db, site,
                    form.get("email", ""), purpose, request.client.host if request.client else "unknown",
                    consent=form.get("marketing_consent") == "on", source=newsletter_source)
                message_id = mail.id if mail else None
                db.commit()
                if footer_signup:
                    response = RedirectResponse(
                        base(site, request) + "/?newsletter=success#footer", status_code=303,
                    )
                else:
                    copy = account_copy(site)
                    response = screen(db, site, request, copy["check_email"], Div(
                        P(copy["email_link_message"]), P(copy["link_expiry_message"]), cls="e-card",
                    ))
            if message_id:
                dispatch_mail(message_id)
            if isinstance(response, RedirectResponse):
                response.headers["Cache-Control"] = "private, no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
                return response
            return private_response(response)
        except CustomerAccountsUnavailable as exc:
            return unavailable_response(exc, request)
        except CommerceError as exc:
            if footer_signup:
                root = request.scope.get("site_base", "/sites/" + slug)
                return RedirectResponse(root + "/?newsletter=error#footer", status_code=303)
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/account/login", methods=["POST"])
    async def post(session, request, slug: str):
        return await request_link(session, request, slug, "login")

    @rt("/sites/{slug}/account/login/code", methods=["POST"])
    async def post(session, request, slug: str):
        form = await request.form()
        try:
            with SessionLocal() as db:
                site = account_site(db, slug)
                check_csrf(session, form)
                mail = None if form.get("website") else customers.request_login_code(db, site,
                    form.get("email", ""), request.client.host if request.client else "unknown")
                message_id = mail.id if mail else None
                email = str(form.get("email", ""))[:320]
                next_path = safe_local_next(form.get("next", ""), base(site, request) + "/account/subscriptions")
                db.commit()
                response = screen(db, site, request, "Login", *login_card(site, request, session,
                    verify=True, next_path=next_path, email=email), P(account_copy(site)["sent_message"]))
            if message_id:
                dispatch_mail(message_id)
            return private_response(response)
        except CustomerAccountsUnavailable as exc:
            return unavailable_response(exc, request)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/account/login/verify", methods=["GET"])
    def get(session, request, slug: str, next: str = "", email: str = ""):
        try:
            with SessionLocal() as db:
                site = account_site(db, slug)
                target = safe_local_next(next, base(site, request) + "/account/subscriptions")
                return private_response(screen(db, site, request, "Login", *login_card(site, request,
                    session, verify=True, next_path=target, email=email)))
        except CustomerAccountsUnavailable as exc:
            return unavailable_response(exc, request)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/account/login/verify", methods=["POST"])
    async def post(session, request, slug: str):
        form = await request.form()
        try:
            with SessionLocal() as db:
                site = account_site(db, slug)
                check_csrf(session, form)
                customer = customers.consume_login_code(db, site, form.get("email", ""), form.get("code", ""))
                if not customer:
                    db.commit()
                    return private_response(screen(db, site, request, "Login",
                        *login_card(site, request, session, verify=True,
                            next_path=safe_local_next(form.get("next", ""), base(site, request) + "/account/subscriptions"),
                            email=str(form.get("email", ""))[:320]),
                        P(account_copy(site)["invalid_code"])))
                db.commit()
                session["customer:" + site.id] = customer.id
                session["csrf_token"] = secrets.token_urlsafe(32)
                target = safe_local_next(form.get("next", ""), base(site, request) + "/account/subscriptions")
                if not target.startswith(base(site, request) + "/"):
                    target = base(site, request) + target
                return RedirectResponse(target, status_code=303)
        except CustomerAccountsUnavailable as exc:
            return unavailable_response(exc, request)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/newsletter", methods=["POST"])
    async def post(session, request, slug: str):
        return await request_link(session, request, slug, "newsletter")

    @rt("/sites/{slug}/account/verify/{challenge_id}", methods=["GET"])
    def get(session, request, slug: str, challenge_id: str):
        try:
            with SessionLocal() as db:
                site = verification_site(db, slug, challenge_id)
                return private_response(screen(db, site, request, "Confirm your email", Div(
                    P("Select Confirm to finish the request. Opening the link alone does not sign you in or subscribe you."),
                    Form(csrf(session), Label("Verification code", Input(name="token", required=True, data_email_token="", autocomplete="off", maxlength=64)),
                        Button("Confirm", cls="e-button"), method="post", cls="e-form"), cls="e-card")))
        except CustomerAccountsUnavailable as exc:
            return unavailable_response(exc, request)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/account/verify/{challenge_id}", methods=["POST"])
    async def post(session, request, slug: str, challenge_id: str):
        form = await request.form()
        try:
            with SessionLocal() as db:
                site = verification_site(db, slug, challenge_id)
                check_csrf(session, form)
                customer, purpose = customers.consume_challenge(db, site, challenge_id, form.get("token", ""))
                if purpose != "login":
                    resolve(db, slug)
                db.commit()
                if purpose == "login":
                    session["customer:" + site.id] = customer.id
                    session["csrf_token"] = secrets.token_urlsafe(32)
                    return RedirectResponse(base(site, request) + "/account", status_code=303)
                mails = list(db.scalars(select(CommerceMail.id).where(CommerceMail.site_id == site.id,
                    CommerceMail.tenant_id == site.tenant_id, CommerceMail.customer_id == customer.id, CommerceMail.status == "pending")))
                response = screen(db, site, request, "Email confirmed", Div(P("Your marketing preference has been confirmed. If eligible, your first-order code will arrive by email. You can unsubscribe at any time."),
                    A("Back to store", href=base(site, request) + "/"), cls="e-card"))
            for message_id in mails:
                dispatch_mail(message_id)
            return private_response(response)
        except CustomerAccountsUnavailable as exc:
            return unavailable_response(exc, request)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/account/logout", methods=["POST"])
    async def post(session, request, slug: str):
        try:
            form = await request.form()
            with SessionLocal() as db:
                site = account_site(db, slug)
                check_csrf(session, form)
                session.pop("customer:" + site.id, None)
                return RedirectResponse(base(site, request) + "/account", status_code=303)
        except CustomerAccountsUnavailable as exc:
            return unavailable_response(exc, request)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/account/unsubscribe", methods=["POST"])
    async def post(session, request, slug: str):
        try:
            check_csrf(session, await request.form())
            with SessionLocal() as db:
                site = account_site(db, slug, accounts=False)
                customer = customers.signed_in_customer(db, site, session)
                if not customer:
                    raise CommerceError("Sign in to update email preferences.")
                customers.unsubscribe(db, site, customer)
                db.commit()
                return RedirectResponse(base(site, request) + "/account", status_code=303)
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/unsubscribe/{customer_id}", methods=["GET"])
    def get(session, request, slug: str, customer_id: str):
        try:
            with SessionLocal() as db:
                site = resolve(db, slug, enabled=False)
                return private_response(screen(db, site, request, "Unsubscribe", Div(P("Confirm below to stop marketing emails. Order and account-security messages are unaffected."),
                    Form(csrf(session), Label("Confirmation code", Input(name="token", required=True, data_email_token="", maxlength=64, autocomplete="off")),
                        Button("Unsubscribe", cls="e-button"), method="post", cls="e-form"), cls="e-card")))
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/unsubscribe/{customer_id}", methods=["POST"])
    async def post(session, request, slug: str, customer_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site = resolve(db, slug, enabled=False)
                customer = db.scalar(select(ShopCustomer).where(ShopCustomer.id == customer_id,
                    ShopCustomer.site_id == site.id, ShopCustomer.tenant_id == site.tenant_id))
                if not customer or not secrets.compare_digest(customers.unsubscribe_token(site, customer), str(form.get("token", ""))):
                    raise CommerceError("Invalid unsubscribe link.")
                customers.unsubscribe(db, site, customer)
                db.commit()
                return private_response(screen(db, site, request, "Unsubscribed", P("You will no longer receive marketing emails from this store.")))
        except CommerceError as exc:
            return public_message(request, slug, exc)

    @rt("/sites/{slug}/account/orders/{site_order_id}", methods=["GET"])
    def get(session, request, slug: str, site_order_id: str):
        try:
            with SessionLocal() as db:
                site = account_site(db, slug)
                customer = customers.signed_in_customer(db, site, session)
                if not customer:
                    return RedirectResponse(base(site, request) + "/account", status_code=303)
                row = db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
                    SiteOrder.id == site_order_id, SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
                    SiteOrder.customer_id == customer.id, Order.tenant_id == site.tenant_id)).first()
                if not row:
                    return public_message(request, slug, "Order not found", status_code=404, plain=False)
                link, order = row
                events = customers.tracking_for(db, site, link.id)
                copy = account_copy(site)
                return private_response(screen(db, site, request, order.number,
                    Div(H2(copy["order_summary"]), P(copy["payment_label"] + ": " + order.payment_status),
                        P(copy["sales_tax_label"] + ": " + money(order.tax_minor, order.currency)),
                        P(copy["total_label"] + ": " + money(order.total_minor, order.currency)),
                        *[P(f"{line.product_name} · {line.variant_name} × {line.quantity}") for line in order.lines], cls="e-card"),
                    Div(H2(copy["delivery_tracking"]), P(copy["no_shipment_updates"]) if not events else None,
                        Ul(*[Li(P(event.status.replace("_", " ").title()), P(event.carrier + " " + event.tracking_number),
                            P(event.note), A(copy["track_carrier"], href=event.tracking_url, target="_blank", rel="noopener noreferrer") if event.tracking_url else None,
                            Small(str(event.created_at))) for event in events]), cls="e-card")))
        except CustomerAccountsUnavailable as exc:
            return unavailable_response(exc, request)
        except CommerceError as exc:
            return public_message(request, slug, exc)

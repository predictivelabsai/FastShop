"""Customer accounts, double opt-in offers and owned order tracking."""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, Li, Option, P, Select, Small, Ul
from sqlalchemy import select
from starlette.responses import RedirectResponse, Response

from app import commerce, content
from app import customer_services as customers
from app.db import SessionLocal
from app.integrations.commerce_email import dispatch_mail
from app.models import (
    CommerceMail,
    CustomerOffer,
    MarketingConsent,
    Order,
    ShipmentEvent,
    ShopCustomer,
    Site,
    SiteOrder,
)
from app.platform_ui import platform_page
from app.services import CommerceError, money

SHIPMENT_STATES = {"label_created", "in_transit", "out_for_delivery", "delivered", "exception", "returned"}
TRACKING_HOSTS = {"dhl.com", "fedex.com", "ups.com", "usps.com", "omniva.ee", "omniva.eu"}


def register_customer_routes(rt, actor, csrf, check_csrf, merchant_shell, error):
    def resolve(db, slug, *, enabled=True):
        query = select(Site).where(Site.slug == slug)
        if enabled:
            query = query.where(Site.status.in_(["preview", "published"]))
        site = db.scalar(query)
        if not site:
            raise CommerceError("Store not found.")
        config = commerce.settings_for(db, site)
        if enabled and (not config or config.mode != "sandbox"):
            raise CommerceError("Customer services are not enabled for this store yet.")
        return site

    def base(site, request):
        return request.scope.get("site_base", "/sites/" + site.slug)

    def account_site(db, slug):
        site = resolve(db, slug, enabled=False)
        if not commerce.settings_for(db, site):
            raise CommerceError("Customer services are not configured for this store.")
        return site

    def screen(site, request, title, *children):
        root = base(site, request)
        return platform_page(title, P(site.name + " · Powered by FastShop"), *children,
            navigation=[A("Back to store", href=root + "/"), A("My account", href=root + "/account")], customer=True)

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
                    return private_response(screen(site, request, "My account", Div(H2("Sign in with an email link"),
                        P("No password needed. We’ll email a single-use sign-in link. Signing in does not subscribe you to marketing."),
                        Form(csrf(session), Label("Email address", Input(name="email", type="email", required=True, autocomplete="email", maxlength=320)),
                            Input(name="website", tabindex="-1", autocomplete="off", style="display:none"),
                            Button("Email me a sign-in link", cls="e-button"), method="post", action=root + "/account/login", cls="e-form"), cls="e-card")))
                orders = customers.orders_for(db, site, customer)
                consent = db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site.id,
                    MarketingConsent.tenant_id == site.tenant_id, MarketingConsent.customer_id == customer.id))
                offer = db.scalar(select(CustomerOffer).where(CustomerOffer.site_id == site.id,
                    CustomerOffer.tenant_id == site.tenant_id, CustomerOffer.customer_id == customer.id))
                return private_response(screen(site, request, "My account", P(customer.email),
                    Div(H2("Subscriptions"), A("Manage your subscriptions", href=root + "/account/subscriptions", cls="e-button"), cls="e-card"),
                    Div(H2("Your orders"), *[P(A(order.number, href=root + "/account/orders/" + link.id),
                        " · " + money(order.total_minor, order.currency) + " · " + order.payment_status) for link, order in orders],
                        P("No orders yet. Your confirmed purchases will appear here.") if not orders else None, cls="e-card"),
                    Div(H2("Email preferences"), P("Marketing: " + (consent.status if consent else "not subscribed")),
                        Form(csrf(session), Button("Unsubscribe from marketing", cls="e-button"), method="post", action=root + "/account/unsubscribe") if consent and consent.status == "subscribed" else None,
                        P("Your first-order code: " + offer.code + " · expires " + offer.expires_at.strftime("%Y-%m-%d")) if offer and not offer.redeemed_order_id else None,
                        P("Transactional order and security messages are separate from marketing."), cls="e-card"),
                    Form(csrf(session), Button("Sign out", cls="e-button"), method="post", action=root + "/account/logout")))
        except CommerceError as exc:
            return error(exc)

    async def request_link(session, request, slug, purpose):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = account_site(db, slug) if purpose == "login" else resolve(db, slug)
                mail = None if form.get("website") else customers.request_email_challenge(db, site,
                    form.get("email", ""), purpose, request.client.host if request.client else "unknown",
                    consent=form.get("marketing_consent") == "on")
                message_id = mail.id if mail else None
                db.commit()
                response = screen(site, request, "Check your email", Div(P("If your request can be processed, an email link will arrive shortly. Check your spam folder too. Repeated requests are limited."),
                    P("The link expires and can only be used once. No order has been placed."), cls="e-card"))
            if message_id:
                dispatch_mail(message_id)
            return private_response(response)
        except CommerceError as exc:
            return error(exc)

    @rt("/sites/{slug}/account/login", methods=["POST"])
    async def post(session, request, slug: str):
        return await request_link(session, request, slug, "login")

    @rt("/sites/{slug}/newsletter", methods=["POST"])
    async def post(session, request, slug: str):
        return await request_link(session, request, slug, "newsletter")

    @rt("/sites/{slug}/account/verify/{challenge_id}", methods=["GET"])
    def get(session, request, slug: str, challenge_id: str):
        try:
            with SessionLocal() as db:
                site = account_site(db, slug)
                return private_response(screen(site, request, "Confirm your email", Div(
                    P("Select Confirm to finish the request. Opening the link alone does not sign you in or subscribe you."),
                    Form(csrf(session), Label("Verification code", Input(name="token", required=True, data_email_token="", autocomplete="off", maxlength=64)),
                        Button("Confirm", cls="e-button"), method="post", cls="e-form"), cls="e-card")))
        except CommerceError as exc:
            return error(exc)

    @rt("/sites/{slug}/account/verify/{challenge_id}", methods=["POST"])
    async def post(session, request, slug: str, challenge_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = account_site(db, slug)
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
                response = screen(site, request, "Email confirmed", Div(P("Your marketing preference has been confirmed. If eligible, your first-order code will arrive by email. You can unsubscribe at any time."),
                    A("Back to store", href=base(site, request) + "/"), cls="e-card"))
            for message_id in mails:
                dispatch_mail(message_id)
            return private_response(response)
        except CommerceError as exc:
            return error(exc)

    @rt("/sites/{slug}/account/logout", methods=["POST"])
    async def post(session, request, slug: str):
        try:
            check_csrf(session, await request.form())
            with SessionLocal() as db:
                site = account_site(db, slug)
                session.pop("customer:" + site.id, None)
                return RedirectResponse(base(site, request) + "/account", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/sites/{slug}/account/unsubscribe", methods=["POST"])
    async def post(session, request, slug: str):
        try:
            check_csrf(session, await request.form())
            with SessionLocal() as db:
                site = account_site(db, slug)
                customer = customers.signed_in_customer(db, site, session)
                if not customer:
                    raise CommerceError("Sign in to update email preferences.")
                customers.unsubscribe(db, site, customer)
                db.commit()
                return RedirectResponse(base(site, request) + "/account", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/sites/{slug}/unsubscribe/{customer_id}", methods=["GET"])
    def get(session, request, slug: str, customer_id: str):
        try:
            with SessionLocal() as db:
                site = resolve(db, slug, enabled=False)
                return private_response(screen(site, request, "Unsubscribe", Div(P("Confirm below to stop marketing emails. Order and account-security messages are unaffected."),
                    Form(csrf(session), Label("Confirmation code", Input(name="token", required=True, data_email_token="", maxlength=64, autocomplete="off")),
                        Button("Unsubscribe", cls="e-button"), method="post", cls="e-form"), cls="e-card")))
        except CommerceError as exc:
            return error(exc)

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
                return private_response(screen(site, request, "Unsubscribed", P("You will no longer receive marketing emails from this store.")))
        except CommerceError as exc:
            return error(exc)

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
                    return Response("Order not found", status_code=404)
                link, order = row
                events = customers.tracking_for(db, site, link.id)
                return private_response(screen(site, request, order.number,
                    Div(H2("Order summary"), P("Payment: " + order.payment_status),
                        P("Sales tax: " + money(order.tax_minor, order.currency)), P("Total: " + money(order.total_minor, order.currency)),
                        *[P(f"{line.product_name} · {line.variant_name} × {line.quantity}") for line in order.lines], cls="e-card"),
                    Div(H2("Delivery tracking"), P("No shipment updates yet.") if not events else None,
                        Ul(*[Li(P(event.status.replace("_", " ").title()), P(event.carrier + " " + event.tracking_number),
                            P(event.note), A("Track with carrier ↗", href=event.tracking_url, target="_blank", rel="noopener noreferrer") if event.tracking_url else None,
                            Small(str(event.created_at))) for event in events]), cls="e-card")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/shipments/{site_order_id}", methods=["POST"])
    async def post(session, request, site_id: str, site_order_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                link = db.scalar(select(SiteOrder).where(SiteOrder.id == site_order_id,
                    SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id))
                if not link or form.get("status") not in SHIPMENT_STATES:
                    raise CommerceError("Choose an owned order and a valid shipment status.")
                tracking_url = content.safe_url(str(form.get("tracking_url", "")))
                host = urlsplit(tracking_url).hostname or ""
                if tracking_url and not any(host == allowed or host.endswith("." + allowed) for allowed in TRACKING_HOSTS):
                    raise CommerceError("Use an HTTPS tracking link from DHL, FedEx, UPS, USPS or Omniva.")
                shipment = ShipmentEvent(tenant_id=site.tenant_id, site_id=site.id, site_order_id=link.id,
                    author_id=user_id, carrier=str(form.get("carrier", ""))[:100], tracking_number=str(form.get("tracking_number", ""))[:100],
                    tracking_url=tracking_url[:500], status=form["status"], note=str(form.get("note", ""))[:500])
                db.add(shipment)
                db.flush()
                customers.queue_order_mail(db, site, link, shipment=shipment)
                db.commit()
                return RedirectResponse(f"/admin/sites/{site.id}/customers", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/customers", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                rows = list(db.scalars(select(ShopCustomer).where(ShopCustomer.site_id == site.id,
                    ShopCustomer.tenant_id == site.tenant_id).order_by(ShopCustomer.created_at.desc()).limit(100)))
                messages = list(db.scalars(select(CommerceMail).where(CommerceMail.site_id == site.id,
                    CommerceMail.tenant_id == site.tenant_id).order_by(CommerceMail.created_at.desc()).limit(50)))
                orders = list(db.execute(select(SiteOrder, Order).join(Order, Order.id == SiteOrder.order_id).where(
                    SiteOrder.site_id == site.id, SiteOrder.tenant_id == site.tenant_id,
                    Order.tenant_id == site.tenant_id).order_by(Order.created_at.desc()).limit(50)))
                return merchant_shell("Customers & email", A("← Site", href=f"/admin/sites/{site.id}"),
                    Div(H2("Customers"), *[P(row.email + (" · verified" if row.verified_at else " · verification pending")) for row in rows], cls="e-card"),
                    Div(H2("Email queue"), *[P(row.kind + " · " + row.status + f" · attempts {row.attempts}") for row in messages], cls="e-card"),
                    Div(H2("Order tracking"), P("Enter verified carrier updates. These are merchant-entered events, not an automatic carrier feed."),
                        *[Div(H2(order.number), P(order.email + " · " + order.payment_status),
                            Form(csrf(session), Label("Shipment status", Select(*[Option(value.replace("_", " ").title(), value=value) for value in sorted(SHIPMENT_STATES)], name="status")),
                                Label("Carrier", Input(name="carrier", maxlength=100)), Label("Tracking number", Input(name="tracking_number", maxlength=100)),
                                Label("Carrier tracking URL", Input(name="tracking_url", type="url", maxlength=500)),
                                Label("Customer-visible note", Input(name="note", maxlength=500)), Button("Add tracking update", cls="e-button"),
                                action=f"/admin/sites/{site.id}/shipments/{link.id}", method="post", cls="e-form"), cls="e-card") for link, order in orders], cls="e-card"))
        except CommerceError as exc:
            return error(exc)

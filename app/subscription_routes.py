"""Customer-owned subscription controls with shared FastShop presentation."""

import secrets

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, Option, P, Select
from fasthtml.core import FtResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response

from app import (
    commerce,
    customer_services,
    subscription_payments,
    subscription_recovery,
    subscriptions,
)
from app.db import SessionLocal
from app.models import (
    ProductVariant,
    Site,
    SubscriptionContract,
    SubscriptionCycle,
    SubscriptionPaymentSetup,
)
from app.platform_ui import platform_page
from app.services import CommerceError, money


def register_subscription_routes(rt, csrf, check_csrf):
    def identity(db, session, slug):
        site = db.scalar(select(Site).where(Site.slug == slug))
        customer = customer_services.signed_in_customer(db, site, session) if site else None
        if not site or not customer:
            raise CommerceError("Sign in to manage your subscriptions.")
        return site, customer

    def base(site, request):
        return request.scope.get("site_base", "/sites/" + site.slug)

    def screen(site, request, title, *children):
        root = base(site, request)
        return FtResponse(platform_page(title, P(site.name + " · Powered by FastShop"), *children,
            navigation=[A("Store", href=root + "/"), A("My account", href=root + "/account"),
                A("Subscriptions", href=root + "/account/subscriptions")], customer=True),
            headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})

    @rt("/sites/{slug}/account/subscriptions", methods=["GET"])
    def get(session, request, slug: str):
        try:
            with SessionLocal() as db:
                site, customer = identity(db, session, slug)
                rows = db.scalars(select(SubscriptionContract).where(SubscriptionContract.site_id == site.id,
                    SubscriptionContract.tenant_id == site.tenant_id, SubscriptionContract.customer_id == customer.id)
                    .order_by(SubscriptionContract.created_at.desc())).all()
                return screen(site, request, "Your subscriptions", *[Div(H2(A("Tablet delivery", href=base(site, request) + "/account/subscriptions/" + row.id)),
                    P("Status: " + row.state), P("Next scheduled date: " + row.next_due_at.strftime("%Y-%m-%d") + " UTC") if row.state == "active" else None,
                    P("Merchandise per delivery: " + money(sum(line["unit_minor"] * line["quantity"] for line in row.lines_json), "USD") + " + applicable shipping and tax"), cls="e-card") for row in rows],
                    P("No subscriptions yet.") if not rows else None)
        except CommerceError:
            return RedirectResponse("/sites/" + slug + "/account", status_code=303)

    @rt("/sites/{slug}/account/subscriptions/{contract_id}", methods=["GET"])
    def get(session, request, slug: str, contract_id: str):
        try:
            with SessionLocal() as db:
                site, customer = identity(db, session, slug)
                contract = subscriptions.owned(db, site, customer.id, contract_id)
                root = base(site, request) + "/account/subscriptions/" + contract.id
                def form(action, label, *fields):
                    return Form(csrf(session), Input(type="hidden", name="version", value=contract.version),
                        Input(type="hidden", name="request_key", value=secrets.token_hex(16)), *fields,
                        Button(label, cls="e-button"), method="post", action=root + "/" + action, cls="e-form")
                cycles = db.scalars(select(SubscriptionCycle).where(SubscriptionCycle.contract_id == contract.id,
                    SubscriptionCycle.site_id == site.id, SubscriptionCycle.tenant_id == site.tenant_id)
                    .order_by(SubscriptionCycle.due_at.desc()).limit(12)).all()
                setups = db.scalars(select(SubscriptionPaymentSetup).where(SubscriptionPaymentSetup.contract_id == contract.id,
                    SubscriptionPaymentSetup.site_id == site.id, SubscriptionPaymentSetup.tenant_id == site.tenant_id,
                    SubscriptionPaymentSetup.customer_id == customer.id, SubscriptionPaymentSetup.state == "pending")).all()
                flavours = []
                for line in contract.lines_json:
                    variants = db.scalars(select(ProductVariant).where(ProductVariant.product_id == line["product_id"],
                        ProductVariant.tenant_id == site.tenant_id, ProductVariant.is_active.is_(True))).all()
                    flavours.append(form("flavour", "Change flavour", Input(type="hidden", name="from_variant_id", value=line["variant_id"]),
                        Label(line["name"] + " · " + str(line["quantity"]) + " per delivery",
                            Select(*[Option(variant.name, value=variant.id, selected=variant.id == line["variant_id"]) for variant in variants], name="variant_id"))))
                destination = contract.destination_json
                recovery = subscription_recovery.pending(db, site, contract.id)
                address_fields = [Label(label, Input(name=key, value=destination.get(key, ""), required=key != "line2", maxlength=200)) for key, label in
                    [("line1", "Street address"), ("line2", "Apartment / suite"), ("city", "City"), ("postal_code", "ZIP code")]]
                return screen(site, request, "Manage subscription", Div(P("Status: " + contract.state),
                    P("Next scheduled date: " + contract.next_due_at.strftime("%Y-%m-%d") + " UTC") if contract.state == "active" else P("No new deliveries are scheduled while this subscription is " + contract.state + "."),
                    P("Delivery frequency: every " + str(contract.interval_months) + " month(s)"),
                    P("Merchandise: " + money(sum(line["unit_minor"] * line["quantity"] for line in contract.lines_json), "USD") + "; shipping and destination tax are calculated per delivery."),
                    P("Changes affect future deliveries. A payment already processing is not cancelled or refunded by these controls."), cls="e-card"),
                    Div(H2("Schedule"),
                        form("skip", "Skip next delivery") if contract.state == "active" else None,
                        form("pause", "Pause subscription") if contract.state == "active" else None,
                        form("resume", "Resume future deliveries") if contract.state == "paused" else None,
                        P("Resuming skips missed periods; it does not charge catch-up deliveries."),
                        form("frequency", "Change frequency", Label("Months between deliveries", Input(name="months", type="number", min=1, max=12, value=contract.interval_months, required=True))),
                        P("Changing frequency keeps the next scheduled date and changes the interval after it."), cls="e-card") if contract.state != "cancelled" else None,
                    Div(H2("Flavours"), *flavours, cls="e-card") if contract.state != "cancelled" else None,
                    Div(H2("Delivery address"), form("address", "Update delivery address",
                        Label("Recipient's full name", Input(name="recipient_name", value=contract.recipient_name, required=True, maxlength=160)),
                        *address_fields, Label("State", Select(*[Option(label, value=code, selected=destination.get("state") == code) for code, label in commerce.US_STATES.items()], name="state")),
                        Input(type="hidden", name="country", value="US")), cls="e-card") if contract.state != "cancelled" else None,
                    Div(H2("Payment"), P("Stripe securely stores your card. FastShop does not store card numbers. Updating your card does not automatically resume a paused subscription."),
                        form("payment", "Update payment method"),
                        *[P(A("Verify card update", href=root + "/payment/" + setup.id)) for setup in setups], cls="e-card") if contract.state != "cancelled" else None,
                    Div(H2("Recent deliveries"), *[P(cycle.due_at.strftime("%Y-%m-%d") + " · " + cycle.state) for cycle in cycles],
                        P("A failed delivery was not fulfilled. Retrying opens a fresh one-time checkout for review; it does not resume future deliveries or change your saved card.") if any(cycle.state == "failed" for cycle in cycles) else None,
                        P(A("Continue delivery recovery", href=base(site, request) + "/checkout/" + recovery.id)) if recovery else None,
                        *[form("recover", "Review missed delivery", Input(type="hidden", name="cycle_id", value=cycle.id)) for cycle in cycles
                            if not recovery and contract.state == "paused" and cycle.state == "failed" and cycle.due_at == contract.next_due_at],
                        P("No renewal deliveries yet.") if not cycles else None, cls="e-card"),
                    Div(H2("Cancel"), form("cancel", "Cancel subscription", Label(Input(type="checkbox", name="confirm_cancel", required=True), " Stop all future renewals")), cls="e-card") if contract.state != "cancelled" else None)
        except CommerceError:
            return Response("Subscription not found.", status_code=404)

    @rt("/sites/{slug}/account/subscriptions/{contract_id}/{action}", methods=["POST"])
    async def post(session, request, slug: str, contract_id: str, action: str):
        data = await request.form()
        try:
            check_csrf(session, data)
            with SessionLocal() as db:
                site, customer = identity(db, session, slug)
                root = base(site, request) + "/account/subscriptions/" + contract_id
                if action in ("payment", "recover"):
                    site_id, customer_id = site.id, customer.id
                else:
                    values = {}
                    if action == "frequency":
                        values = {"months": int(data.get("months", "0"))}
                    elif action == "address":
                        values = {key: str(data.get(key, "")) for key in ("line1", "line2", "city", "state", "postal_code", "country", "recipient_name")}
                    elif action == "flavour":
                        values = {key: str(data.get(key, "")) for key in ("from_variant_id", "variant_id")}
                    elif action == "cancel" and data.get("confirm_cancel") != "on":
                        raise CommerceError("Confirm that you want to cancel future renewals.")
                    subscriptions.change(db, site, customer.id, contract_id, action, values,
                        version=int(data.get("version", "0")), request_key=str(data.get("request_key", "")))
                    db.commit()
            if action == "payment":
                url = await run_in_threadpool(subscription_payments.start, site_id, customer_id, contract_id, str(data.get("request_key", "")))
                return RedirectResponse(url, status_code=303)
            if action == "recover":
                attempt_id = await run_in_threadpool(subscription_recovery.start, site_id, customer_id, contract_id,
                    str(data.get("cycle_id", "")), int(data.get("version", "0")), str(data.get("request_key", "")))
                return RedirectResponse(request.scope.get("site_base", "/sites/" + slug) + "/checkout/" + attempt_id, status_code=303)
            return RedirectResponse(root, status_code=303)
        except (CommerceError, ValueError) as exc:
            return FtResponse(platform_page("Subscription needs attention", P(str(exc)),
                A("Back to subscription", href=request.scope.get("site_base", "/sites/" + slug) + "/account/subscriptions/" + contract_id), navigation=[], customer=True),
                status_code=400, headers={"Cache-Control": "private, no-store"})

    @rt("/sites/{slug}/account/subscriptions/{contract_id}/payment/{setup_id}", methods=["GET", "POST"])
    async def payment(session, request, slug: str, contract_id: str, setup_id: str):
        try:
            if request.method == "POST":
                check_csrf(session, await request.form())
            with SessionLocal() as db:
                site, customer = identity(db, session, slug)
                subscriptions.owned(db, site, customer.id, contract_id)
                setup = db.scalar(select(SubscriptionPaymentSetup).where(SubscriptionPaymentSetup.id == setup_id,
                    SubscriptionPaymentSetup.site_id == site.id, SubscriptionPaymentSetup.tenant_id == site.tenant_id,
                    SubscriptionPaymentSetup.customer_id == customer.id, SubscriptionPaymentSetup.contract_id == contract_id))
                if not setup:
                    raise CommerceError("Card-update request not found.")
                state, site_id, customer_id = setup.state, site.id, customer.id
            if request.method == "POST":
                state = await run_in_threadpool(subscription_payments.finish, site_id, customer_id, contract_id, setup_id)
            root = base(site, request) + "/account/subscriptions/" + contract_id
            return screen(site, request, "Payment method update", P("Status: " + state),
                P("No payment is taken here. A browser return alone does not update your saved card."),
                Form(csrf(session), Button("Verify card update", cls="e-button"), method="post", action=root + "/payment/" + setup_id) if state == "pending" else None,
                A("Back to subscription", href=root))
        except CommerceError as exc:
            return Response(str(exc), status_code=400)

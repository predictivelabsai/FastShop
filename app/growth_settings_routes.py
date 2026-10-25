"""Merchant settings forms and invitation acceptance, using the shared shell."""

import re
from datetime import UTC, datetime
from urllib.parse import urlencode

from fasthtml.common import (
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Option,
    P,
    Pre,
    Select,
    Summary,
    Textarea,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app import commerce, content
from app import growth_settings as service
from app.admin_ui import badge, card, index_table
from app.db import SessionLocal
from app.growth_settings_models import NotificationTemplate, ShippingRate, ShippingZone, TaxRate
from app.models import CommerceMail, Membership, SitePage, User, new_id
from app.services import CommerceError, money


def register_growth_settings_routes(rt, actor, csrf, check_csrf, shell, error):
    def back(site, section, message="Settings saved"):
        return RedirectResponse(f"/admin/sites/{site.id}/settings/{section}?" +
                                urlencode({"notice": message}), status_code=303)

    def form(session, site, section, *fields, label="Save", extra=""):
        return Form(csrf(session), *fields, Button(label, cls="e-button"), method="post",
                    action=f"/admin/sites/{site.id}/settings/{section}" + extra,
                    cls="e-form", data_dirty_guard=True)

    def field(label, name, value="", **kwargs):
        return Label(label, Input(name=name, value=value, **kwargs))

    def check(label, name, value=False):
        return Label(Input(type="checkbox", name=name, checked=value), " " + label)

    def number(value, *, optional=False):
        if optional and str(value).strip() == "":
            return None
        try:
            result = int(value)
        except (ValueError, TypeError) as exc:
            raise CommerceError("Enter a whole non-negative amount.") from exc
        if result < 0:
            raise CommerceError("Enter a whole non-negative amount.")
        return result

    def options(items, selected=None):
        return [Option(label, value=value, selected=value == selected) for value, label in items]

    def rate_fields(rate):
        return [Input(type="hidden", name="rate_id", value=rate.id),
            Input(type="hidden", name="zone_id", value=rate.zone_id),
            field("Rate name", "name", rate.name, required=True),
            Label("Rate type", Select(*options([("flat", "Flat rate"), ("price_based", "Based on basket price"), ("weight_based", "Based on weight")], rate.kind), name="kind")),
            field("Price (minor units)", "amount_minor", rate.amount_minor, type="number", min="0"),
            field("Minimum", "minimum", rate.minimum, type="number", min="0"),
            field("Maximum (optional)", "maximum", rate.maximum if rate.maximum is not None else "", type="number", min="0"),
            field("Free shipping threshold (optional)", "free_threshold", rate.free_shipping_threshold_minor if rate.free_shipping_threshold_minor is not None else "", type="number", min="0"),
            check("Local pickup", "local_pickup", rate.local_pickup)]

    @rt("/admin/sites/{site_id}/settings/users", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id)
                service.require_admin(db, site, user_id)
                rows = db.execute(select(Membership, User).join(User).where(
                    Membership.tenant_id == site.tenant_id,
                    Membership.role.in_(service.STAFF_ROLES)).order_by(User.email))
                return shell("Users and permissions", card(index_table(
                    ["Staff member", "Role", "Access"], [[Div(user.name, P(user.email)),
                    form(session, site, "users", Input(type="hidden", name="membership_id", value=member.id),
                        Select(*options([(role, role.title()) for role in sorted(service.STAFF_ROLES)], member.role), name="role"),
                        label="Change role", extra="/role"),
                    form(session, site, "users", Input(type="hidden", name="membership_id", value=member.id),
                        label="Remove staff", extra="/remove")] for member, user in rows])),
                    card(form(session, site, "users", field("Email address", "email", type="email", required=True),
                        Label("Role", Select(*options([(role, role.title()) for role in sorted(service.STAFF_ROLES)], "editor"), name="role")),
                        label="Send invitation", extra="/invite"), title="Invite staff"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/users/{action}", methods=["POST"])
    async def post(session, request, site_id: str, action: str):
        try:
            values = await request.form()
            check_csrf(session, values)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id)
                if action == "invite":
                    service.invite_staff(db, site, user_id, values.get("email", ""), values.get("role"))
                elif action in {"role", "remove"}:
                    service.change_staff(db, site, user_id, values.get("membership_id"),
                        role=values.get("role"), remove=action == "remove")
                else:
                    raise CommerceError("Unknown staff action.")
                db.commit()
                return back(site, "users", "Invitation queued" if action == "invite" else "Staff updated")
        except CommerceError as exc:
            return error(exc)

    @rt("/staff/accept", methods=["GET"])
    def get(session, token: str = ""):
        try:
            with SessionLocal() as db:
                invite = service.pending_invite(db, token)
                existing = db.scalar(select(User.id).where(User.email == invite.email))
                return shell("Accept invitation", card(P("You have been invited as " + invite.role + "."),
                    P(invite.email), P("Sign in with this email first if you already have an account.") if existing else None,
                    Form(csrf(session), Input(type="hidden", name="token", value=token),
                        None if existing else field("Your name", "name", required=True),
                        None if existing else field("Password (at least 24 characters)", "password", type="password", minlength="24", required=True),
                        None if existing else field("Confirm password", "confirmation", type="password", required=True),
                        Button("Accept invitation", cls="e-button"), method="post", action="/staff/accept", cls="e-form")))
        except CommerceError as exc:
            return error(exc)

    @rt("/staff/accept", methods=["POST"])
    async def post(session, request):
        try:
            values = await request.form()
            check_csrf(session, values)
            with SessionLocal() as db:
                user, invite = service.accept_invite(db, values.get("token", ""),
                    user_id=session.get("user_id"), name=values.get("name", ""),
                    password=values.get("password", ""), confirmation=values.get("confirmation", ""))
                db.commit()
                session["user_id"] = user.id
                return RedirectResponse(f"/admin/sites/{invite.site_id}", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/payments", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                config = commerce.settings_for(db, site)
                mode = config.mode if config else "disabled"
                return shell("Payments", card(P("Stripe"), badge(mode.title(), "success" if mode != "disabled" else "neutral"),
                    P("Provider credentials are managed securely outside these forms."),
                    A("Review provider configuration", href=f"/admin/sites/{site.id}/commerce", cls="e-button")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/checkout", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                config = commerce.settings_for(db, site)
                return shell("Checkout", card(form(session, site, "checkout",
                    check("Enable customer accounts", "customer_accounts_enabled", config.customer_accounts_enabled if config else False),
                    field("Default shipping (minor units)", "shipping_minor", config.shipping_minor if config and config.shipping_minor is not None else "", type="number", min="0"),
                    field("Free shipping threshold (minor units)", "threshold", config.free_shipping_threshold_minor if config else 7500, type="number", min="0")), title="Checkout preferences"),
                    card(P("Delivery zones override default shipping when configured. Stripe tax continues to use reviewed provider settings."),
                        A("Advanced commerce settings", href=f"/admin/sites/{site.id}/commerce", cls="e-button")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/checkout", methods=["POST"])
    async def post(session, request, site_id: str):
        try:
            values = await request.form()
            check_csrf(session, values)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                config = commerce.settings_for(db, site, create=True)
                config.customer_accounts_enabled = values.get("customer_accounts_enabled") == "on"
                config.shipping_minor = number(values.get("shipping_minor"), optional=True)
                config.free_shipping_threshold_minor = number(values.get("threshold"))
                config.version += 1
                db.commit()
                return back(site, "checkout")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/shipping", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                zones = list(db.scalars(select(ShippingZone).where(ShippingZone.site_id == site.id,
                    ShippingZone.tenant_id == site.tenant_id).order_by(ShippingZone.name)))
                rates = list(db.scalars(select(ShippingRate).where(ShippingRate.site_id == site.id,
                    ShippingRate.tenant_id == site.tenant_id)))
                return shell("Shipping and delivery", *[card(
                    P(", ".join(zone.countries_json) + (" · " + ", ".join(zone.states_json) if zone.states_json else "")),
                    Details(Summary("Edit zone"), form(session, site, "shipping",
                        Input(type="hidden", name="zone_id", value=zone.id),
                        field("Zone name", "name", zone.name, required=True),
                        field("Country codes", "countries", ", ".join(zone.countries_json), required=True),
                        field("State or region codes (optional)", "states", ", ".join(zone.states_json)),
                        check("Active", "is_active", zone.is_active), extra="/zones")),
                    index_table(["Rate", "Condition", "Price", "Manage"], [[rate.name,
                        "Local pickup" if rate.local_pickup else rate.kind.replace("_", " ").title(),
                        money(rate.amount_minor, "USD"), Details(Summary("Edit rate"),
                            form(session, site, "shipping", *rate_fields(rate), extra="/rates"),
                            form(session, site, "shipping", Input(type="hidden", name="rate_id", value=rate.id),
                                 Input(type="hidden", name="action", value="delete"), label="Delete rate", extra="/rates"))]
                        for rate in rates if rate.zone_id == zone.id]), title=zone.name) for zone in zones],
                    card(form(session, site, "shipping", field("Zone name", "name", required=True),
                        field("Country codes (comma separated)", "countries", "US", required=True),
                        field("State or region codes (optional)", "states"), label="Add zone", extra="/zones"), title="New delivery zone"),
                    card(form(session, site, "shipping", Label("Zone", Select(*options([(zone.id, zone.name) for zone in zones]), name="zone_id", required=True)),
                        field("Rate name", "name", "Standard delivery", required=True),
                        Label("Rate type", Select(*options([("flat", "Flat rate"), ("price_based", "Based on basket price"), ("weight_based", "Based on weight")]), name="kind")),
                        field("Price (minor units)", "amount_minor", 0, type="number", min="0"),
                        field("Minimum price or weight in grams", "minimum", 0, type="number", min="0"),
                        field("Maximum price or weight (optional)", "maximum", type="number", min="0"),
                        field("Free shipping threshold (optional, minor units)", "free_threshold", type="number", min="0"),
                        check("Local pickup", "local_pickup"), label="Add rate", extra="/rates"), title="New shipping rate"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/shipping/{kind}", methods=["POST"])
    async def post(session, request, site_id: str, kind: str):
        try:
            values = await request.form()
            check_csrf(session, values)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                if kind == "rates" and values.get("action") == "delete":
                    rate = db.scalar(select(ShippingRate).where(ShippingRate.id == values.get("rate_id"),
                        ShippingRate.site_id == site.id, ShippingRate.tenant_id == site.tenant_id))
                    if not rate:
                        raise CommerceError("Shipping rate not found.")
                    db.delete(rate)
                    db.commit()
                    return back(site, "shipping", "Rate deleted")
                name = str(values.get("name", "")).strip()
                if not name or len(name) > 160:
                    raise CommerceError("Enter a name up to 160 characters.")
                if kind == "zones":
                    countries = [code.strip().upper() for code in str(values.get("countries", "")).split(",") if code.strip()]
                    states = [code.strip().upper() for code in str(values.get("states", "")).split(",") if code.strip()]
                    if not countries or any(not re.fullmatch(r"[A-Z]{2}", code) for code in countries):
                        raise CommerceError("Enter two-letter country codes.")
                    if any(not re.fullmatch(r"[A-Z0-9:-]{1,20}", code) for code in states):
                        raise CommerceError("Enter valid region codes.")
                    zone = db.scalar(select(ShippingZone).where(ShippingZone.id == values.get("zone_id"),
                        ShippingZone.site_id == site.id, ShippingZone.tenant_id == site.tenant_id)) if values.get("zone_id") else None
                    if values.get("zone_id") and not zone:
                        raise CommerceError("Delivery zone not found.")
                    if not zone:
                        zone = ShippingZone(tenant_id=site.tenant_id, site_id=site.id)
                        db.add(zone)
                    zone.name, zone.countries_json, zone.states_json = name, sorted(set(countries)), sorted(set(states))
                    zone.is_active = values.get("is_active") == "on" if values.get("zone_id") else True
                elif kind == "rates":
                    zone = db.scalar(select(ShippingZone).where(ShippingZone.id == values.get("zone_id"),
                        ShippingZone.site_id == site.id, ShippingZone.tenant_id == site.tenant_id))
                    rate_kind = values.get("kind")
                    minimum, maximum = number(values.get("minimum", 0)), number(values.get("maximum"), optional=True)
                    if not zone or rate_kind not in {"flat", "price_based", "weight_based"}:
                        raise CommerceError("Choose a delivery zone and rate type.")
                    if maximum is not None and maximum < minimum:
                        raise CommerceError("Maximum must be at least the minimum.")
                    rate = db.scalar(select(ShippingRate).where(ShippingRate.id == values.get("rate_id"),
                        ShippingRate.site_id == site.id, ShippingRate.tenant_id == site.tenant_id)) if values.get("rate_id") else None
                    if values.get("rate_id") and not rate:
                        raise CommerceError("Shipping rate not found.")
                    if not rate:
                        rate = ShippingRate(tenant_id=site.tenant_id, site_id=site.id, zone_id=zone.id)
                        db.add(rate)
                    rate.name, rate.kind, rate.amount_minor = name, rate_kind, number(values.get("amount_minor"))
                    rate.minimum, rate.maximum = minimum, maximum
                    rate.free_shipping_threshold_minor = number(values.get("free_threshold"), optional=True)
                    rate.local_pickup = values.get("local_pickup") == "on"
                else:
                    raise CommerceError("Unknown shipping action.")
                db.commit()
                return back(site, "shipping")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/taxes", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                rates = db.scalars(select(TaxRate).where(TaxRate.site_id == site.id, TaxRate.tenant_id == site.tenant_id))
                return shell("Taxes and duties", card(P("Merchant tax rates apply to sandbox checkout only. Live payments always use Stripe Tax and reviewed provider registrations."),
                    index_table(["Country", "Region", "Rate", "Prices"], [[rate.country, rate.region or "All regions", f"{rate.rate_bps / 100:g}%", "Tax included" if rate.prices_include_tax else "Tax excluded"] for rate in rates])),
                    card(form(session, site, "taxes", field("Tax name", "name", "Sales tax"),
                        field("Country code", "country", "US", maxlength="2", required=True),
                        field("Region code (optional)", "region"),
                        field("Rate in basis points (100 = 1%)", "rate_bps", 0, type="number", min="0", max="10000"),
                        check("Prices include tax", "prices_include_tax")), title="Add or update tax rate"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/taxes", methods=["POST"])
    async def post(session, request, site_id: str):
        try:
            values = await request.form()
            check_csrf(session, values)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                country, region = str(values.get("country", "")).upper().strip(), str(values.get("region", "")).upper().strip()
                rate_bps = number(values.get("rate_bps"))
                if not re.fullmatch(r"[A-Z]{2}", country) or len(region) > 20 or rate_bps > 10000:
                    raise CommerceError("Choose a valid country, region and rate up to 100%.")
                rate = db.scalar(select(TaxRate).where(TaxRate.site_id == site.id,
                    TaxRate.tenant_id == site.tenant_id, TaxRate.country == country, TaxRate.region == region))
                if not rate:
                    rate = TaxRate(tenant_id=site.tenant_id, site_id=site.id, country=country, region=region)
                    db.add(rate)
                rate.name = str(values.get("name", "Sales tax"))[:100]
                rate.rate_bps, rate.prices_include_tax = rate_bps, values.get("prices_include_tax") == "on"
                db.commit()
                return back(site, "taxes")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/notifications", methods=["GET"])
    def get(session, site_id: str, event: str = "order_confirmation"):
        try:
            if event not in service.EVENTS:
                raise CommerceError("Choose a notification event.")
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                template = db.scalar(select(NotificationTemplate).where(NotificationTemplate.site_id == site.id,
                    NotificationTemplate.tenant_id == site.tenant_id, NotificationTemplate.event == event))
                return shell("Notifications", card(Form(Label("Event", Select(*options([(key, key.replace("_", " ").title()) for key in sorted(service.EVENTS)], event), name="event")), Button("Open", cls="e-button"), method="get", cls="e-form")),
                    card(form(session, site, "notifications", Input(type="hidden", name="event", value=event),
                        field("Subject", "subject", template.subject if template else "{{ store_name }} notification", required=True),
                        Label("Message", Textarea(template.body if template else "Hello {{ customer_name }},\n{{ message }}", name="body", rows="10", required=True)),
                        check("Enabled", "enabled", template.enabled if template else True),
                        P("Available placeholders: " + ", ".join("{{ " + key + " }}" for key in sorted(service.PLACEHOLDERS))),
                        Button("Preview", name="action", value="preview", cls="e-button"),
                        Button("Send test to my email", name="action", value="test", cls="e-button")), title="Notification template"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/notifications", methods=["POST"])
    async def post(session, request, site_id: str):
        try:
            values = await request.form()
            check_csrf(session, values)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                event = values.get("event")
                if event not in service.EVENTS:
                    raise CommerceError("Choose a notification event.")
                subject, body = str(values.get("subject", "")), str(values.get("body", ""))
                preview = service.render_template(subject, body, {"store_name": site.name, "customer_name": "Alex", "order_number": "PREVIEW-001", "total": "25.00", "message": "This is a preview.", "action_url": "https://example.test/account"}, escape=False)
                if values.get("action") == "preview":
                    return shell("Notification preview", card(P(preview[0]), Pre(preview[1]),
                        A("Back to notifications", href=f"/admin/sites/{site.id}/settings/notifications?event={event}", cls="e-button")))
                if values.get("action") == "test":
                    db.add(CommerceMail(tenant_id=site.tenant_id, site_id=site.id, user_id=user_id,
                        kind="notification_test", dedupe_key="notification-test:" + new_id(),
                        available_at=datetime.now(UTC),
                        reference_json={"subject": preview[0], "body": preview[1]}))
                else:
                    template = db.scalar(select(NotificationTemplate).where(NotificationTemplate.site_id == site.id,
                        NotificationTemplate.tenant_id == site.tenant_id, NotificationTemplate.event == event))
                    if not template:
                        template = NotificationTemplate(tenant_id=site.tenant_id, site_id=site.id, event=event)
                        db.add(template)
                    template.subject, template.body, template.enabled = subject, body, values.get("enabled") == "on"
                db.commit()
                return back(site, "notifications", "Test email queued" if values.get("action") == "test" else "Template saved")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/domains", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                return shell("Domains", card(P(site.hostname or "No custom domain configured"),
                    A("Manage domain and go-live checks", href=f"/admin/sites/{site.id}/golive", cls="e-button")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/policies", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                policies = (site.settings_json or {}).get("policies", {})
                pages = list(db.scalars(select(SitePage).where(SitePage.site_id == site.id,
                    SitePage.tenant_id == site.tenant_id, SitePage.kind == "legal")))
                return shell("Policies", card(form(session, site, "policies", *[
                    Label(key.replace("_", " ").title(), Textarea(policies.get(key, ""), name=key, rows="7"))
                    for key in ("refund", "privacy", "terms", "shipping_policy")]), title="Store policies"),
                    card(*[A(page.title, href=f"/admin/sites/{site.id}/editor?page={page.id}", cls="e-button") for page in pages], title="Legal pages"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/policies", methods=["POST"])
    async def post(session, request, site_id: str):
        try:
            values = await request.form()
            check_csrf(session, values)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                policies = {key: str(values.get(key, "")).strip() for key in ("refund", "privacy", "terms", "shipping_policy")}
                if any(len(value) > 50000 for value in policies.values()):
                    raise CommerceError("Each policy must contain fewer than 50,000 characters.")
                site.settings_json = {**(site.settings_json or {}), "policies": policies}
                db.commit()
                return back(site, "policies")
        except CommerceError as exc:
            return error(exc)

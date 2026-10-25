"""Shipping, local tax, safe notifications and staff authorisation services."""

import hashlib
import hmac
import html
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app import signup_services
from app.auth import hash_admin_password
from app.config import settings
from app.growth_settings_models import (
    NotificationTemplate,
    ShippingRate,
    ShippingZone,
    StaffInvite,
    TaxRate,
)
from app.models import CommerceMail, Membership, Tenant, User, new_id
from app.services import CommerceError

STAFF_ROLES = {"admin", "merchant", "editor"}
EVENTS = {"draft_invoice", "order_confirmation", "shipping_confirmation", "refund", "invite", "customer_account"}
PLACEHOLDERS = {"store_name", "customer_name", "order_number", "total", "tracking_number", "action_url", "message"}


def require_admin(db, site, user_id):
    db.execute(update(Tenant).where(Tenant.id == site.tenant_id).values(updated_at=Tenant.updated_at))
    # Lock the tenant's admins in a stable order before changing access.
    rows = list(db.scalars(select(Membership).where(
        Membership.tenant_id == site.tenant_id).order_by(Membership.id).with_for_update()))
    user = db.get(User, user_id)
    if not user or not user.is_active or not any(
            row.user_id == user_id and row.role == "admin" for row in rows):
        raise CommerceError("Only administrators can manage staff.")
    return rows


def change_staff(db, site, actor_id, membership_id, *, role=None, remove=False):
    rows = require_admin(db, site, actor_id)
    member = next((row for row in rows if row.id == membership_id), None)
    if not member or member.role not in STAFF_ROLES:
        raise CommerceError("Staff member not found.")
    if not remove and role not in STAFF_ROLES:
        raise CommerceError("Choose an administrator, merchant or editor role.")
    if member.role == "admin" and (remove or role != "admin"):
        active_admins = db.scalar(select(func.count()).select_from(Membership).join(User).where(
            Membership.tenant_id == site.tenant_id, Membership.role == "admin", User.is_active.is_(True)))
        if active_admins <= 1:
            raise CommerceError("Your workspace must keep at least one administrator.")
    if remove:
        db.delete(member)
    else:
        member.role = role
    db.flush()


def invite_token(invite):
    return hmac.new(settings.session_secret.encode(),
                    ("staff-invite:" + invite.id).encode(), hashlib.sha256).hexdigest()


def invite_url(invite):
    return settings.public_url.rstrip("/") + "/staff/accept?token=" + invite_token(invite)


def invite_staff(db, site, actor_id, email, role):
    require_admin(db, site, actor_id)
    email = signup_services.normalized_email(email)
    if role not in STAFF_ROLES:
        raise CommerceError("Choose an administrator, merchant or editor role.")
    if db.scalar(select(Membership.id).join(User, User.id == Membership.user_id).where(
            Membership.tenant_id == site.tenant_id, func.lower(User.email) == email)):
        raise CommerceError("This person already belongs to your workspace.")
    invite = StaffInvite(id=new_id(), tenant_id=site.tenant_id, site_id=site.id,
                         email=email, role=role, invited_by=actor_id,
                         expires_at=datetime.now(UTC) + timedelta(days=7))
    invite.token_hash = hashlib.sha256(invite_token(invite).encode()).hexdigest()
    db.add(invite)
    db.flush()
    db.add(CommerceMail(tenant_id=site.tenant_id, site_id=site.id, user_id=actor_id,
        kind="staff_invite", dedupe_key="staff-invite:" + invite.id,
        available_at=datetime.now(UTC),
        reference_json={"invite_id": invite.id}))
    db.flush()
    return invite


def pending_invite(db, token):
    if not re.fullmatch(r"[a-f0-9]{64}", str(token)):
        raise CommerceError("This invitation is invalid or has expired.")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    # A no-op write serialises acceptance on SQLite as well as row-locking databases.
    db.execute(update(StaffInvite).where(StaffInvite.token_hash == token_hash).values(
        updated_at=StaffInvite.updated_at))
    invite = db.scalar(select(StaffInvite).where(
        StaffInvite.token_hash == token_hash).with_for_update().execution_options(populate_existing=True))
    if not invite or invite.accepted_at or signup_services.utc(invite.expires_at) <= datetime.now(UTC):
        raise CommerceError("This invitation is invalid or has expired.")
    return invite


def accept_invite(db, token, *, user_id=None, name="", password="", confirmation=""):
    invite = pending_invite(db, token)
    user = db.scalar(select(User).where(func.lower(User.email) == invite.email))
    if user:
        if user.id != user_id or not user.is_active:
            raise CommerceError("Sign in with the invited email address to accept this invitation.")
    else:
        password = signup_services.validate_password(password, confirmation)
        if len(password) < 24:
            raise CommerceError("Use a password of at least 24 characters for a staff account.")
        user = User(email=invite.email, name=signup_services.normalized_name(name),
                    password_hash=hash_admin_password(password), email_verified_at=datetime.now(UTC))
        db.add(user)
        db.flush()
    member = db.scalar(select(Membership).where(Membership.tenant_id == invite.tenant_id,
                                                Membership.user_id == user.id))
    if not member:
        db.add(Membership(tenant_id=invite.tenant_id, user_id=user.id, role=invite.role))
    invite.accepted_at = datetime.now(UTC)
    db.flush()
    return user, invite


def shipping_options(db, site, destination, subtotal_minor, *, weight_grams=0):
    if min(subtotal_minor, weight_grams) < 0:
        raise CommerceError("Shipping values cannot be negative.")
    country = str(destination.get("country", "")).upper()
    state = str(destination.get("state", "")).upper()
    zones = list(db.scalars(select(ShippingZone).where(ShippingZone.tenant_id == site.tenant_id,
        ShippingZone.site_id == site.id, ShippingZone.is_active.is_(True))))
    matching = [zone for zone in zones if country in zone.countries_json
                and (not zone.states_json or state in zone.states_json
                     or country + ":" + state in zone.states_json)]
    # Region-specific zones take precedence over country-wide zones.
    specific = [zone for zone in matching if zone.states_json]
    matching = specific or matching
    options = []
    if matching:
        for rate in db.scalars(select(ShippingRate).where(
                ShippingRate.tenant_id == site.tenant_id, ShippingRate.site_id == site.id,
                ShippingRate.zone_id.in_([zone.id for zone in matching]))):
            measured = weight_grams if rate.kind == "weight_based" else subtotal_minor
            if rate.kind != "flat" and not (rate.minimum <= measured and (
                    rate.maximum is None or measured <= rate.maximum)):
                continue
            free = (rate.free_shipping_threshold_minor is not None
                    and subtotal_minor >= rate.free_shipping_threshold_minor)
            options.append({"id": rate.id, "name": rate.name,
                            "amount_minor": 0 if free else rate.amount_minor,
                            "local_pickup": rate.local_pickup})
    return sorted(options, key=lambda rate: (rate["local_pickup"], rate["amount_minor"], rate["id"]))


def shipping_amount(db, site, destination, subtotal_minor, *, weight_grams=0, rate_id=None):
    if not db.scalar(select(ShippingZone.id).where(ShippingZone.tenant_id == site.tenant_id,
                                                 ShippingZone.site_id == site.id)):
        return None
    options = shipping_options(db, site, destination, subtotal_minor, weight_grams=weight_grams)
    if rate_id:
        options = [rate for rate in options if rate["id"] == rate_id]
    else:
        options = [rate for rate in options if not rate["local_pickup"]]
    if not options:
        raise CommerceError("No delivery rate is available for this address and basket.")
    return options[0]["amount_minor"]


def tax_amount(db, site, destination, amount_minor):
    if amount_minor < 0:
        raise CommerceError("Taxable amount cannot be negative.")
    rates = list(db.scalars(select(TaxRate).where(TaxRate.tenant_id == site.tenant_id,
        TaxRate.site_id == site.id, TaxRate.country == str(destination.get("country", "")).upper())))
    region = str(destination.get("state", "")).upper()
    rate = next((rate for rate in rates if rate.region and rate.region == region), None)
    rate = rate or next((rate for rate in rates if not rate.region), None)
    if not rate:
        return None
    divisor = 10000 + rate.rate_bps if rate.prices_include_tax else 10000
    return {"amount_minor": (amount_minor * rate.rate_bps + divisor // 2) // divisor,
            "rate_bps": rate.rate_bps, "prices_include_tax": rate.prices_include_tax,
            "name": rate.name, "id": rate.id}


def render_template(subject, body, values, *, escape=True):
    if not subject.strip() or len(subject) > 300 or len(body) > 50000 or "\n" in subject or "\r" in subject:
        raise CommerceError("Enter a subject up to 300 characters and a body up to 50,000 characters.")
    pattern = re.compile(r"{{\s*([a-z_]+)\s*}}")
    for source in (subject, body):
        remainder = pattern.sub("", source)
        if "{{" in remainder or "}}" in remainder:
            raise CommerceError("Use a supported placeholder such as {{ store_name }}.")
        if any(key not in PLACEHOLDERS for key in pattern.findall(source)):
            raise CommerceError("This notification contains an unsupported placeholder.")
    def replace(match):
        value = str(values.get(match.group(1), ""))
        return html.escape(value, quote=True) if escape else value
    # Template copy is plain text too, never executable HTML or template code.
    if escape:
        subject, body = html.escape(subject), html.escape(body)
    rendered_subject = pattern.sub(replace, subject).replace("\r", " ").replace("\n", " ")
    return rendered_subject, pattern.sub(replace, body)


def notification_message(db, site, event, values, *, default=None, escape=False):
    template = db.scalar(select(NotificationTemplate).where(
        NotificationTemplate.tenant_id == site.tenant_id, NotificationTemplate.site_id == site.id,
        NotificationTemplate.event == event))
    if not template:
        return default
    if not template.enabled:
        return None
    return render_template(template.subject, template.body,
                           {"store_name": site.name, **values}, escape=escape)

"""FastShop platform subscription billing boundary.

Platform billing is deliberately isolated from tenant storefront payments:
it reads only ``FASTSHOP_BILLING_*`` configuration, stores only Stripe object
references, and defaults to test mode. Live credentials remain inert until a
separate operator acceptance flag is present in production.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.config import settings
from app.models import (
    BillingSubscription,
    CommerceMail,
    Membership,
    Site,
    Tenant,
    User,
    new_id,
)
from app.plans import PLANS
from app.services import CommerceError

API_VERSION = "2025-03-31.basil"
PAID_PLANS = ("basic", "pro")
ENTITLED_STATUSES = {"active", "trialing", "past_due"}
TERMINAL_STATUSES = {"canceled", "incomplete_expired", "unpaid"}


@dataclass(frozen=True)
class BillingConfiguration:
    secret_key: str
    webhook_secret: str
    prices: dict[str, str]
    mode: str

    @property
    def configured(self) -> bool:
        return bool(self.secret_key and self.webhook_secret)


def configuration() -> BillingConfiguration:
    """Read the dedicated platform-billing settings without store fallback."""
    key = str(settings.billing_stripe_secret_key or "").strip()
    webhook = str(settings.billing_stripe_webhook_secret or "").strip()
    mode = "live" if key.startswith("sk_live_") else "test"
    return BillingConfiguration(
        secret_key=key,
        webhook_secret=webhook,
        prices={
            "basic": str(settings.billing_price_basic or "").strip(),
            "pro": str(settings.billing_price_pro or "").strip(),
        },
        mode=mode,
    )


def validated_configuration(*, require_webhook: bool = True) -> BillingConfiguration:
    config = configuration()
    if not config.secret_key:
        raise CommerceError("Self-serve billing is not yet enabled.")
    if config.mode == "live":
        if not settings.is_production or not settings.billing_live_accepted:
            raise CommerceError(
                "Platform live billing has not completed operator acceptance."
            )
        key_pattern = r"sk_live_[A-Za-z0-9_]{8,}"
    else:
        key_pattern = r"(?:sk|rk)_test_[A-Za-z0-9_]{8,}"
    if not re.fullmatch(key_pattern, config.secret_key):
        raise CommerceError("Platform Stripe billing credentials are invalid.")
    if require_webhook and not re.fullmatch(
        r"whsec_[A-Za-z0-9_]{8,}", config.webhook_secret
    ):
        raise CommerceError("Platform billing webhook signing is not configured.")
    return config


def price_id(plan_id: str, config: BillingConfiguration | None = None) -> str:
    plan_id = str(plan_id or "").strip().lower()
    if plan_id not in PAID_PLANS:
        raise CommerceError("Choose Basic or Pro for self-serve billing.")
    value = (config or configuration()).prices.get(plan_id, "")
    if not re.fullmatch(r"price_[A-Za-z0-9_]+", value):
        raise CommerceError(f"Self-serve {PLANS[plan_id].name} billing is not yet available.")
    return value


def available_plans(config: BillingConfiguration | None = None) -> dict[str, bool]:
    config = config or configuration()
    if config.mode == "live":
        key_ready = bool(
            re.fullmatch(r"sk_live_[A-Za-z0-9_]{8,}", config.secret_key)
            and settings.is_production
            and settings.billing_live_accepted
        )
    else:
        key_ready = bool(
            re.fullmatch(r"(?:sk|rk)_test_[A-Za-z0-9_]{8,}", config.secret_key)
        )
    credentials_ready = bool(
        key_ready
        and re.fullmatch(r"whsec_[A-Za-z0-9_]{8,}", config.webhook_secret)
    )
    return {
        plan_id: bool(
            credentials_ready
            and re.fullmatch(r"price_[A-Za-z0-9_]+", config.prices.get(plan_id, ""))
        )
        for plan_id in PAID_PLANS
    }


def encode_form(value, prefix=""):
    result = {}
    if isinstance(value, dict):
        for key, item in value.items():
            result.update(encode_form(item, f"{prefix}[{key}]" if prefix else key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(encode_form(item, f"{prefix}[{index}]"))
    elif value is not None:
        result[prefix] = str(value).lower() if isinstance(value, bool) else str(value)
    return result


class StripeBillingGateway:
    """Bounded Stripe client for FastShop's own subscription account."""

    def __init__(self, *, transport=None, config: BillingConfiguration | None = None):
        self.config = config or validated_configuration()
        self.transport = transport

    def request(self, method: str, path: str, payload=None, *, idempotency_key=None):
        if method not in {"GET", "POST"} or not re.fullmatch(
            r"/[A-Za-z0-9_/]+", path
        ) or path.startswith("//"):
            raise CommerceError("Invalid platform billing operation.")
        if method == "POST" and (
            not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 255
        ):
            raise CommerceError("Platform billing writes require a stable idempotency key.")
        headers = {
            "Authorization": f"Bearer {self.config.secret_key}",
            "Stripe-Version": API_VERSION,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(
                base_url="https://api.stripe.com/v1",
                timeout=10,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                for retry in range(2):
                    try:
                        response = client.request(
                            method,
                            path,
                            data=encode_form(payload or {}),
                            headers=headers,
                        )
                        break
                    except httpx.TransportError:
                        if retry:
                            raise
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Invalid Stripe response")
                expected_live = self.config.mode == "live"
                if "livemode" in result and result.get("livemode") is not expected_live:
                    raise CommerceError("Stripe returned the wrong platform billing mode.")
                return result
        except CommerceError:
            raise
        except (httpx.HTTPError, ValueError):
            raise CommerceError(
                "Stripe could not start platform billing. Check configuration or try again."
            ) from None

    def create_checkout_session(
        self,
        row: BillingSubscription,
        email: str,
        *,
        success_url: str,
        cancel_url: str,
    ) -> dict:
        selected_price = price_id(row.plan_id, self.config)
        metadata = {
            "tenant_id": row.tenant_id,
            "billing_subscription_id": row.id,
            "checkout_command_id": row.checkout_command_id,
            "plan_id": row.plan_id,
        }
        payload = {
            "mode": "subscription",
            "client_reference_id": row.id,
            "line_items": [{"price": selected_price, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": metadata,
            "subscription_data": {"metadata": metadata},
            "allow_promotion_codes": True,
        }
        if row.stripe_customer_id:
            payload["customer"] = row.stripe_customer_id
        else:
            payload["customer_email"] = email
        result = self.request(
            "POST",
            "/checkout/sessions",
            payload,
            idempotency_key=(
                "fastshop-platform-billing-checkout-" + row.checkout_command_id
            ),
        )
        prefix = "cs_live_" if self.config.mode == "live" else "cs_test_"
        if not re.fullmatch(prefix + r"[A-Za-z0-9_]+", str(result.get("id", ""))):
            raise CommerceError("Stripe returned an invalid billing checkout session.")
        if not str(result.get("url", "")).startswith("https://checkout.stripe.com/"):
            raise CommerceError("Stripe returned an invalid billing checkout address.")
        return result


def verify_webhook(payload: bytes, signature: str, secret: str, *, now=None):
    """Verify and decode a Stripe event using the platform webhook secret."""
    if not secret or not signature or len(payload) > 2_000_000:
        raise CommerceError("Invalid platform billing webhook.")
    parts: dict[str, list[str]] = {}
    for item in signature.split(","):
        key, separator, value = item.strip().partition("=")
        if separator:
            parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts["t"][0])
    except (KeyError, ValueError, IndexError) as exc:
        raise CommerceError("Invalid platform billing webhook.") from exc
    current = int(time.time() if now is None else now)
    if abs(current - timestamp) > 300:
        raise CommerceError("Invalid platform billing webhook.")
    expected = hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, value) for value in parts.get("v1", [])):
        raise CommerceError("Invalid platform billing webhook.")
    try:
        event = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommerceError("Invalid platform billing webhook.") from exc
    if not isinstance(event, dict):
        raise CommerceError("Invalid platform billing webhook.")
    return event


def account_billing_row(db, user_id: str) -> BillingSubscription | None:
    return db.scalar(
        select(BillingSubscription)
        .join(Membership, Membership.tenant_id == BillingSubscription.tenant_id)
        .where(
            Membership.user_id == user_id,
            Membership.role.in_(("admin", "merchant")),
        )
        .order_by(BillingSubscription.created_at)
    )


def billing_tenant(db, user_id: str) -> Tenant:
    existing = account_billing_row(db, user_id)
    if existing:
        tenant = db.get(Tenant, existing.tenant_id)
        if tenant:
            return tenant
    tenant = db.scalar(
        select(Tenant)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .where(
            Membership.user_id == user_id,
            Membership.role.in_(("admin", "merchant")),
        )
        .order_by(Tenant.created_at)
    )
    if not tenant:
        raise CommerceError("A managed workspace is required for billing.")
    return tenant


def begin_checkout(db, user_id: str, plan_id: str) -> BillingSubscription:
    config = validated_configuration()
    plan_id = str(plan_id or "").strip().lower()
    price_id(plan_id, config)
    tenant = billing_tenant(db, user_id)
    row = db.scalar(
        select(BillingSubscription).where(BillingSubscription.tenant_id == tenant.id)
    )
    if row and row.operator_disabled_at:
        raise CommerceError(
            "This workspace plan is operator-managed; self-serve billing is unavailable."
        )
    if row and row.status in ENTITLED_STATUSES | {"checkout_complete"}:
        raise CommerceError(
            "This workspace already has self-serve billing. Contact the platform operator to change it."
        )
    if row is None:
        row = BillingSubscription(
            tenant_id=tenant.id,
            user_id=user_id,
            plan_id=plan_id,
            status="checkout_pending",
        )
        db.add(row)
    else:
        if row.plan_id != plan_id or row.status != "checkout_pending":
            row.checkout_command_id = new_id()
        row.user_id = user_id
        row.plan_id = plan_id
        row.status = "checkout_pending"
        row.stripe_subscription_id = None
        row.stripe_checkout_session_id = None
        row.current_period_end = None
        row.cancel_at_period_end = False
        row.last_invoice_status = ""
    db.flush()
    return row


def record_checkout_session(
    db, row: BillingSubscription, session_id: str
) -> BillingSubscription:
    owned = db.scalar(
        select(BillingSubscription).where(
            BillingSubscription.id == row.id,
            BillingSubscription.tenant_id == row.tenant_id,
        )
    )
    if not owned:
        raise CommerceError("Billing checkout no longer belongs to this workspace.")
    owned.stripe_checkout_session_id = session_id
    db.flush()
    return owned


def _provider_id(value, prefix: str, label: str) -> str:
    value = str(value or "")
    if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_]+", value):
        raise CommerceError(f"Invalid Stripe {label} reference.")
    return value


def _period_end(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CommerceError("Invalid Stripe subscription period.") from exc


def _metadata(obj: dict) -> dict:
    metadata = obj.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise CommerceError("Invalid Stripe billing metadata.")
    return metadata


def _row_from_metadata(db, obj: dict) -> BillingSubscription:
    metadata = _metadata(obj)
    row_id = str(metadata.get("billing_subscription_id", ""))
    tenant_id = str(metadata.get("tenant_id", ""))
    row = db.scalar(
        select(BillingSubscription).where(
            BillingSubscription.id == row_id,
            BillingSubscription.tenant_id == tenant_id,
        )
    )
    if not row:
        raise CommerceError("Stripe billing mapping was not found.")
    return row


def _row_from_provider(db, obj: dict) -> BillingSubscription:
    subscription_value = obj.get("subscription")
    if isinstance(subscription_value, dict):
        subscription_value = subscription_value.get("id")
    if obj.get("object") == "subscription":
        subscription_value = obj.get("id")
    customer_value = obj.get("customer")
    if isinstance(customer_value, dict):
        customer_value = customer_value.get("id")
    if subscription_value:
        row = db.scalar(
            select(BillingSubscription).where(
                BillingSubscription.stripe_subscription_id == str(subscription_value)
            )
        )
        if row:
            return row
    if customer_value:
        row = db.scalar(
            select(BillingSubscription).where(
                BillingSubscription.stripe_customer_id == str(customer_value)
            )
        )
        if row:
            return row
    return _row_from_metadata(db, obj)


def _subscription_plan(obj: dict, config: BillingConfiguration) -> str:
    items = obj.get("items") or {}
    data = items.get("data", []) if isinstance(items, dict) else []
    price_values = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        price = item.get("price") or {}
        price_values.append(price.get("id") if isinstance(price, dict) else price)
    matches = [plan_id for plan_id, configured in config.prices.items() if configured in price_values]
    if len(matches) == 1:
        return matches[0]
    metadata_plan = str(_metadata(obj).get("plan_id", "")).lower()
    if metadata_plan in PAID_PLANS and config.prices.get(metadata_plan) in price_values:
        return metadata_plan
    raise CommerceError("Stripe subscription price does not map to a FastShop plan.")


def _apply_entitlement(db, row: BillingSubscription, plan_id: str) -> None:
    if row.operator_disabled_at:
        return
    tenant = db.get(Tenant, row.tenant_id)
    if not tenant:
        raise CommerceError("Billing workspace was not found.")
    tenant.plan = plan_id


def _checkout_completed(db, obj: dict) -> BillingSubscription:
    if obj.get("object") != "checkout.session" or obj.get("mode") != "subscription":
        raise CommerceError("Invalid Stripe subscription checkout event.")
    row = _row_from_metadata(db, obj)
    if str(_metadata(obj).get("checkout_command_id", "")) != row.checkout_command_id:
        raise CommerceError("Stripe checkout command does not match the billing mapping.")
    session_id = _provider_id(obj.get("id"), "cs_", "checkout")
    if row.stripe_checkout_session_id and row.stripe_checkout_session_id != session_id:
        raise CommerceError("Stripe checkout does not match the billing mapping.")
    metadata_plan = str(_metadata(obj).get("plan_id", "")).lower()
    if metadata_plan != row.plan_id:
        raise CommerceError("Stripe checkout plan does not match the billing mapping.")
    row.stripe_checkout_session_id = session_id
    row.stripe_customer_id = _provider_id(obj.get("customer"), "cus_", "customer")
    row.stripe_subscription_id = _provider_id(
        obj.get("subscription"), "sub_", "subscription"
    )
    row.status = "checkout_complete"
    _apply_entitlement(db, row, row.plan_id)
    return row


def _subscription_changed(
    db, obj: dict, config: BillingConfiguration, *, deleted: bool = False
) -> BillingSubscription:
    if obj.get("object") != "subscription":
        raise CommerceError("Invalid Stripe subscription event.")
    row = _row_from_provider(db, obj)
    subscription_id = _provider_id(obj.get("id"), "sub_", "subscription")
    customer_id = _provider_id(obj.get("customer"), "cus_", "customer")
    if row.stripe_subscription_id and row.stripe_subscription_id != subscription_id:
        raise CommerceError("Stripe subscription does not match the billing mapping.")
    if row.stripe_customer_id and row.stripe_customer_id != customer_id:
        raise CommerceError("Stripe customer does not match the billing mapping.")
    row.stripe_subscription_id = subscription_id
    row.stripe_customer_id = customer_id
    status = "canceled" if deleted else str(obj.get("status", ""))
    if status not in ENTITLED_STATUSES | TERMINAL_STATUSES | {
        "incomplete", "paused"
    }:
        raise CommerceError("Invalid Stripe subscription status.")
    row.status = status
    row.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    row.current_period_end = _period_end(obj.get("current_period_end"))
    if status in ENTITLED_STATUSES:
        row.plan_id = _subscription_plan(obj, config)
        _apply_entitlement(db, row, row.plan_id)
    elif status in TERMINAL_STATUSES:
        _apply_entitlement(db, row, "free")
    return row


def _queue_dunning(db, row: BillingSubscription, invoice_id: str) -> None:
    site = db.scalar(
        select(Site).where(Site.tenant_id == row.tenant_id).order_by(Site.created_at)
    )
    if not site:
        raise CommerceError("Billing workspace has no site for transactional mail.")
    admins = list(
        db.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.tenant_id == row.tenant_id,
                Membership.role == "admin",
                User.is_active.is_(True),
            )
            .order_by(User.id)
        )
    )
    now = datetime.now(UTC)
    for user in admins:
        dedupe_key = f"platform-billing-payment-failed:{invoice_id}:{user.id}"
        exists = db.scalar(
            select(CommerceMail.id).where(CommerceMail.dedupe_key == dedupe_key)
        )
        if not exists:
            db.add(
                CommerceMail(
                    tenant_id=row.tenant_id,
                    site_id=site.id,
                    user_id=user.id,
                    customer_id=None,
                    kind="platform_billing_payment_failed",
                    dedupe_key=dedupe_key,
                    available_at=now,
                    reference_json={
                        "billing_subscription_id": row.id,
                        "invoice_id": invoice_id,
                    },
                )
            )


def _invoice_changed(db, obj: dict, *, paid: bool) -> BillingSubscription:
    if obj.get("object") != "invoice":
        raise CommerceError("Invalid Stripe invoice event.")
    row = _row_from_provider(db, obj)
    invoice_id = _provider_id(obj.get("id"), "in_", "invoice")
    row.last_invoice_status = "paid" if paid else "payment_failed"
    if paid:
        if row.status not in TERMINAL_STATUSES:
            row.status = "active"
            _apply_entitlement(db, row, row.plan_id)
    else:
        if row.status not in TERMINAL_STATUSES:
            row.status = "past_due"
        _queue_dunning(db, row, invoice_id)
    return row


def process_event(db, event: dict) -> tuple[str, str | None]:
    config = validated_configuration()
    expected_live = config.mode == "live"
    if event.get("livemode") is not expected_live:
        raise CommerceError("Stripe billing event mode does not match configuration.")
    event_type = event.get("type")
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if not isinstance(obj, dict):
        raise CommerceError("Invalid Stripe billing event.")
    row = None
    if event_type == "checkout.session.completed":
        candidate = _row_from_metadata(db, obj)
        command_id = str(_metadata(obj).get("checkout_command_id", ""))
        if command_id and command_id != candidate.checkout_command_id:
            return "ignored", candidate.tenant_id
        row = _checkout_completed(db, obj)
    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        row = _subscription_changed(db, obj, config)
    elif event_type == "customer.subscription.deleted":
        row = _subscription_changed(db, obj, config, deleted=True)
    elif event_type == "invoice.payment_failed":
        row = _invoice_changed(db, obj, paid=False)
    elif event_type == "invoice.paid":
        row = _invoice_changed(db, obj, paid=True)
    else:
        return "ignored", None
    db.flush()
    return "processed", row.tenant_id

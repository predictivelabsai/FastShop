"""Stripe boundary: sandbox-first, site-owned credentials, no card data in FastShop."""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.services import CommerceError

API_VERSION = "2025-03-31.basil"


class StripeTaxError(CommerceError):
    """A rejected Checkout request specifically caused by automatic tax."""


def credentials(site, db=None, mode=None):
    if db is None and hasattr(site, "tenant_id"):
        from app.db import SessionLocal

        with SessionLocal() as session:
            return credentials(site, db=session, mode=mode)
    if db is not None:
        from sqlalchemy import select

        from app.live_credentials import decrypt_secret
        from app.models import SiteCommerceSettings, SiteStripeConnection

        config = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == site.id,
            SiteCommerceSettings.tenant_id == site.tenant_id,
        ))
        selected_mode = ("live" if mode == "live" or (mode is None and config and config.mode == "live")
                         else "test")
        if selected_mode == "live":
            from app.live_credentials import credential_for, live_secrets
            key, secret = live_secrets(db, site, config)
            connection = db.scalar(select(SiteStripeConnection).where(
                SiteStripeConnection.site_id == site.id,
                SiteStripeConnection.tenant_id == site.tenant_id,
                SiteStripeConnection.mode == selected_mode,
            ))
            if connection:
                aad = f"fastshop:{site.id}:{connection.id}:"
                publishable = decrypt_secret(connection.publishable_key_ciphertext,
                    aad=aad + "publishable-key")
            else:
                credential = credential_for(db, site)
                publishable = credential.publishable_key or "" if credential else ""
            return key, secret, publishable

        connection = db.scalar(select(SiteStripeConnection).where(
            SiteStripeConnection.site_id == site.id,
            SiteStripeConnection.tenant_id == site.tenant_id,
            SiteStripeConnection.mode == selected_mode,
        ))
        if connection:
            aad = f"fastshop:{site.id}:{connection.id}:"
            return (
                decrypt_secret(connection.secret_key_ciphertext, aad=aad + "secret-key"),
                decrypt_secret(connection.webhook_secret_ciphertext, aad=aad + "webhook-secret"),
                decrypt_secret(connection.publishable_key_ciphertext, aad=aad + "publishable-key"),
            )
    prefix = f"FASTSHOP_STRIPE_{site.id.upper()}_"
    key = os.getenv(prefix + "SECRET_KEY", "")
    secret = os.getenv(prefix + "WEBHOOK_SECRET", "")
    publishable = os.getenv(prefix + "PUBLISHABLE_KEY", "")
    # Existing global credentials are exclusively for the explicitly designated store.
    # Other tenants never inherit the designated merchant account.
    primary_site = os.getenv("FASTSHOP_STRIPE_PRIMARY_SITE", "")
    if primary_site and site.slug == primary_site:
        key = key or os.getenv("STRIPE_SECRET_KEY", "")
        secret = secret or os.getenv("STRIPE_WEBHOOK_SECRET", "")
        publishable = publishable or os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    return key, secret, publishable


@dataclass(frozen=True)
class TaxResult:
    provider_id: str
    currency: str
    tax_minor: int
    total_minor: int
    expires_at: datetime
    breakdown: list[dict]


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


class StripeGateway:
    def __init__(self, site, *, transport=None, db=None):
        self.live_mode = False
        if db is not None:
            from app import commerce, live_credentials

            config = commerce.settings_for(db, site)
            if config and config.mode == "live":
                self.key, self.webhook_secret = live_credentials.live_secrets(db, site, config)
                self.live_mode = True
            else:
                self.key, self.webhook_secret, _ = credentials(site, db=db, mode="test")
        else:
            self.key, self.webhook_secret, _ = credentials(site)
        self.transport = transport
        allowed = ("sk_live_",) if self.live_mode else ("sk_test_", "rk_test_")
        if not self.key.startswith(allowed):
            raise CommerceError("Configure a Stripe sandbox key for this site. Live keys are not enabled.")

    @classmethod
    def for_connection(cls, key: str, webhook_secret: str = "", *, transport=None):
        if not re.fullmatch(r"(?:sk|rk)_(?:test|live)_[A-Za-z0-9_]{8,}", key):
            raise CommerceError("Enter a valid Stripe secret or restricted key.")
        gateway = cls.__new__(cls)
        gateway.key = key
        gateway.webhook_secret = webhook_secret
        gateway.transport = transport
        gateway.live_mode = "_live_" in key
        return gateway

    @classmethod
    def for_live_validation(cls, key: str, webhook_secret: str, *, transport=None):
        if not re.fullmatch(r"(?:sk|rk)_live_[A-Za-z0-9_]{8,}", key) or not re.fullmatch(
            r"whsec_[A-Za-z0-9_]{8,}", webhook_secret
        ):
            raise CommerceError("Stored live Stripe credentials are invalid.")
        gateway = cls.__new__(cls)
        gateway.key = key
        gateway.webhook_secret = webhook_secret
        gateway.transport = transport
        gateway.live_mode = True
        return gateway

    def request(self, method, path, payload=None, *, idempotency_key=None):
        if method not in {"GET", "POST", "DELETE"} or not re.fullmatch(r"/[A-Za-z0-9_/]+", path) or path.startswith("//"):
            raise CommerceError("Invalid Stripe operation.")
        if method == "POST" and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 255):
            raise CommerceError("Stripe writes require a stable idempotency key.")
        headers = {"Authorization": f"Bearer {self.key}", "Stripe-Version": API_VERSION}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(base_url="https://api.stripe.com/v1", timeout=10,
                              transport=self.transport, follow_redirects=False) as client:
                # Only replay transport failures, with the identical durable command/key.
                # HTTP errors (including 402/429/500) need caller reconciliation, not
                # a fresh payment or a retry storm. Bound network retries to one.
                for retry in range(2):
                    try:
                        response = client.request(method, path, data=encode_form(payload or {}), headers=headers)
                        break
                    except httpx.TransportError:
                        if retry:
                            raise
                if response.is_error:
                    try:
                        error = response.json().get("error", {})
                    except (ValueError, AttributeError):
                        error = {}
                    code = str(error.get("code", "")).lower()
                    param = str(error.get("param", "")).lower()
                    if code.startswith("tax") or "tax" in param or "automatic_tax" in param:
                        raise StripeTaxError("Stripe could not apply automatic tax to this checkout.")
                    response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Invalid provider response")
                if "livemode" in result and result.get("livemode") is not self.live_mode:
                    if not self.live_mode and result.get("livemode") is True:
                        raise CommerceError("A live Stripe response was rejected by sandbox commerce.")
                    raise CommerceError("Stripe returned a response for the wrong payment mode.")
                return result
        except CommerceError:
            raise
        except (httpx.HTTPError, ValueError):
            # Stripe errors can include customer/address data or account details.
            raise CommerceError("Stripe could not complete this request. Check configuration or try again; no payment is assumed.") from None

    def verify_connection(self):
        """Verify the account and collect identity plus best-effort Stripe Tax state."""
        account = self.request("GET", "/account")
        if not str(account.get("id", "")).startswith("acct_"):
            raise CommerceError("Stripe returned an invalid readiness response.")
        try:
            tax = self.tax_settings()
        except CommerceError:
            # Tax availability is a status check only. It never blocks connection setup.
            tax = {"status": "unknown", "missing_fields": [], "defaults": {}, "head_office": None}
        return {"connected": True, "mode": "live" if self.live_mode else "test",
            "charges_enabled": account.get("charges_enabled") is True,
            "account_id": account["id"],
            "account_name": (account.get("business_profile") or {}).get("name") or
                ((account.get("settings") or {}).get("dashboard") or {}).get("display_name", ""),
            "country": account.get("country", ""),
            "tax_settings_active": tax["status"] == "active",
            "tax_settings": tax,
            "webhook_secret_configured": bool(self.webhook_secret),
            "provider_acceptance_complete": False}

    def connection_status(self):
        """Return readiness flags without exposing account identifiers or secrets."""
        verified = self.verify_connection()
        return {key: verified[key] for key in (
            "connected", "mode", "charges_enabled", "tax_settings_active", "tax_settings",
            "webhook_secret_configured", "provider_acceptance_complete",
        )}

    def tax_settings(self):
        """Read the documented Stripe Tax Settings shape without blocking payments."""
        result = self.request("GET", "/tax/settings")
        if result.get("object") != "tax.settings" or result.get("livemode") is not self.live_mode:
            return {"status": "unknown", "missing_fields": [], "defaults": {}, "head_office": None}
        status = result.get("status")
        if status not in {"active", "pending"}:
            status = "unknown"
        details = result.get("status_details") or {}
        pending = details.get("pending") or {}
        missing = pending.get("missing_fields", [])
        return {
            "status": status,
            "missing_fields": [str(field)[:100] for field in missing if isinstance(field, str)]
                if isinstance(missing, list) else [],
            "defaults": result.get("defaults") if isinstance(result.get("defaults"), dict) else {},
            "head_office": result.get("head_office") if isinstance(result.get("head_office"), dict) else None,
        }

    def create_webhook_endpoint(self, url: str):
        from app.commerce_webhooks import CHECKOUT_EVENTS, RENEWAL_EVENTS

        result = self.request("POST", "/webhook_endpoints", {
            "url": url,
            "enabled_events": sorted(CHECKOUT_EVENTS | RENEWAL_EVENTS),
            "description": "FastShop site payment events",
        }, idempotency_key="fastshop-webhook-" + secrets.token_hex(24))
        if not str(result.get("id", "")).startswith("we_") or not str(result.get("secret", "")).startswith("whsec_"):
            raise CommerceError("Stripe did not return a usable webhook endpoint.")
        return result["id"], result["secret"]

    def delete_webhook_endpoint(self, endpoint_id: str):
        if not re.fullmatch(r"we_[A-Za-z0-9_]+", endpoint_id):
            raise CommerceError("Invalid webhook endpoint reference.")
        return self.request("DELETE", "/webhook_endpoints/" + endpoint_id)

    def validate_live_account(self):
        if not self.live_mode:
            raise CommerceError("A live Stripe key is required for this validation.")
        account = self.request("GET", "/account")
        if (not str(account.get("id", "")).startswith("acct_")
                or account.get("livemode", True) is not True):
            raise CommerceError("Stripe returned an invalid live account response.")
        return {"connected": True, "mode": "live"}

    def calculate_tax(self, lines, destination, origin, shipping_minor, *, idempotency_key):
        result = self.request("POST", "/tax/calculations", {
            "currency": "usd",
            "customer_details": {"address": destination, "address_source": "shipping"},
            "ship_from_details": {"address": origin},
            "line_items": [{"reference": line.reference, "amount": line.amount_minor,
                "quantity": line.quantity, "tax_code": line.tax_code, "tax_behavior": "exclusive"} for line in lines],
            "shipping_cost": {"amount": shipping_minor, "tax_behavior": "exclusive"},
            "expand": ["line_items"],
        }, idempotency_key=idempotency_key)
        try:
            tax, total = result["tax_amount_exclusive"], result["amount_total"]
            if type(tax) is not int or type(total) is not int or min(tax, total) < 0:
                raise ValueError("Invalid totals")
            if result.get("tax_amount_inclusive", 0) != 0 or result.get("livemode") is not self.live_mode:
                raise ValueError("Unexpected tax mode")
            if not str(result["id"]).startswith("taxcalc_") or not isinstance(result["tax_breakdown"], list):
                raise ValueError("Invalid tax calculation")
            return TaxResult(result["id"], result["currency"].upper(), tax, total,
                datetime.fromtimestamp(result["expires_at"], UTC), result["tax_breakdown"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise CommerceError("Stripe returned an incomplete tax calculation; checkout is unavailable.") from exc

    def checkout_status(self, session_id):
        prefix = "cs_live_" if self.live_mode else "cs_test_"
        if not re.fullmatch(prefix + r"[A-Za-z0-9]+", session_id):
            raise CommerceError("Invalid checkout session.")
        return self.request("GET", "/checkout/sessions/" + session_id)

    def create_checkout(self, attempt_id, command, *, tax_fallback=False):
        """Replay an immutable command, never rebuild it from mutable catalog/customer data."""
        customer = self.request("POST", "/customers", command["customer"],
            idempotency_key="fastshop-customer-" + attempt_id)
        if not re.fullmatch(r"cus_[A-Za-z0-9]+", str(customer.get("id", ""))):
            raise CommerceError("Stripe did not return a customer reference.")
        key = "fastshop-checkout-" + attempt_id + ("-no-tax" if tax_fallback else "")
        return self.request("POST", "/checkout/sessions", command["session"] | {"customer": customer["id"]},
            idempotency_key=key)

    def expire_checkout(self, session_id):
        prefix = "cs_live_" if self.live_mode else "cs_test_"
        if not re.fullmatch(prefix + r"[A-Za-z0-9]+", session_id):
            raise CommerceError("Invalid checkout session.")
        return self.request("POST", "/checkout/sessions/" + session_id + "/expire",
            idempotency_key="fastshop-expire-" + session_id)

    def create_refund(self, command_id, payload):
        """Replay one persisted refund command with a stable provider key."""
        return self.request("POST", "/refunds", payload,
            idempotency_key="fastshop-refund-" + command_id)

    def payment_intent_status(self, payment_intent_id):
        if not re.fullmatch(r"pi_[A-Za-z0-9_]+", payment_intent_id):
            raise CommerceError("Invalid payment reference.")
        return self.request("GET", "/payment_intents/" + payment_intent_id)

    def create_renewal(self, cycle_id, payload):
        return self.request("POST", "/payment_intents", payload,
            idempotency_key="fastshop-renewal-create-" + cycle_id)

    def create_payment_setup(self, setup_id, payload):
        return self.request("POST", "/checkout/sessions", payload,
            idempotency_key="fastshop-card-update-" + setup_id)

    def setup_intent_status(self, setup_intent_id):
        if not re.fullmatch(r"seti_[A-Za-z0-9_]+", setup_intent_id):
            raise CommerceError("Invalid card-update reference.")
        return self.request("GET", "/setup_intents/" + setup_intent_id)

    def confirm_renewal(self, cycle_id, payment_intent_id):
        if not re.fullmatch(r"pi_[A-Za-z0-9_]+", payment_intent_id):
            raise CommerceError("Invalid payment reference.")
        try:
            return self.request("POST", "/payment_intents/" + payment_intent_id + "/confirm",
                {"off_session": True}, idempotency_key="fastshop-renewal-confirm-" + cycle_id)
        except CommerceError:
            # A decline/3DS requirement can be an HTTP 402. Never infer failure
            # from transport status: re-fetch the known intent without logging errors.
            return self.payment_intent_status(payment_intent_id)

    def cancel_renewal(self, payment_intent_id):
        if not re.fullmatch(r"pi_[A-Za-z0-9_]+", payment_intent_id):
            raise CommerceError("Invalid payment reference.")
        try:
            return self.request("POST", "/payment_intents/" + payment_intent_id + "/cancel",
                idempotency_key="fastshop-renewal-cancel-" + payment_intent_id)
        except CommerceError:
            return self.payment_intent_status(payment_intent_id)

    def record_renewal_tax(self, cycle_id, calculation_id):
        result = self.request("POST", "/tax/transactions/create_from_calculation",
            {"calculation": calculation_id, "reference": "fastshop-renewal-" + cycle_id},
            idempotency_key="fastshop-renewal-tax-" + cycle_id)
        if not str(result.get("id", "")).startswith("tax_") or result.get("livemode") is not self.live_mode:
            raise CommerceError("Tax transaction reconciliation is pending.")
        return result["id"]


def make_gateway(factory, site, db):
    """Pass the DB only to the production gateway; simple test doubles stay compatible."""
    try:
        accepts_db = "db" in inspect.signature(factory).parameters
    except (TypeError, ValueError):
        accepts_db = False
    return factory(site, db=db) if accepts_db else factory(site)


def verify_webhook(payload: bytes, signature: str, secret: str, *, now=None):
    """Verify Stripe's signed raw body before parsing or trusting event metadata."""
    if not secret or len(payload) > 2_000_000:
        raise CommerceError("Invalid webhook.")
    try:
        fields = [item.split("=", 1) for item in signature.split(",")]
        stamps = [value for key, value in fields if key == "t"]
        if len(stamps) != 1:
            raise ValueError("Invalid timestamp")
        stamp = int(stamps[0])
        if abs((time.time() if now is None else now) - stamp) > 300:
            raise ValueError("Expired signature")
        expected = hmac.new(secret.encode(), str(stamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, value) for key, value in fields if key == "v1"):
            raise ValueError("Signature mismatch")
        event = json.loads(payload)
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"].startswith("evt_"):
            raise ValueError("Invalid event")
        return event
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CommerceError("Invalid webhook.") from exc

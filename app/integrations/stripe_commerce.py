"""Stripe boundary: sandbox-first, site-owned credentials, no card data in FastShop."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.services import CommerceError

API_VERSION = "2025-03-31.basil"


def credentials(site):
    prefix = f"FASTSHOP_STRIPE_{site.id.upper()}_"
    key = os.getenv(prefix + "SECRET_KEY", "")
    secret = os.getenv(prefix + "WEBHOOK_SECRET", "")
    # Existing global credentials are exclusively for the explicitly designated store.
    # Other tenants never inherit the H2 4 You merchant account.
    if site.slug == os.getenv("FASTSHOP_STRIPE_PRIMARY_SITE", "h24you"):
        key = key or os.getenv("STRIPE_SECRET_KEY", "")
        secret = secret or os.getenv("STRIPE_WEBHOOK_SECRET", "")
    return key, secret


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
    def __init__(self, site, *, transport=None):
        self.key, self.webhook_secret = credentials(site)
        self.transport = transport
        if not self.key.startswith(("sk_test_", "rk_test_")):
            raise CommerceError("Configure a Stripe sandbox key for this site. Live keys are not enabled.")

    def request(self, method, path, payload=None, *, idempotency_key=None):
        if method not in {"GET", "POST"} or not re.fullmatch(r"/[A-Za-z0-9_/]+", path) or path.startswith("//"):
            raise CommerceError("Invalid Stripe operation.")
        if method == "POST" and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 255):
            raise CommerceError("Stripe writes require a stable idempotency key.")
        headers = {"Authorization": f"Bearer {self.key}", "Stripe-Version": API_VERSION}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(base_url="https://api.stripe.com/v1", timeout=20,
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
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Invalid provider response")
                if result.get("livemode") is True:
                    raise CommerceError("A live Stripe response was rejected by sandbox commerce.")
                return result
        except CommerceError:
            raise
        except (httpx.HTTPError, ValueError):
            # Stripe errors can include customer/address data or account details.
            raise CommerceError("Stripe could not complete this request. Check configuration or try again; no payment is assumed.") from None

    def connection_status(self):
        """Read-only provider check, returning no account identifiers or secret values."""
        account = self.request("GET", "/account")
        tax = self.request("GET", "/tax/settings")
        if not str(account.get("id", "")).startswith("acct_") or tax.get("object") != "tax.settings" or tax.get("livemode") is not False:
            raise CommerceError("Stripe returned an invalid sandbox readiness response.")
        return {"connected": True, "mode": "sandbox",
            "charges_enabled": account.get("charges_enabled") is True,
            "tax_settings_active": tax.get("status") == "active",
            "webhook_secret_configured": bool(self.webhook_secret),
            "provider_acceptance_complete": False}

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
            if result.get("tax_amount_inclusive", 0) != 0 or result.get("livemode") is not False:
                raise ValueError("Unexpected tax mode")
            if not str(result["id"]).startswith("taxcalc_") or not isinstance(result["tax_breakdown"], list):
                raise ValueError("Invalid tax calculation")
            return TaxResult(result["id"], result["currency"].upper(), tax, total,
                datetime.fromtimestamp(result["expires_at"], UTC), result["tax_breakdown"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise CommerceError("Stripe returned an incomplete tax calculation; checkout is unavailable.") from exc

    def checkout_status(self, session_id):
        if not re.fullmatch(r"cs_test_[A-Za-z0-9]+", session_id):
            raise CommerceError("Invalid sandbox checkout session.")
        return self.request("GET", "/checkout/sessions/" + session_id)

    def create_checkout(self, attempt_id, command):
        """Replay an immutable command, never rebuild it from mutable catalog/customer data."""
        customer = self.request("POST", "/customers", command["customer"],
            idempotency_key="fastshop-customer-" + attempt_id)
        if not re.fullmatch(r"cus_[A-Za-z0-9]+", str(customer.get("id", ""))):
            raise CommerceError("Stripe did not return a customer reference.")
        return self.request("POST", "/checkout/sessions", command["session"] | {"customer": customer["id"]},
            idempotency_key="fastshop-checkout-" + attempt_id)

    def expire_checkout(self, session_id):
        if not re.fullmatch(r"cs_test_[A-Za-z0-9]+", session_id):
            raise CommerceError("Invalid sandbox checkout session.")
        return self.request("POST", "/checkout/sessions/" + session_id + "/expire",
            idempotency_key="fastshop-expire-" + session_id)

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
        if not str(result.get("id", "")).startswith("tax_") or result.get("livemode") is not False:
            raise CommerceError("Tax transaction reconciliation is pending.")
        return result["id"]


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

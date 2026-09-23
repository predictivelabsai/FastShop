"""Opt-in, site-scoped WooCommerce REST adapter. No synchronisation or billing writes.

Only deployment-managed origins are accepted; never pass a shopper-supplied URL.
WordPress remains responsible for its native checkout and plugin lifecycle.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx

from app.services import CommerceError


def usd_minor(value: str) -> int:
    """Convert Woo's decimal strings exactly; never round unknown prices silently."""
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
            raise ValueError
        amount = Decimal(value) * 100
        if not amount.is_finite():
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError):
        raise CommerceError("WooCommerce returned an invalid USD amount.") from None


class WooCommerceGateway:
    """Read-only plumbing; tenant permission must be checked before construction."""

    def __init__(self, site, *, tenant_id, transport=None):
        if not tenant_id or site.tenant_id != tenant_id:
            raise CommerceError("Store not found.")
        prefix = f"FASTSHOP_WOOCOMMERCE_{site.id.upper()}_"
        if os.getenv(prefix + "ENABLED", "").lower() != "true":
            raise CommerceError("WooCommerce integration is disabled for this site.")
        origin = os.getenv(prefix + "URL", "").rstrip("/")
        parsed = urlsplit(origin)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path
                or host == "localhost" or "." not in host
                or host.endswith((".localhost", ".local", ".internal"))):
            raise CommerceError("Configure a public HTTPS WooCommerce origin without a path.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise CommerceError("Private WooCommerce origins are not supported.")
        self._key = os.getenv(prefix + "CONSUMER_KEY", "")
        self._secret = os.getenv(prefix + "CONSUMER_SECRET", "")
        if not self._key.startswith("ck_") or not self._secret.startswith("cs_"):
            raise CommerceError("Configure this site's WooCommerce read-only API credentials.")
        self._origin = origin
        self._transport = transport

    def _get(self, resource, params=None):
        try:
            with httpx.Client(timeout=20, transport=self._transport, follow_redirects=False,
                              trust_env=False) as client:
                response = client.get(self._origin + "/wp-json/wc/v3/" + resource,
                    params=params, auth=(self._key, self._secret))
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, (list, dict)):
                    raise ValueError
                return result
        except (httpx.HTTPError, ValueError):
            # No raw provider errors, customer bodies, request URLs or credentials.
            raise CommerceError("WooCommerce read failed; check origin, permissions and plugin availability.") from None

    def list_resources(self, resource, *, page=1, per_page=50):
        if resource not in {"products", "orders", "customers", "coupons", "shipping/zones", "taxes"}:
            raise CommerceError("Unsupported WooCommerce resource.")
        if type(page) is not int or page < 1 or type(per_page) is not int or not 1 <= per_page <= 100:
            raise CommerceError("Invalid WooCommerce pagination.")
        result = self._get(resource, {"page": page, "per_page": per_page})
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise CommerceError("WooCommerce returned an invalid resource list.")
        return result

    def order(self, order_id):
        if type(order_id) is not int or order_id < 1:
            raise CommerceError("Invalid WooCommerce order reference.")
        result = self._get(f"orders/{order_id}")
        if not isinstance(result, dict) or result.get("id") != order_id:
            raise CommerceError("WooCommerce returned an unexpected order.")
        return result

    def connection_status(self):
        self.list_resources("products", per_page=1)
        return {"connected": True, "access": "read-only", "sync_enabled": False,
                "checkout_owner": "woocommerce", "subscriptions": "plugin-not-verified"}

    def create_order(self, *args, **kwargs):
        raise CommerceError("WooCommerce order writes are not implemented; use native WooCommerce checkout.")

    def create_coupon(self, *args, **kwargs):
        raise CommerceError("WooCommerce coupon synchronisation is not implemented.")

    def manage_subscription(self, *args, **kwargs):
        raise CommerceError("WooCommerce Subscriptions requires a selected plugin and a separate adapter.")


def verify_webhook(payload: bytes, signature: str, secret: str):
    """Verify raw Woo webhook HMAC. Caller must persist/deduplicate delivery IDs.

    No ingestion route is exposed until ownership mapping and replay storage exist.
    Woo signatures alone do not provide timestamp/replay protection.
    """
    if not secret or not signature or len(payload) > 2_000_000:
        raise CommerceError("Invalid WooCommerce webhook.")
    expected = base64.b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).decode()
    try:
        if not hmac.compare_digest(expected, signature):
            raise ValueError
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (TypeError, ValueError, UnicodeError):
        raise CommerceError("Invalid WooCommerce webhook.") from None

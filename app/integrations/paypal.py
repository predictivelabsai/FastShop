"""PayPal credential resolution and readiness for merchant or operator connections.

An encrypted store-admin connection takes precedence. Existing operator environment
variables remain a fallback, and readiness reports presence only, never credential values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SANDBOX_HOST = "https://api-m.sandbox.paypal.com"
LIVE_HOST = "https://api-m.paypal.com"


def env_prefix(site) -> str:
    return f"FASTSHOP_PAYPAL_{site.id.upper()}_"


def credentials(site, db=None) -> tuple[str, str, str]:
    """Return (client_id, secret, mode). Mode defaults to sandbox."""
    if getattr(site, "tenant_id", None):
        from app.site_integrations import payload_for

        connected = payload_for(site, "paypal", db)
        if connected:
            return (connected["client_id"], connected["secret"], connected["mode"])
    prefix = env_prefix(site)
    client_id = os.getenv(prefix + "CLIENT_ID", "")
    secret = os.getenv(prefix + "SECRET", "")
    mode = os.getenv(prefix + "MODE", "")
    # The primary store may use unprefixed globals, exactly like Stripe.
    primary_site = os.getenv("FASTSHOP_PAYPAL_PRIMARY_SITE", "")
    if primary_site and site.slug == primary_site:
        client_id = client_id or os.getenv("PAYPAL_CLIENT_ID", "")
        secret = secret or os.getenv("PAYPAL_SECRET", "")
        mode = mode or os.getenv("PAYPAL_MODE", "")
    return client_id, secret, (mode.strip().lower() or "sandbox")


@dataclass(frozen=True)
class PayPalReadiness:
    configured: bool
    mode: str
    client_id_set: bool
    secret_set: bool
    env_prefix: str


def readiness(site, db=None) -> PayPalReadiness:
    client_id, secret, mode = credentials(site, db)
    return PayPalReadiness(
        configured=bool(client_id and secret),
        mode="live" if mode == "live" else "sandbox",
        client_id_set=bool(client_id),
        secret_set=bool(secret),
        env_prefix=env_prefix(site),
    )

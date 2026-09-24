"""PayPal credential resolution and readiness (env-managed, per-site).

Mirrors the Stripe/WooCommerce model: secrets live in deployment environment variables
(set through Coolify/FastDevOps), never in the application database. There are two ways
to offer PayPal:

  1. Through Stripe Checkout — enable PayPal on the Stripe account and toggle it in the
     store's commerce settings. FastShop holds no PayPal keys. This is the supported path
     today (see ``checkout_payments.payment_methods_for``).
  2. Direct PayPal REST — set the environment variables below. Readiness is surfaced in
     the admin PayPal guide; a direct-PayPal checkout flow is a provider-gated seam.

``readiness`` never returns secret values, only whether each is present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SANDBOX_HOST = "https://api-m.sandbox.paypal.com"
LIVE_HOST = "https://api-m.paypal.com"


def env_prefix(site) -> str:
    return f"FASTSHOP_PAYPAL_{site.id.upper()}_"


def credentials(site) -> tuple[str, str, str]:
    """Return (client_id, secret, mode). Mode defaults to sandbox."""
    prefix = env_prefix(site)
    client_id = os.getenv(prefix + "CLIENT_ID", "")
    secret = os.getenv(prefix + "SECRET", "")
    mode = os.getenv(prefix + "MODE", "")
    # The primary store may use unprefixed globals, exactly like Stripe.
    if site.slug == os.getenv("FASTSHOP_PAYPAL_PRIMARY_SITE", "h24you"):
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


def readiness(site) -> PayPalReadiness:
    client_id, secret, mode = credentials(site)
    return PayPalReadiness(
        configured=bool(client_id and secret),
        mode="live" if mode == "live" else "sandbox",
        client_id_set=bool(client_id),
        secret_set=bool(secret),
        env_prefix=env_prefix(site),
    )

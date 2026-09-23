"""Pluggable carrier-tracking seam. Manual by default; real feeds are opt-in adapters.

Tracking today is merchant-entered shipment events. This module makes an automatic
carrier feed a first-class, testable extension point without inventing status a real
carrier has not confirmed: the default provider returns nothing, and a real provider
(AfterShip/EasyPost/carrier API) is registered here and selected per site once its
credentials are configured — a provider-acceptance gate, not a code gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Normalized carrier statuses a provider may report; kept small and provider-neutral.
STATUSES = {"pending", "in_transit", "out_for_delivery", "delivered", "exception"}


@dataclass(frozen=True)
class TrackingUpdate:
    status: str
    detail: str = ""
    carrier: str = ""
    tracking_number: str = ""


class CarrierProvider(Protocol):
    name: str

    def fetch(self, carrier: str, tracking_number: str) -> TrackingUpdate | None:
        """Return the latest normalized status, or None when unavailable."""


class ManualCarrier:
    """Default: no automatic feed. Shipment events stay merchant-entered."""

    name = "manual"

    def fetch(self, carrier: str, tracking_number: str) -> TrackingUpdate | None:
        return None


# Real adapters register themselves here, e.g. PROVIDERS["aftership"] = AfterShipCarrier.
PROVIDERS: dict[str, CarrierProvider] = {"manual": ManualCarrier()}


def resolve_provider(config: dict | None) -> CarrierProvider:
    """Pick the site's configured carrier provider, defaulting to manual."""
    name = (config or {}).get("carrier_provider", "manual")
    return PROVIDERS.get(name, PROVIDERS["manual"])


def automatic_tracking_enabled(config: dict | None) -> bool:
    return resolve_provider(config).name != "manual"

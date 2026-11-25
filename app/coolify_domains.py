"""Automatic HTTPS on Coolify: serve every connected store domain from this application.

The shared Traefik proxy issues a Let's Encrypt certificate for each domain in the
application's Coolify domain list. When a store domain connects (its DNS points here) or is
removed, FastShop updates that list through the Coolify API and queues a redeploy so the new
routes and certificates take effect. Nothing happens unless all three settings are present:

    FASTSHOP_COOLIFY_URL         e.g. https://coolify.example.com
    FASTSHOP_COOLIFY_TOKEN       a Coolify API token with write and deploy abilities
    FASTSHOP_COOLIFY_APP_UUID    this application's UUID in Coolify

``FASTSHOP_COOLIFY_KEEP_DOMAINS`` lists platform hostnames that always stay (comma
separated, e.g. https://shop.fastsme.com). Only hostnames of connected store domains are
added; anything else already in the list is kept.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select

from app.config import settings
from app.models import SiteDomain

log = logging.getLogger(__name__)
TIMEOUT_SECONDS = 15.0


def enabled() -> bool:
    return bool(settings.coolify_url and settings.coolify_token and settings.coolify_app_uuid)


def _host(entry: str) -> str:
    return entry.strip().removeprefix("https://").removeprefix("http://").split("/")[0].lower()


def desired_domains(current: list[str], connected: set[str], managed: set[str]) -> list[str]:
    """Current list, minus store domains that are no longer connected, plus connected ones.

    ``managed`` holds every hostname FastShop knows as a store domain (connected or not);
    entries outside it (the platform host, anything an operator added) are never removed.
    """
    keep = [entry for entry in current if entry.strip()
            and (_host(entry) not in managed or _host(entry) in connected)]
    for host in sorted(connected):
        if not any(_host(entry) == host for entry in keep):
            keep.append("https://" + host)
    for entry in settings.coolify_keep_domains:
        if not any(_host(item) == _host(entry) for item in keep):
            keep.insert(0, entry)
    return keep


def sync(db, *, removed: set[str] | None = None, transport=None) -> str:
    """Bring Coolify's domain list in line with connected store domains.

    Returns "off", "unchanged", "updated" or "failed". Never raises: a failed sync leaves
    the store working on its existing domains and is retried on the next domain change.
    """
    if not enabled():
        return "off"
    rows = list(db.scalars(select(SiteDomain)))
    connected = {row.hostname.lower() for row in rows if row.status == "connected"}
    managed = {row.hostname.lower() for row in rows} | {h.lower() for h in (removed or set())}
    headers = {"Authorization": "Bearer " + settings.coolify_token, "Accept": "application/json"}
    base = settings.coolify_url.rstrip("/") + "/api/v1"
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, headers=headers, transport=transport) as http:
            app = http.get(f"{base}/applications/{settings.coolify_app_uuid}")
            app.raise_for_status()
            current = [d for d in str(app.json().get("fqdn") or "").split(",") if d.strip()]
            wanted = desired_domains(current, connected, managed)
            if [_host(d) for d in wanted] == [_host(d) for d in current]:
                return "unchanged"
            http.patch(f"{base}/applications/{settings.coolify_app_uuid}",
                       json={"domains": ",".join(wanted)}).raise_for_status()
            http.get(f"{base}/deploy", params={"uuid": settings.coolify_app_uuid}).raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Coolify domain sync failed: %s", exc)
        return "failed"
    log.info("Coolify domains updated: %s", ",".join(_host(d) for d in wanted))
    return "updated"

"""Membership-scoped merchant entry selection and authentication return paths.

Non-store route audit (GET unless stated otherwise):
* main.py legacy commerce entries map through LEGACY_DESTINATIONS below.
  There is no legacy /admin/orders/{id} handler, so no detail alias is added.
* /developers keeps public documentation; merchants open Settings developers.
* /admin/billing, /success and /cancel retain account billing/callback semantics;
  /admin/billing/checkout retains its POST, CSRF and provider boundary. The shared
  contextual_page shell must present billing as merchant administration.
* /admin/onboarding/{id} retains ownership checks; /brief, /skip and /generate
  retain POST/CSRF checks. A completed wizard resumes a pending safe login path.
* /admin/platform/revenue and /deliveries retain operator checks.
* /admin/platform/plans and POST /tenants/{id}/plan retain operator/CSRF checks.
* /admin/platform/live-credentials, /sites/{id}/live-credentials and its POST
  /store, /verify, /accept and /disable retain operator, ownership and CSRF checks.
Capability routes are registered once by register_site_routes, using task 02's
register_admin_capability_routes(rt, actor, csrf, check_csrf, shell, error).
"""

from __future__ import annotations

from unicodedata import category
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from sqlalchemy import select
from starlette.responses import RedirectResponse

from app.db import SessionLocal
from app.models import Membership, OnboardingState, Site, User

STORE_SESSION_KEY = "admin_site_id"
STORE_SELECTION_REQUIRED = "admin_store_selection_required"
STORE_ROLES = ("admin", "merchant", "editor")
LEGACY_DESTINATIONS = {
    "products": "products",
    "orders": "orders",
    "customers": "customers",
    "inventory": "inventory",
    "promotions": "discounts",
    "content": "content/pages",
    "channels": "settings/channels",
    "integrations": "settings/integrations",
    **{f"integrations/{provider}": f"settings/integrations/{provider}"
       for provider in ("stripe", "woocommerce", "paypal", "fasterp")},
    "assistant": "assistant",
}
ORDER_FILTERS = {
    "view", "q", "payment", "fulfilment", "start", "end", "tag", "channel",
    "sort", "page", "per_page", "status_filter",
}


def safe_next_path(value: str, fallback: str = "") -> str:
    """Reject external and browser-normalised external/control destinations."""
    if not isinstance(value, str):
        return fallback
    decoded = value
    while True:
        if (not decoded.startswith("/") or decoded.startswith("//")
                or "\\" in decoded or any(category(c) == "Cc" for c in decoded)):
            return fallback
        expanded = unquote(decoded)
        if expanded == decoded:
            return value
        decoded = expanded


def accessible_sites(db, user_id: str) -> list[Site]:
    """Platform roles never bypass active-user and tenant membership checks."""
    return list(db.scalars(
        select(Site).join(Membership, Membership.tenant_id == Site.tenant_id)
        .join(User, User.id == Membership.user_id)
        .where(User.id == user_id, User.is_active.is_(True), Membership.role.in_(STORE_ROLES),
               Site.status != "deleted")
        .order_by(Site.name, Site.id)
    ).unique())


def current_site(db, session: dict, *, site_id: str | None = None) -> Site | None:
    """Revalidate explicit/remembered selection; a stale ID requires the picker.

    Shell callers may pass site_id to remember a store they are entering. Without
    a preference, use deterministic name/id order. Never silently switch tenants
    when an explicit or remembered store is inaccessible.
    """
    sites = accessible_sites(db, session.get("user_id", ""))
    if site_id is None and session.get(STORE_SELECTION_REQUIRED):
        return None
    preferred = site_id if site_id is not None else session.get(STORE_SESSION_KEY)
    if preferred:
        site = next((site for site in sites if site.id == preferred), None)
        if site:
            session[STORE_SESSION_KEY] = site.id
            session.pop(STORE_SELECTION_REQUIRED, None)
        else:
            session.pop(STORE_SESSION_KEY, None)
            session[STORE_SELECTION_REQUIRED] = True
        return site
    return sites[0] if sites else None


def merchant_destination(db, session: dict) -> str:
    sites = accessible_sites(db, session.get("user_id", ""))
    site = current_site(db, session)
    return f"/admin/sites/{site.id}" if len(sites) == 1 and site else "/admin/sites"


def authentication_destination(db, session: dict) -> str:
    """Use memberships across all tenants, including editor memberships."""
    if accessible_sites(db, session.get("user_id", "")) or session.get("role") in STORE_ROLES:
        return merchant_destination(db, session)
    return "/account"


def login_destination(path: str, query: str = "") -> str:
    original = safe_next_path(path + ("?" + query if query else ""), "/admin")
    return "/login?" + urlencode({"next": original})


class AdminEntryMiddleware:
    """Run inside session middleware, preserving auth state across owned flows."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        session = scope["session"]
        path = scope["path"]
        query = scope.get("query_string", b"").decode("latin-1")
        if path in {"/signup", "/auth/google"}:
            destination = safe_next_path(parse_qs(query).get("next", [""])[0])
            if destination:
                session["login_next"] = destination
        if (path == "/admin" or path.startswith("/admin/")) and scope["method"] == "GET":
            with SessionLocal() as db:
                user = db.get(User, session.get("user_id", ""))
                authenticated = bool(user and user.is_active)
            if not authenticated:
                return await RedirectResponse(login_destination(path, query), status_code=303)(
                    scope, receive, send)

        async def resume_onboarding(message):
            if (path.startswith("/admin/onboarding/")
                    and message["type"] == "http.response.start"
                    and message["status"] == 303):
                pending = safe_next_path(session.get("login_next", ""))
                site_id = path.split("/")[3]
                headers = list(message.get("headers", []))
                location = next((v.decode("latin-1") for k, v in headers
                                 if k.lower() == b"location"), "")
                if pending and urlsplit(location).path == f"/admin/sites/{site_id}":
                    with SessionLocal() as db:
                        site = current_site(db, session, site_id=site_id)
                        complete = site and db.scalar(select(OnboardingState.id).where(
                            OnboardingState.site_id == site.id,
                            OnboardingState.tenant_id == site.tenant_id,
                            OnboardingState.status.in_(("complete", "skipped")),
                        ))
                    if complete:
                        session.pop("login_next", None)
                        encoded = RedirectResponse(pending).headers["location"].encode("latin-1")
                        message = {**message, "headers": [
                            (k, encoded if k.lower() == b"location" else v)
                            for k, v in headers]}
            await send(message)

        await self.app(scope, receive, resume_onboarding)

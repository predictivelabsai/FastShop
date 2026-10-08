"""Resolve a configured hostname to a tenant-owned site before route dispatch."""

from urllib.parse import urlsplit

from sqlalchemy import select
from starlette.responses import Response

from app import commerce as commerce_service
from app.config import settings
from app.db import SessionLocal
from app.models import Site, SiteCommerceSettings


class SiteHostMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin-1").split(":", 1)[0].lower().rstrip(".")
        path = scope.get("path", "/")
        platform_host = (urlsplit(settings.public_url).hostname or "").lower().rstrip(".")
        landing_root = (
            path == "/"
            and settings.landing_root
            and settings.environment.lower() != "development"
            and host == platform_host
        )
        if landing_root:
            scope = dict(scope)
            scope["path"] = "/marketing/"
            scope["raw_path"] = b"/marketing/"
            return await self.app(scope, receive, send)
        shared = (
            "/static/", "/site-media/", "/admin", "/login", "/logout", "/auth/",
            "/healthz", "/readyz", "/marketing/", "/signup",
        )
        if not path.startswith(shared):
            with SessionLocal() as db:
                query = select(Site).where(Site.hostname == host)
                site = db.scalar(query)
                if site:
                    # The slug path remains the bounded merchant preview. A bound custom
                    # hostname is public only after the reviewed publication transition.
                    if site.status != "published" and not path.startswith(("/unsubscribe/", "/account")):
                        return await Response("This store is not open.", status_code=404)(scope, receive, send)
                    commerce = db.scalar(select(SiteCommerceSettings).where(SiteCommerceSettings.site_id == site.id,
                        SiteCommerceSettings.tenant_id == site.tenant_id))
                    if (path.startswith(("/api", "/sites/")) or
                            (path.startswith("/account") and not commerce) or
                            (path.startswith(("/cart", "/checkout")) and
                             not commerce_service.payments_enabled(db, site, commerce))):
                        return await Response("Commerce is not open on this preview site.", status_code=404)(scope, receive, send)
                    scope = dict(scope)
                    scope["site_base"] = ""
                    scope["site_canonical"] = "https://" + site.hostname
                    scope["path"] = f"/sites/{site.slug}" + path
                    scope["raw_path"] = scope["path"].encode()
        return await self.app(scope, receive, send)

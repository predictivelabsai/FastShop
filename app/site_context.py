"""Resolve a configured hostname to a tenant-owned site before route dispatch."""

from sqlalchemy import select
from starlette.responses import Response

from app.db import SessionLocal
from app.models import Site


class SiteHostMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin-1").split(":", 1)[0].lower().rstrip(".")
        path = scope.get("path", "/")
        shared = ("/static/", "/site-media/", "/admin", "/login", "/logout", "/auth/", "/healthz", "/readyz")
        if not path.startswith(shared):
            with SessionLocal() as db:
                site = db.scalar(select(Site).where(Site.hostname == host, Site.status.in_(["preview", "published"])))
                if site:
                    if path.startswith(("/api", "/cart", "/checkout", "/account", "/sites/")):
                        return await Response("Commerce is not open on this preview site.", status_code=404)(scope, receive, send)
                    scope = dict(scope)
                    scope["site_base"] = ""
                    scope["site_canonical"] = "https://" + site.hostname
                    scope["path"] = f"/sites/{site.slug}" + path
                    scope["raw_path"] = scope["path"].encode()
        return await self.app(scope, receive, send)

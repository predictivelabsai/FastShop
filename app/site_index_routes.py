"""Per-site robots and sitemap documents for hostname routed stores."""

from xml.sax.saxutils import escape

from sqlalchemy import select
from starlette.responses import PlainTextResponse, Response

from app.config import settings
from app.db import SessionLocal
from app.models import Product, Site, SitePage


def site_origin(site):
    return "https://" + (site.hostname or settings.public_url.split("://", 1)[-1].split("/", 1)[0])


def register_site_index_routes(rt):
    from app.site_seo import metadata as seo_metadata
    from app.site_seo import robots_content, seo_document

    @rt("/sites/{site_slug}/robots.txt")
    def robots(site_slug: str):
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == site_slug))
            if not site or site.status == "deleted":
                return PlainTextResponse("", status_code=404)
            if site.status != "published":
                return PlainTextResponse("User-agent: *\nDisallow: /\n")
            return PlainTextResponse(robots_content(site, site.published_settings_json or {}))

    @rt("/sites/{site_slug}/sitemap.xml")
    def sitemap(site_slug: str):
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == site_slug))
            if not site or site.status == "deleted":
                return Response("", status_code=404, media_type="application/xml")
            if site.status != "published":
                return Response(
                    '<?xml version="1.0" encoding="UTF-8"?><urlset '
                    'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>',
                    media_type="application/xml",
                )
            pages = list(db.scalars(select(SitePage).where(
                SitePage.site_id == site.id,
                SitePage.tenant_id == site.tenant_id,
                SitePage.published_json.is_not(None),
            ).order_by(SitePage.path)))
            urls = []
            for page in pages:
                document = seo_document(db, site, page, page.published_json or {})
                if seo_metadata(site, page, document, site_origin(site) + page.path)["noindex"]:
                    continue
                if page.kind == "product":
                    product = db.scalar(select(Product).where(
                        Product.id == page.product_id,
                        Product.tenant_id == site.tenant_id,
                        Product.status == "active",
                        Product.is_published.is_(True),
                    ))
                    if not product:
                        continue
                lastmod = page.updated_at.date().isoformat() if page.updated_at else ""
                urls.append(
                    "<url><loc>" + escape(site_origin(site) + page.path) + "</loc>"
                    + ("<lastmod>" + lastmod + "</lastmod>" if lastmod else "")
                    + "</url>"
                )
            body = (
                '<?xml version="1.0" encoding="UTF-8"?><urlset '
                'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                + "".join(urls) + "</urlset>"
            )
            return Response(body, media_type="application/xml")

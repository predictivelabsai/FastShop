"""Build JSON-LD from the site content and catalogue data being rendered."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from fastcore.xml import NotStr
from fasthtml.common import Script


def jsonld_script(payload):
    if not payload:
        return None
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return Script(NotStr(encoded), type="application/ld+json")


def jsonld_for_page(db, site, page, document, canonical, seo, *, preview=False):
    """Build structured data for the published content rendered on this page."""
    from sqlalchemy import select

    from app.integrations.csv_catalog import _minor_amount
    from app.models import Product, ProductVariant, Stock, VariantChannelListing, Warehouse

    payloads = []
    parsed = urlsplit(canonical)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    if page.path == "/":
        organisation = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": site.name,
            "url": canonical,
        }
        if seo.get("logo"):
            organisation["logo"] = seo["logo"]
        payloads.append(organisation)

    kind = getattr(page, "kind", "")
    if kind == "product" and page.product_id:
        product = db.scalar(select(Product).where(
            Product.id == page.product_id,
            Product.tenant_id == site.tenant_id,
        ))
        if product and (preview or product.is_published and product.status == "active"):
            product_data = {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": product.name,
                "description": product.short_description or product.description or seo.get("description", ""),
            }
            if seo.get("image"):
                product_data["image"] = [seo["image"]]
            offers = []
            variants = db.scalars(select(ProductVariant).where(
                ProductVariant.product_id == product.id,
                ProductVariant.tenant_id == site.tenant_id,
                ProductVariant.is_active.is_(True),
            ).order_by(ProductVariant.sort_order, ProductVariant.id))
            for variant in variants:
                listing = db.scalar(select(VariantChannelListing).where(
                    VariantChannelListing.variant_id == variant.id,
                    VariantChannelListing.channel_id == site.channel_id,
                ))
                if not listing:
                    continue
                quantity = sum(db.scalars(select(Stock.quantity - Stock.allocated)
                    .join(Warehouse, Warehouse.id == Stock.warehouse_id)
                    .where(
                        Stock.variant_id == variant.id,
                        Warehouse.tenant_id == site.tenant_id,
                        Warehouse.is_active.is_(True),
                        Warehouse.fulfils_online_orders.is_(True),
                    )))
                in_stock = (not variant.track_inventory or variant.continue_selling_when_out_of_stock
                            or quantity > 0)
                offers.append({
                    "@type": "Offer",
                    "url": canonical,
                    "price": _minor_amount(listing.price_minor, listing.currency),
                    "priceCurrency": listing.currency,
                    "availability": "https://schema.org/InStock" if in_stock
                                    else "https://schema.org/OutOfStock",
                })
            if offers:
                product_data["offers"] = offers
            payloads.append(product_data)

    if kind in {"product", "collection", "blog", "article"}:
        label = str(document.get("title") or page.title or site.name)
        crumbs = [{"name": "Home", "path": "/"}]
        if kind == "product":
            crumbs.append({"name": "Shop", "path": "/shop"})
        elif kind == "collection":
            crumbs.append({"name": "Collections", "path": "/shop"})
        elif kind == "article":
            crumbs.append({"name": "Blog", "path": "/blog"})
        crumbs.append({"name": label, "path": page.path})
        payloads.append({
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": index,
                 "name": crumb["name"], "item": origin + crumb["path"]}
                for index, crumb in enumerate(crumbs, 1)
            ],
        })

    if kind == "article":
        article = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": str(document.get("seo_title") or document.get("title") or page.title),
            "description": str(seo.get("description") or ""),
            "mainEntityOfPage": canonical,
            "publisher": {"@type": "Organization", "name": site.name},
        }
        blog = document.get("blog") or {}
        if blog.get("author_name"):
            article["author"] = {"@type": "Person", "name": blog["author_name"]}
        if document.get("published_at"):
            article["datePublished"] = str(document["published_at"])
        if seo.get("image"):
            article["image"] = [seo["image"]]
        payloads.append(article)

    return [jsonld_script(payload) for payload in payloads]

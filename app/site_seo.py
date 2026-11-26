"""Store SEO settings, page metadata resolution, crawler documents and health advice.

Store-wide values live in ``Site.settings_json`` (draft) and
``Site.published_settings_json`` (live) under ``seo_*`` keys. Page values live in the
page document (``seo_title``, ``seo_description``, ``seo_social_title``,
``seo_social_image``, ``seo_noindex``); products and collections keep their search title
and description in their own columns.

A store that has never saved SEO settings renders exactly the head FastShop always
rendered: the page title followed by the store name, the page description, Open Graph
title, description and URL, and the canonical link. The richer tags (Open Graph type and
image, Twitter card, structured data) are added once the merchant saves the SEO page.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from urllib.parse import urlsplit

from app.services import CommerceError

ROBOTS_LIMIT = 5000
TOKEN = re.compile(r"^[A-Za-z0-9_=-]{1,200}$")
ENTITY_FIELDS = {"seo_title", "seo_description", "seo_social_title", "seo_social_image", "seo_noindex"}
# The title the storefront has always rendered: the page name, then the store name.
DEFAULT_TITLE_FORMAT = "{page} — {store}"
STORE_KEYS = ("seo_title_format", "seo_default_description", "seo_social_title", "seo_social_image",
              "seo_indexing", "seo_google_verification", "seo_bing_verification",
              "seo_robots_override")
STRING_KEYS = tuple(key for key in STORE_KEYS if key != "seo_indexing")


def site_origin(site) -> str:
    """Public origin of a store: its own domain, or its path on the platform."""
    from app.config import settings

    if getattr(site, "hostname", None):
        return "https://" + site.hostname
    return settings.public_url.rstrip("/") + f"/sites/{site.slug}"


def media_origin(site) -> str:
    """Origin that serves ``/site-media/`` for this store."""
    from app.config import settings

    if getattr(site, "hostname", None):
        return "https://" + site.hostname
    return settings.public_url.rstrip("/")


def validate_site_image(db, site, value: str) -> str:
    from sqlalchemy import select

    from app.content import safe_url
    from app.models import SiteMedia
    from app.site_media import media_url

    value = safe_url(value, media=True)
    if not value:
        return value
    rows = db.scalars(select(SiteMedia).where(
        SiteMedia.site_id == site.id, SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.content_type.startswith("image/"),
    ))
    allowed = {media_url(media) for media in rows} | {f"/site-media/{site.id}/{media.id}" for media in rows}
    if value not in allowed:
        raise CommerceError("Choose an image from this store's media library.")
    return value


def validate(settings: dict) -> dict:
    """Validate the store SEO keys in a settings dictionary; other keys pass through."""
    result = dict(settings or {})
    if any(key in result and not isinstance(result[key], str) for key in STRING_KEYS):
        raise CommerceError("SEO text settings must be text.")
    title_format = result.get("seo_title_format", "").strip()[:200]
    if title_format and not all(key in title_format for key in ("{page}", "{store}")):
        raise CommerceError("Title format must include {page} and {store}.")
    result["seo_title_format"] = title_format
    result["seo_default_description"] = result.get("seo_default_description", "").strip()[:320]
    result["seo_social_title"] = result.get("seo_social_title", "").strip()[:240]
    from app.content import safe_url
    result["seo_social_image"] = safe_url(result.get("seo_social_image", "")[:500], media=True)
    if "seo_indexing" in result and type(result["seo_indexing"]) is not bool:
        raise CommerceError("Search engine visibility must be enabled or disabled.")
    result["seo_indexing"] = result.get("seo_indexing", True)
    result["seo_google_verification"] = result.get("seo_google_verification", "").strip()
    result["seo_bing_verification"] = result.get("seo_bing_verification", "").strip()
    for key in ("seo_google_verification", "seo_bing_verification"):
        if result[key] and not TOKEN.fullmatch(result[key]):
            raise CommerceError("Verification codes may contain letters, numbers, underscores, hyphens and equals signs.")
    robots = result.get("seo_robots_override", "")
    if (len(robots) > ROBOTS_LIMIT or "<" in robots or ">" in robots
            or any(ord(char) < 32 and char not in "\r\n\t" for char in robots)):
        raise CommerceError("Custom robots.txt must be plain text under 5,001 characters.")
    result["seo_robots_override"] = robots
    return result


def store_config(settings: dict | None) -> dict:
    """Read store SEO values for rendering; a damaged value falls back to its default."""
    settings = settings or {}
    values = {key: settings[key] for key in STORE_KEYS if key in settings}
    try:
        return validate(values)
    except CommerceError:
        result = validate({})
        for key, value in values.items():
            try:
                result.update({k: v for k, v in validate({key: value}).items() if k == key})
            except CommerceError:
                continue
        return result


def configured(settings: dict | None) -> bool:
    """True once the merchant has saved the SEO settings page for these settings."""
    return any(key in (settings or {}) for key in STORE_KEYS)


def validate_entity_fields(db, site, values: dict) -> dict:
    """Validate metadata shared by page, blog, product and collection editors."""
    if not isinstance(values, dict) or set(values) - ENTITY_FIELDS:
        raise CommerceError("Choose supported page SEO fields.")
    result = {}
    limits = {"seo_title": 240, "seo_description": 320, "seo_social_title": 240}
    for key, limit in limits.items():
        if key in values:
            value = values[key]
            if not isinstance(value, str):
                raise CommerceError("SEO titles and descriptions must be text.")
            result[key] = value.strip()[:limit]
    if "seo_social_image" in values:
        if not isinstance(values["seo_social_image"], str):
            raise CommerceError("Choose an image from this store's media library.")
        result["seo_social_image"] = validate_site_image(db, site, values["seo_social_image"])
    if "seo_noindex" in values:
        if type(values["seo_noindex"]) is not bool:
            raise CommerceError("Search visibility must be enabled or disabled.")
        result["seo_noindex"] = values["seo_noindex"]
    return result


def seo_document(db, site, page, document: dict) -> dict:
    """Apply catalogue column values to the page metadata view."""
    from sqlalchemy import select

    from app.models import Collection, Product

    result = deepcopy(document or {})
    product_id = getattr(page, "product_id", None)
    collection_id = getattr(page, "collection_id", None)
    if product_id:
        entity = db.scalar(select(Product).where(
            Product.id == product_id, Product.tenant_id == site.tenant_id,
        ))
        if entity:
            result["seo_title"] = entity.seo_title
            result["seo_description"] = entity.seo_description
            result.setdefault("title", entity.name)
            result.setdefault("description", entity.short_description or entity.description)
            if not result.get("image") and entity.image_url:
                result["image"] = entity.image_url
    elif collection_id:
        entity = db.scalar(select(Collection).where(
            Collection.id == collection_id, Collection.tenant_id == site.tenant_id,
        ))
        if entity:
            result["seo_title"] = entity.seo_title
            result["seo_description"] = entity.seo_description
            result.setdefault("title", entity.title)
            result.setdefault("description", entity.seo_description or entity.description)
            if not result.get("image") and entity.image_url:
                result["image"] = entity.image_url
    return result


def format_title(site, own_title: str, config: dict, *, home: bool = False, explicit: bool = False,
                 rich: bool = True) -> str:
    own_title = str(own_title or "")
    # A title that already ends with the store name is used as written, never "X | Store | Store".
    # Stores that have not set up SEO keep the title they have always rendered.
    already_named = ((rich or explicit) and bool(site.name)
                     and own_title.rstrip().lower().endswith(site.name.lower()))
    if (home and explicit) or already_named:
        return own_title
    return (config.get("seo_title_format") or DEFAULT_TITLE_FORMAT).replace(
        "{page}", own_title).replace("{store}", site.name)


def _absolute(site, value: str) -> str:
    return media_origin(site) + value if value.startswith("/") else value


def metadata(site, page, document, canonical: str, preview: bool = False) -> dict:
    from app.content import safe_url

    settings = (site.settings_json if preview else site.published_settings_json) or {}
    config = store_config(settings)
    home = page.path == "/"
    explicit = bool(str(document.get("seo_title") or "").strip())
    own_title = str(document.get("seo_title") or document.get("title") or "")
    rich = configured(settings)
    title = format_title(site, own_title, config, home=home, explicit=explicit, rich=rich)
    description = str(document.get("seo_description") or document.get("description")
                      or config["seo_default_description"] or "")
    social_title = str(document.get("seo_social_title") or document.get("seo_title")
                       or (config["seo_social_title"] if home else "") or document.get("title") or "")
    explicit_image = document.get("seo_social_image") or config.get("seo_social_image") or ""
    image_value = document.get("seo_social_image") or (document.get("image") if rich else "") or explicit_image
    image = safe_url(str(image_value), media=True) if image_value else ""
    if site.hostname:
        path = urlsplit(canonical).path or "/"
        prefix = f"/sites/{site.slug}"
        if path == prefix or path.startswith(prefix + "/"):
            path = path[len(prefix):] or "/"
        canonical = "https://" + site.hostname + path
    logo_value = settings.get("logo") or ""
    logo = safe_url(str(logo_value), media=True) if logo_value else ""
    indexing = config["seo_indexing"]
    noindex = bool(document.get("seo_noindex", False)) or not indexing
    kind = getattr(page, "kind", "")
    og_type = "product" if kind == "product" else "article" if kind == "article" else "website"
    return {"title": title, "description": description, "social_title": social_title,
            "image": _absolute(site, image) if image else "", "canonical": canonical,
            "indexing": indexing, "noindex": noindex, "og_type": og_type,
            "logo": _absolute(site, logo) if logo else "", "rich": rich,
            "has_seo_title": explicit,
            "google_verification": config["seo_google_verification"],
            "bing_verification": config["seo_bing_verification"]}


def robots_value(site, seo: dict, *, preview: bool = False, blog_noindex: bool = False) -> str:
    """The robots meta value; unchanged from earlier releases unless a page opts out."""
    if site.status != "published" or preview:
        return "noindex,nofollow"
    if seo["noindex"]:
        return "noindex"
    return "noindex,follow" if blog_noindex else "index,follow"


def head_nodes(seo: dict, *, robots: str):
    """Storefront ``<head>`` metadata. Extra tags appear only once SEO is configured."""
    from fasthtml.common import Link, Meta, Title

    rich = seo["rich"]
    image = seo["image"]
    return (
        Title(seo["title"]), Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="description", content=seo["description"]), Meta(name="robots", content=robots),
        Meta(property="og:title", content=seo["social_title"]), Meta(property="og:description", content=seo["description"]),
        Meta(property="og:image", content=image) if image else None,
        Meta(property="og:type", content=seo["og_type"]) if rich else None,
        Meta(property="og:url", content=seo["canonical"]), Link(rel="canonical", href=seo["canonical"]),
        Meta(name="twitter:card", content="summary_large_image" if image else "summary") if rich or image else None,
        Meta(name="google-site-verification", content=seo["google_verification"]) if seo["google_verification"] else None,
        Meta(name="msvalidate.01", content=seo["bing_verification"]) if seo["bing_verification"] else None,
    )


def jsonld_script(payload):
    from fasthtml.common import NotStr, Script

    if not payload:
        return None
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return Script(NotStr(encoded), type="application/ld+json")


def structured_data(db, site, page, document, seo, *, preview=False):
    """Truthful schema.org data for the content on this page, once SEO is configured."""
    from sqlalchemy import select

    from app.integrations.csv_catalog import _minor_amount
    from app.models import Product, ProductVariant, Stock, VariantChannelListing, Warehouse

    if not seo.get("rich"):
        return []
    canonical = seo["canonical"]
    origin = site_origin(site)
    payloads = []
    if page.path == "/":
        organisation = {"@context": "https://schema.org", "@type": "Organization",
                        "name": site.name, "url": canonical}
        if seo.get("logo"):
            organisation["logo"] = seo["logo"]
        payloads.append(organisation)

    kind = getattr(page, "kind", "")
    if kind == "product" and page.product_id:
        product = db.scalar(select(Product).where(
            Product.id == page.product_id, Product.tenant_id == site.tenant_id,
        ))
        if product and (preview or product.is_published and product.status == "active"):
            product_data = {"@context": "https://schema.org", "@type": "Product", "name": product.name,
                            "description": product.short_description or product.description
                            or seo.get("description", "")}
            if seo.get("image"):
                product_data["image"] = [seo["image"]]
            offers = []
            variants = db.scalars(select(ProductVariant).where(
                ProductVariant.product_id == product.id, ProductVariant.tenant_id == site.tenant_id,
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
                    .join(Warehouse, Warehouse.id == Stock.warehouse_id).where(
                        Stock.variant_id == variant.id, Warehouse.tenant_id == site.tenant_id,
                        Warehouse.is_active.is_(True), Warehouse.fulfils_online_orders.is_(True),
                    )))
                in_stock = (not variant.track_inventory or variant.continue_selling_when_out_of_stock
                            or quantity > 0)
                offers.append({"@type": "Offer", "url": canonical,
                               "price": _minor_amount(listing.price_minor, listing.currency),
                               "priceCurrency": listing.currency,
                               "availability": "https://schema.org/InStock" if in_stock
                               else "https://schema.org/OutOfStock"})
            if offers:
                product_data["offers"] = offers
            payloads.append(product_data)

    if kind in {"product", "collection", "blog", "article"}:
        label = str(document.get("title") or page.title or site.name)
        crumbs = [("Home", "/")]
        if kind == "product":
            crumbs.append(("Shop", "/shop"))
        elif kind == "collection":
            crumbs.append(("Collections", "/shop"))
        elif kind == "article":
            crumbs.append(("Blog", "/blog"))
        crumbs.append((label, page.path))
        payloads.append({"@context": "https://schema.org", "@type": "BreadcrumbList",
                         "itemListElement": [
                             {"@type": "ListItem", "position": index, "name": name,
                              "item": origin + path}
                             for index, (name, path) in enumerate(crumbs, 1)]})

    if kind == "article":
        article = {"@context": "https://schema.org", "@type": "Article",
                   "headline": str(document.get("seo_title") or document.get("title") or page.title),
                   "description": str(seo.get("description") or ""),
                   "mainEntityOfPage": canonical,
                   "publisher": {"@type": "Organization", "name": site.name}}
        blog = document.get("blog") or {}
        if isinstance(blog, dict) and blog.get("author_name"):
            article["author"] = {"@type": "Person", "name": blog["author_name"]}
        if document.get("published_at"):
            article["datePublished"] = str(document["published_at"])
        if seo.get("image"):
            article["image"] = [seo["image"]]
        payloads.append(article)
    return [jsonld_script(payload) for payload in payloads]


def edit_href(site, page) -> str:
    """Where a merchant edits the search listing for this page."""
    if page.product_id:
        return f"/admin/sites/{site.id}/products/{page.product_id}"
    if page.collection_id:
        return f"/admin/sites/{site.id}/collections/{page.collection_id}"
    if page.kind == "article":
        return f"/admin/sites/{site.id}/content/blog-posts/{page.id}"
    return f"/admin/sites/{site.id}/content/pages/{page.id}/seo"


def health_report(db, site) -> list[dict]:
    """Return advisory SEO issues for published pages owned by this site."""
    from sqlalchemy import select

    from app.models import Collection, Product, SitePage
    from app.site_blocks import default_locale, resolve_document
    from app.site_blog import is_listed

    pages = list(db.scalars(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
        SitePage.published_json.is_not(None),
    ).order_by(SitePage.path, SitePage.id)))
    canonical_base = site_origin(site)
    rows = []
    for page in pages:
        if not page.published_json:  # A JSON null is stored as text, not SQL NULL.
            continue
        if page.product_id:
            product = db.scalar(select(Product).where(
                Product.id == page.product_id, Product.tenant_id == site.tenant_id,
                Product.status == "active", Product.is_published.is_(True),
            ))
            if not product:
                continue
        elif page.collection_id:
            collection = db.scalar(select(Collection).where(
                Collection.id == page.collection_id, Collection.tenant_id == site.tenant_id,
                Collection.is_published.is_(True),
            ))
            if not collection:
                continue
        if page.kind == "article" and not is_listed(page):
            continue
        document = seo_document(db, site, page, resolve_document(page.published_json, default_locale(site)))
        seo = metadata(site, page, document, canonical_base + page.path)
        title = str(seo["title"] or "").strip()
        description = str(seo["description"] or "").strip()
        issues = []
        if not seo["has_seo_title"]:
            issues.append(("missing_title", "Missing SEO title"))
        if not description:
            issues.append(("missing_description", "Missing description"))
        elif len(description) > 160:
            issues.append(("long_description", "Description over 160 characters"))
        if not seo["image"] and not document.get("image"):
            issues.append(("missing_social_image", "Missing social image"))
        if seo["noindex"]:
            issues.append(("noindex", "Hidden from search engines"))
        if len(title) > 60:
            issues.append(("long_title", "Title over 60 characters"))
        rows.append({"id": page.id, "kind": page.kind, "path": page.path, "title": title or page.title,
                     "url": canonical_base + page.path, "edit_href": edit_href(site, page),
                     "issues": [{"code": code, "label": label} for code, label in issues]})

    counts = {}
    for row in rows:
        normalized = row["title"].casefold()
        if normalized:
            counts[normalized] = counts.get(normalized, 0) + 1
    for row in rows:
        if row["title"].casefold() and counts.get(row["title"].casefold(), 0) > 1:
            row["issues"].append({"code": "duplicate_title", "label": "Duplicate title"})
    return rows


def robots_content(site, config: dict) -> str:
    sitemap = "Sitemap: " + site_origin(site) + "/sitemap.xml"
    config = store_config(config)
    prefix = "" if site.hostname else f"/sites/{site.slug}"
    if not config["seo_indexing"]:
        generated = "User-agent: *\nDisallow: /\n"
    else:
        generated = "User-agent: *\nAllow: /\n" + "".join(
            f"Disallow: {prefix}{path}\n" for path in ("/cart", "/checkout", "/account", "/api"))
    custom = str(config.get("seo_robots_override", "")).strip()
    body = custom + "\n" if custom else generated
    if not config["seo_indexing"] and "Disallow: /" not in {line.strip() for line in body.splitlines()}:
        body += "Disallow: /\n"
    if "Sitemap:" not in body:
        body += sitemap + "\n"
    return body

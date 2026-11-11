"""Shared SEO settings validation and storefront metadata resolution."""

from __future__ import annotations

import re
from copy import deepcopy

from app.services import CommerceError

ROBOTS_LIMIT = 5000
TOKEN = re.compile(r"^[A-Za-z0-9_=-]{1,200}$")
ENTITY_FIELDS = {"seo_title", "seo_description", "seo_social_title", "seo_social_image", "seo_noindex"}


def validate_site_image(db, site, value: str) -> str:
    from sqlalchemy import select

    from app.content import safe_url
    from app.models import SiteMedia

    value = safe_url(value, media=True)
    if not value:
        return value
    rows = db.scalars(select(SiteMedia).where(
        SiteMedia.site_id == site.id, SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.content_type.startswith("image/"),
    ))
    allowed = {media.public_url or f"/site-media/{site.id}/{media.id}" for media in rows}
    if value not in allowed:
        raise CommerceError("Choose an image from this store's media library.")
    return value


def validate(settings: dict) -> dict:
    result = dict(settings or {})
    string_fields = ("seo_title_format", "seo_default_description", "seo_social_title",
                     "seo_social_image", "seo_google_verification", "seo_bing_verification",
                     "seo_robots_override")
    if any(key in result and not isinstance(result[key], str) for key in string_fields):
        raise CommerceError("SEO text settings must be text.")
    result["seo_title_format"] = result.get("seo_title_format", "{page} | {store}")[:200]
    if not all(key in result["seo_title_format"] for key in ("{page}", "{store}")):
        raise CommerceError("Title format must include {page} and {store}.")
    result["seo_default_description"] = result.get("seo_default_description", "")[:320]
    result["seo_social_title"] = result.get("seo_social_title", "")[:240]
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
    """Apply catalogue column values to the site page metadata view."""
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


def metadata(site, page, document, canonical: str, preview: bool = False) -> dict:
    from app.content import safe_url

    config = validate(site.settings_json if preview else site.published_settings_json or {})
    home = page.path == "/"
    own_title = document.get("seo_title") or document.get("title", "")
    title = own_title if home else config["seo_title_format"].replace("{page}", own_title).replace("{store}", site.name)
    if home and config.get("seo_home_title"):
        title = config["seo_home_title"]
    description = document.get("seo_description") or document.get("description") or config["seo_default_description"]
    if home and config.get("seo_home_description"):
        description = config["seo_home_description"]
    social_title = (document.get("seo_social_title") or document.get("social_title")
                    or document.get("seo_title") or config.get("seo_social_title") or title)
    image_value = (document.get("seo_social_image") or document.get("social_image")
                   or document.get("image") or config.get("seo_social_image", ""))
    image = safe_url(image_value, media=True) if image_value else ""
    origin = "https://" + (site.hostname or "")
    logo_value = config.get("logo") or config.get("logo_dark") or config.get("logo_light") or ""
    logo = safe_url(logo_value, media=True) if logo_value else ""
    indexing = config["seo_indexing"]
    noindex = bool(document.get("seo_noindex", False)) or not indexing
    kind = getattr(page, "kind", "")
    og_type = "product" if kind == "product" else "article" if kind == "article" else "website"
    return {"title": title, "description": description, "social_title": social_title,
            "image": image, "canonical": origin + page.path if site.hostname else canonical,
            "indexing": indexing, "noindex": noindex, "og_type": og_type, "logo": logo,
            "has_seo_title": bool(str(document.get("seo_title") or "").strip()
                                   or (home and config.get("seo_home_title"))),
            "google_verification": config["seo_google_verification"],
            "bing_verification": config["seo_bing_verification"]}


def health_report(db, site) -> list[dict]:
    """Return advisory SEO issues for published pages owned by this site."""
    from sqlalchemy import select

    from app.models import Collection, Product, SitePage
    from app.site_blocks import default_locale, resolve_document
    from app.site_blog import is_listed
    from app.site_index_routes import site_origin

    pages = list(db.scalars(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
        SitePage.published_json.is_not(None),
    ).order_by(SitePage.path, SitePage.id)))
    canonical_base = site_origin(site)
    rows = []
    for page in pages:
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

        published = page.published_json or page.draft_json or {
            "title": page.title, "blocks": [],
        }
        document = seo_document(db, site, page, resolve_document(
            published, default_locale(site),
        ))
        seo = metadata(site, page, document, canonical_base + page.path)
        if page.product_id:
            edit_href = f"/admin/sites/{site.id}/products/{page.product_id}"
        elif page.collection_id:
            edit_href = f"/admin/sites/{site.id}/collections/{page.collection_id}"
        elif page.kind == "article":
            edit_href = f"/admin/sites/{site.id}/content/blog-posts/{page.id}"
        else:
            edit_href = f"/admin/sites/{site.id}/content/pages/{page.id}/seo"
        title = str(seo["title"] or "").strip()
        description = str(seo["description"] or "").strip()
        issues = []
        if not seo["has_seo_title"]:
            issues.append(("missing_title", "Missing SEO title"))
        if not description:
            issues.append(("missing_description", "Missing description"))
        elif len(description) > 160:
            issues.append(("long_description", "Description over 160 characters"))
        if not seo["image"]:
            issues.append(("missing_social_image", "Missing social image"))
        if seo["noindex"]:
            issues.append(("noindex", "Hidden from search engines"))
        if len(title) > 60:
            issues.append(("long_title", "Title over 60 characters"))
        rows.append({
            "id": page.id,
            "kind": page.kind,
            "path": page.path,
            "title": title or page.title,
            "url": canonical_base + page.path,
            "edit_href": edit_href,
            "issues": [{"code": code, "label": label} for code, label in issues],
        })

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
    from app.site_index_routes import site_origin

    sitemap = "Sitemap: " + site_origin(site) + "/sitemap.xml"
    if not config.get("seo_indexing", True):
        generated = "User-agent: *\nDisallow: /\n"
    else:
        generated = ("User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /cart\n"
                    "Disallow: /checkout\nDisallow: /account\nDisallow: /login\nDisallow: /api\n")
    custom = str(config.get("seo_robots_override", "")).strip()
    body = custom + ("\n" if custom else "") if custom else generated
    if (not config.get("seo_indexing", True)
            and "Disallow: /" not in {line.strip() for line in body.splitlines()}):
        body += "Disallow: /\n"
    if "Sitemap:" not in body:
        body += sitemap + "\n"
    return body

"""Shared SEO settings validation and storefront metadata resolution."""

from __future__ import annotations

import re

from app.services import CommerceError

ROBOTS_LIMIT = 5000
TOKEN = re.compile(r"^[A-Za-z0-9_=-]{1,200}$")


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
    social_title = (document.get("social_title") or document.get("seo_title")
                    or config.get("seo_social_title") or title)
    image_value = document.get("social_image") or document.get("image") or config.get("seo_social_image", "")
    image = safe_url(image_value, media=True) if image_value else ""
    origin = "https://" + (site.hostname or "")
    return {"title": title, "description": description, "social_title": social_title,
            "image": image, "canonical": origin + page.path if site.hostname else canonical,
            "indexing": config["seo_indexing"],
            "google_verification": config["seo_google_verification"],
            "bing_verification": config["seo_bing_verification"]}


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

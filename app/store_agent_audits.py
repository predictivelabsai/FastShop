"""Bounded, static draft audits for the merchant assistant."""
from __future__ import annotations

from sqlalchemy import select

from app.models import SiteMedia
from app.site_blocks import default_locale, resolve_document, resolve_text, walk_blocks


def _images(document: dict, locale: str):
    for block in walk_blocks(document["blocks"]):
        image = resolve_text(block.get("image", ""), locale)
        if image:
            yield block, image, resolve_text(block.get("alt", ""), locale).strip()
        for index, item in enumerate(block.get("items", [])):
            if not isinstance(item, dict):
                continue
            image = resolve_text(item.get("image", ""), locale)
            if image:
                yield block, image, resolve_text(item.get("alt", ""), locale).strip(), index


def page_draft_report(db, site, page) -> dict:
    """Return deterministic draft checks, not a simulated browser or live crawl."""
    locale = default_locale(site)
    document = resolve_document(page.draft_json, locale)
    issues = []
    title = str(document.get("seo_title") or document.get("title") or "").strip()
    description = str(document.get("seo_description") or document.get("description") or "").strip()
    if not title:
        issues.append({"area": "seo", "severity": "high", "code": "missing_title",
                       "message": "The draft has no page title."})
    elif len(title) > 60:
        issues.append({"area": "seo", "severity": "medium", "code": "long_title",
                       "message": "The page title is longer than 60 characters."})
    if not description:
        issues.append({"area": "seo", "severity": "medium", "code": "missing_description",
                       "message": "The draft has no search description."})
    elif len(description) > 160:
        issues.append({"area": "seo", "severity": "low", "code": "long_description",
                       "message": "The search description is longer than 160 characters."})

    images = list(_images(document, locale))
    image_urls = []
    for entry in images:
        block, image, alt, *item_index = entry
        image_urls.append(image)
        if not alt:
            location = f"block {block['id']}"
            if item_index:
                location += f" item {item_index[0] + 1}"
            issues.append({"area": "accessibility", "severity": "high", "code": "missing_alt",
                           "message": f"Image in {location} has no alt text.",
                           "block_id": block["id"]})
    owned = {media.public_url or f"/site-media/{site.id}/{media.id}": media for media in db.scalars(
        select(SiteMedia).where(SiteMedia.tenant_id == site.tenant_id, SiteMedia.site_id == site.id)
    )}
    for url in dict.fromkeys(image_urls):
        media = owned.get(url)
        if media and media.size > 500_000:
            issues.append({"area": "performance", "severity": "medium", "code": "large_image",
                           "message": f"{media.title or 'An image'} is larger than 500 KB.",
                           "media_id": media.id})
    if len(images) > 12:
        issues.append({"area": "performance", "severity": "low", "code": "many_images",
                       "message": f"The draft contains {len(images)} images. Review mobile loading."})
    blocks = document["blocks"]
    if len(blocks) > 24:
        issues.append({"area": "performance", "severity": "low", "code": "many_sections",
                       "message": f"The draft contains {len(blocks)} top-level sections. Review mobile loading."})
    return {
        "page_id": page.id,
        "title": page.title,
        "draft_preview_url": f"/admin/sites/{site.id}/preview/{page.id}",
        "coverage": ["static SEO", "image alt text", "image weight", "section count"],
        "issues": issues,
        "published_content_changed": False,
    }

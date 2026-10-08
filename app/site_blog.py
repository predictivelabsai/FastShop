"""Site-owned taxonomy and snapshot-based article editorial metadata."""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select

from app.models import BlogCategory, SitePage
from app.services import CommerceError
from app.site_blocks import default_locale, resolve_text

STATES = ("draft", "published", "archived")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def taxonomy_slug(name):
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:80].rstrip("-") or "learn"


def validate_blog(value):
    if not isinstance(value, dict) or set(value) - {"state", "category_slug", "tags", "author_name", "author_bio"}:
        raise CommerceError("Choose supported article metadata fields.")
    result = {"state": "draft", "category_slug": "", "tags": [], "author_name": "", "author_bio": ""} | value
    if result["state"] not in STATES:
        raise CommerceError("Choose draft, published or archived editorial state.")
    for key, limit in (("author_name", 160), ("author_bio", 2000), ("category_slug", 80)):
        if not isinstance(result[key], str) or len(result[key]) > limit:
            raise CommerceError(f"{key} must be text of at most {limit} characters.")
        result[key] = result[key].strip()
    if result["category_slug"] and not SLUG.fullmatch(result["category_slug"]):
        raise CommerceError("Category slugs use lowercase letters, numbers and hyphens.")
    tags = result["tags"]
    if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(t, str) or len(t) > 80 or not SLUG.fullmatch(t) for t in tags):
        raise CommerceError("Use up to 20 tag slugs: lowercase letters, numbers and hyphens.")
    result["tags"] = list(dict.fromkeys(tags))
    return result


def metadata(document):
    return validate_blog((document or {}).get("blog", {"state": "published"}))


def is_listed(page, *, preview=False):
    document = page.draft_json if preview else page.published_json
    return page.kind == "article" and bool(document) and metadata(document)["state"] == "published"


def categories_for(db, site):
    return list(db.scalars(select(BlogCategory).where(
        BlogCategory.site_id == site.id, BlogCategory.tenant_id == site.tenant_id,
    ).order_by(BlogCategory.name, BlogCategory.slug)))


def ensure_category(db, site, name):
    name = name.strip() or "LEARN"
    if len(name) > 100:
        raise CommerceError("Category names must be at most 100 characters.")
    slug = taxonomy_slug(name)
    category = db.scalar(select(BlogCategory).where(
        BlogCategory.site_id == site.id, BlogCategory.tenant_id == site.tenant_id, BlogCategory.slug == slug))
    if not category:
        category = BlogCategory(tenant_id=site.tenant_id, site_id=site.id, slug=slug, name=name)
        db.add(category)
        db.flush()
    return category


def prepare_article(db, site, document):
    """Called at the shared save boundary, including restores and builder saves."""
    if "blog" in document:
        value = validate_blog(document["blog"])
        if value["category_slug"] and value["category_slug"] not in {c.slug for c in categories_for(db, site)}:
            raise CommerceError("Choose a category belonging to this site.")
        document["blog"] = value
    return document


def backfill_categories(db, site):
    pages = db.scalars(select(SitePage).where(
        SitePage.tenant_id == site.tenant_id, SitePage.site_id == site.id, SitePage.kind == "article"))
    for page in pages:
        for document, preview in ((page.draft_json, True), (page.published_json, False)):
            if document:
                ensure_category(db, site, resolve_text(document.get("category", "LEARN"), default_locale(site, preview=preview)))


def category_slug(document, site, *, preview=False):
    return metadata(document)["category_slug"] or taxonomy_slug(resolve_text(
        document.get("category", "LEARN"), default_locale(site, preview=preview)))


def articles_for(db, site, *, preview=False, category="", tag=""):
    pages = db.scalars(select(SitePage).where(
        SitePage.tenant_id == site.tenant_id, SitePage.site_id == site.id, SitePage.kind == "article"))
    result = []
    for page in pages:
        if not is_listed(page, preview=preview):
            continue
        document = page.draft_json if preview else page.published_json
        if category and category_slug(document, site, preview=preview) != category:
            continue
        if tag and tag not in metadata(document)["tags"]:
            continue
        result.append(page)
    result.sort(key=lambda p: ((p.published_json or {}).get("published_at") or p.created_at.isoformat(), p.id), reverse=True)
    return result

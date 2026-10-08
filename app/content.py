"""Tenant-owned site editing and publication; independent of checkout services."""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Channel,
    Membership,
    Product,
    Site,
    SiteMedia,
    SitePage,
    SiteRevision,
    Tenant,
    new_id,
)
from app.services import CommerceError
from app.site_blocks import BLOCK_TYPES, default_locale, normalize_document, resolve_text

SECTION_TYPES = {key: spec.label for key, spec in BLOCK_TYPES.items()}
PAGE_KINDS = {"home", "content", "collection", "product", "science", "blog", "article", "contact", "legal"}


def safe_url(value: str, *, media: bool = False) -> str:
    value = str(value).strip()
    if not value:
        return ""
    if any(c in value for c in ("\n", "\r", "\\")):
        raise CommerceError("Use a valid HTTPS or local URL.")
    parsed = urlsplit(value)
    if value.startswith("/") and not value.startswith("//"):
        return value
    if parsed.scheme == "https" and parsed.hostname and not parsed.username:
        return value
    if not media and value.startswith("mailto:") and "@" in value:
        return value
    raise CommerceError("Use a valid HTTPS or local URL.")


def page_path(value: str) -> str:
    value = value.strip().rstrip("/") or "/"
    if not re.fullmatch(r"/(?:[a-z0-9-]+/)*[a-z0-9-]*", value):
        raise CommerceError("Page paths use lowercase letters, numbers, hyphens and slashes.")
    if value.split("/")[1] in {"admin", "api", "login", "auth", "static", "sites", "cart", "checkout", "account"}:
        raise CommerceError("This path is reserved by the application.")
    return value


def validate_document(document: dict) -> dict:
    # New legacy-shaped input gets fresh identities; persisted legacy reads are deterministic.
    document = copy.deepcopy(document)
    blocks = document if isinstance(document, list) else document.get("sections", []) if isinstance(document, dict) else []
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict):
                block.setdefault("id", new_id())
    result = normalize_document(document)
    title = result.get("title", "")
    titles = title.values() if isinstance(title, dict) else [title]
    if not titles or any(not value.strip() for value in titles):
        raise CommerceError("Give this page a title.")
    return result


def owned_site(db: Session, site_id: str, user_id: str, *, publish: bool = False) -> Site:
    site = db.scalar(
        select(Site).join(Membership, Membership.tenant_id == Site.tenant_id).where(
            Site.id == site_id, Membership.user_id == user_id,
            Membership.role.in_(["admin", "merchant"] if publish else ["admin", "merchant", "editor"]),
        )
    )
    if not site:
        raise CommerceError("Site not found or access denied.")
    return site


def site_page(db: Session, site: Site, page_id: str) -> SitePage:
    page = db.scalar(select(SitePage).where(
        SitePage.id == page_id, SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
    ))
    if not page:
        raise CommerceError("Page not found.")
    return page


def site_pages(db: Session, site: Site) -> list[SitePage]:
    return list(db.scalars(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
    ).order_by(SitePage.path)))


def create_page(db: Session, site: Site, title: str, path: str, kind: str = "content", document=None) -> SitePage:
    if kind not in PAGE_KINDS:
        raise CommerceError("Choose a supported page type.")
    path = page_path(path)
    if db.scalar(select(SitePage.id).where(SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id, SitePage.path == path)):
        raise CommerceError("A page already uses this path.")
    page = SitePage(tenant_id=site.tenant_id, site_id=site.id, title=title.strip(), path=path,
                    kind=kind, draft_json=validate_document(document or {"title": title, "sections": []}))
    db.add(page)
    db.flush()
    return page


def save_page(db: Session, site: Site, page_id: str, user_id: str, document: dict, version: int, action="draft") -> SitePage:
    owned_site(db, site.id, user_id, publish=action in {"publish", "unpublish"})
    page = db.scalar(select(SitePage).where(
        SitePage.id == page_id, SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
    ).with_for_update().execution_options(populate_existing=True))
    if not page:
        raise CommerceError("Page not found.")
    if page.version != version:
        raise CommerceError("Someone updated this page. Reload before saving your changes.")
    document = validate_document(document)
    validate_media_ownership(db, site, document)
    if action == "publish":
        from app.compliance import assert_document_compliant
        assert_document_compliant(document)
    page.draft_json = document
    page.title = resolve_text(document["title"], default_locale(site))
    if not page.title.strip():
        raise CommerceError("Give this page a title in the default locale.")
    page.version += 1
    if action == "publish":
        published = copy.deepcopy(document)
        published["published_at"] = (page.published_json or {}).get("published_at") or datetime.now(UTC).isoformat()
        page.published_json = published
    elif action == "unpublish":
        page.published_json = None
    db.add(SiteRevision(tenant_id=site.tenant_id, site_id=site.id, page_id=page.id,
                        author_id=user_id, content_json=document, action=action))
    db.flush()
    return page


def validate_media_ownership(db, site, document):
    """Media IDs are references, not permission to attach another tenant's assets."""
    def visit(value):
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value.startswith("/site-media/"):
            parts = value.split("/")
            if len(parts) != 4 or parts[2] != site.id:
                raise CommerceError("Choose media belonging to this site.")
            media = db.scalar(select(SiteMedia.id).where(SiteMedia.id == parts[3], SiteMedia.site_id == site.id, SiteMedia.tenant_id == site.tenant_id))
            if not media:
                raise CommerceError("Media not found in this site.")
    visit(document)


def media_is_public(db, site, media_id):
    """An upload is public only when a published page/settings references it."""
    if site.status not in {"preview", "published"}:
        return False
    target = f"/site-media/{site.id}/{media_id}"

    def references(value):
        if isinstance(value, dict):
            return any(references(item) for item in value.values())
        if isinstance(value, list):
            return any(references(item) for item in value)
        return value == target

    if references(site.published_settings_json):
        return True
    pages = [page for page in site_pages(db, site) if page.published_json]
    if any(references(page.published_json) for page in pages):
        return True
    product_ids = [page.product_id for page in pages if page.product_id]
    return bool(product_ids and db.scalar(select(Product.id).where(
        Product.tenant_id == site.tenant_id, Product.id.in_(product_ids),
        Product.is_published.is_(True), Product.image_url == target)))


def restore_page(db: Session, site: Site, page_id: str, revision_id: str, user_id: str, version: int):
    revision = db.scalar(select(SiteRevision).where(
        SiteRevision.id == revision_id, SiteRevision.site_id == site.id,
        SiteRevision.tenant_id == site.tenant_id, SiteRevision.page_id == page_id,
    ))
    if not revision:
        raise CommerceError("Revision not found.")
    return save_page(db, site, page_id, user_id, revision.content_json, version, "restore")


def create_site(db: Session, user_id: str, name: str, slug: str) -> Site:
    name, slug = name.strip(), slug.strip().lower()
    if not name or len(name) > 160 or not re.fullmatch(r"[a-z][a-z0-9-]{2,60}", slug):
        raise CommerceError("Enter a name and a unique lowercase site address (3–61 characters).")
    if db.scalar(select(Site.id).where(Site.slug == slug)):
        raise CommerceError("That site address is already taken.")
    tenant = Tenant(name=name, slug=f"site-{new_id()}")
    db.add(tenant)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user_id, role="admin"))
    channel = Channel(tenant_id=tenant.id, slug="us", name="United States", currency="USD",
                      country_code="US", locale="en", prices_include_tax=False)
    db.add(channel)
    db.flush()
    config = {"default_locale": "en", "name": name, "tagline": "A little more everyday.", "accent": "#145cce",
              "email": "", "address": "", "navigation": [{"label": "Home", "path": "/"},
              {"label": "Shop", "path": "/shop"}, {"label": "About", "path": "/pages/about-us"},
              {"label": "Contact", "path": "/pages/contact"}], "claims": [], "facts": [],
              "footer": "", "announcement": "Welcome to our new site", "preview_notice": True}
    site = Site(tenant_id=tenant.id, channel_id=channel.id, slug=slug, name=name,
                settings_json=config, published_settings_json=copy.deepcopy(config))
    db.add(site)
    db.flush()
    create_page(db, site, name, "/", "home", {"title": name, "description": "Welcome to our site.",
        "sections": [{"type": "hero", "eyebrow": "Welcome", "heading": "Make room for something good.",
                      "body": "Your story starts here. Open the editor to make this site your own."}]})
    create_page(db, site, "About us", "/pages/about-us", document={"title": "About us", "sections": [
        {"type": "text", "heading": "Our story", "body": "Tell your visitors what matters to you."}]})
    create_page(db, site, "Our collection", "/shop", "collection", {"title": "Our collection", "sections": [
        {"type": "text", "heading": "Our collection", "body": "Discover our products."}, {"type": "products"}]})
    create_page(db, site, "Contact", "/pages/contact", "contact", {"title": "Contact", "sections": [
        {"type": "text", "heading": "Get in touch", "body": "We would love to hear from you."}, {"type": "contact"}]})
    for slug, title in [("terms-and-conditions", "Terms and Conditions"), ("privacy-policy", "Privacy Policy"), ("returns-and-refunds", "Returns and Refunds"), ("faq", "FAQ")]:
        create_page(db, site, title, "/pages/" + slug, "legal", {"title": title, "sections": [
            {"type": "text", "heading": title, "body": "PLACEHOLDER — add your company's reviewed policy before launch."}]})
    from app.site_menus import adapt_navigation
    adapt_navigation(db, site)
    return site


def catalog_product(db: Session, site: Site, product_id: str) -> Product | None:
    return db.scalar(select(Product).where(Product.id == product_id, Product.tenant_id == site.tenant_id))

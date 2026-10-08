"""Site-owned media registry. Content URLs are never rewritten by registration."""
import mimetypes
from pathlib import Path

from sqlalchemy import select

from app import content
from app.models import Product, SiteChangeSet, SiteMedia, SiteMenu, SiteRevision
from app.services import CommerceError


def media_url(media):
    return media.public_url or f"/site-media/{media.site_id}/{media.id}"


def media_for(db, site):
    return list(db.scalars(select(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id, SiteMedia.site_id == site.id,
    ).order_by(SiteMedia.created_at.desc(), SiteMedia.id)))


def owned_media(db, site, media_id):
    row = db.scalar(select(SiteMedia).where(SiteMedia.id == media_id,
        SiteMedia.tenant_id == site.tenant_id, SiteMedia.site_id == site.id))
    if row is None:
        raise CommerceError("Media not found in this site.")
    return row


def documents(db, site):
    yield "Site settings", site.settings_json
    yield "Published settings", site.published_settings_json
    for page in content.site_pages(db, site):
        yield f"Draft: {page.path}", page.draft_json
        yield f"Published: {page.path}", page.published_json
    for model, fields, label in (
        (SiteRevision, ("content_json",), "Revision"),
        (SiteMenu, ("items_json", "published_items_json"), "Menu"),
        (SiteChangeSet, ("before_json", "after_json"), "Builder history"),
    ):
        for row in db.scalars(select(model).where(model.tenant_id == site.tenant_id, model.site_id == site.id)):
            for field in fields:
                yield f"{label}: {row.id}", getattr(row, field)
    for product in db.scalars(select(Product).where(Product.tenant_id == site.tenant_id)):
        yield f"Product: {product.id}", product.image_url


def strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)
    elif isinstance(value, str):
        yield value


def references(db, site, media):
    targets = {media_url(media), f"/site-media/{site.id}/{media.id}"}
    return sorted({label for label, doc in documents(db, site) if targets.intersection(strings(doc))})


def delete_media(db, site, media_id):
    media = owned_media(db, site, media_id)
    used = references(db, site, media)
    if used:
        raise CommerceError("Cannot delete referenced media. Used in " + "; ".join(used[:5]))
    db.delete(media)
    db.flush()


def media_fields(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"image", "gallery", "poster", "video", "mobile_video"}:
                for url in strings(child):
                    yield url, value.get("alt", "")
            else:
                yield from media_fields(child)
    elif isinstance(value, list):
        for child in value:
            yield from media_fields(child)


def backfill_media(db, site):
    known = {media_url(row) for row in media_for(db, site)}
    root = Path(__file__).resolve().parents[1] / "static"
    count = 0
    for _, doc in documents(db, site):
        for url, alt in media_fields(doc):
            if not url or url in known or len(url) > 2048:
                continue
            try:
                content.safe_url(url, media=True)
                content.validate_media_ownership(db, site, url)
            except CommerceError:
                continue
            # Existing uploads already have rows; never alias an unknown upload.
            if url.startswith("/site-media/"):
                continue
            size = 0
            if url.startswith("/static/"):
                path = (root / url.removeprefix("/static/")).resolve()
                if root not in path.parents:
                    continue
                if path.is_file():
                    size = path.stat().st_size
            alt = {locale: text[:400] for locale, text in alt.items()} if isinstance(alt, dict) else alt[:400]
            db.add(SiteMedia(tenant_id=site.tenant_id, site_id=site.id,
                public_url=url, title=url.rsplit("/", 1)[-1][:200], alt=alt if isinstance(alt, str) else "",
                localized_alt=alt, content_type=mimetypes.guess_type(url)[0] or "application/octet-stream",
                storage_key=url[:240], size=size, is_placeholder="placeholder" in url))
            known.add(url)
            count += 1
    db.flush()
    return count

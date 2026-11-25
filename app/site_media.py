"""Site-owned media registry. Content URLs are never rewritten by registration."""
import io
import mimetypes
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import defer

from app import content
from app.models import Product, ProductImage, SiteChangeSet, SiteMedia, SiteMenu, SiteRevision
from app.services import CommerceError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


def prepare_image_bytes(data: bytes, *, max_size=(1800, 1800)) -> bytes:
    """Validate and normalize trusted-boundary image bytes like merchant uploads."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise CommerceError("Images must be smaller than 8 MB.")
    try:
        with Image.open(io.BytesIO(data)) as original:
            if (
                original.format not in {"JPEG", "PNG", "WEBP"}
                or original.width * original.height > MAX_IMAGE_PIXELS
            ):
                raise CommerceError(
                    "Choose a JPEG, PNG or WebP image smaller than 25 megapixels."
                )
            original.thumbnail(max_size)
            output = io.BytesIO()
            original.convert("RGB").save(output, format="WEBP", quality=85)
            return output.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise CommerceError("Invalid image data.") from exc


def media_url(media):
    return media.public_url or f"/site-media/{media.site_id}/{media.id}"


def media_for(db, site):
    # The image bytes load only when a caller reads them, so listing a large library stays fast.
    return list(db.scalars(select(SiteMedia).options(defer(SiteMedia.data)).where(
        SiteMedia.tenant_id == site.tenant_id, SiteMedia.site_id == site.id,
    ).order_by(SiteMedia.created_at.desc(), SiteMedia.id)))


_THUMBNAILS: dict = {}


def thumbnail_bytes(media, width: int) -> bytes:
    """A small WebP preview of stored image bytes, cached in memory by media id and width."""
    key = (media.id, media.size, width)
    if key not in _THUMBNAILS:
        with Image.open(io.BytesIO(media.data)) as original:
            original.thumbnail((width, width))
            output = io.BytesIO()
            original.convert("RGB").save(output, format="WEBP", quality=80)
        if len(_THUMBNAILS) > 512:
            _THUMBNAILS.clear()
        _THUMBNAILS[key] = output.getvalue()
    return _THUMBNAILS[key]


def generated_direction(site, url: str) -> str:
    """Return the retained generation direction for one resolved asset URL."""
    generation = (site.settings_json or {}).get("site_generation", {})
    imagery = generation.get("imagery", []) if isinstance(generation, dict) else []
    return next((
        str(item.get("direction", ""))
        for item in imagery
        if isinstance(item, dict) and item.get("resolved_url") == url
    ), "")


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
    for image in db.scalars(select(ProductImage).where(ProductImage.tenant_id == site.tenant_id)):
        yield f"Product gallery: {image.product_id}", image.image_url


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


def reference_map(db, site, library):
    """references() for many files in one pass over the site's documents."""
    owners = {}
    for media in library:
        for target in (media_url(media), f"/site-media/{site.id}/{media.id}"):
            owners.setdefault(target, set()).add(media.id)
    found = {media.id: set() for media in library}
    for label, doc in documents(db, site):
        for value in set(strings(doc)):
            for media_id in owners.get(value, ()):
                found[media_id].add(label)
    return {media_id: sorted(labels) for media_id, labels in found.items()}


def usage_summary(labels):
    """Plain-language usage for the files list: live content first, editor history last."""
    pages = sorted({label.split(": ", 1)[1] for label in labels if label.startswith(("Draft: ", "Published: "))})
    products = {label.split(": ", 1)[1] for label in labels if label.startswith(("Product: ", "Product gallery: "))}
    parts = []
    if pages:
        parts.append(f"{len(pages)} page" + ("" if len(pages) == 1 else "s"))
    if products:
        parts.append(f"{len(products)} product" + ("" if len(products) == 1 else "s"))
    if any(label.startswith("Menu: ") for label in labels):
        parts.append("menus")
    if any(label in {"Site settings", "Published settings"} for label in labels):
        parts.append("store design")
    if parts:
        return "Used in " + ", ".join(parts), pages
    if labels:
        return "Only in editor history", pages
    return "Not used", pages


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

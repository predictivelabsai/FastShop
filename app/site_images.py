"""Bounded resolution of generation-time imagery into site-owned media."""

from __future__ import annotations

import copy
import hashlib
import io
import mimetypes
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

from PIL import Image, ImageColor, ImageDraw
from sqlalchemy import select

from app import content
from app.config import settings
from app.models import SiteMedia
from app.services import CommerceError
from app.site_blocks import default_locale, normalize_document, resolve_text
from app.site_media import media_url, prepare_image_bytes
from app.site_theme import DEFAULTS, validate_theme

MAX_IMAGES_PER_RESOLUTION = 60
IMAGE_SIZES = {
    "hero": (1600, 960),
    "split": (1200, 900),
    "product": (1200, 1200),
}
ProviderResult = bytes | str


class SiteImageProvider(Protocol):
    """Operator-selected image source. Implementations must keep work bounded."""

    def generate(self, direction: str, size: tuple[int, int], key: str) -> ProviderResult:
        ...


@dataclass(frozen=True)
class ImageProviderContext:
    site_id: str
    theme: dict


ProviderFactory = Callable[[ImageProviderContext], SiteImageProvider]
_PROVIDERS: dict[str, ProviderFactory] = {}


def register_image_provider(name: str, factory: ProviderFactory) -> None:
    """Register an operator-controlled provider name at application startup."""
    name = str(name).strip().lower()
    if not name or not name.replace("-", "").isalnum() or not callable(factory):
        raise ValueError("Image providers need a simple name and callable factory.")
    _PROVIDERS[name] = factory


def image_provider(name: str | None = None, *, theme: dict | None = None,
                   site_id: str = "") -> tuple[str, SiteImageProvider]:
    """Resolve the configured provider without accepting merchant-supplied selection."""
    selected = settings.image_provider if name is None else str(name).strip().lower()
    factory = _PROVIDERS.get(selected)
    if factory is None:
        raise CommerceError(f"Image provider {selected!r} is not registered.")
    context = ImageProviderContext(site_id=site_id, theme=validate_theme(theme or DEFAULTS))
    return selected, factory(context)


class PlaceholderImageProvider:
    """Deterministic, theme-aware WebP artwork with no network or credentials."""

    def __init__(self, context: ImageProviderContext):
        self.theme = validate_theme(context.theme)

    def generate(self, direction: str, size: tuple[int, int], key: str) -> bytes:
        width, height = size
        if not (320 <= width <= 2400 and 320 <= height <= 2400):
            raise CommerceError("Image dimensions are outside the supported range.")
        seed = int(hashlib.sha256(f"{key}:{direction}".encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        background = ImageColor.getrgb(self.theme["background"])
        surface = ImageColor.getrgb(self.theme["surface"])
        accent = ImageColor.getrgb(self.theme["accent"])
        strip = Image.new("RGB", (1, 256))
        pixels = strip.load()
        for y in range(256):
            blend = y / 255
            pixels[0, y] = tuple(
                round(background[channel] * (1 - blend) + surface[channel] * blend)
                for channel in range(3)
            )
        canvas = strip.resize((width, height))
        draw = ImageDraw.Draw(canvas, "RGBA")
        for index in range(5):
            radius = int(min(width, height) * rng.uniform(0.14, 0.36))
            x = rng.randint(-radius // 2, width - radius // 2)
            y = rng.randint(-radius // 2, height - radius // 2)
            alpha = 18 + index * 8
            draw.ellipse((x, y, x + radius * 2, y + radius * 2), fill=(*accent, alpha))
        spacing = max(36, min(width, height) // 12)
        offset = seed % spacing
        for x in range(-height, width, spacing):
            draw.line((x + offset, height, x + height + offset, 0), fill=(*accent, 18), width=2)
        output = io.BytesIO()
        canvas.save(output, format="WEBP", quality=84, method=4)
        return output.getvalue()


register_image_provider("placeholders", PlaceholderImageProvider)


@dataclass(frozen=True)
class ResolutionResult:
    total: int
    resolved: int
    reused: int
    preserved: int
    provider_calls: int


def _image_key(site, page_path: str, block_id: str) -> str:
    value = f"v1:{site.id}:{page_path}:{block_id}"
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _storage_key(key: str) -> str:
    return f"generated/site-image-{key}.webp"


def _title(direction: str) -> str:
    return ("Image direction · " + direction.strip())[:200]


def _localized_alt(block: dict, direction: str, locale: str) -> dict[str, str]:
    value = block.get("alt")
    if isinstance(value, dict):
        result = {key: str(text).strip()[:400] for key, text in value.items() if str(text).strip()}
        if result:
            return result
    elif isinstance(value, str) and value.strip():
        return {locale: value.strip()[:400]}
    return {locale: direction.strip()[:400]}


def _upsert_bytes(db, site, *, key: str, direction: str, localized_alt: dict, data: bytes,
                  placeholder: bool) -> SiteMedia:
    storage_key = _storage_key(key)
    row = db.scalar(select(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.site_id == site.id,
        SiteMedia.storage_key == storage_key,
    ))
    data = prepare_image_bytes(data)
    alt = resolve_text(localized_alt, default_locale(site)) or next(iter(localized_alt.values()), "")
    if row is None:
        row = SiteMedia(
            tenant_id=site.tenant_id,
            site_id=site.id,
            title=_title(direction),
            alt=alt[:400],
            localized_alt=localized_alt,
            content_type="image/webp",
            storage_key=storage_key,
            is_placeholder=placeholder,
            size=len(data),
            data=data,
        )
        db.add(row)
    else:
        row.title = _title(direction)
        row.alt = alt[:400]
        row.localized_alt = localized_alt
        row.public_url = None
        row.content_type = "image/webp"
        row.is_placeholder = placeholder
        row.size = len(data)
        row.data = data
    db.flush()
    return row


def _upsert_remote(db, site, *, key: str, direction: str, localized_alt: dict,
                   url: str, placeholder: bool) -> SiteMedia:
    storage_key = _storage_key(key)
    row = db.scalar(select(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.site_id == site.id,
        SiteMedia.storage_key == storage_key,
    )) or db.scalar(select(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.site_id == site.id,
        SiteMedia.public_url == url,
    ))
    alt = resolve_text(localized_alt, default_locale(site)) or next(iter(localized_alt.values()), "")
    content_type = mimetypes.guess_type(urlsplit(url).path)[0] or "image/remote"
    if not content_type.startswith("image/"):
        raise CommerceError("Image providers must return an image URL.")
    if row is None:
        row = SiteMedia(
            tenant_id=site.tenant_id,
            site_id=site.id,
            title=_title(direction),
            alt=alt[:400],
            localized_alt=localized_alt,
            public_url=url,
            content_type=content_type,
            storage_key=storage_key,
            is_placeholder=placeholder,
            size=0,
            data=None,
        )
        db.add(row)
    else:
        row.title = _title(direction)
        row.alt = alt[:400]
        row.localized_alt = localized_alt
        row.public_url = url
        row.content_type = content_type
        row.is_placeholder = placeholder
        row.size = 0
        row.data = None
    db.flush()
    return row


def resolve_site_imagery(db, site, user_id: str, *, provider: SiteImageProvider | None = None,
                         provider_name: str | None = None) -> ResolutionResult:
    """Resolve generated visual blocks without replacing merchant-chosen imagery."""
    site = content.owned_site(db, site.id, user_id)
    config = copy.deepcopy(site.settings_json or {})
    generation = config.get("site_generation")
    imagery = generation.get("imagery") if isinstance(generation, dict) else None
    if not isinstance(imagery, list) or not imagery:
        raise CommerceError("This site has no generated imagery directions to resolve.")
    if len(imagery) > MAX_IMAGES_PER_RESOLUTION:
        raise CommerceError(
            f"Resolve at most {MAX_IMAGES_PER_RESOLUTION} generated images at a time."
        )
    theme = validate_theme(config.get("design", DEFAULTS))
    if provider is None:
        selected_name, provider = image_provider(provider_name, theme=theme, site_id=site.id)
    else:
        selected_name = provider_name or "injected"
    pages = {page.path: page for page in content.site_pages(db, site)}
    documents = {path: normalize_document(page.draft_json) for path, page in pages.items()}
    changed_pages: set[str] = set()
    resolved = reused = preserved = provider_calls = 0
    now = datetime.now(UTC).isoformat()

    for item in imagery:
        if not isinstance(item, dict):
            raise CommerceError("Generated imagery directions are invalid.")
        page_path = str(item.get("page_path", ""))
        block_id = str(item.get("block_id", ""))
        direction = str(item.get("direction", "")).strip()
        page = pages.get(page_path)
        document = documents.get(page_path)
        block = next(
            (candidate for candidate in document["blocks"] if candidate["id"] == block_id),
            None,
        ) if document else None
        if page is None or block is None or block["type"] not in IMAGE_SIZES or not direction:
            raise CommerceError("Generated imagery no longer matches this site's visual blocks.")
        current = resolve_text(block.get("image", ""), default_locale(site))
        previous = str(item.get("resolved_url", ""))
        if current and (current != previous or item.get("status") == "preserved"):
            content.safe_url(current, media=True)
            content.validate_media_ownership(db, site, current)
            item.update(status="preserved", resolved_url=current, resolved_at=now)
            preserved += 1
            continue

        key = _image_key(site, page_path, block_id)
        localized_alt = _localized_alt(block, direction, default_locale(site))
        existing = db.scalar(select(SiteMedia).where(
            SiteMedia.tenant_id == site.tenant_id,
            SiteMedia.site_id == site.id,
            SiteMedia.storage_key == _storage_key(key),
        ))
        target = previous
        if existing is not None and existing.title == _title(direction):
            existing.alt = resolve_text(localized_alt, default_locale(site))[:400]
            existing.localized_alt = localized_alt
            target = media_url(existing)
            reused += 1
        elif item.get("placeholder_url"):
            target = content.safe_url(str(item["placeholder_url"]), media=True)
            reused += 1
        elif previous:
            target = content.safe_url(previous, media=True)
            content.validate_media_ownership(db, site, target)
            reused += 1
        else:
            output = provider.generate(direction, IMAGE_SIZES[block["type"]], key)
            provider_calls += 1
            if isinstance(output, bytes):
                target = media_url(_upsert_bytes(
                    db, site, key=key, direction=direction, localized_alt=localized_alt,
                    data=output, placeholder=selected_name == "placeholders",
                ))
            elif isinstance(output, str):
                target = content.safe_url(output, media=True)
                if target.startswith("/static/") and "placeholder" in target.lower():
                    pass
                elif target.startswith("/site-media/"):
                    content.validate_media_ownership(db, site, target)
                else:
                    target = media_url(_upsert_remote(
                        db, site, key=key, direction=direction, localized_alt=localized_alt,
                        url=target, placeholder="placeholder" in target.lower(),
                    ))
            else:
                raise CommerceError("Image providers must return image bytes or a safe URL.")
            resolved += 1
        content.validate_media_ownership(db, site, target)
        localized_target = {default_locale(site): target}
        if block.get("image") != localized_target:
            block["image"] = localized_target
            changed_pages.add(page_path)
        resolved_at = (
            item.get("resolved_at", now)
            if current == target and item.get("status") == "resolved"
            else now
        )
        item.update(
            key=key,
            provider=selected_name,
            resolved_url=target,
            resolved_at=resolved_at,
            status="resolved",
        )

    for path in sorted(changed_pages):
        page = pages[path]
        content.save_page(db, site, page.id, user_id, documents[path], page.version)
    generation["imagery"] = imagery
    generation["image_provider"] = selected_name
    generation["imagery_resolved_at"] = generation.get("imagery_resolved_at", now)
    site.settings_json = config
    content.validate_media_ownership(db, site, config)
    db.flush()
    return ResolutionResult(
        total=len(imagery),
        resolved=resolved,
        reused=reused,
        preserved=preserved,
        provider_calls=provider_calls,
    )

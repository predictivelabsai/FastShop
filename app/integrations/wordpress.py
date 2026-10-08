"""Bounded WordPress REST import and review-only WXR export.

WordPress content is untrusted input. The connector converts an allowlisted HTML
subset into FastShop's canonical blocks, registers remote HTTPS media without
downloading it, and only applies the exact stored dry-run payload.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.dom import minidom

import httpx
from sqlalchemy import select

from app import content
from app.connectors import ConnectorCapabilities, CredentialState, ExportArtifact
from app.models import BlogCategory, ExternalMapping, SiteMedia, SitePage
from app.services import CommerceError
from app.site_blocks import default_locale, normalize_document, resolve_text
from app.site_blog import ensure_category, metadata, taxonomy_slug
from app.site_media import media_url

PAGE_SIZE = 50
MAX_PAGES = 5
MAX_ITEMS = 750
MAX_REQUESTS = 40
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 2_000_000
MAX_HTML_BYTES = 300_000
MAX_WXR_ITEMS = 500
PLAN_VERSION = 1
SYSTEM = "wordpress"
RESOURCES = ("categories", "tags", "users", "media", "posts", "pages")
_RESOURCE = re.compile(r"categories|tags|users|media|posts|pages")
_SHORTCODE = re.compile(r"\[[A-Za-z][^\]\r\n]{0,500}\]")
_SKIP_CONTENT = {"script", "style"}
_ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "a", "img", "blockquote", "strong", "em", "br",
}
_PAGE_KINDS = {"home", "content", "science", "blog", "contact", "legal"}
_WXR_NAMESPACES = {
    "excerpt": "http://wordpress.org/export/1.2/excerpt/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "wfw": "http://wellformedweb.org/CommentAPI/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "wp": "http://wordpress.org/export/1.2/",
}


@dataclass(frozen=True)
class _WordPressConfig:
    base_url: str
    username: str
    application_password: str
    fixture: Path | None = None


def _prefix(site) -> str:
    return f"FASTSHOP_WORDPRESS_{site.id.upper()}_"


def _valid_rest_base(value: str) -> bool:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or path != "/wp-json/wp/v2"
        or host == "localhost"
        or "." not in host
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return address.is_global


def _config(site, *, allow_incomplete: bool = False) -> _WordPressConfig:
    prefix = _prefix(site)
    fixture_value = os.getenv("FASTSHOP_WORDPRESS_FIXTURE_PATH", "").strip()
    fixture = None
    if fixture_value and os.getenv("FASTSHOP_ENV", "development").lower() != "production":
        fixture = Path(fixture_value).resolve()
    base_url = os.getenv(prefix + "REST_BASE_URL", "").rstrip("/")
    if fixture and not base_url:
        base_url = "https://fixture.wordpress.test/wp-json/wp/v2"
    config = _WordPressConfig(
        base_url=base_url,
        username=os.getenv(prefix + "USERNAME", "").strip(),
        application_password=os.getenv(prefix + "APPLICATION_PASSWORD", ""),
        fixture=fixture,
    )
    if allow_incomplete:
        return config
    enabled = os.getenv(prefix + "ENABLED", "").lower() == "true" or fixture is not None
    if not enabled:
        raise CommerceError("WordPress integration is disabled for this site.")
    if not _valid_rest_base(config.base_url):
        raise CommerceError("Configure a public HTTPS WordPress REST v2 base URL.")
    if bool(config.username) != bool(config.application_password):
        raise CommerceError("Configure both the WordPress username and application password, or neither.")
    if fixture is not None and (not fixture.is_file() or fixture.suffix.lower() != ".json"):
        raise CommerceError("The development WordPress fixture is unavailable.")
    return config


def _version(item: dict) -> str:
    supplied = str(item.get("modified_gmt") or item.get("date_gmt") or "")
    if supplied:
        return supplied[:80]
    encoded = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:32]


def _external_id(item: dict, resource: str) -> str:
    value = item.get("id")
    if type(value) is not int or value < 1:
        raise CommerceError(f"WordPress returned {resource} without a valid id.")
    return str(value)


def _rendered(value) -> str:
    if isinstance(value, dict):
        value = value.get("rendered", "")
    return str(value or "")


class _PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_CONTENT:
            self.suppressed += 1
        elif not self.suppressed and tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in _SKIP_CONTENT and self.suppressed:
            self.suppressed -= 1
        elif not self.suppressed and tag.lower() in {"p", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(data)


def plain_text(value, limit=30_000) -> str:
    parser = _PlainTextParser()
    try:
        parser.feed(_rendered(value)[:MAX_HTML_BYTES])
        parser.close()
    except (TypeError, ValueError):
        return ""
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()[:limit]


def _safe_media_url(value: str) -> str:
    value = str(value or "").strip()
    try:
        safe = content.safe_url(value, media=True)
    except CommerceError:
        return ""
    parsed = urlsplit(safe)
    if parsed.scheme == "https" or safe.startswith(("/site-media/", "/static/")):
        return safe
    return ""


class _WPHTMLParser(HTMLParser):
    """Lossy allowlist conversion. It never emits source HTML into a block."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.media: list[dict] = []
        self.warnings: list[str] = []
        self.buffer: list[str] = []
        self.mode = "body"
        self.list_kind = ""
        self.list_items: list[str] = []
        self.anchor_href = ""
        self.anchor_start = 0
        self.suppressed = 0

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _clean(self) -> str:
        return re.sub(r"[ \t\f\v]+", " ", "".join(self.buffer)).strip()

    def _add_text(self, *, heading=False) -> None:
        text = self._clean()
        self.buffer.clear()
        if text:
            self.blocks.append({"type": "text", "heading" if heading else "body": text})

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag in _SKIP_CONTENT:
            self.suppressed += 1
            self._warn(f"Removed unsupported <{tag}> content.")
            return
        if self.suppressed:
            return
        if tag not in _ALLOWED_TAGS:
            self._warn(f"Flattened unsupported <{tag}> markup to text.")
            return
        if tag.startswith("h") and len(tag) == 2:
            self._add_text(heading=self.mode == "heading")
            self.mode = "heading"
        elif tag in {"p", "blockquote"}:
            self._add_text(heading=self.mode == "heading")
            self.mode = "body"
        elif tag in {"ul", "ol"}:
            self._add_text(heading=self.mode == "heading")
            self.list_kind, self.list_items = tag, []
        elif tag == "li":
            self.buffer.clear()
            self.mode = "list"
        elif tag == "br":
            self.buffer.append("\n")
        elif tag == "a":
            href = attrs.get("href", "")
            try:
                href = content.safe_url(href) if href else ""
            except CommerceError:
                href = ""
                self._warn("Removed an unsafe link URL.")
            self.anchor_href, self.anchor_start = href, len(self.buffer)
        elif tag == "img":
            src = _safe_media_url(attrs.get("src", ""))
            if not src:
                self._warn("Removed an image with an unsafe or unsupported URL.")
                return
            self._add_text(heading=self.mode == "heading")
            alt = plain_text(attrs.get("alt", ""), 400)
            self.blocks.append({"type": "split", "image": src, "alt": alt})
            self.media.append({"url": src, "alt": alt, "title": plain_text(attrs.get("title", ""), 200)})

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_CONTENT and self.suppressed:
            self.suppressed -= 1
            return
        if self.suppressed:
            return
        if tag == "a" and self.anchor_href:
            label = "".join(self.buffer[self.anchor_start:]).strip()
            if self.anchor_href not in label:
                self.buffer.append(f" ({self.anchor_href})")
            self.anchor_href = ""
        elif tag == "li" and self.list_kind:
            text = self._clean()
            if text:
                self.list_items.append(text)
            self.buffer.clear()
        elif tag in {"ul", "ol"} and self.list_kind:
            prefix = (lambda index: f"{index + 1}. ") if self.list_kind == "ol" else (lambda index: "• ")
            body = "\n".join(prefix(index) + value for index, value in enumerate(self.list_items))
            if body:
                self.blocks.append({"type": "text", "body": body})
            self.list_kind, self.list_items, self.mode = "", [], "body"
        elif tag.startswith("h") and len(tag) == 2:
            self._add_text(heading=True)
            self.mode = "body"
        elif tag in {"p", "blockquote"}:
            self._add_text()

    def handle_data(self, data):
        if self.suppressed:
            return
        shortcodes = _SHORTCODE.findall(data)
        if shortcodes:
            self._warn("Removed WordPress shortcode markup; surrounding text was retained.")
            data = _SHORTCODE.sub("", data)
        self.buffer.append(data)

    def finish(self):
        self._add_text(heading=self.mode == "heading")
        if len(self.blocks) > 60:
            self.blocks = self.blocks[:60]
            self._warn("Content exceeded FastShop's 60-block page limit and was truncated.")


def html_to_blocks(value: str) -> tuple[list[dict], list[dict], list[str]]:
    encoded = str(value or "").encode("utf-8")
    if len(encoded) > MAX_HTML_BYTES:
        raise CommerceError("WordPress content exceeds the 300 KB per-item HTML limit.")
    parser = _WPHTMLParser()
    try:
        parser.feed(str(value or ""))
        parser.close()
    except (TypeError, ValueError):
        raise CommerceError("WordPress returned malformed HTML content.") from None
    parser.finish()
    blocks = []
    for index, block in enumerate(parser.blocks):
        digest = hashlib.sha256(
            json.dumps(block, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()[:16]
        blocks.append({"id": f"wp-{index}-{digest}", "version": 1, **block})
    return blocks, parser.media, parser.warnings


def _fixture_transport(path: Path) -> httpx.MockTransport:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CommerceError("The development WordPress fixture is invalid.") from None
    if not isinstance(data, dict) or any(not isinstance(data.get(name), list) for name in RESOURCES):
        raise CommerceError("The development WordPress fixture is invalid.")

    def respond(request):
        resource = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        rows = data.get(resource)
        if rows is None:
            return httpx.Response(404, json={"code": "not_found"})
        page = int(request.url.params.get("page", "1"))
        per_page = int(request.url.params.get("per_page", str(PAGE_SIZE)))
        start = (page - 1) * per_page
        total_pages = max(1, (len(rows) + per_page - 1) // per_page)
        return httpx.Response(
            200,
            json=rows[start:start + per_page],
            headers={"X-WP-TotalPages": str(total_pages), "X-WP-Total": str(len(rows))},
        )

    return httpx.MockTransport(respond)


class WordPressGateway:
    def __init__(self, site, *, tenant_id, transport=None):
        if not tenant_id or site.tenant_id != tenant_id:
            raise CommerceError("Site not found.")
        config = _config(site)
        self._base_url = config.base_url
        self._auth = (
            httpx.BasicAuth(config.username, config.application_password)
            if config.username else None
        )
        self._transport = transport or (_fixture_transport(config.fixture) if config.fixture else None)
        self._requests = 0

    def list_resources(self, resource: str, *, page=1, per_page=PAGE_SIZE):
        if not _RESOURCE.fullmatch(resource):
            raise CommerceError("Unsupported WordPress resource.")
        if type(page) is not int or page < 1 or type(per_page) is not int or not 1 <= per_page <= 100:
            raise CommerceError("Invalid WordPress pagination.")
        self._requests += 1
        if self._requests > MAX_REQUESTS:
            raise CommerceError("WordPress request limit reached; narrow the bounded import.")
        try:
            params = {"page": page, "per_page": per_page, "context": "view"}
            if self._auth is not None and resource in {"posts", "pages"}:
                params.update(
                    context="edit",
                    status="publish,draft,pending,private,future",
                )
            with httpx.Client(
                timeout=TIMEOUT_SECONDS,
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
                auth=self._auth,
            ) as client:
                response = client.get(
                    self._base_url + "/" + resource,
                    params=params,
                )
                response.raise_for_status()
                if len(response.content) > MAX_RESPONSE_BYTES:
                    raise ValueError
                result = response.json()
                if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
                    raise ValueError
                try:
                    total_pages = int(response.headers.get("X-WP-TotalPages", "0"))
                except ValueError:
                    total_pages = 0
                return result, total_pages
        except (httpx.HTTPError, ValueError):
            raise CommerceError(
                "WordPress read failed; check the REST base URL, visibility and application password."
            ) from None

    def paged(self, resource: str) -> list[dict]:
        rows = []
        for page in range(1, MAX_PAGES + 1):
            batch, total_pages = self.list_resources(resource, page=page)
            if total_pages > MAX_PAGES:
                raise CommerceError(
                    f"WordPress {resource} exceeded the {MAX_PAGES}-page import limit."
                )
            rows.extend(batch)
            if len(batch) < PAGE_SIZE or (total_pages and page >= total_pages):
                break
        else:
            raise CommerceError(
                f"WordPress {resource} exceeded the {MAX_PAGES}-page import limit."
            )
        return rows


def _mapping(db, site, resource_type: str, external_id) -> ExternalMapping | None:
    return db.scalar(select(ExternalMapping).where(
        ExternalMapping.tenant_id == site.tenant_id,
        ExternalMapping.site_id == site.id,
        ExternalMapping.system == SYSTEM,
        ExternalMapping.resource_type == resource_type,
        ExternalMapping.external_id == str(external_id),
    ))


def _map(db, site, resource_type: str, external_id, local_id: str, version: str):
    mapping = _mapping(db, site, resource_type, external_id)
    if mapping is None:
        mapping = ExternalMapping(
            tenant_id=site.tenant_id,
            site_id=site.id,
            system=SYSTEM,
            resource_type=resource_type,
            external_id=str(external_id),
            local_id=local_id,
        )
        db.add(mapping)
    mapping.local_id, mapping.version = local_id, str(version)[:80]
    db.flush()
    return mapping


def _action(db, site, resource_type, external_id) -> str:
    return "update" if _mapping(db, site, resource_type, external_id) else "create"


def _media_action(db, site, row: dict) -> str:
    if _mapping(db, site, "media", row["external_id"]):
        return "update"
    existing = db.scalar(select(SiteMedia.id).where(
        SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.site_id == site.id,
        SiteMedia.public_url == row["url"],
    ))
    return "update" if existing else "create"


def _normalize_term(item: dict, resource: str) -> dict:
    external_id = _external_id(item, resource)
    name = plain_text(item.get("name"), 100)
    if not name:
        raise CommerceError(f"WordPress returned {resource} without a name.")
    return {
        "external_id": external_id,
        "name": name,
        "slug": taxonomy_slug(item.get("slug") or name),
        "version": _version(item),
    }


def _normalize_author(item: dict) -> dict:
    external_id = _external_id(item, "author")
    name = plain_text(item.get("name"), 160)
    if not name:
        raise CommerceError("WordPress returned an author without a name.")
    return {"external_id": external_id, "name": name, "version": _version(item)}


def _normalize_media(item: dict) -> dict:
    external_id = _external_id(item, "media item")
    url = _safe_media_url(item.get("source_url", ""))
    if not url or urlsplit(url).scheme != "https":
        raise CommerceError("media URL is not public HTTPS")
    mime = str(item.get("mime_type") or mimetypes.guess_type(url)[0] or "application/octet-stream")
    if len(mime) > 80:
        mime = "application/octet-stream"
    return {
        "external_id": external_id,
        "url": url,
        "title": plain_text(item.get("title"), 200) or url.rsplit("/", 1)[-1][:200],
        "alt": plain_text(item.get("alt_text"), 400),
        "content_type": mime,
        "version": _version(item),
    }


def _normalize_entry(
    item: dict,
    resource_type: str,
    categories: dict[str, dict],
    tags: dict[str, dict],
    authors: dict[str, dict],
    media: dict[str, dict],
) -> tuple[dict, list[dict], list[str]]:
    external_id = _external_id(item, resource_type)
    title = plain_text(item.get("title"), 240) or f"Untitled WordPress {resource_type} {external_id}"
    source_slug = taxonomy_slug(item.get("slug") or title)
    blocks, inline_media, warnings = html_to_blocks(_rendered(item.get("content")))
    category_ids = [str(value) for value in item.get("categories", []) if type(value) is int]
    tag_ids = [str(value) for value in item.get("tags", []) if type(value) is int]
    category = next((categories[value] for value in category_ids if value in categories), None)
    tag_slugs = [tags[value]["slug"] for value in tag_ids if value in tags]
    if len(tag_slugs) > 20:
        warnings.append(f"WordPress {resource_type} #{external_id} had more than 20 tags; extras were omitted.")
        tag_slugs = tag_slugs[:20]
    author = authors.get(str(item.get("author", "")))
    featured = media.get(str(item.get("featured_media", "")))
    if item.get("featured_media") and not featured:
        warnings.append(f"WordPress {resource_type} #{external_id} referenced unavailable featured media.")
    status = "published" if item.get("status") == "publish" else "draft"
    document = {
        "version": 1,
        "title": title,
        "description": plain_text(item.get("excerpt"), 30_000),
        "blocks": blocks,
    }
    if featured:
        document["image"] = featured["url"]
    if resource_type == "post":
        document["category"] = category["name"] if category else "WordPress import"
        document["blog"] = {
            "state": status,
            "category_slug": category["slug"] if category else "wordpress-import",
            "tags": list(dict.fromkeys(tag_slugs)),
            "author_name": author["name"] if author else "",
            "author_bio": "",
        }
    document = normalize_document(document)
    return ({
        "external_id": external_id,
        "title": title,
        "slug": source_slug,
        "path": ("/blogs/learn/" if resource_type == "post" else "/pages/") + source_slug,
        "status": status,
        "category_external_id": category["external_id"] if category else "",
        "document": document,
        "version": _version(item),
    }, inline_media, warnings)


def _inline_media(row: dict) -> dict:
    url = row["url"]
    digest = hashlib.sha256(url.encode()).hexdigest()[:32]
    return {
        "external_id": "inline:" + digest,
        "url": url,
        "title": row.get("title") or url.rsplit("/", 1)[-1][:200],
        "alt": row.get("alt", "")[:400],
        "content_type": mimetypes.guess_type(url)[0] or "application/octet-stream",
        "version": digest,
    }


def _unique_page_path(db, site, requested: str, external_id: str) -> str:
    used = db.scalar(select(SitePage.id).where(
        SitePage.tenant_id == site.tenant_id,
        SitePage.site_id == site.id,
        SitePage.path == requested,
    ))
    if not used:
        return requested
    suffix = "-wordpress-" + external_id
    return requested[:240 - len(suffix)].rstrip("-") + suffix


def _unique_category_slug(db, site, requested: str, external_id: str) -> str:
    used = db.scalar(select(BlogCategory.id).where(
        BlogCategory.tenant_id == site.tenant_id,
        BlogCategory.site_id == site.id,
        BlogCategory.slug == requested,
    ))
    if not used:
        return requested
    suffix = "-wordpress-" + external_id
    return requested[:80 - len(suffix)].rstrip("-") + suffix


class WordPressConnector:
    platform = SYSTEM
    label = "WordPress"
    capabilities = ConnectorCapabilities(
        imports=("posts", "pages", "categories", "tags", "authors", "featured media"),
        exports=("WXR XML (published content or published plus drafts)",),
        max_pages=MAX_PAGES,
        max_items=MAX_ITEMS,
        timeout_seconds=TIMEOUT_SECONDS,
    )

    def credential_state(self, site) -> CredentialState:
        config = _config(site, allow_incomplete=True)
        fixture_ready = config.fixture is not None and config.fixture.is_file()
        enabled = os.getenv(_prefix(site) + "ENABLED", "").lower() == "true"
        auth_complete = bool(config.username) == bool(config.application_password)
        configured = fixture_ready or (enabled and _valid_rest_base(config.base_url) and auth_complete)
        if fixture_ready:
            message = "Development fixture site ready."
        elif configured and config.username:
            message = "Operator REST URL and application password are configured."
        elif configured:
            message = "Operator REST URL is configured for public read-only access."
        else:
            message = "Ask the operator to configure this site's WordPress REST connection."
        return CredentialState(configured, message, "fixture" if fixture_ready else "sandbox")

    def fetch(self, site, *, transport=None) -> dict:
        gateway = WordPressGateway(site, tenant_id=site.tenant_id, transport=transport)
        result = {resource: gateway.paged(resource) for resource in RESOURCES}
        if sum(len(result[name]) for name in RESOURCES) > MAX_ITEMS:
            raise CommerceError(f"WordPress returned more than the {MAX_ITEMS}-item run limit.")
        return result

    def dry_run(self, db, site, user_id, *, transport=None):
        raw = self.fetch(site, transport=transport)
        warnings = [
            "WordPress content is untrusted text and is revalidated through FastShop block and media boundaries.",
            "WordPress snippets, head scripts and footer scripts are not imported because consent policies differ.",
            "Remote media is registered by public HTTPS URL; no asset bytes are downloaded.",
        ]
        unmapped = []

        def normalized(name, function):
            rows = []
            for item in raw[name]:
                try:
                    rows.append(function(item))
                except CommerceError as exc:
                    unmapped.append({
                        "type": name.rstrip("s"),
                        "external_id": str(item.get("id", "")),
                        "reason": str(exc),
                    })
            return rows

        categories = normalized("categories", lambda row: _normalize_term(row, "category"))
        tags = normalized("tags", lambda row: _normalize_term(row, "tag"))
        authors = normalized("users", _normalize_author)
        media_rows = normalized("media", _normalize_media)
        category_map = {row["external_id"]: row for row in categories}
        tag_map = {row["external_id"]: row for row in tags}
        author_map = {row["external_id"]: row for row in authors}
        media_map = {row["external_id"]: row for row in media_rows}
        entries = {"posts": [], "pages": []}
        inline = []
        for plural, singular in (("posts", "post"), ("pages", "page")):
            for item in raw[plural]:
                try:
                    row, discovered, row_warnings = _normalize_entry(
                        item, singular, category_map, tag_map, author_map, media_map
                    )
                    entries[plural].append(row)
                    inline.extend(discovered)
                    warnings.extend(row_warnings)
                except CommerceError as exc:
                    unmapped.append({
                        "type": singular,
                        "external_id": str(item.get("id", "")),
                        "reason": str(exc),
                    })
        known_urls = {row["url"] for row in media_rows}
        for row in inline:
            if row["url"] not in known_urls:
                media_rows.append(_inline_media(row))
                known_urls.add(row["url"])
        if len(categories) + len(tags) + len(authors) + len(media_rows) + sum(map(len, entries.values())) > MAX_ITEMS:
            raise CommerceError(f"Normalized WordPress content exceeds the {MAX_ITEMS}-item run limit.")

        resources = {
            "categories": categories,
            "tags": tags,
            "authors": authors,
            "media": media_rows,
            **entries,
        }
        singular = {
            "categories": "category", "tags": "tag", "authors": "author",
            "media": "media", "posts": "post", "pages": "page",
        }
        counts, samples = {}, []
        raw_names = {
            "categories": "categories", "tags": "tags", "authors": "users",
            "media": "media", "posts": "posts", "pages": "pages",
        }
        for name, rows in resources.items():
            actions = [
                _media_action(db, site, row)
                if name == "media"
                else _action(db, site, singular[name], row["external_id"])
                for row in rows
            ]
            counts[name] = {
                "fetched": len(raw[raw_names[name]]) if name != "media" else len(media_rows),
                "create": actions.count("create"),
                "update": actions.count("update"),
                "skip": max(0, len(raw[raw_names[name]]) - len(rows)),
            }
            for row, action in list(zip(rows, actions, strict=True))[:3]:
                samples.append({
                    "type": singular[name],
                    "external_id": row["external_id"],
                    "label": row.get("title") or row.get("name") or row.get("url", ""),
                    "action": action,
                })
        payload = {"schema_version": PLAN_VERSION, "platform": SYSTEM, **resources}
        report = {
            "platform": SYSTEM,
            "mode": "dry-run",
            "counts": counts,
            "samples": samples,
            "unmapped_items": unmapped[:100],
            "warnings": list(dict.fromkeys(warnings))[:100],
            "limits": {
                "max_pages": MAX_PAGES,
                "max_items": MAX_ITEMS,
                "max_wxr_items": MAX_WXR_ITEMS,
                "timeout_seconds": TIMEOUT_SECONDS,
            },
        }
        return payload, report

    @staticmethod
    def _validate_payload(payload):
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != PLAN_VERSION
            or payload.get("platform") != SYSTEM
        ):
            raise CommerceError("The reviewed WordPress plan is invalid.")
        total = 0
        for key in ("categories", "tags", "authors", "media", "posts", "pages"):
            if not isinstance(payload.get(key), list):
                raise CommerceError("The reviewed WordPress plan is incomplete.")
            total += len(payload[key])
        if total > MAX_ITEMS:
            raise CommerceError("The reviewed WordPress plan exceeds the item limit.")
        for row in payload["posts"] + payload["pages"]:
            normalize_document(row.get("document"))
        for row in payload["media"]:
            if not _safe_media_url(row.get("url", "")):
                raise CommerceError("The reviewed WordPress plan contains unsafe media.")

    def apply_import(self, db, site, user_id, payload):
        self._validate_payload(payload)
        counts = {
            name: {"created": 0, "updated": 0}
            for name in ("categories", "tags", "authors", "media", "posts", "pages")
        }
        for row in payload["categories"]:
            mapping = _mapping(db, site, "category", row["external_id"])
            category = db.scalar(select(BlogCategory).where(
                BlogCategory.id == mapping.local_id,
                BlogCategory.tenant_id == site.tenant_id,
                BlogCategory.site_id == site.id,
            )) if mapping else None
            action = "updated" if category else "created"
            if category is None:
                category = BlogCategory(
                    tenant_id=site.tenant_id,
                    site_id=site.id,
                    slug=_unique_category_slug(
                        db, site, row["slug"], row["external_id"]
                    ),
                    name=row["name"],
                )
                db.add(category)
                db.flush()
            category.name = row["name"]
            _map(db, site, "category", row["external_id"], category.id, row["version"])
            counts["categories"][action] += 1
        fallback = ensure_category(db, site, "WordPress import")

        for name in ("tags", "authors"):
            singular = name.rstrip("s")
            for row in payload[name]:
                existed = _mapping(db, site, singular, row["external_id"])
                local_id = f"{singular}:{row['external_id']}:{row.get('slug') or row['name']}"[:64]
                _map(db, site, singular, row["external_id"], local_id, row["version"])
                counts[name]["updated" if existed else "created"] += 1

        for row in payload["media"]:
            mapping = _mapping(db, site, "media", row["external_id"])
            media = db.scalar(select(SiteMedia).where(
                SiteMedia.id == mapping.local_id,
                SiteMedia.tenant_id == site.tenant_id,
                SiteMedia.site_id == site.id,
            )) if mapping else None
            if media is None:
                media = db.scalar(select(SiteMedia).where(
                    SiteMedia.tenant_id == site.tenant_id,
                    SiteMedia.site_id == site.id,
                    SiteMedia.public_url == row["url"],
                ))
            action = "updated" if media else "created"
            created = media is None
            if media is None:
                media = SiteMedia(
                    tenant_id=site.tenant_id,
                    site_id=site.id,
                    title=row["title"],
                    alt=row["alt"],
                    public_url=row["url"],
                    localized_alt=row["alt"],
                    content_type=row["content_type"],
                    storage_key=row["url"][:240],
                    size=0,
                    data=None,
                    is_placeholder=False,
                )
                db.add(media)
                db.flush()
            if created or mapping:
                media.title, media.alt = row["title"], row["alt"]
                media.localized_alt, media.content_type = row["alt"], row["content_type"]
            _map(db, site, "media", row["external_id"], media.id, row["version"])
            counts["media"][action] += 1

        for plural, singular, kind in (("posts", "post", "article"), ("pages", "page", "content")):
            for row in payload[plural]:
                mapping = _mapping(db, site, singular, row["external_id"])
                page = db.scalar(select(SitePage).where(
                    SitePage.id == mapping.local_id,
                    SitePage.tenant_id == site.tenant_id,
                    SitePage.site_id == site.id,
                )) if mapping else None
                action = "updated" if page else "created"
                document = row["document"]
                if kind == "article":
                    category = None
                    if row.get("category_external_id"):
                        category_mapping = _mapping(
                            db, site, "category", row["category_external_id"]
                        )
                        if category_mapping:
                            category = db.scalar(select(BlogCategory).where(
                                BlogCategory.id == category_mapping.local_id,
                                BlogCategory.tenant_id == site.tenant_id,
                                BlogCategory.site_id == site.id,
                            ))
                    document = {
                        **document,
                        "blog": {
                            **document["blog"],
                            "category_slug": (category or fallback).slug,
                        },
                    }
                if page is None:
                    page = content.create_page(
                        db,
                        site,
                        row["title"],
                        _unique_page_path(db, site, row["path"], row["external_id"]),
                        kind,
                        document,
                    )
                content.save_page(
                    db,
                    site,
                    page.id,
                    user_id,
                    document,
                    page.version,
                    "publish" if row["status"] == "published" else "unpublish",
                )
                _map(db, site, singular, row["external_id"], page.id, row["version"])
                counts[plural][action] += 1
        db.flush()
        return counts

    def export_bundle(self, db, site):
        return {"format": "WXR", "download": "Use the WordPress XML export action."}

    def export_artifact(self, db, site, *, include_drafts=False) -> ExportArtifact:
        return _wxr_artifact(db, site, include_drafts=include_drafts)


def _append_text(document, parent, name, value):
    node = document.createElement(name)
    node.appendChild(document.createTextNode(str(value)))
    parent.appendChild(node)
    return node


def _append_cdata(document, parent, name, value):
    node = document.createElement(name)
    safe = str(value).replace("]]>", "]]&gt;")
    node.appendChild(document.createCDATASection(safe))
    parent.appendChild(node)
    return node


def _safe_link(value: str) -> str:
    try:
        return content.safe_url(value) if value else ""
    except CommerceError:
        return ""


def _body_html(value: str) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", str(value or "")) if part.strip()]
    return "".join(f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>" for part in paragraphs)


def _absolute_url(value: str, base: str) -> str:
    return base + value if value.startswith("/") else value


def _document_html(document: dict, locale: str, base: str) -> str:
    chunks = []
    for block in normalize_document(document)["blocks"]:
        heading = resolve_text(block.get("heading", ""), locale)
        body = resolve_text(block.get("body", ""), locale)
        if heading:
            chunks.append(f"<h2>{html.escape(heading)}</h2>")
        if body:
            chunks.append(_body_html(body))
        image = resolve_text(block.get("image", ""), locale)
        safe_image = _safe_media_url(image)
        if safe_image:
            safe_image = _absolute_url(safe_image, base)
            alt = resolve_text(block.get("alt", ""), locale)
            chunks.append(
                f'<p><img src="{html.escape(safe_image, quote=True)}" '
                f'alt="{html.escape(alt, quote=True)}"></p>'
            )
        if block["type"] == "faq":
            for item in block.get("items", []):
                chunks.append(f"<h2>{html.escape(resolve_text(item.get('heading', ''), locale))}</h2>")
                chunks.append(_body_html(resolve_text(item.get("body", ""), locale)))
        elif block["type"] in {"references", "research"}:
            for item in block.get("items", []):
                label = resolve_text(item.get("heading", ""), locale) or "Source"
                link = _safe_link(resolve_text(item.get("url", ""), locale))
                if link:
                    link = _absolute_url(link, base)
                    chunks.append(
                        f'<p><a href="{html.escape(link, quote=True)}">{html.escape(label)}</a></p>'
                    )
        elif block["type"] == "embed":
            link = _safe_link(resolve_text(block.get("url", ""), locale))
            if link:
                link = _absolute_url(link, base)
                chunks.append(
                    f'<p><a href="{html.escape(link, quote=True)}">Embedded content</a></p>'
                )
    return "".join(chunks)


def _wxr_date(value) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except ValueError:
            pass
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return datetime.now(UTC)


def _wxr_artifact(db, site, *, include_drafts=False) -> ExportArtifact:
    candidates = list(db.scalars(select(SitePage).where(
        SitePage.tenant_id == site.tenant_id,
        SitePage.site_id == site.id,
        SitePage.kind.in_(_PAGE_KINDS | {"article"}),
    ).order_by(SitePage.path, SitePage.id)))
    selected = []
    for page in candidates:
        snapshot = page.published_json
        status = "publish"
        if page.kind == "article" and snapshot and metadata(snapshot)["state"] != "published":
            snapshot = None
        if snapshot is None and include_drafts:
            snapshot, status = page.draft_json, "draft"
        if snapshot is not None:
            selected.append((page, snapshot, status))
    if len(selected) > MAX_WXR_ITEMS:
        raise CommerceError(f"WordPress export is limited to {MAX_WXR_ITEMS} posts and pages.")

    categories = list(db.scalars(select(BlogCategory).where(
        BlogCategory.tenant_id == site.tenant_id,
        BlogCategory.site_id == site.id,
    ).order_by(BlogCategory.slug)))
    category_names = {row.slug: row.name for row in categories}
    tags = sorted({tag for page, snapshot, _ in selected if page.kind == "article" for tag in metadata(snapshot)["tags"]})
    authors = sorted({metadata(snapshot)["author_name"] or f"{site.name} team" for page, snapshot, _ in selected if page.kind == "article"})
    if not authors:
        authors = [f"{site.name} team"]
    media_by_url = {media_url(row): row for row in db.scalars(select(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.site_id == site.id,
    ))}
    base = "https://" + site.hostname if site.hostname else f"https://{site.slug}.example.invalid"

    document = minidom.Document()
    rss = document.createElement("rss")
    rss.setAttribute("version", "2.0")
    for prefix, namespace in _WXR_NAMESPACES.items():
        rss.setAttribute("xmlns:" + prefix, namespace)
    document.appendChild(rss)
    channel = document.createElement("channel")
    rss.appendChild(channel)
    _append_text(document, channel, "title", site.name)
    _append_text(document, channel, "link", base)
    _append_text(document, channel, "description", f"FastShop CMS export for {site.name}")
    _append_text(document, channel, "pubDate", format_datetime(datetime.now(UTC)))
    _append_text(document, channel, "language", "en")
    _append_text(document, channel, "wp:wxr_version", "1.2")
    _append_text(document, channel, "wp:base_site_url", base)
    _append_text(document, channel, "wp:base_blog_url", base)

    for index, author in enumerate(authors, 1):
        node = document.createElement("wp:author")
        channel.appendChild(node)
        _append_text(document, node, "wp:author_id", index)
        login = taxonomy_slug(author)
        _append_cdata(document, node, "wp:author_login", login)
        _append_cdata(document, node, "wp:author_email", "")
        _append_cdata(document, node, "wp:author_display_name", author)
        _append_cdata(document, node, "wp:author_first_name", "")
        _append_cdata(document, node, "wp:author_last_name", "")
    for index, category in enumerate(categories, 1):
        node = document.createElement("wp:category")
        channel.appendChild(node)
        _append_text(document, node, "wp:term_id", index)
        _append_cdata(document, node, "wp:category_nicename", category.slug)
        _append_cdata(document, node, "wp:cat_name", category.name)
    for index, tag in enumerate(tags, 1):
        node = document.createElement("wp:tag")
        channel.appendChild(node)
        _append_text(document, node, "wp:term_id", len(categories) + index)
        _append_cdata(document, node, "wp:tag_slug", tag)
        _append_cdata(document, node, "wp:tag_name", tag)

    for index, (page, snapshot, status) in enumerate(selected, 1):
        item = document.createElement("item")
        channel.appendChild(item)
        locale = default_locale(site, preview=status == "draft")
        title = resolve_text(snapshot.get("title", page.title), locale)
        _append_text(document, item, "title", title)
        _append_text(document, item, "link", base + ("/" if page.path == "/" else page.path))
        published = _wxr_date(snapshot.get("published_at") or page.created_at)
        _append_text(document, item, "pubDate", format_datetime(published))
        author = (
            metadata(snapshot)["author_name"] or f"{site.name} team"
            if page.kind == "article"
            else f"{site.name} team"
        )
        _append_cdata(document, item, "dc:creator", author)
        guid = _append_text(document, item, "guid", f"fastshop:{site.id}:{page.id}")
        guid.setAttribute("isPermaLink", "false")
        _append_text(document, item, "description", "")
        _append_cdata(
            document,
            item,
            "content:encoded",
            _document_html(snapshot, locale, base),
        )
        _append_cdata(
            document,
            item,
            "excerpt:encoded",
            resolve_text(snapshot.get("description", ""), locale),
        )
        _append_text(document, item, "wp:post_id", index)
        _append_cdata(document, item, "wp:post_date", published.strftime("%Y-%m-%d %H:%M:%S"))
        _append_cdata(document, item, "wp:post_date_gmt", published.strftime("%Y-%m-%d %H:%M:%S"))
        _append_cdata(document, item, "wp:comment_status", "closed")
        _append_cdata(document, item, "wp:ping_status", "closed")
        _append_cdata(document, item, "wp:post_name", page.path.rstrip("/").rsplit("/", 1)[-1] or "home")
        _append_cdata(document, item, "wp:status", status)
        _append_text(document, item, "wp:post_parent", 0)
        _append_text(document, item, "wp:menu_order", 0)
        _append_cdata(document, item, "wp:post_type", "post" if page.kind == "article" else "page")
        _append_cdata(document, item, "wp:post_password", "")
        _append_text(document, item, "wp:is_sticky", 0)
        if page.kind == "article":
            blog = metadata(snapshot)
            category_slug = blog["category_slug"]
            if category_slug:
                node = _append_cdata(document, item, "category", category_names.get(category_slug, category_slug))
                node.setAttribute("domain", "category")
                node.setAttribute("nicename", category_slug)
            for tag in blog["tags"]:
                node = _append_cdata(document, item, "category", tag)
                node.setAttribute("domain", "post_tag")
                node.setAttribute("nicename", tag)
        image = resolve_text(snapshot.get("image", ""), locale)
        media = media_by_url.get(image)
        if media and _safe_media_url(image):
            enclosure = document.createElement("enclosure")
            enclosure.setAttribute("url", _absolute_url(image, base))
            enclosure.setAttribute("length", str(media.size or 0))
            enclosure.setAttribute("type", media.content_type)
            item.appendChild(enclosure)

    payload = document.toxml(encoding="utf-8")
    suffix = "with-drafts" if include_drafts else "published"
    return ExportArtifact(
        content=payload,
        media_type="application/xml",
        filename=f"fastshop-{site.slug}-wordpress-{suffix}.xml",
    )

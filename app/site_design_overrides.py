"""Tenant-scoped, sanitised storefront CSS overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import tinycss2
from sqlalchemy import select, update

from app import content
from app.models import SiteDesignOverride, SiteDesignOverrideRevision, User
from app.services import CommerceError
from app.site_media import media_for, media_url

SITE_ROOT_SELECTOR = "[data-fs-site-root]"
MAX_CSS_BYTES = 50 * 1024
MAX_NOTE = 400
MAX_PUBLISHED_VERSIONS = 50
_BLOCKED_PROPERTIES = {"behavior", "behaviour", "-moz-binding", "-ms-behavior"}
_BLOCKED_PSEUDOS = {"global", "host", "host-context", "root", "scope", "slotted", "view-transition"}
_URL_IMAGE_FUNCTIONS = {"image-set", "-webkit-image-set", "cross-fade", "-webkit-cross-fade"}


@dataclass(frozen=True)
class DesignOverrideSnapshot:
    """The current, tenant-scoped draft and published CSS state."""

    id: str | None
    draft_css: str
    published_css: str | None
    version: int
    published_version: int


@dataclass(frozen=True)
class DesignOverrideHistoryEntry:
    """A published CSS snapshot, including the member who published it."""

    id: str
    publication_number: int
    css: str
    note: str | None
    author_id: str | None
    author_name: str
    created_at: object


def _location(item) -> str:
    line = getattr(item, "source_line", 0)
    column = getattr(item, "source_column", 0)
    return f" line {line}, column {column}" if line and column else ""


def _error(item, message: str) -> None:
    raise CommerceError("Custom CSS" + _location(item) + ": " + message)


def _meaningful(tokens):
    return [token for token in tokens if token.type not in {"whitespace", "comment"}]


def _validate_url(value: str, allowed_media_urls: set[str], item) -> None:
    if not isinstance(value, str) or not value:
        _error(item, "url() needs one non-empty URL.")
    value = value.strip()
    parsed = urlsplit(value)
    decoded_path = unquote(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or value.startswith("//")
        or not parsed.path.startswith("/")
        or "\\" in decoded_path
        or any(part == ".." for part in decoded_path.split("/"))
        or any(ord(character) < 32 for character in value)
    ):
        _error(item, "url() may only load same-origin /static/ assets or approved site media URLs.")
    if decoded_path.startswith("/static/"):
        return
    if value in allowed_media_urls:
        return
    _error(item, "url() may only load same-origin /static/ assets or approved site media URLs.")


def _validate_url_function(token, allowed_media_urls: set[str]) -> None:
    arguments = _meaningful(token.arguments)
    if len(arguments) != 1 or arguments[0].type not in {"string", "url"}:
        _error(token, "url() must contain one literal URL.")
    _validate_url(arguments[0].value, allowed_media_urls, token)


def _looks_like_url(value: str) -> bool:
    value = value.strip()
    parsed = urlsplit(value)
    return value.startswith("/") or bool(parsed.scheme or parsed.netloc)


def _validate_image_tokens(tokens, allowed_media_urls: set[str], item) -> None:
    for token in _meaningful(tokens):
        if token.type == "error":
            _error(token, "Invalid CSS value.")
        if token.type == "string":
            _validate_url(token.value, allowed_media_urls, token)
        elif token.type == "url":
            _validate_url(token.value, allowed_media_urls, token)
        elif token.type == "function":
            name = token.lower_name
            if name == "var":
                _error(token, "Dynamic image values are not allowed.")
            if name == "url":
                _validate_url_function(token, allowed_media_urls)
            elif name == "expression":
                _error(token, "expression() is not allowed.")
            else:
                _validate_image_tokens(token.arguments, allowed_media_urls, token)
        elif token.type in {"() block", "[] block", "{} block"}:
            _validate_image_tokens(token.content, allowed_media_urls, token)


def _validate_tokens(tokens, allowed_media_urls: set[str], item) -> None:
    values = _meaningful(tokens)
    for index, token in enumerate(values):
        if token.type == "error":
            _error(token, "Invalid CSS value.")
        if token.type == "url":
            _validate_url(token.value, allowed_media_urls, token)
        elif token.type == "function":
            name = token.lower_name
            if name == "url":
                _validate_url_function(token, allowed_media_urls)
                continue
            if name in _URL_IMAGE_FUNCTIONS:
                _validate_image_tokens(token.arguments, allowed_media_urls, token)
                continue
            if name == "expression":
                _error(token, "expression() is not allowed.")
            if name in _BLOCKED_PROPERTIES:
                _error(token, name + " is not allowed.")
            _validate_tokens(token.arguments, allowed_media_urls, token)
        elif token.type in {"() block", "[] block", "{} block"}:
            _validate_tokens(token.content, allowed_media_urls, token)
        elif token.type == "ident" and token.value.lower() == "javascript":
            next_token = values[index + 1] if index + 1 < len(values) else None
            if next_token and next_token.type == "literal" and next_token.value == ":":
                _error(token, "javascript: is not allowed.")
        elif token.type == "ident" and token.value.lower() == "progid":
            _error(token, "progid is not allowed.")
        elif token.type == "string":
            if token.value.strip().lower().startswith("javascript:"):
                _error(token, "javascript: is not allowed.")
            if _looks_like_url(token.value):
                _validate_url(token.value, allowed_media_urls, token)


def _selector_targets_document(tokens, item) -> None:
    previous = None
    for token in _meaningful(tokens):
        if token.type == "[] block":
            previous = token
            continue
        if token.type == "function":
            name = token.lower_name
            if name in _BLOCKED_PSEUDOS:
                _error(token, ":" + name + " cannot target outside the storefront root.")
            _selector_targets_document(token.arguments, token)
        elif token.type in {"() block", "{} block"}:
            _selector_targets_document(token.content, token)
        elif token.type == "ident":
            name = token.value.lower()
            if name in _BLOCKED_PSEUDOS and getattr(previous, "value", "") == ":":
                _error(token, ":" + name + " cannot target outside the storefront root.")
            if name in {"html", "body"} and getattr(previous, "value", "") not in {".", "#", ":"}:
                _error(token, name + " cannot target outside the storefront root.")
        elif token.type == "literal" and token.value == "|":
            _error(token, "Namespaced selectors are not allowed.")
        previous = token


def _is_site_root_attribute(token) -> bool:
    if token.type != "[] block":
        return False
    contents = _meaningful(token.content)
    return bool(contents and contents[0].type == "ident" and
                contents[0].value.lower() == "data-fs-site-root")


def _contains_site_root_attribute(tokens) -> bool:
    for token in tokens:
        if _is_site_root_attribute(token):
            return True
        if token.type == "function" and _contains_site_root_attribute(token.arguments):
            return True
        if token.type in {"() block", "{} block"} and _contains_site_root_attribute(token.content):
            return True
    return False


def _explicit_root_scope(tokens, item) -> bool:
    """Validate a user-supplied root prefix without letting it select a sibling."""
    values = [token for token in tokens if token.type != "comment"]
    first = next((index for index, token in enumerate(values) if token.type != "whitespace"), None)
    if first is None:
        return False
    if not _is_site_root_attribute(values[first]):
        if _contains_site_root_attribute(values):
            _error(item, "The storefront root selector may only be the initial scope.")
        return False
    if _contains_site_root_attribute(values[first + 1:]):
        _error(item, "The storefront root selector may only appear once.")

    descendant_scope = False
    for index, token in enumerate(values[first + 1:], start=first + 1):
        if token.type == "whitespace":
            next_token = next((candidate for candidate in values[index + 1:]
                               if candidate.type != "whitespace"), None)
            if next_token is None:
                continue
            if (next_token.type == "literal" and next_token.value in {"+", "~", "||"}
                    and not descendant_scope):
                _error(item, "Selectors cannot leave the storefront root through a sibling combinator.")
            descendant_scope = True
        elif token.type == "literal":
            if token.value == ">":
                descendant_scope = True
            elif token.value in {"+", "~", "||"} and not descendant_scope:
                _error(item, "Selectors cannot leave the storefront root through a sibling combinator.")
    return True


def _scoped_selectors(tokens, allowed_media_urls: set[str], item) -> str:
    selectors = [[]]
    for token in tokens:
        if token.type == "literal" and token.value == ",":
            selectors.append([])
        else:
            selectors[-1].append(token)
    result = []
    for selector in selectors:
        text = tinycss2.serialize(selector).strip()
        if not text:
            _error(item, "Selectors cannot be empty.")
        _validate_tokens(selector, allowed_media_urls, item)
        _selector_targets_document(selector, item)
        if text[0] in "&>+~":
            _error(item, "Relative selectors are not allowed.")
        if _explicit_root_scope(selector, item):
            result.append(text)
        else:
            result.append(SITE_ROOT_SELECTOR + " " + text)
    return ", ".join(result)


def _declarations(tokens, allowed_media_urls: set[str], item) -> str:
    declarations = tinycss2.parse_declaration_list(tokens, skip_comments=True, skip_whitespace=True)
    output = []
    for declaration in declarations:
        if declaration.type == "error":
            _error(declaration, "Invalid declaration.")
        if declaration.type != "declaration":
            _error(declaration, "Only CSS declarations are allowed in a style rule.")
        if declaration.lower_name in _BLOCKED_PROPERTIES:
            _error(declaration, declaration.lower_name + " is not allowed.")
        _validate_tokens(declaration.value, allowed_media_urls, declaration)
        value = tinycss2.serialize(declaration.value).strip()
        if not value:
            _error(declaration, declaration.name + " needs a value.")
        output.append(declaration.name + ": " + value + (" !important" if declaration.important else ""))
    if not output:
        _error(item, "Style rules need at least one declaration.")
    return "; ".join(output)


def _rules(rules, allowed_media_urls: set[str]) -> str:
    output = []
    for rule in rules:
        if rule.type == "error":
            _error(rule, "Invalid CSS syntax.")
        if rule.type == "qualified-rule":
            selector = _scoped_selectors(rule.prelude, allowed_media_urls, rule)
            output.append(selector + " { " + _declarations(rule.content, allowed_media_urls, rule) + "; }")
            continue
        if rule.type != "at-rule":
            _error(rule, "Only style rules, @media and @supports are allowed.")
        name = rule.lower_at_keyword
        if name not in {"media", "supports"}:
            _error(rule, "@" + name + " is not allowed.")
        if rule.content is None:
            _error(rule, "@" + name + " needs a block.")
        condition = tinycss2.serialize(rule.prelude).strip()
        if not condition:
            _error(rule, "@" + name + " needs a condition.")
        _validate_tokens(rule.prelude, allowed_media_urls, rule)
        nested = _rules(
            tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True),
            allowed_media_urls,
        )
        output.append("@" + name + " " + condition + " { " + nested + " }")
    return "\n".join(output)


def sanitise_css(css: str, allowed_media_urls: set[str]) -> str:
    """Strictly allow presentation-only CSS and scope every resulting selector."""
    if not isinstance(css, str):
        raise CommerceError("Custom CSS must be text.")
    try:
        size = len(css.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise CommerceError("Custom CSS must be valid UTF-8 text.") from exc
    if size > MAX_CSS_BYTES:
        raise CommerceError("Custom CSS must be 50 KB or smaller.")
    if re.search(r"</\s*style\b", css, re.IGNORECASE):
        raise CommerceError("Custom CSS cannot contain </style.")
    rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    return _rules(rules, allowed_media_urls)


def _site_media_urls(db, site) -> set[str]:
    urls = set()
    for media in media_for(db, site):
        url = media_url(media)
        parsed = urlsplit(url)
        if not parsed.scheme and not parsed.netloc and parsed.path.startswith("/"):
            urls.add(url)
        urls.add(f"/site-media/{site.id}/{media.id}")
    return urls


def _current(db, site):
    return db.scalar(select(SiteDesignOverride).where(
        SiteDesignOverride.site_id == site.id,
        SiteDesignOverride.tenant_id == site.tenant_id,
    ))


def _snapshot(record) -> DesignOverrideSnapshot:
    if record is None:
        return DesignOverrideSnapshot(None, "", None, 0, 0)
    return DesignOverrideSnapshot(
        record.id,
        record.draft_css,
        record.published_css,
        record.version,
        record.published_version,
    )


def _version(value, *, initial=False) -> int:
    minimum = 0 if initial else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CommerceError("Reload Custom CSS before saving.")
    return value


def _note(value: str) -> str:
    if not isinstance(value, str):
        raise CommerceError("Custom CSS note must be text.")
    value = value.strip()
    if len(value) > MAX_NOTE:
        raise CommerceError(f"Custom CSS note must be {MAX_NOTE} characters or fewer.")
    return value


def _claim(db, site, record, version: int, **values) -> SiteDesignOverride:
    _version(version)
    changed = db.execute(update(SiteDesignOverride).where(
        SiteDesignOverride.id == record.id,
        SiteDesignOverride.site_id == site.id,
        SiteDesignOverride.tenant_id == site.tenant_id,
        SiteDesignOverride.version == version,
    ).values(**values, version=SiteDesignOverride.version + 1))
    if changed.rowcount != 1:
        raise CommerceError("Custom CSS changed. Reload and try again.")
    db.refresh(record)
    return record


def _append_history(db, site, author_id: str, record, note: str) -> None:
    db.add(SiteDesignOverrideRevision(
        tenant_id=site.tenant_id,
        site_id=site.id,
        author_id=author_id,
        publication_number=record.published_version,
        css=record.published_css or "",
        note=note or None,
    ))
    db.flush()
    expired = list(db.scalars(select(SiteDesignOverrideRevision).where(
        SiteDesignOverrideRevision.site_id == site.id,
        SiteDesignOverrideRevision.tenant_id == site.tenant_id,
    ).order_by(SiteDesignOverrideRevision.publication_number.desc()).offset(MAX_PUBLISHED_VERSIONS)))
    for revision in expired:
        db.delete(revision)
    db.flush()


def get(db, site_id: str, actor_id: str) -> DesignOverrideSnapshot:
    """Return one site's current overrides after checking membership."""
    site = content.owned_site(db, site_id, actor_id)
    return _snapshot(_current(db, site))


def validate(db, site_id: str, actor_id: str, css: str) -> str:
    """Validate and scope CSS without persisting it."""
    site = content.owned_site(db, site_id, actor_id)
    return sanitise_css(css, _site_media_urls(db, site))


def save_draft(db, site_id: str, actor_id: str, css: str, version: int) -> DesignOverrideSnapshot:
    """Persist a validated draft with optimistic locking."""
    site = content.owned_site(db, site_id, actor_id)
    clean_css = sanitise_css(css, _site_media_urls(db, site))
    record = _current(db, site)
    if record is None:
        _version(version, initial=True)
        if version != 0:
            raise CommerceError("Custom CSS changed. Reload and try again.")
        record = SiteDesignOverride(
            tenant_id=site.tenant_id,
            site_id=site.id,
            draft_css=clean_css,
        )
        db.add(record)
        db.flush()
        return _snapshot(record)
    return _snapshot(_claim(db, site, record, version, draft_css=clean_css))


def publish(
    db,
    site_id: str,
    actor_id: str,
    version: int,
    *,
    note: str = "",
) -> DesignOverrideSnapshot:
    """Publish the current draft and append an immutable, bounded history entry."""
    site = content.owned_site(db, site_id, actor_id, publish=True)
    record = _current(db, site)
    if record is None:
        raise CommerceError("Save Custom CSS before publishing it.")
    record = _claim(
        db,
        site,
        record,
        version,
        published_css=record.draft_css,
        published_version=record.published_version + 1,
    )
    _append_history(db, site, actor_id, record, _note(note))
    return _snapshot(record)


def revert(
    db,
    site_id: str,
    actor_id: str,
    revision_id: str,
    version: int,
    *,
    note: str = "",
) -> DesignOverrideSnapshot:
    """Publish an earlier CSS version as a new version, preserving the audit trail."""
    site = content.owned_site(db, site_id, actor_id, publish=True)
    record = _current(db, site)
    if record is None:
        raise CommerceError("No Custom CSS version is available to revert.")
    revision = db.scalar(select(SiteDesignOverrideRevision).where(
        SiteDesignOverrideRevision.id == revision_id,
        SiteDesignOverrideRevision.site_id == site.id,
        SiteDesignOverrideRevision.tenant_id == site.tenant_id,
    ))
    if revision is None:
        raise CommerceError("Custom CSS version not found.")
    record = _claim(
        db,
        site,
        record,
        version,
        draft_css=revision.css,
        published_css=revision.css,
        published_version=record.published_version + 1,
    )
    _append_history(db, site, actor_id, record, _note(note) or f"Reverted to version {revision.publication_number}.")
    return _snapshot(record)


def history(db, site_id: str, actor_id: str) -> list[DesignOverrideHistoryEntry]:
    """Return retained published versions after checking the caller's membership."""
    site = content.owned_site(db, site_id, actor_id)
    rows = db.execute(select(SiteDesignOverrideRevision, User.name).outerjoin(
        User, User.id == SiteDesignOverrideRevision.author_id,
    ).where(
        SiteDesignOverrideRevision.site_id == site.id,
        SiteDesignOverrideRevision.tenant_id == site.tenant_id,
    ).order_by(
        SiteDesignOverrideRevision.publication_number.desc(),
        SiteDesignOverrideRevision.id.desc(),
    ).limit(MAX_PUBLISHED_VERSIONS)).all()
    return [DesignOverrideHistoryEntry(
        row.id,
        row.publication_number,
        row.css,
        row.note,
        row.author_id,
        name or "Unknown member",
        row.created_at,
    ) for row, name in rows]


def css_for_storefront(db, site, *, preview: bool = False) -> str:
    """Return the saved draft for a private preview or the public CSS otherwise."""
    record = _current(db, site)
    if record is None:
        return ""
    return record.draft_css if preview else record.published_css or ""

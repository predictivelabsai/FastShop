"""Storefront preferences and consent-aware metadata, without theme layout changes."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from fasthtml.common import Link, Meta, Script
from sqlalchemy import select

from app.auth import hash_admin_password, verify_admin_password
from app.config import settings
from app.content_gap_models import SitePreferences
from app.services import CommerceError
from app.site_media import media_url, owned_media


def get_preferences(db, site):
    return db.scalar(select(SitePreferences).where(SitePreferences.site_id == site.id,
                                                 SitePreferences.tenant_id == site.tenant_id))


def save_preferences(db, site, data):
    row = get_preferences(db, site)
    if not row:
        row = SitePreferences(tenant_id=site.tenant_id, site_id=site.id, password_hash="")
    title, description = str(data.get("homepage_title", "")).strip(), str(data.get("meta_description", "")).strip()
    if len(title) > 240 or len(description) > 500:
        raise CommerceError("Use a title under 241 characters and description under 501 characters.")
    analytics = str(data.get("analytics_id", "")).strip()
    pixel = str(data.get("meta_pixel_id", "")).strip()
    if analytics and not re.fullmatch(r"G-[A-Z0-9]{6,20}", analytics):
        raise CommerceError("Use a Google Analytics measurement ID such as G-ABC1234567.")
    if pixel and not re.fullmatch(r"\d{5,30}", pixel):
        raise CommerceError("Use a numeric Meta pixel ID.")
    media = {}
    for field in ("social_image_id", "favicon_id"):
        media[field] = str(data.get(field, "")).strip() or None
        if media[field]:
            asset = owned_media(db, site, media[field])
            if not asset.content_type.startswith("image/"):
                raise CommerceError("Choose an image from this site's files.")
    enabled = data.get("password_enabled") in (True, "1", "true", "on")
    password = str(data.get("password", ""))
    encoded = row.password_hash or ""
    if password:
        if not 8 <= len(password) <= 256:
            raise CommerceError("Use a storefront password between 8 and 256 characters.")
        # Reuse the reviewed PBKDF2 format with domain padding to meet its admin length floor.
        encoded = hash_admin_password("storefront-password:" + password)
    if enabled and not encoded:
        raise CommerceError("Set a password before enabling password protection.")
    row.homepage_title, row.meta_description = title, description
    row.analytics_id, row.meta_pixel_id = analytics, pixel
    row.social_image_id, row.favicon_id = media["social_image_id"], media["favicon_id"]
    row.password_enabled, row.password_hash = enabled, encoded
    db.add(row)
    db.flush()
    return row


def password_matches(password, preferences):
    return bool(preferences and preferences.password_hash and
                verify_admin_password("storefront-password:" + str(password), preferences.password_hash))


def access_token(site, preferences):
    return hmac.new(settings.session_secret.encode(),
                    ("storefront-access:" + site.id + ":" + preferences.password_hash).encode(),
                    hashlib.sha256).hexdigest()


def has_access(db, site, preferences, session, cookies):
    if not preferences or not preferences.password_enabled:
        return True
    from app.models import Membership, User
    if session.get("user_id") and db.scalar(select(Membership.id).join(User).where(
            Membership.user_id == session["user_id"], Membership.tenant_id == site.tenant_id,
            Membership.role.in_(["admin", "merchant", "editor"]), User.is_active.is_(True))):
        return True
    return secrets.compare_digest(cookies.get("fastshop_store_" + site.id, ""),
                                  access_token(site, preferences))


def metadata(db, site, page, document):
    row = get_preferences(db, site)
    if row and page.path == "/":
        document["title"] = row.homepage_title or document["title"]
        document["description"] = row.meta_description or document.get("description", "")
    return row


def head_nodes(db, site, preferences, *, preview=False):
    if not preferences:
        return []
    nodes = []
    if preferences.social_image_id:
        origin = "https://" + site.hostname if site.hostname else settings.public_url
        nodes.append(Meta(property="og:image", content=origin.rstrip("/") + media_url(
            owned_media(db, site, preferences.social_image_id))))
    if preferences.favicon_id:
        nodes.append(Link(rel="icon", href=media_url(owned_media(db, site, preferences.favicon_id))))
    if not preview and site.status == "published" and (preferences.analytics_id or preferences.meta_pixel_id):
        nodes.append(Script(src="/static/store-preferences.js", defer=True,
                            data_site=site.id, data_analytics=preferences.analytics_id,
                            data_pixel=preferences.meta_pixel_id))
    return nodes

def storefront_gate(scope):
    """Run after host rewriting, before any shopper route, with shared endpoints excluded."""
    import base64
    import json
    import re
    from http.cookies import SimpleCookie

    from itsdangerous import BadSignature, TimestampSigner
    from starlette.responses import RedirectResponse

    from app.db import SessionLocal
    from app.models import Site

    match = re.fullmatch(r"/sites/([^/]+)(/.*)?", scope.get("path", ""))
    if not match:
        return None
    path = match[2] or "/"
    if path == "/password" or path.startswith(("/webhooks/", "/healthz", "/api/")):
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(dict(scope.get("headers", [])).get(b"cookie", b"").decode("latin-1"))
    except Exception:
        cookies = SimpleCookie()
    cookie_values = {key: value.value for key, value in cookies.items()}
    session = scope.get("session", {})
    if not session and cookie_values.get("session_"):
        try:
            signed = TimestampSigner(settings.session_secret).unsign(cookie_values["session_"], max_age=31536000)
            session = json.loads(base64.b64decode(signed))
        except (BadSignature, ValueError, TypeError):
            session = {}
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == match[1]))
        if not site:
            return None
        preferences = get_preferences(db, site)
        if not has_access(db, site, preferences, session, cookie_values):
            base = scope.get("site_base", "/sites/" + site.slug)
            return RedirectResponse(base + "/password", 303, headers={"Cache-Control": "no-store"})
    return None

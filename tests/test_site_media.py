"""Media registration, ownership, reference retention and legacy migration."""
import copy
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

from app import content
from app.models import Base, Site, SiteChangeSet, SiteMedia, SiteMenu, SitePage, SiteRevision, User
from app.services import CommerceError
from app.site_media import (
    backfill_media,
    delete_media,
    media_for,
    media_url,
    owned_media,
    references,
)


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="media@example.test", name="Media")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Media", "media-test")
        other = content.create_site(db, user.id, "Other", "media-other")
        yield db, user, site, other
    engine.dispose()


def asset(db, site, **kwargs):
    row = SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title="Image", alt="Water",
        content_type="image/webp", storage_key="water.webp", data=b"image", size=5, **kwargs)
    db.add(row)
    db.flush()
    return row


def test_scoping_and_unreferenced_delete(workspace):
    db, _, site, other = workspace
    row = asset(db, site)
    assert not media_for(db, other)
    with pytest.raises(CommerceError):
        owned_media(db, other, row.id)
    with pytest.raises(CommerceError):
        content.validate_media_ownership(db, other, {"image": media_url(row)})
    delete_media(db, site, row.id)
    assert not media_for(db, site)


@pytest.mark.parametrize("source", ["draft", "published", "revision", "menu", "settings", "history"])
def test_references_prevent_delete(workspace, source):
    db, user, site, _ = workspace
    row = asset(db, site)
    url = media_url(row)
    page = content.site_pages(db, site)[0]
    doc = copy.deepcopy(page.draft_json)
    doc["blocks"][0]["image"] = {"en": url, "et": url}
    if source == "draft":
        page.draft_json = doc
    elif source == "published":
        page.published_json = doc
    elif source == "revision":
        db.add(SiteRevision(tenant_id=site.tenant_id, site_id=site.id, page_id=page.id, content_json=doc))
    elif source == "menu":
        # HTTPS media can also be linked by a menu.
        row.public_url = "https://example.test/image.webp"
        menu = db.scalar(select(SiteMenu).where(SiteMenu.site_id == site.id, SiteMenu.tenant_id == site.tenant_id))
        menu.items_json = [{"id": "asset", "kind": "external", "label": "Image", "url": row.public_url}]
    elif source == "settings":
        site.settings_json = site.settings_json | {"logo": url}
    else:
        db.add(SiteChangeSet(tenant_id=site.tenant_id, site_id=site.id, user_id=user.id,
            source="classical", summary="Earlier image", before_json=doc, after_json={}))
    db.flush()
    assert references(db, site, row)
    with pytest.raises(CommerceError, match="Cannot delete referenced"):
        delete_media(db, site, row.id)


def test_backfill_locales_revisions_and_no_content_rewrite(workspace):
    db, _, site, _ = workspace
    page = content.site_pages(db, site)[0]
    doc = copy.deepcopy(page.draft_json)
    doc["blocks"][0].update(image={"en": "/static/h24you/water-placeholder.webp"}, alt={"en": "Water", "et": "Vesi"})
    page.draft_json = doc
    history = {"sections": [{"type": "hero", "image": "https://example.test/old.jpg"}]}
    db.add(SiteRevision(tenant_id=site.tenant_id, site_id=site.id, page_id=page.id, content_json=history))
    assert backfill_media(db, site) == 2
    assert backfill_media(db, site) == 0
    assert page.draft_json == doc
    rows = media_for(db, site)
    local = next(row for row in rows if row.public_url.startswith("/static/"))
    assert local.localized_alt == {"en": "Water", "et": "Vesi"}
    assert local.size > 0 and local.data is None
    db.delete(page)
    db.flush()
    assert len(media_for(db, site)) == 2


def test_site_cascade(workspace):
    db, _, site, _ = workspace
    asset(db, site)
    site_id, tenant_id = site.id, site.tenant_id
    db.execute(delete(SitePage).where(SitePage.site_id == site_id, SitePage.tenant_id == tenant_id))
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    db.execute(delete(Site).where(Site.id == site_id, Site.tenant_id == tenant_id))
    assert not db.scalar(select(SiteMedia).where(SiteMedia.site_id == site_id, SiteMedia.tenant_id == tenant_id))


def test_populated_pre_media_upgrade(tmp_path):
    path = tmp_path / "legacy.db"
    url = "sqlite:///" + path.as_posix()
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="migration-media@example.test", name="Migration")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Legacy", "legacy-media")
        site_id, tenant_id = site.id, site.tenant_id
        page = content.site_pages(db, site)[0]
        doc = copy.deepcopy(page.draft_json)
        doc["blocks"][0]["image"] = "/static/h24you/water-placeholder.webp"
        page.draft_json = doc
        db.commit()
    SiteMedia.__table__.drop(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE site_media (id VARCHAR(32) NOT NULL PRIMARY KEY, tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id), site_id VARCHAR(32) NOT NULL REFERENCES sites(id), title VARCHAR(200) NOT NULL, alt VARCHAR(400) NOT NULL, content_type VARCHAR(80) NOT NULL, storage_key VARCHAR(240) NOT NULL, is_placeholder BOOLEAN NOT NULL, size INTEGER NOT NULL, data BLOB, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"))
        conn.execute(text("CREATE INDEX ix_site_media_tenant_id ON site_media (tenant_id)"))
        conn.execute(text("CREATE INDEX ix_site_media_site_id ON site_media (site_id)"))
        conn.execute(text("INSERT INTO site_media (id, tenant_id, site_id, title, alt, content_type, storage_key, is_placeholder, size, data) VALUES ('old', :tenant, :site, 'Old upload', 'Old alt', 'image/webp', 'old.webp', 0, 3, X'010203')"), {"tenant": tenant_id, "site": site_id})
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('20261008_0017')"))
    for args in (("upgrade", "head"), ("check",)):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], env=os.environ | {"DB_URL": url}, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    with Session(engine) as db:
        site = db.scalar(select(Site).where(Site.id == site_id, Site.tenant_id == tenant_id))
        rows = media_for(db, site)
        assert len(rows) == 2
        old = owned_media(db, site, "old")
        assert old.alt == "Old alt" and old.data == b"\x01\x02\x03"
        assert media_url(old) == f"/site-media/{site_id}/old"
    engine.dispose()
    path.unlink()

def test_media_routes_upload_edit_csrf_and_ownership():
    import io
    import re
    from uuid import uuid4

    from PIL import Image
    from starlette.testclient import TestClient

    from app.config import settings
    from app.db import SessionLocal
    from app.main import app

    client = TestClient(app)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    client.post("/login", data={"csrf_token": token, "email": settings.admin_email, "password": settings.admin_password})
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, user.id, "Media forms", "media-" + uuid4().hex[:10])
        outsider = User(email=uuid4().hex + "@example.test", name="Outsider")
        db.add(outsider)
        db.flush()
        other = content.create_site(db, outsider.id, "Other media", "media-" + uuid4().hex[:10])
        site_id, other_id, tenant_id = site.id, other.id, site.tenant_id
        db.commit()
    base = f"/admin/sites/{site_id}/media"
    assert client.get(f"/admin/sites/{other_id}/media").status_code == 400
    invalid = client.post(base, data={"csrf_token": token, "alt": "Invalid"}, files={"image": ("bad.txt", b"not an image", "image/png")})
    assert "Invalid image upload" in invalid.text
    oversized = client.post(base, data={"csrf_token": token, "alt": "Large"}, files={"image": ("large.png", b"x" * (8 * 1024 * 1024 + 1), "image/png")})
    assert "Images must be smaller than 8 MB" in oversized.text
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(buffer, "PNG")
    response = client.post(base, data={"csrf_token": token, "alt": "Blue square"}, files={"image": ("test.png", buffer.getvalue(), "image/png")})
    assert response.status_code == 200
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.id == site_id, Site.tenant_id == tenant_id))
        row = media_for(db, site)[0]
        row_id, url = row.id, media_url(row)
        assert row.content_type == "image/webp" and row.size == len(row.data)
        row.localized_alt = {"en": "Blue square", "et": "Sinine"}
        db.commit()
    assert TestClient(app).get(url).status_code == 404
    assert client.get(url).status_code == 200
    target = base + "/" + row_id
    assert "session expired" in client.post(target, data={"action": "delete"}).text
    response = client.post(target, data={"csrf_token": token, "action": "save", "title": "Updated", "alt": "New description"})
    assert "Media updated" in response.text
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.id == site_id, Site.tenant_id == tenant_id))
        assert owned_media(db, site, row_id).localized_alt == {"en": "New description", "et": "Sinine"}
    assert "Media updated" in client.post(target, data={"csrf_token": token, "action": "delete"}).text
    assert client.get(url).status_code == 404

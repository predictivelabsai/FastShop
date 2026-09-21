import copy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.content import (
    create_site,
    owned_site,
    restore_page,
    safe_url,
    save_page,
    site_page,
    site_pages,
    validate_document,
    validate_media_ownership,
)
from app.models import Base, SiteMedia, SiteRevision, User
from app.services import CommerceError


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first = User(email="first@example.test", name="First")
        second = User(email="second@example.test", name="Second")
        db.add_all([first, second])
        db.flush()
        site = create_site(db, first.id, "First site", "first-site")
        other = create_site(db, second.id, "Second site", "second-site")
        db.commit()
        yield db, first, second, site, other


def test_site_creation_assigns_owner_and_creates_private_starter(workspace):
    db, first, _, site, _ = workspace
    assert owned_site(db, site.id, first.id).id == site.id
    assert site.status == "draft"
    assert len(site_pages(db, site)) == 8
    assert all(p.published_json is None for p in site_pages(db, site))
    assert "hydrogen" not in str(site.settings_json).lower()


def test_cross_tenant_read_save_restore_denied(workspace):
    db, first, second, site, other = workspace
    page = site_pages(db, site)[0]
    with pytest.raises(CommerceError, match="access denied"):
        owned_site(db, site.id, second.id)
    with pytest.raises(CommerceError, match="Page not found"):
        site_page(db, other, page.id)
    with pytest.raises(CommerceError, match="access denied"):
        save_page(db, site, page.id, second.id, page.draft_json, page.version)
    saved = save_page(db, site, page.id, first.id, page.draft_json, page.version)
    revision = db.query(SiteRevision).filter_by(page_id=saved.id).one()
    with pytest.raises(CommerceError, match="Revision not found"):
        restore_page(db, other, page.id, revision.id, second.id, saved.version)


def test_draft_edit_and_restore_do_not_change_public_snapshot(workspace):
    db, first, _, site, _ = workspace
    page = site_pages(db, site)[0]
    original = copy.deepcopy(page.draft_json)
    save_page(db, site, page.id, first.id, original, page.version, "publish")
    published_revision = db.query(SiteRevision).filter_by(page_id=page.id).one()
    changed = {**original, "title": "Changed privately"}
    save_page(db, site, page.id, first.id, changed, page.version)
    assert page.published_json["title"] == original["title"]
    assert page.draft_json["title"] == "Changed privately"
    restore_page(db, site, page.id, published_revision.id, first.id, page.version)
    assert page.draft_json == original
    save_page(db, site, page.id, first.id, original, page.version, "unpublish")
    assert page.published_json is None


def test_stale_editor_cannot_overwrite_changes(workspace):
    db, first, _, site, _ = workspace
    page = site_pages(db, site)[0]
    version = page.version
    save_page(db, site, page.id, first.id, page.draft_json, version)
    with pytest.raises(CommerceError, match="Someone updated"):
        save_page(db, site, page.id, first.id, page.draft_json, version)


def test_publication_date_is_server_owned_and_preserved_on_republish(workspace):
    db, first, _, site, _ = workspace
    page = site_pages(db, site)[0]
    document = {**page.draft_json, "published_at": "2099-01-01"}
    save_page(db, site, page.id, first.id, document, page.version, "publish")
    first_publication = page.published_json["published_at"]
    assert first_publication != "2099-01-01"
    save_page(db, site, page.id, first.id, document, page.version, "publish")
    assert page.published_json["published_at"] == first_publication


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.test", "/\\evil.test", "data:text/html,hi", "https://user:pass@host.test"])
def test_unsafe_links_rejected(url):
    with pytest.raises(CommerceError):
        safe_url(url)


def test_unknown_sections_and_invalid_documents_rejected():
    with pytest.raises(CommerceError):
        validate_document({"title": "Test", "sections": [{"type": "raw_html"}]})
    with pytest.raises(CommerceError):
        validate_document({"title": "Test", "sections": [{"type": "hero", "image": "javascript:bad"}]})


def test_owned_media_alt_text_and_cross_tenant_rejection(workspace):
    from app.site_ui import owned_image
    db, _, _, site, other = workspace
    media = SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title="Water",
        alt="Ripples in a clear glass", storage_key="test.webp", content_type="image/webp", size=3, data=b"abc")
    db.add(media)
    db.flush()
    url = f"/site-media/{site.id}/{media.id}"
    validate_media_ownership(db, site, {"gallery": [url]})
    with pytest.raises(CommerceError, match="belonging to this site"):
        validate_media_ownership(db, other, {"gallery": [url]})
    assert owned_image(db, site, url).attrs["alt"] == "Ripples in a clear glass"

"""Blog taxonomy ownership, editorial snapshots and migration compatibility."""
import copy
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

from app import content
from app.models import Base, BlogCategory, Membership, Site, SitePage, SiteRevision, User
from app.services import CommerceError
from app.site_blog import (
    articles_for,
    backfill_categories,
    categories_for,
    ensure_category,
    is_listed,
    metadata,
    validate_blog,
)


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="blog@example.test", name="Writer")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Blog", "blog-test")
        other = content.create_site(db, user.id, "Other", "blog-other")
        yield db, user, site, other
    engine.dispose()


def article(db, site, path="/story", **blog):
    return content.create_page(db, site, "Story", path, "article", {
        "version": 1, "title": "Story", "blocks": [], "blog": {"state": "published", **blog},
    })


def test_taxonomy_scoping_idempotence_and_legacy_backfill(workspace):
    db, _, site, other = workspace
    category = ensure_category(db, site, "The Basics")
    assert ensure_category(db, site, "THE BASICS").id == category.id
    assert not categories_for(db, other)
    assert ensure_category(db, other, "The Basics").id != category.id
    page = article(db, site)
    legacy = {"version": 1, "title": "Legacy", "blocks": [], "category": {"en": "Reading the Methods"}}
    page.draft_json = legacy
    page.published_json = legacy
    before = copy.deepcopy(page.draft_json)
    backfill_categories(db, site)
    backfill_categories(db, site)
    assert {row.slug for row in categories_for(db, site)} == {"the-basics", "reading-the-methods"}
    assert page.draft_json == before
    assert metadata(page.draft_json)["state"] == "published"
    assert is_listed(page)
    assert articles_for(db, site, category="reading-the-methods") == [page]
    assert not articles_for(db, other)


@pytest.mark.parametrize("value", [None, [], {"unknown": "x"}, {"state": "scheduled"},
    {"author_name": 5}, {"author_name": "x" * 161}, {"author_bio": "x" * 2001},
    {"category_slug": "UPPER"}, {"category_slug": "../other"}, {"tags": "water"},
    {"tags": ["bad tag"]}, {"tags": [None]}, {"tags": ["x"] * 21}])
def test_invalid_metadata_rejected(value):
    with pytest.raises(CommerceError):
        validate_blog(value)


def test_category_ownership_and_tag_normalization(workspace):
    db, user, site, other = workspace
    foreign = ensure_category(db, other, "Foreign")
    page = article(db, site)
    document = copy.deepcopy(page.draft_json)
    document["blog"]["category_slug"] = foreign.slug
    with pytest.raises(CommerceError, match="belonging to this site"):
        content.save_page(db, site, page.id, user.id, document, page.version)
    assert validate_blog({"tags": ["water", "science", "water"]})["tags"] == ["water", "science"]
    # Even a malformed row matching site_id must not cross the tenant boundary.
    db.add(BlogCategory(site_id=site.id, tenant_id=other.tenant_id, slug="wrong-tenant", name="Wrong"))
    db.flush()
    assert not any(row.slug == "wrong-tenant" for row in categories_for(db, site))


def test_editorial_state_composes_with_publication_and_restore(workspace):
    db, user, site, _ = workspace
    page = content.create_page(db, site, "Draft", "/draft", "article")
    assert metadata(page.draft_json)["state"] == "draft"
    assert not is_listed(page)
    document = copy.deepcopy(page.draft_json)
    document["blog"].update(state="published", author_name="A Writer", author_bio="Studies water", tags=["water"])
    content.save_page(db, site, page.id, user.id, document, page.version)
    assert not is_listed(page)  # Editorial state alone never publishes a page.
    content.save_page(db, site, page.id, user.id, document, page.version, "publish")
    revision = db.scalar(select(SiteRevision).where(SiteRevision.page_id == page.id, SiteRevision.action == "publish"))
    published = copy.deepcopy(page.published_json)
    document["blog"].update(state="archived", author_name="Private edit")
    content.save_page(db, site, page.id, user.id, document, page.version)
    assert page.published_json == published
    assert articles_for(db, site, tag="water") == [page]
    assert not articles_for(db, site, preview=True)
    content.save_page(db, site, page.id, user.id, document, page.version, "publish")
    assert not is_listed(page)
    content.restore_page(db, site, page.id, revision.id, user.id, page.version)
    assert metadata(page.draft_json)["author_name"] == "A Writer"
    assert not is_listed(page)
    content.save_page(db, site, page.id, user.id, page.draft_json, page.version, "publish")
    assert is_listed(page)
    content.save_page(db, site, page.id, user.id, page.draft_json, page.version, "unpublish")
    assert not is_listed(page)


@pytest.mark.parametrize("state", ["draft", "published", "archived"])
@pytest.mark.parametrize("field", ["author_name", "author_bio", "tags", "body"])
def test_every_state_and_new_text_field_passes_compliance(workspace, state, field):
    db, user, site, _ = workspace
    page = article(db, site, state=state)
    document = copy.deepcopy(page.draft_json)
    if field == "body":
        document["blocks"] = [{"id": "claim", "version": 1, "type": "text", "body": "A miracle cure"}]
    else:
        document["blog"][field] = ["miracle"] if field == "tags" else "A miracle cure"
    content.save_page(db, site, page.id, user.id, document, page.version)
    with pytest.raises(CommerceError, match="cannot be published"):
        content.save_page(db, site, page.id, user.id, document, page.version, "publish")
    assert page.published_json is None


def test_editor_and_foreign_actor_cannot_publish_metadata(workspace):
    db, user, site, other = workspace
    page = article(db, site)
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.tenant_id == site.tenant_id))
    membership.role = "editor"
    db.flush()
    content.save_page(db, site, page.id, user.id, page.draft_json, page.version)
    for action in ("publish", "unpublish"):
        with pytest.raises(CommerceError, match="access denied"):
            content.save_page(db, site, page.id, user.id, page.draft_json, page.version, action)
    with pytest.raises(CommerceError, match="Page not found"):
        content.save_page(db, other, page.id, user.id, page.draft_json, page.version)


def test_category_cascades_with_site(workspace):
    db, _, site, _ = workspace
    ensure_category(db, site, "Basics")
    site_id, tenant_id = site.id, site.tenant_id
    db.execute(delete(SitePage).where(SitePage.site_id == site_id, SitePage.tenant_id == tenant_id))
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    db.execute(delete(Site).where(Site.id == site_id, Site.tenant_id == tenant_id))
    assert not db.scalar(select(BlogCategory).where(BlogCategory.site_id == site_id, BlogCategory.tenant_id == tenant_id))


@pytest.mark.parametrize("populated", [False, True])
def test_alembic_empty_and_populated_pre_taxonomy_upgrade(tmp_path, populated):
    path = tmp_path / "blog-migration.db"
    url = "sqlite:///" + path.as_posix()

    def migrate(*args):
        result = subprocess.run([sys.executable, "-m", "alembic", *args],
            env=os.environ | {"DB_URL": url}, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

    engine = create_engine(url)
    if populated:
        migrate("upgrade", "20261008_0018")
        with Session(engine) as db:
            user = User(email="legacy-blog@example.test", name="Legacy")
            db.add(user)
            db.flush()
            site = content.create_site(db, user.id, "Legacy", "legacy-blog")
            page = article(db, site)
            page.draft_json = {"version": 1, "title": "Legacy", "blocks": [], "category": "A Curious Mind"}
            page.published_json = copy.deepcopy(page.draft_json)
            original = copy.deepcopy(page.published_json)
            site_id, tenant_id, page_id = site.id, site.tenant_id, page.id
            db.commit()
        # Earlier checkfirst migrations import live models: restore the actual old schema.
        BlogCategory.__table__.drop(engine, checkfirst=True)
    migrate("upgrade", "head")
    migrate("check")
    if populated:
        with Session(engine) as db:
            site = db.scalar(select(Site).where(Site.id == site_id, Site.tenant_id == tenant_id))
            assert [row.slug for row in categories_for(db, site)] == ["a-curious-mind"]
            assert content.site_page(db, site, page_id).published_json == original
    engine.dispose()
    path.unlink()


def test_public_filters_seo_draft_preview_and_csrf_forms():
    import re
    from uuid import uuid4

    from starlette.testclient import TestClient

    from app.config import settings
    from app.db import SessionLocal
    from app.main import app

    client = TestClient(app)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    client.post("/login", data={"csrf_token": token, "email": settings.admin_email, "password": settings.admin_password})
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, user.id, "Public blog", "blog-" + uuid4().hex[:10])
        site.status = "published"
        site.published_settings_json = copy.deepcopy(site.settings_json)
        index = content.create_page(db, site, "Journal", "/blogs/learn", "blog", {"title": "Journal", "sections": [{"type": "articles", "heading": "Articles"}]})
        content.save_page(db, site, index.id, user.id, index.draft_json, index.version, "publish")
        ensure_category(db, site, "Research")
        ensure_category(db, site, "Empty")
        live = article(db, site, "/live-story", category_slug="research", tags=["water"], author_name="Jane Reader", author_bio="Reads studies carefully.")
        content.save_page(db, site, live.id, user.id, live.draft_json, live.version, "publish")
        draft = article(db, site, "/private-story", state="draft", tags=["private-tag"])
        content.save_page(db, site, draft.id, user.id, draft.draft_json, draft.version, "publish")
        archived = article(db, site, "/archived-story", state="archived")
        content.save_page(db, site, archived.id, user.id, archived.draft_json, archived.version, "publish")
        site_id, tenant_id, draft_id, version = site.id, site.tenant_id, draft.id, draft.version
        base = f"/sites/{site.slug}"
        db.commit()
    anonymous = TestClient(app)
    for path in ("/blog", "/blog/category/research", "/blog/tag/water"):
        response = anonymous.get(base + path)
        assert response.status_code == 200
        assert "Jane Reader" in response.text
        assert base + "/live-story" in response.text
        assert base + "/private-story" not in response.text
        assert base + "/archived-story" not in response.text
        assert f'href="{settings.public_url}{base}{path}"' in response.text
    assert 'content="index,follow"' in anonymous.get(base + "/blog/category/research").text
    assert 'content="noindex,follow"' in anonymous.get(base + "/blog/tag/water").text
    assert 'content="noindex,follow"' in anonymous.get(base + "/blog/category/empty").text
    for path in ("/private-story", "/archived-story", "/blog/tag/private-tag", "/blog/category/missing", "/blog/tag/missing"):
        assert anonymous.get(base + path).status_code == 404
    assert "Reads studies carefully." in anonymous.get(base + "/live-story").text
    preview = f"/admin/sites/{site_id}/preview/{draft_id}"
    assert client.get(preview).status_code == 200
    assert 'content="noindex,nofollow"' in client.get(preview).text
    assert anonymous.get(preview).status_code != 200
    editor = f"/admin/sites/{site_id}/pages/{draft_id}"
    assert 'name="article_state"' in client.get(editor).text
    form = {"version": version, "title": "Edited privately", "action": "draft", "article_state": "published",
            "article_category": "Research", "article_tags": "water, reading", "article_author": "New Author", "article_bio": "A short biography"}
    assert "session expired" in client.post(editor, data=form).text
    response = client.post(editor, data=form | {"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.id == site_id, Site.tenant_id == tenant_id))
        saved = content.site_page(db, site, draft_id)
        assert metadata(saved.draft_json)["author_name"] == "New Author"
        assert metadata(saved.draft_json)["tags"] == ["water", "reading"]
        assert metadata(saved.published_json)["state"] == "draft"
    assert anonymous.get(base + "/private-story").status_code == 404


def test_latest_three_cards_bit_stable_with_authorship_and_listing_is_complete(workspace):
    from fastcore.xml import to_xml

    from app.site_ui import render_section

    db, user, site, _ = workspace
    pages = []
    for number in range(5):
        page = article(db, site, f"/article-{number}")
        content.save_page(db, site, page.id, user.id, page.draft_json, page.version, "publish")
        pages.append(page)
    section = {"type": "articles", "heading": "Journal"}

    def render(**kwargs):
        return str(to_xml(render_section(db, site, pages[0], site.settings_json, section, "/blog", "csrf", **kwargs)))

    before = render()
    assert before.count("<article>") == 3
    for page in pages:
        document = copy.deepcopy(page.draft_json)
        document["blog"].update(author_name="A Writer", author_bio="Article author")
        content.save_page(db, site, page.id, user.id, document, page.version, "publish")
    assert render() == before
    listing = render(blog_articles=articles_for(db, site))
    assert listing.count("<article>") == 5
    assert listing.count("By A Writer") == 5
    newest = articles_for(db, site)[0]
    document = copy.deepcopy(newest.draft_json)
    document["blog"]["state"] = "archived"
    content.save_page(db, site, newest.id, user.id, document, newest.version, "publish")
    assert f'href="/blog{newest.path}"' not in render()
    assert render().count("<article>") == 3

"""Publication, schedule and tree validation regressions for growth content."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import content, growth_content, site_menus
from app.models import Base, Membership, SitePage, User
from app.services import CommerceError
from app.site_blog import is_listed


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="growth-content@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Content", "content-growth")
        other = content.create_site(db, owner.id, "Other", "content-other")
        db.commit()
        yield db, owner, site, other
    engine.dispose()


def test_article_sanitises_and_preserves_visual_content(workspace):
    db, owner, site, _ = workspace
    page = content.create_page(db, site, "Post", "/blog/post", "article")
    doc = growth_content.article_document(
        db,
        site,
        page,
        {
            "title": "New post",
            "body": "<p>Hello <strong>reader</strong></p><script>bad()</script>",
            "author": "Editor",
            "category": "News",
            "tags": "news, guides",
            "excerpt": "An excerpt",
            "action": "publish",
        },
    )
    assert "<strong>reader</strong>" in doc["blocks"][0]["body"]
    assert "script" not in doc["blocks"][0]["body"]
    assert doc["blog"]["excerpt"] == "An excerpt"
    content.save_page(db, site, page.id, owner.id, doc, page.version, "publish")
    assert is_listed(page)
    assert page.published_json["blog"]["category_slug"] == "news"


def test_article_schedule_and_unpublish(workspace):
    db, owner, site, _ = workspace
    page = content.create_page(db, site, "Scheduled", "/blog/scheduled", "article")
    doc = growth_content.article_document(
        db,
        site,
        page,
        {
            "title": "Scheduled",
            "body": "<p>Soon</p>",
            "action": "publish",
            "publish_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    content.save_page(db, site, page.id, owner.id, doc, page.version, "publish")
    assert not is_listed(page)
    assert is_listed(page, preview=True)
    doc["blog"]["publish_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    content.save_page(db, site, page.id, owner.id, doc, page.version, "publish")
    assert is_listed(page)
    growth_content.bulk_pages(db, site, owner.id, [page.id], "unpublish")
    assert not is_listed(page)


def test_bulk_scope_and_publishing_role(workspace):
    db, owner, site, other = workspace
    foreign = content.site_pages(db, other)[0]
    with pytest.raises(CommerceError):
        growth_content.bulk_pages(db, site, owner.id, [foreign.id], "publish")
    member = db.scalar(
        select(Membership).where(
            Membership.user_id == owner.id, Membership.tenant_id == site.tenant_id
        )
    )
    member.role = "editor"
    db.flush()
    with pytest.raises(CommerceError):
        growth_content.bulk_pages(
            db, site, owner.id, [content.site_pages(db, site)[0].id], "publish"
        )


def test_tree_validation_nested_targets_duplicates_depth_and_total(workspace):
    db, _, site, other = workspace

    def link(id, **extra):
        return {"id": id, "kind": "page", "label": id, "path": "/", **extra}

    items = [link("one", children=[link("two", children=[link("three")])])]
    menu = site_menus.put_menu(db, site, "header", items, publish=True)
    assert menu.published_items_json[0]["children"][0]["id"] == "two"
    assert [i["label"] for i in site_menus.rendered_navigation(db, site, "header")] == [
        "one",
        "two",
        "three",
    ]
    foreign = content.create_page(db, other, "Private", "/private")
    for invalid in (
        [link("a", children=[link("a")])],
        [link("a", children=[link("b", children=[link("c", children=[link("d")])])])],
        [link("a", children=[link(str(i)) for i in range(40)])],
        [link("a", children=[link("b", path=foreign.path)])],
    ):
        with pytest.raises(CommerceError):
            site_menus.put_menu(db, site, "header", invalid)


def test_article_media_tenant_isolation(workspace):
    db, owner, site, other = workspace
    page = content.create_page(db, site, "Post", "/blog/post", "article")
    document = growth_content.article_document(
        db,
        site,
        page,
        {"title": "Post", "image": f"/site-media/{other.id}/foreign", "body": "Text"},
    )
    with pytest.raises(CommerceError):
        content.save_page(db, site, page.id, owner.id, document, page.version)
    assert db.scalar(select(SitePage).where(SitePage.id == page.id)).published_json is None


@pytest.fixture
def content_client(workspace, monkeypatch):
    from fasthtml.common import Input, fast_app
    from starlette.responses import Response
    from starlette.testclient import TestClient

    from app import growth_content_routes

    db, owner, site, _ = workspace
    # SQLite service fixture is confined to the TestClient's event-loop thread by
    # route handlers declared async where mutations occur. GET sessions use a
    # separate shared in-memory engine to exercise FastHTML's worker threads.
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as seeded:
        user = User(email="routes@example.test", name="Owner")
        seeded.add(user)
        seeded.flush()
        route_site = content.create_site(seeded, user.id, "Routes", "growth-routes")
        user_id, site_id = user.id, route_site.id
        seeded.commit()
    monkeypatch.setattr(
        growth_content_routes, "SessionLocal", lambda: Session(engine, expire_on_commit=False)
    )
    app, rt = fast_app(secret_key="growth-content-test")

    def check_csrf(session, data):
        if data.get("csrf_token") != "test-token":
            raise CommerceError("CSRF rejected")

    growth_content_routes.register_growth_content_routes(
        rt,
        lambda session: user_id,
        lambda session: Input(type="hidden", name="csrf_token", value="test-token"),
        check_csrf,
        lambda title, *children: children,
        lambda exc: Response(str(exc), status_code=400),
    )
    with TestClient(app) as client:
        yield client, site_id, engine
    engine.dispose()


def test_content_routes_render_and_protect_mutations(content_client):
    client, site_id, engine = content_client
    base = f"/admin/sites/{site_id}/content"
    for path, title in [
        ("pages", "Pages"),
        ("blog-posts", "Blog posts"),
        ("blog-posts/new", "Create blog post"),
        ("files", "Files"),
        ("menus", "Menus"),
    ]:
        response = client.get(base + "/" + path)
        assert response.status_code == 200, response.text
        assert title in response.text
    assert client.post(base + "/pages", data={"title": "Fail", "path": "/fail"}).status_code == 400
    assert client.get("/admin/sites/foreign/content/pages").status_code == 400
    response = client.post(
        base + "/blog-posts/new",
        data={
            "csrf_token": "test-token",
            "title": "News",
            "path": "/blog/news",
            "body": "<p>Hello</p>",
            "category": "News",
            "action": "publish",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    with Session(engine) as db:
        page = db.scalar(
            select(SitePage).where(SitePage.site_id == site_id, SitePage.path == "/blog/news")
        )
        assert page and is_listed(page)


@pytest.mark.parametrize(
    "path,data",
    [
        ("pages", {"title": "Page", "path": "/new"}),
        ("pages/bulk", {"action": "publish", "page_id": "foreign"}),
        ("blog-posts/new", {"title": "Post", "path": "/blog/new", "action": "publish"}),
        ("files", {"alt": "Image"}),
        ("files/foreign", {"action": "delete"}),
        ("menus", {"name": "header", "items": "[]", "version": "1"}),
    ],
)
def test_content_mutations_enforce_csrf_and_tenant_scope(content_client, path, data):
    client, site_id, _ = content_client
    assert client.post(f"/admin/sites/{site_id}/content/{path}", data=data).status_code == 400
    response = client.post(
        f"/admin/sites/foreign/content/{path}", data=data | {"csrf_token": "test-token"}
    )
    assert response.status_code == 400
    assert "access denied" in response.text


def test_create_menu_cannot_overwrite_existing_tree(content_client):
    from app.models import Site
    from app.site_menus import get_menu
    client, site_id, engine = content_client
    with Session(engine) as db:
        site = db.get(Site, site_id)
        version = site.version
        before = get_menu(db, site, "header").items_json
    response = client.post(f"/admin/sites/{site_id}/content/menus", data={
        "csrf_token": "test-token", "name": "header", "items": "[]",
        "action": "create", "version": str(version)})
    assert response.status_code == 400 and "already exists" in response.text
    with Session(engine) as db:
        assert get_menu(db, db.get(Site, site_id), "header").items_json == before

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import connectors, content
from app.config import settings
from app.db import SessionLocal
from app.integrations.wordpress import (
    MAX_PAGES,
    PAGE_SIZE,
    WordPressConnector,
    WordPressGateway,
    _fixture_transport,
    html_to_blocks,
)
from app.main import app
from app.models import (
    Base,
    BlogCategory,
    ExternalMapping,
    IntegrationPlan,
    SiteMedia,
    SitePage,
    User,
)
from app.services import CommerceError
from app.site_blog import metadata

FIXTURE = Path(__file__).parent / "fixtures" / "wordpress_site.json"
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "wp": "http://wordpress.org/export/1.2/",
}


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="wordpress-owner@example.test", name="WordPress owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "WordPress target", "wordpress-target")
        prefix = f"FASTSHOP_WORDPRESS_{site.id.upper()}_"
        monkeypatch.setenv(prefix + "ENABLED", "true")
        monkeypatch.setenv(prefix + "REST_BASE_URL", "https://source.wordpress.example/wp-json/wp/v2")
        db.commit()
        yield db, owner, site
    engine.dispose()


def test_gateway_uses_https_rest_base_and_optional_basic_auth(workspace, monkeypatch):
    _, _, site = workspace
    prefix = f"FASTSHOP_WORDPRESS_{site.id.upper()}_"
    monkeypatch.setenv(prefix + "USERNAME", "api-editor")
    monkeypatch.setenv(prefix + "APPLICATION_PASSWORD", "operator-only-secret")
    seen = {}

    def respond(request):
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization", "")
        seen["context"] = request.url.params.get("context")
        seen["status"] = request.url.params.get("status")
        return httpx.Response(200, json=[], headers={"X-WP-TotalPages": "1"})

    gateway = WordPressGateway(
        site, tenant_id=site.tenant_id, transport=httpx.MockTransport(respond)
    )
    rows, pages = gateway.list_resources("posts")
    assert rows == [] and pages == 1
    assert seen["path"] == "/wp-json/wp/v2/posts"
    assert seen["authorization"].startswith("Basic ")
    assert seen["context"] == "edit"
    assert seen["status"] == "publish,draft,pending,private,future"
    assert "operator-only-secret" not in repr(seen)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com/wp-json/wp/v2",
        "https://localhost/wp-json/wp/v2",
        "https://127.0.0.1/wp-json/wp/v2",
        "https://example.com/not-wordpress",
        "https://user:pass@example.com/wp-json/wp/v2",
        "https://example.com/wp-json/wp/v2?context=edit",
    ],
)
def test_invalid_rest_base_is_not_ready(workspace, monkeypatch, base_url):
    _, _, site = workspace
    prefix = f"FASTSHOP_WORDPRESS_{site.id.upper()}_"
    monkeypatch.setenv(prefix + "REST_BASE_URL", base_url)
    assert WordPressConnector().credential_state(site).configured is False
    with pytest.raises(CommerceError):
        WordPressConnector().fetch(site, transport=httpx.MockTransport(lambda request: None))


def test_html_to_blocks_sanitizes_and_degrades_supported_shapes():
    source = """
        <p onclick="bad()">Opening <strong>paragraph</strong><br>second line
        <a href="javascript:alert(1)">unsafe link</a>.</p>
        <h2>Useful heading</h2>
        <ul><li>First item</li><li>Second <em>item</em></li></ul>
        <img src="https://cdn.example.test/photo.jpg" onerror="bad()" alt="A photo">
        <script>window.stolen = true</script>
        [gallery ids="1,2"]
        <iframe src="https://video.example.test/embed"></iframe>
    """
    blocks, media, warnings = html_to_blocks(source)
    assert [block["type"] for block in blocks] == ["text", "text", "text", "split"]
    assert "Opening paragraph" in blocks[0]["body"]
    assert "javascript:" not in json.dumps(blocks)
    assert blocks[1]["heading"] == "Useful heading"
    assert "• First item" in blocks[2]["body"]
    assert blocks[3]["image"] == "https://cdn.example.test/photo.jpg"
    assert media == [{
        "url": "https://cdn.example.test/photo.jpg", "alt": "A photo", "title": "",
    }]
    assert any("unsafe link" in warning.lower() for warning in warnings)
    assert any("shortcode" in warning.lower() for warning in warnings)
    assert any("<script>" in warning for warning in warnings)
    assert any("<iframe>" in warning for warning in warnings)
    assert "window.stolen" not in json.dumps(blocks)


def test_dry_run_apply_and_reimport_are_idempotent(workspace):
    db, owner, site = workspace
    pages_before = db.scalar(select(func.count()).select_from(SitePage).where(
        SitePage.tenant_id == site.tenant_id, SitePage.site_id == site.id
    ))
    media_before = db.scalar(select(func.count()).select_from(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id, SiteMedia.site_id == site.id
    ))
    plan = connectors.create_dry_run(
        db, site, owner.id, "wordpress", transport=_fixture_transport(FIXTURE)
    )
    assert plan.report_json["counts"]["posts"] == {
        "fetched": 2, "create": 2, "update": 0, "skip": 0,
    }
    assert plan.report_json["counts"]["pages"] == {
        "fetched": 1, "create": 1, "update": 0, "skip": 0,
    }
    assert plan.report_json["counts"]["media"]["fetched"] == 3
    assert any("untrusted text" in warning for warning in plan.report_json["warnings"])
    assert any("shortcode" in warning.lower() for warning in plan.report_json["warnings"])
    assert db.scalar(select(func.count()).select_from(SitePage).where(
        SitePage.tenant_id == site.tenant_id, SitePage.site_id == site.id
    )) == pages_before
    assert db.scalar(select(func.count()).select_from(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id, SiteMedia.site_id == site.id
    )) == media_before

    _, applied = connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version)
    db.flush()
    assert applied["posts"] == {"created": 2, "updated": 0}
    assert applied["pages"] == {"created": 1, "updated": 0}
    assert applied["media"] == {"created": 3, "updated": 0}
    pages = list(db.scalars(select(SitePage).where(
        SitePage.tenant_id == site.tenant_id, SitePage.site_id == site.id
    )))
    assert len(pages) == pages_before + 3
    article = next(page for page in pages if page.path == "/blogs/learn/field-guide-to-spring-water")
    draft = next(page for page in pages if page.path == "/blogs/learn/unfinished-reading-notes")
    assert article.published_json is not None
    assert metadata(article.published_json) == {
        "state": "published",
        "category_slug": "field-notes",
        "tags": ["water", "reading"],
        "author_name": "Mara Field",
        "author_bio": "",
    }
    assert draft.published_json is None and metadata(draft.draft_json)["state"] == "draft"
    assert db.scalar(select(func.count()).select_from(BlogCategory).where(
        BlogCategory.tenant_id == site.tenant_id,
        BlogCategory.site_id == site.id,
    )) == 3
    assert db.scalar(select(func.count()).select_from(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.site_id == site.id,
    )) == 3

    totals = {
        model: db.scalar(select(func.count()).select_from(model))
        for model in (SitePage, SiteMedia, BlogCategory, ExternalMapping)
    }
    second = connectors.create_dry_run(
        db, site, owner.id, "wordpress", transport=_fixture_transport(FIXTURE)
    )
    assert all(
        second.report_json["counts"][name]["create"] == 0
        for name in ("categories", "tags", "authors", "media", "posts", "pages")
    )
    _, reapplied = connectors.apply_reviewed_plan(
        db, site, owner.id, second.id, second.version
    )
    db.flush()
    assert reapplied["posts"] == {"created": 0, "updated": 2}
    assert {
        model: db.scalar(select(func.count()).select_from(model)) for model in totals
    } == totals


def test_mapping_and_apply_are_site_tenant_scoped(workspace, monkeypatch):
    db, owner, site = workspace
    other_owner = User(email="other-wordpress@example.test", name="Other owner")
    db.add(other_owner)
    db.flush()
    other = content.create_site(db, other_owner.id, "Other WordPress", "other-wordpress")
    prefix = f"FASTSHOP_WORDPRESS_{other.id.upper()}_"
    monkeypatch.setenv(prefix + "ENABLED", "true")
    monkeypatch.setenv(prefix + "REST_BASE_URL", "https://other.example.test/wp-json/wp/v2")
    plan = connectors.create_dry_run(
        db, other, other_owner.id, "wordpress", transport=_fixture_transport(FIXTURE)
    )
    connectors.apply_reviewed_plan(db, other, other_owner.id, plan.id, plan.version)
    assert db.scalar(select(func.count()).select_from(ExternalMapping).where(
        ExternalMapping.tenant_id == other.tenant_id,
        ExternalMapping.site_id == other.id,
        ExternalMapping.system == "wordpress",
    )) > 0
    assert db.scalar(select(func.count()).select_from(ExternalMapping).where(
        ExternalMapping.tenant_id == site.tenant_id,
        ExternalMapping.site_id == site.id,
        ExternalMapping.system == "wordpress",
    )) == 0
    with pytest.raises(CommerceError):
        connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version + 1)


def test_unmapped_local_category_and_media_metadata_are_not_overwritten(workspace):
    db, owner, site = workspace
    local_category = BlogCategory(
        tenant_id=site.tenant_id,
        site_id=site.id,
        slug="field-notes",
        name="Merchant field notes",
    )
    local_media = SiteMedia(
        tenant_id=site.tenant_id,
        site_id=site.id,
        title="Merchant cover title",
        alt="Merchant-authored alt",
        public_url="https://images.wordpress.example/spring-water-cover.jpg",
        localized_alt="Merchant-authored alt",
        content_type="image/jpeg",
        storage_key="merchant-cover",
        size=0,
        data=None,
        is_placeholder=False,
    )
    db.add_all([local_category, local_media])
    db.flush()
    plan = connectors.create_dry_run(
        db, site, owner.id, "wordpress", transport=_fixture_transport(FIXTURE)
    )
    assert plan.report_json["counts"]["media"] == {
        "fetched": 3, "create": 2, "update": 1, "skip": 0,
    }
    connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version)
    db.flush()
    assert local_category.name == "Merchant field notes"
    assert local_media.title == "Merchant cover title"
    assert local_media.alt == "Merchant-authored alt"
    imported_category = db.scalar(select(BlogCategory).join(
        ExternalMapping, ExternalMapping.local_id == BlogCategory.id
    ).where(
        ExternalMapping.tenant_id == site.tenant_id,
        ExternalMapping.site_id == site.id,
        ExternalMapping.system == "wordpress",
        ExternalMapping.resource_type == "category",
        ExternalMapping.external_id == "11",
    ))
    assert imported_category.id != local_category.id
    assert imported_category.slug == "field-notes-wordpress-11"
    article = db.scalar(select(SitePage).where(
        SitePage.tenant_id == site.tenant_id,
        SitePage.site_id == site.id,
        SitePage.path == "/blogs/learn/field-guide-to-spring-water",
    ))
    assert metadata(article.published_json)["category_slug"] == imported_category.slug


def test_pagination_ceiling_is_enforced(workspace):
    _, _, site = workspace

    def respond(request):
        rows = [{"id": index} for index in range(PAGE_SIZE)]
        return httpx.Response(
            200,
            json=rows,
            headers={"X-WP-TotalPages": str(MAX_PAGES + 1)},
        )

    gateway = WordPressGateway(
        site, tenant_id=site.tenant_id, transport=httpx.MockTransport(respond)
    )
    with pytest.raises(CommerceError, match="5-page import limit"):
        gateway.paged("posts")


def test_wxr_export_is_well_formed_bounded_and_structurally_equivalent(workspace):
    db, owner, site = workspace
    plan = connectors.create_dry_run(
        db, site, owner.id, "wordpress", transport=_fixture_transport(FIXTURE)
    )
    connectors.apply_reviewed_plan(db, site, owner.id, plan.id, plan.version)
    published = connectors.export_artifact(db, site, "wordpress")
    with_drafts = connectors.export_artifact(db, site, "wordpress", include_drafts=True)
    assert published.media_type == "application/xml"
    assert published.filename.endswith("-published.xml")
    assert b"<![CDATA[" in published.content
    published_root = ET.fromstring(published.content)
    draft_root = ET.fromstring(with_drafts.content)
    published_items = published_root.findall("./channel/item")
    all_items = draft_root.findall("./channel/item")
    assert len(published_items) == 2
    assert len(all_items) == len([
        page for page in content.site_pages(db, site)
        if page.kind in {"home", "content", "science", "blog", "contact", "legal", "article"}
    ])

    structure = {
        (
            item.findtext("title"),
            item.findtext("wp:post_type", namespaces=NS),
            item.findtext("wp:status", namespaces=NS),
        )
        for item in all_items
    }
    assert structure >= {
        ("A field guide to spring water", "post", "publish"),
        ("Unfinished reading notes", "post", "draft"),
        ("Water library", "page", "publish"),
    }
    article = next(item for item in all_items if item.findtext("title") == "A field guide to spring water")
    exported_html = article.findtext("content:encoded", namespaces=NS)
    assert "<script" not in exported_html and "onclick" not in exported_html
    round_trip_blocks, _, round_trip_warnings = html_to_blocks(exported_html)
    assert round_trip_blocks and not any("unsafe" in warning.lower() for warning in round_trip_warnings)
    terms = {
        (node.get("domain"), node.get("nicename"), node.text)
        for node in article.findall("category")
    }
    assert ("category", "field-notes", "Field Notes") in terms
    assert ("post_tag", "water", "water") in terms
    assert article.find("enclosure") is not None


def _signed_in():
    client = TestClient(app)
    login = client.get("/login?next=/admin/sites")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.text).group(1)
    response = client.post("/login", data={
        "csrf_token": token,
        "email": settings.admin_email,
        "password": settings.admin_password,
    })
    assert response.status_code == 200
    return client, token


def test_integration_routes_show_wordpress_apply_and_download_wxr(monkeypatch):
    client, token = _signed_in()
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(
            db,
            owner.id,
            "WordPress route " + uuid4().hex[:6],
            "wordpress-" + uuid4().hex[:8],
        )
        site_id = site.id
        db.commit()
    base = f"/admin/sites/{site_id}/integrations"
    missing = client.get(base)
    assert missing.status_code == 200
    assert "WordPress" in missing.text and "Needs operator setup" in missing.text

    monkeypatch.setenv("FASTSHOP_WORDPRESS_FIXTURE_PATH", str(FIXTURE))
    page = client.get(base)
    assert "Development fixture site ready." in page.text
    assert "APPLICATION_PASSWORD" not in page.text
    denied = client.post(base + "/wordpress/dry-run", data={}, follow_redirects=False)
    assert denied.status_code == 303 and "session+expired" in denied.headers["location"]
    preview = client.post(
        base + "/wordpress/dry-run", data={"csrf_token": token}, follow_redirects=False
    )
    assert preview.status_code == 303 and "plan=" in preview.headers["location"]
    review = client.get(preview.headers["location"])
    assert "Reviewed import plan" in review.text and "Posts" in review.text
    plan_id = re.search(r'name="plan_id" value="([^"]+)"', review.text).group(1)
    version = re.search(r'name="plan_version" value="([^"]+)"', review.text).group(1)
    applied = client.post(base + "/wordpress/apply", data={
        "csrf_token": token,
        "plan_id": plan_id,
        "plan_version": version,
        "confirmed": "on",
    }, follow_redirects=False)
    assert applied.status_code == 303 and "successfully" in applied.headers["location"]
    export = client.get(base + "/wordpress/export?include_drafts=1")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/xml")
    assert "attachment" in export.headers["content-disposition"]
    exported_titles = {
        item.findtext("title")
        for item in ET.fromstring(export.content).findall("./channel/item")
    }
    assert {
        "A field guide to spring water", "Unfinished reading notes", "Water library",
    } <= exported_titles
    with SessionLocal() as db:
        plan = db.get(IntegrationPlan, plan_id)
        assert plan.status == "applied" and plan.consumed_at is not None

"""Security and persistence contracts for the visual editor's JSON surface."""

import json
from types import SimpleNamespace

import pytest
from fasthtml.common import Input, fast_app
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import content, site_editor_routes
from app.models import Base, Membership, SitePage, User
from app.services import CommerceError


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="editor-owner@example.test", name="Editor owner")
        outsider = User(email="editor-outsider@example.test", name="Outsider")
        db.add_all([owner, outsider])
        db.flush()
        site = content.create_site(db, owner.id, "Editor store", "editor-store")
        other = content.create_site(db, outsider.id, "Other store", "other-editor-store")
        page = content.site_pages(db, site)[0]
        other_page = content.site_pages(db, other)[0]
        db.commit()
    monkeypatch.setattr(site_editor_routes, "SessionLocal", sessions)
    app, rt = fast_app(secret_key="editor-test-only")
    state = SimpleNamespace(user=owner.id)

    def actor(session):
        if not state.user:
            raise CommerceError("Sign in to manage your sites.")
        return state.user

    def csrf(session):
        session["csrf_token"] = "editor-csrf"
        return Input(name="csrf_token", value="editor-csrf")

    def check_csrf(session, form):
        if form.get("csrf_token") != "editor-csrf":
            raise CommerceError("Your session expired.")

    site_editor_routes.register_site_editor_routes(rt, actor, csrf, check_csrf,
        lambda *args: args, lambda exc: Response(str(exc), status_code=400))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, site=site, page=page,
            other=other, other_page=other_page, state=state, owner=owner,
            base=f"/admin/sites/{site.id}/editor/api", headers={"X-CSRF-Token": "editor-csrf"})
    engine.dispose()


def load(w):
    result = w.client.get(w.base + "/document", params={"page": w.page.id})
    assert result.status_code == 200, result.text
    return result.json()


def test_editor_page_switcher_labels_the_current_page_and_links_to_page_management(workspace):
    w = workspace
    result = w.client.get(f"/admin/sites/{w.site.id}/editor", params={"page": w.page.id})
    assert result.status_code == 200
    assert 'aria-label="Switch the page you are editing"' in result.text
    assert '<span class="page-switcher-label">Editing</span>' in result.text
    assert f'href="/admin/sites/{w.site.id}/pages"' in result.text


def test_load_requires_active_actor_membership_and_scoped_page(workspace):
    w = workspace
    loaded = load(w)
    assert loaded["schema"] and loaded["can_publish"]
    assert loaded["page"]["id"] == w.page.id
    assert w.client.get(w.base + "/document", params={"page": w.other_page.id}).status_code == 404
    assert w.client.get(f"/admin/sites/{w.other.id}/editor/api").status_code == 403
    w.state.user = None
    assert w.client.get(w.base).status_code == 401


def test_unsaved_render_does_not_write_and_rejects_foreign_media(workspace):
    w = workspace
    loaded = load(w)
    document = loaded["document"]
    document["blocks"] = [{"id": "unsaved", "type": "text", "heading": "Unsaved heading", "body": "Draft body"}]
    result = w.client.post(w.base + "/render", params={"page": w.page.id},
                           json={"document": document}, headers=w.headers)
    assert result.status_code == 200, result.text
    assert "Unsaved heading" in result.json()["html"]
    assert "canvas.js" in result.json()["html"]
    with w.sessions() as db:
        assert "Unsaved heading" not in str(db.get(SitePage, w.page.id).draft_json)
    document["blocks"][0]["image"] = f"/site-media/{w.other.id}/asset"
    result = w.client.post(w.base + "/render", json={"document": document}, headers=w.headers)
    assert result.status_code == 422
    assert "belonging" in result.text


def test_canvas_navigation_config_contains_only_current_site_pages(workspace):
    w = workspace
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        target = content.create_page(db, site, "Destination", "/pages/destination",
                                     document={"version": 1, "title": "Destination", "blocks": []})
        db.commit()
    result = w.client.post(w.base + "/render", json={"document": load(w)["document"]}, headers=w.headers)
    assert result.status_code == 200, result.text
    marker = '<script id="editor-canvas-config" type="application/json">'
    config = json.loads(result.json()["html"].split(marker, 1)[1].split("</script>", 1)[0])
    assert config["siteRoot"] == f"/sites/{w.site.slug}"
    page_ids = {page["id"] for page in config["pages"]}
    assert {w.page.id, target.id} <= page_ids
    assert w.other_page.id not in page_ids
    assert all(page["path"].startswith("/") for page in config["pages"])


def test_csrf_draft_conflict_revisions_and_restore(workspace):
    w = workspace
    loaded = load(w)
    values = {"document": loaded["document"], "version": loaded["version"]}
    assert w.client.put(w.base + "/draft", json=values).status_code == 403
    result = w.client.put(w.base + "/draft", json=values, headers=w.headers)
    assert result.status_code == 200, result.text
    version = result.json()["version"]
    assert version == loaded["version"] + 1
    conflict = w.client.put(w.base + "/draft", json=values, headers=w.headers)
    assert conflict.status_code == 409 and conflict.json()["version"] == version
    revisions = w.client.get(w.base + "/revisions").json()["revisions"]
    assert revisions and revisions[0]["action"] == "draft"
    restored = w.client.post(w.base + "/restore", json={"revision_id": revisions[0]["id"],
                             "version": version}, headers=w.headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == version + 1
    assert w.client.post(w.base + "/restore", json={"revision_id": "unknown",
                         "version": version + 1}, headers=w.headers).status_code == 422


def test_publish_compliance_and_editor_role(workspace):
    w = workspace
    values = load(w)
    document = values["document"]
    document["blocks"] = [{"id": "bad-copy", "type": "text", "heading": "A miracle cure", "body": "Content"}]
    rejected = w.client.post(w.base + "/publish", json={"document": document,
        "version": values["version"]}, headers=w.headers)
    assert rejected.status_code == 422 and rejected.json()["errors"]
    assert "cannot be published" in rejected.text
    with w.sessions() as db:
        member = db.scalar(select(Membership).where(Membership.user_id == w.owner.id,
                                                     Membership.tenant_id == w.site.tenant_id))
        member.role = "editor"
        db.commit()
    assert load(w)["can_publish"] is False
    assert w.client.post(w.base + "/publish", json={"version": values["version"]},
                         headers=w.headers).status_code == 403
    assert w.client.put(w.base + "/draft", json={"document": document,
        "version": values["version"]}, headers=w.headers).status_code == 200


def test_theme_validation_versions_and_media_scope(workspace):
    w = workspace
    load(w)
    theme = w.client.get(w.base + "/theme-settings").json()
    values = {"theme_settings": theme["theme_settings"] | {"accent": "#cc8833"},
              "version": theme["version"]}
    saved = w.client.put(w.base + "/theme-settings", json=values, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] > theme["version"]
    assert w.client.put(w.base + "/theme-settings", json=values, headers=w.headers).status_code == 409
    assert w.client.get(w.base + "/media").status_code == 200
    assert w.client.get(f"/admin/sites/{w.other.id}/editor/api/media").status_code == 403

def test_publish_success_and_revision_isolation(workspace):
    w = workspace
    loaded = load(w)
    published = w.client.post(w.base + "/publish", json={"version": loaded["version"]},
                              headers=w.headers)
    assert published.status_code == 200, published.text
    assert published.json()["published"] is True
    with w.sessions() as db:
        page = db.get(SitePage, w.page.id)
        assert page.published_json and page.version == loaded["version"] + 1
    revision = w.client.get(w.base + "/revisions").json()["revisions"][0]
    other_base = f"/admin/sites/{w.other.id}/editor/api"
    assert w.client.post(other_base + "/restore", json={"revision_id": revision["id"],
                         "version": 1}, headers=w.headers).status_code == 403


def test_media_upload_reuses_image_validation_and_site_library(workspace):
    import io

    from PIL import Image

    w = workspace
    load(w)
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(output, format="PNG")
    image = output.getvalue()
    assert w.client.post(w.base + "/media", files={"file": ("blue.png", image, "image/png")},
                         data={"alt": "Blue sample"}).status_code == 403
    invalid = w.client.post(w.base + "/media", files={"file": ("bad.png", b"invalid", "image/png")},
                            data={"alt": "Bad sample"}, headers=w.headers)
    assert invalid.status_code == 422
    uploaded = w.client.post(w.base + "/media", files={"file": ("blue.png", image, "image/png")},
                             data={"alt": "Blue sample", "title": "Blue"}, headers=w.headers)
    assert uploaded.status_code == 201, uploaded.text
    media = uploaded.json()["media"]
    assert media["content_type"] == "image/webp"
    assert media["url"].startswith(f"/site-media/{w.site.id}/")
    assert any(row["id"] == media["id"] for row in w.client.get(w.base + "/media").json()["media"])

def test_render_fragment_and_invalid_json_are_bounded(workspace):
    w = workspace
    document = load(w)["document"]
    document["blocks"] = [{"id": "fragment", "type": "text", "heading": "Fragment heading", "body": "Fragment body"}]
    rendered = w.client.post(w.base + "/render", json={"document": document,
                            "block_id": "fragment"}, headers=w.headers)
    assert rendered.status_code == 200, rendered.text
    assert "Fragment heading" in rendered.json()["html"]
    assert "data-block-id" in rendered.json()["html"]
    assert "canvas.js" not in rendered.json()["html"]
    assert w.client.post(w.base + "/render", json={"document": document,
        "block_id": "unknown"}, headers=w.headers).status_code == 404
    invalid = w.client.put(w.base + "/draft", content="{", headers=w.headers)
    assert invalid.status_code == 422
    assert w.client.put(w.base + "/draft", json=[], headers=w.headers).status_code == 422

def test_restore_never_changes_publication_and_cannot_cross_pages(workspace):
    w = workspace
    values = load(w)
    published = w.client.post(w.base + "/publish", json={"version": values["version"]},
                              headers=w.headers)
    assert published.status_code == 200
    original = published.json()["document"]
    with w.sessions() as db:
        public_snapshot = db.get(SitePage, w.page.id).published_json
        site = content.owned_site(db, w.site.id, w.owner.id)
        sibling = content.create_page(db, site, "Sibling page", "/sibling")
        content.save_page(db, site, sibling.id, w.owner.id, sibling.draft_json, sibling.version)
        from app.models import SiteRevision
        foreign_revision = db.scalar(select(SiteRevision).where(SiteRevision.page_id == sibling.id))
        foreign_id = foreign_revision.id
        db.commit()
    changed = dict(original)
    changed["blocks"] = [{"id": "restored-heading", "type": "text", "heading": "Changed draft", "body": "Draft content"}]
    draft = w.client.put(w.base + "/draft", json={"document": changed,
        "version": published.json()["version"]}, headers=w.headers)
    assert draft.status_code == 200
    version = draft.json()["version"]
    rejected = w.client.post(w.base + "/restore", json={"revision_id": foreign_id,
        "version": version}, headers=w.headers)
    assert rejected.status_code == 422 and "Revision not found" in rejected.text
    own_revision = next(r for r in w.client.get(w.base + "/revisions").json()["revisions"]
                        if r["action"] == "publish")
    restored = w.client.post(w.base + f"/revisions/{own_revision['id']}/restore",
        json={"version": version}, headers=w.headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["document"] == original
    with w.sessions() as db:
        page = db.get(SitePage, w.page.id)
        assert page.published_json == public_snapshot
        assert page.version == version + 1

def test_resource_picker_scopes_products_and_uses_published_collections(workspace):
    from app.models import (
        Category,
        Collection,
        Product,
        ProductType,
        ProductVariant,
        VariantChannelListing,
    )

    w = workspace
    load(w)
    with w.sessions() as db:
        site = content.owned_site(db, w.site.id, w.owner.id)
        category = Category(tenant_id=site.tenant_id, name="Sample grouping", slug="samples")
        product_type = ProductType(tenant_id=site.tenant_id, name="Sample product type", slug="sample-type")
        db.add_all([category, product_type])
        db.flush()
        product = Product(tenant_id=site.tenant_id, category_id=category.id,
            product_type_id=product_type.id, name="Sample product", slug="sample-product", is_published=True)
        hidden = Product(tenant_id=site.tenant_id, category_id=category.id,
            product_type_id=product_type.id, name="Unlinked private product", slug="unlinked", is_published=True)
        db.add_all([product, hidden])
        db.flush()
        variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id,
                                 sku="sample-standard", name="Standard", is_active=True)
        db.add(variant)
        db.flush()
        db.add(VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id,
                                     currency="EUR", price_minor=1999))
        page = content.create_page(db, site, product.name, "/products/" + product.slug, "product")
        page.product_id = product.id
        content.save_page(db, site, page.id, w.owner.id, page.draft_json, page.version, "publish")
        category_page = content.create_page(db, site, "Samples", "/samples", "collection",
            document={"title": "Samples", "category": "samples", "sections": []})
        content.save_page(db, site, category_page.id, w.owner.id,
                          category_page.draft_json, category_page.version, "publish")
        collection = Collection(tenant_id=site.tenant_id, title="Sample collection", handle="samples")
        db.add_all([collection,
            Collection(tenant_id=site.tenant_id, title="Private collection", handle="private", is_published=False),
            Collection(tenant_id=w.other.tenant_id, title="Foreign collection", handle="foreign")])
        db.flush()
        category_page.collection_id = collection.id
        product_id, collection_id = product.id, collection.id
        db.commit()
    result = w.client.get(w.base + "/resources").json()
    assert [p["id"] for p in result["products"]] == [product_id]
    assert result["collections"] == [{"id": collection_id, "title": "Sample collection",
        "name": "Sample collection", "path": "/samples", "source": "collection"}]
    assert result["collection_label"] == "Collection"
    assert w.client.get(w.base + "/resources?q=not-found").json()["collections"] == []
    assert len(w.client.get(w.base + "/resources?q=collection").json()["collections"]) == 1
    assert w.client.get(w.base + "/resources?q=not-found").json()["products"] == []
    assert w.client.get(f"/admin/sites/{w.other.id}/editor/api/resources").status_code == 403


def test_validation_errors_identify_nested_block_path(workspace):
    w = workspace
    loaded = load(w)
    document = loaded["document"]
    document["blocks"] = [{"id": "parent", "type": "container", "children": [
        {"id": "child", "type": "heading", "heading": "Heading", "style": {"padding_top": -1}}]}]
    result = w.client.put(w.base + "/draft", json={"document": document,
                         "version": loaded["version"]}, headers=w.headers)
    assert result.status_code == 422
    assert result.json()["errors"][0]["path"] == "blocks[0].children[0]"

def test_publish_promotes_only_design_and_guards_site_version(workspace):
    from app.models import Site

    w = workspace
    loaded = load(w)
    before = w.client.get(w.base + "/theme-settings").json()
    theme = before["theme_settings"] | {"accent": "#aabb22", "radius": "rounded"}
    saved = w.client.put(w.base + "/theme-settings", json={"theme_settings": theme,
        "version": before["version"]}, headers=w.headers)
    assert saved.status_code == 200, saved.text
    site_version = saved.json()["version"]
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        published_before = dict(site.published_settings_json)
        site.settings_json = site.settings_json | {"email": "draft-only@example.test",
                                                   "operational_draft": "Keep private"}
        db.commit()
    stale = w.client.post(w.base + "/publish", json={"version": loaded["version"],
        "site_version": before["version"]}, headers=w.headers)
    assert stale.status_code == 409, stale.text
    assert stale.json()["site_version"] == site_version
    with w.sessions() as db:
        page = db.get(SitePage, w.page.id)
        assert page.published_json is None and page.version == loaded["version"]
        assert db.get(Site, w.site.id).published_settings_json == published_before
    published = w.client.post(w.base + "/publish", json={"version": loaded["version"],
        "site_version": site_version}, headers=w.headers)
    assert published.status_code == 200, published.text
    assert published.json()["site_version"] == site_version + 1
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.published_settings_json == published_before | {"design": theme}
        assert site.published_settings_json["email"] != "draft-only@example.test"
        assert "operational_draft" not in site.published_settings_json
        assert db.get(SitePage, w.page.id).published_json


def test_editor_cannot_publish_draft_design(workspace):
    from app.models import Site

    w = workspace
    loaded = load(w)
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        site.settings_json = site.settings_json | {"design": {"accent": "#aabb22"}}
        before = dict(site.published_settings_json)
        membership = db.scalar(select(Membership).where(Membership.user_id == w.owner.id,
            Membership.tenant_id == site.tenant_id))
        membership.role = "editor"
        version = site.version
        db.commit()
    denied = w.client.post(w.base + "/publish", json={"version": loaded["version"],
        "site_version": version}, headers=w.headers)
    assert denied.status_code == 403
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.published_settings_json == before and site.version == version
        assert db.get(SitePage, w.page.id).published_json is None


@pytest.mark.parametrize("body", [[], [1], None, "text", 42, True])
@pytest.mark.parametrize("path,method", [
    ("/draft", "put"), ("/render", "post"), ("/publish", "post"),
    ("/restore", "post"), ("/revisions/missing/restore", "post"), ("/theme-settings", "put"),
])
def test_editor_json_endpoints_reject_non_objects(workspace, body, path, method):
    w = workspace
    result = getattr(w.client, method)(w.base + path, content=json.dumps(body),
        headers=w.headers | {"Content-Type": "application/json"})
    assert result.status_code == 422
    assert result.json()["error"] == "Send a JSON object."


def test_editor_chrome_hides_controls_without_a_backend(workspace):
    w = workspace
    result = w.client.get(f"/admin/sites/{w.site.id}/editor", params={"page": w.page.id})
    assert result.status_code == 200
    html = result.text
    # No assistant or SEO page in this installation, so neither control renders.
    assert 'data-mode="assistant"' not in html and "Ctrl+I" not in html
    assert 'id="seo-link"' not in html
    config = json.loads(html.split("window.FASTSHOP_EDITOR=", 1)[1].split(";</script>", 1)[0])
    assert config["features"] == {"assistant": False, "seo_href": ""}
    # Resizable panels, the icon picker and the rich text template are present.
    for target in ("resize-left", "resize-right", "editor-left", "editor-right", "icon-dialog",
                   "rich-text-template-holder"):
        assert f'id="{target}"' in html
    assert 'role="separator"' in html and 'aria-orientation="vertical"' in html
    # Lucide icons replace hand-drawn glyphs in the chrome.
    assert 'class="fs-icon"' in html and 'stroke-width="1.75"' in html
    for glyph in ("☷", "⊞", "◐", "↶", "↷", "⌘"):
        assert glyph not in html
    # The rich text toolbar offers only formatting the storefront keeps.
    assert "insertHorizontalRule" not in html and "data-rich-target" not in html


def test_canvas_bridge_loads_the_rich_text_editor_as_a_module(workspace):
    w = workspace
    document = load(w)["document"]
    rendered = w.client.post(w.base + "/render", json={"document": document}, headers=w.headers)
    assert rendered.status_code == 200, rendered.text
    html = rendered.json()["html"]
    assert 'id="rich-text-template-holder"' in html
    assert '<script type="module" src="/static/editor/canvas.js"></script>' in html


def test_icon_picker_upload_is_sanitised_and_scoped_to_the_site(workspace):
    from app.models import SiteMedia

    w = workspace
    library = w.client.get(w.base + "/icons")
    assert library.status_code == 200, library.text
    icons = library.json()["icons"]
    assert len(icons) >= 300
    truck = next(icon for icon in icons if icon["id"] == "lucide:truck")
    assert "<svg" in truck["svg"] and "currentColor" in truck["svg"]

    unsafe = b'''<svg viewBox="0 0 24 24" onload="alert(1)">
      <script>alert(1)</script><foreignObject><p>unsafe</p></foreignObject>
      <path d="M1 1h2" onclick="alert(1)" stroke="url(https://bad.example/icon)"/>
      <image href="https://bad.example/icon.png"/>
    </svg>'''
    assert w.client.post(w.base + "/icons", files={"file": ("unsafe.svg", unsafe, "image/svg+xml")}).status_code == 403
    uploaded = w.client.post(
        w.base + "/icons", files={"file": ("unsafe.svg", unsafe, "image/svg+xml")},
        data={"title": "Safe mark", "alt": "Safe custom icon"}, headers=w.headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    own_icon = uploaded.json()["media"]
    assert own_icon["content_type"] == "image/svg+xml"
    assert own_icon["url"].startswith(f"/site-media/{w.site.id}/")
    assert "<path" in own_icon["svg"] and "currentColor" in own_icon["svg"]
    rejected = w.client.post(w.base + "/icons", files={"file": ("icon.gif", b"GIF89a", "image/gif")},
                             headers=w.headers)
    assert rejected.status_code == 422

    with w.sessions() as db:
        stored = db.get(SiteMedia, own_icon["id"])
        assert stored.tenant_id == w.site.tenant_id and stored.site_id == w.site.id
        safe = stored.data.decode("utf-8")
        foreign = SiteMedia(
            tenant_id=w.other.tenant_id, site_id=w.other.id, title="Other icon", alt="Other icon",
            storage_key="other-icon.svg", content_type="image/svg+xml", size=6, data=b"<svg/>",
        )
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    for unsafe_part in ("script", "foreignobject", "onload", "https://bad.example"):
        assert unsafe_part not in safe.lower()

    custom = w.client.get(w.base + "/icons").json()["custom"]
    assert own_icon["id"] in {media["id"] for media in custom}
    assert foreign_id not in {media["id"] for media in custom}
    assert w.client.get(f"/admin/sites/{w.other.id}/editor/api/icons").status_code == 403

    loaded = load(w)
    document = loaded["document"]
    document["blocks"] = [{"id": "why", "type": "icon_box", "items": [
        {"icon": f"/site-media/{w.other.id}/{foreign_id}", "heading": "Theirs", "body": "", "link": ""},
    ]}]
    rejected = w.client.put(w.base + "/draft", json={"document": document,
                            "version": loaded["version"]}, headers=w.headers)
    assert rejected.status_code == 422
    assert "belonging to this site" in rejected.json()["error"]
    document["blocks"][0]["items"] = [
        {"icon": own_icon["url"], "heading": "Ours", "body": "", "link": ""},
        {"icon": "lucide:truck", "heading": "Library", "body": "", "link": ""},
        {"icon": "truck", "heading": "Original", "body": "", "link": ""},
    ]
    saved = w.client.put(w.base + "/draft", json={"document": document,
                         "version": loaded["version"]}, headers=w.headers)
    assert saved.status_code == 200, saved.text
    rendered = w.client.post(w.base + "/render", json={"document": document}, headers=w.headers)
    assert rendered.status_code == 200, rendered.text
    assert rendered.json()["html"].count('class="fs-pack-icon"') == 3


def test_header_and_footer_settings_save_as_draft_and_publish_on_their_own(workspace):
    from app.models import Membership, Site

    w = workspace
    loaded = load(w)
    fields = {field["id"]: field for field in loaded["shell_schema"]["fields"]}
    assert {"announcement", "logo", "tagline", "company", "address", "email", "offer",
            "footer"} <= set(fields)
    assert {field["group"] for field in fields.values()} == {"header", "footer"}
    assert loaded["shell_settings"]["offer"] == "Stay curious."
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        published_before = dict(site.published_settings_json)
        company_absent = "company" not in site.settings_json
    body = {"shell_settings": loaded["shell_settings"] | {"announcement": "Free delivery today"},
            "version": loaded["site_version"]}
    assert w.client.put(w.base + "/shell-settings", json=body).status_code == 403
    saved = w.client.put(w.base + "/shell-settings", json=body, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["shell_settings"]["announcement"] == "Free delivery today"
    assert saved.json()["published"] is False
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.settings_json["announcement"] == "Free delivery today"
        assert site.published_settings_json == published_before
        # Unchanged defaults are never pinned, so the storefront fallback still applies.
        assert ("company" not in site.settings_json) == company_absent
    stale = w.client.put(w.base + "/shell-settings", json=body, headers=w.headers)
    assert stale.status_code == 409 and stale.json()["conflict"] is True
    for unsupported in ({"operational_draft": "x"}, {"announcement": 7}, {"logo": "javascript:alert(1)"}):
        rejected = w.client.put(w.base + "/shell-settings", json={
            "shell_settings": unsupported, "version": saved.json()["version"]}, headers=w.headers)
        assert rejected.status_code == 422, unsupported
    # A page publish leaves draft header and footer settings unpublished.
    published = w.client.post(w.base + "/publish", json={"version": loaded["version"],
        "site_version": saved.json()["version"]}, headers=w.headers)
    assert published.status_code == 200, published.text
    with w.sessions() as db:
        assert db.get(Site, w.site.id).published_settings_json.get("announcement") == \
            published_before.get("announcement")
        site = db.get(Site, w.site.id)
        site.settings_json = site.settings_json | {"operational_draft": "Keep private"}
        version = site.version
        db.commit()
    live = w.client.put(w.base + "/shell-settings", json={
        "shell_settings": {"announcement": "Now live"}, "version": version, "publish": True},
        headers=w.headers)
    assert live.status_code == 200, live.text
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.published_settings_json["announcement"] == "Now live"
        assert "operational_draft" not in site.published_settings_json
        membership = db.scalar(select(Membership).where(Membership.user_id == w.owner.id,
            Membership.tenant_id == site.tenant_id))
        membership.role = "editor"
        version = site.version
        db.commit()
    denied = w.client.put(w.base + "/shell-settings", json={
        "shell_settings": {"announcement": "Editor"}, "version": version, "publish": True},
        headers=w.headers)
    assert denied.status_code == 403

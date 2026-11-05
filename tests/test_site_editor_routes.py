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
from app.models import AgentConversation, AgentMessage, Base, Membership, SitePage, User
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


def test_load_requires_active_actor_membership_and_scoped_page(workspace):
    w = workspace
    loaded = load(w)
    assert loaded["schema"] and loaded["can_publish"]
    assert loaded["page"]["id"] == w.page.id
    assert w.client.get(w.base + "/document", params={"page": w.other_page.id}).status_code == 404
    assert w.client.get(f"/admin/sites/{w.other.id}/editor/api").status_code == 403
    w.state.user = None
    assert w.client.get(w.base).status_code == 401


def test_agent_stream_requires_csrf_and_scopes_membership(workspace, monkeypatch):
    from app import site_editor_routes

    w = workspace
    monkeypatch.setattr(site_editor_routes, "settings", SimpleNamespace(xai_api_key="", environment="test"))
    path = f"/admin/sites/{w.site.id}/editor/agent"
    body = {"message": "Update the hero", "page_id": w.page.id}
    assert w.client.post(path, json=body).status_code == 403
    denied = w.client.post(f"/admin/sites/{w.other.id}/editor/agent", json=body,
                           headers=w.headers)
    assert denied.status_code == 403
    result = w.client.post(path, json=body, headers=w.headers)
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
    assert [event["type"] for event in events] == ["assistant_text", "done"]


def test_agent_history_is_tenant_scoped(workspace):
    w = workspace
    path = f"/admin/sites/{w.site.id}/editor/agent/history"
    result = w.client.get(path)
    assert result.status_code == 200
    assert result.json() == {"conversation_id": None, "messages": []}
    assert w.client.get(f"/admin/sites/{w.other.id}/editor/agent/history").status_code == 403


def test_agent_history_rebuilds_steps_from_saved_tool_calls(workspace):
    w = workspace
    with w.sessions() as db:
        conversation = AgentConversation(tenant_id=w.site.tenant_id, site_id=w.site.id,
                                        user_id=w.owner.id, title="Edit the page")
        db.add(conversation)
        db.flush()
        db.add_all([
            AgentMessage(tenant_id=w.site.tenant_id, site_id=w.site.id,
                         conversation_id=conversation.id, role="assistant", content="Updated",
                         tool_calls_json=[{"name": "update_block", "label": "Updated hero"},
                                          {"name": "update_theme", "label": "Updated colours"}]),
            AgentMessage(tenant_id=w.site.tenant_id, site_id=w.site.id,
                         conversation_id=conversation.id, role="tool", content='{"ok": true}',
                         tool_calls_json=[{"function": {"name": "update_block"}}]),
            AgentMessage(tenant_id=w.site.tenant_id, site_id=w.site.id,
                         conversation_id=conversation.id, role="tool", content='{"error": "Rejected"}',
                         tool_calls_json=[{"function": {"name": "update_theme"}}]),
        ])
        db.commit()
    result = w.client.get(f"/admin/sites/{w.site.id}/editor/agent/history")
    assistant = next(message for message in result.json()["messages"]
                     if message["role"] == "assistant")
    events = assistant["events"]
    assert [(event["label"], event["status"]) for event in events] == [
        ("Updated hero", "done"), ("Updated colours", "error")]


def test_agent_stream_forwards_run_turn_event_order(workspace, monkeypatch):
    from app import site_editor_routes, store_agent

    w = workspace
    monkeypatch.setattr(site_editor_routes, "settings", SimpleNamespace(xai_api_key="configured", environment="test"))
    observed = {}
    def run_turn(db, site, user, message, *, page_id, selection=None,
                 conversation_id=None, transport=None):
        observed.update(conversation_id=conversation_id)
        return iter([
        {"type": "conversation", "conversation_id": "conversation-1"},
        {"type": "tool_called", "name": "update_block", "label": "Updating block..."},
        {"type": "tool_result", "name": "update_block", "summary": "Updated"},
        {"type": "assistant_text", "text": "Done"},
        {"type": "done"},
    ])
    monkeypatch.setattr(store_agent, "run_turn", run_turn)
    path = f"/admin/sites/{w.site.id}/editor/agent"
    result = w.client.post(path, json={"message": "Update it", "page_id": w.page.id,
                                       "conversation_id": "conversation-1"},
                           headers=w.headers)
    assert result.status_code == 200
    events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
    assert [event["type"] for event in events] == ["conversation", "tool_called", "tool_result", "assistant_text", "done"]
    assert observed["conversation_id"] == "conversation-1"


def test_agent_restore_checks_csrf_and_uses_owned_site(workspace, monkeypatch):
    from app import store_agent

    w = workspace
    observed = {}
    def restore(db, site, user, message_id):
        observed.update(site=site.id, user=user, message=message_id)
    monkeypatch.setattr(store_agent, "restore_checkpoint", restore)
    path = f"/admin/sites/{w.site.id}/editor/agent/restore"
    assert w.client.post(path, json={"message_id":"msg-1"}).status_code == 403
    result = w.client.post(path, json={"message_id":"msg-1"}, headers=w.headers)
    assert result.status_code == 200
    assert observed == {"site": w.site.id, "user": w.owner.id, "message": "msg-1"}


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


def test_publish_copy_warning_and_editor_role(workspace):
    w = workspace
    values = load(w)
    document = values["document"]
    document["blocks"] = [{"id": "bad-copy", "type": "text", "heading": "A miracle cure", "body": "Content"}]
    published = w.client.post(w.base + "/publish", json={"document": document,
        "version": values["version"]}, headers=w.headers)
    assert published.status_code == 200, published.text
    assert "miracle" in published.json()["warnings"][0]
    with w.sessions() as db:
        member = db.scalar(select(Membership).where(Membership.user_id == w.owner.id,
                                                     Membership.tenant_id == w.site.tenant_id))
        member.role = "editor"
        db.commit()
    assert load(w)["can_publish"] is False
    assert w.client.post(w.base + "/publish", json={"version": values["version"]},
                         headers=w.headers).status_code == 403
    assert w.client.put(w.base + "/draft", json={"document": document,
        "version": published.json()["version"]}, headers=w.headers).status_code == 200


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

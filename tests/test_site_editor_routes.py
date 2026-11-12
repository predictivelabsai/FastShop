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
from app.models import (
    AgentConversation,
    AgentMessage,
    Base,
    Membership,
    Site,
    SiteMedia,
    SitePage,
    User,
)
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


def test_editor_seo_action_is_an_accessible_toolbar_icon(workspace):
    w = workspace

    result = w.client.get(f"/admin/sites/{w.site.id}/editor", params={"page": w.page.id})

    assert result.status_code == 200
    assert f'href="/admin/sites/{w.site.id}/content/pages/{w.page.id}/seo"' in result.text
    assert 'id="seo-link"' in result.text
    assert 'aria-label="Search engine optimisation"' in result.text
    assert 'stroke-width="1.5"' in result.text


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


def test_rich_block_save_round_trips_clean_html_and_renders_structure(workspace):
    w = workspace
    loaded = load(w)
    text_schema = loaded["schema"]["text"]
    body_field = next(field for field in text_schema["fields"] if field["id"] == "body")
    assert body_field["type"] == "richtext"
    dirty = '<h2>Returns</h2><p style="color:red" onerror="x()">Read <strong>this</strong>.' \
            '</p><ul><li>Keep your receipt</li></ul><script>alert(1)</script>'
    loaded["document"]["blocks"] = [{
        "id": "policy", "type": "text", "heading": "Returns and refunds", "body": dirty,
    }]
    saved = w.client.put(w.base + "/draft", json={
        "document": loaded["document"], "version": loaded["version"],
    }, headers=w.headers)
    assert saved.status_code == 200, saved.text
    document = load(w)["document"]
    assert document["blocks"][0]["body"] == (
        '<h2>Returns</h2><p>Read <strong>this</strong>.</p><ul><li>Keep your receipt</li></ul>'
    )
    rendered = w.client.post(w.base + "/render", json={"document": document}, headers=w.headers)
    assert rendered.status_code == 200, rendered.text
    assert '<h2>Returns</h2>' in rendered.json()["html"]
    assert '<ul><li>Keep your receipt</li></ul>' in rendered.json()["html"]


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


def test_styled_words_save_render_in_draft_preview_and_publish(workspace):
    from fasthtml.common import to_xml

    from app.site_ui import storefront

    w = workspace
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        settings = {"theme_pack": "liquid", "default_locale": "en", "styled_words": []}
        site.settings_json = settings
        site.published_settings_json = dict(settings)
        db.commit()

    loaded = load(w)
    theme = w.client.get(w.base + "/theme-settings").json()
    words = [
        {"word": "One", "italic": True, "bold": False, "colour": "brand-primary"},
        {"word": "Fizz", "italic": True, "bold": True, "colour": "#1e6fe0"},
    ]
    saved_theme = w.client.put(
        w.base + "/theme-settings",
        json={"theme_settings": theme["theme_settings"], "styled_words": words,
              "version": theme["version"]},
        headers=w.headers,
    )
    assert saved_theme.status_code == 200, saved_theme.text
    assert saved_theme.json()["styled_words"] == words

    document = {"version": 1, "title": "One", "blocks": [
        {"id": "copy", "type": "text", "heading": "One Fizz",
         "body": "<p>One Fizz and one.</p>"},
    ]}
    preview = w.client.post(w.base + "/render", json={"document": document}, headers=w.headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["html"].count('class="fs-styled-word"') >= 4
    assert "and one." in preview.json()["html"]

    published = w.client.post(
        w.base + "/publish",
        json={"document": document, "version": loaded["version"],
              "site_version": saved_theme.json()["version"]},
        headers=w.headers,
    )
    assert published.status_code == 200, published.text
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        page = db.get(SitePage, w.page.id)
        live = "".join(str(to_xml(node)) for node in storefront(
            db, site, page, "", "editor-csrf", "https://editor.example.test/"
        ) if node is not None)
        assert site.settings_json["styled_words"] == words
        assert site.published_settings_json["styled_words"] == words
    assert live.count('class="fs-styled-word"') >= 4


def test_typography_theme_settings_are_validated_draft_only_then_publish(workspace):
    from fasthtml.common import to_xml

    from app.site_ui import storefront

    w = workspace
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        liquid_settings = {"theme_pack": "liquid", "default_locale": "en"}
        site.settings_json = liquid_settings
        site.published_settings_json = liquid_settings
        db.commit()
    loaded = load(w)
    before = w.client.get(w.base + "/theme-settings").json()
    assert before["typography_schema"]["roles"][-2]["id"] == "footer_heading"
    chrome_schema = before["header_footer_schema"]
    assert [section["id"] for section in chrome_schema["sections"]] == [
        "header", "announcement", "footer", "newsletter", "bottom",
    ]
    header_fields = {field["id"]: field for field in chrome_schema["sections"][0]["fields"]}
    assert header_fields["background"]["type"] == "colour"
    assert header_fields["sticky"]["type"] == "toggle"
    newsletter_fields = {
        field["id"]: field for field in chrome_schema["sections"][3]["fields"]
    }
    assert newsletter_fields["button_background"]["type"] == "colour"
    theme = before["theme_settings"]
    typography = theme["typography"]
    footer_heading = typography["roles"]["footer_heading"] | {
        "size": "28px", "mobile_size": "24px", "weight": "500",
        "line_height": "1.2", "letter_spacing": "0.01em",
    }
    theme["typography"] = typography | {
        "display_font": "fraunces",
        "roles": typography["roles"] | {"footer_heading": footer_heading},
    }
    chrome = theme["header_footer"]
    header = chrome["header"] | {
        "background": "#123456", "link_hover": "#dbeafe", "padding_y": "24px",
        "logo_size": "168px", "sticky": False,
    }
    footer = chrome["footer"] | {
        "background": "#102030", "padding_y": "72px", "logo_size": "9rem",
        "column_gap": "48px",
    }
    newsletter = chrome["newsletter"] | {
        "input_background": "#1c2d3e", "button_background": "#dbeafe",
    }
    bottom = chrome["bottom"] | {"legal_link": "#e2e8f0", "disclaimer": "#cbd5e1"}
    theme["header_footer"] = chrome | {
        "header": header,
        "announcement": chrome["announcement"] | {"background": "rgb(25, 45, 65)"},
        "footer": footer,
        "newsletter": newsletter,
        "bottom": bottom,
    }
    saved = w.client.put(w.base + "/theme-settings", json={
        "theme_settings": theme, "version": before["version"],
    }, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["theme_settings"]["typography"]["roles"]["footer_heading"] == footer_heading
    assert saved.json()["theme_settings"]["header_footer"]["header"] == header
    assert saved.json()["theme_settings"]["header_footer"]["announcement"]["background"] == (
        "rgb(25, 45, 65)"
    )
    assert saved.json()["theme_settings"]["header_footer"]["footer"] == footer
    assert saved.json()["theme_settings"]["header_footer"]["newsletter"] == newsletter
    assert saved.json()["theme_settings"]["header_footer"]["bottom"] == bottom

    invalid = saved.json()["theme_settings"]
    invalid["typography"]["roles"]["footer_heading"]["size"] = "url(https://bad.test)"
    rejected = w.client.put(w.base + "/theme-settings", json={
        "theme_settings": invalid, "version": saved.json()["version"],
    }, headers=w.headers)
    assert rejected.status_code == 422
    invalid = saved.json()["theme_settings"]
    invalid["header_footer"]["header"]["background"] = "#ffffff;--unsafe:1"
    rejected = w.client.put(w.base + "/theme-settings", json={
        "theme_settings": invalid, "version": saved.json()["version"],
    }, headers=w.headers)
    assert rejected.status_code == 422

    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.settings_json["design"] == saved.json()["theme_settings"]
        assert "design" not in site.published_settings_json

    published = w.client.post(w.base + "/publish", json={
        "version": loaded["version"], "site_version": saved.json()["version"],
    }, headers=w.headers)
    assert published.status_code == 200, published.text
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.published_settings_json["design"] == saved.json()["theme_settings"]
        page = db.get(SitePage, w.page.id)
        markup = to_xml(storefront(db, site, page, "/sites/" + site.slug, "csrf", ""))
    assert '--liquid-font-display:"Liquid Fraunces",Georgia,serif' in markup
    assert "--liquid-type-footer-heading-size:28px" in markup
    assert "--liquid-type-footer-heading-size-mobile:24px" in markup
    assert "--liquid-header-background:#123456" in markup
    assert "--liquid-header-padding-y:24px" in markup
    assert "--liquid-header-logo-size:168px" in markup
    assert "--liquid-header-sticky:0" in markup
    assert "--liquid-announcement-background:rgb(25, 45, 65)" in markup
    assert "--liquid-footer-background:#102030" in markup
    assert "--liquid-footer-padding-y:72px" in markup
    assert "--liquid-footer-logo-size:9rem" in markup
    assert "--liquid-footer-column-gap:48px" in markup
    assert "--liquid-footer-newsletter-input-background:#1c2d3e" in markup
    assert "--liquid-footer-newsletter-button-background:#dbeafe" in markup
    assert "--liquid-footer-bottom-legal-link:#e2e8f0" in markup
    assert "--liquid-footer-bottom-disclaimer:#cbd5e1" in markup


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


def test_liquid_product_editor_save_renders_edited_share_body_and_research_link(workspace):
    from fasthtml.common import to_xml

    from app.site_ui import storefront

    w = workspace
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        liquid_settings = {"theme_pack": "liquid", "default_locale": "en"}
        site.settings_json = liquid_settings
        site.published_settings_json = liquid_settings
        site.hostname = "h24you.fastsme.com"
        db.commit()

    loaded = load(w)
    share_body = (
        "Keep the One for yourself, and share tablets with family and friends. "
        "One box is 30 glasses: a month for you, or a couple of weeks shared at home."
    )
    document = loaded["document"] | {
        "title": "Hydrogen Tablets",
        "blocks": [
            {
                "id": "benefits",
                "type": "liquid_product_benefits",
                "layout": "dark",
                "heading": "The research",
                "body": "",
                "image": "/static/research.jpg",
                "alt": "Research water glass",
                "items": [
                    {
                        "theme": "research",
                        "heading": "The research behind it",
                        "body": "Published studies",
                        "value": "2,000+",
                        "label": "articles published",
                        "alt": "Source note",
                        "url": "/science",
                    },
                    {
                        "theme": "research-cta",
                        "label": "Explore the science →",
                        "url": "https://h24you.fastsme.com/why-hydrogen/science",
                    },
                ],
            },
            {
                "id": "share",
                "type": "liquid_product_lifestyle",
                "layout": "light",
                "heading": "Share the routine",
                "body": share_body,
                "image": "/static/share.jpg",
                "alt": "Friends sharing water",
                "button": "Meet the One →",
                "link": "/products/h2-4-you-one",
            },
        ],
    }
    lifestyle_schema = loaded["schema"]["liquid_product_lifestyle"]
    body_field = next(field for field in lifestyle_schema["fields"] if field["id"] == "body")
    assert body_field["type"] == "richtext"

    saved = w.client.put(
        w.base + "/draft",
        params={"page": w.page.id},
        json={"document": document, "version": loaded["version"]},
        headers=w.headers,
    )
    assert saved.status_code == 200, saved.text
    saved_document = saved.json()["document"]
    saved_share = next(block for block in saved_document["blocks"] if block["id"] == "share")
    assert saved_share["body"] == share_body

    share_preview = w.client.post(
        w.base + "/render",
        params={"page": w.page.id, "block_id": "share"},
        json={"document": saved_document},
        headers=w.headers,
    )
    assert share_preview.status_code == 200, share_preview.text
    assert share_body in share_preview.json()["html"]
    assert 'data-field="body"' in share_preview.json()["html"]

    cta_preview = w.client.post(
        w.base + "/render",
        params={"page": w.page.id, "block_id": "benefits"},
        json={"document": saved_document},
        headers=w.headers,
    )
    assert cta_preview.status_code == 200, cta_preview.text
    assert 'href="/sites/editor-store/why-hydrogen/science"' in cta_preview.json()["html"]
    assert 'data-link-field="items.1.url"' in cta_preview.json()["html"]

    published = w.client.post(
        w.base + "/publish",
        params={"page": w.page.id},
        json={
            "document": saved_document,
            "version": saved.json()["version"],
            "site_version": loaded["site_version"],
        },
        headers=w.headers,
    )
    assert published.status_code == 200, published.text

    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        page = db.get(SitePage, w.page.id)
        live_nodes = storefront(
            db,
            site,
            page,
            "",
            "editor-csrf",
            "https://h24you.fastsme.com/products/hydrogen-tablets",
        )
        live = "".join(str(to_xml(node)) for node in live_nodes if node is not None)
    assert share_body in live
    assert 'href="/why-hydrogen/science"' in live


def test_liquid_product_editable_text_and_links_round_trip_for_each_block(workspace, monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace

    from app import site_ui_liquid_products
    from app.site_liquid_products_content import product_documents

    w = workspace
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        site.settings_json = {"theme_pack": "liquid", "default_locale": "en"}
        site.published_settings_json = site.settings_json
        db.commit()
    monkeypatch.setattr(
        site_ui_liquid_products,
        "catalogue",
        lambda *_: [(SimpleNamespace(id=f"variant-{index}"), 2800) for index in range(12)],
    )

    loaded = load(w)
    expected = {}
    blocks = []
    for page_index, (_, _, _, source_blocks) in enumerate(product_documents()):
        for block_index, source in enumerate(source_blocks):
            if not source["type"].startswith("liquid_product_"):
                continue
            block = deepcopy(source)
            block["id"] = f"audit_{page_index}_{block_index}_{source['id']}"
            block_expected = []
            schema = loaded["schema"][block["type"]]
            for field in schema["fields"]:
                key = field["id"]
                if key in {"items", "layout", "hidden"} or key.startswith("style."):
                    continue
                if key == "url" and block["type"] == "liquid_product_purchase":
                    continue
                value = (
                    f"/static/{block['id']}_{key}.jpg"
                    if key == "image"
                    else f"/edited/{block['id']}/{key}"
                    if key == "url" or key == "link"
                    else f"edit_{block['id']}_{key}"
                )
                block[key] = value
                block_expected.append(value)

            repeater = next(
                (field for field in schema["fields"] if field["id"] == "items"), None
            )
            if repeater:
                layout_fields = repeater.get("layout_theme_fields", {}).get(
                    block.get("layout"), repeater["theme_fields"]
                )
                for item_index, item in enumerate(block.get("items", [])):
                    for field in layout_fields.get(item["theme"], []):
                        key = field["id"]
                        if key == "theme":
                            continue
                        theme = item["theme"]
                        if key == "value" and theme in {
                            "bundle", "flavor", "ingredient", "quality", "day-stat", "rating"
                        }:
                            continue
                        if key == "label" and theme == "rating":
                            continue
                        if key == "body" and theme == "bundle":
                            item[key] = "15"
                            block_expected.append("15")
                            continue
                        value = (
                            f"/static/{block['id']}_{item_index}_{key}.jpg"
                            if key == "image"
                            else f"/edited/{block['id']}/{item_index}/{key}"
                            if key == "url"
                            else "lucide:droplet"
                            if key == "icon"
                            else f"edit_{block['id']}_{item_index}_{key}"
                        )
                        item[key] = value
                        if key != "icon":
                            block_expected.append(value)
            expected[block["id"]] = block_expected
            blocks.append(block)

    faq_block = next(block for block in blocks if block["type"] == "liquid_product_faq")
    faq_link = {
        "theme": "faq-link",
        "label": "edit_faq_resource_label",
        "url": "/edited/faq-resource",
    }
    faq_block["items"].append(faq_link)
    expected[faq_block["id"]].extend([faq_link["label"], faq_link["url"]])

    document = loaded["document"] | {"title": "Product field audit", "blocks": blocks}
    saved = w.client.put(
        w.base + "/draft",
        params={"page": w.page.id},
        json={"document": document, "version": loaded["version"]},
        headers=w.headers,
    )
    assert saved.status_code == 200, saved.text

    for block_id, values in expected.items():
        rendered = w.client.post(
            w.base + "/render",
            params={"page": w.page.id, "block_id": block_id},
            json={"document": saved.json()["document"]},
            headers=w.headers,
        )
        assert rendered.status_code == 200, rendered.text
        markup = rendered.json()["html"]
        for value in values:
            assert value in markup, f"{block_id} did not render edited value {value!r}"
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


def test_icon_picker_upload_is_sanitised_and_scoped_to_the_site(workspace):
    w = workspace
    editor = w.client.get(f"/admin/sites/{w.site.id}/editor", params={"page": w.page.id})
    assert editor.status_code == 200
    for target in ("icon-dialog", "icon-search", "icon-upload", "icon-grid", "custom-icon-grid"):
        assert f'id="{target}"' in editor.text

    library = w.client.get(w.base + "/icons")
    assert library.status_code == 200, library.text
    icons = library.json()["icons"]
    assert len(icons) >= 300
    droplet = next(icon for icon in icons if icon["id"] == "lucide:droplet")
    assert "<svg" in droplet["svg"] and "currentColor" in droplet["svg"]

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

    with w.sessions() as db:
        stored = db.get(SiteMedia, own_icon["id"])
        assert stored.tenant_id == w.site.tenant_id and stored.site_id == w.site.id
        safe = stored.data.decode("utf-8")
        foreign = SiteMedia(
            tenant_id=w.other.tenant_id, site_id=w.other.id, title="Other icon", alt="Other icon",
            storage_key="other-icon.svg", content_type="image/svg+xml", size=6, data=b"<svg/>",
        )
        legacy = SiteMedia(
            tenant_id=w.site.tenant_id, site_id=w.site.id, title="External legacy icon",
            alt="External legacy icon", storage_key="https://bad.example/icon.svg",
            public_url="https://bad.example/icon.svg", content_type="image/svg+xml", size=0,
            data=None,
        )
        db.add_all([foreign, legacy])
        db.commit()
        foreign_id, legacy_id = foreign.id, legacy.id
    assert "script" not in safe.lower()
    assert "foreignobject" not in safe.lower()
    assert "onload" not in safe.lower()
    assert "https://bad.example" not in safe.lower()
    assert "currentColor" in safe

    current_library = w.client.get(w.base + "/icons").json()["custom"]
    assert own_icon["id"] in {media["id"] for media in current_library}
    assert foreign_id not in {media["id"] for media in current_library}
    assert legacy_id not in {media["id"] for media in current_library}
    assert w.client.get(f"/admin/sites/{w.other.id}/editor/api/icons").status_code == 403

    document = load(w)["document"]
    document["blocks"] = [{
        "id": "why", "type": "liquid_benefits",
        "items": [{
            "theme": "cell", "heading": "Cellular health", "body": "A benefit.",
            "icon": f"/site-media/{w.other.id}/{foreign_id}",
        }],
    }]
    rejected = w.client.put(
        w.base + "/draft", json={"document": document, "version": load(w)["version"]},
        headers=w.headers,
    )
    assert rejected.status_code == 422
    assert "belonging to this site" in rejected.json()["error"]

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


@pytest.mark.parametrize("theme", ["cell", "shield", "exercise", "leaf", "molecule", "water"])
def test_live_liquid_benefits_fields_round_trip_through_editor_save(workspace, theme):
    w = workspace
    loaded = load(w)
    benefits = {
        "id": "why",
        "type": "liquid_benefits",
        "version": 1,
        "heading": "Why drink hydrogen water?",
        "body": "The disclaimer.",
        "button": "More questions",
        "link": "/faq",
        "items": [
            {"heading": "Cellular health*", "body": "Supports cellular health.*", "theme": "cell"},
            {"heading": "Antioxidant support*", "body": "Helps neutralise free radicals.*", "theme": "shield"},
        ],
    }
    loaded["document"]["blocks"] = [benefits]
    loaded["document"]["blocks"][0]["items"][1]["icon"] = theme
    loaded["document"]["blocks"][0]["items"][0]["body"] = "Edited benefit text."
    result = w.client.put(
        w.base + "/draft",
        json={"document": loaded["document"], "version": loaded["version"]},
        headers=w.headers,
    )
    assert result.status_code == 200, result.text
    saved = result.json()["document"]["blocks"][0]["items"][1]
    assert saved["theme"] == "shield"
    assert saved["icon"] == theme
    assert result.json()["document"]["blocks"][0]["items"][0]["body"] == "Edited benefit text."


def test_liquid_benefit_icon_override_renders_and_blank_icon_keeps_legacy_art(workspace):
    w = workspace
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        liquid_settings = {"theme_pack": "liquid", "default_locale": "en"}
        site.settings_json = liquid_settings
        site.published_settings_json = liquid_settings
        db.commit()
    loaded = load(w)
    document = loaded["document"] | {"blocks": [{
        "id": "why", "type": "liquid_benefits", "heading": "Why drink hydrogen water?",
        "items": [{
            "theme": "cell", "icon": "lucide:droplet", "heading": "Cellular health",
            "body": "A benefit.",
        }],
    }]}
    saved = w.client.put(
        w.base + "/draft", json={"document": document, "version": loaded["version"]},
        headers=w.headers,
    )
    assert saved.status_code == 200, saved.text
    item = saved.json()["document"]["blocks"][0]["items"][0]
    assert item["theme"] == "cell"
    assert item["icon"] == "lucide:droplet"

    selected = w.client.post(
        w.base + "/render", params={"page": w.page.id},
        json={"document": saved.json()["document"], "block_id": "why"}, headers=w.headers,
    )
    assert selected.status_code == 200, selected.text
    assert 'd="M12 22a7 7 0 0 0 7-7' in selected.json()["html"]
    assert 'stroke="currentColor"' in selected.json()["html"]

    legacy_document = json.loads(json.dumps(saved.json()["document"]))
    legacy_document["blocks"][0]["items"][0].pop("icon")
    legacy = w.client.post(
        w.base + "/render", params={"page": w.page.id},
        json={"document": legacy_document, "block_id": "why"}, headers=w.headers,
    )
    assert legacy.status_code == 200, legacy.text
    assert 'd="M37 14c8 6' in legacy.json()["html"]
    assert 'd="M12 22a7 7 0 0 0 7-7' not in legacy.json()["html"]

    legacy_choice = json.loads(json.dumps(saved.json()["document"]))
    legacy_choice["blocks"][0]["items"][0].update(theme="shield", icon="cell")
    rendered_choice = w.client.post(
        w.base + "/render", params={"page": w.page.id},
        json={"document": legacy_choice, "block_id": "why"}, headers=w.headers,
    )
    assert rendered_choice.status_code == 200, rendered_choice.text
    assert 'd="M37 14c8 6' in rendered_choice.json()["html"]


def test_unsupported_item_field_error_names_field_and_index(workspace):
    w = workspace
    loaded = load(w)
    loaded["document"]["blocks"] = [{
        "id": "why",
        "type": "liquid_benefits",
        "items": [
            {"heading": "One", "body": "Text", "theme": "cell"},
            {"heading": "Two", "body": "Text", "theme": "shield", "mystery": "value"},
        ],
    }]
    result = w.client.put(
        w.base + "/draft",
        json={"document": loaded["document"], "version": loaded["version"]},
        headers=w.headers,
    )
    assert result.status_code == 422
    assert 'item 2: "mystery" is not a supported field' in result.json()["error"]


@pytest.mark.parametrize("locale", ["en", "en-US"])
def test_locale_keys_on_items_remain_unsupported(workspace, locale):
    w = workspace
    loaded = load(w)
    loaded["document"]["blocks"] = [{
        "id": "why",
        "type": "liquid_benefits",
        "items": [{
            "heading": "Benefit",
            "body": "Details",
            "theme": "cell",
            locale: {"heading": "Benefit", "body": "Details", "theme": "cell"},
        }],
    }]
    result = w.client.put(
        w.base + "/draft",
        json={"document": loaded["document"], "version": loaded["version"]},
        headers=w.headers,
    )
    assert result.status_code == 422
    assert f'item 1: "{locale}" is not a supported field' in result.json()["error"]


def test_widget_item_field_error_names_field_and_index(workspace):
    w = workspace
    loaded = load(w)
    loaded["document"]["blocks"] = [{
        "id": "features",
        "type": "icon_list",
        "items": [{"heading": "Feature", "body": "Detail", "theme": "check", "unknown": "value"}],
    }]
    result = w.client.put(
        w.base + "/draft",
        json={"document": loaded["document"], "version": loaded["version"]},
        headers=w.headers,
    )
    assert result.status_code == 422
    assert 'item 1: "unknown" is not a supported field' in result.json()["error"]


@pytest.mark.parametrize("kind", [
    "liquid_stats", "liquid_collection", "liquid_benefits", "liquid_reviews",
    "liquid_articles", "liquid_catalog", "liquid_choices", "liquid_trust",
])
def test_liquid_editor_item_fields_round_trip_through_save(workspace, kind):
    w = workspace
    loaded = load(w)
    schema = loaded["schema"][kind]
    items_field = next(field for field in schema["fields"] if field["id"] == "items")
    item = dict(items_field["item_default"])
    for field in items_field["item_fields"]:
        key = field["id"]
        if key == "theme":
            continue
        if key == "url":
            item[key] = "/edited"
        elif key == "image":
            item[key] = "/static/edited.jpg"
        elif key == "icon":
            item[key] = "lucide:droplet"
        elif key == "value" and kind == "liquid_reviews":
            item[key] = "4"
        else:
            item[key] = f"Edited {key}"
    block = {"id": f"liquid-{kind}", "type": kind, "items": [item]}
    loaded["document"]["blocks"] = [block]
    result = w.client.put(
        w.base + "/draft",
        json={"document": loaded["document"], "version": loaded["version"]},
        headers=w.headers,
    )
    assert result.status_code == 200, result.text
    saved_item = result.json()["document"]["blocks"][0]["items"][0]
    for field in items_field["item_fields"]:
        assert saved_item[field["id"]] == item[field["id"]]

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

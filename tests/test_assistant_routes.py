"""Store assistant routes and bring-your-own-key model connections."""

import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fasthtml.common import Div, Input, fast_app, to_xml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import (
    assistant_provider,
    assistant_routes,
    config,
    content,
    live_credentials,
    site_integrations,
    store_agent,
)
from app import (
    db as app_db,
)
from app.models import (
    AgentAction,
    AgentConversation,
    AgentDocumentAttachment,
    AgentMessage,
    Base,
    Membership,
    SiteIntegrationCredential,
    SiteMedia,
    User,
)
from app.services import CommerceError

SECRET = "sk-test-0123456789abcdefWXYZ"


def events_of(result):
    return [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="assistant-owner@example.test", name="Owner")
        editor = User(email="assistant-editor@example.test", name="Editor")
        outsider = User(email="assistant-outsider@example.test", name="Outsider")
        db.add_all([owner, editor, outsider])
        db.flush()
        site = content.create_site(db, owner.id, "Assistant store", "assistant-store")
        other = content.create_site(db, outsider.id, "Other store", "other-assistant-store")
        db.add(Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"))
        page = content.site_pages(db, site)[0]
        db.commit()
    monkeypatch.setattr(assistant_routes, "SessionLocal", sessions)
    monkeypatch.setattr(app_db, "SessionLocal", sessions)
    monkeypatch.setattr(site_integrations, "SessionLocal", sessions)
    monkeypatch.setattr(live_credentials, "settings",
                        replace(live_credentials.settings, store_key_encryption_key="k" * 40))
    # No platform key unless a test sets one: the store's own key is the norm.
    monkeypatch.setattr(config, "settings", replace(config.settings, xai_api_key=""))
    app, rt = fast_app(secret_key="assistant-test-only")
    state = SimpleNamespace(user=owner.id)

    def actor(session):
        if not state.user:
            raise CommerceError("Sign in to manage your sites.")
        return state.user

    def csrf(session):
        session["csrf_token"] = "assistant-csrf"
        return Input(type="hidden", name="csrf_token", value="assistant-csrf")

    def check_csrf(session, form):
        if form.get("csrf_token") != "assistant-csrf":
            raise CommerceError("Your session expired.")

    def shell(title, *children, subnavigation=None):
        return str(to_xml(Div(*children)))

    def error(exc):
        return Response(str(exc), status_code=400)

    assistant_routes.register_assistant_routes(rt, actor, csrf, check_csrf, shell, error)
    from app.store_agent_ops import register_routes
    register_routes(rt, actor, csrf, check_csrf, shell, error)
    with TestClient(app) as client:
        client.get(f"/admin/sites/{site.id}/settings/assistant")  # Starts a session with a CSRF token.
        yield SimpleNamespace(client=client, sessions=sessions, site=site, other=other, page=page,
                              owner=owner, editor=editor, outsider=outsider, state=state,
                              settings=f"/admin/sites/{site.id}/settings/assistant",
                              base=f"/admin/sites/{site.id}/editor/api",
                              headers={"X-CSRF-Token": "assistant-csrf"})
    engine.dispose()


def save(w, **values):
    data = {"csrf_token": "assistant-csrf", "provider": "openai", "api_key": SECRET, "model": ""} | values
    return w.client.post(w.settings, data=data, follow_redirects=False)


def stored_row(w):
    with w.sessions() as db:
        return db.scalar(select(SiteIntegrationCredential).where(
            SiteIntegrationCredential.site_id == w.site.id,
            SiteIntegrationCredential.provider == assistant_provider.CREDENTIAL))


# Key resolution

def test_resolver_prefers_the_store_key_then_the_platform_key_then_nothing(workspace, monkeypatch):
    w = workspace
    with w.sessions() as db:
        assert assistant_provider.resolve(db, w.site) is None
        assert assistant_provider.status(db, w.site)["state"] == "none"
        # A platform key alone never powers a store's assistant: the operator must opt in.
        monkeypatch.setattr(config, "settings", replace(config.settings, xai_api_key="platform-key"))
        assert assistant_provider.resolve(db, w.site) is None
        monkeypatch.setattr(config, "settings", replace(config.settings, xai_api_key="platform-key",
                                                        assistant_platform_fallback=True,
                                                        xai_base_url="https://api.x.ai/v1",
                                                        model_name="grok-4-1-fast-reasoning"))
        platform = assistant_provider.resolve(db, w.site)
        assert (platform.source, platform.api_key, platform.base_url) == (
            "platform", "platform-key", "https://api.x.ai/v1")
        assert assistant_provider.status(db, w.site)["state"] == "platform"
        payload = assistant_provider.clean_connection("openrouter", SECRET, "")
        site_integrations.save(db, w.site, assistant_provider.CREDENTIAL, payload, "default",
                               assistant_provider.hints_for(payload))
        db.commit()
        store = assistant_provider.resolve(db, w.site)
        assert store.source == "store" and store.api_key == SECRET
        assert store.base_url == "https://openrouter.ai/api/v1"
        assert store.model == "openai/gpt-4.1-mini"
        assert SECRET not in repr(store)
        # Another store never sees this key; it falls back to the platform key.
        assert assistant_provider.resolve(db, w.other).source == "platform"


def test_an_unreadable_saved_key_falls_back_and_asks_for_attention(workspace, monkeypatch):
    w = workspace
    with w.sessions() as db:
        payload = assistant_provider.clean_connection("xai", SECRET, "")
        site_integrations.save(db, w.site, assistant_provider.CREDENTIAL, payload, "default",
                               assistant_provider.hints_for(payload))
        db.commit()
        monkeypatch.setattr(live_credentials, "settings",
                            replace(live_credentials.settings, store_key_encryption_key="z" * 40))
        assert assistant_provider.resolve(db, w.site) is None
        assert assistant_provider.status(db, w.site)["state"] == "unreadable"


@pytest.mark.parametrize("url", [
    "http://models.example.com/v1",
    "https://localhost/v1",
    "https://127.0.0.1/v1",
    "https://10.1.2.3/v1",
    "https://192.168.0.10/v1",
    "https://169.254.169.254/latest",
    "https://[::1]/v1",
    "https://[fe80::1]/v1",
    "https://intranet/v1",
    "https://models.internal/v1",
    "https://user:pass@models.example.com/v1",
    "https://models.example.com/v1?key=1",
    "ftp://models.example.com",
    "",
])
def test_custom_endpoints_must_be_public_https(url):
    with pytest.raises(CommerceError):
        assistant_provider.validate_base_url(url, resolve=False)


def test_custom_endpoint_hostnames_must_resolve_to_public_addresses(monkeypatch):
    def resolver(address):
        return lambda *args, **kwargs: [(None, None, None, None, (address, 443))]

    monkeypatch.setattr(assistant_provider.socket, "getaddrinfo", resolver("10.0.0.7"))
    with pytest.raises(CommerceError, match="public address"):
        assistant_provider.validate_base_url("https://models.example.com/v1")
    monkeypatch.setattr(assistant_provider.socket, "getaddrinfo", resolver("::ffff:127.0.0.1"))
    with pytest.raises(CommerceError, match="public address"):
        assistant_provider.validate_base_url("https://models.example.com/v1")
    monkeypatch.setattr(assistant_provider.socket, "getaddrinfo", resolver("93.184.216.34"))
    assert (assistant_provider.validate_base_url("https://Models.Example.com:8443/v1/")
            == "https://models.example.com:8443/v1")


def test_check_connection_reports_safe_messages_without_provider_detail(monkeypatch):
    config_ = assistant_provider.ModelConfig("openai", "https://api.openai.com/v1", SECRET,
                                             "gpt-4.1-mini", "store")
    sent = {}

    def respond(status, payload=None):
        def post(url, **kwargs):
            sent.update(url=url, **kwargs)
            return httpx.Response(status, json=payload or {"error": {"message": "key " + SECRET}})
        return post

    monkeypatch.setattr(assistant_provider.httpx, "post", respond(200, {"choices": []}))
    assert assistant_provider.check_connection(config_) == (
        True, "Connected to OpenAI using gpt-4.1-mini.")
    assert sent["url"] == "https://api.openai.com/v1/chat/completions"
    assert sent["timeout"] == assistant_provider.TEST_TIMEOUT
    assert sent["follow_redirects"] is False
    for status, phrase in ((401, "rejected this API key"), (404, "couldn't find that model"),
                           (429, "limiting requests"), (500, "HTTP 500")):
        monkeypatch.setattr(assistant_provider.httpx, "post", respond(status))
        ok, message = assistant_provider.check_connection(config_)
        assert not ok and phrase in message and SECRET not in message
    monkeypatch.setattr(assistant_provider.httpx, "post",
                        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("slow")))
    assert assistant_provider.check_connection(config_)[0] is False


# Settings page

def test_settings_page_saves_an_encrypted_key_and_never_echoes_it(workspace):
    w = workspace
    page = w.client.get(w.settings)
    assert page.status_code == 200
    assert "Connect your own model" in page.text and "Not connected" in page.text
    for provider in ("xAI", "OpenAI", "OpenRouter", "Custom OpenAI-compatible endpoint"):
        assert provider in page.text
    saved = save(w, model="gpt-4.1")
    assert saved.status_code == 303 and "Model+connection+saved" in saved.headers["location"]
    row = stored_row(w)
    assert row.status == "connected" and SECRET.encode() not in row.encrypted_payload
    assert row.verified_at is None  # Saved, not yet checked.
    assert row.masked_hints_json == {"provider": "OpenAI", "model": "gpt-4.1", "api_key": "••••WXYZ"}
    page = w.client.get(w.settings)
    assert "••••WXYZ" in page.text and "Connected" in page.text and "gpt-4.1" in page.text
    assert SECRET not in page.text and SECRET[:-4] not in page.text
    assert 'value="sk-' not in page.text


def test_settings_save_requires_csrf_and_a_store_manager(workspace):
    w = workspace
    assert save(w, csrf_token="wrong").headers["location"].endswith("expired.")
    assert stored_row(w) is None
    w.state.user = w.editor.id
    assert w.client.get(w.settings).status_code == 400
    assert "access+denied" in save(w).headers["location"]
    assert w.client.post(w.settings + "/disconnect", data={"csrf_token": "assistant-csrf"},
                         follow_redirects=False).status_code == 303
    assert stored_row(w) is None
    w.state.user = w.outsider.id
    assert w.client.get(w.settings).status_code == 400
    assert "access+denied" in save(w).headers["location"]
    assert stored_row(w) is None


def test_settings_rejects_private_custom_endpoints_and_keeps_keys_on_their_provider(workspace, monkeypatch):
    w = workspace
    refused = save(w, provider="custom", base_url="https://169.254.169.254/latest")
    assert "public+address" in refused.headers["location"] and stored_row(w) is None
    assert "https" in save(w, provider="custom", base_url="http://models.example.com/v1").headers["location"]
    monkeypatch.setattr(assistant_provider.socket, "getaddrinfo",
                        lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 443))])
    assert save(w, provider="custom", base_url="https://models.example.com/v1",
                model="my-model").status_code == 303
    assert stored_row(w).masked_hints_json["endpoint"] == "models.example.com"
    # A blank key keeps the saved key for the same endpoint, but never sends it elsewhere.
    assert "saved" in save(w, provider="custom", base_url="https://models.example.com/v1",
                           api_key="", model="other-model").headers["location"]
    moved = save(w, provider="custom", base_url="https://evil.example.net/v1", api_key="")
    assert "Enter+the+API+key+again" in moved.headers["location"]
    switched = save(w, provider="xai", api_key="")
    assert "Enter+the+API+key+again" in switched.headers["location"]
    with w.sessions() as db:
        kept = assistant_provider.resolve(db, w.site)
    assert (kept.base_url, kept.model, kept.api_key) == ("https://models.example.com/v1", "other-model", SECRET)


def test_settings_reports_missing_encryption_without_storing_anything(workspace, monkeypatch):
    w = workspace
    monkeypatch.setattr(live_credentials, "settings",
                        replace(live_credentials.settings, store_key_encryption_key=""))
    assert "Secure+storage" in save(w).headers["location"]
    assert stored_row(w) is None


def test_settings_test_connection_uses_only_the_saved_store_key(workspace, monkeypatch):
    w = workspace
    calls = []
    monkeypatch.setattr(assistant_provider, "check_connection",
                        lambda model_config: calls.append(model_config) or (True, "Connected."))
    early = w.client.post(w.settings + "/test", data={"csrf_token": "assistant-csrf"},
                          follow_redirects=False)
    assert "Save+a+model+connection" in early.headers["location"] and calls == []
    save(w)
    assert w.client.post(w.settings + "/test", data={"csrf_token": "wrong"},
                         follow_redirects=False).headers["location"].endswith("expired.")
    tested = w.client.post(w.settings + "/test", data={"csrf_token": "assistant-csrf"},
                           follow_redirects=False)
    assert tested.headers["location"].endswith("Connected.")
    assert calls[0].api_key == SECRET and calls[0].source == "store"
    assert stored_row(w).verified_at is not None
    removed = w.client.post(w.settings + "/disconnect", data={"csrf_token": "assistant-csrf"},
                            follow_redirects=False)
    assert "removed" in removed.headers["location"] and stored_row(w) is None


def test_settings_page_explains_the_platform_fallback(workspace, monkeypatch):
    w = workspace
    monkeypatch.setattr(config, "settings", replace(config.settings, xai_api_key="platform-key",
                                                    assistant_platform_fallback=True))
    page = w.client.get(w.settings)
    assert "Platform key" in page.text and "platform-key" not in page.text


# Studio and editor surfaces

def test_studio_shows_the_unconfigured_state_until_the_store_connects_a_key(workspace):
    w = workspace
    studio = f"/admin/sites/{w.site.id}/assistant"
    missing = w.client.get(studio)
    assert missing.status_code == 200
    assert "Connect an AI model to use the assistant" in missing.text
    assert f'href="/admin/sites/{w.site.id}/settings/assistant"' in missing.text
    assert "operator" not in missing.text and 'data-agent-form=""' not in missing.text
    assert f'data-stream="/admin/sites/{w.site.id}/editor/agent"' in missing.text
    save(w)
    ready = w.client.get(studio)
    assert 'data-agent-form=""' in ready.text and 'data-agent-configured="true"' in ready.text
    assert SECRET not in ready.text
    assert w.client.get(f"/admin/sites/{w.other.id}/assistant").status_code == 400
    w.state.user = w.editor.id
    assert w.client.get(studio).status_code == 400


def test_studio_task_queue_and_history_are_tenant_scoped(workspace):
    w = workspace
    tasks = w.client.get(f"/admin/sites/{w.site.id}/assistant/tasks")
    assert tasks.status_code == 200 and tasks.json() == {"tasks": []}
    assert w.client.get(f"/admin/sites/{w.other.id}/assistant/tasks").status_code == 400
    history = w.client.get(f"/admin/sites/{w.site.id}/assistant/history")
    assert history.json() == {"conversation_id": None, "messages": [], "actions": []}
    assert w.client.get(f"/admin/sites/{w.site.id}/assistant/conversations").json() == []
    assert w.client.get(f"/admin/sites/{w.other.id}/assistant/history").status_code == 400


def test_editor_features_follow_the_per_site_key(workspace):
    from app.store_agent_admin import editor_features

    w = workspace
    with w.sessions() as db:
        assert editor_features(w.site, db) == {
            "assistant": True, "assistant_configured": False,
            "assistant_settings_href": f"/admin/sites/{w.site.id}/settings/assistant"}
    save(w)
    with w.sessions() as db:
        assert editor_features(w.site, db)["assistant_configured"] is True
        assert editor_features(w.other, db)["assistant_configured"] is False
    # A library theme preview edits a snapshot the assistant never touches.
    assert editor_features(SimpleNamespace(id=w.site.id, theme_context_id="theme-1")) == {
        "assistant": False}


def test_editor_stream_requires_csrf_scopes_membership_and_reports_unconfigured(workspace, monkeypatch):
    w = workspace
    monkeypatch.setattr(store_agent, "run_turn", lambda *a, **k: pytest.fail("model called"))
    path = f"/admin/sites/{w.site.id}/editor/agent"
    body = {"message": "Update the hero", "page_id": w.page.id}
    assert w.client.post(path, json=body).status_code == 403
    assert w.client.post(f"/admin/sites/{w.other.id}/editor/agent", json=body,
                         headers=w.headers).status_code == 403
    result = w.client.post(path, json=body, headers=w.headers)
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/event-stream")
    events = events_of(result)
    assert [event["type"] for event in events] == ["assistant_text", "done"]
    assert "Settings, AI assistant" in events[0]["text"]
    assert events[0]["settings_href"] == f"/admin/sites/{w.site.id}/settings/assistant"


def test_editor_stream_forwards_run_turn_event_order_once_a_key_is_saved(workspace, monkeypatch):
    w = workspace
    save(w)
    observed = {}

    def run_turn(db, site, user, message, *, page_id, selection=None, conversation_id=None,
                 transport=None):
        observed.update(conversation_id=conversation_id, transport=transport, site=site.id)
        return iter([
            {"type": "conversation", "conversation_id": "conversation-1"},
            {"type": "tool_called", "name": "update_block", "label": "Updating block"},
            {"type": "tool_result", "name": "update_block", "summary": "Updated"},
            {"type": "assistant_text", "text": "Done"},
            {"type": "done"},
        ])

    monkeypatch.setattr(store_agent, "run_turn", run_turn)
    result = w.client.post(f"/admin/sites/{w.site.id}/editor/agent", headers=w.headers, json={
        "message": "Update it", "page_id": w.page.id, "conversation_id": "conversation-1"})
    assert [event["type"] for event in events_of(result)] == [
        "conversation", "tool_called", "tool_result", "assistant_text", "done"]
    assert observed == {"conversation_id": "conversation-1", "transport": None, "site": w.site.id}


def test_run_turn_uses_the_store_key_and_refuses_without_one(workspace, monkeypatch):
    from app.integrations import agent_llm

    w = workspace
    with w.sessions() as db:
        site, page = db.merge(w.site), db.merge(w.page)
        events = list(store_agent.run_turn(db, site, w.owner.id, "Hello", page_id=page.id))
        assert events[-1]["type"] == "error" and "Settings, AI assistant" in events[-1]["message"]
        assert db.query(AgentConversation).count() == 0
    save(w, provider="xai", model="grok-test")
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, auth=kwargs["headers"]["Authorization"], model=kwargs["json"]["model"])
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": "Hi there"}}]})

    monkeypatch.setattr(agent_llm.httpx, "post", fake_post)
    with w.sessions() as db:
        site, page = db.merge(w.site), db.merge(w.page)
        events = list(store_agent.run_turn(db, site, w.owner.id, "Hello", page_id=page.id))
    assert [event["type"] for event in events][-2:] == ["assistant_text", "done"]
    assert sent == {"url": "https://api.x.ai/v1/chat/completions", "auth": "Bearer " + SECRET,
                    "model": "grok-test"}


def test_editor_history_and_restore_are_scoped(workspace, monkeypatch):
    w = workspace
    path = f"/admin/sites/{w.site.id}/editor/agent/history"
    assert w.client.get(path).json() == {"conversation_id": None, "messages": []}
    assert w.client.get(f"/admin/sites/{w.other.id}/editor/agent/history").status_code == 403
    with w.sessions() as db:
        conversation = AgentConversation(tenant_id=w.site.tenant_id, site_id=w.site.id,
                                         user_id=w.owner.id, title="Review a price change")
        db.add(conversation)
        db.flush()
        message = AgentMessage(tenant_id=w.site.tenant_id, site_id=w.site.id,
                               conversation_id=conversation.id, role="assistant",
                               content="I can change that price.",
                               tool_calls_json=[{"name": "update_block", "label": "Updated hero"}])
        db.add(message)
        db.flush()
        db.add_all([
            AgentMessage(tenant_id=w.site.tenant_id, site_id=w.site.id,
                         conversation_id=conversation.id, role="tool", content='{"error": "No"}',
                         tool_calls_json=[{"function": {"name": "update_block"}}]),
            AgentAction(tenant_id=w.site.tenant_id, site_id=w.site.id,
                        conversation_id=conversation.id, message_id=message.id, user_id=w.owner.id,
                        kind="set_variant_price", payload_json={"price_minor": 4500},
                        summary="Change the price to $45.00", status="pending",
                        idempotency_key="pending-history-action"),
        ])
        db.commit()
    assistant = next(row for row in w.client.get(path).json()["messages"] if row["id"] == message.id)
    assert assistant["events"] == [{"name": "update_block", "label": "Updated hero", "status": "error"}]
    assert assistant["actions"][0]["summary"] == "Change the price to $45.00"
    observed = {}
    monkeypatch.setattr(store_agent, "restore_checkpoint",
                        lambda db, site, user, message_id: observed.update(site=site.id, message=message_id))
    restore = f"/admin/sites/{w.site.id}/editor/agent/restore"
    assert w.client.post(restore, json={"message_id": "m-1"}).status_code == 403
    assert w.client.post(restore, json={"message_id": "m-1"}, headers=w.headers).status_code == 200
    assert observed == {"site": w.site.id, "message": "m-1"}


def test_assistant_documents_are_private_extracted_text(workspace):
    w = workspace
    path = w.base + "/assistant-documents"
    assert w.client.post(path, files={"file": ("brief.txt", b"Brief", "text/plain")}).status_code == 403
    assert w.client.post(path, files={"file": ("brief.exe", b"x", "application/octet-stream")},
                         headers=w.headers).status_code == 422
    uploaded = w.client.post(path, files={"file": ("brief.txt", b"Store brief\nDraft only.", "text/plain")},
                             headers=w.headers)
    assert uploaded.status_code == 201, uploaded.text
    attachment_id = uploaded.json()["attachment"]["id"]
    with w.sessions() as db:
        record = db.get(AgentDocumentAttachment, attachment_id)
        assert record.extracted_text == "Store brief\nDraft only." and record.user_id == w.owner.id
        assert db.query(SiteMedia).filter_by(site_id=w.site.id).count() == 0
    listed = w.client.get(path).json()["attachments"]
    assert listed[0]["id"] == attachment_id and "extracted_text" not in listed[0]
    assert w.client.get(f"/admin/sites/{w.other.id}/editor/api/assistant-documents").status_code == 403
    assert w.client.delete(path + "/missing", headers=w.headers).status_code == 404
    assert w.client.delete(path + "/" + attachment_id, headers=w.headers).json() == {"removed": attachment_id}


def test_action_decisions_require_csrf_and_a_store_manager(workspace):
    w = workspace
    with w.sessions() as db:
        conversation = AgentConversation(tenant_id=w.site.tenant_id, site_id=w.site.id,
                                         user_id=w.owner.id, title="Price")
        db.add(conversation)
        db.flush()
        action = AgentAction(tenant_id=w.site.tenant_id, site_id=w.site.id,
                             conversation_id=conversation.id, user_id=w.owner.id, kind="unknown",
                             payload_json={}, summary="Do something", status="pending",
                             idempotency_key="decision-key")
        db.add(action)
        db.commit()
    url = f"/admin/sites/{w.site.id}/agent/actions/{action.id}/decline"
    assert w.client.post(url, data={"csrf_token": "wrong"}).status_code == 400
    w.state.user = w.editor.id
    assert w.client.post(url, data={"csrf_token": "assistant-csrf"}).status_code == 400
    w.state.user = w.owner.id
    declined = w.client.post(url, data={"csrf_token": "assistant-csrf"})
    assert declined.status_code == 200 and declined.json()["status"] == "declined"
    listed = w.client.get(f"/admin/sites/{w.site.id}/agent/actions?status=declined").json()
    assert [row["id"] for row in listed] == [action.id]

"""Store assistant routes: the studio page, the theme-editor stream, documents and model settings.

Registered with the same security callbacks as every other builder registrar. The model
connection itself lives in ``app.assistant_provider``.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from urllib.parse import urlencode

import anyio
from fasthtml.common import (
    H2,
    A,
    Button,
    Dd,
    Div,
    Dl,
    Dt,
    Form,
    Input,
    Label,
    Link,
    NotStr,
    Option,
    P,
    Script,
    Select,
    Span,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse, StreamingResponse
from starlette.routing import Route

from app import assistant_provider, config, content, site_integrations, store_agent_admin
from app.admin_ui import badge, card
from app.agent_document_attachments import MAX_DOCUMENT_BYTES, extract_document
from app.db import SessionLocal
from app.models import AgentAction, AgentConversation, AgentDocumentAttachment, AgentMessage
from app.services import CommerceError
from app.site_editor_routes import EditorError, failure, response
from app.ui import static_asset

MAX_EDITOR_PAYLOAD = 2_000_000


def _editor_history(db, site, user_id):
    """The latest conversation in the theme editor, with review cards attached to replies."""
    conversation = db.scalar(select(AgentConversation).where(
        AgentConversation.tenant_id == site.tenant_id,
        AgentConversation.site_id == site.id,
        AgentConversation.user_id == user_id,
    ).order_by(AgentConversation.created_at.desc()).limit(1))
    if not conversation:
        return {"conversation_id": None, "messages": []}
    rows = list(db.scalars(select(AgentMessage).where(
        AgentMessage.tenant_id == site.tenant_id,
        AgentMessage.site_id == site.id,
        AgentMessage.conversation_id == conversation.id,
    ).order_by(AgentMessage.created_at, AgentMessage.id).limit(100)))
    actions = db.scalars(select(AgentAction).where(
        AgentAction.tenant_id == site.tenant_id,
        AgentAction.site_id == site.id,
        AgentAction.conversation_id == conversation.id,
    ).order_by(AgentAction.created_at, AgentAction.id)).all()
    actions_by_message, unlinked = {}, []
    for action in actions:
        if action.message_id:
            actions_by_message.setdefault(action.message_id, []).append(action)
        elif action.status == "pending":
            unlinked.append(action)
    if unlinked:
        latest = next((row for row in reversed(rows) if row.role == "assistant"), None)
        if latest:
            actions_by_message.setdefault(latest.id, []).extend(unlinked)
    results = {}
    for row in rows:
        if row.role != "tool":
            continue
        for call in row.tool_calls_json or []:
            name = call.get("function", {}).get("name")
            if name:
                try:
                    result = json.loads(row.content or "{}")
                except (TypeError, ValueError):
                    result = {}
                results.setdefault(name, []).append(bool(result.get("error")))

    def message_events(message):
        if message.role != "assistant":
            return []
        events = list(message.events_json or [])
        if events:
            return events
        for call in message.tool_calls_json or []:
            name = call.get("name")
            if not name:
                continue
            statuses = results.get(name, [])
            failed = statuses.pop(0) if statuses else False
            events.append({"name": name, "label": call.get("label", name),
                           "status": "error" if failed else "done"})
        return events

    return {"conversation_id": conversation.id, "actions": [], "messages": [
        {"id": m.id, "role": m.role, "content": m.content, "events": message_events(m),
         "actions": [{"id": action.id, "kind": action.kind, "summary": action.summary,
                      "status": action.status, "result": action.result_json or {}}
                     for action in actions_by_message.get(m.id, [])],
         "has_checkpoint": bool(m.checkpoint_revision_id)} for m in rows
    ]}


def _fake_transport_enabled():
    """A scripted model for local demos and browser checks. Never available in production."""
    return (config.settings.environment.lower() != "production"
            and os.getenv("FASTSHOP_AGENT_FAKE") == "1")


def _scripted_transport(site, page, message, selection, fallback_block_id):
    from app.models import ProductVariant, VariantChannelListing

    with SessionLocal() as db:
        pair = db.execute(select(ProductVariant, VariantChannelListing).join(
            VariantChannelListing, VariantChannelListing.variant_id == ProductVariant.id
        ).where(ProductVariant.tenant_id == site.tenant_id,
                VariantChannelListing.channel_id == site.channel_id).limit(1)).first()
        variant_id = pair[0].id if pair else ""
        product_id = pair[0].product_id if pair else ""
        price = pair[1].price_minor if pair else 0
    page_id = page.id

    class ScriptedTransport:
        def __init__(self):
            self.sent = False
            self.price_step = 0
            self.price_task = "price change" in message.casefold()
            self.decline_price = "decline" in message.casefold()

        def complete(self, messages, tools):
            if self.price_task:
                self.price_step += 1
                if self.price_step == 1:
                    return {"tool_calls": [{"id": "fake-read-product", "type": "function",
                        "function": {"name": "get_product",
                                     "arguments": json.dumps({"product_id": product_id})}}]}
                if self.price_step == 2:
                    return {"tool_calls": [{"id": "fake-set-price", "type": "function",
                        "function": {"name": "set_variant_price", "arguments": json.dumps({
                            "variant_id": variant_id,
                            "price_minor": price + (5000 if self.decline_price else 2000)})}}]}
                return {"content": "I have proposed the price change for your review."}
            if self.sent:
                delay = float(os.getenv("FASTSHOP_AGENT_FAKE_DELAY", "0"))
                if delay:
                    time.sleep(delay)
                return {"content": "I updated the selected block in your draft preview."}
            self.sent = True
            block_id = (selection or {}).get("block_id") or fallback_block_id
            return {"tool_calls": [{"id": "fake-update", "type": "function",
                "function": {"name": "update_block", "arguments": json.dumps({
                    "page_id": page_id, "block_id": block_id,
                    "fields": {"heading": "Assistant preview update"}})}}]}

    return ScriptedTransport()


def _event_stream(events):
    async def stream():
        for event in events:
            yield "data: " + json.dumps(event, ensure_ascii=True) + "\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


def _settings_page(site, session_csrf, model_status, stored_payload):
    """The AI assistant settings page. The saved key is never rendered, only its masked hint."""
    base = f"/admin/sites/{site.id}/settings/assistant"
    state, hints = model_status["state"], model_status["hints"]
    tone = {"store": "success", "platform": "info", "unreadable": "warning"}.get(state, "neutral")
    current = (stored_payload or {}).get("provider", "xai")
    saved_model = (stored_payload or {}).get("model", "")
    saved_url = (stored_payload or {}).get("base_url", "") if current == assistant_provider.CUSTOM else ""
    facts = []
    if state == "store":
        facts = [("Provider", hints.get("provider", "")), ("Model", hints.get("model", "")),
                 ("API key", hints.get("api_key", "")), ("Endpoint", hints.get("endpoint", ""))]
        verified = model_status.get("verified_at")
        if verified:
            facts.append(("Last checked", verified.strftime("%d %b %Y, %H:%M")))
    explain = {
        "store": "The assistant uses your own model connection. Requests are billed by your provider.",
        "platform": ("This store currently uses the platform's shared key from your FastShop operator. "
                     "Connect your own key to choose your provider and model."),
        "unreadable": ("A saved connection can't be read on this server. Save your API key again "
                       "to reconnect."),
        "none": ("Connect a model to use the assistant in the admin and the theme editor. "
                 "Any OpenAI-compatible chat API works."),
    }[state]
    status_card = card(
        Div(H2("Connection"), badge(model_status["label"], tone), cls="assistant-settings-status"),
        P(explain),
        Dl(*[part for label, value in facts if value for part in (Dt(label), Dd(value))],
           cls="assistant-settings-facts") if facts else None,
        Div(
            Form(Input(type="hidden", name="csrf_token", value=session_csrf),
                 Button("Test connection", cls="e-button", type="submit"),
                 method="post", action=base + "/test") if state == "store" else None,
            Form(Input(type="hidden", name="csrf_token", value=session_csrf),
                 Button("Disconnect", cls="e-button danger", type="submit"),
                 method="post", action=base + "/disconnect") if state in {"store", "unreadable"} else None,
            cls="e-actions assistant-settings-actions") if state in {"store", "unreadable"} else None,
        cls="assistant-settings-card",
    )
    providers = assistant_provider.PROVIDERS
    key_hint = (f"Saved key {hints['api_key']}. Leave blank to keep it."
                if state == "store" and hints.get("api_key") else "Paste your API key")
    form = card(
        H2("Connect your own model"),
        P("Choose an OpenAI-compatible provider, paste an API key and pick a model. The key is "
          "encrypted, used only for this store and never shown again."),
        Form(
            Input(type="hidden", name="csrf_token", value=session_csrf),
            Label("Provider", Select(
                *[Option(spec["label"], value=key, selected=key == current,
                         data_model=spec["model"], data_keys=spec["keys"])
                  for key, spec in providers.items()],
                name="provider", data_assistant_provider="")),
            Label("Endpoint address",
                  Input(name="base_url", type="url", value=saved_url, inputmode="url",
                        placeholder="https://api.example.com/v1", autocomplete="off", spellcheck="false"),
                  Span("Must use https and be publicly reachable. We add /chat/completions.",
                       cls="g-help"),
                  data_assistant_custom="", hidden=current != assistant_provider.CUSTOM),
            Label("API key", Input(name="api_key", type="password", placeholder=key_hint,
                                   autocomplete="new-password", spellcheck="false",
                                   maxlength=str(assistant_provider.MAX_KEY_LENGTH))),
            Label("Model", Input(name="model", value=saved_model, spellcheck="false",
                                 maxlength=str(assistant_provider.MAX_MODEL_LENGTH),
                                 placeholder=providers[current]["model"] or "Model name",
                                 data_assistant_model=""),
                  Span("Leave blank to use the suggested model for your provider.", cls="g-help")),
            P(A("Where do I find my API key?", href=providers[current]["keys"] or "#",
                target="_blank", rel="noopener", data_assistant_keys=""),
              cls="g-help", hidden=not providers[current]["keys"]),
            Button("Save connection", cls="e-button primary", type="submit"),
            method="post", action=base, cls="e-form", data_dirty_guard=True,
        ),
        P("Changing the provider or endpoint needs the API key again, so a saved key is never "
          "sent somewhere new.", cls="g-help"),
    )
    script = Script(NotStr("""
(() => {
  const select = document.querySelector("[data-assistant-provider]");
  if (!select) return;
  const sync = () => {
    const option = select.selectedOptions[0];
    const custom = document.querySelector("[data-assistant-custom]");
    const model = document.querySelector("[data-assistant-model]");
    const keys = document.querySelector("[data-assistant-keys]");
    custom.hidden = select.value !== "custom";
    model.placeholder = option.dataset.model || "Model name";
    keys.parentElement.hidden = !option.dataset.keys;
    if (option.dataset.keys) keys.href = option.dataset.keys;
  };
  select.addEventListener("change", sync);
})();
"""))
    return (Link(rel="stylesheet", href=static_asset("admin/assistant-settings.css")),
            Div(status_card, form, cls="assistant-settings"), script)


def register_assistant_routes(rt, actor, csrf, check_csrf, shell, error):
    def editor_access(db, session, site_id):
        try:
            user_id = actor(session)
        except CommerceError as exc:
            raise EditorError(str(exc), 401) from exc
        try:
            site = content.owned_site(db, site_id, user_id)
        except CommerceError as exc:
            raise EditorError(str(exc), 403) from exc
        return site, user_id

    def header_csrf(session, request):
        try:
            check_csrf(session, {"csrf_token": request.headers.get("X-CSRF-Token", "")})
        except CommerceError as exc:
            raise EditorError(str(exc), 403) from exc

    async def json_payload(session, request):
        header_csrf(session, request)
        try:
            length = int(request.headers.get("content-length", "0") or 0)
        except ValueError as exc:
            raise EditorError("Invalid content length.", 400) from exc
        if length > MAX_EDITOR_PAYLOAD:
            raise EditorError("This request is too large.", 413)
        body = await request.body()
        if len(body) > MAX_EDITOR_PAYLOAD:
            raise EditorError("This request is too large.", 413)
        try:
            value = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise EditorError("Send a JSON object.") from exc
        if not isinstance(value, dict):
            raise EditorError("Send a JSON object.")
        return value

    def page_for(db, site, page_id):
        if not page_id:
            pages = content.site_pages(db, site)
            if pages:
                return next((p for p in pages if p.kind == "home"), pages[0])
        try:
            return content.site_page(db, site, page_id)
        except CommerceError as exc:
            raise EditorError(str(exc), 404) from exc

    # Studio: the full-page assistant beside a live draft preview.
    @rt("/admin/sites/{site_id}/assistant", methods=["GET"])
    def studio(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                page = store_agent_admin.home_page(db, site)
                configured = assistant_provider.is_configured(site, db)
                csrf(session)
                return shell("Assistant", *store_agent_admin.page(
                    site, page, session["csrf_token"], store_agent_admin.site_pages(db, site),
                    configured=configured))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/assistant/conversations", methods=["GET"])
    def conversations(session, site_id: str):
        try:
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                return store_agent_admin.conversations(db, site, user_id)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/assistant/history", methods=["GET"])
    def history(session, site_id: str, conversation_id: str = ""):
        try:
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                return store_agent_admin.history(db, site, user_id, conversation_id)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/assistant/tasks", methods=["GET"])
    def tasks(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                return store_agent_admin.tasks(db, site)
        except CommerceError as exc:
            return error(exc)

    # Theme editor and drawer: history, checkpoint restore and the streamed turn.
    @rt("/admin/sites/{site_id}/editor/agent/history", methods=["GET"])
    def editor_history(session, site_id: str):
        try:
            with SessionLocal() as db:
                site, user_id = editor_access(db, session, site_id)
                return response(_editor_history(db, site, user_id))
        except CommerceError as exc:
            return failure(exc)

    async def editor_restore(request):
        session = request.session
        try:
            values = await json_payload(session, request)
            with SessionLocal() as db:
                site, user_id = editor_access(db, session, request.path_params["site_id"])
                from app.store_agent import restore_checkpoint
                restore_checkpoint(db, site, user_id, str(values.get("message_id", "")))
                db.commit()
                return response({"restored": True})
        except (CommerceError, ValueError, TypeError) as exc:
            return failure(exc)

    async def editor_stream(request):
        session = request.session
        try:
            values = await json_payload(session, request)
            message = str(values.get("message", "")).strip()
            if not message or len(message) > 4000:
                raise EditorError("Write a message up to 4,000 characters.")
            selection = values.get("selection") if isinstance(values.get("selection"), dict) else None
            with SessionLocal() as db:
                site, user_id = editor_access(db, session, request.path_params["site_id"])
                page = page_for(db, site, str(values.get("page_id", "")))
                blocks = (page.draft_json or {}).get("blocks") or [{}]
                fallback_block_id = next((block.get("id") for block in blocks
                                          if block.get("type") == "hero"), blocks[0].get("id"))
                configured = assistant_provider.is_configured(site, db)
            fake = _fake_transport_enabled()
            if not configured and not fake:
                from app.store_agent import NOT_CONFIGURED
                return _event_stream(({"type": "assistant_text", "text": NOT_CONFIGURED,
                                       "settings_href": assistant_provider.settings_href(site.id)},
                                      {"type": "done"}))
            from app import store_agent
            kwargs = {"page_id": page.id, "selection": selection,
                      "conversation_id": str(values.get("conversation_id") or "") or None}
            if fake:
                kwargs["transport"] = _scripted_transport(site, page, message, selection,
                                                          fallback_block_id)
            events: queue.Queue = queue.Queue()
            site_id = request.path_params["site_id"]

            def produce():
                try:
                    with SessionLocal() as db:
                        site_row = content.owned_site(db, site_id, user_id)
                        for event in store_agent.run_turn(db, site_row, user_id, message, **kwargs):
                            events.put(event)
                except Exception as exc:  # noqa: BLE001 - surfaced to the merchant as one event
                    events.put({"type": "error", "message": str(exc)[:300]})
                finally:
                    events.put(None)

            threading.Thread(target=produce, daemon=True).start()

            async def stream():
                while True:
                    item = await anyio.to_thread.run_sync(events.get)
                    if item is None:
                        break
                    yield "data: " + json.dumps(item, ensure_ascii=True) + "\n\n"

            return StreamingResponse(stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        except (CommerceError, ValueError, TypeError) as exc:
            return failure(exc)

    router = rt.__self__.router
    router.routes.append(Route("/admin/sites/{site_id}/editor/agent/restore",
                               endpoint=editor_restore, methods=["POST"]))
    router.routes.append(Route("/admin/sites/{site_id}/editor/agent",
                               endpoint=editor_stream, methods=["POST"]))

    # Private document sources the assistant can read in a conversation.
    @rt("/admin/sites/{site_id}/editor/api/assistant-documents", methods=["POST"])
    async def document_post(session, request, site_id: str):
        try:
            header_csrf(session, request)
            with SessionLocal() as db:
                site, user_id = editor_access(db, session, site_id)
                form = await request.form()
                upload = form.get("file")
                if not upload or not hasattr(upload, "read"):
                    raise EditorError("Select a document.")
                title = str(getattr(upload, "filename", "") or "document")[:200]
                data = await upload.read(MAX_DOCUMENT_BYTES + 1)
                content_type, extracted_text = extract_document(title, data)
                attachment = AgentDocumentAttachment(
                    tenant_id=site.tenant_id, site_id=site.id, user_id=user_id, title=title,
                    content_type=content_type, size=len(data), extracted_text=extracted_text,
                )
                db.add(attachment)
                db.commit()
                return response({"attachment": {
                    "id": attachment.id, "title": attachment.title,
                    "content_type": attachment.content_type,
                    "characters": len(attachment.extracted_text),
                }}, 201)
        except (CommerceError, ValueError, TypeError) as exc:
            return failure(exc)

    @rt("/admin/sites/{site_id}/editor/api/assistant-documents", methods=["GET"])
    def documents_get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site, user_id = editor_access(db, session, site_id)
                rows = db.scalars(select(AgentDocumentAttachment).where(
                    AgentDocumentAttachment.tenant_id == site.tenant_id,
                    AgentDocumentAttachment.site_id == site.id,
                    AgentDocumentAttachment.user_id == user_id,
                ).order_by(AgentDocumentAttachment.created_at.desc()).limit(40))
                return response({"attachments": [{
                    "id": row.id, "title": row.title, "content_type": row.content_type,
                    "size": row.size, "characters": len(row.extracted_text),
                    "created_at": row.created_at.isoformat(),
                } for row in rows]})
        except CommerceError as exc:
            return failure(exc)

    @rt("/admin/sites/{site_id}/editor/api/assistant-documents/{attachment_id}", methods=["DELETE"])
    def document_delete(session, request, site_id: str, attachment_id: str):
        try:
            header_csrf(session, request)
            with SessionLocal() as db:
                site, user_id = editor_access(db, session, site_id)
                row = db.scalar(select(AgentDocumentAttachment).where(
                    AgentDocumentAttachment.id == attachment_id,
                    AgentDocumentAttachment.tenant_id == site.tenant_id,
                    AgentDocumentAttachment.site_id == site.id,
                    AgentDocumentAttachment.user_id == user_id,
                ))
                if row is None:
                    raise EditorError("Document attachment not found.", status=404)
                db.delete(row)
                db.commit()
                return response({"removed": attachment_id})
        except CommerceError as exc:
            return failure(exc)

    # Settings, AI assistant: each store connects its own model. Store managers only.
    def settings_url(site_id, notice=""):
        query = "?" + urlencode({"notice": notice[:250]}) if notice else ""
        return assistant_provider.settings_href(site_id) + query

    def managed_site(db, session, site_id):
        return content.owned_site(db, site_id, actor(session), publish=True)

    @rt("/admin/sites/{site_id}/settings/assistant", methods=["GET"])
    def settings_get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = managed_site(db, session, site_id)
                csrf(session)
                return shell("AI assistant", *_settings_page(
                    site, session["csrf_token"], assistant_provider.status(db, site),
                    _safe_saved(db, site)))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/assistant", methods=["POST"])
    async def settings_post(session, request, site_id: str):
        form = await request.form()
        with SessionLocal() as db:
            try:
                check_csrf(session, form)
                site = managed_site(db, session, site_id)
                provider = str(form.get("provider", "")).strip().lower()
                base_url = str(form.get("base_url", "")).strip()
                api_key = str(form.get("api_key", "")).strip()
                existing = assistant_provider.stored(db, site)
                if not api_key and existing:
                    # A blank key keeps the saved one, but never sends it to a new destination.
                    same_place = existing["provider"] == provider and (
                        provider != assistant_provider.CUSTOM
                        or assistant_provider.validate_base_url(base_url, resolve=False)
                        == existing["base_url"])
                    if not same_place:
                        raise CommerceError("Enter the API key again when you change the provider "
                                            "or endpoint.")
                    api_key = existing["api_key"]
                payload = assistant_provider.clean_connection(
                    provider, api_key, str(form.get("model", "")), base_url)
                try:
                    site_integrations.save(db, site, assistant_provider.CREDENTIAL, payload,
                                           "default", assistant_provider.hints_for(payload))
                    # Saving is not checking: "Last checked" appears only after a successful test.
                    site_integrations.credential_row(
                        db, site, assistant_provider.CREDENTIAL).verified_at = None
                except CommerceError as exc:
                    if "encryption" in str(exc).lower():
                        raise CommerceError("Secure storage for connections isn't set up on this "
                                            "server yet. Ask your FastShop operator to turn it on.") from exc
                    raise
                db.commit()
            except CommerceError as exc:
                db.rollback()
                return RedirectResponse(settings_url(site_id, str(exc)), status_code=303)
        return RedirectResponse(settings_url(
            site_id, "Model connection saved. Test it to check the key works."), status_code=303)

    @rt("/admin/sites/{site_id}/settings/assistant/test", methods=["POST"])
    async def settings_test(session, request, site_id: str):
        form = await request.form()
        with SessionLocal() as db:
            try:
                check_csrf(session, form)
                site = managed_site(db, session, site_id)
                payload = assistant_provider.stored(db, site)
                if not payload:
                    raise CommerceError("Save a model connection before testing it.")
                model_config = assistant_provider.resolve(db, site)
                ok, message = assistant_provider.check_connection(model_config)
                if ok:
                    assistant_provider.mark_verified(db, site)
                    db.commit()
            except CommerceError as exc:
                db.rollback()
                message = str(exc)
        return RedirectResponse(settings_url(site_id, message), status_code=303)

    @rt("/admin/sites/{site_id}/settings/assistant/disconnect", methods=["POST"])
    async def settings_disconnect(session, request, site_id: str):
        form = await request.form()
        with SessionLocal() as db:
            try:
                check_csrf(session, form)
                site = managed_site(db, session, site_id)
                site_integrations.disconnect(db, site, assistant_provider.CREDENTIAL)
                db.commit()
            except CommerceError as exc:
                return RedirectResponse(settings_url(site_id, str(exc)), status_code=303)
        return RedirectResponse(settings_url(site_id, "Model connection removed."), status_code=303)


def _safe_saved(db, site):
    """Non-secret parts of the saved connection, for pre-filling the form."""
    payload = assistant_provider.stored(db, site)
    if not payload:
        return None
    return {key: payload[key] for key in ("provider", "model", "base_url") if key in payload}

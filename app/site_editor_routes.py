"""Private, tenant-scoped visual editor API using the existing content services."""
from __future__ import annotations

import copy
import inspect
import json
import os
import queue
import re
import threading
import time
from html import escape
from types import SimpleNamespace

import anyio
from fasthtml.common import NotStr, to_xml
from sqlalchemy import select, update
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app import content, site_ui, theme_library
from app.config import settings
from app.db import SessionLocal
from app.models import (
    AgentAction,
    AgentConversation,
    AgentMessage,
    Membership,
    SiteMedia,
    SitePage,
    SiteRevision,
    new_id,
)
from app.services import CommerceError
from app.site_blocks import (
    default_locale,
    normalize_document,
    resolve_document,
    resolve_text,
    validate_block,
)
from app.site_media import MAX_IMAGE_BYTES, media_for, media_url, prepare_image_bytes
from app.site_theme import CHOICES, DEFAULTS, validate_theme


class EditorError(CommerceError):
    def __init__(self, message, status=422, **details):
        super().__init__(message)
        self.status, self.details = status, details


def response(value, status=200):
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store"})


def failure(exc):
    message = str(exc)
    match = re.search(r"(?:blocks|sections)\[\d+\](?:\.[\w]+(?:\[\d+\])?)*", message)
    return response({"error": message, "errors": [{"path": getattr(exc, "path", match.group() if match else "$"),
                    "message": message}], **getattr(exc, "details", {})},
                    getattr(exc, "status", 422))


def validate_editor_document(document):
    try:
        return content.validate_document(document)
    except CommerceError as exc:
        # Identify the deepest failing block without weakening the canonical validator.
        blocks = document if isinstance(document, list) else document.get("blocks", document.get("sections", [])) if isinstance(document, dict) else []
        seen = set()
        def locate(nodes, prefix="blocks", depth=1):
            if not isinstance(nodes, list):
                return prefix
            for index, block in enumerate(nodes):
                path = f"{prefix}[{index}]"
                if not isinstance(block, dict):
                    return path
                identity = block.get("id")
                if isinstance(identity, str):
                    if identity in seen:
                        return path
                    seen.add(identity)
                if depth > 3:
                    return path
                if "children" in block:
                    nested = locate(block["children"], path + ".children", depth + 1)
                    if nested:
                        return nested
                try:
                    validate_block(block)
                except CommerceError:
                    return path
            return None
        exc.path = locate(blocks) or "$"
        raise


def shell_payload(site, theme=""):
    """Editable shell fields declared by the active storefront settings."""
    from app.site_editable_settings import shell_fields, shell_values

    fields = [] if theme else shell_fields(site.settings_json)
    return {"shell_schema": {"type": "__shell", "label": "Store settings", "fields": fields},
            "shell_settings": shell_values(site.settings_json, fields)}


def _version(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EditorError("Supply the current version.")
    return value


def _media(row, locale):
    return {"id": row.id, "url": media_url(row), "title": row.title,
            "alt": resolve_text(row.localized_alt or row.alt, locale),
            "content_type": row.content_type, "size": row.size}


def _html(node):
    if node is None:
        return ""
    if isinstance(node, (tuple, list)):
        return "".join(_html(child) for child in node)
    return str(node) if isinstance(node, NotStr) else str(to_xml(node))


def render_document(db, site, page, document, token, block_id=""):
    """Render unsaved content on a detached copy so preview cannot persist changes."""
    document = validate_editor_document(document)
    content.validate_media_ownership(db, site, document)
    from app import custom_data
    document = custom_data.resolve_dynamic_document(db, site, page, document)
    preview = SimpleNamespace(**{column.key: getattr(page, column.key)
                                 for column in SitePage.__table__.columns})
    preview.draft_json = document
    if block_id:
        def find(blocks):
            for block in blocks:
                if block["id"] == block_id:
                    return block
                nested = find(block.get("children", []))
                if nested:
                    return nested
            return None
        resolved = resolve_document(document, default_locale(site))
        block = find(resolved["blocks"])
        if block is None:
            raise EditorError("Block not found.", 404)
        from app.site_theme import pack_renderer
        renderer = pack_renderer(site.settings_json or {}, "block")
        if renderer:
            node = renderer(db, site, preview, site.settings_json, block,
                            f"/sites/{site.slug}", token, preview=True)
        else:
            node = site_ui.render_section(db, site, preview, site.settings_json, block,
                                          f"/sites/{site.slug}", token, True)
        if hasattr(node, "attrs"):
            node.attrs["data-block-id"] = block_id
        return _html(node)
    nodes = site_ui.storefront(db, site, preview, f"/sites/{site.slug}", token,
                              f"/sites/{site.slug}{page.path}", preview=True, editor=True)
    html = _html(nodes)
    # Only application-owned script is introduced, never merchant code.
    config = json.dumps({"siteId": site.id, "pageId": page.id}).replace("<", "\\u003c")
    bridge = (f'<script id="editor-canvas-config" type="application/json">{config}</script>'
              f'<script src="/static/editor/canvas.js" defer data-site-id="{escape(site.id)}" '
              f'data-page-id="{escape(page.id)}"></script>')
    html = html.replace('src="/static/site-preview.js', 'data-disabled-src="/static/site-preview.js')
    return html + bridge


def register_site_editor_routes(rt, actor, csrf, check_csrf, shell, error):
    def json_route(path, *, methods):
        """Keep JSON validation in the editor, before FastHTML form injection."""
        def register(handler):
            parameters = inspect.signature(handler).parameters

            async def endpoint(request):
                values = dict(request.path_params)
                for key in ("page", "theme"):
                    if key in parameters:
                        values[key] = request.query_params.get(key, "")
                return await handler(request.session, request, **values)

            rt.__self__.router.routes.append(Route(path, endpoint=endpoint, methods=methods))
            return handler
        return register

    def access(db, session, site_id, *, publish=False):
        try:
            user_id = actor(session)
        except CommerceError as exc:
            raise EditorError(str(exc), 401) from exc
        try:
            site = content.owned_site(db, site_id, user_id, publish=publish)
        except CommerceError as exc:
            raise EditorError(str(exc), 403) from exc
        return site, user_id

    def page_for(db, site, page_id):
        if not page_id:
            pages = content.site_pages(db, site)
            if pages:
                return next((p for p in pages if p.kind == "home"), pages[0])
        try:
            return content.site_page(db, site, page_id)
        except CommerceError as exc:
            raise EditorError(str(exc), 404) from exc

    def editor_context(db, site, page_id, theme_id):
        if theme_id:
            try:
                return theme_library.context(db, site, theme_id, page_id)
            except CommerceError as exc:
                raise EditorError(str(exc), 404) from exc
        return site, page_for(db, site, page_id)

    def header_csrf(session, request):
        try:
            check_csrf(session, {"csrf_token": request.headers.get("X-CSRF-Token", "")})
        except CommerceError as exc:
            raise EditorError(str(exc), 403) from exc
    async def payload(session, request):
        header_csrf(session, request)
        try:
            length = int(request.headers.get("content-length", "0") or 0)
        except ValueError as exc:
            raise EditorError("Invalid content length.", 400) from exc
        if length > 2_000_000:
            raise EditorError("This editor request is too large.", 413)
        body = await request.body()
        if len(body) > 2_000_000:
            raise EditorError("This editor request is too large.", 413)
        try:
            value = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise EditorError("Send a JSON object.") from exc
        if not isinstance(value, dict):
            raise EditorError("Send a JSON object.")
        return value

    def guard_version(db, site, page, value):
        version = _version(value)
        result = db.execute(update(SitePage).where(
            SitePage.id == page.id, SitePage.tenant_id == site.tenant_id,
            SitePage.site_id == site.id, SitePage.version == version,
        ).values(version=SitePage.version).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            db.refresh(page)
            raise EditorError("Someone updated this page. Reload or review before overwriting.",
                              409, conflict=True, version=page.version)
        return version

    @rt("/admin/sites/{site_id}/editor", methods=["GET"])
    def editor(session, site_id: str, page: str = "", theme: str = ""):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                site, selected = editor_context(db, site, page, theme)
                csrf(session)
                from app.site_editor_ui import editor_page
                return editor_page(site, selected, session["csrf_token"])
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/editor/api", methods=["GET"])
    @rt("/admin/sites/{site_id}/editor/api/document", methods=["GET"])
    def document(session, site_id: str, page: str = "", theme: str = ""):
        try:
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                site, selected = editor_context(db, site, page, theme)
                from app import custom_data
                from app.site_block_schema import block_schema_registry
                from app.site_widgets_pack3 import active_discount_codes

                schema = block_schema_registry()
                code_options = [{"value": "", "label": "No discount code"}] + [
                    {"value": code.id, "label": code.code}
                    for code in active_discount_codes(db, site)
                ]
                for field in schema["promo_banner"]["fields"]:
                    if field["id"] == "discount_code":
                        field.update(type="select", options=code_options)
                role = db.scalar(select(Membership.role).where(
                    Membership.tenant_id == site.tenant_id, Membership.user_id == user_id))
                csrf(session)
                return response({"document": normalize_document(selected.draft_json),
                    "version": selected.version, "site_version": site.version,
                    "default_locale": default_locale(site),
                    "dynamic_sources": custom_data.compatible_sources(db, site),
                    "schema": schema, "pages": [
                        {"id": p.id, "title": p.title, "kind": p.kind, "path": p.path}
                        for p in ([theme_library.context(db, site, theme, saved["id"])[1]
                            for saved in theme_library.theme_for(db, site, theme).snapshot_json["pages"]]
                            if theme else content.site_pages(db, site))],
                    "site": {"id": site.id, "name": site.name, "slug": site.slug},
                    "page": {"id": selected.id, "title": selected.title, "kind": selected.kind,
                        "product_id": custom_data.product_id_for_page(db, site, selected),
                             "path": selected.path},
                    "theme_settings": DEFAULTS | (site.settings_json or {}).get("design", {}),
                    **shell_payload(site, theme),
                    "theme_choices": CHOICES, "can_publish": role in {"admin", "merchant"}})
        except CommerceError as exc:
            return failure(exc)

    @json_route("/admin/sites/{site_id}/editor/api/render", methods=["POST"])
    async def render(session, request, site_id: str, page: str = "", theme: str = ""):
        try:
            values = await payload(session, request)
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                site, selected = editor_context(db, site, page, theme)
                return response({"html": render_document(db, site, selected,
                    values.get("document"), session.get("csrf_token", ""),
                    values.get("block_id", ""))})
        except (CommerceError, ValueError, TypeError) as exc:
            return failure(exc)

    async def write(session, request, site_id, page_id, action, revision_id="", theme_id=""):
        try:
            values = await payload(session, request)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id, publish=action == "publish")
                if theme_id:
                    _, selected = editor_context(db, site, page_id, theme_id)
                    document = values.get("document", selected.draft_json)
                    if action == "restore":
                        saved = theme_library.theme_for(db, site, theme_id)
                        revision = next((r for r in saved.snapshot_json.get("revisions", [])
                            if r["id"] == (revision_id or values.get("revision_id"))
                            and r["page_id"] == selected.id), None)
                        if not revision:
                            raise EditorError("Revision not found.", 404)
                        document = revision["document"]
                    saved = theme_library.edit(db, site, user_id, theme_id, selected.id,
                        validate_editor_document(document), _version(values.get("version")))
                    if action == "publish":
                        theme_library.publish(db, site, user_id, theme_id, saved.version)
                    from app.compliance import warning_for_values
                    theme_documents = [page.get("document") for page in
                                       saved.snapshot_json.get("pages", [])]
                    warning = warning_for_values(theme_documents)
                    result = {"version": saved.version, "site_version": saved.version,
                        "document": document, "published": action == "publish",
                        "warnings": [warning] if warning else []}
                    db.commit()
                    return response(result)
                selected = page_for(db, site, page_id)
                published_design = None
                if action == "publish":
                    from app.site_builder_services import lock_site
                    site_version = _version(values.get("site_version", site.version))
                    try:
                        site = lock_site(db, site.id, user_id, site_version)
                    except CommerceError as exc:
                        raise EditorError(str(exc), 409, conflict=True,
                                          site_version=site.version, version=selected.version) from exc
                    if "design" in (site.settings_json or {}):
                        published_design = validate_theme(site.settings_json["design"])
                    from app.site_editable_settings import publish_shell, shell_fields
                    site.published_settings_json = publish_shell(site.settings_json,
                        site.published_settings_json, shell_fields(site.settings_json))
                version = guard_version(db, site, selected, values.get("version"))
                if action == "restore":
                    selected = content.restore_page(db, site, selected.id,
                        revision_id or str(values.get("revision_id", "")), user_id, version)
                else:
                    selected = content.save_page(db, site, selected.id, user_id,
                        validate_editor_document(values.get("document", selected.draft_json)), version, action)
                if action == "publish":
                    if published_design is not None:
                        published_settings = copy.deepcopy(site.published_settings_json or {})
                        published_settings["design"] = published_design
                        site.published_settings_json = published_settings
                    site.version += 1
                    if site.status == "draft":
                        site.status = "preview"
                from app.compliance import warning_for_values
                warning = warning_for_values(selected.draft_json)
                result = {"version": selected.version, "site_version": site.version,
                          "document": selected.draft_json, "published": action == "publish",
                          "warnings": [warning] if warning else []}
                db.commit()
                return response(result)
        except (CommerceError, ValueError, TypeError) as exc:
            return failure(exc)

    @json_route("/admin/sites/{site_id}/editor/api/draft", methods=["PUT"])
    async def draft(session, request, site_id: str, page: str = "", theme: str = ""):
        return await write(session, request, site_id, page, "draft", theme_id=theme)

    @json_route("/admin/sites/{site_id}/editor/api/publish", methods=["POST"])
    async def publish(session, request, site_id: str, page: str = "", theme: str = ""):
        return await write(session, request, site_id, page, "publish", theme_id=theme)

    @json_route("/admin/sites/{site_id}/editor/api/restore", methods=["POST"])
    async def restore(session, request, site_id: str, page: str = "", theme: str = ""):
        return await write(session, request, site_id, page, "restore", theme_id=theme)

    @json_route("/admin/sites/{site_id}/editor/api/revisions/{revision_id}/restore", methods=["POST"])
    async def restore_revision(session, request, site_id: str, revision_id: str, page: str = "", theme: str = ""):
        return await write(session, request, site_id, page, "restore", revision_id, theme)

    @rt("/admin/sites/{site_id}/editor/api/revisions", methods=["GET"])
    def revisions(session, site_id: str, page: str = "", theme: str = ""):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                if theme:
                    _, selected = editor_context(db, site, page, theme)
                    saved = theme_library.theme_for(db, site, theme)
                    return response({"revisions": [{"id": r["id"], "action": r["action"],
                        "created_at": saved.updated_at.isoformat(), "document": r["document"]}
                        for r in reversed(saved.snapshot_json.get("revisions", []))
                        if r["page_id"] == selected.id]})
                selected = page_for(db, site, page)
                rows = db.scalars(select(SiteRevision).where(
                    SiteRevision.tenant_id == site.tenant_id, SiteRevision.site_id == site.id,
                    SiteRevision.page_id == selected.id,
                ).order_by(SiteRevision.created_at.desc(), SiteRevision.id).limit(100))
                return response({"revisions": [{"id": r.id, "action": r.action,
                    "created_at": r.created_at.isoformat(), "document": r.content_json} for r in rows]})
        except CommerceError as exc:
            return failure(exc)

    @rt("/admin/sites/{site_id}/editor/api/theme-settings", methods=["GET"])
    def theme_get(session, site_id: str, theme: str = ""):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                if theme:
                    site, _ = editor_context(db, site, "", theme)
                return response({"theme_settings": DEFAULTS | site.settings_json.get("design", {}),
                                 "theme_choices": CHOICES, "version": site.version})
        except CommerceError as exc:
            return failure(exc)

    @json_route("/admin/sites/{site_id}/editor/api/theme-settings", methods=["PUT"])
    async def theme_put(session, request, site_id: str, theme: str = ""):
        try:
            values = await payload(session, request)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                if theme:
                    saved = theme_library.edit(db, site, user_id, theme, "", None,
                        _version(values.get("version")), design=values.get("theme_settings", {}))
                    result = {"theme_settings": saved.snapshot_json["settings"]["design"],
                              "version": saved.version}
                    db.commit()
                    return response(result)
                from app import site_builder_services as builder
                try:
                    site = builder.lock_site(db, site.id, user_id, _version(values.get("version")))
                except CommerceError as exc:
                    raise EditorError(str(exc), 409, conflict=True, version=site.version) from exc
                design = validate_theme(values.get("theme_settings", {}))
                builder.apply_operations(db, site.id, user_id, builder.snapshot(db, site),
                    [{"op": "theme", "values": design}], "visual-editor")
                result = {"theme_settings": design, "version": site.version}
                db.commit()
                return response(result)
        except (CommerceError, ValueError, TypeError) as exc:
            return failure(exc)

    @json_route("/admin/sites/{site_id}/editor/api/shell-settings", methods=["PUT"])
    async def shell_put(session, request, site_id: str, theme: str = ""):
        try:
            values = await payload(session, request)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                if theme:
                    raise EditorError("Edit the header and footer on the live theme.")
                from app import site_builder_services as builder
                expected = builder.snapshot(db, site)
                expected["version"] = _version(values.get("version"))
                try:
                    builder.save_shell_settings(db, site.id, user_id, expected,
                        values.get("shell_settings"), "visual-editor")
                except CommerceError as exc:
                    if "Draft changed" in str(exc):
                        raise EditorError(str(exc), 409, conflict=True, version=site.version) from exc
                    raise
                from app.compliance import warning_for_values
                warning = warning_for_values(values.get("shell_settings"))
                result = {**shell_payload(site), "version": site.version,
                          "warnings": [warning] if warning else []}
                db.commit()
                return response(result)
        except (CommerceError, ValueError, TypeError) as exc:
            return failure(exc)

    @rt("/admin/sites/{site_id}/editor/agent/history", methods=["GET"])
    def agent_history(session, site_id: str):
        try:
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                conversation = db.scalar(select(AgentConversation).where(
                    AgentConversation.tenant_id == site.tenant_id,
                    AgentConversation.site_id == site.id,
                    AgentConversation.user_id == user_id,
                ).order_by(AgentConversation.created_at.desc()).limit(1))
                if not conversation:
                    return response({"conversation_id": None, "messages": []})
                messages = db.scalars(select(AgentMessage).where(
                    AgentMessage.tenant_id == site.tenant_id,
                    AgentMessage.site_id == site.id,
                    AgentMessage.conversation_id == conversation.id,
                ).order_by(AgentMessage.created_at, AgentMessage.id).limit(100))
                rows = list(messages)
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
                actions = db.scalars(select(AgentAction).where(
                    AgentAction.tenant_id == site.tenant_id, AgentAction.site_id == site.id,
                    AgentAction.conversation_id == conversation.id, AgentAction.status == "pending"
                ).order_by(AgentAction.created_at))
                return response({"conversation_id": conversation.id, "actions": [
                    {"id": action.id, "kind": action.kind, "summary": action.summary}
                    for action in actions
                ], "messages": [
                    {"id": m.id, "role": m.role, "content": m.content,
                     "events": message_events(m),
                     "has_checkpoint": bool(m.checkpoint_revision_id)} for m in rows
                ]})
        except CommerceError as exc:
            return failure(exc)

    @rt("/admin/sites/{site_id}/editor/agent/restore", methods=["POST"])
    async def agent_restore(request):
        session = request.session
        try:
            values = await payload(session, request)
            with SessionLocal() as db:
                site, user_id = access(db, session, request.path_params["site_id"])
                from app.store_agent import restore_checkpoint
                restore_checkpoint(db, site, user_id, str(values.get("message_id", "")))
                db.commit()
                return response({"restored": True})
        except (CommerceError, ValueError, TypeError) as exc:
            return failure(exc)

    async def agent_stream(request):
        session = request.session
        try:
            values = await payload(session, request)
            message = str(values.get("message", "")).strip()
            if not message or len(message) > 4000:
                raise EditorError("Write a message up to 4,000 characters.")
            with SessionLocal() as db:
                site, user_id = access(db, session, request.path_params["site_id"])
                page = page_for(db, site, str(values.get("page_id", "")))
                blocks = (page.draft_json or {}).get("blocks", [{}])
                fallback_block_id = next(
                    (block.get("id") for block in blocks if block.get("type") == "hero"),
                    blocks[0].get("id"),
                )
            if not settings.xai_api_key and not (
                settings.environment.lower() != "production"
                and os.getenv("FASTSHOP_AGENT_FAKE") == "1"
            ):
                async def unconfigured():
                    for event in ({"type": "assistant_text", "text": "The AI provider is not configured. Add an xAI API key to enable the editor assistant."}, {"type": "done"}):
                        yield "data: " + json.dumps(event) + "\n\n"
                return StreamingResponse(unconfigured(), media_type="text/event-stream")
            from app import store_agent
            params = inspect.signature(store_agent.run_turn).parameters
            conversation_id = str(values.get("conversation_id") or "") or None
            kwargs = {"page_id": page.id, "selection": values.get("selection")}
            if "conversation_id" in params:
                kwargs["conversation_id"] = conversation_id
            transport = None
            if settings.environment.lower() != "production" and os.getenv("FASTSHOP_AGENT_FAKE") == "1":
                class ScriptedTransport:
                    def __init__(self):
                        self.sent = False
                    def complete(self, messages, tools):
                        if self.sent:
                            delay = float(os.getenv("FASTSHOP_AGENT_FAKE_DELAY", "0"))
                            if delay:
                                time.sleep(delay)
                            return {"content": "I updated the selected block for your preview."}
                        self.sent = True
                        block_id = (values.get("selection") or {}).get("block_id") or fallback_block_id
                        return {"tool_calls": [{"id": "fake-editor-update", "type": "function",
                            "function": {"name": "update_block", "arguments": json.dumps({
                                "page_id": page.id, "block_id": block_id,
                                "fields": {"heading": "Assistant preview update"}})}}]}
                transport = ScriptedTransport()
            if "transport" in params:
                kwargs["transport"] = transport
            events: queue.Queue = queue.Queue()
            def produce():
                try:
                    with SessionLocal() as db:
                        site_row = content.owned_site(db, request.path_params["site_id"], user_id)
                        for event in store_agent.run_turn(db, site_row, user_id, message, **kwargs):
                            events.put(event)
                except Exception as exc:
                    events.put({"type": "error", "message": str(exc)})
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

    rt.__self__.router.routes.append(Route(
        "/admin/sites/{site_id}/editor/agent", endpoint=agent_stream, methods=["POST"]))

    @rt("/admin/sites/{site_id}/editor/api/resources", methods=["GET"])
    def resources(session, site_id: str, q: str = ""):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                pages = content.site_pages(db, site)
                public = {p.path: p for p in pages if p.published_json}
                needle = q.strip().casefold()[:160]
                from app.site_widgets_pack3 import (
                    active_discount_codes,
                    scoped_collections,
                    scoped_products,
                )

                products = scoped_products(db, site)
                products = [p for p in products if not needle or needle in p.name.casefold()][:100]
                collections = scoped_collections(db, site)
                collections = [c for c in collections
                               if not needle or needle in c.title.casefold()][:100]
                collection_paths = {p.collection_id: p.path for p in public.values()
                                    if p.collection_id}
                return response({
                    "pages": [{"id": p.id, "title": p.title, "path": p.path, "kind": p.kind}
                              for p in pages if not needle or needle in p.title.casefold()][:100],
                    "products": [{"id": p.id, "title": p.name, "name": p.name,
                                  "path": "/products/" + p.slug, "image": p.image_url} for p in products],
                    "collections": [{"id": c.id, "title": c.title, "name": c.title,
                                     "path": collection_paths.get(c.id, ""),
                                     "source": "collection"} for c in collections],
                    "collection_label": "Collection",
                    "discount_codes": [{"id": code.id, "code": code.code}
                                       for code in active_discount_codes(db, site)
                                       if not needle or needle in code.code.casefold()],
                })
        except CommerceError as exc:
            return failure(exc)

    @rt("/admin/sites/{site_id}/editor/api/media", methods=["GET"])
    def media_get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                return response({"media": [_media(m, default_locale(site)) for m in media_for(db, site)]})
        except CommerceError as exc:
            return failure(exc)

    @rt("/admin/sites/{site_id}/editor/api/media", methods=["POST"])
    async def media_post(session, request, site_id: str):
        try:
            header_csrf(session, request)
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                form = await request.form()
                upload = form.get("file") or form.get("image")
                if not upload or not hasattr(upload, "read"):
                    raise EditorError("Select an image.")
                alt = str(form.get("alt", "")).strip()
                if not alt or len(alt) > 400:
                    raise EditorError("Provide useful alt text, up to 400 characters.")
                data = prepare_image_bytes(await upload.read(MAX_IMAGE_BYTES + 1))
                media = SiteMedia(tenant_id=site.tenant_id, site_id=site.id,
                    title=str(form.get("title", ""))[:200], alt=alt,
                    storage_key=new_id() + ".webp", content_type="image/webp", size=len(data), data=data)
                db.add(media)
                db.flush()
                result = _media(media, default_locale(site))
                db.commit()
                return response({"media": result}, 201)
        except (CommerceError, ValueError, TypeError) as exc:
            return failure(exc)

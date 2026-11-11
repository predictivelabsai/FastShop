"""Admin chat presentation and tenant scoped history for the store agent."""
import json

from fasthtml.common import (
    H2,
    Aside,
    Button,
    Div,
    Form,
    Header,
    NotStr,
    P,
    Textarea,
    to_xml,
)
from sqlalchemy import select
from starlette.responses import JSONResponse

from app.models import AgentAction, AgentConversation, AgentMessage, SitePage

SUGGESTIONS = (
    "Which products sold best this month?",
    "Lower the price of ...",
    "Create a 10% discount code",
    "Make the homepage hero shorter",
)


def home_page(db, site):
    return db.scalar(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
        SitePage.kind == "home").order_by(SitePage.path).limit(1)) or db.scalar(
            select(SitePage).where(SitePage.site_id == site.id,
                                   SitePage.tenant_id == site.tenant_id).order_by(SitePage.path).limit(1))


def _suggestions(disabled=False):
    return Div(*(Button(text, type="button", disabled=disabled, data_suggestion=text)
                 for text in SUGGESTIONS), id="agent-suggestions", cls="agent-suggestions")


def _chat_markup(compact=False):
    return Div(
        Div(id="agent-messages", cls="agent-messages agent-chat-shared", role="log", aria_live="polite"),
        _suggestions(),
        Form(Div(Textarea(name="message", rows="1", maxlength="4000",
            placeholder="Ask about your store or request a design change", required=True,
            aria_label="Message the assistant"), Button("↑", type="submit", aria_label="Send message",
            title="Send"), cls="agent-composer"),
            Div("Enter to send · Shift+Enter for a new line", cls="agent-compose-hint"),
            cls="agent-compose", data_agent_form=""),
        cls="agent-chat" + (" is-compact" if compact else ""))


def page(site, selected_page, csrf_token):
    configured = __import__("app.config", fromlist=["settings"]).settings.xai_api_key
    return (Div(Header(P("Ask questions about your store or make a draft change.",
                    cls="agent-intro"), cls="agent-heading"),
                Div(Div(Aside(H2("Conversations"), Div(id="agent-conversations"),
                              Button("＋ New chat", type="button", data_new_chat=""), cls="agent-history"),
                        Div(_chat_markup(), cls="agent-main"), cls="agent-layout") if configured else
                    Div(P("AI provider not configured", cls="agent-state-title"),
                        P("Add an xAI API key in the application environment to use the assistant."),
                        _suggestions(disabled=True),
                        cls="agent-unconfigured"), cls="agent-content"),
                data_agent_root="", data_site_id=site.id, data_page_id=selected_page.id if selected_page else "",
                data_csrf=csrf_token, data_stream=f"/admin/sites/{site.id}/editor/agent",
                data_history=f"/admin/sites/{site.id}/assistant/history",
                data_conversations=f"/admin/sites/{site.id}/assistant/conversations",
                data_restore=f"/admin/sites/{site.id}/editor/agent/restore",
                cls="agent-page"),)


def drawer(site_id, csrf_token):
    configured = bool(__import__("app.config", fromlist=["settings"]).settings.xai_api_key)
    chat = to_xml(_chat_markup(compact=True)) if configured else (
        '<div class="agent-unconfigured"><p class="agent-state-title">AI provider not configured</p>'
        '<p>Add an xAI API key in the application environment to use the assistant.</p></div>')
    return NotStr(f'''<div class="agent-launcher" data-agent-launcher data-agent-configured="{str(configured).lower()}" data-site-id="{site_id}" data-csrf="{csrf_token}" data-stream="/admin/sites/{site_id}/editor/agent" data-history="/admin/sites/{site_id}/assistant/history" data-conversations="/admin/sites/{site_id}/assistant/conversations" data-restore="/admin/sites/{site_id}/editor/agent/restore">
<button type="button" class="agent-launch" aria-expanded="false" aria-controls="agent-drawer">✦ Ask assistant</button>
<div class="agent-overlay" data-agent-overlay hidden><aside class="agent-drawer" id="agent-drawer" aria-label="Store assistant" aria-hidden="true" inert><header><div><h2>Assistant</h2><p>Ask about your store or request a draft change.</p></div><button type="button" data-agent-close aria-label="Close assistant">×</button></header>
<div class="agent-conversation-strip"><select aria-label="Recent conversations" data-agent-select><option value="">New chat</option></select><button type="button" data-new-chat>＋</button></div>
{chat}</aside></div></div>''')


def conversations(db, site, user_id):
    rows = db.scalars(select(AgentConversation).where(
        AgentConversation.tenant_id == site.tenant_id, AgentConversation.site_id == site.id,
        AgentConversation.user_id == user_id).order_by(AgentConversation.updated_at.desc()).limit(30))
    return JSONResponse([{"id": row.id, "title": row.title,
                          "updated_at": row.updated_at.isoformat()} for row in rows])


def history(db, site, user_id, conversation_id=""):
    query = select(AgentConversation).where(AgentConversation.tenant_id == site.tenant_id,
        AgentConversation.site_id == site.id, AgentConversation.user_id == user_id)
    if conversation_id:
        query = query.where(AgentConversation.id == conversation_id)
    else:
        query = query.order_by(AgentConversation.updated_at.desc()).limit(1)
    conversation = db.scalar(query)
    if not conversation:
        return JSONResponse({"conversation_id": None, "messages": [], "actions": []})
    rows = list(db.scalars(select(AgentMessage).where(
        AgentMessage.tenant_id == site.tenant_id, AgentMessage.site_id == site.id,
        AgentMessage.conversation_id == conversation.id).order_by(
            AgentMessage.created_at, AgentMessage.id).limit(120)))
    tool_results = {}
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
                tool_results.setdefault(name, []).append(bool(result.get("error")))

    def message_events(row):
        events = list(row.events_json or [])
        if events or row.role != "assistant":
            return events
        for call in row.tool_calls_json or []:
            name = call.get("name")
            if not name:
                continue
            failed = tool_results.get(name, []).pop(0) if tool_results.get(name) else False
            events.append({"name": name, "label": call.get("label", name),
                           "status": "error" if failed else "done"})
        return events

    actions = db.scalars(select(AgentAction).where(AgentAction.tenant_id == site.tenant_id,
        AgentAction.site_id == site.id, AgentAction.conversation_id == conversation.id,
        AgentAction.status == "pending").order_by(AgentAction.created_at))
    return JSONResponse({"conversation_id": conversation.id,
        "messages": [{"id": row.id, "role": row.role, "content": row.content,
                      "events": message_events(row),
                      "has_checkpoint": bool(row.checkpoint_revision_id)} for row in rows
                     if row.role in {"user", "assistant"}],
        "actions": [{"id": row.id, "kind": row.kind, "summary": row.summary} for row in actions]})

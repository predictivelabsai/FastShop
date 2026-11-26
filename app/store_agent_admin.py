"""Admin chat presentation and tenant scoped history for the store agent."""
import json

from fasthtml.common import (
    H2,
    A,
    Button,
    Details,
    Div,
    Form,
    Iframe,
    Input,
    NotStr,
    Option,
    P,
    Select,
    Span,
    Summary,
    Textarea,
    to_xml,
)
from sqlalchemy import select
from starlette.responses import JSONResponse

from app.admin_icons import lucide, svg
from app.assistant_provider import is_configured, settings_href
from app.models import AgentAction, AgentConversation, AgentMessage, AssistantTask, SitePage, User
from app.store_agent import conversation_title

_SEND_ICON = svg("arrow-up", 16, 2.25)

SUGGESTIONS = (
    "Which products sold best this month?",
    "Lower the price of ...",
    "Create a 10% discount code",
    "Make the homepage hero shorter",
)
SUGGESTION_ICONS = ("chart-column", "tag", "badge-percent", "layout-template")

CATEGORY_STARTERS = (
    ("read", "Read", "Get a read-only answer", "What should I know about my store right now?"),
    ("draft", "Draft", "Prepare a change for review", "Help me draft a change to my store for review."),
    ("confirm", "Confirm", "Changes wait for approval",
     "Which store changes need my approval before they happen?"),
    ("route", "Route", "Find the right admin area",
     "Which admin area should I use to update my store settings?"),
)

DESIGN_WORKFLOW_STARTERS = (
    ("layout", "Layout", "Draft a layout change, then preview it",
     "Help me draft a layout change for my store. Keep it private, prepare a preview, and leave "
     "the public-store publication decision to me as the merchant."),
    ("media", "Media", "Choose existing media for a draft preview",
     "Help me choose existing media for a page draft. Keep it private, prepare a preview, and leave "
     "the public-store publication decision to me as the merchant."),
    ("copy", "Copy", "Draft page copy, then preview it",
     "Help me improve the copy on a page draft. Keep it private, prepare a preview, and leave the "
     "public-store publication decision to me as the merchant."),
)


def editor_features(site, db=None):
    """Theme editor flags: Assistant mode is installed; whether this store has a model is per site.

    A library theme preview edits a detached snapshot, which the assistant never changes, so
    Assistant mode is offered only for the live store draft.
    """
    if getattr(site, "theme_context_id", ""):
        return {"assistant": False}
    return {"assistant": True, "assistant_configured": is_configured(site, db),
            "assistant_settings_href": settings_href(site.id)}


def home_page(db, site):
    return db.scalar(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
        SitePage.kind == "home").order_by(SitePage.path).limit(1)) or db.scalar(
            select(SitePage).where(SitePage.site_id == site.id,
                                   SitePage.tenant_id == site.tenant_id).order_by(SitePage.path).limit(1))


def site_pages(db, site):
    return list(db.scalars(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id).order_by(SitePage.path)))


def _starter_buttons(starters):
    return tuple(
        Button(Span(category, cls="agent-category-label"),
               Span(outcome, cls="agent-category-outcome"),
               type="button", id=f"agent-category-{key}", data_agent_starter=prompt,
               cls="agent-category-starter")
        for key, category, outcome, prompt in starters
    )


def _category_starters():
    return Div(Details(
        Summary("More ideas", cls="agent-capabilities-toggle"),
        Div(*_starter_buttons(CATEGORY_STARTERS), cls="agent-category-grid"),
        Div(
            Div(Span("Design workflow", cls="agent-design-workflow-label"),
                P("The assistant prepares a private draft. Preview it first. A merchant, not the "
                  "assistant, decides whether to publish it to the public storefront.",
                  cls="agent-design-workflow-copy"), cls="agent-design-workflow-intro"),
            Div(*_starter_buttons(DESIGN_WORKFLOW_STARTERS), cls="agent-category-grid"),
            cls="agent-design-workflow",
        ), cls="agent-capabilities"),
        id="agent-category-starters", cls="agent-category-starters", role="group",
        aria_label="Assistant task categories and design workflow",
    )


def _draft_review(site_id="", page_id=""):
    if not site_id or not page_id:
        return None
    preview_url = f"/admin/sites/{site_id}/preview/{page_id}"
    return Details(
        Summary("Review this page's private draft"),
        Div(
            P("Check the draft at desktop and mobile sizes. This never publishes the page.",
              cls="agent-draft-review-copy"),
            Div(
                Button("Desktop", type="button", data_agent_preview_viewport="desktop",
                       aria_pressed="true"),
                Button("Mobile", type="button", data_agent_preview_viewport="mobile",
                       aria_pressed="false"),
                A("Open preview ↗", href=preview_url, target="_blank", rel="noopener"),
                cls="agent-draft-review-tools",
            ),
            Div(Iframe(src=preview_url, title="Private draft preview", loading="lazy",
                       data_agent_preview_frame=""), cls="agent-draft-preview-frame"),
            P("Publish remains available only in the page editor for the merchant to decide.",
              cls="agent-draft-review-boundary"),
            cls="agent-draft-review-body",
        ),
        data_agent_draft_review="", cls="agent-draft-review",
    )


def _task_queue():
    return Div(
        Div(H2("Task queue"), Span("Private", cls="agent-task-privacy"), cls="agent-task-heading"),
        P("Assistant findings become assigned work here. They never change the storefront.",
          cls="agent-task-copy"),
        Div("No assistant tasks yet.", data_agent_tasks="", cls="agent-task-list"),
        cls="agent-task-queue", aria_label="Private assistant task queue",
    )


def _chat_markup(compact=False, site_id="", page_id=""):
    """Lovable-style chat: a welcome with suggestion cards, the conversation, and one composer card."""
    return Div(
        Div(id="agent-messages", cls="agent-messages agent-chat-shared", role="log", aria_live="polite"),
        Div(Span(lucide("sparkles", 20), cls="agent-welcome-icon"),
            H2("What would you like to do?", cls="agent-welcome-title"),
            P("Ask about your store or describe a change. Changes stay in a private draft until you publish.",
              cls="agent-welcome-copy"),
            Div(*(Button(lucide(icon, 16), Span(text), type="button", data_suggestion=text)
                  for text, icon in zip(SUGGESTIONS, SUGGESTION_ICONS, strict=True)), cls="agent-suggestion-grid"),
            id="agent-suggestions", cls="agent-suggestions agent-welcome"),
        _category_starters(),
        Form(Div(
            Div(Span("Private sources", cls="agent-source-heading"),
                Div("No document sources attached yet.", data_agent_sources="", cls="agent-source-list"),
                cls="agent-sources", aria_label="Private assistant document sources", hidden=True),
            Div(P("Describe an image for accessibility", cls="agent-attachment-label"),
                Input(name="attachment_alt", maxlength="400", aria_label="Image description for accessibility"),
                P("Images are saved privately in this store's media library. Documents are extracted as private assistant reference text. The assistant can use either only in a draft.",
                  cls="agent-attachment-note"),
                data_agent_attachment_details="", hidden=True, cls="agent-attachment-details"),
            P("", role="status", aria_live="polite", data_agent_attachment_status="",
              cls="agent-attachment-status", hidden=True),
            Div(Textarea(name="message", rows="1", maxlength="4000",
                         placeholder="Ask about your store or describe a change",
                         aria_label="Message the assistant"),
                Div(Button(lucide("paperclip", 16), Span("Attach file", cls="a-sr-only"), type="button",
                           data_agent_attachment_picker="", title="Attach an image or document",
                           cls="agent-attachment-control"),
                    Input(type="file", name="attachment",
                          accept="image/jpeg,image/png,image/webp,.pdf,.docx,.xlsx,.csv,.txt,.md",
                          data_agent_attachment="", data_drop_ready="true", hidden=True),
                    Span("Enter to send, Shift and Enter for a new line", cls="agent-compose-hint"),
                    Button(NotStr(_SEND_ICON), type="submit", aria_label="Send message", title="Send",
                           cls="agent-send"),
                    cls="agent-composer-bar"),
                cls="agent-composer-row agent-composer"),
            _draft_review(site_id, page_id),
            cls="agent-compose", data_agent_form=""),
        ),
        cls="agent-chat" + (" is-compact" if compact else ""))


UNCONFIGURED_TITLE = "Connect an AI model to use the assistant"
UNCONFIGURED_COPY = ("Add your own API key from xAI, OpenAI, OpenRouter or another OpenAI-compatible "
                     "provider. The key is encrypted and used only for this store.")


def unconfigured(site_id):
    """The assistant's state for a store without a model connection."""
    return Div(Span(lucide("sparkles", 20), cls="agent-welcome-icon"),
               P(UNCONFIGURED_TITLE, cls="agent-state-title"),
               P(UNCONFIGURED_COPY),
               A("Connect a model", href=settings_href(site_id), cls="a-button agent-connect-model",
                 data_agent_connect=""),
               cls="agent-unconfigured", data_agent_unconfigured="")


_ICONS = {"history": "history", "plus": "square-pen", "desktop": "monitor", "mobile": "smartphone",
          "refresh": "refresh-cw", "external": "external-link"}


def _icon(name):
    return lucide(_ICONS[name], 16)


def _preview_pane(site, selected_page, pages):
    """Lovable-style live preview of the private draft beside the chat."""
    if not selected_page:
        return Div(P("Add a page to your store to preview drafts here.", cls="agent-state-title"),
                   cls="agent-studio-preview agent-studio-preview-empty")
    preview_url = f"/admin/sites/{site.id}/preview/{selected_page.id}"
    choices = [row for row in pages if getattr(row, "id", None)] or [selected_page]
    picker = Select(*[Option(getattr(row, "title", "") or getattr(row, "path", "") or "Page",
                             value=row.id, selected=row.id == selected_page.id) for row in choices],
                    aria_label="Page to preview", data_agent_page_select="",
                    cls="agent-studio-pick") if len(choices) > 1 else Span(
        getattr(selected_page, "title", "") or "Home page", cls="agent-studio-pick")
    return Div(
        Div(
            Div(picker, Span("Private draft", cls="agent-studio-badge"), cls="agent-studio-toolbar-start"),
            Div(
                Div(Button(_icon("desktop"), Span("Desktop", cls="a-sr-only"), type="button",
                           data_agent_preview_viewport="desktop", aria_pressed="true", title="Desktop"),
                    Button(_icon("mobile"), Span("Mobile", cls="a-sr-only"), type="button",
                           data_agent_preview_viewport="mobile", aria_pressed="false", title="Mobile"),
                    cls="agent-studio-segmented", role="group", aria_label="Preview size"),
                Button(_icon("refresh"), Span("Refresh preview", cls="a-sr-only"), type="button",
                       data_agent_preview_refresh="", title="Refresh preview", cls="agent-studio-icon"),
                A(_icon("external"), Span("Open preview", cls="a-sr-only"), href=preview_url,
                  target="_blank", rel="noopener", data_agent_preview_open="", title="Open preview",
                  cls="agent-studio-icon"),
                A("Open in editor", href=f"/admin/sites/{site.id}/editor", cls="a-button"),
                cls="agent-studio-toolbar-end"),
            cls="agent-studio-toolbar"),
        Div(Iframe(src=preview_url, title="Private draft preview", data_agent_preview_frame=""),
            cls="agent-draft-preview-frame agent-studio-frame"),
        P("Publish remains available only in the page editor for the merchant to decide.",
          cls="agent-draft-review-boundary agent-studio-boundary"),
        data_agent_draft_review="", cls="agent-draft-review agent-studio-preview",
    )


def page(site, selected_page, csrf_token, pages=(), configured=False):
    history = Details(
        Summary(_icon("history"), Span("History"), cls="a-button"),
        Div(H2("Conversations"), Div(id="agent-conversations"),
            Button(_icon("plus"), Span("New chat"), type="button", data_new_chat=""),
            _task_queue(), cls="agent-history"),
        cls="agent-studio-history")
    chat = (Div(_chat_markup(site_id=site.id), cls="agent-main") if configured else
            unconfigured(site.id))
    switch = Div(
        Button("Chat", type="button", data_agent_pane="chat", aria_pressed="true"),
        Button("Preview", type="button", data_agent_pane="preview", aria_pressed="false"),
        cls="agent-studio-switch", role="group", aria_label="Show chat or preview")
    return (Div(
        switch,
        Div(Div(Div(Span("Chat", cls="agent-studio-title"),
                    Div(history, Button(_icon("plus"), Span("New chat", cls="a-sr-only"), type="button",
                                        data_new_chat="", title="New chat", cls="agent-head-icon"),
                        cls="agent-studio-head-actions") if configured else None,
                    cls="agent-studio-chat-head"),
                chat, cls="agent-studio-chat"),
            _preview_pane(site, selected_page, pages), cls="agent-studio"),
        data_agent_root="", data_agent_configured=str(bool(configured)).lower(), data_site_id=site.id, data_page_id=selected_page.id if selected_page else "",
        data_csrf=csrf_token, data_stream=f"/admin/sites/{site.id}/editor/agent",
        data_history=f"/admin/sites/{site.id}/assistant/history",
        data_conversations=f"/admin/sites/{site.id}/assistant/conversations",
        data_tasks=f"/admin/sites/{site.id}/assistant/tasks",
        data_restore=f"/admin/sites/{site.id}/editor/agent/restore",
        cls="agent-page agent-studio-page"),)


def drawer(site_id, csrf_token, configured=False):
    chat = to_xml(_chat_markup(compact=True) if configured else unconfigured(site_id))
    sparkle, close, new = svg("sparkles", 16), svg("x", 18), svg("square-pen", 16)
    # Without a model there is no conversation to pick or start, only the way to connect one.
    history = (f'<select aria-label="Recent conversations" data-agent-select class="agent-history-select">'
               f'<option value="">New chat</option></select><button type="button" data-new-chat '
               f'aria-label="New chat" title="New chat" class="agent-head-icon">{new}</button>'
               if configured else "")
    return NotStr(f'''<div class="agent-launcher" data-agent-launcher data-agent-configured="{str(configured).lower()}" data-site-id="{site_id}" data-csrf="{csrf_token}" data-stream="/admin/sites/{site_id}/editor/agent" data-history="/admin/sites/{site_id}/assistant/history" data-conversations="/admin/sites/{site_id}/assistant/conversations" data-restore="/admin/sites/{site_id}/editor/agent/restore">
<button type="button" class="agent-launch" aria-expanded="false" aria-controls="agent-drawer">{sparkle}Ask assistant</button>
<div class="agent-overlay" data-agent-overlay hidden><aside class="agent-drawer" id="agent-drawer" aria-label="Store assistant" aria-hidden="true" inert><header class="agent-drawer-head"><h2>Assistant</h2>{history}<button type="button" data-agent-close aria-label="Close assistant" title="Close" class="agent-head-icon">{close}</button></header>
{chat}</aside></div></div>''')


def conversations(db, site, user_id):
    rows = db.scalars(select(AgentConversation).where(
        AgentConversation.tenant_id == site.tenant_id, AgentConversation.site_id == site.id,
        AgentConversation.user_id == user_id).order_by(AgentConversation.updated_at.desc()).limit(30))
    return JSONResponse([{"id": row.id, "title": conversation_title(row.title),
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
        "actions": [{"id": row.id, "kind": row.kind, "summary": row.summary,
                     "review": (row.payload_json or {}).get("review", {"changes": []})}
                    for row in actions]})


def tasks(db, site):
    rows = db.scalars(select(AssistantTask).where(
        AssistantTask.tenant_id == site.tenant_id,
        AssistantTask.site_id == site.id,
    ).order_by(
        AssistantTask.status == "done",
        AssistantTask.due_date.is_(None),
        AssistantTask.due_date,
        AssistantTask.created_at.desc(),
    ).limit(30)).all()
    assignee_ids = {row.assigned_user_id for row in rows if row.assigned_user_id}
    assignees = ({row.id: row for row in db.scalars(select(User).where(User.id.in_(assignee_ids)))}
                 if assignee_ids else {})
    return JSONResponse({"tasks": [{
        "id": row.id,
        "title": row.title,
        "priority": row.priority,
        "status": row.status,
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "assignee": assignees[row.assigned_user_id].name if row.assigned_user_id in assignees else "",
    } for row in rows]})

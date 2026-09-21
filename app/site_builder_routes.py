"""Classical design controls and chat workspace over one persisted draft."""

import re
from uuid import uuid4

from fasthtml.common import (
    H2,
    A,
    Button,
    Details,
    Div,
    Form,
    Iframe,
    Input,
    Label,
    Link,
    Option,
    P,
    Script,
    Select,
    Small,
    Summary,
    Textarea,
)
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app import content
from app import site_builder_services as builder
from app.config import settings
from app.db import SessionLocal
from app.integrations.site_builder_llm import next_question, respond
from app.models import Membership, SiteBuilderTurn, SiteChangeSet
from app.services import CommerceError, money
from app.site_theme import CHOICES, DEFAULTS, PRESETS, validate_theme


def register_builder_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/build", methods=["GET"])
    def get(session, site_id: str, page: str = "", view: str = "chat", notice: str = ""):
        try:
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id)
                pages = content.site_pages(db, site)
                selected = content.site_page(db, site, page) if page else pages[0]
                turns = list(db.scalars(select(SiteBuilderTurn).where(SiteBuilderTurn.site_id == site.id,
                    SiteBuilderTurn.tenant_id == site.tenant_id).order_by(SiteBuilderTurn.created_at.desc(), SiteBuilderTurn.id.desc()).limit(20)))
                changes = list(db.scalars(select(SiteChangeSet).where(SiteChangeSet.site_id == site.id,
                    SiteChangeSet.tenant_id == site.tenant_id).order_by(SiteChangeSet.after_json["version"].as_integer().desc()).limit(50)))
                change = changes[0] if changes else None
                from app.site_samples import pending_reviews
                pending = pending_reviews(site.settings_json)
                try:
                    theme = validate_theme(site.settings_json.get("design", {}))
                except CommerceError:
                    theme = DEFAULTS
                base = f"/admin/sites/{site.id}"
                can_review = bool(db.scalar(select(Membership.id).where(Membership.tenant_id == site.tenant_id,
                    Membership.user_id == user_id, Membership.role.in_(["admin", "merchant"]))))

                def review_card(turn):
                    response = turn.response_json
                    proposals = response.get("proposals", [])
                    if not proposals:
                        return None
                    details = []
                    previous = response.get("review_context", {})
                    for proposal in proposals:
                        if proposal["kind"] == "merchant":
                            from app.site_samples import FIELDS
                            for key, value in proposal["values"].items():
                                old = previous.get("merchant", {}).get(key)
                                if key.endswith("_minor"):
                                    details.append(P(FIELDS[key] + ": " + (money(old, "USD") if type(old) is int else "Unset") + " → " + money(value, "USD")))
                                else:
                                    details.append(P(FIELDS[key] + ": " + str(old or "Unset") + " → " + value))
                        elif proposal["kind"] == "price":
                            item = next((item for item in previous.get("catalog", []) if item["variant_id"] == proposal["variant_id"]), {})
                            details.append(P(item.get("product", "Product option") + " · " + item.get("variant", "") + ": " +
                                (money(item["price_minor"], "USD") if type(item.get("price_minor")) is int else "Unset") + " → " + money(proposal["price_minor"], "USD")))
                        else:
                            details.append(P("Create product: " + proposal["name"] + " · /products/" + proposal["slug"]))
                            details.extend(P(item["name"] + ": " + money(item["price_minor"], "USD")) for item in proposal["variants"])
                    pending = response.get("review_status") == "pending"
                    return Div(H2("Review merchant changes"), *details,
                        P("Catalog approval changes prices immediately. Merchant details remain draft; shipping settings are operational. This does not publish pages, enable payments or approve tax registrations."),
                        Small("Status: " + response.get("review_status", "unavailable")),
                        Form(csrf(session), Input(type="hidden", name="turn_id", value=turn.id),
                            Input(type="hidden", name="decision", value="approve"), Input(type="hidden", name="page_id", value=selected.id),
                            Label(Input(type="checkbox", name="confirmed", required=True), " I reviewed every proposed change"),
                            Button("Approve merchant changes", cls="e-button"), action=base + "/build/review", method="post") if pending and can_review else None,
                        Form(csrf(session), Input(type="hidden", name="turn_id", value=turn.id), Input(type="hidden", name="decision", value="reject"),
                            Input(type="hidden", name="page_id", value=selected.id), Button("Reject proposal"), action=base + "/build/review", method="post") if pending and can_review else None,
                        P("A merchant or administrator must review this proposal.") if pending and not can_review else None, cls="e-card b-review")

                def hidden():
                    return (csrf(session), Input(type="hidden", name="version", value=site.version), Input(type="hidden", name="page_id", value=selected.id))
                design = Form(*hidden(), H2("Design settings"),
                    *[Label(k.title(), Input(name=k, type="color", value=theme[k])) for k in ("accent", "background", "surface", "text")],
                    *[Label(k.title(), Select(*[Option(v.title(), value=v, selected=theme[k] == v) for v in values], name=k)) for k, values in CHOICES.items()],
                    Button("Save design", cls="e-button"), method="post", action=base + "/build/design", cls="e-form")
                chat = Div(H2("Build together"),
                    P("AI assistant" if settings.xai_api_key else "Guided presets · AI provider not configured", cls="b-mode"),
                    P(next_question(site.settings_json), cls="b-next-question"),
                    Small("Guided commands: Business:, Audience:, Pages:, Tone:, Headline:, Shipping: (USD cents)."),
                    Div(*[P(key.replace("_", " ").title() + ": " + value) for key, value in site.settings_json.get("builder_brief", {}).items()], cls="b-brief"),
                    Div(*[Button(name.title(), type="button", data_prompt=name) for name in PRESETS], cls="b-presets"),
                    Div(*[Div(P(turn.prompt, cls="b-user"), P(turn.response_json.get("answer", "Request interrupted or still processing. Your saved draft is safe.")),
                        P(turn.response_json.get("question", "")), Small(turn.response_json.get("summary", turn.status)),
                        review_card(turn),
                        Form(csrf(session), Input(type="hidden", name="command_id", value=turn.command_id),
                            Input(type="hidden", name="page_id", value=selected.id), Button("Cancel pending request"),
                            method="post", action=base + "/build/cancel") if turn.status == "pending" and turn.user_id == user_id else None,
                        cls="b-turn") for turn in reversed(turns)], cls="b-history", aria_live="polite"),
                    Form(*hidden(), Input(type="hidden", name="command_id", value=uuid4().hex),
                        Label("Target section (optional)", Select(Option("Whole page / design", value=""),
                            *[Option((s.get("heading") or s["type"])[:100], value=s["id"]) for s in selected.draft_json.get("sections", [])], name="section_id")),
                        Label("Describe your site or a change", Textarea(name="prompt", rows=4, required=True, maxlength=4000,
                            placeholder="Warm cream and green, editorial headings, less whitespace…")),
                        Small("Draft edits only. Do not enter passwords, API keys or customer information."),
                        Button("Update draft", cls="e-button"), P("", data_builder_status="", role="status"),
                        Button("Cancel pending edit", type="button", data_builder_cancel=base + "/build/cancel", hidden=True),
                        method="post", action=base + "/build/message", cls="e-form", data_builder_form="", data_site=site.id))
                return shell("Build " + site.name,
                    Link(rel="stylesheet", href="/static/site-workspace.css"), Script(src="/static/site-workspace.js", defer=True),
                    Div(A("Classical editor", href=base), A("Chat", href=base + "/build?page=" + selected.id),
                        A("Design controls", href=base + "/build?view=design&page=" + selected.id),
                        A("Products", href=base + "/products"), A("Commerce", href=base + "/commerce"), cls="e-actions"),
                    A("Merchant details & samples", href=base + "/samples"),
                    P("Merchant fields awaiting review: " + ", ".join(pending), cls="e-note") if pending else None,
                    A("Try commerce demo", href=base + "/demo", cls="e-button"),
                    P("Changes save to a private draft. Review and publish through the classical editor."),
                    P(notice[:200], role="status") if notice else None,
                    Div(Button("Editor", type="button", data_workspace_tab="editor"), Button("Preview", type="button", data_workspace_tab="preview"), cls="b-tabs"),
                    Div(Div(design if view == "design" else chat,
                        Form(*hidden(), Input(type="hidden", name="change_id", value=change.id),
                            Button("Undo latest change", cls="e-button"), Small(change.summary),
                            method="post", action=base + "/build/undo", cls="e-form") if change else None,
                        Details(Summary("Draft revision history"),
                            P("Latest 50 revisions. Undo is available only while that revision is still the current draft; it never rewinds commerce or publication."),
                            *[P(f"v{item.after_json['version']} · {item.source} · {item.summary}", Small(" · " + item.created_at.isoformat())) for item in changes], cls="b-revisions"),
                        cls="b-editor", data_workspace_panel="editor"),
                        Div(Form(Label("Preview page", Select(*[Option(p.title, value=p.id, selected=p.id == selected.id) for p in pages], name="page")),
                            Input(type="hidden", name="view", value=view), Button("Open page"), action=base + "/build", method="get", cls="b-toolbar"),
                            Div(Button("Desktop", type="button", data_preview_size="desktop"), Button("Mobile", type="button", data_preview_size="mobile"),
                                Button("Select section", type="button", data_select_section="", aria_pressed="false") if view != "design" else None,
                                A("Edit page fields", href=base + "/pages/" + selected.id), cls="b-toolbar"),
                            Iframe(src=base + "/preview/" + selected.id, title="Live draft preview", cls="b-preview"),
                            cls="b-canvas", data_workspace_panel="preview"), cls="b-workspace"))
        except CommerceError as exc:
            return error(exc)

    def redirect(site_id, page_id, notice="", view="chat"):
        from urllib.parse import urlencode
        return RedirectResponse(f"/admin/sites/{site_id}/build?" + urlencode({"page": page_id, "notice": notice, "view": view}), status_code=303)

    @rt("/admin/sites/{site_id}/build/design", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = builder.lock_site(db, site_id, user_id, int(form.get("version", 0)))
                theme = validate_theme({key: str(form.get(key, "")) for key in DEFAULTS})
                builder.apply_operations(db, site.id, user_id, builder.snapshot(db, site), [{"op": "theme", "values": theme}], "classical")
                db.commit()
            return redirect(site_id, str(form.get("page_id", "")), "Design saved", "design")
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/build/undo", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                builder.undo_change(db, site_id, actor(session), str(form.get("change_id", "")), int(form.get("version", 0)))
                db.commit()
            return redirect(site_id, "", "Draft change undone")
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/build/message", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        turn_id = None
        try:
            check_csrf(session, form)
            user_id = actor(session)
            prompt = str(form.get("prompt", ""))
            if re.search(r"(?:sk_(?:test|live)_|whsec_|sk-[a-zA-Z0-9]{12})", prompt):
                raise CommerceError("Do not put provider secrets in chat. Use your secure configuration.")
            with SessionLocal() as db:
                turn, fresh = builder.begin_turn(db, site_id, user_id, str(form.get("command_id", "")), prompt,
                    str(form.get("page_id", "")), int(form.get("version", 0)), str(form.get("section_id", "")))
                turn_id, context = turn.id, turn.context_json
                previous = list(db.scalars(select(SiteBuilderTurn).where(SiteBuilderTurn.site_id == site_id,
                    SiteBuilderTurn.tenant_id == turn.tenant_id, SiteBuilderTurn.status == "complete")
                    .order_by(SiteBuilderTurn.created_at.desc()).limit(6)))
                history = [item for row in reversed(previous) for item in (
                    {"role": "user", "content": row.prompt},
                    {"role": "assistant", "content": row.response_json.get("answer", "") + " " + row.response_json.get("question", "")})]
                db.commit()
            if fresh:
                response, provider = await run_in_threadpool(respond, prompt, context, history)
                with SessionLocal() as db:
                    builder.finish_turn(db, site_id, user_id, turn_id, response, provider)
                    db.commit()
            return redirect(site_id, context["page_id"])
        except (CommerceError, ValueError, TypeError) as exc:
            if turn_id:
                with SessionLocal() as db:
                    builder.end_pending_turn(db, site_id, user_id, str(form.get("command_id", "")), failed=True)
                    db.commit()
            return error(exc)

    @rt("/admin/sites/{site_id}/build/cancel", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                builder.end_pending_turn(db, site_id, actor(session), str(form.get("command_id", "")))
                db.commit()
            return redirect(site_id, str(form.get("page_id", "")), "Pending edit cancelled; the provider may still finish processing, but its result cannot change this draft.")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/build/review", methods=["POST"])
    async def post(session, request, site_id: str):
        from app.site_builder_reviews import decide
        form = await request.form()
        try:
            check_csrf(session, form)
            if form.get("decision") not in {"approve", "reject"}:
                raise CommerceError("Choose approve or reject.")
            approve = form.get("decision") == "approve"
            if approve and form.get("confirmed") != "on":
                raise CommerceError("Explicitly confirm that you reviewed every proposed change.")
            with SessionLocal() as db:
                result = decide(db, site_id, actor(session), str(form.get("turn_id", "")), approve=approve)
                db.commit()
            return redirect(site_id, str(form.get("page_id", "")), "Merchant proposal " + result)
        except CommerceError as exc:
            return error(exc)

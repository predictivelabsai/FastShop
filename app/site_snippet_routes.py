"""Merchant controls for trusted site head and footer snippets."""

from urllib.parse import urlencode

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, P, Small, Textarea
from starlette.responses import RedirectResponse

from app import content, site_snippets
from app import site_builder_services as builder
from app.db import SessionLocal
from app.services import CommerceError
from app.site_blocks import default_locale, merge_localized, resolve_text

_LABELS = {
    "head": "Document head",
    "pre-footer": "Before footer",
    "foot": "After footer",
}


def register_snippet_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/snippets", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                locale = default_locale(site)
                saved = {snippet.placement: snippet for snippet in site_snippets.snippets_for(db, site)}
                cards = []
                for placement in site_snippets.PLACEMENTS:
                    snippet = saved.get(placement)
                    snapshot = snippet.draft_json if snippet else {"content": "", "enabled": False, "note": ""}
                    published = bool(snippet and snippet.published_json is not None)
                    cards.append(Div(
                        H2(_LABELS[placement]),
                        P({
                            "head": "Metadata, verification tags, styles, and consent-gated integrations.",
                            "pre-footer": "Visible markup immediately before the storefront footer.",
                            "foot": "Markup after the footer, near the end of the storefront document.",
                        }[placement]),
                        Small("Published snapshot available." if published else "Not published."),
                        Form(
                            csrf(session),
                            Input(type="hidden", name="version", value=site.version),
                            Label(f"Internal note ({locale}, optional)", Input(
                                name="note",
                                value=resolve_text(snapshot.get("note", ""), locale),
                                maxlength=site_snippets.MAX_NOTE,
                            )),
                            Label("Snippet markup", Textarea(
                                snapshot.get("content", ""),
                                name="content",
                                rows=10,
                                maxlength=site_snippets.MAX_CONTENT,
                                spellcheck="false",
                            )),
                            Label(Input(type="checkbox", name="enabled", checked=snapshot.get("enabled", False)),
                                  " Enable this placement"),
                            Div(
                                Button("Save draft", name="action", value="draft", cls="e-button"),
                                Button("Publish snippet", name="action", value="publish", cls="e-button"),
                                cls="e-actions",
                            ),
                            method="post",
                            action=f"/admin/sites/{site.id}/snippets/{placement}",
                            cls="e-form",
                        ),
                        cls="e-card",
                    ))
                return shell(
                    "Snippets — " + site.name,
                    Div(A("Back to site", href=f"/admin/sites/{site.id}"),
                        A("Builder", href=f"/admin/sites/{site.id}/build"), cls="e-actions"),
                    P("Add merchant-trusted integration markup. Snippets are global, not translated. Only the optional note uses the site's default locale."),
                    P("Executable scripts stay inert until the matching analytics or marketing consent is granted. Snippets never run in draft previews, preview-status sites, checkout, payment, cart, or account pages.", cls="e-note"),
                    P("URLs in snippet attributes must use HTTPS. Mailto is allowed only for links. Structural document tags, inline event handlers, javascript URLs, and iframe srcdoc are rejected."),
                    P(notice, role="status", cls="e-note") if notice else None,
                    *cards,
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/snippets/{placement}", methods=["POST"])
    async def post(session, request, site_id: str, placement: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            action = str(form.get("action", "draft"))
            if action not in {"draft", "publish"}:
                raise CommerceError("Choose save draft or publish snippet.")
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                try:
                    version = int(form.get("version", -1))
                except (TypeError, ValueError):
                    raise CommerceError("Reload snippets before saving.") from None
                site = builder.lock_site(db, site.id, user_id, version)
                existing = site_snippets.get_snippet(db, site, placement)
                previous_note = existing.draft_json.get("note", "") if existing else ""
                snapshot = {
                    "content": str(form.get("content", "")),
                    "enabled": form.get("enabled") == "on",
                    "note": merge_localized(previous_note, str(form.get("note", "")), default_locale(site)),
                }
                site_snippets.put_snippet(db, site, placement, snapshot, publish=action == "publish")
                site.version += 1
                db.commit()
            notice = "Snippet published." if action == "publish" else "Snippet draft saved."
        except CommerceError as exc:
            notice = str(exc)
        return RedirectResponse(
            f"/admin/sites/{site_id}/snippets?" + urlencode({"notice": notice}),
            status_code=303,
        )

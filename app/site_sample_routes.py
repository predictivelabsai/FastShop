"""Manual replacement and review of explicitly synthetic merchant details."""

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, P, Small
from starlette.responses import RedirectResponse

from app import commerce, content, site_builder_services, site_samples
from app.db import SessionLocal
from app.services import CommerceError


def register_sample_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/samples", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session), publish=True)
                config = commerce.settings_for(db, site, create=True)
                db.commit()
                values = site_samples.values_for(site, config)
                provenance = site.settings_json.get("sample_fields", {})
                return shell("Merchant details", A("← Site", href=f"/admin/sites/{site.id}"),
                    P("Synthetic values are examples, not approved merchant details or tax advice. Replace them manually and explicitly review each field. No credentials are generated."),
                    Form(csrf(session), Input(type="hidden", name="version", value=site.version),
                        Button("Fill missing fields with sample data", cls="e-button"), method="post", action=f"/admin/sites/{site.id}/samples/seed", cls="e-form"),
                    H2("Replace and review"),
                    Form(csrf(session), Input(type="hidden", name="version", value=site.version), Input(type="hidden", name="config_version", value=config.version),
                        *[Div(Label(label, Input(name=key, value=values[key] if values[key] is not None else "", maxlength=300)),
                            Small(provenance.get(key, {}).get("source", "unrecorded").replace("_", " ").title() + (" · Merchant reviewed" if provenance.get(key, {}).get("reviewed") else " · Awaiting review")),
                            Label(Input(type="checkbox", name="reviewed", value=key, checked=provenance.get(key, {}).get("reviewed", False)), " I have reviewed this value"), cls="e-card") for key, label in site_samples.FIELDS.items()],
                        P("Saving does not enable commerce, approve tax registrations or publish your site."),
                        Button("Save merchant details", cls="e-button"), method="post", cls="e-form"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/samples/seed", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = site_builder_services.lock_site(db, site_id, user_id, int(form.get("version", 0)))
                site_samples.seed_samples(db, site, user_id)
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/samples", status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/samples", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = site_builder_services.lock_site(db, site_id, user_id, int(form.get("version", 0)))
                site_samples.save_samples(db, site, user_id, form, set(form.getlist("reviewed")), int(form.get("config_version", 0)))
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/samples", status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)

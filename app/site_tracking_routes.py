"""Merchant tracking settings with the site's existing draft and publish flow."""

import copy

from fasthtml.common import H2, H3, A, Button, Form, Input, Label, P
from starlette.responses import RedirectResponse

from app import content
from app import site_builder_services as builder
from app.db import SessionLocal
from app.services import CommerceError
from app.site_tracking import TRACKING_FIELDS, tracking_ids, validate_tracking_ids


def register_site_tracking_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/marketing/tracking", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                values = tracking_ids(db, site, site.settings_json or {})
                migrated = "tracking" not in (site.settings_json or {})
                fields = (
                    ("ga4_measurement_id", "GA4 measurement ID", "G-ABC1234567", 22),
                    ("google_ads_conversion_id", "Google Ads conversion ID", "AW-1234567890", 23),
                    ("google_ads_purchase_label", "Google Ads purchase conversion label", "AbCdEfGhIjKlMnOp", 100),
                    ("meta_pixel_id", "Meta pixel ID", "123456789012345", 30),
                    ("tiktok_pixel_id", "TikTok pixel ID", "C1234567890123456789", 31),
                )
                return shell(
                    "Tracking and pixels",
                    A("← Marketing", href=f"/admin/sites/{site.id}/marketing"),
                    P(notice[:300], role="status") if notice else None,
                    Form(
                        csrf(session),
                        Input(type="hidden", name="version", value=site.version),
                        H2("Provider IDs"),
                        P("Tags load only after the matching visitor consent: analytics for GA4, and marketing for Google Ads, Meta and TikTok."),
                        P("Existing GA4, Google Analytics and Meta IDs are prefilled. Save them here to move them into the unified tracking settings.", cls="e-note") if migrated else None,
                        *[
                            Label(
                                label,
                                Input(
                                    name=field,
                                    value=values[field],
                                    placeholder=placeholder,
                                    maxlength=length,
                                    autocomplete="off",
                                    spellcheck="false",
                                ),
                            )
                            for field, label, placeholder, length in fields
                        ],
                        H3("Purchase conversion"),
                        P("Google Ads sends purchase conversions when both its conversion ID and purchase label are set. No customer contact details are sent."),
                        Button("Save draft", name="action", value="draft", cls="e-button"),
                        Button("Publish settings", name="action", value="publish", cls="e-button"),
                        method="post",
                        action=f"/admin/sites/{site.id}/marketing/tracking",
                        cls="e-form",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/tracking", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            action = str(form.get("action", "draft"))
            if action not in {"draft", "publish"}:
                raise CommerceError("Choose save draft or publish settings.")
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=action == "publish")
                if str(site.version) != str(form.get("version")):
                    raise CommerceError("Settings changed in another window. Reload before saving.")
                site = builder.lock_site(db, site.id, user_id, site.version)
                before = builder.snapshot(db, site)
                values = validate_tracking_ids({field: form.get(field, "") for field in TRACKING_FIELDS})
                config = copy.deepcopy(site.settings_json or {})
                config.pop("ga4_measurement_id", None)
                config["tracking"] = values
                site.settings_json = config
                site.version += 1
                if action == "publish":
                    site.published_settings_json = copy.deepcopy(config)
                db.flush()
                builder.record_change(db, site, user_id, before, "classical", "Tracking settings")
                db.commit()
            return RedirectResponse(
                f"/admin/sites/{site_id}/marketing/tracking?notice=Tracking+settings+saved",
                status_code=303,
            )
        except CommerceError as exc:
            return error(exc)

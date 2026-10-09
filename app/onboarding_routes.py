"""Merchant routes for resumable first-site onboarding."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    A,
    Button,
    Details,
    Div,
    Fieldset,
    Form,
    Input,
    Label,
    Legend,
    P,
    Small,
    Summary,
    Textarea,
)
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response

from app import onboarding, site_generation
from app.db import SessionLocal
from app.models import OnboardingState, Product, Site, SitePage
from app.services import CommerceError


class OnboardingAccessDenied(CommerceError):
    """Non-leaking wizard authorization failure."""


def _wizard_url(site_id: str, notice: str = "") -> str:
    url = f"/admin/onboarding/{site_id}"
    return url + ("?" + urlencode({"notice": notice}) if notice else "")


def _site_url(site_id: str, notice: str) -> str:
    return f"/admin/sites/{site_id}?" + urlencode({"notice": notice})


def _access_denied() -> Response:
    return Response(
        "Site not found or access denied.", status_code=403, media_type="text/plain"
    )


def _mark_failed(state_id: str, token: str, code: str) -> None:
    with SessionLocal() as db:
        state = db.scalar(
            select(OnboardingState)
            .where(
                OnboardingState.id == state_id,
                OnboardingState.generation_token == token,
                OnboardingState.status == "generating",
            )
            .with_for_update()
        )
        if state:
            state.status = "failed"
            state.failure_code = code
            state.generation_token = ""
            db.commit()


def _brief_form(state, site, csrf, session):
    return Form(
        csrf(session),
        Label(
            "Describe your business",
            Textarea(
                state.business_description,
                name="business_description",
                rows=5,
                required=True,
                maxlength=onboarding.BUSINESS_DESCRIPTION_MAX_LENGTH,
                placeholder=(
                    "A neighborhood studio helping busy families create calm, "
                    "functional homes."
                ),
            ),
        ),
        Small(
            f"What makes {site.name} useful and who it serves. "
            f"Up to {onboarding.BUSINESS_DESCRIPTION_MAX_LENGTH} characters."
        ),
        Label(
            "What do you sell? (optional)",
            Textarea(
                state.product_context,
                name="product_context",
                rows=3,
                maxlength=onboarding.PRODUCT_CONTEXT_MAX_LENGTH,
                placeholder="Handmade lighting, small furniture and design consultations",
            ),
        ),
        Small(
            "Products, services or memberships are all fine. Leave this blank if you are "
            "still deciding."
        ),
        Fieldset(
            Legend("Choose a look and feel"),
            *[
                Label(
                    Input(
                        type="radio",
                        name="design_direction",
                        value=direction,
                        checked=(state.design_direction or onboarding.DESIGN_DIRECTIONS[0])
                        == direction,
                        required=True,
                    ),
                    direction,
                    cls="n-choice",
                )
                for direction in onboarding.DESIGN_DIRECTIONS
            ],
            cls="n-direction-grid",
        ),
        Div(
            Button("Save and continue", cls="e-button"),
            A("Leave setup for now", href=f"/admin/sites/{site.id}"),
            cls="e-actions",
        ),
        method="post",
        action=f"/admin/onboarding/{site.id}/brief",
        cls="e-form n-brief-form",
    )


def _decision(state, site, csrf, session):
    return Div(
        Div(
            H2("Generate a tailored draft"),
            P(
                "FastShop will replace only the untouched starter structure with a "
                "validated draft: pages, navigation, design, copy and product seeds when "
                "relevant. It stays private until you publish it."
            ),
            Form(
                csrf(session),
                Button("Generate with AI", cls="e-button", data_generation_submit=""),
                P(
                    "",
                    role="status",
                    aria_live="polite",
                    data_generation_status="",
                    hidden=True,
                ),
                P(
                    "Generation can take about a minute. When no AI provider is configured, "
                    "the same validated flow uses guided presets.",
                    cls="n-support",
                ),
                method="post",
                action=f"/admin/onboarding/{site.id}/generate",
                data_generation_form="",
            ),
            cls="n-decision-panel n-decision-primary",
        ),
        Div(
            H2("Keep the clean template"),
            P(
                "Continue with the eight starter pages and shape everything manually in "
                "the builder. You can still use the builder assistant later."
            ),
            Form(
                csrf(session),
                Button("Skip — start with the clean template", cls="n-secondary-button"),
                method="post",
                action=f"/admin/onboarding/{site.id}/skip",
            ),
            cls="n-decision-panel",
        ),
        cls="n-decision-grid",
    )


def register_onboarding_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/onboarding/{site_id}", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                try:
                    state, site = onboarding.owned_state(db, site_id, user_id)
                except CommerceError as exc:
                    raise OnboardingAccessDenied from exc
                if state.status in onboarding.TERMINAL_STATUSES:
                    return RedirectResponse(f"/admin/sites/{site.id}", status_code=303)
                step = "2 of 2" if state.status in {"ready", "generating", "failed"} else "1 of 2"
                if state.status == "generating":
                    body = Div(
                        H2("Your draft is being assembled"),
                        P(
                            "Pages, navigation, design and starter products are passing "
                            "through the same checks as builder edits. Keep this tab open; "
                            "another submit will not start a duplicate run."
                        ),
                        cls="e-card n-state-card",
                    )
                elif state.status == "failed":
                    body = Div(
                        Div(
                            H2("Something went wrong"),
                            P(
                                "Your clean template and saved brief are safe. Try generation "
                                "again, revise the brief, or continue in the builder."
                            ),
                            cls="n-failure-copy",
                            role="alert",
                        ),
                        _decision(state, site, csrf, session),
                        H2("Revise your brief"),
                        _brief_form(state, site, csrf, session),
                        cls="n-failure-state",
                    )
                elif state.status == "ready":
                    body = Div(
                        Div(
                            H2("Your brief is ready"),
                            P(state.business_description),
                            P(
                                "What you sell: " + (state.product_context or "Not specified"),
                                cls="n-support",
                            ),
                            P("Direction: " + state.design_direction, cls="n-support"),
                            cls="e-card n-brief-summary",
                        ),
                        _decision(state, site, csrf, session),
                        Details(
                            Summary("Edit your brief"),
                            Div(
                                _brief_form(state, site, csrf, session),
                                cls="n-edit-brief",
                            ),
                            cls="n-edit-disclosure",
                        ),
                    )
                else:
                    body = Div(
                        H2("Tell us what you are building"),
                        P(
                            "Three short answers are enough. They guide the first draft; "
                            "everything remains editable in the builder."
                        ),
                        _brief_form(state, site, csrf, session),
                    )
                return shell(
                    "Set up " + site.name,
                    Div(
                        P("Onboarding · " + step, cls="n-progress", aria_live="polite"),
                        P(
                            "A private first draft, shaped from your business rather than a "
                            "generic demo.",
                            cls="n-intro",
                        ),
                        P(notice[:300], role="status", cls="e-note") if notice else None,
                        body,
                        cls="n-wizard",
                    ),
                )
        except OnboardingAccessDenied:
            return _access_denied()
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/onboarding/{site_id}/brief", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
        except CommerceError as exc:
            return error(exc)
        try:
            user_id = actor(session)
            values = onboarding.validate_brief_parts(
                str(form.get("business_description", "")),
                str(form.get("product_context", "")),
                str(form.get("design_direction", "")),
            )
            with SessionLocal() as db:
                try:
                    state, site = onboarding.owned_state(
                        db, site_id, user_id, for_update=True
                    )
                except CommerceError as exc:
                    raise OnboardingAccessDenied from exc
                if state.status in onboarding.TERMINAL_STATUSES:
                    return RedirectResponse(f"/admin/sites/{site.id}", status_code=303)
                if state.status == "generating" and not onboarding.generation_is_stale(state):
                    return RedirectResponse(
                        _wizard_url(site.id, "Generation is already in progress."),
                        status_code=303,
                    )
                (
                    state.business_description,
                    state.product_context,
                    state.design_direction,
                ) = values
                state.status = "ready"
                state.failure_code = ""
                state.generation_token = ""
                state.generation_started_at = None
                db.commit()
            return RedirectResponse(_wizard_url(site_id), status_code=303)
        except OnboardingAccessDenied:
            return _access_denied()
        except CommerceError as exc:
            return RedirectResponse(_wizard_url(site_id, str(exc)[:300]), status_code=303)

    @rt("/admin/onboarding/{site_id}/skip", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                try:
                    state, site = onboarding.owned_state(
                        db, site_id, user_id, for_update=True
                    )
                except CommerceError as exc:
                    raise OnboardingAccessDenied from exc
                if state.status == "complete":
                    return RedirectResponse(f"/admin/sites/{site.id}", status_code=303)
                if state.status == "generating" and not onboarding.generation_is_stale(state):
                    return RedirectResponse(
                        _wizard_url(site.id, "Generation is already in progress."),
                        status_code=303,
                    )
                state.status = "skipped"
                state.failure_code = ""
                state.generation_token = ""
                state.completed_at = datetime.now(UTC)
                page_count = db.scalar(
                    select(func.count(SitePage.id)).where(
                        SitePage.site_id == site.id,
                        SitePage.tenant_id == site.tenant_id,
                    )
                )
                state.result_json = {
                    "pages_kept": page_count,
                    "generation_calls": 0,
                }
                db.commit()
            return RedirectResponse(
                _site_url(
                    site_id,
                    f"Clean template kept: {page_count} starter pages, with no generated content.",
                ),
                status_code=303,
            )
        except OnboardingAccessDenied:
            return _access_denied()
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/onboarding/{site_id}/generate", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                try:
                    state, site = onboarding.owned_state(
                        db, site_id, user_id, for_update=True
                    )
                except CommerceError as exc:
                    raise OnboardingAccessDenied from exc
                if state.status == "complete":
                    return RedirectResponse(f"/admin/sites/{site.id}", status_code=303)
                if state.status == "skipped":
                    return RedirectResponse(f"/admin/sites/{site.id}", status_code=303)
                if state.status == "generating" and not onboarding.generation_is_stale(state):
                    return RedirectResponse(
                        _wizard_url(site.id, "Generation is already in progress."),
                        status_code=303,
                    )
                if state.status not in {"ready", "failed", "generating"}:
                    return RedirectResponse(
                        _wizard_url(site.id, "Save your brief before generating a draft."),
                        status_code=303,
                    )
                brief = onboarding.merchant_brief(state, site)
                token = secrets.token_urlsafe(24)
                fingerprint = onboarding.site_fingerprint(db, site)
                state.status = "generating"
                state.generation_token = token
                state.generation_key = onboarding.brief_generation_key(state, site)
                state.site_fingerprint = fingerprint
                state.generation_started_at = datetime.now(UTC)
                state.failure_code = ""
                state_id = state.id
                db.commit()
        except OnboardingAccessDenied:
            return _access_denied()
        except CommerceError as exc:
            return error(exc)

        try:
            plan, source = await run_in_threadpool(site_generation.generate_plan, brief)
        except Exception:
            _mark_failed(state_id, token, "provider_unavailable")
            return RedirectResponse(
                _wizard_url(
                    site_id,
                    "Something went wrong. Your brief and clean template are safe.",
                ),
                status_code=303,
            )

        try:
            with SessionLocal() as db:
                try:
                    state, site = onboarding.owned_state(
                        db, site_id, user_id, for_update=True
                    )
                except CommerceError as exc:
                    raise OnboardingAccessDenied from exc
                if state.status == "complete":
                    return RedirectResponse(f"/admin/sites/{site.id}", status_code=303)
                if state.status != "generating" or state.generation_token != token:
                    return RedirectResponse(
                        _wizard_url(site.id, "A newer generation attempt is in progress."),
                        status_code=303,
                    )
                site = db.scalar(
                    select(Site)
                    .where(Site.id == site.id, Site.tenant_id == site.tenant_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if onboarding.site_fingerprint(db, site) != state.site_fingerprint:
                    raise CommerceError(
                        "The site changed while generation was running; its edits were preserved."
                    )
                site_generation.apply_plan(
                    db,
                    site,
                    user_id,
                    plan,
                    expected_version=site.version,
                    key=state.generation_key,
                    source=source,
                    brief=brief,
                )
                page_count = db.scalar(
                    select(func.count(SitePage.id)).where(
                        SitePage.site_id == site.id,
                        SitePage.tenant_id == site.tenant_id,
                    )
                )
                product_count = db.scalar(
                    select(func.count(Product.id)).where(Product.tenant_id == site.tenant_id)
                )
                state.status = "complete"
                state.generation_source = source
                state.generation_token = ""
                state.failure_code = ""
                state.completed_at = datetime.now(UTC)
                state.result_json = {
                    "pages_generated": page_count,
                    "products_seeded": product_count,
                    "source": source,
                }
                db.commit()
            mode = "guided presets" if source == "guided" else "AI"
            return RedirectResponse(
                _site_url(
                    site_id,
                    f"Draft created with {mode}: {page_count} pages and "
                    f"{product_count} product seeds. Review it before publishing.",
                ),
                status_code=303,
            )
        except OnboardingAccessDenied:
            _mark_failed(state_id, token, "access_changed")
            return _access_denied()
        except Exception:
            _mark_failed(state_id, token, "apply_failed")
            return RedirectResponse(
                _wizard_url(
                    site_id,
                    "Something went wrong. Your brief and existing site edits are safe.",
                ),
                status_code=303,
            )

"""Merchant-triggered, operator-configured site connector workflows."""

from __future__ import annotations

from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    H3,
    A,
    Button,
    Details,
    Div,
    Form,
    Input,
    Label,
    Li,
    P,
    Small,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
    Ul,
)
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse, Response

from app import connectors, content
from app.db import SessionLocal
from app.services import CommerceError


def _redirect(site_id, notice, *, plan_id=""):
    query = {"notice": str(notice)[:300]}
    if plan_id:
        query["plan"] = plan_id
    return RedirectResponse(
        f"/admin/sites/{site_id}/integrations?" + urlencode(query), status_code=303
    )


def _count_table(report):
    rows = []
    counts = report.get("counts", {})
    preferred = (
        "categories", "tags", "authors", "media", "posts", "pages",
        "collections", "products", "customers", "orders",
    )
    names = [name for name in preferred if name in counts]
    names.extend(name for name in counts if name not in names)
    for name in names:
        values = counts.get(name, {})
        rows.append(Tr(Th(name.title(), scope="row"), Td(str(values.get("fetched", 0))),
            Td(str(values.get("create", 0))), Td(str(values.get("update", 0))),
            Td(str(values.get("skip", 0)))))
    return Div(Table(Thead(Tr(Th("Resource"), Th("Fetched"), Th("Create"),
        Th("Update"), Th("Skip"))), Tbody(*rows)), cls="table-scroll i-counts")


def _plan_view(plan, csrf):
    report = plan.report_json
    provider_label = connectors.connector_for(plan.platform).label
    warnings = report.get("warnings", [])
    unmapped = report.get("unmapped_items", [])
    samples = report.get("samples", [])
    status = plan.status.title()
    return Div(
        Div(H2("Reviewed import plan"), Span(status, cls="i-state i-state-" + plan.status),
            cls="i-heading"),
        P("This is the exact stored snapshot that will be applied. It expires after 30 minutes and can be consumed once."),
        _count_table(report),
        H3("Sample changes"),
        Ul(*[Li(Span(item.get("action", "").title(), cls="i-action"), " ",
            item.get("type", "item"), " · ", item.get("label", ""),
            Small(" External #" + item.get("external_id", ""))) for item in samples],
            cls="i-list") if samples else P("No importable rows were returned.", cls="i-empty"),
        Details(Summary(f"Warnings and unmapped items ({len(warnings) + len(unmapped)})"),
            Ul(*[Li(message) for message in warnings],
               *[Li(f"{item.get('type', 'item')} #{item.get('external_id', '')}: {item.get('reason', '')}")
                 for item in unmapped], cls="i-list")) if warnings or unmapped else
            P("No mapping warnings in this preview.", cls="i-success"),
        Form(csrf, Input(type="hidden", name="plan_id", value=plan.id),
            Input(type="hidden", name="plan_version", value=plan.version),
            Label(Input(type="checkbox", name="confirmed", required=True),
                " I reviewed these counts, samples, and warnings."),
            Button("Apply this reviewed plan", cls="e-button",
                   disabled=plan.status != "pending"),
            P(f"Applying creates or updates only mapped data in this site. It does not contact "
              f"{provider_label} again.",
              cls="i-help"),
            method="post", action=f"/admin/sites/{plan.site_id}/integrations/{plan.platform}/apply",
            cls="e-form i-confirm") if plan.status == "pending" else None,
        cls="e-card i-plan",
    )


def register_integration_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/integrations", methods=["GET"])
    def get(session, site_id: str, notice: str = "", plan: str = ""):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                rows = []
                for connector in connectors.listed_connectors():
                    state = connector.credential_state(site)
                    capabilities = connector.capabilities
                    export_label = ", ".join(capabilities.exports) if capabilities.exports else "not included"
                    export_controls = None
                    if capabilities.exports and connector.platform == "wordpress":
                        export_controls = Div(
                            A("Download published WXR XML", cls="i-export",
                              href=f"/admin/sites/{site.id}/integrations/{connector.platform}/export"),
                            A("Download WXR XML with drafts", cls="i-export",
                              href=f"/admin/sites/{site.id}/integrations/{connector.platform}/export?include_drafts=1"),
                            P("Choose whether the portable bundle contains only published snapshots or also unpublished drafts.",
                              cls="i-help"),
                            cls="e-actions",
                        )
                    elif capabilities.exports:
                        export_controls = A(
                            "Download review-only export JSON",
                            cls="i-export",
                            href=f"/admin/sites/{site.id}/integrations/{connector.platform}/export",
                        )
                    rows.append(Div(
                        Div(H2(connector.label), Span("Ready" if state.configured else "Needs operator setup",
                            cls="i-state " + ("i-state-ready" if state.configured else "i-state-missing")),
                            cls="i-heading"),
                        P(state.message, role="status"),
                        P("Import: " + ", ".join(capabilities.imports) + ". Export: " +
                          export_label + "."),
                        P(f"Bounded to {capabilities.max_pages} pages per resource, "
                          f"{capabilities.max_items} items, and {capabilities.timeout_seconds}-second requests.",
                          cls="i-help"),
                        Form(csrf(session), Button("Run import dry run", cls="e-button",
                            disabled=not state.configured), method="post",
                            action=f"/admin/sites/{site.id}/integrations/{connector.platform}/dry-run",
                            cls="e-form") if state.configured else None,
                        export_controls,
                        cls="e-card i-connector"))
                selected = None
                if plan:
                    selected = connectors.pending_plan(db, site, plan, user_id=user_id)
                elif (plans := connectors.recent_plans(db, site, limit=1)):
                    selected = plans[0]
                history = connectors.recent_plans(db, site)
                return shell("Integrations",
                    Div(A("← Back to site", href=f"/admin/sites/{site.id}"), cls="e-actions"),
                    P("Migrations are sandbox-first: preview provider data before any FastShop write. "
                      "Connection details are controlled by the operator and are never entered here.",
                      cls="i-intro"),
                    P(notice, role="status", cls="e-note") if notice else None,
                    Div(*rows, cls="i-connectors"),
                    _plan_view(selected, csrf(session)) if selected else None,
                    Details(Summary("Recent import plans"),
                        Ul(*[Li(A(f"{row.platform.title()} · {row.status} · plan {row.id[:8]}",
                            href=f"/admin/sites/{site.id}/integrations?plan={row.id}")) for row in history],
                           cls="i-list")) if history else None)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/integrations/{platform}/dry-run", methods=["POST"])
    async def post(session, request, site_id: str, platform: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)

            def create():
                with SessionLocal() as db:
                    site = content.owned_site(db, site_id, user_id, publish=True)
                    planned = connectors.create_dry_run(db, site, user_id, platform)
                    db.commit()
                    return planned.id

            plan_id = await run_in_threadpool(create)
            return _redirect(site_id, "Dry run complete. Review the stored plan before applying it.", plan_id=plan_id)
        except CommerceError as exc:
            return _redirect(site_id, exc)

    @rt("/admin/sites/{site_id}/integrations/{platform}/apply", methods=["POST"])
    async def post(session, request, site_id: str, platform: str):
        form = await request.form()
        plan_id = str(form.get("plan_id", ""))
        try:
            check_csrf(session, form)
            if form.get("confirmed") != "on":
                raise CommerceError("Confirm that you reviewed the import plan.")
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                reviewed = connectors.pending_plan(db, site, plan_id, user_id=user_id)
                if reviewed.platform != platform:
                    raise CommerceError("Reviewed import plan does not match this connector.")
                _, result = connectors.apply_reviewed_plan(
                    db, site, user_id, plan_id, int(form.get("plan_version", 0))
                )
                db.commit()
            total = sum(values["created"] + values["updated"] for values in result.values())
            return _redirect(site_id, f"Applied reviewed plan successfully: {total} resources processed.", plan_id=plan_id)
        except (CommerceError, ValueError) as exc:
            return _redirect(site_id, exc, plan_id=plan_id)

    @rt("/admin/sites/{site_id}/integrations/{platform}/export.json", methods=["GET"])
    def get(session, site_id: str, platform: str):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                bundle = connectors.export_bundle(db, site, platform)
            response = JSONResponse(bundle)
            response.headers["Content-Disposition"] = (
                f'attachment; filename="fastshop-{site_id}-{platform}-export.json"'
            )
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/integrations/{platform}/export", methods=["GET"])
    def get(session, site_id: str, platform: str, include_drafts: str = "0"):
        try:
            if include_drafts not in {"0", "1"}:
                raise CommerceError("Choose a supported export content scope.")
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                artifact = connectors.export_artifact(
                    db, site, platform, include_drafts=include_drafts == "1"
                )
            response = Response(artifact.content, media_type=artifact.media_type)
            response.headers["Content-Disposition"] = (
                f'attachment; filename="{artifact.filename}"'
            )
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except CommerceError as exc:
            return error(exc)

"""Merchant-triggered, operator-configured site connector workflows."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
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
    Option,
    P,
    Select,
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
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, RedirectResponse, Response

from app import connectors, content, ui
from app.admin_ui import empty_state
from app.db import SessionLocal
from app.models import SiteCommerceSettings
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


def _row_status_table(report):
    rows = report.get("rows", [])
    if not rows:
        return None
    return Details(Summary(f"Per-row status ({len(rows)})"), Div(Table(
        Thead(Tr(Th("CSV row"), Th("Status"), Th("Name"), Th("Details"))),
        Tbody(*[Tr(Th(str(row.get("row_number", "")), scope="row"),
            Td(str(row.get("status", "")).title()), Td(row.get("name", "") or "—"),
            Td("; ".join(row.get("errors", [])) or row.get("identity", "")))
            for row in rows])), cls="table-scroll"))


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
        _row_status_table(report),
        H3("Sample changes"),
        Ul(*[Li(Span(item.get("action", "").title(), cls="i-action"), " ",
            item.get("type", "item"), " · ", item.get("label", ""),
            Small(" External #" + item.get("external_id", ""))) for item in samples],
            cls="i-list") if samples else empty_state("No importable records",
                "The preview found no records to import. Check the source data and run another preview.",
                A("Review connections", href="#integration-connections", cls="e-button")),
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
    from app.models import Membership, User

    def managed_site(db, site_id, user_id):
        site = content.owned_site(db, site_id, user_id)
        role = db.scalar(select(Membership.role).where(
            Membership.tenant_id == site.tenant_id, Membership.user_id == user_id,
        ))
        if role not in {"admin", "merchant"}:
            raise CommerceError("A store admin or merchant membership is required.")
        return site

    @rt("/admin/sites/{site_id}/settings/integrations", methods=["GET"])
    def get(session, site_id: str):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = managed_site(db, site_id, user_id)
                base = f"/admin/sites/{site.id}/settings/integrations"
                return shell("Integrations", *ui.integrations_hub(db, base=base, in_store=True))
        except CommerceError as exc:
            return error(exc)

    def integration_page(site, provider, credential, notice="", csrf_field=None):
        is_paypal = provider == "paypal"
        title = "PayPal" if is_paypal else "Email"
        path = f"/admin/sites/{site.id}/settings/integrations/{provider}"
        connected = bool(credential and credential.status == "connected")
        status = "Connected" if connected else "Not connected"
        details = []
        if credential:
            hints = credential.masked_hints_json or {}
            if is_paypal:
                details.append(P("Client ID: " + hints.get("client_id", "Connected")))
                details.append(P("Mode: " + credential.mode.title()))
            else:
                details.append(P("Sender: " + hints.get("sender", "")))
                details.append(P("Sender name: " + hints.get("sender_name", "")))
                details.append(P("Sender signature or domain: "
                                 + hints.get("sender_confirmation", "unverified")))
            if credential.verified_at:
                details.append(P("Last verified: " + credential.verified_at.strftime("%Y-%m-%d %H:%M UTC")))
        if is_paypal:
            fields = [
                Label("Client ID", Input(name="client_id", autocomplete="off", required=True)),
                Label("Client secret", Input(name="secret", type="password", autocomplete="new-password",
                    required=True)),
                Label("Environment", Select(Option("Sandbox", value="sandbox",
                    selected=not credential or credential.mode != "live"),
                    Option("Live", value="live", selected=bool(credential and credential.mode == "live")),
                    name="mode")),
            ]
        else:
            fields = [
                Label("Postmark server token", Input(name="token", type="password",
                    autocomplete="new-password", required=True)),
                Label("Postmark account token (optional, for sender checks)", Input(
                    name="account_token", type="password", autocomplete="new-password")),
                Label("Sender address", Input(name="sender", type="email", required=True,
                    value=(credential.masked_hints_json.get("sender", "") if credential else ""))),
                Label("Sender name", Input(name="sender_name", autocomplete="organization-title",
                    value=(credential.masked_hints_json.get("sender_name", "") if credential else ""))),
            ]
        return shell("Settings · Integrations · " + title,
            Div(A("← Integrations", href=f"/admin/sites/{site.id}/settings/integrations"), cls="e-actions"),
            P(notice, role="status", cls="e-note") if notice else None,
            Div(H2(title), P("Connection status: " + status, role="status"), *details,
                cls="e-card i-connection-status"),
            Div(H2("Connection details"),
                P("Credentials are encrypted when stored and never shown again. Replace a connection by saving new details."),
                Form(csrf_field, *fields, Button("Verify and save connection", cls="e-button"),
                     method="post", action=path + "/save", cls="e-form i-connection-form"),
                P("Operator environment variables remain a fallback when no store connection is saved."),
                cls="e-card i-connection-form-card"),
            Div(Form(csrf_field, Button("Test connection", cls="e-button secondary"), method="post",
                     action=path + "/test", cls="e-form") if connected else None,
                Form(csrf_field, Button("Send test email to my address", cls="e-button secondary"),
                     method="post", action=path + "/send-test", cls="e-form")
                if connected and not is_paypal else None,
                Form(csrf_field, Button("Disconnect", cls="e-button danger"), method="post",
                     action=path + "/disconnect", cls="e-form") if connected else None,
                cls="e-actions"),
            P("Choosing live credentials does not enable live payments. Live payment approval remains controlled by Commerce settings and the platform operator.")
            if is_paypal else P("Transactional order mail, customer login codes and contact messages use this connection first."),
        )

    def connection_redirect(site_id, provider, notice):
        return RedirectResponse(
            f"/admin/sites/{site_id}/settings/integrations/{provider}?"
            + urlencode({"notice": str(notice)[:250]}), status_code=303,
        )

    def register_provider(provider):
        @rt(f"/admin/sites/{{site_id}}/settings/integrations/{provider}", methods=["GET"])
        def get(session, site_id: str, notice: str = ""):
            try:
                user_id = actor(session)
                with SessionLocal() as db:
                    site = managed_site(db, site_id, user_id)
                    from app.site_integrations import credential_row

                    credential = credential_row(db, site, provider)
                    return integration_page(site, provider, credential, notice, csrf(session))
            except CommerceError as exc:
                return error(exc)

        @rt(f"/admin/sites/{{site_id}}/settings/integrations/{provider}/save", methods=["POST"])
        async def post(session, request, site_id: str):
            form = await request.form()
            try:
                check_csrf(session, form)
                user_id = actor(session)
                from app import site_integrations

                with SessionLocal() as db:
                    site = managed_site(db, site_id, user_id)
                    if provider == "paypal":
                        client_id = str(form.get("client_id", "")).strip()
                        secret = str(form.get("secret", ""))
                        mode = str(form.get("mode", "sandbox")).strip().lower()
                        if not client_id or not secret or mode not in {"sandbox", "live"}:
                            raise CommerceError("Enter a client ID, client secret and valid environment.")
                        if mode == "live":
                            from app import commerce

                            try:
                                payment_mode = commerce.payment_mode(db, site)
                            except CommerceError as exc:
                                raise CommerceError("Live credentials require operator-approved live payments in Commerce settings.") from exc
                            if payment_mode != "live":
                                raise CommerceError("Live credentials require operator-approved live payments in Commerce settings.")
                        if not site_integrations.verify_paypal(client_id, secret, mode):
                            raise CommerceError("PayPal could not verify these credentials. Check the environment and try again.")
                        site_integrations.save(db, site, "paypal",
                            {"client_id": client_id, "secret": secret, "mode": mode}, mode,
                            {"client_id": site_integrations.mask(client_id)})
                    else:
                        token = str(form.get("token", "")).strip()
                        sender = str(form.get("sender", "")).strip()
                        sender_name = " ".join(str(form.get("sender_name", "")).split())[:160]
                        if not token or not sender or "\n" in sender or "\r" in sender:
                            raise CommerceError("Enter a Postmark server token and sender address.")
                        account_token = str(form.get("account_token", "")).strip()
                        verified, confirmation = site_integrations.verify_postmark(
                            token, sender, account_token=account_token)
                        if not verified:
                            raise CommerceError("Postmark could not verify the server or account token. Check the credentials and try again.")
                        site_integrations.save(db, site, "email",
                            {"token": token, "account_token": account_token,
                             "sender": sender, "sender_name": sender_name}, "default",
                            {"sender": sender, "sender_name": sender_name,
                             "sender_confirmation": confirmation})
                    db.commit()
                return connection_redirect(site_id, provider, "Connection verified and saved.")
            except (CommerceError, ValueError) as exc:
                return connection_redirect(site_id, provider, exc)

        @rt(f"/admin/sites/{{site_id}}/settings/integrations/{provider}/test", methods=["POST"])
        async def post(session, request, site_id: str):
            form = await request.form()
            try:
                check_csrf(session, form)
                user_id = actor(session)
                from app import site_integrations

                with SessionLocal() as db:
                    site = managed_site(db, site_id, user_id)
                    payload = site_integrations.payload_for(site, provider, db)
                    if not payload:
                        raise CommerceError("Connect this service before testing it.")
                    if provider == "paypal":
                        ok = site_integrations.verify_paypal(payload["client_id"], payload["secret"], payload["mode"])
                        confirmation = ""
                    else:
                        ok, confirmation = site_integrations.verify_postmark(
                            payload["token"], payload["sender"],
                            account_token=payload.get("account_token", ""))
                    if not ok:
                        raise CommerceError("The connection test failed. Check the provider account and try again.")
                    from datetime import UTC, datetime

                    credential = site_integrations.credential_row(db, site, provider)
                    credential.verified_at = datetime.now(UTC)
                    if provider == "email":
                        credential.masked_hints_json = {
                            **(credential.masked_hints_json or {}),
                            "sender_confirmation": confirmation,
                        }
                    db.commit()
                return connection_redirect(site_id, provider, "Connection test passed.")
            except (CommerceError, ValueError) as exc:
                return connection_redirect(site_id, provider, exc)

        @rt(f"/admin/sites/{{site_id}}/settings/integrations/{provider}/disconnect", methods=["POST"])
        async def post(session, request, site_id: str):
            form = await request.form()
            try:
                check_csrf(session, form)
                user_id = actor(session)
                from app.site_integrations import disconnect

                with SessionLocal() as db:
                    site = managed_site(db, site_id, user_id)
                    disconnect(db, site, provider)
                    db.commit()
                return connection_redirect(site_id, provider, "Connection disconnected.")
            except CommerceError as exc:
                return connection_redirect(site_id, provider, exc)

    register_provider("paypal")
    register_provider("email")

    @rt("/admin/sites/{site_id}/settings/integrations/email/send-test", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            from app.site_integrations import send_test_email

            with SessionLocal() as db:
                site = managed_site(db, site_id, user_id)
                user = db.get(User, user_id)
                if not user or not user.email:
                    raise CommerceError("Your account does not have an email address for the test.")
                if not send_test_email(site, user.email, db):
                    raise CommerceError("Postmark did not accept the test email.")
                db.commit()
            return connection_redirect(site_id, "email", "Test email sent to your signed-in address.")
        except (CommerceError, ValueError, httpx.HTTPError) as exc:
            return connection_redirect(site_id, "email", exc)

    @rt("/admin/sites/{site_id}/integrations", methods=["GET"])
    def get(session, site_id: str, notice: str = "", plan: str = ""):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                commerce_config = db.scalar(select(SiteCommerceSettings).where(
                    SiteCommerceSettings.site_id == site.id,
                    SiteCommerceSettings.tenant_id == site.tenant_id,
                ))
                live_approved = bool(
                    commerce_config and commerce_config.mode == "live"
                    and commerce_config.live_accepted_at and commerce_config.live_accepted_by
                    and commerce_config.live_credential_id
                )
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
                    elif capabilities.exports and connector.platform == "csv":
                        export_controls = Div(
                            A("Download Merchant Center XML feed", cls="i-export",
                              href=f"/admin/sites/{site.id}/integrations/{connector.platform}/export"),
                            A("Download feed validation report", cls="i-export",
                              href=f"/admin/sites/{site.id}/integrations/{connector.platform}/export?review_report=1"),
                            P("The feed contains only valid published site products. The report lists every candidate and any missing required attributes.",
                              cls="i-help"), cls="e-actions")
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
                        P("CSV uploads: 2 MB, 750 rows, 32 columns, 20,000 characters per cell."
                          if connector.platform == "csv" else
                          f"Bounded to {capabilities.max_pages} pages per resource, "
                          f"{capabilities.max_items} items, and {capabilities.timeout_seconds}-second requests.",
                          cls="i-help"),
                        Form(csrf(session),
                            Label("Catalog CSV", Input(type="file", name="catalog_file",
                                accept=".csv,text/csv", required=True)),
                            P("Required: name, category, and exactly one price column. Optional: sku, description, currency, stock, variant_name, variant_options, and image_url(s).",
                              cls="i-help"), Button("Preview CSV import", cls="e-button"),
                            method="post", enctype="multipart/form-data",
                            action=f"/admin/sites/{site.id}/integrations/{connector.platform}/dry-run",
                            cls="e-form") if state.configured and connector.platform == "csv" else
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
                    Div(H2("Payments"), P("Connect Stripe from the store admin, manage test and live keys, and check Stripe Tax."),
                        A("Manage Stripe payments", href=f"/admin/sites/{site.id}/commerce/payments", cls="e-button"),
                        cls="e-card"),
                    P("Live payments: " + ("approved by operator" if live_approved else "not approved"),
                      cls="g-state g-state-live" if live_approved else "g-state"),
                    P("Migrations are sandbox-first: preview provider data before any FastShop write. "
                      "Connection details are controlled by the operator and are never entered here.",
                      cls="i-intro"),
                    P(notice, role="status", cls="e-note") if notice else None,
                    Div(*rows, cls="i-connectors", id="integration-connections"),
                    _plan_view(selected, csrf(session)) if selected else None,
                    Details(Summary("Recent import plans"),
                        Ul(*[Li(A(f"{row.platform.title()} · {row.status} · plan {row.id[:8]}",
                            href=f"/admin/sites/{site.id}/integrations?plan={row.id}")) for row in history],
                           cls="i-list")) if history else empty_state("No import plans yet",
                        "Preview an import from one of your connections to review changes before applying them.",
                        A("Review connections", href="#integration-connections", cls="e-button")))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/integrations/{platform}/dry-run", methods=["POST"])
    async def post(session, request, site_id: str, platform: str):
        try:
            form = await request.form(max_files=1, max_fields=10,
                                      max_part_size=2 * 1024 * 1024 + 1)
            check_csrf(session, form)
            user_id = actor(session)
            source = None
            if platform == "csv":
                upload = form.get("catalog_file")
                if upload is None or not hasattr(upload, "read"):
                    raise CommerceError("Choose a UTF-8 CSV file to preview.")
                source = await upload.read(2 * 1024 * 1024 + 1)
                await upload.close()
                if len(source) > 2 * 1024 * 1024:
                    raise CommerceError("CSV uploads are limited to 2097152 bytes.")

            def create():
                with SessionLocal() as db:
                    site = content.owned_site(db, site_id, user_id, publish=True)
                    planned = connectors.create_dry_run(db, site, user_id, platform,
                                                        transport=source)
                    db.commit()
                    return planned.id

            plan_id = await run_in_threadpool(create)
            return _redirect(site_id, "Dry run complete. Review the stored plan before applying it.", plan_id=plan_id)
        except (CommerceError, HTTPException) as exc:
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
    def get(session, site_id: str, platform: str, include_drafts: str = "0",
            review_report: str = "0"):
        try:
            if include_drafts not in {"0", "1"} or review_report not in {"0", "1"}:
                raise CommerceError("Choose a supported export content scope.")
            if include_drafts == "1" and review_report == "1":
                raise CommerceError("Choose one export content scope.")
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=True)
                artifact = (connectors.export_review_artifact(db, site, platform)
                    if review_report == "1" else connectors.export_artifact(
                        db, site, platform, include_drafts=include_drafts == "1"))
            response = Response(artifact.content, media_type=artifact.media_type)
            response.headers["Content-Disposition"] = (
                f'attachment; filename="{artifact.filename}"'
            )
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except CommerceError as exc:
            return error(exc)

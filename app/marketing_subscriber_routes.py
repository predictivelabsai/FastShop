"""Storefront unsubscribe and merchant subscriber management routes."""

from __future__ import annotations

import math
from urllib.parse import urlencode

from fasthtml.common import (
    H1,
    H2,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    Option,
    P,
    Select,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
)
from fasthtml.core import FtResponse
from sqlalchemy import select
from starlette.responses import RedirectResponse, Response

from app import content
from app import marketing_subscribers as subscribers
from app.db import SessionLocal
from app.models import MarketingSubscriber, Site
from app.services import CommerceError

PAGE_SIZE = 25


def register_marketing_subscriber_routes(rt, actor, csrf, check_csrf, shell, error):
    def unsubscribe_copy(site):
        from app.site_editable_settings import shell_fields, shell_values

        config = site.published_settings_json or {}
        values = shell_values(
            {**config, "theme_pack": "generic"}, shell_fields({"theme_pack": "generic"}),
        )
        return values["generic_labels"]

    def public_page(site, request, title, *children):
        from app.platform_ui import platform_page

        return platform_page(title, P(site.name + " · Powered by FastShop"), *children,
            navigation=[A("Back to store", href=request.scope.get("site_base", "/sites/" + site.slug) + "/")],
            customer=True)

    def public_response(page):
        return FtResponse(page, headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
        })

    def subscriber_for_token(db, site, token):
        return db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.site_id == site.id,
            MarketingSubscriber.tenant_id == site.tenant_id,
            MarketingSubscriber.unsubscribe_token == str(token or ""),
        ))

    @rt("/sites/{slug}/unsubscribe", methods=["GET"])
    def get(session, request, slug: str, token: str = ""):
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == slug, Site.status != "deleted"))
            if not site:
                return Response("Unsubscribe link is invalid.", status_code=404,
                                headers={"Referrer-Policy": "no-referrer"})
            subscriber = subscriber_for_token(db, site, token)
            if not subscriber:
                return Response("Unsubscribe link is invalid.", status_code=404,
                                headers={"Referrer-Policy": "no-referrer"})
            copy = unsubscribe_copy(site)
            if subscriber.status == "unsubscribed":
                title = copy["newsletter_unsubscribe_title"]
                return public_response(public_page(site, request, title,
                    H1(title), P(copy["newsletter_unsubscribed"], role="status")))
            root = request.scope.get("site_base", "/sites/" + site.slug)
            title = copy["newsletter_unsubscribe_title"]
            return public_response(public_page(site, request, title,
                H1(title), P(copy["newsletter_unsubscribe_instruction"]),
                Form(csrf(session), Input(type="hidden", name="token", value=token),
                     Button(copy["newsletter_unsubscribe_cta"], cls="e-button"),
                     method="post", cls="e-form", action=root + "/unsubscribe?" + urlencode({"token": token}))))

    @rt("/sites/{slug}/unsubscribe", methods=["POST"])
    async def post(session, request, slug: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = db.scalar(select(Site).where(Site.slug == slug, Site.status != "deleted"))
                subscriber = subscriber_for_token(db, site, form.get("token")) if site else None
                if not site or not subscriber:
                    return Response("Unsubscribe link is invalid.", status_code=404,
                                    headers={"Referrer-Policy": "no-referrer"})
                subscribers.set_status(db, site, subscriber.id, "unsubscribed")
                copy = unsubscribe_copy(site)
                db.commit()
                title = copy["newsletter_unsubscribe_title"]
                page = public_page(site, request, title,
                    H1(title), P(copy["newsletter_unsubscribed"], role="status"))
                return public_response(page)
        except CommerceError as exc:
            return Response(str(exc), status_code=400, headers={"Referrer-Policy": "no-referrer"})

    def list_url(site_id, params=None):
        query = urlencode({key: value for key, value in (params or {}).items() if value not in ("", None)})
        return f"/admin/sites/{site_id}/marketing/subscribers" + ("?" + query if query else "")

    def admin_list(site, rows, total, query, status, source, page, csrf_field, notice):
        site_id = site.id
        base = f"/admin/sites/{site_id}/marketing/subscribers"
        pages = max(1, math.ceil(total / PAGE_SIZE))
        selected = {"q": query, "status": status, "source": source}
        filters = Form(
            Label("Search email", Input(name="q", type="search", value=query, maxlength=160)),
            Label("Status", Select(
                Option("All statuses", value="", selected=not status),
                *[Option(value.title(), value=value, selected=status == value)
                  for value in sorted(subscribers.STATUSES)], name="status")),
            Label("Source", Select(
                Option("All sources", value="", selected=not source),
                *[Option(value.title(), value=value, selected=source == value)
                  for value in sorted(subscribers.SOURCES)], name="source")),
            Button("Filter", cls="a-button"), method="get", action=base, cls="e-form a-subscriber-filters",
        )
        add_form = Form(
            csrf_field(),
            Label("Email address", Input(name="email", type="email", required=True, maxlength=320)),
            Label(Input(type="checkbox", name="consent", required=True), " I have consent to email this person."),
            Button("Add subscriber", cls="a-button"), method="post", action=base + "/add", cls="e-form",
        )
        import_form = Form(
            csrf_field(),
            Label("CSV file", Input(name="file", type="file", accept=".csv,text/csv", required=True)),
            Label(Input(type="checkbox", name="consent", required=True),
                  " I have consent to email everyone in this file."),
            Button("Import subscribers", cls="a-button"), method="post", enctype="multipart/form-data",
            action=base + "/import", cls="e-form",
        )
        headers = Tr(Th("Email"), Th("Status"), Th("Source"), Th("Consented at"), Th("Actions"))
        body_rows = []
        for row in rows:
            if row.status == "subscribed":
                action = Form(csrf_field(), Button("Unsubscribe", cls="a-button a-button-secondary"),
                    method="post", action=base + f"/{row.id}/unsubscribe")
            else:
                action = Form(csrf_field(),
                    Label(Input(type="checkbox", name="consent", required=True), " I have consent."),
                    Button("Resubscribe", cls="a-button a-button-secondary"),
                    method="post", action=base + f"/{row.id}/subscribe")
            body_rows.append(Tr(Td(row.email), Td(row.status.title()), Td(row.source.title()),
                Td(row.consented_at.isoformat() if row.consented_at else ""), Td(action)))
        table = Table(Thead(headers), Tbody(*body_rows), cls="a-subscriber-table")
        export = Form(
            csrf_field(),
            *[Input(type="hidden", name=key, value=value) for key, value in selected.items()],
            Button("Export CSV", cls="a-button a-button-secondary"),
            method="post", action=base + "/export", cls="a-subscriber-export",
        )
        pagination = Div(
            P(f"Page {page} of {pages} · {total} subscriber" + ("s" if total != 1 else "")),
            A("Previous", href=list_url(site_id, {**selected, "page": page - 1}),
              aria_disabled="true" if page <= 1 else None) if page > 1 else None,
            A("Next", href=list_url(site_id, {**selected, "page": page + 1}) if page < pages else "#",
              aria_disabled="true" if page >= pages else None) if page < pages else None,
            cls="a-subscriber-pagination",
        )
        return (
            A("← Marketing", href=f"/admin/sites/{site_id}/marketing"),
            H1("Email subscribers"),
            P(notice, role="status") if notice else None,
            Div(filters, export, cls="a-subscriber-toolbar"),
            Div(Div(table, cls="a-table-wrap") if rows else P("No subscribers match these filters."), pagination,
                cls="e-card a-subscriber-list"),
            Div(H2("Add subscriber"), P("Only add an address when the person has agreed to marketing email."),
                add_form, cls="e-card"),
            Div(H2("Import subscribers"),
                P("Upload a CSV with an email column. Files are limited to 1 MB; repeated and invalid addresses are counted."),
                import_form, cls="e-card"),
        )

    @rt("/admin/sites/{site_id}/marketing/subscribers", methods=["GET"])
    def get(session, request, site_id: str, q: str = "", status: str = "", source: str = "",
            page: int = 1, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                page = max(1, int(page))
                rows, total = subscribers.subscribers_page(
                    db, site, query=q, status=status, source=source, page=page, page_size=PAGE_SIZE,
                )
                page = min(page, max(1, math.ceil(total / PAGE_SIZE)))
                if not rows and page > 1:
                    page -= 1
                    rows, total = subscribers.subscribers_page(
                        db, site, query=q, status=status, source=source, page=page, page_size=PAGE_SIZE,
                    )
                return shell("Email subscribers", *admin_list(
                    site, rows, total, q, status, source, page, lambda: csrf(session), notice,
                ))
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/subscribers/export", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                payload = subscribers.export_csv(db, site, query=form.get("q", ""),
                                                  status=form.get("status", ""),
                                                  source=form.get("source", ""))
                return Response(payload, media_type="text/csv; charset=utf-8", headers={
                    "Content-Disposition": 'attachment; filename="marketing-subscribers.csv"',
                    "Cache-Control": "private, no-store",
                })
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/subscribers/add", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            if form.get("consent") != "on":
                raise CommerceError("Confirm that you have consent before adding this subscriber.")
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                _, created = subscribers.upsert_subscriber(
                    db, site, form.get("email", ""), source="admin", status="subscribed",
                    evidence="I have consent (merchant added subscriber).",
                )
                db.commit()
                notice = "Subscriber added." if created else "Subscriber preference updated."
                return RedirectResponse(list_url(site.id, {"notice": notice}), status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/subscribers/import", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            upload = form.get("file")
            if upload and hasattr(upload, "read"):
                payload = await upload.read(subscribers.MAX_CSV_BYTES + 1)
            else:
                payload = str(form.get("csv_text", "")).encode("utf-8")
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                counts = subscribers.import_csv(
                    db, site, payload, consent_confirmed=form.get("consent") == "on",
                )
                db.commit()
                notice = (f"Added {counts['added']}, updated {counts['updated']}, "
                          f"skipped {counts['duplicates']} duplicates and {counts['invalid']} invalid addresses.")
                return RedirectResponse(list_url(site.id, {"notice": notice}), status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/marketing/subscribers/{subscriber_id}/{action}", methods=["POST"])
    async def post(session, request, site_id: str, subscriber_id: str, action: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            if action not in {"subscribe", "unsubscribe"}:
                raise CommerceError("Choose a valid subscriber action.")
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                row = subscribers.set_status(
                    db, site, subscriber_id,
                    "subscribed" if action == "subscribe" else "unsubscribed",
                    consent=form.get("consent") == "on",
                )
                db.commit()
                notice = f"{row.email} is now {row.status}."
                return RedirectResponse(list_url(site.id, {"notice": notice}), status_code=303)
        except CommerceError as exc:
            return error(exc)

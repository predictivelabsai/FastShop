"""Site builder routes, using membership checks for every owned operation."""

from __future__ import annotations

import copy
import hashlib
import io
import re
import secrets
from datetime import UTC, datetime, timedelta

from fasthtml.common import (
    H1,
    H2,
    H3,
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
    Title,
)
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from starlette.responses import RedirectResponse, Response

from app import content, site_ui
from app.config import settings
from app.db import SessionLocal
from app.integrations.contact_email import deliver
from app.models import (
    Membership,
    Site,
    SiteContact,
    SiteMedia,
    SitePage,
    SiteRevision,
    User,
    new_id,
)
from app.services import CommerceError
from app.site_catalog import register_catalog_routes


def register_site_routes(rt):
    def actor(session):
        with SessionLocal() as db:
            user = db.get(User, session.get("user_id", ""))
            if not user or not user.is_active:
                raise CommerceError("Sign in to manage your sites.")
            return user.id

    def csrf(session):
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        return Input(type="hidden", name="csrf_token", value=session["csrf_token"])

    def check_csrf(session, form):
        if not session.get("csrf_token") or not secrets.compare_digest(session["csrf_token"], str(form.get("csrf_token", ""))):
            raise CommerceError("Your session expired. Reload and try again.")

    def shell(title, *children):
        from fasthtml.common import Meta
        return (Title(title + " — FastShop Sites"), Meta(name="viewport", content="width=device-width, initial-scale=1"), Link(rel="icon", href="/static/favicon.svg"), Link(rel="stylesheet", href="/static/site-editor.css"),
                Script(src="/static/site-editor.js", defer=True),
                Div(A("← FastShop", href="/admin"), A("My sites", href="/admin/sites"), cls="e-top"),
                Div(H1(title), *children, cls="e-main"))

    def error(exc):
        return Response(str(exc), status_code=400, media_type="text/plain")

    register_catalog_routes(rt, actor, csrf, check_csrf, shell, error)

    @rt("/admin/sites", methods=["GET"])
    def get(session):
        if not session.get("user_id"):
            return RedirectResponse("/login?next=/admin/sites", status_code=303)
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                sites = list(db.scalars(select(Site).join(Membership, Membership.tenant_id == Site.tenant_id).where(
                    Membership.user_id == user_id, Membership.role.in_(["admin", "merchant", "editor"]))))
                return shell("Your websites", P("Build your story. Shape your storefront. Publish when you're ready."),
                    Div(*[Div(H2(site.name), P("/sites/" + site.slug), A("Open editor →", href=f"/admin/sites/{site.id}"), cls="e-card") for site in sites], cls="e-grid"),
                    H2("Create a website"), Form(csrf(session), Label("Site name", Input(name="name", required=True, maxlength=160)),
                        Label("Site address", Input(name="slug", required=True, pattern="[a-z][a-z0-9-]{2,60}", placeholder="your-brand")),
                        P("Start with the editorial commerce theme. Your site stays private until you publish."),
                        Button("Create site", cls="e-button"), method="post", action="/admin/sites", cls="e-form"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites", methods=["POST"])
    async def post(session, request):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.create_site(db, user_id, str(form.get("name", "")), str(form.get("slug", "")))
                db.commit()
                return RedirectResponse(f"/admin/sites/{site.id}", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}", methods=["GET"])
    def get(session, site_id: str):
        try:
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id)
                pages = content.site_pages(db, site)
                config = site.settings_json
                return shell(site.name,
                    Div(A("View site ↗", href=f"/sites/{site.slug}/", target="_blank"), A("Products", href=f"/admin/sites/{site.id}/products"), A("Inbox", href=f"/admin/sites/{site.id}/inbox"), A("Media library", href=f"/admin/sites/{site.id}/media"), cls="e-actions"),
                    P("Phase 1: pages, branding and content. Commerce stays closed for this review."),
                    Div(Div(H2("Pages"), *[Div(A(p.title, href=f"/admin/sites/{site.id}/pages/{p.id}"), Small(p.path),
                        Small("Published" if p.published_json else "Draft"), cls="e-page-row") for p in pages],
                        Details(Summary("Add a page"), Form(csrf(session), Label("Title", Input(name="title", required=True)),
                            Label("Path", Input(name="path", placeholder="/pages/our-story", required=True)),
                            Label("Type", Select(*[Option(k.title(), value=k) for k in sorted(content.PAGE_KINDS)], name="kind")),
                            Button("Create page", cls="e-button"), method="post", action=f"/admin/sites/{site.id}/pages", cls="e-form")), cls="e-card"),
                        Div(H2("Brand and shared content"), Form(csrf(session), Input(type="hidden", name="version", value=site.version),
                            *[Label(label, Input(name=key, value=config.get(key, ""))) for key, label in [
                                ("name", "Brand name"), ("tagline", "Tagline"), ("email", "Contact email"),
                                ("company", "Company"), ("address", "Company address"), ("announcement", "Announcement"),
                                ("offer", "First-order banner"), ("logo", "Logo URL"), ("logo_light", "Light logo URL"),
                                ("science_date", "Research figures checked as of")]],
                            Label("Footer disclaimer", Textarea(config.get("footer", ""), name="footer", rows=4)),
                            H3("Menu links"), *[Div(Input(name=f"nav_label_{i}", value=item["label"], aria_label="Menu label"),
                                Input(name=f"nav_path_{i}", value=item["path"], aria_label="Menu path"), cls="e-pair") for i, item in enumerate(config.get("navigation", []))],
                            Div(Input(name="new_nav_label", placeholder="New menu label", aria_label="New menu label"), Input(name="new_nav_path", placeholder="/pages/new-page", aria_label="New menu path"), cls="e-pair"),
                            H3("Research figures"), *[Div(Label("Value", Input(name=f"fact_value_{i}", value=item["value"])), Label("Label", Input(name=f"fact_label_{i}", value=item["label"]))) for i, item in enumerate(config.get("facts", []))],
                            H3("Social links"), *[Label(item["label"], Input(name=f"social_url_{i}", value=item.get("url", ""), placeholder="https://…")) for i, item in enumerate(config.get("socials", []))],
                            H3("Benefit statements"), P("Only approved statements appear. Unchecking one removes it everywhere after publishing settings."),
                            *[Div(Label("Statement", Textarea(item["text"], name=f"claim_text_{i}", rows=2)),
                                Label(Input(type="checkbox", name=f"claim_approved_{i}", checked=item.get("approved", False)), " Approved for publication")) for i, item in enumerate(config.get("claims", []))],
                            Button("Save draft settings", name="action", value="draft", cls="e-button"),
                            Button("Publish shared settings", name="action", value="publish", cls="e-button"),
                            method="post", action=f"/admin/sites/{site.id}/settings", cls="e-form"), cls="e-card"), cls="e-grid"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            user_id = actor(session)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, user_id, publish=form.get("action") == "publish")
                site = db.scalar(select(Site).where(Site.id == site.id, Site.tenant_id == site.tenant_id).with_for_update().execution_options(populate_existing=True))
                if str(site.version) != str(form.get("version")):
                    raise CommerceError("Settings changed in another window. Reload before saving.")
                config = copy.deepcopy(site.settings_json)
                for key in ("name", "tagline", "email", "company", "address", "announcement", "offer", "logo", "logo_light", "science_date", "footer"):
                    config[key] = str(form.get(key, ""))[:2000]
                for key in ("logo", "logo_light"):
                    if config.get(key):
                        config[key] = content.safe_url(config[key], media=True)
                content.validate_media_ownership(db, site, config)
                for i, item in enumerate(config.get("navigation", [])):
                    item["label"] = str(form.get(f"nav_label_{i}", item["label"]))[:80]
                    item["path"] = content.safe_url(str(form.get(f"nav_path_{i}", item["path"])))
                config["navigation"] = [item for item in config.get("navigation", []) if item["label"] and item["path"]]
                if form.get("new_nav_label") and form.get("new_nav_path"):
                    config["navigation"].append({"label": str(form["new_nav_label"])[:80], "path": content.safe_url(str(form["new_nav_path"]))})
                for i, item in enumerate(config.get("facts", [])):
                    item["value"] = str(form.get(f"fact_value_{i}", item["value"]))[:20]
                    item["label"] = str(form.get(f"fact_label_{i}", item["label"]))[:200]
                for i, item in enumerate(config.get("socials", [])):
                    item["url"] = content.safe_url(str(form.get(f"social_url_{i}", item.get("url", ""))))
                for i, item in enumerate(config.get("claims", [])):
                    item["text"] = str(form.get(f"claim_text_{i}", item["text"]))[:1000]
                    item["approved"] = form.get(f"claim_approved_{i}") == "on"
                    item["reviewed_by"] = user_id
                    item["reviewed_at"] = datetime.now(UTC).isoformat()
                site.settings_json = config
                site.version += 1
                if form.get("action") == "publish":
                    site.published_settings_json = copy.deepcopy(config)
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/pages", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                page = content.create_page(db, site, str(form.get("title", "")), str(form.get("path", "")), str(form.get("kind", "content")))
                db.commit()
                return RedirectResponse(f"/admin/sites/{site.id}/pages/{page.id}", status_code=303)
        except CommerceError as exc:
            return error(exc)

    def section_editor(section, index):
        fields = []
        for key, label in [("eyebrow", "Eyebrow"), ("heading", "Heading"), ("body", "Text"), ("image", "Image URL"),
                           ("video", "Video URL"), ("mobile_video", "Mobile video URL"), ("link", "Link"), ("button", "Button label")]:
            value = section.get(key, "")
            fields.append(Label(label, Textarea(value, name=f"section_{index}_{key}", rows=5 if key == "body" else 2) if key in {"body", "heading"} else Input(name=f"section_{index}_{key}", value=value)))
        for j, item in enumerate(section.get("items", [])):
            fields.append(Div(H3(f"Entry {j + 1}"), *[Label(key.title(), Textarea(str(item.get(key, "")), name=f"section_{index}_item_{j}_{key}", rows=3 if key == "body" else 1)) for key in ("heading", "body", "url", "theme", "image")], Label(Input(type="checkbox", name=f"section_{index}_item_{j}_remove"), " Remove entry"), cls="e-entry"))
        if section["type"] in {"faq", "team", "research", "references"}:
            fields.append(Label(Input(type="checkbox", name=f"section_{index}_add_item"), " Add a new entry on save"))
        if section["type"] == "product":
            fields.append(Label("Gallery image URLs (one per line)", Textarea("\n".join(section.get("gallery", [])), name=f"section_{index}_gallery", rows=4)))
        return Details(Summary(content.SECTION_TYPES[section["type"]] + (" · " + section.get("heading", "")[:45] if section.get("heading") else "")),
                       Input(type="hidden", name="section_order", value=section["id"]),
                       Div(Button("Move up", type="button", data_move="up"), Button("Move down", type="button", data_move="down"),
                           Button("Remove section", type="button", data_remove=""), cls="e-actions"), *fields,
                       Label(Input(type="checkbox", name=f"section_{index}_hidden", checked=section.get("hidden", False)), " Hide section"),
                       cls="e-section", data_section=section["id"])

    @rt("/admin/sites/{site_id}/pages/{page_id}", methods=["GET"])
    def get(session, site_id: str, page_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                page = content.site_page(db, site, page_id)
                document = page.draft_json
                revisions = list(db.scalars(select(SiteRevision).where(SiteRevision.page_id == page.id,
                    SiteRevision.site_id == site.id, SiteRevision.tenant_id == site.tenant_id).order_by(SiteRevision.created_at.desc()).limit(20)))
                return shell(document["title"], A("← All pages and settings", href=f"/admin/sites/{site.id}"),
                    Div(Form(csrf(session), Input(type="hidden", name="version", value=page.version),
                        Label("Page title", Input(name="title", value=document["title"], required=True, maxlength=240)),
                        Label("SEO description", Textarea(document.get("description", ""), name="description", rows=3, maxlength=320)),
                        Div(Label("Article image URL", Input(name="article_image", value=document.get("image", ""))),
                            Label("Article category", Input(name="article_category", value=document.get("category", "LEARN")))) if page.kind == "article" else None,
                        Div(*[section_editor(s, i) for i, s in enumerate(document.get("sections", []))], id="e-sections"),
                        Label("Add a section", Select(Option("Choose a section…", value=""), *[Option(label, value=key) for key, label in content.SECTION_TYPES.items()], name="new_section")),
                        Div(Button("Save draft", name="action", value="draft", cls="e-button"), Button("Publish page", name="action", value="publish", cls="e-button"),
                            Button("Unpublish", name="action", value="unpublish"), cls="e-actions e-save"),
                        method="post", action=f"/admin/sites/{site.id}/pages/{page.id}", cls="e-form e-editor-form"),
                        Div(A("Open draft preview ↗", href=f"/admin/sites/{site.id}/preview/{page.id}", target="_blank"),
                            Iframe(src=f"/admin/sites/{site.id}/preview/{page.id}", title="Draft page preview", cls="e-preview"), cls="e-preview-wrap"), cls="e-editor"),
                    Details(Summary("Revision history"), *[Form(csrf(session), Input(type="hidden", name="version", value=page.version),
                        Input(type="hidden", name="revision", value=r.id), P(str(r.created_at) + " · " + r.action),
                        Button("Restore as draft", cls="e-button"), method="post", action=f"/admin/sites/{site.id}/pages/{page.id}/restore") for r in revisions]))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/pages/{page_id}", methods=["POST"])
    async def post(session, request, site_id: str, page_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id)
                page = content.site_page(db, site, page_id)
                document = copy.deepcopy(page.draft_json)
                document["title"] = str(form.get("title", ""))
                document["description"] = str(form.get("description", ""))[:320]
                if page.kind == "article":
                    document["image"] = str(form.get("article_image", ""))
                    document["category"] = str(form.get("article_category", "LEARN"))[:100]
                sections = {}
                for i, section in enumerate(document.get("sections", [])):
                    for key in ("eyebrow", "heading", "body", "image", "video", "mobile_video", "link", "button"):
                        section[key] = str(form.get(f"section_{i}_{key}", ""))[:30000]
                    section["hidden"] = form.get(f"section_{i}_hidden") == "on"
                    for j, item in enumerate(section.get("items", [])):
                        for key in ("heading", "body", "url", "theme", "image"):
                            item[key] = str(form.get(f"section_{i}_item_{j}_{key}", item.get(key, "")))[:5000]
                    if "items" in section:
                        section["items"] = [item for j, item in enumerate(section["items"]) if form.get(f"section_{i}_item_{j}_remove") != "on"]
                    if section["type"] in {"faq", "team", "research", "references"} and form.get(f"section_{i}_add_item") == "on":
                        section.setdefault("items", []).append({"heading": "New entry", "body": "Add your text", "url": ""})
                    if section["type"] == "product":
                        section["gallery"] = [line.strip() for line in str(form.get(f"section_{i}_gallery", "")).splitlines() if line.strip()]
                    sections[section["id"]] = section
                document["sections"] = [sections[key] for key in form.getlist("section_order") if key in sections]
                if form.get("new_section") in content.SECTION_TYPES:
                    document["sections"].append({"type": form["new_section"], "heading": "New section", "body": "Add your story here."})
                action = str(form.get("action", "draft"))
                if action not in {"draft", "publish", "unpublish"}:
                    raise CommerceError("Unknown publication action.")
                content.save_page(db, site, page.id, user_id, document, int(form.get("version", 0)), action)
                if action == "publish" and site.status == "draft":
                    site.status = "preview"
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/pages/{page_id}", status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/pages/{page_id}/restore", methods=["POST"])
    async def post(session, request, site_id: str, page_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id)
                content.restore_page(db, site, page_id, str(form.get("revision", "")), user_id, int(form.get("version", 0)))
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/pages/{page_id}", status_code=303)
        except (CommerceError, ValueError) as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/preview/{page_id}", methods=["GET"])
    def get(session, site_id: str, page_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                page = content.site_page(db, site, page_id)
                csrf(session)
                return site_ui.storefront(db, site, page, f"/sites/{site.slug}", session["csrf_token"],
                                          settings.public_url + f"/sites/{site.slug}" + page.path, preview=True)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/inbox", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                rows = db.scalars(select(SiteContact).where(SiteContact.site_id == site.id, SiteContact.tenant_id == site.tenant_id).order_by(SiteContact.created_at.desc()).limit(100))
                return shell("Contact inbox", A("← Site", href=f"/admin/sites/{site.id}"), *[Div(H2(row.name), P(row.email), P(row.message), Small("Delivery: " + row.status), cls="e-card") for row in rows])
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/media", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                media = list(db.scalars(select(SiteMedia).where(SiteMedia.site_id == site.id, SiteMedia.tenant_id == site.tenant_id).order_by(SiteMedia.created_at.desc())))
                return shell("Media library", A("← Site", href=f"/admin/sites/{site.id}"),
                    P("Upload photographs with descriptive alt text. Images are optimized to WebP and stored with this site."),
                    Form(csrf(session), Label("Title", Input(name="title", required=True, maxlength=200)),
                        Label("Alt text", Input(name="alt", required=True, maxlength=400)),
                        Label("Image (JPEG, PNG or WebP, up to 8 MB)", Input(type="file", name="image", accept="image/jpeg,image/png,image/webp", required=True)),
                        Label(Input(type="checkbox", name="placeholder"), " Placeholder image"),
                        Button("Upload image", cls="e-button"), method="post", enctype="multipart/form-data", cls="e-form"),
                    Div(*[Div(H2(m.title), site_ui.image(f"/site-media/{site.id}/{m.id}", m.alt),
                        Label("Image URL — use in a section", Input(value=f"/site-media/{site.id}/{m.id}", readonly=True)),
                        P(m.alt), Small("PLACEHOLDER" if m.is_placeholder else "Uploaded asset"), cls="e-card") for m in media], cls="e-grid"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/media", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                upload = form.get("image")
                if not upload or not hasattr(upload, "read"):
                    raise CommerceError("Select an image.")
                data = await upload.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024:
                    raise CommerceError("Images must be smaller than 8 MB.")
                with Image.open(io.BytesIO(data)) as original:
                    if original.format not in {"JPEG", "PNG", "WEBP"} or original.width * original.height > 25_000_000:
                        raise CommerceError("Choose a JPEG, PNG or WebP image smaller than 25 megapixels.")
                    original.thumbnail((1800, 1800))
                    output = io.BytesIO()
                    original.convert("RGB").save(output, format="WEBP", quality=85)
                title, alt = str(form.get("title", "")).strip(), str(form.get("alt", "")).strip()
                if not title or not alt:
                    raise CommerceError("Provide a title and useful alt text.")
                data = output.getvalue()
                db.add(SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title=title[:200], alt=alt[:400],
                    storage_key=new_id() + ".webp", content_type="image/webp", size=len(data), data=data,
                    is_placeholder=form.get("placeholder") == "on"))
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/media", status_code=303)
        except (CommerceError, UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
            return error(CommerceError("Invalid image upload.") if not isinstance(exc, CommerceError) else exc)

    @rt("/site-media/{site_id}/{media_id}", methods=["GET"])
    def get(session, site_id: str, media_id: str):
        with SessionLocal() as db:
            site = db.get(Site, site_id)
            if not site:
                return Response("Not found", status_code=404)
            if site.status == "draft":
                try:
                    content.owned_site(db, site.id, actor(session))
                except CommerceError:
                    return Response("Not found", status_code=404)
            media = db.scalar(select(SiteMedia).where(SiteMedia.id == media_id, SiteMedia.site_id == site.id, SiteMedia.tenant_id == site.tenant_id))
            if not media or not media.data:
                return Response("Not found", status_code=404)
            return Response(media.data, media_type=media.content_type,
                            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    @rt("/sites/{slug}/contact", methods=["POST"])
    async def post(session, request, slug: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            if form.get("website"):
                raise CommerceError("Please submit the form without automatic filling.")
            name = str(form.get("name", "")).strip()
            email = str(form.get("email", "")).strip()
            message = str(form.get("message", "")).strip()
            if not 1 <= len(name) <= 160 or not 10 <= len(message) <= 5000 or len(email) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise CommerceError("Enter your name, a valid email and a message of 10–5000 characters.")
            with SessionLocal() as db:
                site = db.scalar(select(Site).where(Site.slug == slug, Site.status.in_(["preview", "published"])))
                if not site:
                    return Response("Not found", status_code=404)
                client_hash = hashlib.sha256((settings.session_secret + site.id + (request.client.host if request.client else "unknown")).encode()).hexdigest()
                recent = db.scalar(select(SiteContact.id).where(SiteContact.site_id == site.id,
                    SiteContact.tenant_id == site.tenant_id, SiteContact.client_hash == client_hash,
                    SiteContact.created_at > datetime.now(UTC) - timedelta(seconds=30)))
                if recent:
                    return Response("Please wait a moment before sending another message.", status_code=429)
                entry = SiteContact(tenant_id=site.tenant_id, site_id=site.id, name=name, email=email, message=message, client_hash=client_hash)
                db.add(entry)
                db.commit()
                entry.status = deliver(entry, site.published_settings_json)
                entry.attempts += 1
                db.commit()
                return public(session, slug, "/pages/contact", request=request, message="Thank you. Your message is saved in our inbox." + (" Email delivery is pending setup." if entry.status == "awaiting_configuration" else ""))
        except CommerceError as exc:
            return error(exc)

    @rt("/sites/{slug}/", methods=["GET"])
    def get(session, request, slug: str):
        return public(session, slug, "/", request=request)

    @rt("/sites/{slug}/{path:path}", methods=["GET"])
    def get(session, request, slug: str, path: str):
        return public(session, slug, "/" + path, request=request)

    def public(session, slug, path, message="", request=None):
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == slug, Site.status.in_(["preview", "published"])))
            if not site:
                return Response("Site not found", status_code=404)
            page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id, SitePage.path == (path.rstrip("/") or "/")))
            if not page or not page.published_json:
                return Response("Page not found", status_code=404)
            csrf(session)
            base = request.scope.get("site_base", f"/sites/{site.slug}") if request else f"/sites/{site.slug}"
            canonical_base = request.scope.get("site_canonical", settings.public_url + base) if request else settings.public_url + base
            return site_ui.storefront(db, site, page, base, session["csrf_token"], canonical_base + page.path, message=message)

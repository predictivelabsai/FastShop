"""Site builder routes, using membership checks for every owned operation."""

from __future__ import annotations

import copy
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from fasthtml.common import (
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
    Option,
    P,
    Select,
    Small,
    Summary,
    Textarea,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse, Response

from app import content, site_ui
from app import site_builder_services as builder
from app.config import settings
from app.db import SessionLocal
from app.integrations.contact_email import deliver
from app.models import (
    Membership,
    Site,
    SiteContact,
    SitePage,
    SiteRevision,
    User,
    new_id,
)
from app.services import CommerceError
from app.site_blocks import (
    add_block,
    default_locale,
    merge_localized,
    normalize_document,
    patch_block,
    remove_block,
    reorder_blocks,
    resolve_document,
    resolve_text,
)
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
        from app.platform_ui import platform_page
        return platform_page(title, *children)

    def error(exc):
        return Response(str(exc), status_code=400, media_type="text/plain")

    from app.site_media_routes import register_media_routes
    register_media_routes(rt, actor, csrf, check_csrf, shell, error)
    from app.site_menu_routes import register_menu_routes
    register_menu_routes(rt, actor, csrf, check_csrf, shell, error)
    from app.site_snippet_routes import register_snippet_routes
    register_snippet_routes(rt, actor, csrf, check_csrf, shell, error)
    register_catalog_routes(rt, actor, csrf, check_csrf, shell, error)
    from app.site_builder_routes import register_builder_routes
    register_builder_routes(rt, actor, csrf, check_csrf, shell, error)
    from app.site_sample_routes import register_sample_routes
    register_sample_routes(rt, actor, csrf, check_csrf, shell, error)
    from app.demo_routes import register_demo_routes
    register_demo_routes(rt, actor, csrf, check_csrf, shell, error)
    from app.commerce_routes import register_commerce_routes
    register_commerce_routes(rt, actor, csrf, check_csrf, shell, error)
    from app.customer_routes import register_customer_routes
    register_customer_routes(rt, actor, csrf, check_csrf, shell, error)
    from app.store_checkout_routes import register_store_checkout_routes
    register_store_checkout_routes(rt, csrf, check_csrf, error)
    from app.subscription_routes import register_subscription_routes
    register_subscription_routes(rt, csrf, check_csrf)

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
                        Label("Build your way", Select(Option("Classical editor", value="classical"), Option("Build with AI / guided presets", value="chat"), name="flow")),
                        Label(Input(type="checkbox", name="samples"), " Start with clearly labelled sample merchant details"),
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
                if form.get("samples") == "on":
                    from app.site_samples import seed_samples
                    seed_samples(db, site, user_id)
                db.commit()
                return RedirectResponse(f"/admin/sites/{site.id}" + ("/build" if form.get("flow") == "chat" else ""), status_code=303)
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
                from app.site_samples import pending_reviews
                pending = pending_reviews(config)
                return shell(site.name,
                    Div(A("Build with AI →", href=f"/admin/sites/{site.id}/build"), A("Design controls", href=f"/admin/sites/{site.id}/build?view=design"), A("Merchant details & samples", href=f"/admin/sites/{site.id}/samples"), A("Try commerce demo", href=f"/admin/sites/{site.id}/demo"), cls="e-actions"),
                    Div(A("View site ↗", href=f"/sites/{site.slug}/", target="_blank"), A("Products", href=f"/admin/sites/{site.id}/products"), A("Commerce", href=f"/admin/sites/{site.id}/commerce"), A("Inbox", href=f"/admin/sites/{site.id}/inbox"), A("Menus", href=f"/admin/sites/{site.id}/menus"), A("Media library", href=f"/admin/sites/{site.id}/media"), A("Snippets", href=f"/admin/sites/{site.id}/snippets"), A("Reviews", href=f"/admin/sites/{site.id}/reviews"), A("Placeholders", href=f"/admin/sites/{site.id}/placeholders"), cls="e-actions"),
                    P("Manage your pages, brand and catalog. Configure sandbox commerce separately before enabling customer services."),
                    P("Before publication, review merchant fields: " + ", ".join(pending) + ". Publication does not confirm these details or enable payments.", cls="e-note") if pending else None,
                    A("Customers, tracking & email", href=f"/admin/sites/{site.id}/customers"),
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
                            H3("Analytics"), Label("GA4 measurement ID (optional)", Input(name="ga4_measurement_id", value=config.get("ga4_measurement_id", ""), placeholder="G-ABC1234567", maxlength=22)),
                            P("Disabled when empty. Published public pages load Google Analytics only after analytics consent. Account and checkout pages are excluded; no Meta pixel is installed. Publish shared settings to apply changes."),
                            H3("Checkout payment methods"),
                            Label(Input(type="checkbox", name="offer_paypal", checked="paypal" in config.get("payment_methods", ["card"])), " Offer PayPal at one-time checkout (requires PayPal enabled on your Stripe account)"),
                            P("Card is always available. Apple Pay and Google Pay appear automatically once your store domain is registered with Stripe. Subscriptions stay card-only so renewals can charge the saved card.", cls="e-note"),
                            H3("Navigation"), A("Edit menus", href=f"/admin/sites/{site.id}/menus"),
                            H3("Research figures"), *[Div(Label("Value", Input(name=f"fact_value_{i}", value=item["value"])), Label("Label", Input(name=f"fact_label_{i}", value=item["label"]))) for i, item in enumerate(config.get("facts", []))],
                            H3("Social links"), *[Label(item["label"], Input(name=f"social_url_{i}", value=item.get("url", ""), placeholder="https://…")) for i, item in enumerate(config.get("socials", []))],
                            H3("Benefit statements"), P("Only approved statements appear. Unchecking one removes it everywhere after publishing settings. Clear a statement's text to delete it, or add new ones below (brief §8 reserved slots)."),
                            *[Div(Label("Statement", Textarea(item["text"], name=f"claim_text_{i}", rows=2)),
                                Label(Input(type="checkbox", name=f"claim_approved_{i}", checked=item.get("approved", False)), " Approved for publication")) for i, item in enumerate(config.get("claims", []))],
                            Div(Label("Add a benefit statement", Textarea("", name="new_claim_text", rows=2, placeholder="Supports … .*")),
                                Label(Input(type="checkbox", name="new_claim_approved"), " Approve now"), cls="e-pair"),
                            P("An approved statement must include an asterisk (*) and compliant wording; it renders beside the FDA footer disclaimer.", cls="e-note"),
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
                site = builder.lock_site(db, site.id, user_id, site.version)
                before = builder.snapshot(db, site)
                config = copy.deepcopy(site.settings_json)
                from app.site_analytics import validate_measurement_id
                config["ga4_measurement_id"] = validate_measurement_id(form.get("ga4_measurement_id", config.get("ga4_measurement_id", "")))
                config["payment_methods"] = ["card"] + (["paypal"] if form.get("offer_paypal") == "on" else [])
                for key in ("name", "tagline", "email", "company", "address", "announcement", "offer", "logo", "logo_light", "science_date", "footer"):
                    config[key] = str(form.get(key, ""))[:2000]
                for key in ("logo", "logo_light"):
                    if config.get(key):
                        config[key] = content.safe_url(config[key], media=True)
                content.validate_media_ownership(db, site, config)
                for i, item in enumerate(config.get("facts", [])):
                    item["value"] = str(form.get(f"fact_value_{i}", item["value"]))[:20]
                    item["label"] = str(form.get(f"fact_label_{i}", item["label"]))[:200]
                for i, item in enumerate(config.get("socials", [])):
                    item["url"] = content.safe_url(str(form.get(f"social_url_{i}", item.get("url", ""))))
                from app.compliance import assert_claim_compliant
                claims = []
                for i, item in enumerate(config.get("claims", [])):
                    text = str(form.get(f"claim_text_{i}", item["text"]))[:1000].strip()
                    if not text:
                        continue  # a cleared statement is removed everywhere
                    approved = form.get(f"claim_approved_{i}") == "on"
                    if approved:
                        assert_claim_compliant(text)
                    item.update(text=text, approved=approved, reviewed_by=user_id,
                                reviewed_at=datetime.now(UTC).isoformat())
                    item.setdefault("key", new_id())
                    claims.append(item)
                new_text = str(form.get("new_claim_text", "")).strip()[:1000]
                if new_text:
                    approved = form.get("new_claim_approved") == "on"
                    if approved:
                        assert_claim_compliant(new_text)
                    claims.append({"key": new_id(), "text": new_text, "approved": approved,
                                   "reviewed_by": user_id, "reviewed_at": datetime.now(UTC).isoformat()})
                config["claims"] = claims
                if any(c.get("approved") for c in claims) and not config.get("footer", "").strip():
                    raise CommerceError("Add the FDA disclaimer to the footer before approving a benefit statement (brief §8).")
                from app.site_samples import invalidate_reviews
                config = invalidate_reviews(config, site.settings_json, config)
                site.settings_json = config
                site.version += 1
                if form.get("action") == "publish":
                    site.published_settings_json = copy.deepcopy(config)
                db.flush()
                builder.record_change(db, site, user_id, before, "classical", "Brand and shared content")
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/reviews", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                from app.site_reviews import all_reviews
                reviews = all_reviews(db, site)
                rows = [Form(csrf(session), Small(("APPROVED" if r.is_approved else "PENDING"), cls="e-pill"),
                             P(f"{r.rating}★ · {r.title}"), P(r.body), Small("— " + r.author_name),
                             Button("Hide" if r.is_approved else "Approve", name="action",
                                    value="hide" if r.is_approved else "approve", cls="e-button"),
                             Button("Delete", name="action", value="delete", cls="e-button"),
                             method="post", action=f"/admin/sites/{site.id}/reviews/{r.id}", cls="e-page-row")
                        for r in reviews]
                return shell("Reviews — " + site.name,
                    Div(A("← Back to site", href=f"/admin/sites/{site.id}"), cls="e-actions"),
                    H2(f"Customer reviews ({len(reviews)})"),
                    P("Only approved reviews appear on the storefront. No reviews are invented; the sample section shows until real ones are approved."),
                    Div(*rows, cls="e-card") if rows else P("No reviews collected yet.", cls="e-note"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/reviews/{review_id}", methods=["POST"])
    async def post(session, request, site_id: str, review_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=True)
                from app.site_reviews import moderate_review
                moderate_review(db, site, user_id, review_id, str(form.get("action", "")))
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/reviews", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/placeholders", methods=["GET"])
    def get(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                from app.site_placeholders import placeholder_inventory
                items = placeholder_inventory(db, site)
                rows = [Div(Small(item["status"].upper(), cls="e-pill"), Small(item["area"]),
                            P(item["location"]), P(item["detail"]), cls="e-page-row") for item in items]
                return shell("Placeholders — " + site.name,
                    Div(A("← Back to site", href=f"/admin/sites/{site.id}"), cls="e-actions"),
                    H2(f"Placeholders and pending items ({len(items)})"),
                    P("Everything to replace or approve before launch, generated from the current content."),
                    Div(*rows, cls="e-card") if rows else P("No outstanding placeholders found.", cls="e-note"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/pages", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id)
                site = builder.lock_site(db, site.id, user_id, site.version)
                before = builder.snapshot(db, site)
                page = content.create_page(db, site, str(form.get("title", "")), str(form.get("path", "")), str(form.get("kind", "content")))
                site.version += 1
                db.flush()
                builder.record_change(db, site, user_id, before, "classical", "Created page: " + page.title)
                db.commit()
                return RedirectResponse(f"/admin/sites/{site.id}/pages/{page.id}", status_code=303)
        except CommerceError as exc:
            return error(exc)

    def media_picker(target, key, library, alt_target):
        return Label("Pick from library: " + key, Select(
            Option("Choose an image…", value=""),
            *[Option(title, value=url, data_alt=alt) for url, title, alt in library],
            data_media_pick=target, data_alt_target=alt_target, aria_label="Pick from library: " + key,
            onchange="const f=this.form,t=f.elements[this.dataset.mediaPick],a=f.elements[this.dataset.altTarget];if(this.value){t.value=this.dataset.mediaPick.endsWith('_gallery')?[t.value,this.value].filter(Boolean).join('\\n'):this.value;if(a&&!a.value)a.value=this.selectedOptions[0].dataset.alt;}this.value='';"))

    def section_editor(section, index, library):
        fields = []
        for key, label in [("eyebrow", "Eyebrow"), ("heading", "Heading"), ("body", "Text"), ("image", "Image URL"),
                           ("poster", "Poster URL"), ("alt", "Alt text"), ("video", "Video URL"), ("mobile_video", "Mobile video URL"), ("link", "Link"), ("button", "Button label")]:
            value = section.get(key, "")
            fields.append(Label(label, Textarea(value, name=f"section_{index}_{key}", rows=5 if key == "body" else 2) if key in {"body", "heading"} else Input(name=f"section_{index}_{key}", value=value)))
        if section["type"] == "embed":
            fields.append(Label("Embed URL (HTTPS)", Input(
                name=f"section_{index}_url",
                value=section.get("url", ""),
                type="url",
                placeholder="https://embed.example.com/item",
                required=True,
                maxlength=2048,
            )))
        for key in ("image", "poster", "gallery"):
            if key == "gallery" and section["type"] != "product":
                continue
            fields.append(media_picker(f"section_{index}_{key}", key, library, f"section_{index}_alt"))
        for j, item in enumerate(section.get("items", [])):
            fields.append(Div(H3(f"Entry {j + 1}"), *[Label(key.title(), Textarea(str(item.get(key, "")), name=f"section_{index}_item_{j}_{key}", rows=3 if key == "body" else 1)) for key in ("heading", "body", "url", "theme", "image", "alt")], media_picker(f"section_{index}_item_{j}_image", "entry image", library, f"section_{index}_item_{j}_alt"), Label(Input(type="checkbox", name=f"section_{index}_item_{j}_remove"), " Remove entry"), cls="e-entry"))
        if section["type"] in {"faq", "team", "research", "references"}:
            fields.append(Label(Input(type="checkbox", name=f"section_{index}_add_item"), " Add a new entry on save"))
        if section["type"] == "product":
            fields.append(Label("Gallery image URLs (one per line)", Textarea("\n".join(section.get("gallery", [])), name=f"section_{index}_gallery", rows=4, aria_label="Gallery image URLs (one per line)")))
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
                document = resolve_document(page.draft_json, default_locale(site))
                from app.site_blog import STATES, categories_for, metadata
                blog = metadata(document)
                blog_categories = categories_for(db, site) if page.kind == "article" else []
                from app.site_media import media_for, media_url
                library = [(media_url(m), m.title or "Untitled image", resolve_text(m.localized_alt if m.localized_alt is not None else m.alt, default_locale(site))) for m in media_for(db, site) if m.content_type.startswith("image/")]

                revisions = list(db.scalars(select(SiteRevision).where(SiteRevision.page_id == page.id,
                    SiteRevision.site_id == site.id, SiteRevision.tenant_id == site.tenant_id).order_by(SiteRevision.created_at.desc()).limit(20)))
                return shell(document["title"], Div(A("← All pages and settings", href=f"/admin/sites/{site.id}"),
                    A("Browse media library", href=f"/admin/sites/{site.id}/media", target="_blank"), cls="e-actions"),
                    Div(Form(csrf(session), Input(type="hidden", name="version", value=page.version),
                        Label("Page title", Input(name="title", value=document["title"], required=True, maxlength=240)),
                        Label("SEO description", Textarea(document.get("description", ""), name="description", rows=3, maxlength=320)),
                        Div(Label("Article image URL", Input(name="article_image", value=document.get("image", ""))),
                            Label("Article category", Input(name="article_category", value=document.get("category", "LEARN"), maxlength=100)),
                            Small("Use an existing name or enter a new category. Existing: " + ", ".join(c.name for c in blog_categories)),
                            Label("Editorial state", Select(*[Option(s.title(), value=s, selected=blog["state"] == s) for s in STATES], name="article_state")),
                            P("Save draft keeps the live page unchanged. Publish page applies this editorial state; only Published articles appear publicly."),
                            Label("Tags (comma-separated slugs)", Input(name="article_tags", value=", ".join(blog["tags"]), placeholder="research, everyday-reading")),
                            Label("Author name", Input(name="article_author", value=blog["author_name"], maxlength=160)),
                            Label("Author bio (optional)", Textarea(blog["author_bio"], name="article_bio", maxlength=2000, rows=3))) if page.kind == "article" else None,
                        Div(*[section_editor(s, i, library) for i, s in enumerate(document["blocks"])], id="e-sections"),
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
                site = builder.lock_site(db, site.id, user_id, site.version)
                before = builder.snapshot(db, site)
                page = content.site_page(db, site, page_id)
                document = normalize_document(page.draft_json)
                locale = default_locale(site)
                document["title"] = merge_localized(document.get("title", ""), str(form.get("title", "")), locale)
                document["description"] = merge_localized(document.get("description", ""), str(form.get("description", ""))[:320], locale)
                if page.kind == "article":
                    document["image"] = merge_localized(document.get("image", ""), str(form.get("article_image", "")), locale)
                    document["category"] = merge_localized(document.get("category", "LEARN"), str(form.get("article_category", "LEARN"))[:100], locale)
                    from app.site_blog import ensure_category, metadata
                    category = ensure_category(db, site, resolve_text(document["category"], locale))
                    blog = metadata(document)
                    document["blog"] = blog | {
                        "category_slug": category.slug,
                        "state": str(form.get("article_state", blog["state"])),
                        "tags": [t.strip() for t in str(form.get("article_tags", ",".join(blog["tags"]))).split(",") if t.strip()],
                        "author_name": str(form.get("article_author", blog["author_name"])),
                        "author_bio": str(form.get("article_bio", blog["author_bio"])),
                    }
                for i, section in enumerate(document["blocks"]):
                    values = {}
                    for key in ("eyebrow", "heading", "body", "image", "poster", "alt", "video", "mobile_video", "link", "button", "url"):
                        if f"section_{i}_{key}" not in form:
                            continue
                        values[key] = merge_localized(section.get(key, ""), str(form.get(f"section_{i}_{key}", ""))[:30000], locale)
                    values["hidden"] = form.get(f"section_{i}_hidden") == "on"
                    if "items" in section:
                        items = []
                        for j, original in enumerate(section["items"]):
                            if form.get(f"section_{i}_item_{j}_remove") == "on":
                                continue
                            item = copy.deepcopy(original)
                            for key in ("heading", "body", "url", "theme", "image", "alt"):
                                field = f"section_{i}_item_{j}_{key}"
                                if field in form:
                                    item[key] = merge_localized(original.get(key, ""), str(form[field])[:5000], locale)
                            items.append(item)
                        values["items"] = items
                    if section["type"] in {"faq", "team", "research", "references"} and form.get(f"section_{i}_add_item") == "on":
                        values.setdefault("items", []).append({"heading": "New entry", "body": "Add your text", "url": ""})
                    if section["type"] == "product":
                        gallery = [line.strip() for line in str(form.get(f"section_{i}_gallery", "")).splitlines() if line.strip()]
                        values["gallery"] = merge_localized(section.get("gallery", []), gallery, locale)
                    document = patch_block(document, section["id"], values)
                order = form.getlist("section_order")
                for section in document["blocks"]:
                    if section["id"] not in order:
                        document = remove_block(document, section["id"])
                document = reorder_blocks(document, order)
                if form.get("new_section") in content.SECTION_TYPES:
                    document = add_block(document, {"type": form["new_section"], "heading": "New section", "body": "Add your story here."})
                action = str(form.get("action", "draft"))
                if action not in {"draft", "publish", "unpublish"}:
                    raise CommerceError("Unknown publication action.")
                content.save_page(db, site, page.id, user_id, document, int(form.get("version", 0)), action)
                site.version += 1
                db.flush()
                builder.record_change(db, site, user_id, before, "classical", "Page: " + page.title)
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
                site = builder.lock_site(db, site.id, user_id, site.version)
                before = builder.snapshot(db, site)
                content.restore_page(db, site, page_id, str(form.get("revision", "")), user_id, int(form.get("version", 0)))
                site.version += 1
                db.flush()
                builder.record_change(db, site, user_id, before, "classical", "Restored page draft")
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
            from app.site_blog import articles_for, categories_for, is_listed, metadata
            category, tag = "", ""
            normalized_path = path.rstrip("/") or "/"
            match = re.fullmatch(r"/blog/(category|tag)/([a-z0-9]+(?:-[a-z0-9]+)*)", normalized_path)
            if match or normalized_path == "/blog":
                page = db.scalar(select(SitePage).where(SitePage.site_id == site.id,
                    SitePage.tenant_id == site.tenant_id, SitePage.kind == "blog",
                    SitePage.published_json.is_not(None)).order_by(SitePage.path))
                if match:
                    category = match[2] if match[1] == "category" else ""
                    tag = match[2] if match[1] == "tag" else ""
                    if category and category not in {c.slug for c in categories_for(db, site)}:
                        return Response("Category not found", status_code=404)
                    if tag and tag not in {t for p in articles_for(db, site) for t in metadata(p.published_json)["tags"]}:
                        return Response("Tag not found", status_code=404)
            if not page or not page.published_json:
                return Response("Page not found", status_code=404)
            if page.kind == "article" and not is_listed(page):
                return Response("Page not found", status_code=404)
            csrf(session)
            base = request.scope.get("site_base", f"/sites/{site.slug}") if request else f"/sites/{site.slug}"
            canonical_base = request.scope.get("site_canonical", settings.public_url + base) if request else settings.public_url + base
            return site_ui.storefront(db, site, page, base, session["csrf_token"], canonical_base + normalized_path,
                message=message, blog_listing=page.kind == "blog", blog_category=category, blog_tag=tag)

"""Merchant SEO settings editor."""

import copy

from fasthtml.common import (
    H2,
    H3,
    A,
    Button,
    Form,
    Input,
    Label,
    NotStr,
    Option,
    P,
    Script,
    Select,
    Span,
    Textarea,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app import content
from app import site_builder_services as builder
from app.db import SessionLocal
from app.models import SiteMedia, SitePage
from app.services import CommerceError
from app.site_seo import robots_content, validate, validate_site_image


def register_site_seo_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/admin/sites/{site_id}/settings/seo", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                config = site.settings_json or {}
                home = db.scalar(select(SitePage).where(SitePage.site_id == site.id,
                    SitePage.tenant_id == site.tenant_id, SitePage.path == "/"))
                home_doc = (home.draft_json or {}) if home else {}
                media = db.scalars(select(SiteMedia).where(SiteMedia.site_id == site.id,
                    SiteMedia.tenant_id == site.tenant_id).order_by(SiteMedia.created_at.desc())).all()
                images = [m for m in media if m.content_type.startswith("image/")]
                origin = "https://" + (site.hostname or "your-store.example")
                return shell("SEO", A("← Settings", href=f"/admin/sites/{site.id}/settings"),
                    P(notice[:300], role="status") if notice else None,
                    Form(csrf(session), Input(type="hidden", name="version", value=site.version),
                        H2("Search appearance"),
                        Label("Home page title", Input(name="home_title", value=home_doc.get("seo_title", home_doc.get("title", "")), maxlength=240)),
                        Label("Home page meta description", Textarea(home_doc.get("seo_description", home_doc.get("description", "")), name="home_description", rows=3, maxlength=320)),
                        Label("Title format for other pages", Input(name="title_format", value=config.get("seo_title_format", "{page} | {store}"), maxlength=200)),
                        P("Preview: ", Input(type="hidden", value=site.name, data_seo_store=True),
                          Span("Example page | " + site.name, data_seo_preview=True)),
                        Label("Default meta description", Textarea(config.get("seo_default_description", ""), name="default_description", rows=3, maxlength=320)),
                        H3("Social sharing"),
                        Label("Social sharing image", Select(Option("No image", value="", selected=not config.get("seo_social_image")),
                            *[Option(m.title or "Store image", value=m.public_url or f"/site-media/{site.id}/{m.id}", selected=(m.public_url or f"/site-media/{site.id}/{m.id}") == config.get("seo_social_image")) for m in images], name="social_image")),
                        Label("Default social title", Input(name="social_title", value=config.get("seo_social_title", ""), maxlength=240)),
                        H3("Search engines"),
                        Label(Input(type="checkbox", name="indexing", checked=config.get("seo_indexing", True)), " Allow search engines to index this store"),
                        H3("Robots and sitemap"),
                        P("Generated robots.txt", cls="e-label"), Textarea(robots_content(site, config), readonly=True, rows=7),
                        Label("Custom robots.txt (optional)", Textarea(config.get("seo_robots_override", ""), name="robots_override", rows=7, maxlength=5000)),
                        P("Sitemap: ", A(origin + "/sitemap.xml", href=origin + "/sitemap.xml", target="_blank", rel="noopener")),
                        H3("Search verification"),
                        Label("Google Search Console verification code", Input(name="google_verification", value=config.get("seo_google_verification", ""), maxlength=200)),
                        Label("Bing verification code", Input(name="bing_verification", value=config.get("seo_bing_verification", ""), maxlength=200)),
                        Button("Save draft settings", name="action", value="draft", cls="e-button"),
                        Button("Publish settings", name="action", value="publish", cls="e-button"),
                        Script(NotStr("""document.addEventListener('input',e=>{if(e.target.name!=='title_format')return;const store=document.querySelector('[data-seo-store]').value;document.querySelector('[data-seo-preview]').textContent=e.target.value.replaceAll('{page}','Example page').replaceAll('{store}',store)})""")),
                        method="post", action=f"/admin/sites/{site.id}/settings/seo", cls="e-form"))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/seo", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=form.get("action") == "publish")
                if str(site.version) != str(form.get("version")):
                    raise CommerceError("Settings changed in another window. Reload before saving.")
                site = builder.lock_site(db, site.id, user_id, site.version)
                before = builder.snapshot(db, site)
                config = copy.deepcopy(site.settings_json or {})
                config.update({"seo_title_format": str(form.get("title_format", "")),
                    "seo_default_description": str(form.get("default_description", "")),
                    "seo_social_image": validate_site_image(db, site, str(form.get("social_image", ""))),
                    "seo_social_title": str(form.get("social_title", "")),
                    "seo_indexing": form.get("indexing") == "on",
                    "seo_robots_override": str(form.get("robots_override", "")),
                    "seo_google_verification": str(form.get("google_verification", "")),
                    "seo_bing_verification": str(form.get("bing_verification", ""))})
                config = validate(config)
                content.validate_media_ownership(db, site, {"seo_social_image": config["seo_social_image"]})
                home = db.scalar(select(SitePage).where(SitePage.site_id == site.id,
                    SitePage.tenant_id == site.tenant_id, SitePage.path == "/"))
                if home:
                    doc = copy.deepcopy(home.draft_json or {})
                    doc["seo_title"] = str(form.get("home_title", ""))[:240]
                    doc["seo_description"] = str(form.get("home_description", ""))[:320]
                    home.draft_json = doc
                    if form.get("action") == "publish":
                        home.published_json = copy.deepcopy(doc)
                site.settings_json = config
                site.version += 1
                if form.get("action") == "publish":
                    site.published_settings_json = copy.deepcopy(config)
                db.flush()
                builder.record_change(db, site, user_id, before, "classical", "SEO settings")
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/settings/seo?notice=Settings+saved", status_code=303)
        except CommerceError as exc:
            return error(exc)

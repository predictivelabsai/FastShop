"""Merchant SEO settings, page SEO, SEO health and per-store crawler documents."""

import copy
from xml.sax.saxutils import escape

from fasthtml.common import (
    H3,
    A,
    Article,
    Button,
    Div,
    Form,
    Input,
    Label,
    P,
    Select,
    Small,
    Span,
    Textarea,
)
from sqlalchemy import select
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app import content
from app import site_builder_services as builder
from app.admin_ui import card, page_header
from app.db import SessionLocal
from app.models import Product, Site, SitePage
from app.services import CommerceError
from app.site_seo import (
    DEFAULT_TITLE_FORMAT,
    STORE_KEYS,
    edit_href,
    health_report,
    robots_content,
    site_origin,
    validate,
    validate_site_image,
)


def _home(db, site):
    return db.scalar(select(SitePage).where(SitePage.site_id == site.id,
        SitePage.tenant_id == site.tenant_id, SitePage.path == "/"))


def _images(db, site):
    from app.site_media import media_for

    return [m for m in media_for(db, site) if m.content_type.startswith("image/")]


def publish_seo_settings(draft: dict, published: dict) -> dict:
    """Copy only the store SEO keys live; other draft settings stay in the draft."""
    result = copy.deepcopy(published or {})
    for key in STORE_KEYS:
        if key in (draft or {}):
            result[key] = copy.deepcopy(draft[key])
    return result


def register_site_seo_routes(rt, actor, csrf, check_csrf, shell, error):
    def base(site):
        return f"/admin/sites/{site.id}"

    @rt("/admin/sites/{site_id}/settings/seo", methods=["GET"])
    def seo_settings(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                config = site.settings_json or {}
                home = _home(db, site)
                from app.site_blocks import default_locale, resolve_document
                from app.site_seo_ui import image_options, seo_home_preview

                home_doc = resolve_document(home.draft_json, default_locale(site)) if home else {}
                origin = site_origin(site)
                stored_format = config.get("seo_title_format", "")
                return shell(
                    "Search engine optimisation",
                    page_header("Search engine optimisation", back_href=base(site) + "/settings",
                                secondary_actions=[A("SEO health", href=base(site) + "/settings/seo/health",
                                                     cls="a-button")]),
                    Form(
                        csrf(session), Input(type="hidden", name="version", value=site.version),
                        card(
                            P("How your store appears in search results and when it is shared. "
                              "Save keeps changes in your draft; publish makes them live.", cls="c-muted"),
                            seo_home_preview(
                                site,
                                home_doc.get("seo_title", ""),
                                home_doc.get("seo_description", ""),
                                page_title=home_doc.get("title", ""),
                                default_description=(home_doc.get("description")
                                                     or config.get("seo_default_description", "")),
                            ) if home else P("Add a home page to set its search listing."),
                            title="Search appearance",
                        ),
                        card(
                            Label("Title format for other pages", Input(
                                name="title_format", value=stored_format, maxlength=200,
                                placeholder="{page} | {store}", data_seo_format_input="",
                                data_default_format=DEFAULT_TITLE_FORMAT)),
                            Small("Use {page} for the page title and {store} for the store name. Leave empty "
                                  "to show the page title followed by the store name.", cls="c-muted"),
                            P("Example: ", Span(
                                (stored_format or DEFAULT_TITLE_FORMAT).replace("{page}", "Example page")
                                .replace("{store}", site.name),
                                data_seo_format_preview="", data_store=site.name,
                                data_default_format=DEFAULT_TITLE_FORMAT)),
                            Label("Default meta description", Textarea(
                                config.get("seo_default_description", ""), name="default_description",
                                rows=3, maxlength=320)),
                            Small("Used by pages that have no description of their own.", cls="c-muted"),
                            title="Titles and descriptions",
                        ),
                        card(
                            Label("Default social sharing image", Select(
                                *image_options(site, _images(db, site), config.get("seo_social_image", ""),
                                               "No image"), name="social_image")),
                            Label("Home page social title", Input(
                                name="social_title", value=config.get("seo_social_title", ""), maxlength=240)),
                            Small("Shown when the home page is shared. Other pages use their own title.",
                                  cls="c-muted"),
                            title="Social sharing",
                        ),
                        card(
                            Label(Input(type="checkbox", name="indexing", checked=config.get("seo_indexing", True)),
                                  " Allow search engines to index this store"),
                            H3("Robots and sitemap"),
                            Label("Generated robots.txt", Textarea(robots_content(site, config), readonly=True,
                                                                   rows=7)),
                            Label("Custom robots.txt (optional)", Textarea(
                                config.get("seo_robots_override", ""), name="robots_override", rows=7,
                                maxlength=5000)),
                            P("Sitemap: ", A(origin + "/sitemap.xml", href=origin + "/sitemap.xml",
                                             target="_blank", rel="noopener")),
                            H3("Search verification"),
                            Label("Google Search Console verification code", Input(
                                name="google_verification", value=config.get("seo_google_verification", ""),
                                maxlength=200)),
                            Label("Bing verification code", Input(
                                name="bing_verification", value=config.get("seo_bing_verification", ""),
                                maxlength=200)),
                            title="Search engines",
                        ),
                        Div(Button("Save draft", name="action", value="draft", cls="a-button"),
                            Button("Publish settings", name="action", value="publish",
                                   cls="a-button a-button-primary"), cls="a-actions"),
                        method="post", action=base(site) + "/settings/seo", cls="e-form cg-form",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/seo", methods=["POST"])
    async def seo_settings_post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            action = str(form.get("action", "draft"))
            if action not in {"draft", "publish"}:
                raise CommerceError("Choose save draft or publish settings.")
            publish = action == "publish"
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=publish)
                if str(site.version) != str(form.get("version")):
                    raise CommerceError("Settings changed in another window. Reload before saving.")
                site = builder.lock_site(db, site.id, user_id, site.version)
                before = builder.snapshot(db, site)
                config = copy.deepcopy(site.settings_json or {})
                config.update({
                    "seo_title_format": str(form.get("title_format", "")),
                    "seo_default_description": str(form.get("default_description", "")),
                    "seo_social_image": validate_site_image(db, site, str(form.get("social_image", ""))),
                    "seo_social_title": str(form.get("social_title", "")),
                    "seo_indexing": form.get("indexing") == "on",
                    "seo_robots_override": str(form.get("robots_override", "")),
                    "seo_google_verification": str(form.get("google_verification", "")),
                    "seo_bing_verification": str(form.get("bing_verification", "")),
                })
                config = validate(config)
                content.validate_media_ownership(db, site, {"seo_social_image": config["seo_social_image"]})
                home = _home(db, site)
                if home and ("home_title" in form or "home_description" in form):
                    from app.site_seo import validate_entity_fields

                    values = validate_entity_fields(db, site, {
                        "seo_title": str(form.get("home_title", "")),
                        "seo_description": str(form.get("home_description", "")),
                    })
                    document = copy.deepcopy(home.draft_json or {})
                    if any(document.get(key, "") != value for key, value in values.items()):
                        document.update(values)
                        content.save_page(db, site, home.id, user_id, document, home.version, "draft")
                    if publish and home.published_json is not None:
                        # Only the search listing goes live, never the rest of the home page draft.
                        live = copy.deepcopy(home.published_json)
                        live.update(values)
                        home.published_json = live
                site.settings_json = config
                site.version += 1
                if publish:
                    site.published_settings_json = publish_seo_settings(config, site.published_settings_json)
                db.flush()
                builder.record_change(db, site, user_id, before, "classical", "SEO settings")
                db.commit()
            notice = "SEO+settings+published" if publish else "SEO+settings+saved"
            return RedirectResponse(f"/admin/sites/{site_id}/settings/seo?notice={notice}", status_code=303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/settings/seo/health", methods=["GET"])
    def seo_health(session, site_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                rows = [row for row in health_report(db, site) if row["issues"]]
                entries = [
                    Article(
                        Div(Small(row["kind"].replace("_", " ").title()), H3(row["title"]), P(row["url"]),
                            P(*[Span(issue["label"], cls="a-seo-issue") for issue in row["issues"]])),
                        A("Edit SEO", href=row["edit_href"], cls="a-button"),
                        cls="a-seo-health-row",
                    )
                    for row in rows
                ]
                return shell(
                    "SEO health",
                    page_header("SEO health", back_href=base(site) + "/settings/seo"),
                    P("Recommendations for published pages and catalogue entries. They are advice, "
                      "not errors: fix the ones that matter for your store.", cls="c-muted"),
                    card(*(entries or [P("No SEO issues found for published content.")]),
                         title=f"Published content needing review ({len(rows)})"),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/pages/{page_id}/seo", methods=["GET"])
    def page_seo(session, site_id: str, page_id: str):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                page = content.site_page(db, site, page_id)
                if page.product_id or page.collection_id:
                    return RedirectResponse(edit_href(site, page), status_code=303)
                from app.site_blocks import default_locale, resolve_document
                from app.site_seo_ui import seo_editor_fields

                document = resolve_document(page.draft_json, default_locale(site))
                return shell(
                    "Page SEO",
                    page_header(f"Search listing: {page.title}", back_href=base(site) + "/content/pages",
                                secondary_actions=[A("Open in editor", href=f"{base(site)}/editor?page={page.id}",
                                                     cls="a-button")]),
                    Form(
                        csrf(session),
                        Input(type="hidden", name="version", value=page.version),
                        Input(type="hidden", name="site_version", value=site.version),
                        card(
                            seo_editor_fields(
                                site,
                                title=document.get("title", page.title),
                                path=page.path,
                                seo_title=document.get("seo_title", ""),
                                seo_description=document.get("seo_description", ""),
                                social_title=document.get("seo_social_title", ""),
                                social_image=document.get("seo_social_image", ""),
                                noindex=document.get("seo_noindex", False),
                                images=_images(db, site),
                                default_description=(document.get("description")
                                    or (site.settings_json or {}).get("seo_default_description", "")),
                                home=page.path == "/",
                            ),
                            title="Search engine listing",
                        ),
                        Div(Button("Save draft", name="action", value="draft", cls="a-button"),
                            Button("Publish SEO", name="action", value="publish",
                                   cls="a-button a-button-primary"), cls="a-actions"),
                        method="post", action=f"{base(site)}/content/pages/{page.id}/seo", cls="e-form cg-form",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/pages/{page_id}/seo", methods=["POST"])
    async def page_seo_post(session, request, site_id: str, page_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            action = str(form.get("action", "draft"))
            if action not in {"draft", "publish"}:
                raise CommerceError("Choose save draft or publish SEO.")
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id, publish=action == "publish")
                if str(site.version) != str(form.get("site_version")):
                    raise CommerceError("Site content changed in another window. Reload before saving.")
                site = builder.lock_site(db, site.id, user_id, site.version)
                before = builder.snapshot(db, site)
                page = content.site_page(db, site, page_id)
                if page.product_id or page.collection_id:
                    raise CommerceError("Edit catalogue SEO from the product or collection page.")
                try:
                    version = int(form.get("version", -1))
                except ValueError as exc:
                    raise CommerceError("Reload the page and try again.") from exc
                from app.site_seo import validate_entity_fields

                values = validate_entity_fields(db, site, {
                    "seo_title": str(form.get("seo_title", "")),
                    "seo_description": str(form.get("seo_description", "")),
                    "seo_social_title": str(form.get("seo_social_title", "")),
                    "seo_social_image": str(form.get("seo_social_image", "")),
                    "seo_noindex": form.get("seo_noindex") == "on",
                })
                document = copy.deepcopy(page.draft_json or {})
                document.update(values)
                content.save_page(db, site, page.id, user_id, document, version, "draft")
                published_live = page.published_json is not None
                if action == "publish" and published_live:
                    # Only the search listing goes live, never the rest of the page draft.
                    published = copy.deepcopy(page.published_json)
                    published.update(values)
                    page.published_json = published
                site.version += 1
                builder.record_change(db, site, user_id, before, "classical", "Page SEO")
                db.commit()
            notice = ("SEO+saved.+Publish+the+page+to+make+it+live" if action == "publish" and not published_live
                      else "SEO+published" if action == "publish" else "SEO+saved")
            return RedirectResponse(f"/admin/sites/{site_id}/content/pages/{page_id}/seo?notice={notice}",
                                    status_code=303)
        except CommerceError as exc:
            return error(exc)

    def public_site(db, slug):
        site = db.scalar(select(Site).where(Site.slug == slug))
        return site if site and site.status in {"preview", "published"} else None

    @rt("/sites/{site_slug}/robots.txt", methods=["GET"])
    def robots(site_slug: str):
        with SessionLocal() as db:
            site = public_site(db, site_slug)
            if not site:
                return PlainTextResponse("", status_code=404)
            if site.status != "published":
                return PlainTextResponse("User-agent: *\nDisallow: /\n")
            return PlainTextResponse(robots_content(site, site.published_settings_json or {}))

    @rt("/sites/{site_slug}/sitemap.xml", methods=["GET"])
    def sitemap(site_slug: str):
        from app.site_blocks import default_locale, resolve_document
        from app.site_blog import is_listed
        from app.site_seo import metadata, seo_document

        empty = ('<?xml version="1.0" encoding="UTF-8"?><urlset '
                 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>')
        with SessionLocal() as db:
            site = public_site(db, site_slug)
            if not site:
                return Response("", status_code=404, media_type="application/xml")
            if site.status != "published":
                return Response(empty, media_type="application/xml")
            origin = site_origin(site)
            urls = []
            for page in db.scalars(select(SitePage).where(
                    SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
                    SitePage.published_json.is_not(None)).order_by(SitePage.path)):
                if not page.published_json:
                    continue
                if page.product_id:
                    product = db.scalar(select(Product).where(
                        Product.id == page.product_id, Product.tenant_id == site.tenant_id,
                        Product.status == "active", Product.is_published.is_(True)))
                    if not product:
                        continue
                if page.collection_id:
                    from app.models import Collection
                    if not db.scalar(select(Collection.id).where(
                            Collection.id == page.collection_id, Collection.tenant_id == site.tenant_id,
                            Collection.is_published.is_(True))):
                        continue
                if page.kind == "article" and not is_listed(page):
                    continue
                document = seo_document(db, site, page, resolve_document(page.published_json,
                                                                        default_locale(site)))
                if metadata(site, page, document, origin + page.path)["noindex"]:
                    continue
                lastmod = page.updated_at.date().isoformat() if page.updated_at else ""
                urls.append("<url><loc>" + escape(origin + page.path) + "</loc>"
                            + ("<lastmod>" + lastmod + "</lastmod>" if lastmod else "") + "</url>")
            body = ('<?xml version="1.0" encoding="UTF-8"?><urlset '
                    'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(urls) + "</urlset>")
            return Response(body, media_type="application/xml")

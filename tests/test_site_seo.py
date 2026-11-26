"""Store SEO: head output, page and catalogue metadata, crawler documents and health advice."""

import copy
import json
import re
from types import SimpleNamespace

import pytest
from fasthtml.common import Div, Input, fast_app, to_xml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import catalog_admin, content, site_seo_routes
from app.models import (
    Base,
    Membership,
    Product,
    ProductVariant,
    SiteMedia,
    SitePage,
    User,
    VariantChannelListing,
)
from app.services import CommerceError
from app.site_seo import (
    DEFAULT_TITLE_FORMAT,
    health_report,
    metadata,
    robots_content,
    seo_document,
    validate,
)
from app.site_ui import storefront
from tests.test_catalog_admin_routes import catalogue  # noqa: F401
from tests.test_growth_content import content_client, workspace  # noqa: F401


@pytest.fixture
def store():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="seo-owner@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Seo Shop", "seo-shop")
        for page in content.site_pages(db, site):
            page.published_json = copy.deepcopy(page.draft_json)
        site.status = "published"
        db.commit()
        yield db, owner, site
    engine.dispose()


def render(db, site, path="/", *, preview=False, editor=False, canonical=None):
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == path))
    nodes = storefront(db, site, page, f"/sites/{site.slug}", "csrf",
                       canonical or f"https://platform.test/sites/{site.slug}{path}",
                       preview=preview, editor=editor)
    return "".join(str(to_xml(node)) for node in nodes if node is not None)


def head_tag(markup, pattern):
    match = re.search(pattern, markup)
    return match.group(1) if match else None


def jsonld(markup):
    return [json.loads(item) for item in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', markup, re.S)]


def media(db, site, name="share.png"):
    image = SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title="Share image", alt="Share image",
                      content_type="image/png", storage_key=name, size=1, data=b"x")
    db.add(image)
    db.flush()
    return image, f"/site-media/{site.id}/{image.id}"


def test_head_without_seo_settings_matches_the_storefront_fallbacks(store):
    db, _, site = store
    home = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
    markup = render(db, site)
    title = home.published_json["title"]
    assert f"<title>{title} \u2014 Seo Shop</title>" in markup
    description = home.published_json.get("description", "")
    assert f'<meta name="description" content="{description}">' in markup
    assert '<meta name="robots" content="index,follow">' in markup
    assert f'<meta property="og:title" content="{title}">' in markup
    assert f'<link rel="canonical" href="https://platform.test/sites/{site.slug}/">' in markup
    # Nothing new appears until the merchant sets up SEO.
    for absent in ('og:type', 'twitter:card', 'og:image', 'google-site-verification', 'msvalidate.01',
                   'application/ld+json'):
        assert absent not in markup
    assert DEFAULT_TITLE_FORMAT == "{page} \u2014 {store}"


def test_head_with_seo_settings_adds_social_tags_verification_and_structured_data(store):
    db, _, site = store
    _, image_url = media(db, site)
    site.hostname = "seo.example.test"
    site.published_settings_json = (site.published_settings_json or {}) | validate({
        "seo_title_format": "{page} | {store}", "seo_default_description": "Store default",
        "seo_social_image": image_url, "seo_social_title": "Share the shop",
        "seo_google_verification": "verify_123", "seo_bing_verification": "bing-123",
    })
    db.commit()
    markup = render(db, site, canonical="https://wrong.test/sites/seo-shop/")
    assert '<meta property="og:type" content="website">' in markup
    assert '<meta name="twitter:card" content="summary_large_image">' in markup
    assert f'<meta property="og:image" content="https://seo.example.test{image_url}">' in markup
    assert '<meta property="og:title" content="Share the shop">' in markup
    assert '<meta name="google-site-verification" content="verify_123">' in markup
    assert '<meta name="msvalidate.01" content="bing-123">' in markup
    assert '<link rel="canonical" href="https://seo.example.test/">' in markup
    organisation = next(item for item in jsonld(markup) if item["@type"] == "Organization")
    assert organisation == {"@context": "https://schema.org", "@type": "Organization",
                            "name": "Seo Shop", "url": "https://seo.example.test/"}
    other = next(p for p in content.site_pages(db, site) if p.path != "/" and p.kind == "content")
    page_markup = render(db, site, other.path)
    assert f"<title>{other.title} | Seo Shop</title>" in page_markup


def test_page_noindex_and_store_indexing_switch(store):
    db, _, site = store
    page = next(p for p in content.site_pages(db, site) if p.path != "/" and p.kind == "content")
    page.published_json = copy.deepcopy(page.published_json) | {"seo_noindex": True}
    db.commit()
    assert '<meta name="robots" content="noindex">' in render(db, site, page.path)
    assert '<meta name="robots" content="index,follow">' in render(db, site)
    site.published_settings_json = (site.published_settings_json or {}) | validate({"seo_indexing": False})
    db.commit()
    assert '<meta name="robots" content="noindex">' in render(db, site)
    # Previews stay out of search engines whatever the settings say.
    assert '<meta name="robots" content="noindex,nofollow">' in render(db, site, preview=True)


def test_metadata_precedence_and_store_name_rules(store):
    _, _, site = store
    site.hostname = "canonical.example.test"
    site.published_settings_json = {"seo_title_format": "{page} | {store}",
                                    "seo_default_description": "Default", "seo_indexing": True}
    page = SimpleNamespace(path="/about", kind="content")
    values = metadata(site, page, {
        "title": "About", "seo_title": "Search title", "description": "Page description",
        "seo_description": "Search description", "seo_social_title": "Share title",
        "seo_social_image": "/site-media/store/share.png", "seo_noindex": True,
    }, "https://wrong.test/sites/seo-shop/about")
    assert values["title"] == "Search title | Seo Shop"
    assert values["description"] == "Search description"
    assert values["social_title"] == "Share title"
    assert values["image"] == "https://canonical.example.test/site-media/store/share.png"
    assert values["noindex"] is True and values["og_type"] == "website"
    assert values["canonical"] == "https://canonical.example.test/about"
    assert metadata(site, page, {"title": "About"}, "x")["description"] == "Default"
    named = metadata(site, page, {"title": "About us | Seo Shop"}, "x")
    assert named["title"] == "About us | Seo Shop"
    home = SimpleNamespace(path="/", kind="home")
    assert metadata(site, home, {"title": "Home", "seo_title": "Welcome in"}, "x")["title"] == "Welcome in"
    assert metadata(site, home, {"title": "Home"}, "x")["title"] == "Home | Seo Shop"
    # Without SEO settings the long-standing title is kept exactly, store name and all.
    site.published_settings_json = {}
    assert metadata(site, page, {"title": "Seo Shop"}, "x")["title"] == "Seo Shop \u2014 Seo Shop"


def test_validation_rejects_unsafe_settings_and_robots_output(store):
    _, _, site = store
    site.hostname = "seo.example.test"
    for bad in ({"seo_google_verification": "<script>bad"}, {"seo_bing_verification": "bad token"},
                {"seo_robots_override": "<script>bad</script>"}, {"seo_indexing": "false"},
                {"seo_title_format": "{page} only"}, {"seo_default_description": 5}):
        with pytest.raises(CommerceError):
            validate(bad)
    assert validate({})["seo_title_format"] == ""
    hidden = robots_content(site, {"seo_indexing": False})
    assert "Disallow: /" in hidden and "Sitemap: https://seo.example.test/sitemap.xml" in hidden
    custom = robots_content(site, {"seo_robots_override": "User-agent: *\nAllow: /catalog"})
    assert "Allow: /catalog" in custom and "Sitemap:" in custom
    site.hostname = None
    assert "Disallow: /sites/seo-shop/cart" in robots_content(site, {})


def test_product_and_article_metadata_and_structured_data(store):
    db, _, site = store
    site.published_settings_json = (site.published_settings_json or {}) | validate({})
    product = catalog_admin.create_product(db, site, {
        "name": "Search lamp", "slug": "search-lamp", "status": "active", "is_published": True,
        "seo_title": "Column SEO title", "seo_description": "Column meta description", "options": []})
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.product_id == product.id))
    assert page is not None
    if page.published_json is None:
        page.published_json = copy.deepcopy(page.draft_json)
    variant = db.scalar(select(ProductVariant).where(ProductVariant.product_id == product.id))
    variant.track_inventory = False
    listing = db.scalar(select(VariantChannelListing).where(VariantChannelListing.variant_id == variant.id))
    if listing is None:
        listing = VariantChannelListing(variant_id=variant.id, channel_id=site.channel_id,
                                        price_minor=12345, currency="USD")
        db.add(listing)
    listing.price_minor, listing.currency = 12345, "USD"
    db.commit()
    document = seo_document(db, site, page, page.published_json)
    values = metadata(site, page, document, "https://x.test" + page.path)
    assert values["title"] == "Column SEO title \u2014 Seo Shop"
    assert values["description"] == "Column meta description" and values["og_type"] == "product"
    data = jsonld(render(db, site, page.path))
    offers = next(item for item in data if item["@type"] == "Product")["offers"]
    assert {"@type": "Offer", "url": f"https://platform.test/sites/{site.slug}{page.path}", "price": "123.45",
            "priceCurrency": "USD", "availability": "https://schema.org/InStock"} in offers
    assert any(item["@type"] == "BreadcrumbList" for item in data)

    article = content.create_page(db, site, "SEO article", "/blog/seo-article", "article")
    document = copy.deepcopy(article.draft_json)
    document["seo_title"] = "</script><script>alert(1)</script>"
    document["blog"] = document["blog"] | {"state": "published", "author_name": "Editor"}
    article.published_json = document
    db.commit()
    markup = render(db, site, article.path)
    assert "</script><script>alert(1)" not in "".join(
        re.findall(r'<script type="application/ld\+json">(.*?)</script>', markup, re.S))
    headline = next(item for item in jsonld(markup) if item["@type"] == "Article")
    assert headline["headline"] == "</script><script>alert(1)</script>"
    assert headline["author"] == {"@type": "Person", "name": "Editor"}


def test_health_report_lists_issues_and_stays_site_scoped(store):
    db, owner, site = store
    other = content.create_site(db, owner.id, "Other shop", "other-seo-shop")
    site.published_settings_json = {"seo_indexing": True, "seo_title_format": "{page} | {store}"}
    repeated = "Long SEO title " + "x" * 60
    pages = []
    for index, values in enumerate(({"seo_title": repeated, "seo_description": "d" * 161, "seo_noindex": True},
                                    {"seo_title": repeated, "seo_description": ""},
                                    {"seo_title": "", "seo_description": ""})):
        page = content.create_page(db, site, f"Page {index}", f"/seo-{index}")
        content.save_page(db, site, page.id, owner.id, copy.deepcopy(page.draft_json) | values,
                          page.version, "publish")
        pages.append(page)
    foreign = content.create_page(db, other, "Private page", "/seo-private")
    content.save_page(db, other, foreign.id, owner.id, foreign.draft_json, foreign.version, "publish")
    rows = {row["id"]: {issue["code"] for issue in row["issues"]} for row in health_report(db, site)}
    assert foreign.id not in rows
    assert {"long_title", "long_description", "noindex", "duplicate_title"} <= rows[pages[0].id]
    assert {"missing_description", "duplicate_title"} <= rows[pages[1].id]
    assert {"missing_title", "missing_description"} <= rows[pages[2].id]
    edit = next(row for row in health_report(db, site) if row["id"] == pages[2].id)["edit_href"]
    assert edit == f"/admin/sites/{site.id}/content/pages/{pages[2].id}/seo"


@pytest.fixture
def seo_admin(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="seo-admin@example.test", name="Owner")
        outsider = User(email="seo-outsider@example.test", name="Outsider")
        editor = User(email="seo-editor@example.test", name="Editor")
        db.add_all([owner, outsider, editor])
        db.flush()
        site = content.create_site(db, owner.id, "Admin Shop", "admin-seo-shop")
        other = content.create_site(db, outsider.id, "Private Shop", "private-seo-shop")
        db.add(Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"))
        for page in content.site_pages(db, site):
            page.published_json = copy.deepcopy(page.draft_json)
        site.status = "published"
        site.hostname = "admin-seo.example.test"
        site.settings_json = (site.settings_json or {}) | {"tagline": "Draft tagline"}
        site.published_settings_json = (site.published_settings_json or {}) | {"tagline": "Live tagline"}
        _, image_url = media(db, site)
        db.commit()
    monkeypatch.setattr(site_seo_routes, "SessionLocal", sessions)
    app, rt = fast_app(secret_key="seo-tests-only")
    # As in app.main, the static file catch-all must not shadow robots.txt and sitemap.xml.
    app.routes[:] = [route for route in app.routes if getattr(route, "path", "") != "/{fname:path}.{ext:static}"]
    state = SimpleNamespace(user=owner.id)

    def actor(session):
        return state.user

    def check_csrf(session, form):
        if form.get("csrf_token") != "seo-csrf":
            raise CommerceError("Your session expired.")

    site_seo_routes.register_site_seo_routes(
        rt, actor, lambda session: Input(type="hidden", name="csrf_token", value="seo-csrf"), check_csrf,
        lambda title, *children: Div(*children), lambda exc: Response(str(exc), status_code=400))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions, site=site, other=other, owner=owner,
                              outsider=outsider, editor=editor, state=state, image_url=image_url)
    engine.dispose()


def settings_form(w, **values):
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        version = site.version
    return {"csrf_token": "seo-csrf", "version": str(version), "title_format": "{page} | {store}",
            "default_description": "Everyday goods", "social_image": w.image_url,
            "social_title": "Share us", "indexing": "on", "robots_override": "",
            "google_verification": "token_1", "bing_verification": "", "home_title": "Admin Shop home",
            "home_description": "The home page", "action": "draft"} | values


def test_seo_settings_page_renders_and_requires_csrf_and_scope(seo_admin):
    w = seo_admin
    path = f"/admin/sites/{w.site.id}/settings/seo"
    page = w.client.get(path)
    assert page.status_code == 200
    for text in ("Search appearance", "Google search preview", "Title format for other pages",
                 "Default meta description", "Social sharing", "https://admin-seo.example.test/sitemap.xml",
                 f'href="/admin/sites/{w.site.id}/settings/seo/health"'):
        assert text in page.text
    # The only dash shown is the storefront's own default title in the live examples.
    assert "\u2014" not in re.sub(r"[^<>\"]*\u2014 Admin Shop", "", page.text.replace(DEFAULT_TITLE_FORMAT, ""))
    assert w.client.post(path, data=settings_form(w, csrf_token="bad")).status_code == 400
    w.state.user = w.outsider.id
    assert w.client.get(path).status_code == 400
    assert w.client.post(path, data=settings_form(w)).status_code == 400
    with w.sessions() as db:
        assert "seo_title_format" not in db.get(type(w.site), w.site.id).settings_json


def test_seo_settings_draft_then_publish_only_promotes_seo_values(seo_admin):
    w = seo_admin
    path = f"/admin/sites/{w.site.id}/settings/seo"
    saved = w.client.post(path, data=settings_form(w), follow_redirects=False)
    assert saved.status_code == 303
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        assert site.settings_json["seo_title_format"] == "{page} | {store}"
        assert "seo_title_format" not in site.published_settings_json
        home = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
        assert home.draft_json["seo_title"] == "Admin Shop home"
        assert "seo_title" not in home.published_json
    # Editors may save drafts but not publish.
    w.state.user = w.editor.id
    assert w.client.post(path, data=settings_form(w, action="publish")).status_code == 400
    assert w.client.post(path, data=settings_form(w), follow_redirects=False).status_code == 303
    w.state.user = w.owner.id
    published = w.client.post(path, data=settings_form(w, action="publish"), follow_redirects=False)
    assert published.status_code == 303
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        live = site.published_settings_json
        assert live["seo_title_format"] == "{page} | {store}" and live["seo_social_image"] == w.image_url
        assert live["seo_google_verification"] == "token_1"
        # A draft header change stays a draft.
        assert live["tagline"] == "Live tagline" and site.settings_json["tagline"] == "Draft tagline"
        home = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
        assert home.published_json["seo_title"] == "Admin Shop home"
    # A stale form is rejected rather than overwriting newer settings.
    stale = settings_form(w)
    stale["version"] = "1"
    assert w.client.post(path, data=stale).status_code == 400
    # Images from another store's library are refused.
    assert w.client.post(path, data=settings_form(w, social_image="/site-media/other/x")).status_code == 400


def test_page_seo_saves_draft_publishes_listing_and_guards_access(seo_admin):
    w = seo_admin
    with w.sessions() as db:
        page = next(p for p in content.site_pages(db, w.site) if p.path != "/" and p.kind == "content")
        page_id = page.id
        other_page_id = content.site_pages(db, w.other)[0].id
    route = f"/admin/sites/{w.site.id}/content/pages/{page_id}/seo"
    editor = w.client.get(route)
    assert editor.status_code == 200
    assert "Google search preview" in editor.text and "/ 60 characters recommended" in editor.text
    assert w.client.get(f"/admin/sites/{w.site.id}/content/pages/{other_page_id}/seo").status_code == 400

    def form(action, **values):
        with w.sessions() as db:
            current = db.get(SitePage, page_id)
            site = db.get(type(w.site), w.site.id)
            return {"csrf_token": "seo-csrf", "version": str(current.version), "site_version": str(site.version),
                    "action": action, "seo_title": "Draft search title", "seo_description": "Draft description",
                    "seo_social_title": "Share title", "seo_social_image": w.image_url, "seo_noindex": "on"} | values

    assert w.client.post(route, data=form("draft", csrf_token="bad")).status_code == 400
    assert w.client.post(route, data=form("draft"), follow_redirects=False).status_code == 303
    with w.sessions() as db:
        page = db.get(SitePage, page_id)
        assert page.draft_json["seo_title"] == "Draft search title" and page.draft_json["seo_noindex"] is True
        assert "seo_title" not in page.published_json
        before_blocks = copy.deepcopy(page.published_json["blocks"])
    w.state.user = w.editor.id
    assert w.client.post(route, data=form("publish")).status_code == 400
    w.state.user = w.owner.id
    assert w.client.post(route, data=form("publish", seo_title="Live title"), follow_redirects=False).status_code == 303
    with w.sessions() as db:
        page = db.get(SitePage, page_id)
        assert page.published_json["seo_title"] == "Live title"
        assert page.published_json["seo_social_image"] == w.image_url
        assert page.published_json["blocks"] == before_blocks


def test_page_seo_for_catalogue_pages_points_to_the_catalogue_editor(seo_admin):
    w = seo_admin
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        product = catalog_admin.create_product(db, site, {"name": "Lamp", "slug": "lamp", "options": []})
        page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.product_id == product.id))
        db.commit()
        page_id, product_id = page.id, product.id
    response = w.client.get(f"/admin/sites/{w.site.id}/content/pages/{page_id}/seo", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/sites/{w.site.id}/products/{product_id}"


def test_seo_health_page_is_scoped(seo_admin):
    w = seo_admin
    path = f"/admin/sites/{w.site.id}/settings/seo/health"
    page = w.client.get(path)
    assert page.status_code == 200 and "Published content needing review" in page.text
    assert "Edit SEO" in page.text
    w.state.user = w.outsider.id
    assert w.client.get(path).status_code == 400


def test_sitemap_and_robots_follow_publication_and_noindex(seo_admin):
    w = seo_admin
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        hidden = next(p for p in content.site_pages(db, site) if p.path != "/" and p.kind == "content")
        hidden.published_json = copy.deepcopy(hidden.published_json) | {"seo_noindex": True}
        hidden_path = hidden.path
        db.commit()
    sitemap = w.client.get(f"/sites/{w.site.slug}/sitemap.xml")
    assert sitemap.status_code == 200
    assert "<loc>https://admin-seo.example.test/</loc>" in sitemap.text
    assert f"https://admin-seo.example.test{hidden_path}<" not in sitemap.text
    robots = w.client.get(f"/sites/{w.site.slug}/robots.txt")
    assert "Sitemap: https://admin-seo.example.test/sitemap.xml" in robots.text
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        site.published_settings_json = site.published_settings_json | {"seo_indexing": False}
        db.commit()
    assert "<url>" not in w.client.get(f"/sites/{w.site.slug}/sitemap.xml").text
    assert "Disallow: /" in w.client.get(f"/sites/{w.site.slug}/robots.txt").text
    with w.sessions() as db:
        db.get(type(w.site), w.site.id).status = "preview"
        db.commit()
    assert w.client.get(f"/sites/{w.site.slug}/robots.txt").text == "User-agent: *\nDisallow: /\n"
    assert w.client.get(f"/sites/{w.other.slug}/sitemap.xml").status_code == 404


def test_catalogue_seo_forms_save_social_fields_on_the_page(catalogue):
    work = catalogue
    base = f"/admin/sites/{work.site.id}"
    with work.sessions() as db:
        _, image_url = media(db, work.site, "catalogue.png")
        db.commit()
    form = work.client.get(base + "/products/new")
    assert form.status_code == 200 and "Google search preview" in form.text
    response = work.client.post(base + "/products/new", data={
        "csrf_token": work.csrf, "name": "SEO product", "slug": "seo-product", "status": "active",
        "published": "on", "seo_title": "Product search title", "seo_description": "Product description",
        "seo_social_title": "Product share title", "seo_social_image": image_url, "seo_noindex": "on",
        "price": "12.34", "sku": "SEO-1", "options_json": "[]", "variants_json": "[]", "images_json": "[]",
        "quantity_" + work.warehouse.id: "2",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    with work.sessions() as db:
        product = db.scalar(select(Product).where(Product.slug == "seo-product"))
        page = db.scalar(select(SitePage).where(SitePage.product_id == product.id))
        assert product.seo_title == "Product search title"
        assert page.draft_json["seo_social_title"] == "Product share title"
        assert page.draft_json["seo_social_image"] == image_url and page.draft_json["seo_noindex"] is True
    collection = work.client.get(base + "/collections/new")
    assert collection.status_code == 200 and "Google search preview" in collection.text


def test_blog_post_seo_form_publishes_document_metadata(content_client):
    client, site_id, engine = content_client
    base = f"/admin/sites/{site_id}/content/blog-posts"
    editor = client.get(base + "/new")
    assert editor.status_code == 200 and "Google search preview" in editor.text
    response = client.post(base + "/new", data={
        "csrf_token": "test-token", "title": "Published article", "path": "/blog/published-article",
        "body": "Article body", "excerpt": "Excerpt", "seo_title": "Article SEO title",
        "seo_description": "Article meta description", "seo_social_title": "Article share title",
        "seo_social_image": "", "seo_noindex": "on", "image": "", "category": "Guides",
        "author": "Store editor", "tags": "guides", "publish_at": "", "action": "publish",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    with Session(engine) as db:
        page = db.scalar(select(SitePage).where(SitePage.site_id == site_id,
                                                SitePage.path == "/blog/published-article"))
        assert page.published_json["seo_title"] == "Article SEO title"
        assert page.published_json["seo_description"] == "Article meta description"
        assert page.published_json["seo_social_title"] == "Article share title"
        assert page.published_json["seo_noindex"] is True

"""Entity SEO metadata, publication, crawler output and advisory health coverage."""

import copy
import json
import re

from fasthtml.common import to_xml
from sqlalchemy import select
from starlette.testclient import TestClient

from app import catalog_admin, content, site_index_routes
from app.models import (
    AgentConversation,
    Collection,
    Product,
    ProductVariant,
    SiteMedia,
    SitePage,
    VariantChannelListing,
)
from app.site_seo import health_report, metadata, seo_document
from app.site_ui import storefront
from app.store_agent_ops import confirm_action, register_tools
from tests.test_catalog_admin_routes import catalogue  # noqa: F401
from tests.test_marketing_hub import marketing_hub  # noqa: F401
from tests.test_store_agent import workspace as agent_workspace  # noqa: F401


def jsonld_from(markup):
    scripts = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', markup, re.S,
    )
    return [json.loads(script) for script in scripts]


def render_storefront(db, site, page):
    nodes = storefront(
        db, site, page, f"/sites/{site.slug}", "seo-test-csrf",
        f"https://{site.hostname}{page.path}",
    )
    return "".join(str(to_xml(node)) for node in nodes if node is not None)


def published_product(db, site):
    product = db.scalar(select(Product).where(
        Product.tenant_id == site.tenant_id,
        Product.status == "active",
        Product.is_published.is_(True),
    ).order_by(Product.name))
    page = db.scalar(select(SitePage).where(
        SitePage.site_id == site.id,
        SitePage.tenant_id == site.tenant_id,
        SitePage.product_id == product.id,
    ))
    return product, page


def test_product_column_values_resolve_before_document_fallback(catalogue):
    with catalogue.sessions() as db:
        site = db.get(type(catalogue.site), catalogue.site.id)
        site.hostname = "seo-depth.example.test"
        product, page = published_product(db, site)
        product.seo_title = "Column SEO title"
        product.seo_description = "Column meta description"
        document = seo_document(db, site, page, page.published_json or page.draft_json)
        result = metadata(site, page, document, "https://fallback.example.test" + page.path)

        assert result["title"] == "Column SEO title | " + site.name
        assert result["description"] == "Column meta description"
        assert result["canonical"] == "https://seo-depth.example.test" + page.path
        assert result["og_type"] == "product"


def test_jsonld_includes_organisation_product_offers_and_breadcrumbs(catalogue):
    with catalogue.sessions() as db:
        site = db.get(type(catalogue.site), catalogue.site.id)
        site.status = "published"
        site.hostname = "seo-depth.example.test"
        site.published_settings_json = (site.published_settings_json or {}) | {
            "seo_indexing": True, "logo": "/site-media/site-logo.svg",
        }
        product, page = published_product(db, site)
        home = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
            SitePage.path == "/",
        ))
        if home.published_json is None:
            home.published_json = home.draft_json
        variant = db.scalar(select(ProductVariant).where(
            ProductVariant.product_id == product.id,
            ProductVariant.tenant_id == site.tenant_id,
            ProductVariant.is_active.is_(True),
        ).order_by(ProductVariant.sort_order))
        listing = db.scalar(select(VariantChannelListing).where(
            VariantChannelListing.variant_id == variant.id,
            VariantChannelListing.channel_id == site.channel_id,
        ))
        if listing is None:
            listing = VariantChannelListing(
                variant_id=variant.id, channel_id=site.channel_id,
                currency="USD", price_minor=12345,
            )
            db.add(listing)
        variant.track_inventory = False
        listing.price_minor = 12345
        listing.currency = "USD"
        db.commit()

        home_data = jsonld_from(render_storefront(db, site, home))
        organisation = next(item for item in home_data if item.get("@type") == "Organization")
        assert organisation["name"] == site.name
        assert organisation["url"] == "https://seo-depth.example.test/"
        assert organisation["logo"] == "/site-media/site-logo.svg"

        product_data = jsonld_from(render_storefront(db, site, page))
        product_jsonld = next(item for item in product_data if item.get("@type") == "Product")
        assert {
            "@type": "Offer",
            "url": "https://seo-depth.example.test" + page.path,
            "price": "123.45",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock",
        } in product_jsonld["offers"]
        assert any(item.get("@type") == "BreadcrumbList" for item in product_data)
        listing.price_minor = 12345
        listing.currency = "JPY"
        db.commit()
        jpy_product = next(
            item for item in jsonld_from(render_storefront(db, site, page))
            if item.get("@type") == "Product"
        )
        assert {offer["price"] for offer in jpy_product["offers"]
                if offer["priceCurrency"] == "JPY"} == {"12345"}

        collection = catalog_admin.save_collection(db, site, {
            "title": "Search collection",
            "handle": "search-collection",
            "kind": "manual",
            "description": "A collection",
            "is_published": True,
            "rules_json": {"match": "all", "rules": []},
            "product_ids": [product.id],
        })
        db.commit()
        collection_page = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.collection_id == collection.id,
        ))
        if collection_page.published_json is None:
            collection_page.published_json = collection_page.draft_json
            db.commit()
        collection_data = jsonld_from(render_storefront(db, site, collection_page))
        assert any(item.get("@type") == "BreadcrumbList" for item in collection_data)


def test_article_jsonld_escapes_script_content_and_has_breadcrumbs(catalogue):
    with catalogue.sessions() as db:
        site = db.get(type(catalogue.site), catalogue.site.id)
        site.status = "published"
        site.hostname = "seo-depth.example.test"
        article = content.create_page(db, site, "SEO article", "/blog/seo-article", "article")
        document = copy.deepcopy(article.draft_json)
        document["seo_title"] = "</script><script>alert(1)</script>"
        document["seo_description"] = "An article description"
        document["blog"] = document["blog"] | {"state": "published", "author_name": "Editor"}
        article.published_json = document
        db.commit()

        markup = render_storefront(db, site, article)
        scripts = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', markup, re.S,
        )
        assert all("</script><script>alert(1)" not in script for script in scripts)
        data = [json.loads(script) for script in scripts]
        article_jsonld = next(item for item in data if item.get("@type") == "Article")
        assert article_jsonld["headline"] == "</script><script>alert(1)</script>"
        assert article_jsonld["author"] == {"@type": "Person", "name": "Editor"}
        assert any(item.get("@type") == "BreadcrumbList" for item in data)
        assert "aggregateRating" not in markup


def test_noindex_meta_and_sitemap_exclude_entity(catalogue, monkeypatch):
    with catalogue.sessions() as db:
        site = db.get(type(catalogue.site), catalogue.site.id)
        site.status = "published"
        site.hostname = "seo-depth.example.test"
        site.published_settings_json = (site.published_settings_json or {}) | {"seo_indexing": True}
        product, page = published_product(db, site)
        document = copy.deepcopy(page.published_json or page.draft_json)
        document["seo_noindex"] = True
        page.published_json = document
        db.commit()

        markup = render_storefront(db, site, page)
        assert 'name="robots" content="noindex"' in markup

        monkeypatch.setattr(site_index_routes, "SessionLocal", catalogue.sessions)
        from fasthtml.common import fast_app

        app, rt = fast_app(secret_key="seo-index-test-only")
        site_index_routes.register_site_index_routes(rt)
        sitemap_route = next(
            route for route in app.routes
            if route.path == "/sites/{site_slug}/sitemap.xml"
        )
        app.router.routes.remove(sitemap_route)
        app.router.routes.insert(0, sitemap_route)
        with TestClient(app) as client:
            sitemap = client.get(f"/sites/{site.slug}/sitemap.xml")
            assert sitemap.status_code == 200
            assert f"<loc>https://seo-depth.example.test{page.path}</loc>" not in sitemap.text

            site.published_settings_json = site.published_settings_json | {"seo_indexing": False}
            db.commit()
            assert "<url>" not in client.get(f"/sites/{site.slug}/sitemap.xml").text


def test_seo_health_lists_issues_and_stays_site_scoped(marketing_hub):
    work = marketing_hub
    with work.sessions() as db:
        site = db.get(type(work.site), work.site.id)
        other_site = db.get(type(work.other_site), work.other_site.id)
        site.published_settings_json = {
            "seo_indexing": True,
            "seo_title_format": "{page} | {store}",
            "seo_default_description": "",
        }
        repeated_title = "Long SEO title " + "x" * 60
        first = content.create_page(db, site, "First page", "/seo-first")
        first_document = copy.deepcopy(first.draft_json) | {
            "seo_title": repeated_title,
            "seo_description": "d" * 161,
            "seo_noindex": True,
        }
        content.save_page(db, site, first.id, work.owner.id, first_document, first.version, "publish")
        second = content.create_page(db, site, "Second page", "/seo-second")
        second_document = copy.deepcopy(second.draft_json) | {
            "seo_title": repeated_title,
            "seo_description": "",
        }
        content.save_page(db, site, second.id, work.owner.id, second_document, second.version, "publish")
        third = content.create_page(db, site, "Third page", "/seo-third")
        third_document = copy.deepcopy(third.draft_json) | {
            "seo_title": "",
            "seo_description": "",
        }
        content.save_page(db, site, third.id, work.owner.id, third_document, third.version, "publish")
        foreign = content.create_page(db, other_site, "Private page", "/seo-private")
        content.save_page(db, other_site, foreign.id, work.other_owner.id,
                          foreign.draft_json, foreign.version, "publish")
        rows = health_report(db, site)
        by_id = {row["id"]: {issue["code"] for issue in row["issues"]} for row in rows}
        first_issues = by_id[first.id]
        second_issues = by_id[second.id]
        third_issues = by_id[third.id]
        assert foreign.id not in by_id
        db.commit()

    assert {"long_title", "long_description", "missing_social_image", "noindex", "duplicate_title"} <= first_issues
    assert {"missing_description", "missing_social_image", "duplicate_title"} <= second_issues
    assert {"missing_title", "missing_description", "missing_social_image"} <= third_issues

    page = work.client.get(f"/admin/sites/{work.site.id}/marketing/seo-health")
    assert page.status_code == 200
    assert "Long SEO title" in page.text and "Third page" in page.text
    assert "Edit SEO" in page.text
    work.state.user_id = work.other_owner.id
    denied = work.client.get(f"/admin/sites/{work.site.id}/marketing/seo-health")
    assert denied.status_code == 400


def test_page_seo_form_saves_draft_and_publishes_metadata(marketing_hub):
    work = marketing_hub
    with work.sessions() as db:
        page = db.scalar(select(SitePage).where(
            SitePage.site_id == work.site.id, SitePage.path == "/",
        ))
        image = SiteMedia(
            tenant_id=work.site.tenant_id,
            site_id=work.site.id,
            title="Search image",
            alt="Search image",
            content_type="image/png",
            storage_key="seo-image.png",
            size=1,
            data=b"x",
        )
        db.add(image)
        db.flush()
        page_id = page.id
        image_url = f"/site-media/{work.site.id}/{image.id}"
        db.commit()
    route = f"/admin/sites/{work.site.id}/content/pages/{page_id}/seo"
    editor = work.client.get(route)
    assert editor.status_code == 200
    assert "Google search preview" in editor.text
    assert "/ 60 characters recommended" in editor.text
    assert "/ 160 characters recommended" in editor.text

    with work.sessions() as db:
        site = db.get(type(work.site), work.site.id)
        page = db.get(SitePage, page_id)
        draft_data = {
            "version": page.version,
            "site_version": site.version,
            "action": "draft",
            "seo_title": "Draft search title",
            "seo_description": "Draft description",
            "seo_social_title": "Draft share title",
            "seo_social_image": image_url,
            "seo_noindex": "on",
        }
    saved = work.client.post(route, data=draft_data, follow_redirects=False)
    assert saved.status_code == 303
    with work.sessions() as db:
        page = db.get(SitePage, page_id)
        assert page.draft_json["seo_title"] == "Draft search title"
        assert page.draft_json["seo_noindex"] is True
        assert page.published_json.get("seo_title") != "Draft search title"
        site = db.get(type(work.site), work.site.id)
        publish_data = {
            "version": page.version,
            "site_version": site.version,
            "action": "publish",
            "seo_title": "Published search title",
            "seo_description": "Published description",
            "seo_social_title": "Published share title",
            "seo_social_image": image_url,
            "seo_noindex": "on",
        }
    published = work.client.post(route, data=publish_data, follow_redirects=False)
    assert published.status_code == 303
    with work.sessions() as db:
        page = db.get(SitePage, page_id)
        assert page.published_json["seo_title"] == "Published search title"
        assert page.published_json["seo_social_title"] == "Published share title"
        assert page.published_json["seo_social_image"] == image_url
        assert page.published_json["seo_noindex"] is True


def test_blog_post_seo_form_publishes_document_metadata(marketing_hub):
    work = marketing_hub
    base = f"/admin/sites/{work.site.id}/content/blog-posts"
    editor = work.client.get(base + "/new")
    assert editor.status_code == 200
    assert "Google search preview" in editor.text
    response = work.client.post(base + "/new", data={
        "title": "Published article",
        "path": "/blog/published-article",
        "body": "Article body",
        "excerpt": "Article excerpt",
        "seo_title": "Published article SEO title",
        "seo_description": "Published article meta description",
        "seo_social_title": "Article share title",
        "seo_social_image": "",
        "seo_noindex": "on",
        "image": "",
        "category": "Guides",
        "author": "Store editor",
        "tags": "guides",
        "publish_at": "",
        "action": "publish",
    }, follow_redirects=False)
    assert response.status_code == 303
    with work.sessions() as db:
        page = db.scalar(select(SitePage).where(
            SitePage.site_id == work.site.id,
            SitePage.path == "/blog/published-article",
        ))
        assert page.published_json["seo_title"] == "Published article SEO title"
        assert page.published_json["seo_description"] == "Published article meta description"
        assert page.published_json["seo_social_title"] == "Article share title"
        assert page.published_json["seo_noindex"] is True


def test_catalogue_seo_forms_save_and_publish_product_and_collection(catalogue):
    work = catalogue
    base = f"/admin/sites/{work.site.id}"
    with work.sessions() as db:
        image = SiteMedia(
            tenant_id=work.site.tenant_id,
            site_id=work.site.id,
            title="Catalogue SEO image",
            alt="Catalogue SEO image",
            content_type="image/png",
            storage_key="catalogue-seo-image.png",
            size=1,
            data=b"x",
        )
        db.add(image)
        db.flush()
        image_url = f"/site-media/{work.site.id}/{image.id}"
        db.commit()

    product_form = work.client.get(base + "/products/new")
    assert product_form.status_code == 200
    assert "Google search preview" in product_form.text
    product_response = work.client.post(base + "/products/new", data={
        "csrf_token": work.csrf,
        "name": "SEO depth product",
        "slug": "seo-depth-product",
        "status": "active",
        "published": "on",
        "seo_title": "Product search title",
        "seo_description": "Product meta description",
        "seo_social_title": "Product share title",
        "seo_social_image": image_url,
        "seo_noindex": "on",
        "price": "12.34",
        "sku": "SEO-DEPTH",
        "options_json": "[]",
        "variants_json": "[]",
        "images_json": "[]",
        "quantity_" + work.warehouse.id: "2",
    }, follow_redirects=False)
    assert product_response.status_code == 303, product_response.text
    with work.sessions() as db:
        product = db.scalar(select(Product).where(
            Product.tenant_id == work.site.tenant_id,
            Product.slug == "seo-depth-product",
        ))
        product_page = db.scalar(select(SitePage).where(
            SitePage.site_id == work.site.id, SitePage.product_id == product.id,
        ))
        assert product.seo_title == "Product search title"
        assert product.seo_description == "Product meta description"
        assert product_page.published_json["seo_social_title"] == "Product share title"
        assert product_page.published_json["seo_social_image"] == image_url
        assert product_page.published_json["seo_noindex"] is True

    collection_form = work.client.get(base + "/collections/new")
    assert collection_form.status_code == 200
    assert "Google search preview" in collection_form.text
    collection_response = work.client.post(base + "/collections/new", data={
        "csrf_token": work.csrf,
        "title": "SEO depth collection",
        "handle": "seo-depth-collection",
        "description": "Collection description",
        "image_url": "",
        "seo_title": "Collection search title",
        "seo_description": "Collection meta description",
        "seo_social_title": "Collection share title",
        "seo_social_image": image_url,
        "seo_noindex": "on",
        "kind": "manual",
        "sort_order": "manual",
        "is_published": "on",
        "product_ids_json": "[]",
        "rules_json": '{"match":"all","rules":[]}',
    }, follow_redirects=False)
    assert collection_response.status_code == 303, collection_response.text
    with work.sessions() as db:
        collection = db.scalar(select(Collection).where(
            Collection.tenant_id == work.site.tenant_id,
            Collection.handle == "seo-depth-collection",
        ))
        collection_page = db.scalar(select(SitePage).where(
            SitePage.site_id == work.site.id,
            SitePage.collection_id == collection.id,
        ))
        assert collection.seo_title == "Collection search title"
        assert collection.seo_description == "Collection meta description"
        assert collection_page.published_json["seo_social_title"] == "Collection share title"
        assert collection_page.published_json["seo_social_image"] == image_url
        assert collection_page.published_json["seo_noindex"] is True


def test_store_agent_entity_seo_uses_confirmation_action(agent_workspace):
    db, user, site = agent_workspace
    page = content.site_pages(db, site)[0]
    page.published_json = copy.deepcopy(page.draft_json)
    conversation = AgentConversation(
        tenant_id=site.tenant_id, site_id=site.id, user_id=user.id,
        title="SEO confirmation",
    )
    db.add(conversation)
    db.flush()
    tool = register_tools(db, site, user.id, conversation.id)["update_entity_seo"]
    pending = tool.handler({
        "entity_type": "page",
        "entity_id": page.id,
        "seo_title": "Confirmed SEO title",
        "seo_description": "Confirmed SEO description",
        "seo_noindex": True,
    })
    assert pending["pending_action"]
    action = confirm_action(db, site, user, pending["pending_action"])
    assert action.status == "confirmed"
    assert page.draft_json["seo_title"] == "Confirmed SEO title"
    assert page.published_json["seo_title"] == "Confirmed SEO title"
    assert page.published_json["seo_noindex"] is True

    product = catalog_admin.create_product(db, site, {"name": "Agent SEO product"})
    product_page = db.scalar(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.product_id == product.id,
    ))
    product_pending = tool.handler({
        "entity_type": "product",
        "entity_id": product.id,
        "seo_social_title": "Confirmed product share title",
        "seo_noindex": True,
    })
    product_action = confirm_action(db, site, user, product_pending["pending_action"])
    assert product_action.status == "confirmed"
    assert product.version == 2
    assert product_page.draft_json["seo_social_title"] == "Confirmed product share title"
    assert product_page.draft_json["seo_noindex"] is True

    collection = catalog_admin.save_collection(db, site, {
        "title": "Agent SEO collection",
        "handle": "agent-seo-collection",
        "kind": "manual",
        "is_published": False,
    })
    collection_page = db.scalar(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.collection_id == collection.id,
    ))
    collection_pending = tool.handler({
        "entity_type": "collection",
        "entity_id": collection.id,
        "seo_social_title": "Confirmed collection share title",
        "seo_noindex": True,
    })
    collection_action = confirm_action(db, site, user, collection_pending["pending_action"])
    assert collection_action.status == "confirmed"
    assert collection.version == 2
    assert collection_page.draft_json["seo_social_title"] == "Confirmed collection share title"
    assert collection_page.draft_json["seo_noindex"] is True

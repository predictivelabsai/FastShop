"""Header, footer and consent copy is merchant-editable through the visual editor."""
import pytest
from fastcore.xml import to_xml
from sqlalchemy import delete, select

from app.models import Site, SiteMenu, SitePage, VariantChannelListing
from app.services import CommerceError
from app.site_block_schema import block_schema_registry
from app.site_editable_settings import apply_shell, publish_shell, shell_fields, shell_values
from app.site_ui import render_section
from tests.test_liquid_storefront import browser  # noqa: F401
from tests.test_site_editor_routes import load, workspace  # noqa: F401
from tests.test_site_seed_liquid import liquid  # noqa: F401

LIQUID = {"theme_pack": "liquid"}
FDA = ("*These statements have not been evaluated by the Food and Drug Administration. "
       "This product is not intended to diagnose, treat, cure, or prevent any disease.")


def test_defaults_fill_absent_paths_only_and_unknown_packs_have_no_fields():
    fields = shell_fields(LIQUID)
    values = shell_values(LIQUID, fields)
    assert values["footer"] == FDA
    assert values["liquid_shell"]["footer"]["newsletterTitle"] == "Notes from the glass"
    portal_fields = [field for field in fields if field.get("group") == "portal"]
    assert portal_fields and {field["id"] for field in portal_fields} >= {
        "liquid_shell.portal.next_order", "liquid_shell.portal.welcome"}
    edited = shell_values({**LIQUID, "footer": "", "liquid_shell": {"offer": {"title": "Edited"}}},
                          fields)
    assert edited["footer"] == ""  # a cleared value is a merchant choice, not a gap
    assert edited["liquid_shell"]["offer"]["title"] == "Edited"
    assert edited["liquid_shell"]["offer"]["cta"] == "Send my code"
    portal_copy = shell_values({**LIQUID, "liquid_shell": {"portal": {"next_order": "Coming soon"}}}, fields)
    assert portal_copy["liquid_shell"]["portal"]["next_order"] == "Coming soon"
    assert shell_fields({"theme_pack": "missing-pack"}) == []
    assert shell_fields({}) == []
    schemas = block_schema_registry()
    hero = {field["id"]: field for field in schemas["liquid_hero"]["fields"]}
    assert hero["disclaimer"]["type"] == "textarea"
    assert hero["disclaimer"]["default"] == FDA
    assert schemas["liquid_reviews"]["defaults"]["sample_note"] == "Sample reviews"


def test_apply_validates_declared_paths_and_keeps_unrelated_settings():
    fields = shell_fields(LIQUID)
    config = {**LIQUID, "design": {"accent": "#123456"}, "liquid_shell": {"offer": {"title": "Old"}}}
    saved = apply_shell(config, {"footer": "New disclaimer", "unknown": "ignored",
        "liquid_shell": {"footer": {"helpLinks": [{"label": "FAQ", "href": "/faq", "hash": ""}],
                                    "payments": "Visa, PayPal"}}}, fields)
    assert saved["footer"] == "New disclaimer" and "unknown" not in saved
    assert saved["design"] == {"accent": "#123456"}
    assert saved["liquid_shell"]["offer"]["title"] == "Old"
    assert saved["liquid_shell"]["footer"]["payments"] == ["Visa", "PayPal"]
    for bad in ({"footer": "x" * 2001},
                {"liquid_shell": {"offer": {"privacyHref": "javascript:alert(1)"}}},
                {"liquid_shell": {"footer": {"payments": "Bitcoin"}}},
                {"liquid_shell": {"footer": {"links": "not a list"}}}):
        with pytest.raises(CommerceError):
            apply_shell(config, bad, fields)
    published = publish_shell(saved, {"design": {"accent": "#000000"}}, fields)
    assert published["footer"] == "New disclaimer"
    assert published["design"] == {"accent": "#000000"}


def test_portal_labels_load_from_liquid_shell_fields():
    from types import SimpleNamespace

    from app.subscription_portal import labels

    site = SimpleNamespace(published_settings_json={"theme_pack": "liquid",
        "liquid_shell": {"portal": {"next_order": "Delivery ahead"}}})
    assert labels(site)["next_order"] == "Delivery ahead"


def test_storefront_renders_edited_shell_and_keeps_banner_without_policy(browser):  # noqa: F811
    client, db, site = browser
    home = client.get("/sites/" + site.slug + "/").text
    assert FDA in home and "Notes from the glass" in home and "Cookie preferences" in home
    site.published_settings_json = apply_shell(site.published_settings_json, {
        "footer": "Edited footer disclaimer.*",
        "liquid_shell": {"footer": {"newsletterTitle": "Edited newsletter"},
                         "labels": {"cookies": "Manage cookies", "copyright": "Edited copyright"}},
        "liquid_consent": {"heading": "Edited banner", "privacy_href": "#"},
    }, shell_fields(site.published_settings_json))
    db.commit()
    home = client.get("/sites/" + site.slug + "/").text
    for text in ("Edited footer disclaimer.*", "Edited newsletter", "Manage cookies",
                 "Edited copyright", "Edited banner"):
        assert text in home
    assert "Notes from the glass" not in home
    assert 'id="h-cookie"' in home and "Privacy policy</a>" not in home


def test_store_pages_fields_render_and_cart_copy_is_editable(browser):  # noqa: F811
    client, db, site = browser
    fields = shell_fields(site.published_settings_json)
    ids = {field["id"] for field in fields}
    assert "liquid_shell.pages.cart_empty" in ids
    assert "liquid_shell.pages.not_found_title" in ids
    site.published_settings_json = apply_shell(site.published_settings_json, {
        "liquid_shell": {"pages": {"cart_empty": "Your edited empty bag message.",
                                    "shop_link": "Browse products",
                                    "subtotal": "Basket total"}}
    }, fields)
    db.commit()
    cart = client.get("/sites/" + site.slug + "/cart").text
    assert "Your edited empty bag message." in cart
    assert "Browse products" in cart
    missing = client.get("/sites/" + site.slug + "/missing").text
    assert "Page not found" in missing


def test_store_pages_customer_and_checkout_copy_defaults_and_overrides(browser):  # noqa: F811
    from app.account_copy import DEFAULT_ACCOUNT_COPY
    client, db, site = browser
    fields = shell_fields(site.published_settings_json)
    values = shell_values(site.published_settings_json, fields)
    defaults = values["liquid_shell"]["pages"]
    assert defaults["not_found_message"] == "The page you were looking for has moved or no longer exists."
    assert defaults["shipping_next"] == "Shipping and destination sales tax are calculated next."
    assert defaults["order_summary"] == "Order summary"
    assert values["account_copy"] == DEFAULT_ACCOUNT_COPY
    assert values["liquid_shell"]["portal"]["calculated_when_prepared"] == "Calculated when prepared"
    edited = apply_shell(site.published_settings_json, {
        "account_copy": {"heading": "Member access", "verification_code": "One-time code"},
        "liquid_shell": {"pages": {"not_found_message": "This page has moved.",
                                      "shipping_next": "Delivery costs appear next."},
                         "portal": {"calculated_when_prepared": "Set before dispatch"}}}, fields)
    site.published_settings_json = edited
    db.commit()
    missing = client.get("/sites/" + site.slug + "/missing").text
    assert "This page has moved." in missing
    import re

    from tests.test_liquid_bundle_commerce import variants_for

    base = "/sites/" + site.slug
    empty_cart = client.get(base + "/cart")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', empty_cart.text)[1]
    variant = variants_for(db, site, "h2-4-you-one")[0]
    client.post(base + "/cart/add", data={"csrf_token": csrf, "variant_id": variant.id,
                                          "quantity": "1", "subscription": "off"})
    cart = client.get(base + "/cart").text
    assert "Delivery costs appear next." in cart
    assert "Member access" in client.get("/sites/" + site.slug + "/account").text


def test_header_logo_and_favicon_are_editable_image_fields(browser):  # noqa: F811
    client, db, site = browser
    fields = shell_fields(LIQUID)
    by_id = {field["id"]: field for field in fields}
    assert by_id["liquid_shell.header.logo"]["type"] == "image"
    assert by_id["liquid_shell.header.logo"]["default"] == "/static/h24you/liquid/logo-clean-light.png"
    assert by_id["liquid_shell.header.favicon"]["type"] == "image"
    assert by_id["liquid_shell.header.favicon"]["default"] == "/static/h24you/liquid/logo-clean-dark.png"
    saved = apply_shell(LIQUID, {"liquid_shell": {"header": {
        "logo": "/site-media/site-1/media-1", "favicon": "/site-media/site-1/media-2"}}}, fields)
    assert saved["liquid_shell"]["header"]["logo"] == "/site-media/site-1/media-1"
    assert saved["liquid_shell"]["header"]["favicon"] == "/site-media/site-1/media-2"
    site.published_settings_json = saved
    db.commit()
    rendered = client.get("/sites/" + site.slug + "/").text
    assert 'src="/site-media/site-1/media-1"' in rendered
    assert 'rel="icon" type="image/png" href="/site-media/site-1/media-2"' in rendered


def test_hero_disclaimer_can_be_edited_and_hidden(browser):  # noqa: F811
    client, db, site = browser
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
    original = page.published_json
    hero = next(block for block in original["blocks"] if block["type"] == "liquid_hero")
    assert hero.get("disclaimer") is None
    default_home = client.get("/sites/" + site.slug + "/").text
    assert FDA in default_home
    page.published_json = {**original, "blocks": [
        ({**block, "disclaimer": "Merchant hero disclaimer."}
         if block.get("type") == "liquid_hero" else block)
        for block in original["blocks"]
    ]}
    db.commit()
    assert "Merchant hero disclaimer." in client.get("/sites/" + site.slug + "/").text
    page.published_json = {**page.published_json, "blocks": [
        ({**block, "disclaimer": ""} if block.get("type") == "liquid_hero" else block)
        for block in page.published_json["blocks"]
    ]}
    db.commit()
    hidden_home = client.get("/sites/" + site.slug + "/").text
    assert 'class="liquid-hero-disclaimer"' not in hidden_home


def test_liquid_review_renderer_uses_edited_accessible_copy(browser):  # noqa: F811
    client, db, site = browser
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
    original = page.published_json
    page.published_json = {**original, "blocks": [
        ({**block, "sample_note": "Customer stories", "carousel_label": "Edited carousel label",
         "previous_label": "Earlier stories", "reviews_label": "Customer feedback"}
         if block.get("type") == "liquid_reviews" else block)
        for block in original["blocks"]
    ]}
    db.commit()
    html = client.get("/sites/" + site.slug + "/").text
    for text in ("Customer stories", "Edited carousel label", "Earlier stories", "Customer feedback"):
        assert text in html


def test_editor_loads_saves_and_publishes_shell(workspace):  # noqa: F811
    w = workspace
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        site.settings_json = site.settings_json | LIQUID
        db.commit()
    loaded = load(w)
    ids = {f["id"] for f in loaded["shell_schema"]["fields"]}
    assert {"footer", "liquid_shell.footer.company", "liquid_consent.heading"} <= ids
    # Existing site values win over pack defaults; absent paths are filled.
    assert loaded["shell_settings"]["footer"] == site.settings_json.get("footer", FDA)
    assert loaded["shell_settings"]["liquid_shell"]["footer"]["helpTitle"] == "Help"
    body = {"shell_settings": {"footer": "Draft miracle cure"}, "version": loaded["site_version"]}
    saved = w.client.put(w.base + "/shell-settings", json=body, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["shell_settings"]["footer"] == "Draft miracle cure"
    assert "miracle" in saved.json()["warnings"][0] and "cure" in saved.json()["warnings"][0]
    assert w.client.put(w.base + "/shell-settings", json=body, headers=w.headers).status_code == 409
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.settings_json["footer"] == "Draft miracle cure"
        assert site.published_settings_json.get("footer") != "Draft miracle cure"
    published = w.client.post(w.base + "/publish", json={"version": loaded["version"],
        "site_version": saved.json()["version"]}, headers=w.headers)
    assert published.status_code == 200, published.text
    with w.sessions() as db:
        assert db.get(Site, w.site.id).published_settings_json["footer"] == "Draft miracle cure"


def test_generic_label_fields_are_exposed_in_store_editor_panels(workspace):  # noqa: F811
    w = workspace
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        site.settings_json = site.settings_json | {"theme_pack": "generic"}
        db.commit()
    loaded = load(w)
    fields = loaded["shell_schema"]["fields"]
    groups = {field["group"] for field in fields if field["id"].startswith("generic_labels.")}
    assert groups == {"header", "footer", "store labels"}
    assert {"generic_labels.price_confirm", "generic_labels.research_all",
            "generic_labels.offer_submit", "generic_labels.footer_help"} <= {
                field["id"] for field in fields}
    assert {"price_confirm", "research_all", "offer_submit", "footer_help"} <= {
        key for key in loaded["shell_settings"]["generic_labels"]}


def test_shell_copy_scan_warns_without_blocking_and_allows_exact_fda_disclaimer():
    from app.compliance import assert_copy_compliant
    from app.site_seed import DISCLAIMER

    assert_copy_compliant("*" + DISCLAIMER)
    assert "cure" in assert_copy_compliant("Our tablets cure everything. " + DISCLAIMER)


def test_page_scan_allows_the_exact_fda_disclaimer_only():
    from app.compliance import scan_document
    from app.site_seed import DISCLAIMER

    clean = {"blocks": [{"id": "a", "type": "text", "body": "*" + DISCLAIMER}]}
    assert scan_document(clean)["banned"] == [] and scan_document(clean)["diseases"] == []
    dirty = {"blocks": [{"id": "a", "type": "text", "body": "It can cure you. " + DISCLAIMER}]}
    assert scan_document(dirty)["banned"] == ["cure"]


def test_generic_shell_labels_default_and_override():
    config = {"theme_pack": "generic"}
    fields = shell_fields(config)
    values = shell_values(config, fields)
    assert values["generic_labels"]["placeholder_image"] == "PLACEHOLDER IMAGE"
    assert values["generic_labels"]["collection_empty"] == "Your collection is coming soon."
    assert values["generic_labels"]["split_video_alt"] == "Placeholder motion study"
    edited = apply_shell(config, {"generic_labels": {"placeholder_image": "Image pending"}}, fields)
    assert shell_values(edited, fields)["generic_labels"]["placeholder_image"] == "Image pending"
    assert {field["group"] for field in fields if field["id"].startswith("generic_labels.")} == {
        "header", "footer", "store labels"}


def test_generic_labels_render_from_shell_settings_across_store_pages(browser, monkeypatch):
    from types import SimpleNamespace

    client, db, site = browser
    config = {key: value for key, value in site.published_settings_json.items()
              if key != "theme_pack"}
    fields = shell_fields({"theme_pack": "generic"})
    labels = {field["id"].split(".", 1)[1]: f"GENERIC_SENTINEL_{field['id'].split('.', 1)[1]}"
              for field in fields}
    site.published_settings_json = {**config, "generic_labels": labels,
        "navigation": [{"label": "Shop", "path": "/shop"}],
        "collections": [{"label": "All", "path": "/shop"}]}
    site.published_settings_json.pop("science_date", None)
    home = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
    document = dict(home.published_json)
    document["blocks"] = list(document.get("blocks", [])) + [
        {"id": "sentinel-facts", "type": "facts", "heading": "Facts", "body": "",
         "items": []},
        {"id": "sentinel-claims", "type": "claims", "heading": "Claims", "body": ""},
        {"id": "sentinel-reviews", "type": "reviews", "heading": "Reviews", "body": ""},
        {"id": "sentinel-empty-products", "type": "products", "heading": "Products",
         "body": "", "category": "no-such-category"},
        {"id": "sentinel-product-cards", "type": "products", "heading": "Products", "body": ""},
        {"id": "sentinel-research", "type": "research", "heading": "Research", "body": "",
         "items": [{"body": "Study text", "url": "https://example.test"}]},
        {"id": "sentinel-team", "type": "team", "heading": "Team", "body": "",
         "items": [{"heading": "A person", "body": "Bio", "image": "", "url": "https://example.test"}]},
        {"id": "sentinel-cta", "type": "text", "heading": "CTA", "body": "",
         "link": "/pages/contact"},
        {"id": "sentinel-hero-video", "type": "hero", "heading": "Video", "body": "",
         "video": "/video.mp4"},
        {"id": "sentinel-split-video", "type": "split", "heading": "Split", "body": "",
         "video": "/video.mp4"},
        {"id": "sentinel-editorial-image", "type": "hero", "heading": "Image", "body": "",
         "image": "/image.jpg"},
        {"id": "sentinel-articles", "type": "articles", "heading": "Articles", "body": ""},
    ]
    home.published_json = document
    product_page = db.scalar(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.path == "/products/hydrogen-tablets"))
    product_page.published_json = {"version": 1, "title": "Product", "blocks": [{
        "id": "generic-product", "type": "product", "heading": "Product", "body": "",
        "image": "/product.jpg", "gallery": ["/gallery.jpg"], "subscription_preview": True,
    }]}
    contact_page = SitePage(
        tenant_id=site.tenant_id, site_id=site.id, path="/pages/contact", title="Contact",
        kind="page", draft_json={"version": 1, "title": "Contact", "blocks": [{"id": "contact", "type": "contact"}]},
        published_json={"version": 1, "title": "Contact", "blocks": [{"id": "contact", "type": "contact"}]},
    )
    db.add(contact_page)
    from app.models import Review
    db.add(Review(tenant_id=site.tenant_id, product_id=product_page.product_id,
                  author_name="Example reviewer", rating=5, title="Example", body="Example",
                  is_approved=True))
    article = SitePage(
        tenant_id=site.tenant_id, site_id=site.id, path="/articles/generic-copy",
        title="Generic copy", kind="article", draft_json={"version": 1, "title": "Generic copy", "blocks": []},
        published_json={"version": 1, "title": "Generic copy", "image": "/article.jpg",
                         "blocks": [], "blog": {"state": "published"}},
    )
    db.add(article)
    db.execute(delete(SiteMenu).where(SiteMenu.site_id == site.id, SiteMenu.tenant_id == site.tenant_id))
    db.add(SiteMenu(tenant_id=site.tenant_id, site_id=site.id, name="header",
        items_json=[{"id": "shop", "label": "Shop", "kind": "page", "path": "/shop"}],
        published_items_json=[{"id": "shop", "label": "Shop", "kind": "page", "path": "/shop"}]))
    db.commit()

    def commerce_config(*_args):
        return SimpleNamespace(subscription_product_ids_json=[product_page.product_id],
                               customer_accounts_enabled=True)

    monkeypatch.setattr("app.commerce.settings_for", commerce_config)
    monkeypatch.setattr("app.commerce.payments_enabled", lambda *_args: True)
    monkeypatch.setattr("app.commerce.payment_mode", lambda *_args: "sandbox")
    rendered = "".join(client.get(f"/sites/{site.slug}{path}").text for path in (
        "/", "/products/hydrogen-tablets", "/pages/contact"))
    monkeypatch.setattr("app.commerce.payments_enabled", lambda *_args: False)
    rendered += client.get(f"/sites/{site.slug}/").text
    rendered += client.get(f"/sites/{site.slug}/products/hydrogen-tablets").text
    monkeypatch.setattr("app.commerce.payments_enabled", lambda *_args: True)
    monkeypatch.setattr("app.commerce.payment_mode", lambda *_args: "live")
    rendered += client.get(f"/sites/{site.slug}/products/hydrogen-tablets").text
    monkeypatch.setattr("app.commerce.payment_mode", lambda *_args: "sandbox")
    original_product_id = product_page.product_id
    product_page.product_id = None
    db.commit()
    rendered += client.get(f"/sites/{site.slug}/products/hydrogen-tablets").text
    product_page.product_id = original_product_id
    db.commit()
    listings = list(db.scalars(select(VariantChannelListing).where(
        VariantChannelListing.channel_id == site.channel_id)))
    listing_values = [{key: getattr(row, key) for key in (
        "variant_id", "channel_id", "currency", "price_minor", "compare_at_minor", "cost_minor")}
        for row in listings]
    db.execute(delete(VariantChannelListing).where(VariantChannelListing.channel_id == site.channel_id))
    db.commit()
    rendered += client.get(f"/sites/{site.slug}/").text
    db.add_all(VariantChannelListing(**value) for value in listing_values)
    db.commit()
    review = db.scalar(select(Review).where(Review.tenant_id == site.tenant_id))
    db.delete(review)
    db.commit()
    rendered += client.get(f"/sites/{site.slug}/").text
    missing = [key for key, sentinel in labels.items() if sentinel not in rendered]
    assert not missing, f"Generic labels were not rendered: {missing}"
    research = {"id": "preview-research", "type": "research", "heading": "Research",
                "body": "", "items": [{"heading": "Preview study", "body": "Preview body",
                                          "url": "https://example.test"}]}
    preview_markup = str(to_xml(render_section(db, site, home, site.published_settings_json,
        research, f"/sites/{site.slug}", "csrf", preview=True)))
    assert 'data-field="items.0.heading"' in preview_markup
    assert 'data-field="items.0.body"' in preview_markup
    product_block = {"id": "preview-product", "type": "product", "heading": "Preview product",
                     "body": "Preview product body", "image": "", "gallery": []}
    product_markup = str(to_xml(render_section(db, site, product_page,
        site.published_settings_json, product_block, f"/sites/{site.slug}", "csrf", preview=True)))
    assert 'data-field="heading"' in product_markup
    assert 'data-field="body"' in product_markup


def test_generic_label_defaults_keep_storefront_copy_byte_identical(browser, monkeypatch):
    from types import SimpleNamespace

    client, db, site = browser
    site.published_settings_json = {
        key: value for key, value in site.published_settings_json.items() if key != "theme_pack"
    }
    site.published_settings_json.pop("science_date", None)
    home = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
    home.published_json = {**home.published_json, "blocks": home.published_json["blocks"] + [
        {"id": "default-products", "type": "products", "heading": "Products", "body": ""},
        {"id": "default-empty", "type": "products", "heading": "Empty", "body": "",
         "category": "no-such-category"},
        {"id": "default-video", "type": "hero", "heading": "Video", "body": "",
         "video": "/video.mp4"},
        {"id": "default-split", "type": "split", "heading": "Split", "body": "",
         "video": "/video.mp4"},
        {"id": "default-editorial-image", "type": "hero", "heading": "Image", "body": "",
         "image": "/image.jpg"},
        {"id": "default-facts", "type": "facts", "heading": "Facts", "body": ""},
        {"id": "default-claims", "type": "claims", "heading": "Claims", "body": ""},
        {"id": "default-reviews", "type": "reviews", "heading": "Reviews", "body": ""},
        {"id": "default-research", "type": "research", "heading": "Research", "body": "",
         "items": [{"body": "Source text", "url": "https://example.test"}]},
        {"id": "default-team", "type": "team", "heading": "Team", "body": "",
         "items": [{"heading": "A person", "body": "Bio", "image": "", "url": "https://example.test"}]},
    ]}
    product_page = db.scalar(select(SitePage).where(
        SitePage.site_id == site.id, SitePage.path == "/products/hydrogen-tablets"))
    original_product_id = product_page.product_id
    product_page.published_json = {"version": 1, "title": "Product", "blocks": [{
        "id": "default-product", "type": "product", "heading": "Product", "body": "",
        "image": "/product.jpg", "gallery": ["/gallery.jpg"], "subscription_preview": True,
    }]}
    contact_page = SitePage(
        tenant_id=site.tenant_id, site_id=site.id, path="/pages/contact", title="Contact",
        kind="page", draft_json={"version": 1, "title": "Contact", "blocks": [{"id": "contact", "type": "contact"}]},
        published_json={"version": 1, "title": "Contact", "blocks": [{"id": "contact", "type": "contact"}]},
    )
    db.add(contact_page)
    db.commit()
    monkeypatch.setattr("app.commerce.settings_for", lambda *_args: SimpleNamespace(
        subscription_product_ids_json=[original_product_id], customer_accounts_enabled=False))
    monkeypatch.setattr("app.commerce.payments_enabled", lambda *_args: True)
    monkeypatch.setattr("app.commerce.payment_mode", lambda *_args: "sandbox")
    pages = "".join(client.get(f"/sites/{site.slug}{path}").text for path in (
        "/", "/products/hydrogen-tablets", "/pages/contact"))
    monkeypatch.setattr("app.commerce.payments_enabled", lambda *_args: False)
    pages += client.get(f"/sites/{site.slug}/products/hydrogen-tablets").text
    product_page.product_id = None
    db.commit()
    pages += client.get(f"/sites/{site.slug}/products/hydrogen-tablets").text
    listings = list(db.scalars(select(VariantChannelListing).where(
        VariantChannelListing.channel_id == site.channel_id)))
    listing_values = [{key: getattr(row, key) for key in (
        "variant_id", "channel_id", "currency", "price_minor", "compare_at_minor", "cost_minor")}
        for row in listings]
    db.execute(delete(VariantChannelListing).where(VariantChannelListing.channel_id == site.channel_id))
    db.commit()
    pages += client.get(f"/sites/{site.slug}/").text
    db.add_all(VariantChannelListing(**value) for value in listing_values)
    db.commit()
    originals = (
        "PLACEHOLDER IMAGE", "Your collection is coming soon.", "Water and bubbles — placeholder video",
        "Placeholder motion study", "Source: ", "hydrogenclinicalresearch.com",
        "Editorial placeholder photography",
        " — figures as of ", "date to be confirmed", "SAMPLE SECTION · NO CUSTOMER TESTIMONIALS",
        "Read original source ↗", "PLACEHOLDER PRODUCT IMAGES", "US price to be confirmed",
        "Price to be confirmed",
        "PLACEHOLDER PRICE · final price and pack size pending", "Choose your option", "Quantity",
        "Purchase option", "One-time purchase", "Subscribe and save 10% · monthly",
        " / box · one-time price", " · draft price", "USD · Sandbox checkout",
        "Shipping and state-specific sales tax are calculated before payment.",
        "Add to cart — coming in Phase 2", "Design preview. Orders and subscriptions are not open yet.",
        "Cookie preferences",
        "A LITTLE SOMETHING TO LOOK FORWARD TO", "Our first-order offer is coming when the shop opens.",
        "Explore", "Here to help", "Terms and Conditions", "Privacy Policy", "FAQ",
        "Returns and Refunds", "© 2026 ", "Email my confirmation link",
    )
    missing = [original for original in originals if original not in pages]
    assert not missing, f"Default storefront copy changed or did not render: {missing}"

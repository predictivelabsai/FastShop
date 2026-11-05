"""Header, footer and consent copy is merchant-editable through the visual editor."""
import pytest
from sqlalchemy import select

from app.models import Site, SitePage
from app.services import CommerceError
from app.site_block_schema import block_schema_registry
from app.site_editable_settings import apply_shell, publish_shell, shell_fields, shell_values
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

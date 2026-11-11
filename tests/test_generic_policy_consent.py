"""Generic storefront policy pages and shared cookie consent copy."""

from app.models import SitePage
from tests.test_liquid_storefront import browser  # noqa: F401
from tests.test_site_seed_liquid import liquid  # noqa: F401


def generic_site(browser):
    client, db, site = browser
    site.published_settings_json = {
        key: value for key, value in site.published_settings_json.items()
        if key != "theme_pack"
    }
    db.execute(SitePage.__table__.delete().where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
        SitePage.path == "/pages/privacy-policy"))
    db.commit()
    return client, db, site


def test_generic_policy_renders_sanitised_content_and_empty_message(browser):
    client, db, site = generic_site(browser)
    site.published_settings_json = site.published_settings_json | {
        "policies": {"privacy": "<h2>Privacy details</h2><p>Stored policy.</p>"
                     "<ul><li>Clear choices</li></ul><script>alert(1)</script>"}
    }
    db.commit()

    response = client.get(f"/sites/{site.slug}/pages/privacy-policy")
    assert response.status_code == 200
    assert "Privacy policy" in response.text
    assert "<h2>Privacy details</h2><p>Stored policy.</p>" in response.text
    assert "<ul><li>Clear choices</li></ul>" in response.text
    assert 'class="h-container h-policy-content"' in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "This policy has not been added yet." not in response.text

    site.published_settings_json = site.published_settings_json | {"policies": {"privacy": ""}}
    db.commit()
    response = client.get(f"/sites/{site.slug}/pages/privacy-policy")
    assert response.status_code == 200
    assert "This policy has not been added yet." in response.text


def test_published_site_page_takes_precedence_over_policy_setting(browser):
    client, db, site = generic_site(browser)
    site.published_settings_json = site.published_settings_json | {
        "policies": {"privacy": "<p>Settings policy.</p>"}
    }
    page = SitePage(tenant_id=site.tenant_id, site_id=site.id,
                    path="/pages/privacy-policy", title="Merchant privacy page",
                    kind="page", draft_json={"title": "Merchant privacy page", "sections": [
                        {"type": "text", "heading": "Privacy", "body": "Published page wins."}
                    ]}, published_json={"title": "Merchant privacy page", "sections": [
                        {"type": "text", "heading": "Privacy", "body": "Published page wins."}
                    ]})
    db.add(page)
    db.commit()

    response = client.get(f"/sites/{site.slug}/pages/privacy-policy")
    assert response.status_code == 200
    assert "Merchant privacy page" in response.text
    assert "Published page wins." in response.text
    assert "Settings policy." not in response.text


def test_generic_banner_uses_shared_edited_copy_and_works_without_policy_link(browser):
    client, db, site = generic_site(browser)
    site.published_settings_json = site.published_settings_json | {
        "policies": {"privacy": ""},
        "liquid_consent": {
            "heading": "A changed heading", "body": "Changed explanation.",
            "accept": "Allow all", "decline": "No thanks", "save": "Keep choices",
            "categories_label": "Cookie types", "essential": "Required storage",
            "preferences": "Remember my settings", "analytics": "Usage data",
            "marketing": "Promotions", "privacy_label": "Read our privacy terms",
            "privacy_href": "/custom/privacy",
        },
    }
    db.commit()

    response = client.get(f"/sites/{site.slug}/")
    assert response.status_code == 200
    for text in ("A changed heading", "Changed explanation.", "Allow all", "No thanks",
                 "Keep choices", "Cookie types", "Required storage", "Remember my settings",
                 "Usage data", "Promotions"):
        assert text in response.text
    assert "Read our privacy terms" not in response.text
    assert 'id="h-cookie"' in response.text
    assert 'hidden' in response.text.split('id="h-cookie"', 1)[0][-100:]

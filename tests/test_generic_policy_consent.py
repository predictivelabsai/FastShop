"""Generic storefront policy pages and shared cookie consent copy."""

import re

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import SitePage
from tests.test_liquid_storefront import browser  # noqa: F401
from tests.test_site_seed_liquid import liquid  # noqa: F401

POLICY_PAGES = (
    ("privacy-policy", "privacy", "Privacy policy"),
    ("terms-and-conditions", "terms", "Terms and conditions"),
    ("returns-and-refunds", "refunds", "Returns and refunds"),
    ("shipping-policy", "shipping", "Shipping policy"),
)

PASTED_POLICY_HTML = (
    '<html><head><style>.MsoNormal { color: red; }</style></head><body><!--StartFragment-->'
    '<div class="WordSection1"><h2 class="MsoTitle" style="color: red">Policy details</h2>'
    '<p class="MsoNormal" style="margin-left: 0">This paragraph was pasted from a document.</p>'
    '<ul class="ListParagraph"><li><p>Keep the receipt</p></li>'
    '<li><p>Contact us within 30 days</p></li></ul>'
    '<p><a href="https://example.test/policy" title="More details">Read the full details</a></p>'
    '</div><script>alert("unsafe")</script></body></html>'
)


def policy_settings_app(db, owner, monkeypatch):
    from fasthtml.common import fast_app

    from app import admin_ui, site_routes

    sessions = sessionmaker(bind=db.bind, expire_on_commit=False)
    for module in (admin_ui, site_routes):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    from app import growth_settings_routes

    monkeypatch.setattr(growth_settings_routes, "SessionLocal", sessions)
    app, rt = fast_app(secret_key="policy-settings-test", default_hdrs=False)
    site_routes.register_site_routes(rt)
    app.add_middleware(admin_ui.AdminContextMiddleware)

    @rt("/_test/policy-login", methods=["GET"])
    def get(session):
        session["user_id"] = owner.id
        return "Signed in"

    return app


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


@pytest.mark.parametrize("theme_pack", [None, "liquid"], ids=["generic", "liquid"])
@pytest.mark.parametrize("slug,policy_key,title", POLICY_PAGES)
def test_policy_pages_render_exactly_one_editable_title_in_both_themes(
    browser, theme_pack, slug, policy_key, title
):
    client, db, site = browser
    settings = dict(site.published_settings_json)
    if theme_pack is None:
        settings.pop("theme_pack", None)
    else:
        settings["theme_pack"] = theme_pack
    settings["policies"] = {
        **(settings.get("policies") or {}),
        policy_key: "<h2>Merchant policy details</h2><p>Editable policy text.</p>",
    }
    db.execute(SitePage.__table__.delete().where(
        SitePage.site_id == site.id,
        SitePage.tenant_id == site.tenant_id,
        SitePage.path.in_(["/pages/" + page_slug for page_slug, _, _ in POLICY_PAGES]),
    ))
    site.published_settings_json = settings
    db.commit()

    response = client.get(f"/sites/{site.slug}/pages/{slug}")

    assert response.status_code == 200
    assert response.text.count("<h1") == 1
    assert f">{title}</h1>" in response.text
    assert "<h2>Merchant policy details</h2><p>Editable policy text.</p>" in response.text


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


@pytest.mark.parametrize("slug,key,title", POLICY_PAGES)
def test_policy_settings_save_publishes_and_renders_pasted_document(
    liquid, monkeypatch, slug, key, title
):
    from starlette.testclient import TestClient

    from app.rich_text import sanitise_rich_text

    db, site, owner = liquid
    db.execute(SitePage.__table__.delete().where(
        SitePage.site_id == site.id,
        SitePage.tenant_id == site.tenant_id,
        SitePage.path.in_(["/pages/" + page_slug for page_slug, _, _ in POLICY_PAGES]),
    ))
    site.status = "published"
    legacy_values = {"refund": "<p>Older returns draft</p>",
                     "shipping_policy": "<p>Older shipping draft</p>"}
    if key == "refunds":
        site.settings_json = {**site.settings_json, "policies": {"refund": legacy_values["refund"]}}
    elif key == "shipping":
        site.settings_json = {
            **site.settings_json, "policies": {"shipping_policy": legacy_values["shipping_policy"]}
        }
    db.commit()

    app = policy_settings_app(db, owner, monkeypatch)
    with TestClient(app) as client:
        client.get("/_test/policy-login")
        settings_page = client.get(f"/admin/sites/{site.id}/settings/policies")
        assert settings_page.status_code == 200
        if key == "refunds":
            assert "Older returns draft" in settings_page.text
        elif key == "shipping":
            assert "Older shipping draft" in settings_page.text
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', settings_page.text)[1]
        payload = {policy_key: "" for _, policy_key, _ in POLICY_PAGES}
        payload.update({"csrf_token": csrf_token, key: PASTED_POLICY_HTML})
        saved = client.post(f"/admin/sites/{site.id}/settings/policies", data=payload,
                            follow_redirects=False)
        assert saved.status_code == 303, saved.text

        with db.no_autoflush:
            db.expire_all()
            expected = sanitise_rich_text(PASTED_POLICY_HTML)
            assert site.settings_json["policies"][key] == expected
            assert site.published_settings_json["policies"][key] == expected

        page = client.get(f"/sites/{site.slug}/pages/{slug}")

    assert page.status_code == 200
    assert f">{title}</h1>" in page.text
    assert 'class="liquid-policy-content"' in page.text
    assert expected in page.text
    assert "<h2>Policy details</h2>" in page.text
    assert "<ul><li><p>Keep the receipt</p></li><li><p>Contact us within 30 days</p></li></ul>" in page.text
    assert '<a href="https://example.test/policy" title="More details">Read the full details</a>' in page.text
    assert "MsoNormal" not in page.text
    assert "alert(\"unsafe\")" not in page.text


def test_policy_settings_reports_when_sanitising_removes_all_content(liquid, monkeypatch):
    from starlette.testclient import TestClient

    db, site, owner = liquid
    app = policy_settings_app(db, owner, monkeypatch)
    with TestClient(app) as client:
        client.get("/_test/policy-login")
        settings_page = client.get(f"/admin/sites/{site.id}/settings/policies")
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', settings_page.text)[1]
        payload = {key: "" for _, key, _ in POLICY_PAGES}
        payload.update({"csrf_token": csrf_token, "privacy": "<script>alert('unsafe')</script>"})
        response = client.post(f"/admin/sites/{site.id}/settings/policies", data=payload)

    assert response.status_code == 400
    assert "could not be saved" in response.text
    assert "no supported text" in response.text
    assert "paragraphs, headings, lists, or links" in response.text

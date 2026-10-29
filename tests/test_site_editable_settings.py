"""Header, footer and consent copy is merchant-editable through the visual editor."""
import pytest

from app.models import Site
from app.services import CommerceError
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
    edited = shell_values({**LIQUID, "footer": "", "liquid_shell": {"offer": {"title": "Edited"}}},
                          fields)
    assert edited["footer"] == ""  # a cleared value is a merchant choice, not a gap
    assert edited["liquid_shell"]["offer"]["title"] == "Edited"
    assert edited["liquid_shell"]["offer"]["cta"] == "Send my code"
    assert shell_fields({"theme_pack": "missing-pack"}) == []
    assert shell_fields({}) == []


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
    body = {"shell_settings": {"footer": "Draft disclaimer"}, "version": loaded["site_version"]}
    saved = w.client.put(w.base + "/shell-settings", json=body, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["shell_settings"]["footer"] == "Draft disclaimer"
    assert w.client.put(w.base + "/shell-settings", json=body, headers=w.headers).status_code == 409
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.settings_json["footer"] == "Draft disclaimer"
        assert site.published_settings_json.get("footer") != "Draft disclaimer"
    published = w.client.post(w.base + "/publish", json={"version": loaded["version"],
        "site_version": saved.json()["version"]}, headers=w.headers)
    assert published.status_code == 200, published.text
    with w.sessions() as db:
        assert db.get(Site, w.site.id).published_settings_json["footer"] == "Draft disclaimer"

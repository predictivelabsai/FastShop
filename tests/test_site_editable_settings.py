"""Theme-pack shell settings are merchant-editable through the visual editor."""
import pytest

from app import site_editable_settings
from app.models import Site
from app.services import CommerceError
from app.site_editable_settings import apply_shell, publish_shell, shell_fields, shell_values
from tests.test_site_editor_routes import load, workspace  # noqa: F401

PACK = {"theme_pack": "shelltest"}
FIELDS = [
    {"id": "footer", "group": "footer", "label": "Footer note", "type": "textarea",
     "default": "Default footer note."},
    {"id": "shelltest.offer.title", "group": "header", "label": "Offer title", "type": "text",
     "default": "Default offer"},
    {"id": "shelltest.offer.cta", "group": "header", "label": "Offer button", "type": "text",
     "default": "Send my code"},
    {"id": "shelltest.offer.href", "group": "header", "label": "Offer link", "type": "url",
     "default": "/offer"},
    {"id": "shelltest.footer.payments", "group": "footer", "label": "Payment marks", "type": "list",
     "choices": ["Visa", "Mastercard", "PayPal"], "default": ["Visa"]},
    {"id": "shelltest.footer.links", "group": "footer", "label": "Footer links", "type": "repeater",
     "default": [], "fields": [{"id": "label", "label": "Label", "type": "text"},
                               {"id": "href", "label": "Link", "type": "url"}]},
]


@pytest.fixture(autouse=True)
def shelltest_pack(monkeypatch):
    monkeypatch.setitem(site_editable_settings._PROVIDERS, "shelltest", lambda: FIELDS)


def test_defaults_fill_absent_paths_only_and_unknown_packs_have_no_fields():
    fields = shell_fields(PACK)
    values = shell_values(PACK, fields)
    assert values["footer"] == "Default footer note."
    assert values["shelltest"]["footer"]["payments"] == ["Visa"]
    edited = shell_values({**PACK, "footer": "", "shelltest": {"offer": {"title": "Edited"}}}, fields)
    assert edited["footer"] == ""  # a cleared value is a merchant choice, not a gap
    assert edited["shelltest"]["offer"]["title"] == "Edited"
    assert edited["shelltest"]["offer"]["cta"] == "Send my code"
    assert shell_fields({"theme_pack": "missing-pack"}) == []
    assert shell_fields({}) == []


def test_apply_validates_declared_paths_and_keeps_unrelated_settings():
    fields = shell_fields(PACK)
    config = {**PACK, "design": {"accent": "#123456"}, "shelltest": {"offer": {"title": "Old"}}}
    saved = apply_shell(config, {"footer": "New note", "unknown": "ignored",
        "shelltest": {"footer": {"links": [{"label": "FAQ", "href": "/faq"}],
                                 "payments": "Visa, PayPal"}}}, fields)
    assert saved["footer"] == "New note" and "unknown" not in saved
    assert saved["design"] == {"accent": "#123456"}
    assert saved["shelltest"]["offer"]["title"] == "Old"
    assert saved["shelltest"]["footer"]["payments"] == ["Visa", "PayPal"]
    assert saved["shelltest"]["footer"]["links"] == [{"label": "FAQ", "href": "/faq"}]
    for bad in ({"footer": "x" * 2001},
                {"shelltest": {"offer": {"href": "javascript:alert(1)"}}},
                {"shelltest": {"footer": {"payments": "Bitcoin"}}},
                {"shelltest": {"footer": {"links": "not a list"}}}):
        with pytest.raises(CommerceError):
            apply_shell(config, bad, fields)
    published = publish_shell(saved, {"design": {"accent": "#000000"}}, fields)
    assert published["footer"] == "New note"
    assert published["design"] == {"accent": "#000000"}


def test_editor_loads_saves_and_publishes_shell(workspace):  # noqa: F811
    w = workspace
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        site.settings_json = site.settings_json | PACK
        db.commit()
    loaded = load(w)
    ids = {f["id"] for f in loaded["shell_schema"]["fields"]}
    assert {"footer", "shelltest.offer.title", "shelltest.footer.links"} <= ids
    # Existing site values win over pack defaults; absent paths are filled.
    assert loaded["shell_settings"]["footer"] == site.settings_json.get("footer", "Default footer note.")
    assert loaded["shell_settings"]["shelltest"]["offer"]["cta"] == "Send my code"
    body = {"shell_settings": {"footer": "Draft note"}, "version": loaded["site_version"]}
    saved = w.client.put(w.base + "/shell-settings", json=body, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["shell_settings"]["footer"] == "Draft note"
    assert w.client.put(w.base + "/shell-settings", json=body, headers=w.headers).status_code == 409
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.settings_json["footer"] == "Draft note"
        assert site.published_settings_json.get("footer") != "Draft note"
    published = w.client.post(w.base + "/publish", json={"version": loaded["version"],
        "site_version": saved.json()["version"]}, headers=w.headers)
    assert published.status_code == 200, published.text
    with w.sessions() as db:
        assert db.get(Site, w.site.id).published_settings_json["footer"] == "Draft note"

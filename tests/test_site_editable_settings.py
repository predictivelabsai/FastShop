"""Theme-pack shell settings are merchant-editable through the visual editor."""
import pytest
from fastcore.xml import to_xml

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
    default_fields = shell_fields({})
    assert {"name", "announcement", "footer"} <= {field["id"] for field in default_fields}
    assert shell_values({})["footer"] == ""


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


def test_default_theme_shell_settings_are_editable(workspace):
    w = workspace
    loaded = load(w)
    ids = {field["id"] for field in loaded["shell_schema"]["fields"]}
    assert {"name", "announcement", "footer", "generic_labels.cta",
            "generic_labels.contact_heading", "generic_labels.card_product_alt",
            "generic_labels.research_read", "generic_labels.editorial_image_alt",
            "account_copy.heading",
            "account_copy.order_summary"} <= ids
    values = {"footer": "A note for visitors.", "announcement": "A store update.",
              "generic_labels": {"contact_heading": "Contact our team"},
              "account_copy": {"heading": "Member sign-in", "order_summary": "Purchase details"}}
    saved = w.client.put(w.base + "/shell-settings", json={
        "shell_settings": values, "version": loaded["site_version"],
    }, headers=w.headers)
    assert saved.status_code == 200, saved.text
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.settings_json["footer"] == "A note for visitors."
        assert site.settings_json["announcement"] == "A store update."
        assert site.settings_json["generic_labels"]["contact_heading"] == "Contact our team"
        assert site.settings_json["account_copy"]["heading"] == "Member sign-in"
        assert site.settings_json["account_copy"]["order_summary"] == "Purchase details"
        assert site.published_settings_json["footer"] != "A note for visitors."
    published = w.client.post(w.base + "/publish", json={
        "version": loaded["version"], "site_version": saved.json()["version"],
    }, headers=w.headers)
    assert published.status_code == 200, published.text
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.published_settings_json["footer"] == "A note for visitors."
        assert site.published_settings_json["announcement"] == "A store update."
        assert site.published_settings_json["generic_labels"]["contact_heading"] == "Contact our team"
        assert site.published_settings_json["account_copy"]["heading"] == "Member sign-in"
        assert site.published_settings_json["account_copy"]["order_summary"] == "Purchase details"


def test_generic_shell_labels_use_defaults_and_merchant_values():
    from app.site_ui import label

    assert label({}, "cta") == "Explore"
    assert label({"generic_labels": {"cta": "Browse items"}}, "cta") == "Browse items"


def test_generic_research_copy_renders_merchant_values(workspace):
    from app.content import site_pages
    from app.site_ui import render_section

    with workspace.sessions() as db:
        site = db.get(Site, workspace.site.id)
        page = site_pages(db, site)[0]
        config = {"generic_labels": {
            "research_all": "Every topic", "research_exercise": "Movement",
            "research_reviews": "Reviews", "research_meta": "Overviews",
            "research_source": "Reference", "research_study": "Untitled reference",
            "research_read": "Open reference",
        }}
        section = {"id": "research-copy", "type": "research", "heading": "References",
                   "items": [{"theme": "Reference", "heading": "Example study",
                              "body": "A neutral summary.", "url": "https://example.test/source"}]}
        markup = to_xml(render_section(db, site, page, config, section, "", "csrf", preview=True))

    assert "Every topic" in markup and "Movement" in markup
    assert "Overviews" in markup and "Open reference" in markup
    assert "Example study" in markup and "A neutral summary." in markup

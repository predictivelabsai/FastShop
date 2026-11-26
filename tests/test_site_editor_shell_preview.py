"""Header and footer in the editor canvas: live draft preview and click-to-open markers."""

import copy
import re
from pathlib import Path

from fasthtml.common import to_xml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import content
from app.models import Base, Site, SitePage, User
from app.site_ui import storefront
from tests.test_site_editor_routes import load, workspace  # noqa: F401

STATIC = Path(__file__).resolve().parents[1] / "static" / "editor"


def markers(html):
    return re.findall(r'data-builder-shell="(header|footer)"', html)


def test_editor_canvas_marks_header_and_footer_for_selection(workspace):
    w = workspace
    loaded = load(w)
    html = w.client.post(w.base + "/render", json={"document": loaded["document"]},
                         headers=w.headers).json()["html"]
    assert markers(html) == ["header", "header", "footer", "footer"]
    assert 'data-builder-label="Store header"' in html and 'data-builder-label="Store footer"' in html
    header = re.search(r'<header[^>]*>', html).group(0)
    assert 'data-builder-shell="header"' in header and 'tabindex="0"' in header


def test_markers_never_reach_the_public_storefront_or_ordinary_previews():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="marker-owner@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Marker shop", "marker-shop")
        site.status = "published"
        for page in content.site_pages(db, site):
            page.published_json = copy.deepcopy(page.draft_json)
        db.commit()
        for page in content.site_pages(db, site):
            for preview in (False, True):
                nodes = storefront(db, site, page, "/sites/marker-shop", "csrf",
                                   "https://example.test" + page.path, preview=preview)
                html = "".join(str(to_xml(node)) for node in nodes if node is not None)
                assert "data-builder-shell" not in html and "data-builder-label=\"Store" not in html
    engine.dispose()


def test_render_applies_unsaved_header_and_footer_values_without_saving(workspace):
    w = workspace
    loaded = load(w)
    with w.sessions() as db:
        before = copy.deepcopy(db.get(Site, w.site.id).settings_json)
    draft = loaded["shell_settings"] | {"announcement": "Typed, not saved", "tagline": "Draft footer line"}
    result = w.client.post(w.base + "/render", json={"document": loaded["document"], "shell_settings": draft},
                           headers=w.headers)
    assert result.status_code == 200, result.text
    html = result.json()["html"]
    assert re.search(r'class="h-announcement"[^>]*>Typed, not saved<', html)
    assert "Draft footer line" in html
    with w.sessions() as db:
        assert db.get(Site, w.site.id).settings_json == before
    # Without draft values the saved settings render.
    plain = w.client.post(w.base + "/render", json={"document": loaded["document"]}, headers=w.headers)
    assert "Typed, not saved" not in plain.json()["html"]
    # A single block render accepts them too, so section previews stay consistent.
    block_id = loaded["document"]["blocks"][0]["id"]
    block = w.client.post(w.base + "/render", json={"document": loaded["document"], "block_id": block_id,
                                                    "shell_settings": draft}, headers=w.headers)
    assert block.status_code == 200, block.text


def test_render_validates_draft_header_and_footer_values(workspace):
    w = workspace
    loaded = load(w)
    for bad in ("not an object", {"announcement": 7}, {"announcement": "x" * 2001},
                {"logo": f"/site-media/{w.other.id}/foreign"}):
        result = w.client.post(w.base + "/render", json={"document": loaded["document"], "shell_settings": bad},
                               headers=w.headers)
        assert result.status_code == 422, bad
    # Scripts in text are escaped like any storefront copy.
    result = w.client.post(w.base + "/render", json={"document": loaded["document"],
                           "shell_settings": {"announcement": "<script>alert(1)</script>"}}, headers=w.headers)
    assert result.status_code == 200
    assert "<script>alert(1)</script>" not in result.json()["html"]
    # The render endpoint still needs the CSRF header.
    assert w.client.post(w.base + "/render", json={"document": loaded["document"],
                         "shell_settings": {}}).status_code == 403


def test_previewing_header_values_then_publishing_a_page_keeps_them_draft(workspace):
    w = workspace
    loaded = load(w)
    with w.sessions() as db:
        published_before = copy.deepcopy(db.get(Site, w.site.id).published_settings_json)
    w.client.post(w.base + "/render", json={"document": loaded["document"],
                  "shell_settings": {"announcement": "Preview only"}}, headers=w.headers)
    published = w.client.post(w.base + "/publish", json={"version": loaded["version"],
                              "site_version": loaded["site_version"]}, headers=w.headers)
    assert published.status_code == 200, published.text
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        assert site.published_settings_json.get("announcement") == published_before.get("announcement")
        assert "Preview only" not in str(site.settings_json)
        page = db.scalar(select(SitePage).where(SitePage.id == w.page.id))
        assert "Preview only" not in str(page.published_json)


def test_editor_scripts_wire_live_shell_preview_undo_and_canvas_selection():
    editor = (STATIC / "editor.js").read_text(encoding="utf-8")
    canvas = (STATIC / "canvas.js").read_text(encoding="utf-8")
    # Typing sends the draft values to the render endpoint and patches only the shell.
    assert "shell_settings: state.shell_settings" in editor
    assert "function patchShell(html)" in editor and "preview(null, true)" in editor
    # History entries carry header and footer values, so undo and redo cover them.
    assert "shell: copy(state.shell_settings || {})" in editor
    assert "state.shell_settings = copy(history[cursor].shell)" in editor
    # The canvas outlines, labels and opens the header and footer, by pointer or keyboard.
    assert "[data-builder-shell]:hover" in canvas and "dataset.builderLabel" in canvas
    assert 'send({ type: "shell", group: node.dataset.builderShell })' in canvas
    assert 'e.key === "Enter" || e.key === " "' in canvas

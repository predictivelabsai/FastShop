"""Layout/media registry, safe input and editor persistence contracts."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fasthtml.common import to_xml

from app.models import SitePage
from app.services import CommerceError
from app.site_block_schema import default_block, editor_schema
from app.site_blocks import normalize_document, validate_block
from app.site_refinement import apply_operation, derive_operations
from app.site_theme import PRESETS
from app.site_ui import render_section, storefront
from app.site_widgets import WIDGET_CSS, WIDGETS
from tests.test_site_editor_blocks import context as context
from tests.test_site_editor_routes import load
from tests.test_site_editor_routes import workspace as workspace

PACK = (
    "slideshow", "image_banner", "before_after", "logo_cloud", "icon_box",
    "image_hotspots", "section_heading", "video_embed",
)


def document(*blocks):
    return {"version": 1, "title": "Your page", "blocks": list(blocks)}


@pytest.mark.parametrize("kind", PACK)
def test_defaults_validate_render_and_keep_inline_hooks(context, kind):
    db, site, page = context
    block = default_block(kind)
    assert validate_block(block) == block
    assert normalize_document(document(block))["blocks"] == [block]
    markup = to_xml(render_section(db, site, page, {}, block, "", "csrf", True))
    assert "fs-widget-" + kind in markup
    assert f'data-block-id="{block["id"]}"' in markup
    if kind not in {"video_embed", "before_after"}:
        assert 'data-field="' in markup
    expected = {
        "slideshow": "data-slideshow", "before_after": "data-compare-range",
        "image_hotspots": "aria-controls", "icon_box": "<svg",
        "section_heading": "<h2", "logo_cloud": "fs-pack-logo-grid",
        "image_banner": "fs-pack-banner", "video_embed": "Choose a YouTube or Vimeo video",
    }
    assert expected[kind] in markup


def test_widget_integer_choice_and_boolean_constraints():
    cases = {
        "slideshow": [("interval", 2), ("interval", 16), ("overlay_opacity", 81),
                      ("height", "huge"), ("text_position", "outside"), ("autoplay", "true")],
        "image_banner": [("overlay_opacity", -1), ("content_position", "outside"),
                         ("text_colour", "red"), ("height", "huge")],
        "before_after": [("split", 9), ("split", 91), ("orientation", "diagonal")],
        "logo_cloud": [("columns", 1), ("columns", 9), ("greyscale", 1)],
        "icon_box": [("columns", 0), ("columns", 5), ("icon_position", "right"),
                     ("alignment", "justify")],
        "section_heading": [("alignment", "justify"), ("level", "script")],
        "video_embed": [("aspect", "21/9")],
    }
    for kind, patches in cases.items():
        for key, value in patches:
            with pytest.raises(CommerceError):
                validate_block(default_block(kind) | {key: value})
    with pytest.raises(CommerceError):
        validate_block(default_block("slideshow") | {"interval": True})
    with pytest.raises(CommerceError):
        validate_block(default_block("section_heading") | {"heading": "x" * 241})


def test_repeater_bounds_item_allowlists_and_coordinate_validation():
    for kind, maximum, minimum in (("slideshow", 8, 1), ("logo_cloud", 24, 0),
                                   ("icon_box", 12, 1), ("image_hotspots", 10, 0)):
        block = default_block(kind)
        entry = block["items"][0]
        validate_block(block | {"items": [copy.deepcopy(entry) for _ in range(maximum)]})
        with pytest.raises(CommerceError):
            validate_block(block | {"items": [entry] * (maximum + 1)})
        with pytest.raises(CommerceError):
            validate_block(block | {"items": [entry | {"unexpected": "value"}]})
        if minimum:
            with pytest.raises(CommerceError):
                validate_block(block | {"items": []})
    spot = default_block("image_hotspots")
    for axis in ("x", "y"):
        for value in (-1, 101, True, "50"):
            with pytest.raises(CommerceError):
                validate_block(spot | {"items": [spot["items"][0] | {axis: value}]})
        for value in (0, 100):
            validate_block(spot | {"items": [spot["items"][0] | {axis: value}]})
    icon = default_block("icon_box")
    with pytest.raises(CommerceError):
        validate_block(icon | {"items": [icon["items"][0] | {"icon": "<svg>"}]})


def test_image_link_safety_and_dynamic_sources():
    for kind in PACK:
        block = default_block(kind)
        schema = editor_schema()[kind]
        for field in schema["fields"]:
            if field["id"].startswith("style."):
                continue
            if field["type"] in {"image", "url"}:
                with pytest.raises(CommerceError):
                    validate_block(block | {field["id"]: "javascript:alert(1)"})
                if field["type"] == "image":
                    validate_block(block | {field["id"]: "metafield:custom.photo"})
            if field["type"] == "repeater":
                for item_field in field["fields"]:
                    if item_field["type"] in {"image", "url"}:
                        entry = block["items"][0] | {item_field["id"]: "javascript:alert(1)"}
                        with pytest.raises(CommerceError):
                            validate_block(block | {"items": [entry]})


def test_video_host_https_and_privacy_conversion(context):
    db, site, page = context
    urls = {
        "https://www.youtube.com/watch?v=abcdefghijk&tracking=discard":
            "https://www.youtube-nocookie.com/embed/abcdefghijk",
        "https://youtu.be/abcdefghijk": "https://www.youtube-nocookie.com/embed/abcdefghijk",
        "https://youtube.com/shorts/abcdefghijk": "https://www.youtube-nocookie.com/embed/abcdefghijk",
        "https://vimeo.com/123456789": "https://player.vimeo.com/video/123456789?dnt=1",
        "https://player.vimeo.com/video/123456789": "https://player.vimeo.com/video/123456789?dnt=1",
    }
    for source, target in urls.items():
        block = validate_block(default_block("video_embed") | {"url": source})
        markup = to_xml(render_section(db, site, page, {}, block, "", "", True))
        assert f'src="{target}"' in markup and "tracking" not in markup
    for source in ("http://youtube.com/watch?v=abcdefghijk", "https://example.test/video",
                   "https://youtube.com.evil.test/watch?v=abcdefghijk", "//youtu.be/abcdefghijk",
                   "https://user:password@vimeo.com/123", "https://vimeo.com/not-a-video"):
        with pytest.raises(CommerceError):
            validate_block(default_block("video_embed") | {"url": source})


def test_existing_widget_schema_snapshot_and_legacy_normalisation():
    schemas = editor_schema()
    snapshots = {
        "heading": [("heading", "text", "Your heading", None, None, None),
                    ("level", "select", "h2", None, None, None),
                    ("alignment", "select", "left", None, None, None)],
        "image_gallery": [("columns", "range", 3, 1, 4, ""),
                          ("gap", "range", 16, 0, 120, "px"),
                          ("items", "repeater", [{"image": "", "alt": "Gallery image", "url": ""}], 0, 200, None)],
    }
    for kind, expected in snapshots.items():
        actual = [tuple(f.get(key) for key in ("id", "type", "default", "min", "max", "unit"))
                  for f in schemas[kind]["fields"] if not f["id"].startswith("style.")]
        assert actual == expected
    legacy = {"title": "Your story", "sections": [{"type": "text", "heading": "Our story", "body": "Content"}]}
    saved = copy.deepcopy(legacy)
    canonical = normalize_document(legacy)
    assert normalize_document(canonical) == canonical and legacy == saved
    existing = document(*(default_block(kind, kind) for kind in WIDGETS if kind not in PACK))
    snapshot = copy.deepcopy(existing)
    assert normalize_document(existing) == snapshot
    assert existing == snapshot


def test_all_pack_widgets_are_allowed_in_containers():
    container = default_block("container") | {"children": [default_block(kind) for kind in PACK]}
    assert validate_block(container) == container
    assert set(PACK) <= set(editor_schema()["container"]["allowed_children"])


def test_storefront_presets_and_conditional_progressive_assets(context):
    db, site, page = context
    original_settings = site.settings_json
    for preset in ("original", "warm"):
        site.settings_json = original_settings | {"design": PRESETS[preset]}
        page.draft_json = document(*(default_block(kind) for kind in PACK))
        markup = to_xml(storefront(db, site, page, "", "csrf", "/your-page", preview=True))
        for kind in PACK:
            assert "fs-widget-" + kind in markup
        assert "widgets-pack1.js" in markup and "widgets-pack1.css" in markup
        assert "--store-accent:" + PRESETS[preset]["accent"] in markup
    page.draft_json = document(default_block("section_heading"))
    static_markup = to_xml(storefront(db, site, page, "", "csrf", "/your-page", preview=True))
    assert "widgets-pack1.css" in static_markup and "widgets-pack1.js" not in static_markup
    page.draft_json = document(default_block("heading"))
    legacy_markup = to_xml(storefront(db, site, page, "", "csrf", "/your-page", preview=True))
    assert "widgets-pack1.css" not in legacy_markup and "widgets-pack1.js" not in legacy_markup
    assert WIDGET_CSS in legacy_markup
    page.draft_json = document(default_block("container") | {"children": [default_block("slideshow")]})
    nested_markup = to_xml(storefront(db, site, page, "", "csrf", "/your-page", preview=True))
    assert "widgets-pack1.js" in nested_markup


def test_ai_refinement_preserves_new_widgets_when_editing_existing_copy():
    blocks = [default_block(kind, kind) for kind in PACK]
    blocks.append(default_block("heading", "editable"))
    original = document(*blocks)
    snapshot = {"settings": {"default_locale": "en"}, "pages": {"page": {"document": original}}}
    immediate, operations = derive_operations(snapshot, [
        {"op": "patch", "page_id": "page", "block_id": "editable", "after": {"heading": "Updated heading"}},
    ])
    assert not immediate and len(operations) == 1
    changed = apply_operation(original, operations[0])
    assert changed["blocks"][:len(PACK)] == blocks[:len(PACK)]
    assert changed["blocks"][-1]["heading"] == "Updated heading"
    assert derive_operations(snapshot, [{"op": "document", "page_id": "page", "document": original}]) == ([], [])


def test_editor_schema_save_and_publish_all_widgets_with_csrf(workspace):
    w = workspace
    loaded = load(w)
    categories = {kind: "Media" for kind in PACK} | {"icon_box": "Layout", "section_heading": "Text"}
    for kind, category in categories.items():
        assert loaded["schema"][kind]["category"] == category
        assert loaded["schema"][kind]["icon"] and loaded["schema"][kind]["insertable"]
    doc = document(*(default_block(kind, kind) for kind in PACK))
    payload = {"document": doc, "version": loaded["version"]}
    params = {"page": w.page.id}
    assert w.client.put(w.base + "/draft", params=params, json=payload).status_code == 403
    saved = w.client.put(w.base + "/draft", params=params, json=payload, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["document"] == doc
    payload["version"] = saved.json()["version"]
    assert w.client.post(w.base + "/publish", params=params, json=payload).status_code == 403
    published = w.client.post(w.base + "/publish", params=params, json=payload, headers=w.headers)
    assert published.status_code == 200, published.text
    assert published.json()["published"] is True
    assert load(w)["document"] == doc
    with w.sessions() as db:
        stored = db.get(SitePage, w.page.id).published_json
        assert stored["published_at"]
        assert {key: stored[key] for key in doc} == doc


def test_picker_metadata_covers_widgets_and_legacy_sections():
    schemas = editor_schema()
    icons = []
    for kind, schema in schemas.items():
        assert schema["description"] and "Add and customise" not in schema["description"]
        icons.append(schema["icon"])
        if kind in WIDGETS:
            assert schema["icon"] == WIDGETS[kind].icon
            assert schema["description"] == WIDGETS[kind].description
    assert len(icons) == len(set(icons))


def test_slideshow_controls_and_merchant_overlay(context):
    db, site, page = context
    block = default_block("slideshow")
    block["items"] *= 2

    def markup():
        return to_xml(render_section(db, site, page, {}, block, "", "csrf", False))

    rendered = markup()
    assert 'aria-label="Previous slide"' in rendered
    assert 'aria-label="Next slide"' in rendered
    assert 'aria-label="Go to slide 2"' in rendered
    assert '<svg' in rendered
    assert 'data-slide-pause' not in rendered
    assert '--fs-overlay:0.6' in rendered
    assert 'aria-live="polite"' in rendered
    block.update(autoplay=True, show_arrows=False, show_dots=False, overlay_opacity=15)
    rendered = markup()
    assert 'data-slide-pause' in rendered
    assert 'data-slide-prev' not in rendered and 'data-slide-next' not in rendered
    assert 'data-slide-to' not in rendered
    assert '--fs-overlay:0.15' in rendered


@pytest.mark.parametrize("source", ["/static/img/storefront-object.jpg", "", "metafield:custom.photo"])
def test_hotspot_editor_preview_markup(source):
    """Exercise the actual dependency-free settings renderer, including escaping."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to exercise the editor renderer")
    module = (Path(__file__).resolve().parents[1] / "static/editor/hotspots.js").as_uri()
    block = default_block("image_hotspots")
    block.update(image=source, alt='Detail "image" <test>')
    block["items"] = [block["items"][0] | {"x": 0, "y": 100},
                      block["items"][0] | {"x": 72, "y": 34}]
    result = subprocess.run(
        [node, "--input-type=module", "-e",
         f"import {{hotspotPreview}} from {json.dumps(module)}; "
         "let data = ''; for await (const chunk of process.stdin) data += chunk; "
         "process.stdout.write(hotspotPreview(JSON.parse(data)));"],
        input=json.dumps(block), text=True, capture_output=True, check=True,
    )
    markup = BeautifulSoup(result.stdout, "html.parser")
    preview = markup.select_one("[data-hotspot-editor]")
    assert preview is not None
    stage = preview.select_one("[data-hotspot-stage]")
    assert stage["aria-label"] == "Hotspot placement preview"
    instructions = markup.find(id=stage["aria-describedby"])
    assert "You can also type the position below." in instructions.text
    assert "tabindex" not in stage.attrs
    if not source or source.startswith("metafield:"):
        assert not stage.select("img, button")
        assert "Add an image" in preview.select_one("[data-hotspot-empty]").text
        return
    image = stage.select_one("img")
    assert image["src"] == source and image["alt"] == block["alt"]
    markers = stage.select("[data-hotspot-index]")
    assert len(markers) == 2
    for index, marker in enumerate(markers):
        assert marker["type"] == "button"
        assert marker["data-hotspot-index"] == str(index)
        assert marker["aria-label"] == f"Select hotspot {index + 1}"
        assert marker["aria-pressed"] == str(index == 0).lower()
        item = block["items"][index]
        assert marker["style"] == f"left:{item['x']}%;top:{item['y']}%"


def test_hotspot_typed_coordinates_save_and_reject_invalid_values(workspace):
    w = workspace
    loaded = load(w)
    block = default_block("image_hotspots")
    block["image"] = "/static/img/storefront-object.jpg"
    block["items"][0].update(x=27, y=83)
    values = {"document": document(block), "version": loaded["version"]}
    params = {"page": w.page.id}
    assert w.client.put(w.base + "/draft", params=params, json=values).status_code == 403
    saved = w.client.put(w.base + "/draft", params=params, json=values, headers=w.headers)
    assert saved.status_code == 200, saved.text
    assert load(w)["document"]["blocks"][0]["items"][0] == block["items"][0]
    values["version"] = saved.json()["version"]
    for invalid in (-1, 101, 27.5):
        block["items"][0]["x"] = invalid
        response = w.client.put(w.base + "/draft", params=params, json=values, headers=w.headers)
        assert response.status_code == 422, response.text
    assert load(w)["document"]["blocks"][0]["items"][0]["x"] == 27

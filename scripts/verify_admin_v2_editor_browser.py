"""Exercise the visual editor against an isolated, disposable FastShop storefront.

Run: uv run python scripts/verify_admin_v2_editor_browser.py
No environment files, provider calls or stored browser sessions are used.
"""
from __future__ import annotations

import json
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/admin-v2/upstream/editor-check"


def fixtures():
    from sqlalchemy import select

    from app import content
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == EMAIL))
        site = content.create_site(db, user.id, "Editor fixture", "editor-fixture")
        site.status = "preview"
        home = next(p for p in content.site_pages(db, site) if p.path == "/")
        page = content.create_page(db, site, "Our next chapter", "/pages/editor-browser",
                                   document={"version": 1, "title": "Our next chapter", "blocks": []})
        db.commit()
        return site.id, home.id, page.id


def capture(page, name):
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(OUTPUT / (name + ".png")))


def drag_to_canvas(page, source, target=None, position="after"):
    """Use a real pointer drag across the iframe boundary."""
    frame = page.locator("#editor-canvas").content_frame
    target = target or frame.locator("main").first
    source.scroll_into_view_if_needed()
    origin = source.bounding_box()
    assert origin
    box = target.bounding_box()
    assert box
    canvas_box = page.locator("#editor-canvas").bounding_box()
    assert canvas_box
    x = box["x"] + box["width"] / 2
    y = box["y"] + (box["height"] / 2 if position == "inside" else min(max(box["height"] - 2, 20), 100))
    y = min(max(y, canvas_box["y"] + 30), canvas_box["y"] + canvas_box["height"] - 30)
    page.mouse.move(origin["x"] + origin["width"] / 2, origin["y"] + origin["height"] / 2)
    page.mouse.down()
    page.mouse.move(origin["x"] + origin["width"] / 2 + 12, origin["y"] + origin["height"] / 2, steps=5)
    page.mouse.move(x, y, steps=20)
    page.mouse.move(x + 1, y + 1, steps=2)
    page.mouse.up()


def journey(browser, base, site_id, home_id, page_id):
    context = browser.new_context(base_url=base, viewport={"width": 1440, "height": 900},
                                  reduced_motion="reduce")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    editor = f"/admin/sites/{site_id}/editor"
    page.goto(editor + f"?page={home_id}")
    frame = page.locator("#editor-canvas").content_frame
    expect(frame.locator("[data-block-id]").first).to_be_visible()
    hero_row = page.locator(".tree-row").first
    expect(hero_row.locator(".block-icon")).to_be_visible()
    expect(hero_row.locator(".tree-title > span")).to_have_text("Hero image or video")
    expect(hero_row.locator(".tree-title small")).to_have_text("Make room for something good.")
    capture(page, "desktop-1440-storefront-home")
    page.locator("#add-section").click()
    picker = page.locator("#picker-dialog")
    expect(picker).to_have_attribute("aria-labelledby", "picker-title")
    expect(picker.locator(".picker-item .block-icon").first).to_be_visible()
    capture(page, "desktop-section-picker")
    picker.locator("button").last.focus()
    page.keyboard.press("Tab")
    expect(picker.locator("[data-close-dialog]")).to_be_focused()
    page.keyboard.press("Escape")
    expect(picker).not_to_be_visible()
    expect(page.locator("#add-section")).to_be_focused()
    page.set_viewport_size({"width": 1280, "height": 800})
    capture(page, "desktop-1280-storefront-home")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(editor + f"?page={page_id}")
    expect(page.locator("#save-status")).to_contain_text("Saved")
    expect(frame.locator(".fs-editor-hint")).to_be_attached()
    assert not errors, errors
    capture(page, "desktop-fresh-page")
    page.locator('[data-mode="widgets"]').click()
    drag_to_canvas(page, page.locator('[data-widget-type="heading"]'))
    heading = frame.locator('[data-block-type="heading"] [data-field="heading"]')
    expect(heading).to_be_visible()
    heading.dblclick()
    heading.fill("Thoughtfully made, every day")
    heading.press("Tab")
    expect(heading).to_contain_text("Thoughtfully made, every day")
    drag_to_canvas(page, page.locator('[data-widget-type="container"]'))
    container = frame.locator('[data-block-type="container"]')
    expect(container).to_be_visible()
    drag_to_canvas(page, page.locator('[data-widget-type="image"]'), container, "inside")
    expect(container.locator('[data-block-type="image"]')).to_be_attached()
    drag_to_canvas(page, page.locator('[data-widget-type="button"]'), container, "inside")
    expect(container.locator('[data-block-type="button"]')).to_be_attached()
    container.locator('[data-block-type="image"]').click()
    page.get_by_role("tab", name="Content", exact=True).click()
    page.get_by_role("button", name="Choose media", exact=True).click()
    expect(page.locator(".upload-label")).to_contain_text("Drop an image here")
    capture(page, "desktop-media-dropzone")
    from verify_admin_v2_catalog_browser import product_image
    page.locator("#media-upload").set_input_files({"name": "fixture.png", "mimeType": "image/png", "buffer": product_image()})
    expect(page.locator("#media-dialog")).not_to_be_visible()
    expect(container.locator("img")).to_be_visible()
    container.locator("img").dblclick()
    expect(page.locator("#media-dialog")).to_be_visible()
    page.locator("#media-grid button").first.click()
    page.locator('[data-mode="sections"]').click()
    heading_row = page.locator('.tree-row[data-block-id]').filter(has_text="Heading")
    container_row = page.locator('.tree-row[data-block-id]').filter(has_text="Container")
    origin, target = heading_row.bounding_box(), container_row.bounding_box()
    assert origin and target
    page.mouse.move(origin["x"] + 20, origin["y"] + origin["height"] / 2)
    page.mouse.down()
    page.mouse.move(target["x"] + 40, target["y"] + target["height"] - 2, steps=15)
    page.mouse.move(target["x"] + 41, target["y"] + target["height"] - 2, steps=2)
    page.mouse.up()
    container.click(position={"x": 8, "y": 8})
    page.get_by_role("tab", name="Style", exact=True).click()
    page.get_by_label("Background colour", exact=True).fill("#eaf2ff")
    page.locator('[data-companion="style.padding_top"]').fill("48")
    page.locator('[data-companion="style.padding_top"]').press("Tab")
    page.get_by_label("Text colour", exact=True).fill("#202223")
    expect(container).to_have_css("background-color", "rgb(234, 242, 255)")
    expect(container).to_have_css("padding-top", "48px")
    expect(frame.locator('[data-block-type="heading"]')).to_have_count(1)
    expect(container.locator('[data-block-type="heading"]')).to_have_count(0)
    assert frame.locator('[data-block-id]').evaluate_all(
        "nodes => new Set(nodes.map(n => n.dataset.blockId)).size === nodes.length"
    ), "Preview contains stale duplicate block nodes"
    capture(page, "desktop-1440-edited-page")
    page.locator('[data-device="mobile"]').click()
    expect(page.locator("#editor-canvas")).to_have_css("width", "390px")
    capture(page, "mobile-canvas")
    heading.dblclick()
    heading.fill("An edit to undo")
    heading.press("Tab")
    expect(heading).to_contain_text("An edit to undo")
    page.locator("#undo").click()
    expect(heading).to_contain_text("Thoughtfully made, every day")
    page.locator("#redo").click()
    expect(heading).to_contain_text("An edit to undo")
    page.locator("#undo").click()
    expect(heading).to_contain_text("Thoughtfully made, every day")
    page.locator("#save").click()
    expect(page.locator("#save-status")).to_contain_text("Saved")
    page.reload()
    expect(frame.locator('[data-field="heading"]').filter(has_text="Thoughtfully made, every day")).to_be_visible()
    expect(frame.locator('[data-block-type="container"] [data-block-type="image"]')).to_be_attached()
    expect(frame.locator('[data-block-type="container"] [data-block-type="button"]')).to_be_attached()
    page.locator('[data-mode="theme"]').click()
    page.get_by_label("Accent", exact=True).fill("#1765b3")
    page.locator("#save").click()
    expect(page.locator("#save-status")).to_contain_text("Saved")
    page.reload()
    page.locator('[data-mode="theme"]').click()
    expect(page.get_by_label("Accent", exact=True)).to_have_value("#1765b3")
    page.locator("#publish").click()
    page.locator("#confirm-publish").click()
    expect(page.locator("#save-status")).to_contain_text("Published")
    live = page.request.get("/sites/editor-fixture/pages/editor-browser")
    assert live.ok and "--store-accent:#1765b3" in live.text()
    live_page = context.new_page()
    live_page.goto("/sites/editor-fixture/pages/editor-browser")
    expect(live_page.get_by_role("heading", name="Thoughtfully made, every day", exact=True)).to_be_visible()
    expect(live_page.locator('main .fs-widget img').first).to_be_visible()
    capture(live_page, "desktop-published-editor-page")
    live_page.close()
    page.locator("#revisions").click()
    expect(page.locator("#revision-list button").first).to_be_visible()
    revisions = page.request.get(editor + "/api/revisions?page=" + page_id).json()["revisions"]
    expected_revision = revisions[-1]["document"]
    page.locator("#revision-list button").last.click()
    expect(page.locator("#revision-dialog")).not_to_be_visible()
    expect(page.locator("#save-status")).to_contain_text("Saved")
    restored = page.request.get(editor + "/api/document?page=" + page_id).json()["document"]
    assert restored == expected_revision, "Restored document differs from the selected revision"
    page.reload()
    assert page.request.get(editor + "/api/document?page=" + page_id).json()["document"] == expected_revision
    for restored_block in expected_revision.get("blocks", []):
        expect(frame.locator('[data-block-id="' + restored_block["id"] + '"]')).to_be_visible()
    contrast = frame.locator(".h-footer").evaluate("""element => {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const context = canvas.getContext('2d');
      const luminance = colour => {
        context.fillStyle = colour;
        context.fillRect(0, 0, 1, 1);
        const rgb = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3)
          .map(value => value / 255)
          .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
        return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
      };
      const css = getComputedStyle(element);
      const values = [luminance(css.color), luminance(css.backgroundColor)].sort((a, b) => a - b);
      return (values[1] + .05) / (values[0] + .05);
    }""")
    assert contrast >= 4.5, f"Storefront footer contrast after theme edit: {contrast:.2f}"
    capture(page, "desktop-restored-revision")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Preview page", exact=True).click()
    canvas_box = page.locator("#editor-canvas").bounding_box()
    assert canvas_box and canvas_box["width"] >= 300
    assert canvas_box["x"] + canvas_box["width"] <= 390
    capture(page, "mobile-390-editor-restored")
    page.get_by_role("button", name="Widgets", exact=True).click()
    expect(page.get_by_role("heading", name="Widgets", exact=True)).to_be_visible()
    capture(page, "mobile-390-editor-widgets")
    page.get_by_role("button", name="Preview page", exact=True).click()
    expect(page.locator("#editor")).not_to_have_class(re.compile(r".*is-panel-open.*"))
    assert not errors, errors
    context.close()
    return errors


def main():
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-p5-browser-") as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, home_id, page_id = fixtures()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated editor server failed to start")
                thread.join(0.05)
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    errors = journey(browser, f"http://127.0.0.1:{port}", site_id, home_id, page_id)
                finally:
                    browser.close()
            print(json.dumps({"status": "passed", "browser_errors": errors,
                              "screenshots": str(OUTPUT)}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

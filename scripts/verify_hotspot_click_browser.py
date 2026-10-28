"""Verify hotspot placement in disposable desktop and mobile editor sessions.

Run: uv run python scripts/verify_hotspot_click_browser.py
No environment files, external services or stored browser sessions are used.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate
from verify_widgets_pack1_browser import dismiss_consent, open_item, setting

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/hotspot-click"
PHOTO = "/static/img/storefront-object.jpg"


def fixture(device):
    from sqlalchemy import select

    from app import content
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == EMAIL))
        site = content.create_site(db, owner.id, "Hotspot placement", f"hotspot-click-{device}")
        site.status = "preview"
        page = content.create_page(db, site, "Image details", "/pages/image-details",
                                   document={"version": 1, "title": "Image details", "blocks": []})
        db.commit()
        return site.id, page.id


def capture(page, name):
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Horizontal overflow"
    page.screenshot(path=str(OUTPUT / f"{name}.png"))


def position(page, index, x, y):
    expect(page.locator(f'[data-setting="items.{index}.x"]')).to_have_value(str(x))
    expect(page.locator(f'[data-setting="items.{index}.y"]')).to_have_value(str(y))


def place(page, x, y, *, mobile):
    image = page.locator("[data-hotspot-stage] img")
    expect(image).to_be_visible()
    expect(image).to_have_js_property("complete", True)
    assert image.evaluate("image => image.naturalWidth > 0"), "Preview image failed to load"
    image.scroll_into_view_if_needed()
    bounds = image.bounding_box()
    assert bounds
    point = {"x": bounds["width"] * x / 100, "y": bounds["height"] * y / 100}
    if mobile:
        image.tap(position=point)
    else:
        image.click(position=point)


def journey(browser, base, *, mobile):
    device = "mobile" if mobile else "desktop"
    site_id, page_id = fixture(device)
    context = browser.new_context(
        base_url=base, viewport={"width": 390, "height": 844} if mobile else {"width": 1440, "height": 900},
        is_mobile=mobile, has_touch=mobile, reduced_motion="reduce",
    )
    errors = []
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    page.goto(f"/admin/sites/{site_id}/editor?page={page_id}")
    frame = page.locator("#editor-canvas").content_frame
    expect(frame.locator(".fs-editor-hint")).to_be_attached()
    page.get_by_role("button", name="Widgets", exact=True).click()
    page.get_by_role("searchbox", name="Search widgets").fill("Image hotspots")
    page.locator('[data-widget-type="image_hotspots"]').click()
    page.get_by_role("tab", name="Content", exact=True).click()
    preview = page.locator("[data-hotspot-editor]")
    expect(preview).to_be_visible()
    expect(preview).to_contain_text(re.compile("image", re.I))
    expect(preview.locator("img")).to_have_count(0)
    setting(page, "image", PHOTO)
    expect(preview.locator("[data-hotspot-index]")).to_have_count(1)
    setting(page, "image", "")
    expect(preview.locator("img")).to_have_count(0)
    expect(preview).to_contain_text("Add an image")
    setting(page, "image", PHOTO)
    expect(preview.locator("[data-hotspot-stage]")).to_have_attribute("aria-label", "Hotspot placement preview")
    expect(preview).to_contain_text("You can also type the position below")
    setting(page, "alt", "A sofa with numbered image details")
    expect(preview.locator("img")).to_have_attribute("alt", "A sofa with numbered image details")
    open_item(page, 0)
    page.locator('[data-repeat-action="remove"][data-index="0"]').click()
    expect(preview.locator("[data-hotspot-index]")).to_have_count(0)
    place(page, 45, 55, mobile=mobile)
    position(page, 0, 45, 55)
    expect(preview.locator("[data-hotspot-index]")).to_have_count(1)
    page.locator('[data-repeat-add="items"]').click()
    expect(preview.locator("[data-hotspot-index]")).to_have_count(2)
    selected_marker = preview.locator('[data-hotspot-index="1"]')
    expect(selected_marker).to_be_visible()
    selected_marker.focus()
    expect(selected_marker).to_be_focused()
    selected_marker.press("Enter")
    expect(selected_marker).to_have_attribute("aria-pressed", "true")
    selected_marker.press("Tab")
    expect(selected_marker).not_to_be_focused()
    expect(selected_marker).to_be_visible()
    selected_marker.focus()
    expect(selected_marker).to_be_focused()
    selected_marker.press("Enter")
    place(page, 1, 99, mobile=mobile)
    position(page, 1, 1, 99)
    place(page, 70, 40, mobile=mobile)
    position(page, 1, 70, 40)
    preview.locator('[data-hotspot-index="0"]').click()
    place(page, 25, 65, mobile=mobile)
    position(page, 0, 25, 65)
    position(page, 1, 70, 40)
    open_item(page, 0)
    page.locator('[data-repeat-action="down"][data-index="0"]').click()
    expect(preview.locator('[data-hotspot-index="1"]')).to_have_attribute("aria-pressed", "true")
    place(page, 60, 75, mobile=mobile)
    position(page, 1, 60, 75)
    position(page, 0, 70, 40)
    open_item(page, 1)
    page.locator('[data-repeat-action="up"][data-index="1"]').click()
    expect(preview.locator('[data-hotspot-index="0"]')).to_have_attribute("aria-pressed", "true")
    place(page, 20, 30, mobile=mobile)
    position(page, 0, 20, 30)
    position(page, 1, 70, 40)
    open_item(page, 0)
    setting(page, "items.0.x", 28)
    setting(page, "items.0.y", 62)
    expect(preview.locator('[data-hotspot-index="0"]')).to_have_attribute("style", re.compile(r"left:\s*28%.*top:\s*62%"))
    # Click again after typing, proving the same native inputs remain authoritative.
    place(page, 50, 25, mobile=mobile)
    position(page, 0, 50, 25)
    open_item(page, 1)
    setting(page, "items.1.x", 72)
    setting(page, "items.1.y", 42)
    expect(frame.locator('[data-hotspot-target]').first).to_have_attribute("style", re.compile(r"left:clamp\(22px,50%,.*top:clamp\(22px,25%,"))
    preview.scroll_into_view_if_needed()
    capture(page, f"{device}-editor-placement")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#save-status")).to_contain_text("Saved")
    page.reload()
    frame.locator('[data-block-type="image_hotspots"]').click(position={"x": 8, "y": 8})
    page.get_by_role("tab", name="Content", exact=True).click()
    position(page, 0, 50, 25)
    position(page, 1, 72, 42)
    capture(page, f"{device}-editor-saved")
    page.locator("#publish").click()
    page.locator("#confirm-publish").click()
    expect(page.locator("#save-status")).to_contain_text("Published")
    page.goto(f"/sites/hotspot-click-{device}/pages/image-details")
    dismiss_consent(page)
    markers = page.locator("[data-hotspot-target]")
    expect(markers).to_have_count(2)
    for index, (x, y) in enumerate(((50, 25), (72, 42))):
        marker = markers.nth(index)
        expect(marker).to_be_visible()
        expect(marker).to_have_attribute("style", re.compile(rf"left:clamp\(22px,{x}%,.*top:clamp\(22px,{y}%,"))
        stage_bounds = page.locator(".fs-pack-hotspot-stage").bounding_box()
        marker_bounds = marker.bounding_box()
        assert stage_bounds and marker_bounds
        for axis, size, expected in (("x", "width", x), ("y", "height", y)):
            actual = 100 * (marker_bounds[axis] + marker_bounds[size] / 2 - stage_bounds[axis]) / stage_bounds[size]
            assert abs(actual - expected) < 1, (device, index, axis, actual, expected)
    markers.first.click()
    expect(markers.first).to_have_attribute("aria-expanded", "true")
    capture(page, f"{device}-published-hotspots")
    context.close()
    assert not errors, errors
    return {"device": device, "status": "passed", "browser_errors": errors}


def main():
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-hotspot-click-") as data_dir:
        # Always use disposable SQLite even if a browser DB override was set elsewhere.
        os.environ.pop("FASTSHOP_BROWSER_DB_URL", None)
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated hotspot server failed to start")
                thread.join(.05)
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    results = [journey(browser, f"http://127.0.0.1:{port}", mobile=mobile)
                               for mobile in (False, True)]
                finally:
                    browser.close()
            print(json.dumps({"status": "passed", "journeys": results, "screenshots": str(OUTPUT)}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

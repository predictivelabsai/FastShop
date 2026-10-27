"""Verify layout/media widgets in a disposable editor and storefront.

Run: uv run python scripts/verify_widgets_pack1_browser.py
No environment files, external services or stored browser sessions are used.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate
from verify_admin_v2_editor_browser import drag_to_canvas

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/widgets-pack1"
KINDS = ("section_heading", "slideshow", "image_banner", "before_after", "logo_cloud",
         "icon_box", "image_hotspots", "video_embed")
PHOTO = "/static/img/storefront-object.jpg"
SECOND_PHOTO = "/static/img/product-interior.jpg"


def fixtures():
    from sqlalchemy import select

    from app import content
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == EMAIL))
        site = content.create_site(db, owner.id, "Your studio", "widgets-pack1")
        site.status = "preview"
        page = content.create_page(db, site, "Your collection", "/pages/layout-media",
                                   document={"version": 1, "title": "Your collection", "blocks": []})
        db.commit()
        return site.id, page.id


def capture(page, name, *, full_page=False):
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Horizontal overflow"
    page.screenshot(path=str(OUTPUT / (name + ".png")), full_page=full_page)


def dismiss_consent(page):
    decline = page.get_by_role("button", name="Decline", exact=True)
    if decline.is_visible():
        decline.click()


def setting(page, path, value):
    """Edit the actual settings panel, including its native event handlers."""
    control = page.locator(f'[data-setting="{path}"]')
    if isinstance(value, bool):
        control.set_checked(value)
    elif control.evaluate("element => element.tagName") == "SELECT":
        control.select_option(str(value))
    else:
        if control.get_attribute("type") == "range":
            control = page.locator(f'[data-companion="{path}"]')
        control.fill(str(value))
    # Preview responses are debounced, so use the saved status as a readiness check.
    control.press("Tab")


def open_item(page, index=0):
    details = page.locator(f'.repeater-item[data-index="{index}"]')
    if details.get_attribute("open") is None:
        details.locator("summary").click()
    return details


def check_interactions(page, *, mobile=False):
    slideshow = page.locator("[data-slideshow]")
    expect(slideshow.locator(".fs-pack-slide:visible")).to_have_count(1)
    slideshow.locator("[data-slide-next]").click()
    expect(slideshow.locator('[data-slide-to="1"]')).to_have_attribute("aria-current", "true")
    # Reduced motion stops autoplay, while arrows remain fully usable.
    expect(slideshow.locator("[data-slide-pause]")).to_be_disabled()
    capture(page, ("mobile" if mobile else "desktop") + "-slideshow")
    comparison = page.locator("[data-before-after]")
    slider = comparison.locator("[data-compare-range]")
    slider.scroll_into_view_if_needed()
    slider.focus()
    before = int(slider.input_value())
    slider.press("ArrowRight")
    expect(slider).to_have_value(str(before + 1))
    stage = comparison.locator(".fs-pack-compare-stage")
    stage.scroll_into_view_if_needed()
    bounds = stage.bounding_box()
    assert bounds
    x, y = bounds["x"] + bounds["width"] * .5, bounds["y"] + bounds["height"] * .5
    if mobile:
        page.touchscreen.tap(bounds["x"] + bounds["width"] * .65, y)
        assert 62 <= int(slider.input_value()) <= 68, ("Touch comparison", slider.input_value(), bounds)
    else:
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(bounds["x"] + bounds["width"] * .65, y, steps=12)
        assert 62 <= int(slider.input_value()) <= 68, ("Drag comparison", slider.input_value(), bounds)
        capture(page, "desktop-before-after-mid-drag")
        page.mouse.up()
    hotspot = page.locator("[data-hotspot-target]").first
    hotspot.scroll_into_view_if_needed()
    if mobile:
        hotspot.tap()
    else:
        hotspot.focus()
        hotspot.press("Enter")
    expect(hotspot).to_have_attribute("aria-expanded", "true")
    target = page.locator("#" + hotspot.get_attribute("aria-controls"))
    expect(target).to_be_visible()
    capture(page, ("mobile" if mobile else "desktop") + "-hotspot-popover")
    target.press("Escape")
    expect(target).to_be_hidden()
    expect(hotspot).to_be_focused()


def journey(browser, base, site_id, page_id):
    context = browser.new_context(base_url=base, viewport={"width": 1440, "height": 900},
                                  reduced_motion="reduce")
    errors = []

    def watch(page):
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

    page = context.new_page()
    watch(page)
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    editor = f"/admin/sites/{site_id}/editor"
    page.goto(editor + f"?page={page_id}")
    frame = page.locator("#editor-canvas").content_frame
    expect(frame.locator(".fs-editor-hint")).to_be_attached()
    page.locator('[data-mode="widgets"]').click()
    capture(page, "desktop-editor-widget-picker")
    drag_to_canvas(page, page.locator('[data-widget-type="section_heading"]'))
    expect(frame.locator('[data-block-type="section_heading"]')).to_be_visible()
    setting(page, "heading", "Your collection")
    setting(page, "eyebrow", "Made for your everyday")
    for kind in KINDS[1:]:
        search = page.get_by_role("searchbox", name="Search widgets")
        search.fill(kind.replace("_", " ") if kind != "before_after" else "Before and after")
        # The display name for icon_box is plural, so search by the common word.
        if kind == "icon_box":
            search.fill("Icon")
        tile = page.locator(f'[data-widget-type="{kind}"]')
        expect(tile).to_be_visible()
        tile.click()
        expect(frame.locator(f'[data-block-type="{kind}"]')).to_be_attached()
        page.get_by_role("tab", name="Content", exact=True).click()
        if kind == "slideshow":
            open_item(page)
            expect(page.locator('[data-repeat-action="remove"]').first).to_be_disabled()
            setting(page, "items.0.image", PHOTO)
            setting(page, "items.0.heading", "Your first slide")
            page.locator('[data-repeat-add="items"]').click()
            open_item(page, 1)
            setting(page, "items.1.image", SECOND_PHOTO)
            setting(page, "items.1.heading", "Your next slide")
            page.locator('[data-repeat-add="items"]').click()
            open_item(page, 2)
            page.locator('[data-repeat-action="remove"][data-index="2"]').click()
            expect(page.locator(".repeater-item")).to_have_count(2)
            setting(page, "autoplay", True)
            setting(page, "interval", 3)
        elif kind == "image_banner":
            setting(page, "image", SECOND_PHOTO)
            setting(page, "heading", "Your next chapter")
        elif kind == "before_after":
            setting(page, "before_image", PHOTO)
            setting(page, "after_image", SECOND_PHOTO)
        elif kind == "logo_cloud":
            for index in range(4):
                if index:
                    page.locator('[data-repeat-add="items"]').click()
                open_item(page, index)
                setting(page, f"items.{index}.image", "/static/favicon.svg")
                setting(page, f"items.{index}.alt", f"Your logo {index + 1}")
        elif kind == "icon_box":
            for index, icon in enumerate(("box", "leaf", "gift")):
                if index:
                    page.locator('[data-repeat-add="items"]').click()
                open_item(page, index)
                setting(page, f"items.{index}.icon", icon)
            page.locator('[data-repeat-action="up"][data-index="2"]').click()
        elif kind == "image_hotspots":
            setting(page, "image", PHOTO)
            open_item(page)
            setting(page, "items.0.x", 65)
            setting(page, "items.0.y", 45)
            setting(page, "items.0.link", "/")
        search.fill("")
    # A block replacement must initialise interactive widgets again in the iframe.
    frame.locator('[data-block-type="before_after"]').click(position={"x": 8, "y": 8})
    setting(page, "split", 55)
    expect(frame.locator('[data-compare-range]')).to_have_value("55")
    expect(frame.locator('[data-before-after]')).to_have_attribute("data-enhanced", "true")
    page.locator("#save").click()
    expect(page.locator("#save-status")).to_contain_text("Saved")
    page.reload()
    expect(frame.locator('[data-block-type]')).to_have_count(len(KINDS))
    frame.locator("[data-slide-next]").click()
    expect(frame.locator('[data-slide-to="1"]')).to_have_attribute("aria-current", "true")
    frame.locator('[data-slide-to="0"]').click()
    inline = frame.locator('[data-block-type="slideshow"] [data-field="items.0.heading"]')
    inline.dblclick()
    inline.fill("Your opening slide")
    inline.press("Tab")
    expect(inline).to_have_text("Your opening slide")
    marker = frame.locator("[data-hotspot-target]").first
    marker.click()
    expect(marker).to_have_attribute("aria-expanded", "true")
    frame.locator(".fs-pack-hotspot-detail a").click()
    expect(frame.locator('[data-block-type]')).to_have_count(len(KINDS))
    expect(frame.locator(".fs-pack-hotspot-detail")).to_be_visible()
    frame.locator("[data-hotspot-close]").click()
    expect(marker).to_have_attribute("aria-expanded", "false")
    page.locator('[data-mode="widgets"]').click()
    capture(page, "desktop-editor-all-widgets")
    page.locator("#publish").click()
    page.locator("#confirm-publish").click()
    expect(page.locator("#save-status")).to_contain_text("Published")
    live_path = "/sites/widgets-pack1/pages/layout-media"
    live = context.new_page()
    watch(live)
    live.goto(live_path)
    dismiss_consent(live)
    for kind in KINDS:
        expect(live.locator(".fs-widget-" + kind)).to_have_count(1)
    expect(live.locator('script[src*="widgets-pack1.js"]')).to_have_count(1)
    capture(live, "desktop-storefront-all-widgets", full_page=True)
    check_interactions(live)
    mobile = browser.new_context(base_url=base, viewport={"width": 390, "height": 844},
                                 is_mobile=True, has_touch=True, reduced_motion="reduce")
    mobile_page = mobile.new_page()
    watch(mobile_page)
    mobile_page.goto(live_path)
    dismiss_consent(mobile_page)
    capture(mobile_page, "mobile-storefront-all-widgets", full_page=True)
    check_interactions(mobile_page, mobile=True)
    mobile_page.set_viewport_size({"width": 360, "height": 800})
    assert mobile_page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    no_js = browser.new_context(base_url=base, java_script_enabled=False,
                                viewport={"width": 390, "height": 844})
    plain = no_js.new_page()
    plain.goto(live_path)
    expect(plain.locator(".fs-pack-slide:visible")).to_have_count(2)
    expect(plain.locator(".fs-pack-compare-before")).to_be_visible()
    expect(plain.locator(".fs-pack-compare-after")).to_be_visible()
    expect(plain.locator(".fs-pack-hotspot-detail")).to_be_visible()
    capture(plain, "mobile-storefront-without-javascript", full_page=True)
    no_js.close()
    motion = browser.new_context(base_url=base, viewport={"width": 1440, "height": 900})
    animated = motion.new_page()
    watch(animated)
    animated.goto(live_path)
    dismiss_consent(animated)
    # Dismissing a floating consent panel can leave the pointer over the slideshow.
    animated.mouse.move(0, 0)
    animated.locator("header a").first.focus()
    slides = animated.locator("[data-slideshow]")
    expect(slides.locator('[data-slide-to="1"]')).to_have_attribute("aria-current", "true", timeout=6000)
    slides.hover()
    slides.locator('[data-slide-to="0"]').click()
    animated.wait_for_timeout(3200)
    expect(slides.locator('[data-slide-to="0"]')).to_have_attribute("aria-current", "true")
    animated.mouse.move(0, 0)
    slides.locator('[data-slide-to="0"]').focus()
    animated.wait_for_timeout(3200)
    expect(slides.locator('[data-slide-to="0"]')).to_have_attribute("aria-current", "true")
    slides.locator("[data-slide-pause]").click()
    expect(slides.locator("[data-slide-pause]")).to_have_attribute("aria-pressed", "true")
    expect(slides.locator("[data-slide-pause]")).to_have_text("Play slideshow")
    motion.close()
    mobile.close()
    context.close()
    assert not errors, errors
    return errors


def main():
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-widgets-pack1-") as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, page_id = fixtures()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated widget server failed to start")
                thread.join(.05)
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    errors = journey(browser, f"http://127.0.0.1:{port}", site_id, page_id)
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

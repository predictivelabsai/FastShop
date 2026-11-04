"""Edit the Liquid hero disclaimer and open shell panels from the visual editor canvas.

Run: uv run python scripts/verify_hero_disclaimer_browser.py
No environment files, provider calls or stored browser sessions are used.
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

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/hero-disclaimer"
EDITED = "Edited hero note for the browser check."


def fixtures():
    from sqlalchemy import select

    from app import content
    from app.db import SessionLocal
    from app.models import Site

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
        home = next(p for p in content.site_pages(db, site) if p.path == "/")
        return site.id, home.id


def capture(page, name):
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(OUTPUT / (name + ".png")))


def journey(browser, base, site_id, home_id):
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
    page.goto(f"/admin/sites/{site_id}/editor?page={home_id}")
    frame = page.locator("#editor-canvas").content_frame
    disclaimer = frame.locator(".liquid-hero-disclaimer")
    expect(disclaimer).to_have_attribute("data-field", "disclaimer")
    original = disclaimer.inner_text()
    assert "Food and Drug Administration" in original, original
    # Side panel shows the effective disclaimer for the hero block.
    disclaimer.click()
    page.get_by_role("tab", name="Content", exact=True).click()
    panel = page.get_by_label("Hero disclaimer", exact=True)
    expect(panel).to_be_visible()
    assert "Food and Drug Administration" in panel.input_value()
    capture(page, "desktop-panel")
    # Inline edit on the canvas.
    disclaimer.dblclick()
    disclaimer.fill(EDITED)
    disclaimer.press("Tab")
    expect(disclaimer).to_have_text(EDITED)
    expect(panel).to_have_value(EDITED)
    page.locator("#save").click()
    expect(page.locator("#save-status")).to_contain_text("Saved")
    page.reload()
    expect(frame.locator(".liquid-hero-disclaimer")).to_have_text(EDITED)
    # Header and footer clicks open their settings panels.
    frame.locator(".metal-footer").click(position={"x": 5, "y": 5})
    expect(page.locator("#right-panel, #inspector, body").first).to_contain_text("Store footer")
    capture(page, "desktop-footer-panel")
    frame.locator(".metal-header").click(position={"x": 5, "y": 5})
    expect(page.locator("#right-panel, #inspector, body").first).to_contain_text("Store header")
    page.locator("#publish").click()
    page.locator("#confirm-publish").click()
    expect(page.locator("#save-status")).to_contain_text("Published")
    live = context.new_page()
    live.goto("/sites/h24you-liquid/")
    expect(live.locator(".liquid-hero-disclaimer")).to_have_text(EDITED)
    assert live.locator(".liquid-hero-disclaimer").get_attribute("data-field") is None
    capture(live, "desktop-published")
    live.set_viewport_size({"width": 390, "height": 844})
    live.locator(".liquid-hero-disclaimer").scroll_into_view_if_needed()
    capture(live, "mobile-published")
    assert not errors, errors
    context.close()
    return errors


def main():
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-disclaimer-browser-") as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, home_id = fixtures()
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
                    errors = journey(browser, f"http://127.0.0.1:{port}", site_id, home_id)
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

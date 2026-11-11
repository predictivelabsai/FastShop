"""Capture the SEO settings page at the requested desktop and mobile sizes."""

from __future__ import annotations

import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate, seed_order

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/seo"


def login(page, path: str) -> None:
    page.goto(f"/login?next={path}")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url(f"**{path}")


def main() -> None:
    import uvicorn
    sys.path.insert(0, str(ROOT))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-seo-") as data_dir:
        isolate(data_dir, port)
        from sqlalchemy import select

        from app.db import SessionLocal, engine
        from app.main import app
        from app.models import Site, SitePage
        site_id = seed_order()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
            log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local verification server failed to start")
                thread.join(0.05)
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        context = browser.new_context(base_url=f"http://127.0.0.1:{port}",
                            viewport={"width": width, "height": height})
                        page = context.new_page()
                        page.set_default_timeout(15000)
                        login(page, "/admin/sites")
                        page.goto(f"/admin/sites/{site_id}/settings/seo")
                        expect(page.get_by_role("heading", name="Search appearance")).to_be_visible()
                        expect(page.get_by_label("Title format for other pages")).to_be_visible()
                        if device == "desktop":
                            page.get_by_label("Home page title").fill("SEO browser home title")
                            page.get_by_label("Home page meta description").fill("SEO browser home description")
                            page.get_by_label("Default meta description").fill("Browser saved SEO description")
                            page.get_by_role("button", name="Save draft settings").click()
                            expect(page.locator('p[role="status"]')).to_contain_text("Settings saved")
                            with SessionLocal() as db:
                                saved = db.get(Site, site_id)
                                home = db.scalar(select(SitePage).where(SitePage.site_id == site_id,
                                    SitePage.path == "/"))
                                assert saved.settings_json["seo_default_description"] == "Browser saved SEO description"
                                assert saved.published_settings_json.get("seo_default_description") != "Browser saved SEO description"
                                assert home.draft_json["seo_title"] == "SEO browser home title"
                                assert not home.published_json or home.published_json.get("seo_title") != "SEO browser home title"
                            page.get_by_role("button", name="Publish settings").click()
                            expect(page.locator('p[role="status"]')).to_contain_text("Settings saved")
                            page.get_by_role("button", name="Dismiss notification").click()
                            with SessionLocal() as db:
                                saved = db.get(Site, site_id)
                                home = db.scalar(select(SitePage).where(SitePage.site_id == site_id,
                                    SitePage.path == "/"))
                                assert saved.published_settings_json["seo_default_description"] == "Browser saved SEO description"
                                assert home.published_json["seo_title"] == "SEO browser home title"
                        page.add_style_tag(content="[data-agent-launcher]{display:none!important}")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                        page.screenshot(path=str(OUTPUT / f"{device}.png"), full_page=True)
                        context.close()
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()
    print(f"SEO settings screenshots saved to {OUTPUT}")


if __name__ == "__main__":
    main()

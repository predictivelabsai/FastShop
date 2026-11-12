"""Capture generic policy and consent UI at desktop and mobile widths."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from app.config import settings


def main():
    if settings.db_url or not settings.data_dir.name.startswith("fastshop-"):
        raise SystemExit("Requires DB_URL empty and an isolated fastshop-* data directory.")
    from app.db import SessionLocal
    from app.main import app
    from scripts.demo_site import seed_demo_site

    with SessionLocal() as db:
        site = seed_demo_site(db, settings.admin_email)
        config = dict(site.published_settings_json or {})
        config.pop("theme_pack", None)
        config["policies"] = {
            **(config.get("policies") or {}),
            "privacy": "<h2>Privacy at a glance</h2><p>We use only the information needed to run this shop.</p>",
        }
        config["policies"]["empty_message"] = "This policy has not been added yet."
        site.status = "published"
        site.published_settings_json = config
        db.commit()
        slug = site.slug

    port = int(settings.public_url.rsplit(":", 1)[1])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Local generic policy browser server did not start")
    output = Path("output/playwright/generic-policy")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    base = settings.public_url + "/sites/" + slug
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                context = browser.new_context(viewport={"width": width, "height": height},
                                              reduced_motion="reduce")
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                response = page.goto(base + "/pages/privacy-policy")
                assert response.status == 200, (response.status, response.url, page.title(), response.text()[:240])
                expect(page.get_by_role("heading", name="Privacy policy")).to_be_visible()
                expect(page.get_by_role("heading", name="Privacy at a glance")).to_be_visible()
                page.locator("#h-cookie").evaluate("element => { element.hidden = true; }")
                page.screenshot(path=str(output / f"{device}-policy.png"), full_page=True)
                page.locator("#h-cookie").evaluate("element => { element.hidden = false; }")
                expect(page.locator("#h-cookie")).to_be_visible()
                expect(page.get_by_role("heading", name="Your privacy, your choice.")).to_be_visible()
                expect(page.locator("#h-cookie").get_by_role("link", name="Privacy policy")).to_be_visible()
                page.screenshot(path=str(output / f"{device}-banner.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    if errors:
        raise AssertionError(errors)
    print("Captured desktop and mobile generic policy and consent screenshots in " + str(output))


if __name__ == "__main__":
    main()

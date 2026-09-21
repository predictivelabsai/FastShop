"""Local consent/network interception checks; never contacts a real analytics property."""

import json
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from app.config import settings


def main():
    if settings.db_url or not str(settings.data_dir).startswith("/tmp/fastshop-") or settings.public_url != "http://127.0.0.1:5043":
        raise SystemExit("Requires isolated /tmp/fastshop-* data, empty DB_URL and http://127.0.0.1:5043.")
    from app.db import SessionLocal
    from app.main import app
    from app.models import Site

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        site.status = "published"
        site_id = site.id
        db.commit()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=5043, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Local analytics verifier did not start")
    output = Path("output/playwright/phase2-analytics")
    output.mkdir(parents=True, exist_ok=True)
    root = settings.public_url + "/sites/h24you"
    requests, errors = [], []
    measurement = "G-TEST123456"

    def intercept(route):
        host = urlsplit(route.request.url).hostname or ""
        if host == "www.googletagmanager.com":
            requests.append(route.request.url)
            assert route.request.headers.get("referer", "") == ""
            route.fulfill(content_type="application/javascript", body="window.__gaFixtureLoaded = true;")
        elif host != "127.0.0.1":
            route.abort()  # No real Google, advertising or other external requests.
        else:
            route.continue_()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.route("**/*", intercept)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(settings.public_url + "/login?next=/admin/sites/" + site_id)
            page.locator('input[name="email"]').fill(settings.admin_email)
            page.locator('input[name="password"]').fill(settings.admin_password)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.goto(settings.public_url + "/admin/sites/" + site_id)
            page.locator('input[name="ga4_measurement_id"]').fill(measurement)
            page.get_by_role("button", name="Publish shared settings", exact=True).click()
            page.wait_for_load_state("networkidle")
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.locator('input[name="ga4_measurement_id"]').scroll_into_view_if_needed()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(output / f"{device}-analytics-settings.png"))
            page.goto(root + "/?email=fixture-private&token=fixture-private#fixture-private")
            page.wait_for_load_state("networkidle")
            assert requests == []
            page.locator('[data-consent="none"]').click()
            page.reload(wait_until="networkidle")
            assert requests == []
            page.get_by_role("button", name="Cookie preferences", exact=True).click()
            page.locator('#h-cookie summary').click()
            page.locator('#consent-analytics').check()
            page.locator('[data-consent="custom"]').click()
            page.wait_for_function("window.__gaFixtureLoaded === true")
            assert requests == ["https://www.googletagmanager.com/gtag/js?id=" + measurement]
            commands = page.evaluate("window.dataLayer.map(args => Array.from(args))")
            serialized = json.dumps(commands, default=str)
            assert "fixture-private" not in serialized
            assert len([row for row in commands if row[:2] == ["event", "page_view"]]) == 1
            config = next(row[2] for row in commands if row[0] == "config")
            assert config["cookie_prefix"] == "fs_" + site_id
            assert config["cookie_path"] == "/sites/h24you" and config["allow_google_signals"] is False
            page.get_by_role("button", name="Cookie preferences", exact=True).click()
            page.locator('[data-consent="custom"]').click()
            assert len(requests) == 1  # Repeated grant does not reload or duplicate page_view.
            page.evaluate("name => { document.cookie = name + '=fixture; Path=/sites/h24you'; document.cookie = 'unrelated_fixture=keep; Path=/'; }", "fs_" + site_id + "_ga")
            second = context.new_page()
            second.goto(root + "/")
            second.wait_for_function("window.__gaFixtureLoaded === true")
            assert len(requests) == 2
            second.get_by_role("button", name="Cookie preferences", exact=True).click()
            with page.expect_navigation(wait_until="networkidle"):
                second.locator('[data-consent="none"]').click()
            second.wait_for_load_state("networkidle")
            second.close()
            assert len(requests) == 2
            cookies = {row["name"] for row in context.cookies()}
            assert "fs_" + site_id + "_ga" not in cookies and "unrelated_fixture" in cookies
            page.get_by_role("button", name="Cookie preferences", exact=True).click()
            page.locator('#h-cookie summary').click()
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.screenshot(path=str(output / f"{device}-consent-withdrawn.png"))
            assert not page.locator('#consent-analytics').is_checked()
            # Malformed consent must not silently opt a visitor in.
            page.evaluate("key => { localStorage.setItem(key, JSON.stringify({version:1, analytics:'true', marketing:false})); }", "fastshop-consent:" + site_id)
            page.reload(wait_until="networkidle")
            expect(page.locator('#h-cookie')).to_be_visible()
            assert len(requests) == 2
            # GPC overrides even an explicit local analytics grant.
            private = browser.new_context()
            private.route("**/*", intercept)
            private.add_init_script("Object.defineProperty(navigator, 'globalPrivacyControl', {value:true});")
            other = private.new_page()
            other.goto(root + "/")
            other.locator('[data-consent="all"]').click()
            other.wait_for_load_state("networkidle")
            assert len(requests) == 2
            private.close()
            browser.close()
        assert not errors, errors
        print({"status": "passed", "provider": "intercepted fixture only", "no_preconsent_requests": True, "withdrawal": True, "desktop_mobile": True})
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()

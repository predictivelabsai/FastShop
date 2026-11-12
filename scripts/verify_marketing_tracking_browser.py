"""Capture tracking settings and verify consent-gated storefront loading locally."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/tracking"
IDS = {
    "ga4_measurement_id": "G-TEST123456",
    "google_ads_conversion_id": "AW-1234567890",
    "google_ads_purchase_label": "Purchase_1",
    "meta_pixel_id": "123456789012345",
    "tiktok_pixel_id": "C1234567890123456789",
}
TRACKING_HOSTS = {
    "www.googletagmanager.com": "window.__googleFixtureLoaded = true;",
    "connect.facebook.net": "window.__metaFixtureLoaded = true;",
    "analytics.tiktok.com": "window.__tiktokFixtureLoaded = true;",
}


def login(page, path: str) -> None:
    page.goto(f"/login?next={path}")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url(f"**{path}")


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    os.environ["FASTSHOP_BROWSER_DB_URL"] = ""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-tracking-") as data_dir:
        isolate(data_dir, port)
        from app import content
        from app.db import SessionLocal, engine
        from app.main import app
        from app.models import Site, SitePage, User
        from sqlalchemy import select

        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == EMAIL))
            if not user:
                raise RuntimeError("The disposable browser user was not created")
            site = content.create_site(db, user.id, "Tracking sample store", "tracking-sample-store")
            slug = site.slug
            home = db.scalar(select(SitePage).where(
                SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
                SitePage.path == "/",
            ))
            if not home:
                home = content.create_page(
                    db, site, "Tracking check", "/", kind="home",
                    document={"title": "Tracking check", "sections": []},
                )
            content.save_page(db, site, home.id, user.id, home.draft_json,
                              home.version, action="publish")
            site_id = site.id
            db.commit()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error", access_log=False,
        ))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local tracking verification server failed to start")
                thread.join(0.05)

            requests: list[str] = []
            errors: list[str] = []

            def intercept(route):
                host = urlsplit(route.request.url).hostname or ""
                if host in TRACKING_HOSTS:
                    requests.append(route.request.url)
                    route.fulfill(
                        content_type="application/javascript",
                        body=TRACKING_HOSTS[host],
                    )
                elif host == "127.0.0.1":
                    route.continue_()
                else:
                    route.abort()

            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    base = f"http://127.0.0.1:{port}"
                    tracking_path = f"/admin/sites/{site_id}/marketing/tracking"
                    for device, width, height in (
                        ("desktop", 1440, 1000), ("mobile", 390, 844),
                    ):
                        context = browser.new_context(
                            base_url=base,
                            viewport={"width": width, "height": height},
                            reduced_motion="reduce",
                        )
                        context.route("**/*", intercept)
                        page = context.new_page()
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        login(page, "/admin/sites")
                        response = page.goto(tracking_path)
                        assert response and response.status == 200
                        expect(page.get_by_role("heading", name="Provider IDs")).to_be_visible()
                        if device == "desktop":
                            for field, value in IDS.items():
                                page.locator(f'input[name="{field}"]').fill(value)
                            page.get_by_role("button", name="Publish settings", exact=True).click()
                            page.wait_for_url("**/marketing/tracking?notice=*")
                        page.evaluate("document.fonts.ready")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                        page.screenshot(
                            path=str(OUTPUT / f"admin-{device}.png"), full_page=True,
                        )
                        context.close()

                    with SessionLocal() as db:
                        site = db.scalar(select(Site).where(Site.id == site_id))
                        site.status = "published"
                        db.commit()

                    context = browser.new_context(base_url=base, viewport={"width": 1440, "height": 900})
                    context.route("**/*", intercept)
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"/sites/{slug}/", wait_until="networkidle")
                    expect(page.locator("#h-cookie")).to_be_visible()
                    page.wait_for_timeout(500)
                    assert requests == [], f"Provider request fired before consent: {requests}"
                    assert page.evaluate("typeof window.gtag") == "undefined"
                    assert page.evaluate("typeof window.fbq") == "undefined"
                    page.locator('#h-cookie [data-consent="all"]').click()
                    page.wait_for_function(
                        "window.gtag && window.fbq && window.ttq", timeout=10000,
                    )
                    page.wait_for_function(
                        "window.__googleFixtureLoaded && window.__metaFixtureLoaded "
                        "&& window.__tiktokFixtureLoaded",
                        timeout=10000,
                    )
                    assert len(requests) == 3, requests
                    assert any("G-TEST123456" in url for url in requests)
                    assert any("connect.facebook.net" in url for url in requests)
                    assert any("C1234567890123456789" in url for url in requests)
                    commands = page.evaluate("window.dataLayer.map(args => Array.from(args))")
                    default = next(row[2] for row in commands if row[:2] == ["consent", "default"])
                    update = next(row[2] for row in commands if row[:2] == ["consent", "update"])
                    assert default == {
                        "analytics_storage": "denied",
                        "ad_storage": "denied",
                        "ad_user_data": "denied",
                        "ad_personalization": "denied",
                    }
                    assert update == {
                        "analytics_storage": "granted",
                        "ad_storage": "granted",
                        "ad_user_data": "granted",
                        "ad_personalization": "granted",
                    }
                    assert not errors, errors
                    print({
                        "status": "passed",
                        "provider_requests_before_consent": 0,
                        "provider_requests_after_consent": len(requests),
                        "gtag_defined_after_consent": True,
                        "fbq_defined_after_consent": True,
                        "tiktok_loaded_after_consent": True,
                        "screenshots": str(OUTPUT),
                    })
                    context.close()
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

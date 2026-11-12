"""Capture the isolated Custom CSS draft and publish journey on desktop and mobile."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/design-overrides"
EMAIL = "design-overrides-browser@example.test"
PASSWORD = "LocalBrowser2026$"


def isolate(data_dir: str, port: int) -> None:
    """Use a disposable local database and no provider credentials."""
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "DB_URL": "",
        "FASTSHOP_ENV": "development",
        "FASTSHOP_AUTO_CREATE_SCHEMA": "1",
        "FASTSHOP_DATA_DIR": data_dir,
        "FASTSHOP_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "FASTSHOP_HOST": "127.0.0.1",
        "FASTSHOP_PORT": str(port),
        "FASTSHOP_ADMIN_EMAIL": EMAIL,
        "FASTSHOP_ADMIN_PASSWORD": PASSWORD,
        "FASTSHOP_ADMIN_PASSWORD_HASH": "",
        "FASTSHOP_ALLOW_PASSWORD_LOGIN": "1",
        "FASTSHOP_ENABLE_SCHEDULER": "0",
        "FASTSHOP_IMAGE_PROVIDER": "placeholders",
        "FASTSHOP_SESSION_SECRET": uuid4().hex + uuid4().hex,
    })
    for key in (
        "XAI_API_KEY", "POSTMARK_API_TOKEN", "POSTMARK_SERVER_TOKEN", "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET", "FASTSME_API_TOKEN", "FASTERP_API_TOKEN", "FASTERP_BASE_URL",
        "FASTERP_COMPANY_ID", "FASTSHOP_CONNECTOR_TOKEN", "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET", "FASTSHOP_BILLING_STRIPE_SECRET_KEY",
        "FASTSHOP_BILLING_STRIPE_WEBHOOK_SECRET", "FASTSHOP_ENCRYPTION_KEY",
        "FASTSHOP_STORE_KEY_ENCRYPTION_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "PAYPAL_MODE",
    ):
        os.environ[key] = ""


def login(page) -> None:
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")


def assert_no_horizontal_overflow(page, label: str) -> None:
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), label


def main() -> None:
    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    OUTPUT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="fastshop-design-overrides-") as data_dir:
        isolate(data_dir, port)
        import uvicorn
        from sqlalchemy import select

        from app import content
        from app.db import SessionLocal, engine
        from app.main import app
        from app.models import User

        with SessionLocal() as db:
            owner = db.scalar(select(User).where(User.email == EMAIL))
            if owner is None:
                raise AssertionError("The disposable administrator was not created.")
            site = content.create_site(db, owner.id, "Custom CSS studio", "custom-css-browser")
            page = next(item for item in content.site_pages(db, site) if item.path == "/")
            page.published_json = dict(page.draft_json)
            site.settings_json = site.settings_json | {"theme_pack": "liquid"}
            site.published_settings_json = site.published_settings_json | {"theme_pack": "liquid"}
            site.status = "published"
            db.commit()
            site_id, site_slug, page_id = site.id, site.slug, page.id

        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        browser = None
        errors = []
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated Custom CSS server failed to start.")
                thread.join(0.05)

            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Error:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(
                    base_url=f"http://127.0.0.1:{port}",
                    viewport={"width": 1440, "height": 1000},
                    reduced_motion="reduce",
                )
                context.route(
                    "**/*",
                    lambda route: route.continue_()
                    if route.request.url.startswith(f"http://127.0.0.1:{port}/")
                    else route.abort(),
                )
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.set_default_timeout(20_000)
                login(page)

                design_url = f"/admin/sites/{site_id}/build?view=design&page={page_id}"
                page.goto(design_url)
                expect(page.get_by_role("heading", name="Custom CSS", exact=True)).to_be_visible()
                expect(page.get_by_label("Custom CSS", exact=True)).to_be_visible()
                custom_css = page.locator("#custom-css")
                custom_css.scroll_into_view_if_needed()
                assert_no_horizontal_overflow(page, "Desktop Custom CSS panel overflows horizontally.")
                custom_css.screenshot(path=str(OUTPUT / "desktop-custom-css.png"))

                css = ".metal-header { outline: 3px solid rgb(8, 96, 65) !important; }"
                page.get_by_label("Custom CSS", exact=True).fill(css)
                page.get_by_role("button", name="Save draft", exact=True).click()
                expect(page.locator("#custom-css > p[role='status']")).to_have_text(
                    "Custom CSS draft saved."
                )
                preview = page.frame_locator(".b-preview")
                expect(preview.locator("[data-fs-site-root]")).to_be_visible()
                expect(preview.locator(".metal-header")).to_have_css(
                    "outline-color", "rgb(8, 96, 65)"
                )
                custom_css.screenshot(path=str(OUTPUT / "desktop-draft-preview.png"))

                page.get_by_role("button", name="Publish CSS", exact=True).click()
                expect(page.locator("#custom-css > p[role='status']")).to_have_text(
                    "Custom CSS published."
                )
                page.goto(f"/sites/{site_slug}/")
                expect(page.locator("#fs-design-overrides")).to_be_attached()
                expect(page.locator("[data-fs-site-root]")).to_be_visible()
                expect(page.locator(".metal-header")).to_have_css(
                    "outline-color", "rgb(8, 96, 65)"
                )
                assert_no_horizontal_overflow(page, "Published desktop storefront overflows horizontally.")
                page.screenshot(path=str(OUTPUT / "desktop-published-storefront.png"), full_page=True)

                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(design_url)
                expect(page.get_by_role("heading", name="Custom CSS", exact=True)).to_be_visible()
                assert css in page.locator("textarea[name='css']").input_value()
                assert_no_horizontal_overflow(page, "Mobile Custom CSS panel overflows horizontally.")
                page.screenshot(path=str(OUTPUT / "mobile-custom-css.png"), full_page=True)
                context.close()
                browser.close()
                browser = None
            if errors:
                raise AssertionError(errors)
            print(json.dumps({"status": "passed", "screenshots": str(OUTPUT)}))
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Error:
                    pass
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

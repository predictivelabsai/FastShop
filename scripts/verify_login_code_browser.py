"""Capture isolated desktop and mobile customer login-code pages with fake mail."""

import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    data_dir = tempfile.mkdtemp(prefix="fastshop-login-code-")
    os.environ.pop("FASTSHOP_PUBLIC_URL", None)
    os.environ.update({
        "DB_URL": "", "FASTSHOP_ENV": "development",
        "FASTSHOP_SESSION_SECRET": secrets.token_hex(32),
        "FASTSHOP_AUTO_CREATE_SCHEMA": "1", "FASTSHOP_DATA_DIR": data_dir,
        "XAI_API_KEY": "", "POSTMARK_API_TOKEN": "", "POSTMARK_SERVER_TOKEN": "",
        "STRIPE_SECRET_KEY": "", "STRIPE_WEBHOOK_SECRET": "",
        "STRIPE_PUBLISHABLE_KEY": "", "FASTSHOP_ENABLE_SCHEDULER": "0",
    })
    from sqlalchemy import select

    import app.customer_routes as customer_routes
    from app import commerce
    from app.db import SessionLocal
    from app.main import app  # noqa: F401
    from app.models import Site

    customer_routes.dispatch_mail = lambda _message_id: False
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        if not site:
            site = db.scalar(select(Site).where(Site.status.in_(["preview", "published"])))
        if not site:
            raise RuntimeError("No preview or published store is available for the browser capture.")
        original_published = dict(site.published_settings_json or {})
        commerce.settings_for(db, site, create=True).customer_accounts_enabled = True
        slug = site.slug
        db.commit()

    output = Path("output/playwright/login-code")
    output.mkdir(parents=True, exist_ok=True)
    port = 5047
    root = f"http://127.0.0.1:{port}/sites/{slug}"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError("The isolated browser server exited before it was ready.")
            try:
                if httpx.get(root + "/", trust_env=False, timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("The isolated browser server did not start.")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            for pack in ("generic", "liquid"):
                with SessionLocal() as db:
                    stored_site = db.scalar(select(Site).where(Site.slug == slug))
                    published = dict(original_published)
                    if pack == "liquid":
                        published["theme_pack"] = "liquid"
                    else:
                        published.pop("theme_pack", None)
                    stored_site.published_settings_json = published
                    db.commit()
                prefix = "" if pack == "generic" else "liquid-"
                for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                    context = browser.new_context(viewport={"width": width, "height": height},
                                                  reduced_motion="reduce")
                    page = context.new_page()
                    page.goto(root + "/account")
                    page.get_by_role("heading", name="Login", level=2).wait_for()
                    decline = page.get_by_role("button", name="Decline", exact=True)
                    if decline.count():
                        decline.click()
                    page.screenshot(path=str(output / f"{prefix}{device}-login.png"), full_page=True)
                    page.get_by_role("textbox", name="Email", exact=True).fill(
                        f"{pack}-{device}@example.test")
                    page.get_by_role("button", name="Send login code").click()
                    page.get_by_label("Login code").wait_for()
                    decline = page.get_by_role("button", name="Decline", exact=True)
                    if decline.count():
                        decline.click()
                    page.screenshot(path=str(output / f"{prefix}{device}-verify.png"), full_page=True)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    context.close()
            browser.close()
        print(f"Login-code browser captures saved to {output.resolve()}")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()

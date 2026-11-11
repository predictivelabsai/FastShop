"""Capture isolated desktop and mobile customer login-code pages with fake mail."""

import atexit
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    data_root = Path(__file__).resolve().parents[1] / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    data_dir = tempfile.mkdtemp(prefix="fastshop-login-code-", dir=data_root)
    atexit.register(shutil.rmtree, data_dir, ignore_errors=True)
    os.environ.pop("FASTSHOP_PUBLIC_URL", None)
    os.environ.update({
        "DB_URL": "", "FASTSHOP_ENV": "development",
        "FASTSHOP_SESSION_SECRET": secrets.token_hex(32),
        "FASTSHOP_AUTO_CREATE_SCHEMA": "1", "FASTSHOP_DATA_DIR": data_dir,
        "XAI_API_KEY": "", "POSTMARK_API_TOKEN": "", "POSTMARK_SERVER_TOKEN": "",
        "STRIPE_SECRET_KEY": "", "STRIPE_WEBHOOK_SECRET": "",
        "STRIPE_PUBLISHABLE_KEY": "", "FASTSHOP_ENABLE_SCHEDULER": "0",
    })
    import app.customer_routes as customer_routes
    from app import commerce
    from app.config import settings
    from app.db import SessionLocal
    from app.main import app  # noqa: F401
    from scripts.demo_site import seed_demo_site

    customer_routes.dispatch_mail = lambda _message_id: False
    with SessionLocal() as db:
        site = seed_demo_site(db, settings.admin_email)
        published = dict(site.published_settings_json or {})
        published["account_copy"] = {
            "heading": "Customer sign in", "email_label": "Email address",
            "send_code": "Email me a code", "code_label": "One-time code",
            "verify_code": "Verify sign in", "sent_message": "A code is on its way.",
            "invalid_code": "That code did not work. Request another one.",
        }
        site.published_settings_json = published
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
            for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                context = browser.new_context(viewport={"width": width, "height": height},
                                              reduced_motion="reduce")
                page = context.new_page()
                page.goto(root + "/account")
                page.get_by_role("heading", name="Customer sign in", level=2).wait_for()
                page.screenshot(path=str(output / f"{device}-login.png"), full_page=True)
                page.get_by_role("textbox", name="Email address", exact=True).fill(
                    f"{device}@example.test")
                page.get_by_role("button", name="Email me a code").click()
                page.get_by_label("One-time code").wait_for()
                assert "A code is on its way." in page.content()
                page.screenshot(path=str(output / f"{device}-verify.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                context.close()
            browser.close()
        print(f"Login-code browser captures saved to {output.resolve()}")
    finally:
        process.terminate()
        process.wait(timeout=10)
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

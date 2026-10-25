"""Local isolated account opt-in journeys and desktop/mobile screenshots."""

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
    data_dir = tempfile.mkdtemp(prefix="fastshop-account-browser-")
    os.environ.update({
        "DB_URL": "", "FASTSHOP_ENV": "development", "PYTHON_DOTENV_DISABLED": "1",
        "FASTSHOP_SESSION_SECRET": secrets.token_hex(32),
        "FASTSHOP_AUTO_CREATE_SCHEMA": "1", "FASTSHOP_DATA_DIR": data_dir,
        "XAI_API_KEY": "", "POSTMARK_API_TOKEN": "", "POSTMARK_SERVER_TOKEN": "",
        "STRIPE_SECRET_KEY": "", "STRIPE_WEBHOOK_SECRET": "",
        "FASTSHOP_ENABLE_SCHEDULER": "0",
        "FASTSHOP_PUBLIC_URL": "http://127.0.0.1:5039",
    })
    from sqlalchemy import select

    from app import commerce, content, customer_services
    from app.config import settings
    from app.db import SessionLocal
    from app.main import app  # noqa: F401
    from app.models import CustomerChallenge, ShopCustomer, Site, User

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, user.id, "Account fixture", "account-fixture")
        site.status = "preview"
        for page in content.site_pages(db, site):
            page.published_json = page.draft_json
        config = commerce.settings_for(db, site, create=True)
        config.mode = "sandbox"
        config.customer_accounts_enabled = False
        site_id = site.id
        db.commit()
    output = Path("output/playwright/customer-account-opt-in")
    output.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", "5039"],
        env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    root = "http://127.0.0.1:5039/sites/account-fixture"
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError("Local browser server exited before it was ready.")
            try:
                if httpx.get(root + "/", trust_env=False, timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("Local browser server did not start.")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            errors = []
            for enabled in [False, True]:
                with SessionLocal() as db:
                    commerce.settings_for(db, db.get(Site, site_id)).customer_accounts_enabled = enabled
                    db.commit()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    context = browser.new_context(viewport={"width": width, "height": height},
                                                  reduced_motion="reduce")
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(root + "/")
                    assert page.locator(".h-header-actions").get_by_role("link", name="My account").count() == int(enabled)
                    state = "on" if enabled else "off"
                    page.screenshot(path=str(output / f"{device}-header-{state}.png"), full_page=True)
                    response = page.goto(root + "/account")
                    assert response.status == (200 if enabled else 404)
                    if enabled:
                        page.get_by_role("heading", name="Sign in with an email link").wait_for()
                        page.screenshot(path=str(output / f"{device}-account-on.png"), full_page=True)
                        email = f"{device}@example.test"
                        page.get_by_label("Email address").fill(email)
                        page.get_by_role("button", name="Email me a sign-in link").click()
                        with SessionLocal() as db:
                            challenge = db.scalar(select(CustomerChallenge).join(ShopCustomer, ShopCustomer.id == CustomerChallenge.customer_id).where(
                                CustomerChallenge.site_id == site_id,
                                CustomerChallenge.tenant_id == site.tenant_id,
                                ShopCustomer.email == email,
                                ShopCustomer.site_id == site_id,
                                ShopCustomer.tenant_id == site.tenant_id,
                                CustomerChallenge.purpose == "login",
                            ).order_by(CustomerChallenge.created_at.desc()))
                            challenge_id = challenge.id
                            token = customer_services.challenge_token(challenge)
                        page.goto(root + "/account/verify/" + challenge_id)
                        page.get_by_label("Verification code").fill(token)
                        page.get_by_role("button", name="Confirm", exact=True).click()
                        page.get_by_role("heading", name="Your orders").wait_for()
                        page.screenshot(path=str(output / f"{device}-signed-in.png"), full_page=True)
                    else:
                        page.get_by_text("Customer accounts are not available for this store", exact=True).wait_for()
                        assert page.locator("form").count() == 0
                        page.screenshot(path=str(output / f"{device}-account-off.png"), full_page=True)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    context.close()
            browser.close()
            assert not errors, errors
        print("Desktop and mobile account off/on journeys passed; no browser errors.")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()

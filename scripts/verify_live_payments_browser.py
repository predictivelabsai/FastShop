"""Capture Go-live mode states using a fresh local database and fake Stripe verifier."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "output/playwright/live-payments"
EMAIL = "live-payments-browser@example.test"
PASSWORD = "LocalLivePayments2026$"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    port = free_port()
    data_dir = tempfile.mkdtemp(prefix="fastshop-live-payments-browser-")
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1", "DB_URL": "", "FASTSHOP_ENV": "development",
        "FASTSHOP_AUTO_CREATE_SCHEMA": "1", "FASTSHOP_DATA_DIR": data_dir,
        "FASTSHOP_PUBLIC_URL": f"http://127.0.0.1:{port}", "FASTSHOP_HOST": "127.0.0.1",
        "FASTSHOP_PORT": str(port), "FASTSHOP_ADMIN_EMAIL": EMAIL,
        "FASTSHOP_ADMIN_PASSWORD": PASSWORD, "FASTSHOP_ADMIN_PASSWORD_HASH": "",
        "FASTSHOP_ALLOW_PASSWORD_LOGIN": "1", "FASTSHOP_ENABLE_SCHEDULER": "0",
        "FASTSHOP_IMAGE_PROVIDER": "placeholders",
        "FASTSHOP_SESSION_SECRET": uuid4().hex + uuid4().hex,
        "FASTSHOP_STORE_KEY_ENCRYPTION_KEY": "local-browser-encryption-fixture-only",
    })
    for key in ("XAI_API_KEY", "POSTMARK_API_TOKEN", "POSTMARK_SERVER_TOKEN",
                "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PUBLISHABLE_KEY"):
        os.environ[key] = ""

    from sqlalchemy import select

    from app import commerce, content
    from app.db import SessionLocal
    from app.integrations.stripe_commerce import StripeGateway
    from app.main import app
    from app.models import Membership, Site, User

    def fake_status(self):
        return {
            "connected": True, "mode": "live" if self.key.startswith(("sk_live_", "rk_live_")) else "test",
            "charges_enabled": True, "account_id": "acct_live_browser_fixture",
            "account_name": "Local Live Payments Shop", "country": "US",
            "tax_settings_active": True, "webhook_secret_configured": True,
            "provider_acceptance_complete": False,
        }

    StripeGateway.verify_connection = fake_status
    StripeGateway.connection_status = lambda self: {
        key: value for key, value in fake_status(self).items()
        if key not in {"account_id", "account_name", "country"}
    }
    StripeGateway.create_webhook_endpoint = lambda self, url: (
        "we_live_browser_fixture", "whsec_live_browser_fixture_9876",
    )

    with SessionLocal() as db:
        operator = db.scalar(select(User).where(User.email == EMAIL))
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        if site is None:
            site = content.create_site(db, operator.id, "Live payments fixture", "live-payments-fixture")
        site.status = "published"
        site.hostname = "live-payments.example.test"
        config = commerce.settings_for(db, site, create=True)
        config.mode = "sandbox"
        config.customer_accounts_enabled = True
        if not db.scalar(select(Membership.id).where(
                Membership.tenant_id == site.tenant_id, Membership.user_id == operator.id)):
            db.add(Membership(tenant_id=site.tenant_id, user_id=operator.id, role="admin"))
        site_id = site.id
        db.commit()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Local live payments server did not start")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{port}"
    golive = f"{base}/admin/sites/{site_id}/golive"
    payments = f"{base}/admin/sites/{site_id}/commerce/payments"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(base + "/login")
            page.get_by_label("Email address", exact=True).fill(EMAIL)
            page.get_by_label("Password", exact=True).fill(PASSWORD)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/admin/sites")
            for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                page.goto(golive)
                expect(page.get_by_role("button", name="Turn on live payments")).to_be_disabled()
                page.screenshot(path=str(OUTPUT / f"{device}-before.png"), full_page=True)

            page.goto(payments)
            page.get_by_label("Connection mode").select_option("live")
            page.get_by_label("Secret key or restricted key").fill("sk_live_browser_fixture_1234")
            page.get_by_label("Publishable key").fill("pk_live_browser_fixture_5678")
            page.get_by_label("Webhook signing secret (manual fallback)").fill("whsec_live_browser_fixture_1234")
            page.get_by_role("button", name="Save and verify connection").click()
            page.wait_for_url("**/commerce/payments?notice=*")
            if "acct_live_browser_fixture" not in page.content():
                raise RuntimeError("Stripe verification did not save: " + page.locator("body").inner_text())
            page.goto(golive)
            if not page.get_by_role("button", name="Turn on live payments").is_enabled():
                raise RuntimeError("Live connection did not enable the control: " + page.locator("body").inner_text())
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Turn on live payments").click()
            page.wait_for_url("**/golive?notice=*")
            expect(page.get_by_text("Live payments on", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Switch back to test mode")).to_be_visible()
            for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                page.goto(golive)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(OUTPUT / f"{device}-after.png"), full_page=True)
            page.goto(f"{base}/sites/h24you/")
            storefront = page.locator("body").inner_text()
            assert "Sandbox · Test purchases only" not in storefront
            assert "Sandbox · No live payments" not in storefront
            for public_path in ("/cart", "/account"):
                page.goto(f"{base}/sites/h24you{public_path}")
                body = page.locator("body").inner_text()
                assert "Sandbox · Test purchases only" not in body
                assert "Sandbox · No live payments" not in body
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    print(f"Go-live screenshots saved to {OUTPUT}")


if __name__ == "__main__":
    main()

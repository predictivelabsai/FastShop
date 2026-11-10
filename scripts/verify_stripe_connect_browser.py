"""Capture disconnected and connected Stripe Payments states with a fake gateway."""

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
OUTPUT = ROOT / "output/playwright/payments"
EMAIL = "stripe-connect-browser@example.test"
PASSWORD = "LocalStripeBrowser2026$"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    port = free_port()
    data_dir = tempfile.mkdtemp(prefix="fastshop-stripe-browser-")
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
    for key in ("XAI_API_KEY", "POSTMARK_API_TOKEN", "POSTMARK_SERVER_TOKEN", "STRIPE_SECRET_KEY",
                "STRIPE_WEBHOOK_SECRET", "STRIPE_PUBLISHABLE_KEY"):
        os.environ[key] = ""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.integrations.stripe_commerce import StripeGateway
    from app.main import app
    from app.models import Site

    def fake_status(self):
        return {
            "connected": True, "mode": "test", "charges_enabled": True,
            "account_id": "acct_browser_fixture", "account_name": "Local Stripe Shop",
            "country": "EE", "tax_settings_active": True,
            "webhook_secret_configured": True, "provider_acceptance_complete": False,
        }

    StripeGateway.verify_connection = fake_status
    StripeGateway.connection_status = lambda self: {
        key: value for key, value in fake_status(self).items()
        if key not in {"account_id", "account_name", "country"}
    }
    StripeGateway.create_webhook_endpoint = lambda self, url: ("we_browser_fixture", "whsec_browser_fixture_1234")
    StripeGateway.delete_webhook_endpoint = lambda self, endpoint_id: {"id": endpoint_id, "deleted": True}

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        assert site is not None
        site_id = site.id

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Local Stripe Payments server did not start")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    url = f"http://127.0.0.1:{port}/admin/sites/{site_id}/commerce/payments"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{port}/login")
            page.get_by_label("Email address", exact=True).fill(EMAIL)
            page.get_by_label("Password", exact=True).fill(PASSWORD)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/admin/sites")
            response = page.goto(url)
            if response is None or response.status != 200:
                raise RuntimeError(f"Payments page returned {response.status if response else 'no response'}: {page.locator('body').inner_text()}")
            expect(page.get_by_role("heading", name="Payments")).to_be_visible()
            expect(page.get_by_role("heading", name="Connect Stripe")).to_be_visible()
            expect(page.get_by_text("Not connected").first).to_be_visible()

            for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                page.goto(url)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(OUTPUT / f"{device}-not-connected.png"), full_page=True)

            page.get_by_label("Secret key or restricted key").fill("sk_test_browser_fixture_1234")
            page.get_by_label("Publishable key").fill("pk_test_browser_fixture_5678")
            page.get_by_text("Advanced · webhook settings").click()
            page.get_by_label("Webhook signing secret (manual fallback)").fill("whsec_browser_manual_1234")
            page.get_by_role("button", name="Connect Stripe").click()
            page.wait_for_url("**/commerce/payments?notice=*")
            expect(page.get_by_text("Local Stripe Shop")).to_be_visible()
            expect(page.get_by_text("Connected", exact=True).first).to_be_visible()
            expect(page.locator(".payment-card-confirmation")).to_be_visible()
            assert "sk_test_browser_fixture_1234" not in page.content()
            assert "pk_test_browser_fixture_5678" not in page.content()

            for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                page.goto(url)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(OUTPUT / f"{device}-test-connected.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    if errors:
        raise AssertionError("Browser page errors: " + "; ".join(errors))
    print(f"Stripe Payments screenshots saved to {OUTPUT}")


if __name__ == "__main__":
    main()

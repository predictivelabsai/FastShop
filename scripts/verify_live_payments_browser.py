"""Capture Go-live mode states using a fresh local database and fake Stripe verifier."""

from __future__ import annotations

import copy
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
OUTPUT = ROOT / "output/playwright/golive"
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

    from app import commerce
    from app.db import SessionLocal
    from app.integrations.stripe_commerce import StripeGateway
    from app.main import app
    from app.models import (
        Membership,
        Product,
        ProductVariant,
        SitePage,
        User,
        VariantChannelListing,
    )
    from app.site_golive import COMMERCE_POLICY_PATHS
    from scripts.demo_site import seed_demo_site

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
        site = seed_demo_site(db, EMAIL)
        site.status = "published"
        site.hostname = "live-payments.example.test"
        config = commerce.settings_for(db, site, create=True)
        config.mode = "sandbox"
        config.customer_accounts_enabled = True
        pages = {page.path: page for page in db.scalars(select(SitePage).where(
            SitePage.site_id == site.id,
            SitePage.tenant_id == site.tenant_id,
        ))}
        for path in COMMERCE_POLICY_PATHS:
            page = pages[path]
            published = copy.deepcopy(page.draft_json)
            if published.get("blocks"):
                published["blocks"][0]["body"] = "Reviewed store policy for this browser verification."
            page.published_json = published

        products = list(db.scalars(select(Product).where(
            Product.tenant_id == site.tenant_id,
            Product.is_published.is_(True),
        )))
        variants = list(db.scalars(select(ProductVariant).where(
            ProductVariant.tenant_id == site.tenant_id,
            ProductVariant.product_id.in_([product.id for product in products]) if products else False,
            ProductVariant.is_active.is_(True),
        )))
        listings = {row.variant_id: row for row in db.scalars(select(VariantChannelListing).where(
            VariantChannelListing.channel_id == site.channel_id,
            VariantChannelListing.variant_id.in_([variant.id for variant in variants]) if variants else False,
        ))}
        for index, variant in enumerate(variants, start=1):
            if variant.id not in listings:
                db.add(VariantChannelListing(
                    variant_id=variant.id, channel_id=site.channel_id,
                    currency="USD", price_minor=2500 + index * 100,
                ))
        config.origin_json = {"country": "EE", "line1": "Test warehouse", "city": "Tallinn",
                             "postal_code": "10111"}
        config.shipping_minor = 900
        config.allowed_states_json = ["CA"]
        config.tax_registration_reviewed = True
        config.product_tax_codes_json = {product.id: "txcd_00000000" for product in products}
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
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(base + "/login?next=/admin/sites")
            page.get_by_label("Email address", exact=True).fill(EMAIL)
            page.get_by_label("Password", exact=True).fill(PASSWORD)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/admin/sites")
            def capture_state(state: str) -> None:
                for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(golive)
                    expect(page.get_by_role("heading", name="Payments", exact=True)).to_be_visible()
                    expected_mode = {
                        "checkout-off": "Checkout off",
                        "test-mode": "Test mode",
                        "live-payments": "Live payments",
                    }[state]
                    expect(page.locator(".g-status-chip").nth(2)).to_contain_text(expected_mode)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(OUTPUT / f"{state}-{device}.png"), full_page=True)

            page.goto(golive)
            expect(page.get_by_role("button", name="Live payments", exact=True)).to_be_disabled()
            page.get_by_role("button", name="Turn off checkout", exact=True).click()
            page.wait_for_url("**/golive?notice=*")
            expect(page.get_by_text("Checkout off", exact=True)).to_be_visible()
            capture_state("checkout-off")

            page.get_by_role("button", name="Test mode", exact=True).click()
            page.wait_for_url("**/golive?notice=*")
            expect(page.get_by_text("Test mode", exact=True).first).to_be_visible()
            capture_state("test-mode")

            page.goto(payments)
            page.get_by_role("radio", name="Live", exact=True).check(force=True)
            page.get_by_label("Secret key or restricted key").fill("sk_live_browser_fixture_1234")
            page.get_by_label("Publishable key").fill("pk_live_browser_fixture_5678")
            page.get_by_text("Advanced · webhook settings", exact=True).click()
            page.get_by_label("Webhook signing secret (manual fallback)").fill("whsec_live_browser_fixture_1234")
            page.get_by_role("button", name="Connect Stripe", exact=True).click()
            page.wait_for_url("**/commerce/payments?notice=*")
            if "Local Live Payments Shop" not in page.content():
                raise RuntimeError("Stripe verification did not save: " + page.locator("body").inner_text())
            page.goto(golive)
            if not page.get_by_role("button", name="Live payments", exact=True).is_enabled():
                raise RuntimeError("Live connection did not enable the control: " + page.locator("body").inner_text())
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Live payments", exact=True).click()
            page.wait_for_url("**/golive?notice=*")
            expect(page.get_by_text("Live payments", exact=True).first).to_be_visible()
            capture_state("live-payments")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    print(f"Go-live screenshots saved to {OUTPUT}")


if __name__ == "__main__":
    main()

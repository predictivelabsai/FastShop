"""Reproducible Merchant Admin v2 journeys against a disposable local database.

Run from the repository root: python scripts/verify_admin_v2_browser.py
No environment file, browser session state or provider credentials are used.
"""

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

from playwright.sync_api import Error, Locator, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/admin-v2/p1"
EMAIL = "admin-v2-browser@example.test"
PASSWORD = "LocalBrowser2026$"
ORDER_NUMBER = "LOCAL-P1-1001"


def settle_admin_actions():
    """Wait for enhanced POST results as native clicks previously waited for navigation."""
    if getattr(Locator.click, "admin_settled", False):
        return
    native_click = Locator.click

    def click_and_settle(locator, *args, **kwargs):
        result = native_click(locator, *args, **kwargs)
        if not locator.page.is_closed():
            locator.page.wait_for_function(
                "!document.querySelector('.a-shell form[data-submitting=true]')", timeout=30000,
            )
        return result

    click_and_settle.admin_settled = True
    Locator.click = click_and_settle


def isolate(data_dir: str, port: int) -> None:
    """Set isolation before the first application import, including config."""
    settle_admin_actions()
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "DB_URL": os.environ.get("FASTSHOP_BROWSER_DB_URL", ""),
        "FASTSHOP_ENV": "development",
        "FASTSHOP_AUTO_CREATE_SCHEMA": "0" if os.environ.get("FASTSHOP_BROWSER_DB_URL") else "1",
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
    })
    for key in (
        "XAI_API_KEY", "POSTMARK_API_TOKEN", "POSTMARK_SERVER_TOKEN",
        "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "FASTSME_API_TOKEN",
        "FASTERP_API_TOKEN", "FASTSHOP_CONNECTOR_TOKEN", "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET", "FASTSHOP_BILLING_STRIPE_SECRET_KEY",
        "FASTSHOP_BILLING_STRIPE_WEBHOOK_SECRET", "FASTSHOP_ENCRYPTION_KEY",
        "FASTSHOP_STORE_KEY_ENCRYPTION_KEY",
    ):
        os.environ[key] = ""
    # Site-specific provider credentials must also remain unavailable.
    for key in tuple(os.environ):
        if key.startswith(("FASTSHOP_STRIPE_", "FASTSHOP_WOOCOMMERCE_")):
            os.environ[key] = ""


def seed_order() -> str:
    from sqlalchemy import select

    from app import customer_services
    from app.db import SessionLocal
    from app.models import Order, Site, SiteOrder

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        assert site is not None, "The existing h24you bootstrap site must exist"
        customer = customer_services.customer_for(db, site, "buyer-p1@example.test", create=True)
        order = Order(
            tenant_id=site.tenant_id, channel_id=site.channel_id,
            email=customer.email, number=ORDER_NUMBER, idempotency_key=uuid4().hex,
            currency="USD", subtotal_minor=2995, tax_minor=205,
            total_minor=3200, payment_status="paid",
        )
        db.add(order)
        db.flush()
        db.add(SiteOrder(tenant_id=site.tenant_id, site_id=site.id,
                         order_id=order.id, customer_id=customer.id))
        site_id = site.id
        db.commit()
        return site_id


def screenshot(page, device: str, name: str) -> None:
    expect(page.locator(".a-shell")).to_be_visible()
    expect(page.get_by_role("main")).to_be_visible()
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
        f"Horizontal overflow on {device} {name}"
    )
    page.screenshot(path=str(OUTPUT / f"{device}-{name}.png"), full_page=True)


def verify_device(browser, base: str, site_id: str, device: str,
                  width: int, height: int, errors: list[str]) -> None:
    context = browser.new_context(base_url=base, viewport={"width": width, "height": height},
                                  reduced_motion="reduce")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(f"{device}: {error}"))
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    screenshot(page, device, "stores")
    prefix = f"/admin/sites/{site_id}"
    for name, path in (
        ("home", ""), ("products", "/products"), ("orders", "/orders"),
        ("settings", "/settings"), ("collections", "/collections"),
    ):
        response = page.goto(prefix + path)
        assert response.status == 200, f"{device} {path}: HTTP {response.status}"
        expect(page.get_by_role("navigation", name="Main navigation", include_hidden=True)).to_be_attached()
        active = {"home": "Home", "products": "Products", "orders": "Orders",
                  "settings": "Settings", "collections": "Products"}[name]
        expect(page.get_by_role("complementary", name="Store administration", include_hidden=True)
               .get_by_role("link", name=active, exact=True, include_hidden=True)
               ).to_have_attribute("aria-current", "page")
        if name in {"home", "orders"}:
            expect(page.get_by_role("link", name=ORDER_NUMBER, exact=True)).to_be_visible()
        screenshot(page, device, name)
    if device == "mobile":
        toggle = page.get_by_role("button", name="Open navigation", exact=True)
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(page.get_by_role("navigation", name="Main navigation")).to_be_visible()
        page.keyboard.press("Escape")
        expect(toggle).to_have_attribute("aria-expanded", "false")
    page.keyboard.press("Control+k")
    search = page.get_by_role("combobox", name="Search store")
    expect(search).to_be_focused()
    search.fill(ORDER_NUMBER)
    options = page.get_by_role("listbox").get_by_role("option")
    expect(options.first).to_contain_text(ORDER_NUMBER)
    page.keyboard.press("ArrowDown")
    expect(options.first).to_have_attribute("aria-selected", "true")
    screenshot(page, device, "global-search")
    page.keyboard.press("Enter")
    page.wait_for_url(f"**{prefix}/orders/*")
    expect(page.get_by_role("main")).to_contain_text(ORDER_NUMBER)
    page.goto(prefix + "/settings/general")
    brand = page.get_by_label("Brand name", exact=True)
    original = brand.input_value()
    brand.fill(original + " local change")
    save_bar = page.get_by_role("region", name="Unsaved changes")
    expect(save_bar).to_be_visible()
    save_bar.get_by_role("button", name="Discard", exact=True).click()
    expect(brand).to_have_value(original)
    expect(save_bar).to_be_hidden()
    context.close()


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-admin-v2-") as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        try:
            site_id = seed_order()
        except BaseException:
            engine.dispose()
            raise
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local verification server failed to start")
                thread.join(0.05)
            errors: list[str] = []
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        verify_device(browser, f"http://127.0.0.1:{port}", site_id,
                                      device, width, height, errors)
                finally:
                    browser.close()
            assert not errors, errors
            print(json.dumps({"status": "passed", "screenshots": str(OUTPUT),
                              "count": 14, "browser_errors": errors}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

"""P3 browser acceptance on disposable SQLite with a local refund gateway double.

Run: .venv/Scripts/python.exe scripts/verify_admin_v2_orders_browser.py
No environment files, external providers, stored browser state or live data are used.
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/admin-v2/p3"
PRODUCT = "Everyday canvas tote"


class LocalRefundGateway:
    """Only injected by this local acceptance script; no network is available here."""
    def __init__(self, site):
        self.site = site

    def create_refund(self, command_id, payload):
        return {"id": "re_local_" + command_id, "payment_intent": payload["payment_intent"],
                "amount": payload["amount"], "currency": "usd", "livemode": False,
                "status": "succeeded"}


def seed_store(device):
    from sqlalchemy import select

    from app import catalog_admin, checkout_services, commerce, content, customer_services
    from app.db import SessionLocal
    from app.models import Order, PaymentTransaction, User, Warehouse

    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == EMAIL))
        site = content.create_site(db, owner.id, "Northfield Supply", "orders-browser-" + device)
        config = commerce.settings_for(db, site, create=True)
        config.mode = "sandbox"
        config.origin_json = {"country": "GB"}
        warehouse = Warehouse(tenant_id=site.tenant_id, code="MAIN", name="Main warehouse", country_code="GB")
        db.add(warehouse)
        db.flush()
        product = catalog_admin.create_product(db, site, {"name": PRODUCT, "status": "active"})
        variant = product.variants[0]
        catalog_admin.update_product(db, site, product.id, {"variants": [{"id": variant.id,
            "sku": "TOTE-P3-" + device.upper(), "price_minor": 3000,
            "quantities": {warehouse.id: 10}}]}, expected_version=product.version)
        customer = customer_services.customer_for(db, site, "ada-" + device + "@example.test", create=True)
        customer.name = "Ada Carter"
        destination = {"name": customer.name, "line1": "24 Market Street", "city": "Bristol",
                       "postal_code": "BS1 4QA", "country": "GB", "method": "Standard shipping"}
        snapshot = {"lines": [{"variant_id": variant.id, "name": product.name,
            "variant_name": variant.name, "sku": variant.sku, "quantity": 3, "unit_minor": 3000,
            "amount_minor": 9000, "subscription": False, "track_inventory": True,
            "continue_selling_when_out_of_stock": False}], "destination": destination,
            "recipient_name": customer.name, "origin": {"country": "GB"}}
        link = checkout_services.complete_manual_order(db, site, customer.id, snapshot,
            request_key="browser-seeded-order-" + device, currency="USD", subtotal_minor=9000,
            discount_minor=0, shipping_minor=500)
        db.get(Order, link.order_id).number = "FS-1001"
        # This disposable seed simulates a captured sandbox payment for refund acceptance.
        payment = db.scalar(select(PaymentTransaction).where(PaymentTransaction.order_id == link.order_id))
        payment.provider, payment.external_id = "stripe:" + site.id, "pi_local_" + device
        db.commit()
        return {"site": site.id, "link": link.id, "variant": variant.id,
                "customer": customer.id, "product": product.id, "email": customer.email}


def stock(fixture):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Stock
    with SessionLocal() as db:
        row = db.scalar(select(Stock).where(Stock.variant_id == fixture["variant"]))
        return row.quantity, row.allocated


def capture(page, device, name):
    dismiss = page.get_by_role("button", name="Dismiss notification", exact=True)
    if dismiss.count():
        dismiss.click()
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name
    page.screenshot(path=str(OUTPUT / f"{device}-{name}.png"), full_page=True)


def journey(browser, address, fixture, device, width, height):
    context = browser.new_context(base_url=address, viewport={"width": width, "height": height},
                                  reduced_motion="reduce", accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(12000)
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    prefix = "/admin/sites/" + fixture["site"]
    detail = prefix + "/orders/" + fixture["link"]
    try:
        page.goto("/login?next=/admin/sites")
        page.get_by_label("Email address", exact=True).fill(EMAIL)
        page.get_by_label("Password", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.wait_for_url("**/admin/sites")
        page.goto(detail)
        capture(page, device, "order-before-fulfilment")
        fulfil = page.locator("details").filter(has=page.locator("summary", has_text="Fulfil items")).first
        fulfil.locator("summary").click()
        fulfil.get_by_label("Quantity: " + PRODUCT, exact=True).fill("3")
        fulfil.get_by_label("Carrier", exact=True).fill("UPS")
        fulfil.get_by_label("Tracking number", exact=True).fill("1ZLOCALP3")
        fulfil.get_by_label("Tracking URL", exact=True).fill("https://www.ups.com/track?tracknum=1ZLOCALP3")
        fulfil.get_by_role("button", name="Fulfil selected items", exact=True).click()
        expect(page.locator(".a-toast")).to_contain_text("fulfilled")
        assert stock(fixture) == (7, 0), stock(fixture)
        refund = page.locator("details").filter(has=page.locator("summary", has_text="Refund items")).first
        refund.locator("summary").click()
        refund.get_by_label("Quantity: " + PRODUCT, exact=True).fill("1")
        refund.get_by_label("Restock returned items", exact=True).check()
        refund.get_by_label("Reason for refund", exact=True).fill("One tote was returned unopened.")
        refund.get_by_role("button", name="Review refund", exact=True).click()
        expect(page.get_by_role("heading", name="Review refund", exact=True)).to_be_visible()
        capture(page, device, "refund-review")
        page.get_by_label("I confirm this exact refund", exact=True).check()
        page.get_by_role("button", name="Issue refund", exact=True).click()
        expect(page.locator(".a-toast")).to_contain_text("succeeded")
        assert stock(fixture) == (8, 0), stock(fixture)
        create_return = page.locator("details").filter(has=page.locator("summary", has_text="Create return")).first
        create_return.locator("summary").click()
        create_return.get_by_label("Quantity: " + PRODUCT, exact=True).fill("1")
        create_return.get_by_label("Reason for return", exact=True).fill("A second tote has a damaged seam.")
        create_return.get_by_role("button", name="Create return", exact=True).click()
        page.get_by_role("button", name="Approve return", exact=True).click()
        page.get_by_role("button", name="Mark received", exact=True).click()
        assert stock(fixture) == (9, 0), stock(fixture)
        page.get_by_role("link", name="Refund return", exact=True).click()
        page.get_by_label("I confirm this exact refund", exact=True).check()
        page.get_by_role("button", name="Issue refund", exact=True).click()
        expect(page.locator(".a-toast")).to_contain_text("succeeded")
        assert stock(fixture) == (9, 0), stock(fixture)
        page.get_by_label("Add an internal note", exact=True).fill("Customer confirmed the remaining tote is perfect.")
        page.get_by_role("button", name="Post comment", exact=True).click()
        page.get_by_label("Tags, separated by commas", exact=True).fill("VIP, Return reviewed")
        page.get_by_role("button", name="Save tags", exact=True).click()
        page.get_by_label("Order note", exact=True).fill("Contact Ada before the next delivery.")
        page.get_by_role("button", name="Save note", exact=True).click()
        capture(page, device, "order-after-return")
        page.goto(prefix + "/orders")
        page.get_by_label("Search orders", exact=True).fill(fixture["email"])
        page.get_by_role("button", name="Apply", exact=True).click()
        expect(page.locator("tbody tr")).to_have_count(1)
        page.get_by_text("Add filters", exact=True).click()
        page.get_by_label("Tag", exact=True).fill("VIP")
        page.get_by_label("Payment status", exact=True).select_option("partially_refunded")
        page.get_by_role("button", name="Apply", exact=True).click()
        expect(page.locator("tbody tr")).to_have_count(1)
        expect(page.locator("#order-bulk")).to_be_hidden()
        page.locator("[data-order-select-all]").check()
        expect(page.locator("[data-order-select]")).to_be_checked()
        expect(page.locator("#order-bulk")).to_be_visible()
        page.locator("[data-order-select-all]").uncheck()
        expect(page.locator("#order-bulk")).to_be_hidden()
        capture(page, device, "orders-filtered")
        with page.expect_download() as download:
            page.get_by_role("link", name="Export", exact=True).click()
        assert download.value.suggested_filename == "orders.csv"
        verify_draft(page, prefix, fixture, device)
        verify_customer(page, prefix, fixture, device)
        assert not errors, errors
    except BaseException:
        capture(page, device, "failure")
        raise
    finally:
        context.close()


def verify_draft(page, prefix, fixture, device):
    page.goto(prefix + "/drafts")
    page.get_by_role("button", name="Create draft order", exact=True).click()
    page.get_by_label("Find products", exact=True).fill("TOTE-P3")
    expect(page.locator("[data-draft-variant-results]").locator("option")).to_have_count(2)
    variant = page.locator("[data-draft-variant-results]")
    variant.select_option(fixture["variant"])
    page.locator('form[action$="/lines"]').first.get_by_label("Quantity", exact=True).fill("1")
    page.get_by_role("button", name="Add product", exact=True).click()
    page.locator("[data-draft-customers]").select_option(fixture["customer"])
    page.get_by_role("button", name="Save draft", exact=True).click()
    page.get_by_role("button", name="Send invoice email", exact=True).click()
    capture(page, device, "draft-invoice")
    page.get_by_label("Payment received outside FastShop", exact=True).check()
    page.get_by_role("button", name="Mark as paid and create order", exact=True).click()
    expect(page.get_by_role("link", name="View order", exact=True)).to_be_visible()
    assert stock(fixture) == (9, 1), stock(fixture)
    capture(page, device, "draft-completed")


def verify_customer(page, prefix, fixture, device):
    page.goto(prefix + "/customers")
    page.get_by_label("Search customers", exact=True).fill("Ada")
    page.get_by_role("button", name="Apply filters", exact=True).click()
    expect(page.locator("tbody tr")).to_have_count(1)
    page.goto(prefix + "/customers/" + fixture["customer"])
    page.get_by_label("Phone", exact=True).fill("+44 7700 900123")
    page.get_by_label("Email marketing consent", exact=True).check()
    page.get_by_label("SMS marketing consent", exact=True).check()
    page.get_by_role("button", name="Save customer", exact=True).click()
    page.get_by_text("Add address", exact=True).click()
    address = page.locator('form[action$="/addresses"]')
    address.get_by_label("Address name", exact=True).fill("Ada Carter")
    address.get_by_label("Address line 1", exact=True).fill("48 Queen Square")
    address.get_by_label("City", exact=True).fill("Bristol")
    address.get_by_label("Postcode", exact=True).fill("BS1 4QA")
    address.get_by_label("Country", exact=True).fill("GB")
    address.get_by_label("Default address", exact=True).check()
    address.get_by_role("button", name="Save address", exact=True).click()
    page.locator("summary").filter(has_text="Default").click()
    expect(page.get_by_text("48 Queen Square", exact=False).first).to_be_visible()
    capture(page, device, "customer-address-consent")
    page.goto(prefix + "/customers")
    page.get_by_label("Search customers", exact=True).fill("Ada")
    page.get_by_role("combobox", name="Email consent", exact=True).select_option("subscribed")
    page.get_by_role("textbox", name="Location", exact=True).fill("Bristol")
    page.get_by_role("button", name="Apply filters", exact=True).click()
    expect(page.locator("tbody tr")).to_have_count(1)
    select_all = page.locator("[data-select-all]")
    select_all.check()
    expect(page.locator('input[name="customer_ids"]')).to_be_checked()
    bulk = page.locator('form[action$="/customers/bulk"]')
    bulk.locator('input[name="tags"]').fill("browser-vip")
    bulk.get_by_role("button", name="Add tags", exact=True).click()
    page.get_by_label("Search customers", exact=True).fill("Ada")
    page.get_by_label("Tag", exact=True).fill("browser-vip")
    page.get_by_role("button", name="Apply filters", exact=True).click()
    expect(page.locator("tbody tr")).to_have_count(1)
    capture(page, device, "customers-filtered")
    with page.expect_download() as download:
        page.get_by_role("link", name="Export", exact=True).click()
    assert download.value.suggested_filename.endswith(".csv")


def main():
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    (ROOT / "output").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p3-browser-data-", dir=ROOT / "output") as data_dir:
        isolate(data_dir, port)
        from app import order_admin_routes
        from app.db import engine
        from app.main import app

        order_admin_routes.StripeGateway = LocalRefundGateway
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
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        fixture = seed_store(device)
                        journey(browser, f"http://127.0.0.1:{port}", fixture, device, width, height)
                finally:
                    browser.close()
            print(json.dumps({"status": "passed", "screenshots": str(OUTPUT), "browser_errors": []}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

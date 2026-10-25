"""P4 merchant journeys against disposable SQLite and a local fake tax gateway.

Run: uv run python scripts/verify_admin_v2_growth_browser.py
No provider requests, delivered mail or stored browser sessions are used.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, isolate
from verify_admin_v2_catalog_browser import login, product_image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/admin-v2/p4"


class LocalTaxGateway:
    """Deliberately implements quoting only, so this script cannot take payment."""

    def calculate_tax(self, lines, destination, origin, shipping_minor, **kwargs):
        from app.integrations.stripe_commerce import TaxResult
        return TaxResult("taxcalc_local_browser", "USD", 0,
            sum(line.amount_minor for line in lines) + shipping_minor,
            datetime.now(UTC) + timedelta(hours=1), [])


def seed_store(device):
    from sqlalchemy import select

    from app import commerce, content, site_menus
    from app.db import SessionLocal
    from app.models import (
        Category,
        Order,
        OrderLine,
        Product,
        ProductType,
        ProductVariant,
        ShopCustomer,
        SiteOrder,
        Stock,
        User,
        VariantChannelListing,
        Warehouse,
    )
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == EMAIL))
        site = content.create_site(db, owner.id, "Northfield Supply", "growth-" + device)
        site.status = "preview"
        config = commerce.settings_for(db, site, create=True)
        config.mode = "sandbox"
        config.shipping_minor = 1000
        config.free_shipping_threshold_minor = 100000
        config.tax_registration_reviewed = True
        config.origin_json = {"country": "EE", "line1": "1 Workshop Street", "city": "Tallinn", "postal_code": "10111"}
        category = Category(tenant_id=site.tenant_id, slug="essentials", name="Essentials")
        kind = ProductType(tenant_id=site.tenant_id, slug="physical", name="Physical")
        warehouse = Warehouse(tenant_id=site.tenant_id, code="MAIN", name="Main warehouse")
        db.add_all([category, kind, warehouse])
        db.flush()
        product = Product(tenant_id=site.tenant_id, category_id=category.id,
            product_type_id=kind.id, name="Everyday canvas tote", slug="canvas-tote",
            status="active", is_published=True)
        db.add(product)
        db.flush()
        variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id,
            sku="TOTE-NAT", name="Natural", is_active=True)
        db.add(variant)
        db.flush()
        db.add_all([VariantChannelListing(channel_id=site.channel_id, variant_id=variant.id,
            currency="USD", price_minor=2995), Stock(
            warehouse_id=warehouse.id, variant_id=variant.id, quantity=200)])
        config.product_tax_codes_json = {product.id: "txcd_00000000"}
        product_page = content.create_page(db, site, product.name, "/products/canvas-tote", "product",
            {"version": 1, "title": product.name, "blocks": [{"id": "product", "type": "product",
                "heading": product.name, "body": "A dependable tote for everyday essentials."}]})
        product_page.product_id = product.id
        product_page.published_json = product_page.draft_json
        page = content.create_page(db, site, "Our story", "/pages/our-story", "content",
            {"version": 1, "title": "Our story", "blocks": [{"id": "story", "type": "text",
                "heading": "Made for every day", "body": "Useful essentials, thoughtfully chosen."}]})
        page.published_json = page.draft_json
        site_menus.put_menu(db, site, "main", [
            {"id": "story", "kind": "page", "label": "Our story", "path": page.path},
            {"id": "shop", "kind": "page", "label": "Canvas tote", "path": product_page.path},
            {"id": "home", "kind": "page", "label": "Home", "path": "/"}], publish=True)
        customer = ShopCustomer(site_id=site.id, tenant_id=site.tenant_id,
            email="buyer-" + device + "@example.test", name="Alex", verified_at=datetime.now(UTC))
        db.add(customer)
        db.flush()
        order = Order(tenant_id=site.tenant_id, channel_id=site.channel_id,
            email=customer.email, number="LOCAL-P4-" + device.upper(), idempotency_key="p4-" + device,
            currency="USD", subtotal_minor=5990, shipping_minor=1000, total_minor=6990,
            payment_status="paid")
        db.add(order)
        db.flush()
        db.add_all([SiteOrder(site_id=site.id, tenant_id=site.tenant_id,
            order_id=order.id, customer_id=customer.id),
            OrderLine(order_id=order.id, variant_id=variant.id, sku=variant.sku,
                product_name=product.name, variant_name=variant.name, quantity=2,
                unit_price_minor=2995, total_minor=5990)])
        db.commit()
        return {"id": site.id, "slug": site.slug, "variant": variant.id, "page": page.id}


def capture(page, device, name):
    page.evaluate("document.fonts.ready")
    page.evaluate("window.scrollTo(0, 0)")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name
    page.screenshot(path=str(OUTPUT / f"{device}-{name}.png"), full_page=True)


def checkout(browser, base, fixture, code, device, expected_saving=None, shipping=None):
    """Each code gets a separate shopper bag, avoiding pending checkout mutations."""
    context = browser.new_context(base_url=base, viewport={"width": 390 if device == "mobile" else 1440,
        "height": 844 if device == "mobile" else 900}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(12000)
    try:
        root = "/sites/" + fixture["slug"]
        page.goto(root + "/products/canvas-tote")
        page.get_by_label("Quantity", exact=True).fill("2")
        with page.expect_response(lambda response: response.url.endswith("/cart/add")):
            page.get_by_role("button", name="Add to bag", exact=True).click()
        page.goto(root + "/cart")
        page.get_by_label("Discount code", exact=True).fill(code)
        page.get_by_role("button", name="Save discount code", exact=True).click()
        expect(page.get_by_label("Discount code", exact=True)).to_have_value(code)
        page.get_by_role("link", name="Continue to checkout", exact=True).click()
        for label, value in (("Recipient's full name", "Alex Buyer"),
            ("Email", "shopper-" + code.lower() + "-" + str(time.time_ns()) + "@example.test"),
            ("Street address", "123 Test Street"), ("City", "Los Angeles"), ("ZIP code", "90001")):
            page.get_by_label(label, exact=True).fill(value)
        page.get_by_role("combobox", name="State", exact=True).select_option("CA")
        page.get_by_role("button", name="Review shipping & tax", exact=True).click()
        expect(page.get_by_role("heading", name="Review your order", exact=True)).to_be_visible()
        if expected_saving:
            expect(page.get_by_text("Savings: " + expected_saving, exact=True)).to_be_visible()
        if shipping:
            expect(page.get_by_text("Shipping: " + shipping, exact=True)).to_be_visible()
        capture(page, device, "checkout-" + (code.lower() or "shipping"))
        return page.locator("body").inner_text()
    except BaseException:
        page.screenshot(path=str(OUTPUT / f"{device}-shopper-failure.png"), full_page=True)
        print("Shopper failure:", page.url, page.locator("body").inner_text()[:2500])
        raise
    finally:
        context.close()


def verify_discounts(page, browser, base, fixture, device):
    prefix = f"/admin/sites/{fixture['id']}"
    types = [("amount_off_products", "Amount off products", "PRODUCT10", "10", "$5.99"),
             ("amount_off_order", "Amount off order", "ORDER10", "10", "$5.99"),
             ("buy_x_get_y", "Buy X get Y", "BUYGET", "100", "$29.95"),
             ("free_shipping", "Free shipping", "FREESHIP", "100", None)]
    for kind, title, code, value, expected_saving in types:
        page.goto(prefix + "/discounts/new")
        page.get_by_role("link", name=title, exact=True).click()
        expect(page.get_by_role("combobox", name="Discount type", exact=True)).to_have_value(kind)
        page.get_by_label("Title", exact=True).fill(title + " browser offer")
        page.get_by_label("Codes (comma separated)", exact=True).fill(code)
        if kind != "free_shipping":
            page.get_by_label("Value", exact=True).fill(value)
        capture(page, device, "discount-" + kind)
        page.get_by_role("button", name="Save discount", exact=True).click()
        expect(page.get_by_label("Title", exact=True)).to_have_value(title + " browser offer")
        checkout(browser, base, fixture, code, device,
                 expected_saving=expected_saving, shipping="$0.00" if kind == "free_shipping" else None)
    page.goto(prefix + "/discounts")
    expect(page.get_by_role("link", name="Free shipping browser offer", exact=True)).to_be_visible()
    capture(page, device, "discounts")


def verify_content(page, prefix, fixture, device):
    content_root = prefix + "/content"
    page.goto(content_root + "/pages")
    page.get_by_role("link", name="Our story", exact=True).click()
    expect(page.locator("#editor-canvas")).to_be_visible()
    capture(page, device, "page-editor")
    page.goto(content_root + "/files")
    form = page.locator('form[enctype="multipart/form-data"]')
    form.locator('input[type="file"]').set_input_files({"name": "northfield-tote.png",
        "mimeType": "image/png", "buffer": product_image()})
    form.get_by_label("Title", exact=True).fill("Canvas tote")
    form.get_by_label("Alt text", exact=True).fill("Natural canvas tote")
    form.get_by_role("button", name="Upload image", exact=True).click()
    details = page.locator('form').filter(has=page.get_by_role("button", name="Save details", exact=True)).first
    details.get_by_label("Alt text", exact=True).fill("Natural canvas tote with long handles")
    details.get_by_role("button", name="Save details", exact=True).click()
    expect(page.get_by_label("Alt text", exact=True).last).to_have_value("Natural canvas tote with long handles")
    capture(page, device, "files")
    page.goto(content_root + "/blog-posts/new")
    page.get_by_label("Title", exact=True).fill("A useful everyday companion")
    page.get_by_label("Path", exact=True).fill("/blog/everyday-companion")
    page.get_by_role("textbox", name="Rich content", exact=True).fill("A closer look at our everyday canvas tote.")
    page.get_by_label("Excerpt", exact=True).fill("Useful details for the everyday essentials.")
    page.get_by_label("Author", exact=True).fill("Alex Northfield")
    page.get_by_label("Category", exact=True).fill("Stories")
    page.get_by_label("Tags", exact=True).fill("canvas, everyday")
    page.get_by_label("SEO description", exact=True).fill("Meet the Northfield everyday canvas tote.")
    page.get_by_role("combobox", name="Featured image", exact=True).select_option(index=1)
    page.get_by_role("button", name="Publish", exact=True).click()
    expect(page.get_by_role("row").filter(has_text="A useful everyday companion")).to_contain_text("Published")
    capture(page, device, "blog-posts")
    page.goto(content_root + "/menus")
    form = page.locator('[data-menu-form]').filter(has=page.locator('input[name="name"][value="main"]'))
    root = form.locator(':scope > .gc-tree')
    items = root.locator(':scope > li')
    first, second = items.nth(0), items.nth(1)
    first.drag_to(second, target_position={"x": 15, "y": 60})
    # The accessible alternative exercises the same tree serializer on mobile.
    items.nth(1).get_by_role("button", name="Nest", exact=True).first.click()
    expect(root.locator(':scope > li > ol > li')).to_have_count(1)
    form.get_by_role("button", name="Publish menu", exact=True).click()
    expect(page.get_by_role("status").first).to_contain_text("Menu saved")
    # Reloaded tree data must discard old children when a link is moved out.
    root.locator(':scope > li > ol > li').get_by_role("button", name="Unnest", exact=True).click()
    form.get_by_role("button", name="Save menu", exact=True).click()
    expect(root.locator(':scope > li > ol > li')).to_have_count(0)
    items.nth(1).get_by_role("button", name="Nest", exact=True).first.click()
    form.get_by_role("button", name="Publish menu", exact=True).click()
    expect(root.locator(':scope > li > ol > li')).to_have_count(1)
    capture(page, device, "menus")


def verify_settings(page, browser, base, prefix, fixture, device):
    page.goto(prefix + "/settings/users")
    email = "staff-" + device + "@example.test"
    page.get_by_label("Email address", exact=True).fill(email)
    page.get_by_role("combobox", name="Role", exact=True).select_option("editor")
    page.get_by_role("button", name="Send invitation", exact=True).click()
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.growth_settings import invite_token
    from app.growth_settings_models import StaffInvite
    with SessionLocal() as db:
        invite = db.scalar(select(StaffInvite).where(StaffInvite.site_id == fixture["id"],
                                                     StaffInvite.email == email))
        token = invite_token(invite)
    second = browser.new_context(base_url=base, viewport=page.viewport_size)
    try:
        staff_page = second.new_page()
        staff_page.set_default_timeout(12000)
        staff_page.goto("/staff/accept?token=" + token)
        staff_page.get_by_label("Your name", exact=True).fill("Alex Editor")
        staff_page.locator('input[name="password"]').fill("LocalStaffBrowser2026$LongPassword")
        staff_page.get_by_label("Confirm password", exact=True).fill("LocalStaffBrowser2026$LongPassword")
        staff_page.get_by_role("button", name="Accept invitation", exact=True).click()
        expect(staff_page.get_by_role("heading", name="Home", exact=True)).to_be_visible()
        capture(staff_page, device, "staff-invite-accepted")
    finally:
        second.close()
    page.goto(prefix + "/settings/users")
    expect(page.get_by_role("main")).to_contain_text(email)
    capture(page, device, "staff")
    page.goto(prefix + "/settings/shipping")
    page.get_by_label("Zone name", exact=True).fill("California")
    page.get_by_label("State or region codes (optional)", exact=True).fill("CA")
    page.get_by_role("button", name="Add zone", exact=True).click()
    page.get_by_role("combobox", name="Zone", exact=True).select_option(label="California")
    page.get_by_label("Rate name", exact=True).fill("California standard")
    page.get_by_label("Price (minor units)", exact=True).fill("650")
    page.get_by_role("button", name="Add rate", exact=True).click()
    expect(page.get_by_role("main")).to_contain_text("California standard")
    capture(page, device, "shipping")
    checkout(browser, base, fixture, "", device, shipping="$6.50")
    page.goto(prefix + "/settings/taxes")
    page.get_by_label("Region code (optional)", exact=True).fill("CA")
    page.get_by_label("Rate in basis points (100 = 1%)", exact=True).fill("725")
    page.get_by_role("main").get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("main")).to_contain_text("7.25%")
    capture(page, device, "taxes")
    text = checkout(browser, base, fixture, "PRODUCT10", device)
    assert "Sales tax (CA): $4.38" in text, text
    page.goto(prefix + "/settings/notifications")
    page.get_by_label("Subject", exact=True).fill("{{ store_name }} order {{ order_number }}")
    page.locator('textarea[name="body"]').fill("Hello {{ customer_name }}, thank you for your order.")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_role("main")).to_contain_text("Northfield Supply order PREVIEW-001")
    expect(page.get_by_role("main")).to_contain_text("Hello Alex, thank you for your order.")
    capture(page, device, "notification-preview")


def verify_device(browser, base, fixture, device, width, height, errors):
    context = browser.new_context(base_url=base, viewport={"width": width, "height": height},
        reduced_motion="reduce", accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(12000)
    page.on("pageerror", lambda exc: errors.append(f"{device}: {exc}"))
    try:
        login(page)
        prefix = f"/admin/sites/{fixture['id']}"
        verify_discounts(page, browser, base, fixture, device)
        page.goto(prefix + "/analytics")
        expect(page.get_by_role("heading", name="Analytics", exact=True)).to_be_visible()
        expect(page.get_by_role("main")).to_contain_text("Everyday canvas tote")
        with page.expect_download() as event:
            page.get_by_role("link", name="Export CSV", exact=True).click()
        assert "gross_sales_minor" in Path(event.value.path()).read_text()
        capture(page, device, "analytics")
        verify_content(page, prefix, fixture, device)
        verify_settings(page, browser, base, prefix, fixture, device)
    except BaseException:
        page.screenshot(path=str(OUTPUT / f"{device}-failure.png"), full_page=True)
        raise
    finally:
        context.close()


def main():
    import uvicorn
    sys.path.insert(0, str(ROOT))
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-growth-browser-") as data_dir:
        isolate(data_dir, port)
        from app import store_checkout_routes
        from app.db import engine
        from app.main import app
        store_checkout_routes.make_gateway = lambda *args, **kwargs: LocalTaxGateway()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local growth verification server did not start")
                thread.join(0.05)
            errors = []
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        verify_device(browser, f"http://127.0.0.1:{port}", seed_store(device),
                                      device, width, height, errors)
                finally:
                    browser.close()
            assert not errors, errors
            for failure in OUTPUT.glob("*-failure.png"):
                failure.unlink()
            print(json.dumps({"status": "passed", "screenshots": str(OUTPUT),
                "count": len(list(OUTPUT.glob("*.png"))), "browser_errors": errors}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

"""All ten functional gap journeys on fresh local storage, at two viewports.

Also called by verify_admin_v2_all_browser.py. No environment files, provider
requests, mail delivery, saved sessions or persistent fixture databases are used.
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import isolate
from verify_admin_v2_catalog_browser import login

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/admin-v2/gaps"


def seed_store(device):
    import verify_admin_v2_orders_browser as orders
    from sqlalchemy import select

    from app import catalog_admin, commerce, content
    from app.db import SessionLocal
    from app.models import Product, Site, SitePage, Warehouse

    fixture = orders.seed_store("gaps-" + device)
    with SessionLocal() as db:
        site = db.get(Site, fixture["site"])
        site.status = "preview"
        config = commerce.settings_for(db, site)
        config.shipping_minor = 500
        config.tax_registration_reviewed = True
        config.origin_json = {"country": "EE", "line1": "1 Workshop Street", "city": "Tallinn", "postal_code": "10111"}
        for warehouse in db.scalars(select(Warehouse).where(Warehouse.tenant_id == site.tenant_id)):
            warehouse.country_code = "EE"
        product = db.get(Product, fixture["product"])
        catalog_admin.update_product(db, site, product.id, {"is_published": True}, expected_version=product.version)
        companion = catalog_admin.create_product(db, site, {"name": "Gap companion", "status": "active"})
        config.product_tax_codes_json = {product.id: "txcd_00000000", companion.id: "txcd_00000000"}
        for page in content.site_pages(db, site):
            page.published_json = page.draft_json
        home = next(p for p in content.site_pages(db, site) if p.path == "/")
        product_page = db.scalar(select(SitePage).where(
            SitePage.product_id == product.id, SitePage.site_id == site.id))
        fixture.update(slug=site.slug, home=home.id, companion=companion.id,
                       product_page=product_page.id, handle=product.slug)
        db.commit()
    return fixture


def capture(page, device, name):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    dismiss = page.locator("[data-toast-dismiss]")
    if dismiss.count() and dismiss.first.is_visible():
        dismiss.first.click()
    page.evaluate("document.fonts.ready")
    page.evaluate("document.querySelectorAll('.a-table-wrap').forEach(table => table.scrollLeft = 0)")
    page.evaluate("window.scrollTo(0, 0)")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name
    page.screenshot(path=str(OUTPUT / f"{device}-{name}.png"), full_page=True)


def journey(browser, base, fixture, device, width, height):
    from sqlalchemy import select

    from app import custom_data
    from app.commerce_gap_models import GiftCard, GiftCardEntry, OrderEdit
    from app.db import SessionLocal
    from app.models import Order, Site, SitePage, Warehouse
    from app.theme_bulk_models import ThemeSnapshot

    context = browser.new_context(base_url=base, viewport={"width": width, "height": height},
                                  reduced_motion="reduce", accept_downloads=True)
    page = context.new_page()
    prefix = f"/admin/sites/{fixture['site']}"
    try:
        login(page)
        # 1. Define a typed field, save its value and connect it in the editor.
        page.goto(prefix + "/settings/custom-data")
        page.get_by_label("Name", exact=True).fill("Material note")
        page.locator('select[name="owner_type"]').select_option("product")
        page.get_by_label("Namespace", exact=True).fill("custom")
        page.get_by_label("Key", exact=True).fill("material")
        page.locator('select[name="value_type"]').select_option("single_line_text")
        page.locator('textarea[name="validation"]').fill('{"max": 120}')
        page.get_by_role("button", name="Save definition", exact=True).click()
        expect(page.get_by_role("main")).to_contain_text("custom.material")
        capture(page, device, "01-custom-data-definitions")
        page.goto(prefix + "/products/" + fixture["product"])
        page.get_by_label("Material note", exact=True).fill("Woven cotton for everyday journeys")
        page.get_by_role("button", name="Save custom data", exact=True).click()
        expect(page.get_by_label("Material note", exact=True)).to_have_value("Woven cotton for everyday journeys")
        capture(page, device, "01-custom-data-value")
        with SessionLocal() as db:
            site = db.get(Site, fixture["site"])
            assert custom_data.values(db, site, "product", fixture["product"])["custom.material"].startswith("Woven")
        page.goto(prefix + "/editor?page=" + fixture["product_page"])
        expect(page.locator("#save-status")).to_contain_text("Saved")
        if device == "mobile":
            page.locator('[data-mode="sections"]').click()
        page.locator(".tree-row").first.click()
        page.locator('[data-dynamic-for="field-heading"]').select_option("metafield:custom.material")
        page.locator("#save").click()
        expect(page.locator("#save-status")).to_contain_text("Saved")
        capture(page, device, "01-dynamic-source-editor")
        with SessionLocal() as db:
            assert "metafield:custom.material" in str(db.get(SitePage, fixture["product_page"]).draft_json)

        # 2. Real password protection and metadata in the shared head renderer.
        page.goto(prefix + "/preferences")
        page.get_by_label("Homepage title", exact=True).fill("Northfield essentials")
        page.get_by_label("Meta description", exact=True).fill("Useful goods for everyday journeys.")
        page.get_by_label("Require a storefront password", exact=False).check()
        page.get_by_label("New password", exact=True).fill("LocalStore2026")
        page.get_by_role("button", name="Save preferences", exact=True).click()
        capture(page, device, "02-preferences")
        guest = browser.new_context(base_url=base, viewport={"width": width, "height": height}, reduced_motion="reduce")
        public = guest.new_page()
        public.goto("/sites/" + fixture["slug"] + "/")
        expect(public.get_by_label("Store password", exact=True)).to_be_visible()
        capture(public, device, "02-password-page")
        public.get_by_label("Store password", exact=True).fill("LocalStore2026")
        public.get_by_role("button", name="Enter store", exact=True).click()
        public.wait_for_url("**/sites/" + fixture["slug"] + "/")
        assert public.title().startswith("Northfield essentials")
        assert public.locator('meta[name="description"]').get_attribute("content") == "Useful goods for everyday journeys."
        guest.close()
        page.get_by_label("Require a storefront password", exact=False).uncheck()
        page.get_by_role("button", name="Save preferences", exact=True).click()

        # 3. Create, edit, import/export and follow an actual storefront redirect.
        page.goto(prefix + "/redirects")
        page.get_by_label("Redirect from", exact=True).fill("/former-story")
        page.get_by_label("Redirect to", exact=True).fill("/pages/about-us")
        page.get_by_role("button", name="Save redirect", exact=True).click()
        row = page.get_by_role("row").filter(has_text="/former-story")
        row.get_by_role("link", name="Edit", exact=True).click()
        page.get_by_label("Redirect to", exact=True).fill("/pages/contact")
        page.get_by_role("button", name="Save redirect", exact=True).click()
        page.get_by_label("CSV file", exact=True).set_input_files({"name": "redirects.csv", "mimeType": "text/csv",
            "buffer": b"Redirect from,Redirect to\n/former-about,/pages/about-us\n"})
        page.get_by_role("button", name="Import redirects", exact=True).click()
        expect(page.get_by_role("main")).to_contain_text("Imported 1 redirects")
        page.goto(prefix + "/redirects")
        with page.expect_download() as download:
            page.get_by_role("link", name="Export CSV", exact=True).click()
        assert download.value.suggested_filename.endswith(".csv")
        capture(page, device, "03-redirects")
        redirected = page.goto("/sites/" + fixture["slug"] + "/former-story")
        assert redirected.request.redirected_from is not None
        assert page.url.endswith("/pages/contact")

        # 4. Quantity changes and custom items persist, with a manual settlement.
        order_url = prefix + "/orders/" + fixture["link"]
        page.goto(order_url)
        expect(page.get_by_role("link", name="Edit", exact=True)).to_have_attribute(
            "href", order_url + "/edit")
        page.get_by_role("link", name="Edit", exact=True).click()
        page.get_by_label("Quantity", exact=True).first.fill("4")
        page.get_by_label("Item name", exact=True).fill("Gift wrapping")
        page.get_by_label("Price (minor units)", exact=True).fill("250")
        page.get_by_label("Reason for edit", exact=True).fill("Customer requested another tote and gift wrapping.")
        page.get_by_role("button", name="Save order edit", exact=True).click()
        expect(page.get_by_role("main")).to_contain_text("Amount owing: $32.50")
        page.get_by_role("button", name="Mark paid manually", exact=True).click()
        expect(page.get_by_role("main")).to_contain_text("Settlement: paid")
        capture(page, device, "04-order-edit")
        with SessionLocal() as db:
            edit = db.scalar(select(OrderEdit).where(OrderEdit.site_id == fixture["site"]))
            assert edit.difference_minor == 3250 and edit.settlement == "paid"
        page.goto(order_url)
        expect(page.locator(".p3-timeline")).to_contain_text("Order edited from")
        expect(page.locator(".p3-timeline")).to_contain_text("Payment recorded manually")
        expect(page.locator(".p3-timeline")).not_to_contain_text("minor units")

        # 5. Launch from selected products, navigate cells, copy down and save.
        page.goto(prefix + "/products")
        for product_id in (fixture["product"], fixture["companion"]):
            page.locator(f'input[name="product_ids"][value="{product_id}"]').check()
        page.locator('select[name="action"]').select_option("bulk_edit")
        page.get_by_role("button", name="Apply", exact=True).click()
        expect(page.locator("#bulk-grid")).to_be_visible()
        price = page.locator('[data-cell="price_minor"]').first
        price.fill("3200")
        price.press("ArrowDown")
        expect(page.locator('[data-cell="price_minor"]').nth(1)).to_be_focused()
        price.focus()
        page.get_by_role("button", name="Copy focused cell down", exact=True).click()
        expect(page.locator('[data-cell="price_minor"]').nth(1)).to_have_value("3200")
        page.get_by_role("button", name="Save changes", exact=True).click()
        expect(page.locator("#bulk-status")).to_contain_text("2 saved; 0 need attention")
        capture(page, device, "05-bulk-editor")

        # 6. Personal views and column preferences on all three indexes.
        for resource, hidden in (("products", "Vendor"), ("orders", "Tags"), ("customers", "Amount spent")):
            page.goto(prefix + "/" + resource)
            controls = page.locator(".a-view-controls")
            save_view = controls.locator("details").filter(has=page.locator("summary", has_text="Save view")).first
            save_view.locator("summary").click()
            save_view.get_by_label("View name", exact=True).fill("Daily review")
            save_view.get_by_label(hidden, exact=True).uncheck()
            save_view.get_by_role("button", name="Save view", exact=True).click()
            expect(page.get_by_role("navigation", name="Saved views").get_by_role("link", name="Daily review")).to_be_visible()
            columns = page.locator(".a-view-controls details").filter(has=page.locator("summary", has_text="Columns")).first
            columns.locator("summary").click()
            columns.get_by_label(hidden, exact=True).uncheck()
            columns.get_by_role("button", name="Save columns", exact=True).click()
            expect(page.locator(".a-filter-toolbar .a-view-controls")).to_be_visible()
            # Fetch filtering must retain the saved column preference on desktop and mobile.
            search = page.locator('.a-filter-bar input[name="q"]')
            search.fill("no-matching-row-for-column-check")
            expect(page.locator(".a-filter-status")).to_have_text("Results updated")
            search.fill("")
            page.wait_for_function("!new URL(location.href).searchParams.get('q')")
            expect(page.locator(".a-filter-status")).to_have_text("Results updated")
            expect(page.locator(".a-filter-toolbar .a-view-controls")).to_be_visible()
            header = page.locator("main table thead th").filter(has_text=hidden).first
            expect(header).to_be_hidden()
            capture(page, device, "06-saved-view-" + resource)

        # 7. Add, rename and retire an uncommitted location.
        page.goto(prefix + "/settings/locations")
        page.get_by_label("Location name", exact=True).fill("Northfield studio")
        page.get_by_label("Address", exact=True).fill("12 Studio Street")
        page.get_by_label("City", exact=True).fill("Portland")
        page.get_by_label("Country code", exact=True).fill("US")
        page.get_by_role("button", name="Save location", exact=True).click()
        page.get_by_role("link", name="Northfield studio", exact=True).click()
        page.get_by_label("Location name", exact=True).fill("Northfield collection studio")
        page.get_by_label("Fulfils online orders", exact=True).uncheck()
        page.get_by_role("button", name="Save location", exact=True).click()
        capture(page, device, "07-locations")
        with SessionLocal() as db:
            location = db.scalar(select(Warehouse).where(Warehouse.name == "Northfield collection studio",
                Warehouse.tenant_id == db.get(Site, fixture["site"]).tenant_id))
            assert location.is_active and not location.fulfils_online_orders

        # 8. Filter the append-only log after the preceding real changes.
        page.goto(prefix + "/activity")
        page.get_by_label("Object type", exact=True).fill("location")
        page.get_by_role("button", name="Filter activity", exact=True).click()
        expect(page.get_by_role("main")).to_contain_text("location")
        capture(page, device, "08-activity")

        # 9. Copy the current theme, edit its isolated design, publish and roll back.
        page.goto(prefix + "/themes")
        page.get_by_label("Copy name", exact=True).fill("Seasonal draft")
        page.get_by_role("button", name="Duplicate current theme", exact=True).click()
        draft = page.locator(".a-card").filter(has=page.get_by_role("heading", name="Seasonal draft", exact=True))
        draft.get_by_role("link", name="Edit theme", exact=True).click()
        expect(page.locator("#save-status")).to_contain_text("Saved")
        page.locator('[data-mode="theme"]').click()
        page.get_by_label("Accent", exact=True).fill("#1765b3")
        page.locator("#save").click()
        expect(page.locator("#save-status")).to_contain_text("Saved")
        capture(page, device, "09-theme-editor")
        with SessionLocal() as db:
            theme = db.scalar(select(ThemeSnapshot).where(ThemeSnapshot.site_id == fixture["site"], ThemeSnapshot.kind == "draft"))
            assert theme.snapshot_json["settings"]["design"]["accent"] == "#1765b3"
            assert (db.get(Site, fixture["site"]).settings_json.get("design") or {}).get("accent") != "#1765b3"
        page.goto(prefix + "/themes")
        draft = page.locator(".a-card").filter(has=page.get_by_role("heading", name="Seasonal draft", exact=True))
        draft.get_by_role("button", name="Publish", exact=True).click()
        with SessionLocal() as db:
            assert db.get(Site, fixture["site"]).published_settings_json["design"]["accent"] == "#1765b3"
        page.get_by_role("button", name="Roll back", exact=True).first.click()
        capture(page, device, "09-theme-library")
        with SessionLocal() as db:
            assert (db.get(Site, fixture["site"]).published_settings_json.get("design") or {}).get("accent") != "#1765b3"

        # 10. Issue and adjust a sandbox card, then redeem it in a real checkout.
        code = "GAPS-" + device.upper() + "-2026"
        page.goto(prefix + "/gift-cards")
        page.get_by_label("Code", exact=True).fill(code)
        page.get_by_label("Initial value (minor units)", exact=True).fill("50000")
        page.get_by_role("button", name="Issue gift card", exact=True).click()
        page.get_by_label("Adjustment (minor units)", exact=True).fill("500")
        page.get_by_label("Reason", exact=True).fill("Customer care credit")
        page.get_by_role("button", name="Adjust balance", exact=True).click()
        expect(page.get_by_role("main")).to_contain_text("$505.00")
        capture(page, device, "10-gift-card-history")
        guest = browser.new_context(base_url=base, viewport={"width": width, "height": height}, reduced_motion="reduce")
        public = guest.new_page()
        public.goto("/sites/" + fixture["slug"] + "/products/" + fixture["handle"])
        decline = public.get_by_role("button", name="Decline", exact=True)
        if decline.is_visible():
            decline.click()
        public.get_by_label("Quantity", exact=True).fill("1")
        with public.expect_response(lambda response: response.url.endswith("/cart/add")):
            public.get_by_role("button", name="Add to bag", exact=True).click()
        bag = public.frame_locator('iframe[title="Your bag, quantities and discount code"]')
        bag.get_by_role("link", name="Continue to checkout", exact=True).click()
        for label, value in (("Recipient's full name", "Alex Buyer"),
            ("Email", f"gift-{device}@example.test"), ("Street address", "123 Test Street"),
            ("City", "Los Angeles"), ("ZIP code", "90001")):
            public.get_by_label(label, exact=True).fill(value)
        public.get_by_role("combobox", name="State", exact=True).select_option("CA")
        public.get_by_label("Gift card code (optional, sandbox)", exact=True).fill(code)
        public.get_by_role("button", name="Review shipping & tax", exact=True).click()
        expect(public.get_by_role("button", name="Pay with gift card", exact=True)).to_be_visible()
        capture(public, device, "10-gift-card-checkout")
        public.get_by_role("button", name="Pay with gift card", exact=True).click()
        with SessionLocal() as db:
            gift = db.scalar(select(GiftCard).where(GiftCard.site_id == fixture["site"], GiftCard.code == code))
            redemption = db.scalar(select(GiftCardEntry).where(GiftCardEntry.gift_card_id == gift.id,
                GiftCardEntry.kind == "redemption"))
            assert redemption and gift.balance_minor == 50500 + redemption.amount_minor
            assert db.get(Order, redemption.order_id).payment_status == "paid"
        capture(public, device, "10-gift-card-paid")
        guest.close()
        page.reload()
        page.get_by_role("button", name="Disable gift card", exact=True).click()
        expect(page.get_by_role("main")).to_contain_text("Disabled")
        browser.assert_clean()
        return {"device": device, "features": 10}
    except BaseException:
        page.screenshot(path=str(OUTPUT / f"{device}-failure.png"), full_page=True)
        print("Gap failure:", page.url, page.locator("body").inner_text()[:2500], flush=True)
        raise
    finally:
        context.close()


def main():
    import uvicorn
    import verify_admin_v2_all_browser as acceptance
    import verify_admin_v2_growth_browser as growth

    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-gaps-browser-", dir=ROOT / "output") as storage:
        isolate(storage, port)
        from app import store_checkout_routes
        from app.db import engine
        from app.main import app
        store_checkout_routes.StripeGateway = lambda *args, **kwargs: growth.LocalTaxGateway()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        server_errors = []

        def serve():
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(lambda _, event: server_errors.append(str(event)))
            try:
                loop.run_until_complete(server.serve())
            finally:
                loop.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Gap acceptance server did not start")
                thread.join(0.05)
            with sync_playwright() as pw:
                try:
                    native = pw.chromium.launch(headless=True)
                except Error:
                    native = pw.chromium.launch(channel="chrome", headless=True)
                browser = acceptance.ObservedBrowser(native)
                results = []
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        fixture = seed_store(device)
                        results.append(journey(browser, f"http://127.0.0.1:{port}", fixture, device, width, height))
                        print(device + ": all ten gap journeys passed", flush=True)
                    browser.assert_clean()
                finally:
                    native.close()
            assert not server_errors, server_errors
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()
        assert not thread.is_alive()
        summary = {"status": "passed", "journeys": results, "browser_errors": browser.errors,
            "server_errors": server_errors, "screenshots": str(OUTPUT),
            "count": len([p for p in OUTPUT.glob("*.png") if "failure" not in p.stem
                          and not p.stem.startswith("review-")]),
            "isolation": f"Disposable {engine.dialect.name}; local tax double; sandbox gift tender; no provider calls"}
        (OUTPUT / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary))


if __name__ == "__main__":
    main()

"""Capture the one-page checkout in a fresh local SQLite store fixture."""

import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from app import commerce, content
from app.config import settings


def main():
    if settings.db_url or not Path(settings.data_dir).name.startswith("fastshop-"):
        raise SystemExit("Use DB_URL empty and a new fastshop-* data directory.")
    if settings.public_url != "http://127.0.0.1:5041":
        raise SystemExit("Set FASTSHOP_PUBLIC_URL=http://127.0.0.1:5041.")

    from app import store_checkout_routes
    from app.db import SessionLocal
    from app.main import app
    from app.models import (
        Category,
        Product,
        ProductType,
        ProductVariant,
        ShippingRate,
        ShippingZone,
        Stock,
        User,
        VariantChannelListing,
        Warehouse,
    )
    from tests.test_store_checkout_routes import HostedGateway

    with SessionLocal() as db:
        owner = User(email="checkout-browser@example.test", name="Checkout fixture")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Checkout fixture", "checkout-fixture")
        site.status = "preview"
        config = commerce.settings_for(db, site, create=True)
        config.mode = "sandbox"
        config.shipping_minor = 800
        config.tax_registration_reviewed = True
        config.origin_json = {"country": "EE", "line1": "Fixture warehouse", "city": "Tallinn", "postal_code": "10111"}
        category = Category(tenant_id=site.tenant_id, slug="tablets", name="Tablets")
        product_type = ProductType(tenant_id=site.tenant_id, slug="physical", name="Physical")
        db.add_all([category, product_type])
        db.flush()
        product = Product(tenant_id=site.tenant_id, category_id=category.id,
            product_type_id=product_type.id, name="Sample Tablets", slug="sample-tablets",
            is_published=True, image_url="")
        db.add(product)
        db.flush()
        variant = ProductVariant(tenant_id=site.tenant_id, product_id=product.id, sku="TABLET-01", name="Lemon-Lime")
        db.add(variant)
        db.flush()
        db.add(VariantChannelListing(channel_id=site.channel_id, variant_id=variant.id,
            currency="USD", price_minor=2696))
        config.product_tax_codes_json = {product.id: "txcd_00000000"}
        zone = ShippingZone(tenant_id=site.tenant_id, site_id=site.id, name="United States",
            countries_json=["US"], states_json=["CA"], is_active=True)
        db.add(zone)
        db.flush()
        db.add(ShippingRate(tenant_id=site.tenant_id, site_id=site.id, zone_id=zone.id,
            name="Standard", amount_minor=800))
        warehouse = Warehouse(tenant_id=site.tenant_id, code="BROWSER", name="Browser fixture", country_code="EE")
        db.add(warehouse)
        db.flush()
        db.add(Stock(warehouse_id=warehouse.id, variant_id=variant.id, quantity=10))
        db.commit()
        site_slug, variant_id = site.slug, variant.id

    gateway = HostedGateway()
    store_checkout_routes.StripeGateway = lambda site: gateway
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=5041,
        log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Local checkout capture server did not start.")

    output = Path("output/playwright/checkout-v2")
    output.mkdir(parents=True, exist_ok=True)
    root = settings.public_url + "/sites/" + site_slug
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(root + "/cart")
            token = page.locator('input[name="csrf_token"]').first.get_attribute("value")
            response = page.evaluate("async ({url, data}) => { const response = await fetch(url, {method: 'POST', "
                "headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams(data)}); "
                "return {status: response.status, body: await response.text()}; }", {
                    "url": root + "/cart/add",
                    "data": {"csrf_token": token, "variant_id": variant_id, "quantity": "1"},
                })
            if response["status"] not in (200, 303):
                raise RuntimeError(f"Could not add the fixture item to the bag ({response['status']}: {response['body'][:250]}).")
            page.goto(root + "/checkout")
            for name, value in {
                "email": "buyer@example.test", "first_name": "Taylor", "last_name": "Morgan",
                "line1": "123 Fixture Street", "city": "Los Angeles", "postal_code": "90001",
            }.items():
                page.locator(f'input[name="{name}"]').fill(value)
            page.locator('select[name="state"]').select_option("CA")
            page.locator("#checkout-shipping-rates .checkout-rate").first.wait_for(timeout=15000)
            assert "Sample Tablets" in page.locator("body").inner_text()
            page.evaluate("document.activeElement.blur()")
            for name, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                if page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
                    raise RuntimeError(f"Checkout overflows horizontally at {width}px.")
                page.screenshot(path=str(output / f"{name}.png"), full_page=True)
            browser.close()
        print({"status": "passed", "screenshots": [
            str(output / "desktop.png"), str(output / "mobile.png")], "stripe": "not called"})
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()

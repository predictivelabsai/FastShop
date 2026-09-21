"""Local-only UI acceptance with a clearly simulated Stripe boundary, not sandbox payment proof."""

import argparse
import threading
import time
from functools import partial
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from app.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription", action="store_true")
    args = parser.parse_args()
    if settings.db_url or not str(settings.data_dir).startswith("/tmp/fastshop-"):
        raise SystemExit("Requires DB_URL empty and an isolated /tmp/fastshop-* data directory.")
    if settings.public_url != "http://127.0.0.1:5041":
        raise SystemExit("Requires FASTSHOP_PUBLIC_URL=http://127.0.0.1:5041.")
    from app import checkout_payments, commerce, store_checkout_routes
    from app.db import SessionLocal
    from app.main import app
    from app.models import (
        Product,
        ProductVariant,
        Site,
        SitePage,
        Stock,
        SubscriptionContract,
        Warehouse,
    )
    from tests.test_store_checkout_routes import HostedGateway

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        config = commerce.settings_for(db, site, create=True)
        config.mode, config.shipping_minor, config.tax_registration_reviewed = "sandbox", 1000, True
        config.origin_json = {"country": "EE", "line1": "Local fixture warehouse", "city": "Tallinn", "postal_code": "10111"}
        variants = db.scalars(select(ProductVariant).where(ProductVariant.tenant_id == site.tenant_id)).all()
        config.product_tax_codes_json = {variant.product_id: "txcd_00000000" for variant in variants}
        warehouse = Warehouse(tenant_id=site.tenant_id, code="LOCAL-BROWSER", name="Local browser fixture", country_code="EE")
        db.add(warehouse)
        db.flush()
        for variant in variants:
            db.add(Stock(warehouse_id=warehouse.id, variant_id=variant.id, quantity=25))
        tablet = db.scalar(select(Product).where(Product.tenant_id == site.tenant_id, Product.slug == "hydrogen-tablets"))
        config.subscription_product_ids_json = [tablet.id]
        product_page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.product_id == tablet.id))
        product_path = product_page.path
        db.commit()
    gateway = HostedGateway()
    store_checkout_routes.StripeGateway = lambda site: gateway
    checkout_payments.handoff = partial(checkout_payments.handoff, gateway_factory=lambda site: gateway)
    checkout_payments.cancel = partial(checkout_payments.cancel, gateway_factory=lambda site: gateway)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=5041, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Local acceptance server did not start")
    output = Path("output/playwright/phase2-subscription-checkout" if args.subscription else "output/playwright/phase2-checkout")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    root = settings.public_url + "/sites/h24you"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.route("https://checkout.stripe.com/**", lambda route: route.fulfill(
                content_type="text/html", body="<h1>Local provider simulation — no payment made</h1>"))
            page.goto(root + product_path)
            page.locator('#h-cookie button[data-consent="none"]').click()
            if args.subscription:
                page.locator('select[name="subscription"]').select_option("on")
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(output / f"{device}-product.png"), full_page=True)
            page.get_by_role("button", name="Add to bag", exact=True).click()
            drawer = page.get_by_role("dialog", name="Your FastShop bag")
            drawer.wait_for(state="visible")
            bag = page.frame_locator('iframe[title="Your bag, quantities and discount code"]')
            bag.get_by_role("heading", name="Your bag", exact=True).wait_for()
            assert page.url == root + product_path
            bag.locator('input[name="quantity"]').fill("2")
            bag.get_by_role("button", name="Update quantity", exact=True).click()
            bag.locator('input[name="quantity"][value="2"]').wait_for()
            page.get_by_role("link", name="Open bag, 2 items").wait_for(state="attached")
            bag.locator('input[name="code"]').fill("FIXTURE-CODE")
            bag.get_by_role("button", name="Save discount code", exact=True).click()
            bag.locator('input[name="code"][value="FIXTURE-CODE"]').wait_for()
            # Exercise keyboard dismissal from inside the frame and restore focus.
            bag.locator('input[name="code"]').press("Escape")
            drawer.wait_for(state="hidden")
            expect(page.get_by_role("button", name="Add to bag", exact=True)).to_be_focused()
            page.get_by_role("link", name="Open bag, 2 items").click()
            drawer.wait_for(state="visible")
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                drawer.evaluate("async el => { await Promise.all(el.getAnimations().map(a => a.finished)); }")
                assert drawer.evaluate("el => { const r = el.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1; }")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                assert bag.locator('body').evaluate("el => el.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(output / f"{device}-cart-drawer.png"))
            bag.locator('input[name="code"]').scroll_into_view_if_needed()
            page.screenshot(path=str(output / "mobile-cart-drawer-discount.png"))
            # Clear the deliberately invalid fixture code and return to one item.
            bag.locator('input[name="code"]').fill("")
            bag.get_by_role("button", name="Save discount code", exact=True).click()
            bag.locator('input[name="code"][value=""]').wait_for()
            bag.locator('input[name="quantity"]').fill("1")
            bag.get_by_role("button", name="Update quantity", exact=True).click()
            bag.locator('input[name="quantity"][value="1"]').wait_for()
            page.goto(root + "/cart")
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.screenshot(path=str(output / f"{device}-bag.png"), full_page=True)
            page.get_by_role("link", name="Continue to checkout").click()
            for name, value in {"full_name": "Browser Fixture", "email": "browser-checkout@example.test", "line1": "123 Fixture Street",
                "city": "Los Angeles", "postal_code": "90001"}.items():
                page.locator(f'input[name="{name}"]').fill(value)
            page.locator('select[name="state"]').select_option("CA")
            if args.subscription:
                consent = page.locator('input[name="subscription_consent"]')
                assert not consent.is_checked()
                consent.check()
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(output / f"{device}-delivery.png"), full_page=True)
            page.get_by_role("button", name="Review shipping & tax").click()
            page.wait_for_load_state("networkidle")
            assert page.get_by_role("heading", name="Review your order", exact=True).is_visible()
            review_url = page.url
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(output / f"{device}-review.png"), full_page=True)
            page.get_by_role("button", name="Continue to Stripe test checkout").click()
            page.wait_for_url("https://checkout.stripe.com/**")
            page.goto(review_url)
            assert page.get_by_role("heading", name="Payment pending", exact=True).is_visible()
            # Explicit fixture state change; this is not a Stripe API payment.
            response = next(iter(gateway.responses.values()))
            response.update(status="complete", payment_status="paid", payment_intent="pi_browserfixture")
            page.get_by_role("button", name="Check payment status").click()
            page.wait_for_load_state("networkidle")
            assert page.get_by_role("heading", name="Payment confirmed", exact=True).is_visible()
            if args.subscription:
                with SessionLocal() as db:
                    contract = db.scalar(select(SubscriptionContract).where(SubscriptionContract.site_id == site.id))
                    assert contract and contract.state == "active" and contract.consent_json["accepted"] is True
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(output / f"{device}-confirmed-fixture.png"), full_page=True)
            page.get_by_role("button", name="Start a new bag").click()
            page.wait_for_load_state("networkidle")
            assert page.get_by_text("Your bag is empty.", exact=True).is_visible()
            # Progressive enhancement must not make shopping depend on JavaScript.
            fallback = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
            plain = fallback.new_page()
            plain.goto(root + product_path)
            plain.get_by_role("button", name="Add to bag", exact=True).click()
            plain.wait_for_url("**/cart")
            assert plain.get_by_role("heading", name="Your bag", exact=True).is_visible()
            plain.get_by_role("link", name="Continue to checkout").click()
            plain.wait_for_url("**/checkout")
            fallback.close()
            browser.close()
        assert not errors, errors
        print({"status": "passed", "subscription": args.subscription, "provider": "local simulation, not Stripe", "desktop_mobile": True, "browser_errors": errors})
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()

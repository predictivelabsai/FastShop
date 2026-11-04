"""Capture the subscription portal and saved reference at desktop and mobile sizes."""

import os
import secrets
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.pop("FASTSHOP_PUBLIC_URL", None)
    os.environ.update({
        "DB_URL": "", "FASTSHOP_ENV": "development", "FASTSHOP_SESSION_SECRET": secrets.token_hex(32),
        "FASTSHOP_AUTO_CREATE_SCHEMA": "1", "FASTSHOP_DATA_DIR": tempfile.mkdtemp(prefix="fastshop-portal-"),
        "FASTSHOP_ENABLE_SCHEDULER": "0", "FASTSHOP_PUBLIC_URL": "http://127.0.0.1:5049",
        "XAI_API_KEY": "", "POSTMARK_API_TOKEN": "",
        "POSTMARK_SERVER_TOKEN": "", "STRIPE_SECRET_KEY": "", "STRIPE_WEBHOOK_SECRET": "",
        "STRIPE_PUBLISHABLE_KEY": "",
    })
    import uvicorn
    from sqlalchemy import select

    from app import (
        checkout_services,
        commerce,
        customer_routes,
        customer_services,
    )
    from app.db import SessionLocal
    from app.main import app
    from app.models import (
        CommerceQuote,
        CustomerChallenge,
        Product,
        ProductVariant,
        Site,
        Stock,
        SubscriptionContract,
        Warehouse,
    )
    from tests.test_checkout_services import Gateway
    from tests.test_us_commerce import DESTINATION

    customer_routes.dispatch_mail = lambda _message_id: False

    def dismiss_cookie_notice(page):
        decline = page.get_by_role("button", name="Decline", exact=True)
        if decline.count() and decline.is_visible():
            decline.click()

    email = "portal-browser-desktop-generic@example.test"
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        if not site:
            site = db.scalar(select(Site).where(Site.status.in_(["preview", "published"])))
        if not site:
            raise RuntimeError("No preview or published store is available for the browser capture.")
        products = list(db.scalars(select(Product).where(Product.tenant_id == site.tenant_id,
            Product.is_published.is_(True), Product.status == "active").order_by(Product.name)))
        product = products[0] if products else None
        variants = list(db.scalars(select(ProductVariant).where(
            ProductVariant.tenant_id == site.tenant_id, ProductVariant.product_id == product.id,
            ProductVariant.is_active.is_(True))))
        if not variants:
            raise RuntimeError("The preview store has no active product variant.")
        config = commerce.settings_for(db, site, create=True)
        config.customer_accounts_enabled, config.mode = True, "sandbox"
        config.shipping_minor, config.tax_registration_reviewed = 1000, True
        config.origin_json = {"country": "EE", "line1": "Fixture warehouse", "city": "Tallinn", "postal_code": "10111"}
        config.product_tax_codes_json = {product.id: "txcd_00000000"}
        config.subscription_product_ids_json = [item.id for item in products]
        warehouse = Warehouse(tenant_id=site.tenant_id, code="PORTAL-CAPTURE",
            name="Portal capture", country_code="EE")
        db.add(warehouse)
        db.flush()
        for variant in variants:
            db.add(Stock(warehouse_id=warehouse.id, variant_id=variant.id, quantity=20))
        customer = customer_services.customer_for(db, site, email, create=True)
        customer.name = "Portal Browser"
        gateway = Gateway()
        attempt = checkout_services.prepare(db, site, customer.id,
            [{"variant_id": variants[0].id, "quantity": 1, "subscription": True}], DESTINATION,
            gateway, request_key="portal-browser-subscription", subscription_consent=True)
        quote = db.get(CommerceQuote, attempt.quote_id)
        checkout_services.begin_provider(db, site, attempt.id)
        attempt.stripe_session_id, attempt.state = "cs_test_portalbrowser", "open"
        gateway.result = {"id": attempt.stripe_session_id, "livemode": False,
            "client_reference_id": attempt.id, "metadata": {"site_id": site.id},
            "currency": "usd", "amount_total": quote.total_minor, "status": "complete",
            "payment_status": "paid", "mode": "payment", "payment_intent": "pi_portalbrowser",
            "customer": "cus_portalbrowser"}
        gateway.payment_intent_status = lambda _intent: {"id": "pi_portalbrowser", "livemode": False,
            "status": "succeeded", "customer": "cus_portalbrowser", "payment_method": "pm_portalbrowser",
            "currency": "usd", "amount_received": quote.total_minor, "setup_future_usage": "off_session",
            "created": int(datetime.now(UTC).timestamp())}
        checkout_services.reconcile(db, site, attempt.id, gateway)
        db.commit()
        contract = db.scalar(select(SubscriptionContract).where(
            SubscriptionContract.initial_attempt_id == attempt.id,
            SubscriptionContract.site_id == site.id, SubscriptionContract.tenant_id == site.tenant_id))
        site_id, slug, contract_id = site.id, site.slug, contract.id

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=5049,
        log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Local subscription portal server did not start.")

    output = Path("output/playwright/subscription-portal")
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                context = browser.new_context(viewport={"width": width, "height": height},
                    reduced_motion="reduce")
                page = context.new_page()
                for pack in ("generic", "liquid"):
                    email = f"portal-browser-{device}-{pack}@example.test"
                    with SessionLocal() as db:
                        site = db.scalar(select(Site).where(Site.id == site_id,
                            Site.tenant_id == db.scalar(select(Site.tenant_id).where(Site.id == site_id))))
                        published = dict(site.published_settings_json or {})
                        if pack == "liquid":
                            published["theme_pack"] = "liquid"
                        else:
                            published.pop("theme_pack", None)
                        site.published_settings_json = published
                        db.commit()
                    root = f"http://127.0.0.1:5049/sites/{slug}"
                    context.clear_cookies()
                    response = page.goto(root + "/account")
                    if not page.get_by_role("heading", name="Login", level=2).count():
                        raise RuntimeError(f"Login capture failed: {response.status if response else 'no response'} {page.url} {page.locator('body').inner_text()[:500]}")
                    dismiss_cookie_notice(page)
                    page.screenshot(path=str(output / f"{pack}-{device}-login-card.png"), full_page=True)
                    page.goto(root + "/account/login/verify?email=" + email)
                    page.get_by_label("Login code").wait_for()
                    dismiss_cookie_notice(page)
                    page.screenshot(path=str(output / f"{pack}-{device}-login-code.png"), full_page=True)
                    with SessionLocal() as db:
                        site = db.scalar(select(Site).where(Site.id == site_id))
                        customer = customer_services.customer_for(db, site, email, create=True)
                        contract = db.scalar(select(SubscriptionContract).where(
                            SubscriptionContract.id == contract_id,
                            SubscriptionContract.site_id == site.id,
                            SubscriptionContract.tenant_id == site.tenant_id))
                        contract.customer_id = customer.id
                        customer.name = "Portal Browser"
                        customer_services.request_email_challenge(db, site, email, "login", "portal-browser")
                        db.commit()
                        challenge = db.scalar(select(CustomerChallenge).where(
                            CustomerChallenge.site_id == site.id,
                            CustomerChallenge.tenant_id == site.tenant_id,
                            CustomerChallenge.customer_id == customer.id,
                            CustomerChallenge.purpose == "login",
                            CustomerChallenge.consumed_at.is_(None)).order_by(
                                CustomerChallenge.created_at.desc()))
                        from app.customer_services import confirmation_url
                        login_link = confirmation_url(site, challenge)
                    page.goto(login_link)
                    page.get_by_role("button", name="Confirm", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    dismiss_cookie_notice(page)
                    base = root + "/account/subscriptions"
                    for tab, label in (("next", "Next order"), ("subscriptions", "Subscriptions"),
                                       ("orders", "Order history"), ("account", "Account")):
                        page.goto(base + "?tab=" + tab)
                        page.get_by_role("tablist").get_by_role("tab", name=label, exact=True).wait_for()
                        assert page.get_by_role("tab", name=label, exact=True).get_attribute(
                            "aria-selected") == "true"
                        overflow = page.evaluate("""() => ({width: document.documentElement.scrollWidth,
                            inner: innerWidth, offenders: [...document.querySelectorAll('*')]
                            .map(node => ({tag: node.tagName, cls: node.className?.baseVal || node.className,
                                left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right,
                                width: node.getBoundingClientRect().width, text: node.textContent?.slice(0, 60)}))
                            .filter(node => node.right > innerWidth + 2).slice(0, 8)})""")
                        assert overflow["width"] <= overflow["inner"] + 1, f"{pack}-{device}-{tab}: {overflow}"
                        if tab == "next":
                            for label in ("Edit", "Swap", "Reschedule"):
                                page.get_by_text(label, exact=True).first.click()
                            assert page.locator("details[open]").count() >= 3
                            assert page.locator('form[action$="/quantity"] input[name="quantity"]').is_visible()
                            assert page.locator('form[action$="/swap"] select[name="variant_id"]').is_visible()
                            assert page.locator('form[action$="/swap"] select[name="variant_id"]').evaluate(
                                "node => node.getBoundingClientRect().width") >= 200
                            assert page.locator('form[action$="/reschedule"] input[type="date"]').is_visible()
                            overflow = page.evaluate("""() => ({width: document.documentElement.scrollWidth,
                                inner: innerWidth})""")
                            assert overflow["width"] <= overflow["inner"] + 1, \
                                f"{pack}-{device}-{tab} opened panels: {overflow}"
                            for label in ("Reschedule", "Swap", "Edit"):
                                page.get_by_text(label, exact=True).first.click()
                            page.evaluate("window.scrollTo(0, 0)")
                        page.screenshot(path=str(output / f"{pack}-{device}-{tab}.png"), full_page=True)
                context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    print(f"Portal captures saved to {output.resolve()}")


if __name__ == "__main__":
    main()

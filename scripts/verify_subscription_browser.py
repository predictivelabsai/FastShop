"""Local fixture subscription controls; no real Stripe calls or persisted browser sessions."""

import threading
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright
from sqlalchemy import select

from app.config import settings


def main():
    if settings.db_url or not str(settings.data_dir).startswith("/tmp/fastshop-") or settings.public_url != "http://127.0.0.1:5042":
        raise SystemExit("Requires isolated /tmp/fastshop-* data, empty DB_URL and http://127.0.0.1:5042 public URL.")
    from app import (
        checkout_payments,
        checkout_services,
        commerce,
        customer_routes,
        customer_services,
        store_checkout_routes,
        subscription_payments,
        subscription_recovery,
        subscription_renewals,
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
    from tests.test_subscription_payments import SetupGateway
    from tests.test_subscription_renewals import RenewalGateway
    from tests.test_us_commerce import DESTINATION

    email = "subscription-fixture@example.test"
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        product = db.scalar(select(Product).where(Product.tenant_id == site.tenant_id, Product.slug == "hydrogen-tablets"))
        variants = list(db.scalars(select(ProductVariant).where(ProductVariant.tenant_id == site.tenant_id, ProductVariant.product_id == product.id)))
        config = commerce.settings_for(db, site, create=True)
        config.mode, config.shipping_minor, config.tax_registration_reviewed = "sandbox", 1000, True
        config.origin_json = {"country": "EE", "line1": "Local fixture warehouse", "city": "Tallinn", "postal_code": "10111"}
        config.product_tax_codes_json = {product.id: "txcd_00000000"}
        config.subscription_product_ids_json = [product.id]
        warehouse = Warehouse(tenant_id=site.tenant_id, code="SUBS-FIXTURE", name="Local fixture", country_code="EE")
        db.add(warehouse)
        db.flush()
        for variant in variants:
            db.add(Stock(warehouse_id=warehouse.id, variant_id=variant.id, quantity=20))
        customer = customer_services.customer_for(db, site, email, create=True)
        customer.name = "Subscription Browser Fixture"
        db.flush()
        gateway = Gateway()
        attempt = checkout_services.prepare(db, site, customer.id,
            [{"variant_id": variants[0].id, "quantity": 1, "subscription": True}], DESTINATION, gateway,
            request_key="browser-subscription-fixture", subscription_consent=True)
        quote = db.get(CommerceQuote, attempt.quote_id)
        checkout_services.begin_provider(db, site, attempt.id)
        attempt.stripe_session_id, attempt.state = "cs_test_browserfixture", "open"
        gateway.result = {"id": attempt.stripe_session_id, "livemode": False, "client_reference_id": attempt.id,
            "metadata": {"site_id": site.id}, "currency": "usd", "amount_total": quote.total_minor,
            "status": "complete", "payment_status": "paid", "mode": "payment", "payment_intent": "pi_browserfixture", "customer": "cus_fixture"}
        gateway.payment_intent_status = lambda intent_id: {"id": "pi_browserfixture", "livemode": False,
            "status": "succeeded", "customer": "cus_fixture", "payment_method": "pm_fixture", "currency": "usd",
            "amount_received": quote.total_minor, "setup_future_usage": "off_session", "created": int(datetime.now(UTC).timestamp())}
        checkout_services.reconcile(db, site, attempt.id, gateway)
        db.commit()
        contract = db.scalar(select(SubscriptionContract).where(SubscriptionContract.initial_attempt_id == attempt.id))
        contract_id, site_id, new_variant_id = contract.id, site.id, variants[1].id
    customer_routes.dispatch_mail = lambda message_id: False
    setup_gateway = SetupGateway()
    subscription_payments.start = partial(subscription_payments.start, gateway_factory=lambda site: setup_gateway)
    subscription_payments.finish = partial(subscription_payments.finish, gateway_factory=lambda site: setup_gateway)
    renewal_gateway = RenewalGateway()
    renewal_gateway.outcome = "requires_action"
    subscription_recovery.start = partial(subscription_recovery.start, gateway_factory=lambda site: renewal_gateway)

    class HostedRecovery:
        response = None

        def create_checkout(self, attempt_id, payload):
            with SessionLocal() as db:
                quote = db.get(CommerceQuote, payload["session"]["metadata"]["quote_id"])
                self.response = {"id": "cs_test_browserrecovery", "livemode": False, "mode": "payment",
                    "client_reference_id": attempt_id, "metadata": payload["session"]["metadata"],
                    "currency": "usd", "amount_total": quote.total_minor, "status": "open", "payment_status": "unpaid",
                    "automatic_tax": {"status": "complete"}, "total_details": {"amount_tax": quote.tax_minor, "amount_shipping": quote.shipping_minor},
                    "url": "https://checkout.stripe.com/c/pay/cs_test_browserrecovery"}
            return self.response

        def checkout_status(self, session_id):
            assert session_id == self.response["id"]
            return self.response

    hosted_recovery = HostedRecovery()
    checkout_payments.handoff = partial(checkout_payments.handoff, gateway_factory=lambda site: hosted_recovery)
    store_checkout_routes.StripeGateway = lambda site: hosted_recovery
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=5042, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Local subscription server did not start")
    output = Path("output/playwright/phase2-subscriptions")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    root = settings.public_url + "/sites/h24you"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.route("https://checkout.stripe.com/**", lambda route: route.fulfill(content_type="text/html", body="<h1>Local card-update simulation</h1>"))
            page.goto(root + "/account")
            page.locator('input[name="email"]').fill(email)
            page.get_by_role("button", name="Email me a sign-in link").click()
            page.wait_for_load_state("networkidle")
            with SessionLocal() as db:
                site = db.get(Site, site_id)
                customer = customer_services.customer_for(db, site, email)
                challenge = db.scalar(select(CustomerChallenge).where(CustomerChallenge.customer_id == customer.id))
                link = customer_services.confirmation_url(site, challenge)
            page.goto(link)
            page.get_by_role("button", name="Confirm", exact=True).click()
            page.wait_for_load_state("networkidle")
            page.get_by_role("link", name="Manage your subscriptions").click()
            page.get_by_role("link", name="Tablet delivery").click()
            manage_url = page.url
            with SessionLocal() as db:
                site, contract = db.get(Site, site_id), db.get(SubscriptionContract, contract_id)
                contract.next_due_at = datetime.now(UTC) - timedelta(days=1)
                db.commit()
                cycle = subscription_renewals.prepare_cycle(db, site, contract.id, renewal_gateway)
                db.commit()
                cycle_id = cycle.id
            assert subscription_renewals.run_cycle(site_id, cycle_id, gateway_factory=lambda site: renewal_gateway) == "failed"
            page.reload()
            page.get_by_role("button", name="Review missed delivery", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert page.get_by_role("heading", name="One-time delivery recovery").is_visible()
            recovery_url = page.url
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(output / f"{device}-delivery-recovery.png"), full_page=True)
            page.get_by_role("button", name="Continue to Stripe test checkout", exact=True).click()
            page.wait_for_url("https://checkout.stripe.com/**")
            hosted_recovery.response.update(status="complete", payment_status="paid", payment_intent="pi_browserrecovery")
            page.goto(recovery_url)
            page.get_by_role("button", name="Check payment status", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert page.get_by_role("heading", name="Payment confirmed", exact=True).is_visible()
            page.goto(manage_url)
            assert page.get_by_text("Status: paused", exact=True).is_visible()
            page.get_by_role("button", name="Resume future deliveries", exact=True).click()
            page.wait_for_load_state("networkidle")
            for label in ("Skip next delivery", "Pause subscription", "Resume future deliveries"):
                page.get_by_role("button", name=label, exact=True).click()
                page.wait_for_load_state("networkidle")
            page.locator('input[name="months"]').fill("2")
            page.get_by_role("button", name="Change frequency", exact=True).click()
            page.wait_for_load_state("networkidle")
            page.locator('select[name="variant_id"]').select_option(new_variant_id)
            page.get_by_role("button", name="Change flavour", exact=True).click()
            page.wait_for_load_state("networkidle")
            page.locator('input[name="city"]').fill("New York")
            page.locator('input[name="postal_code"]').fill("10001")
            page.locator('select[name="state"]').select_option("NY")
            page.get_by_role("button", name="Update delivery address", exact=True).click()
            page.wait_for_load_state("networkidle")
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(output / f"{device}-subscription-controls.png"), full_page=True)
            page.get_by_role("button", name="Update payment method", exact=True).click()
            page.wait_for_url("https://checkout.stripe.com/**")
            setup_gateway.response.update(status="complete", setup_intent="seti_fixture")
            page.goto(manage_url)
            page.get_by_role("link", name="Verify card update", exact=True).click()
            page.get_by_role("button", name="Verify card update", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert page.get_by_text("Status: applied", exact=True).is_visible()
            page.goto(manage_url)
            page.locator('input[name="confirm_cancel"]').check()
            page.get_by_role("button", name="Cancel subscription", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert page.get_by_text("Status: cancelled", exact=True).is_visible()
            page.screenshot(path=str(output / "mobile-subscription-cancelled.png"), full_page=True)
            with SessionLocal() as db:
                contract = db.get(SubscriptionContract, contract_id)
                assert contract.state == "cancelled" and contract.payment_method_id == "pm_newcard"
                assert contract.interval_months == 2 and contract.destination_json["state"] == "NY"
                assert contract.lines_json[0]["variant_id"] == new_variant_id
            browser.close()
        assert not errors, errors
        print({"status": "passed", "provider": "local fixture, no real payment/card", "desktop_mobile": True, "browser_errors": errors})
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()

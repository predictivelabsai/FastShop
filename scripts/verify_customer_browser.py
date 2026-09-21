"""Isolated Chrome journeys; verification links stay in memory, never screenshots/logs."""

import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright
from sqlalchemy import select

from app import customer_services as customers
from app.config import settings
from app.db import SessionLocal
from app.models import CustomerChallenge, MarketingConsent, Order, Site, SiteOrder


def main():
    if settings.db_url or not str(settings.data_dir).startswith("/tmp/fastshop-"):
        raise SystemExit("Run with DB_URL empty and an isolated /tmp/fastshop-* data directory.")
    base = settings.public_url
    if not base.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise SystemExit("This mutating browser test is local-only.")
    output = Path("output/playwright/phase2-customer-flows")
    output.mkdir(parents=True, exist_ok=True)
    email = "browser-" + uuid4().hex[:8] + "@example.test"
    errors = []
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        site_id = site.id

    def verify_link(page, purpose):
        with SessionLocal() as db:
            site = db.get(Site, site_id)
            customer = customers.customer_for(db, site, email)
            challenge = db.scalar(select(CustomerChallenge).where(CustomerChallenge.site_id == site.id,
                CustomerChallenge.customer_id == customer.id, CustomerChallenge.purpose == purpose).order_by(CustomerChallenge.created_at.desc()))
            url = customers.confirmation_url(site, challenge)
        page.goto(url)
        page.get_by_role("button", name="Confirm", exact=True).click()
        page.wait_for_load_state("networkidle")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        merchant = browser.new_context(viewport={"width": 1440, "height": 1000})
        admin = merchant.new_page()
        admin.goto(base + "/login?next=/admin/sites")
        admin.locator('input[name="email"]').fill(os.environ["FASTSHOP_ADMIN_EMAIL"])
        admin.locator('input[name="password"]').fill(os.environ["FASTSHOP_ADMIN_PASSWORD"])
        admin.get_by_role("button", name="Sign in", exact=True).click()
        admin.wait_for_url("**/admin/sites")
        admin.goto(base + f"/admin/sites/{site_id}/commerce")
        admin.locator('select[name="mode"]').select_option("sandbox")
        admin.get_by_role("button", name="Save commerce settings").click()
        admin.wait_for_load_state("networkidle")

        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/sites/h24you/")
        page.locator('#h-cookie button[data-consent="none"]').click()
        page.get_by_role("button", name="Preview the offer").click()
        offer = page.locator("#h-offer-panel")
        assert not offer.locator('input[name="marketing_consent"]').is_checked()
        offer.locator('input[name="email"]').fill(email)
        offer.locator('input[name="marketing_consent"]').check()
        offer.get_by_role("button", name="Email my confirmation link").click()
        page.wait_for_load_state("networkidle")
        verify_link(page, "newsletter")
        assert page.get_by_role("heading", name="Email confirmed", exact=True).is_visible()

        page.goto(base + "/sites/h24you/account")
        assert page.get_by_role("button", name="Email me a sign-in link").is_visible()
        page.screenshot(path=str(output / "desktop-account-sign-in.png"), full_page=True)
        page.locator('input[name="email"]').fill(email)
        page.get_by_role("button", name="Email me a sign-in link").click()
        page.wait_for_load_state("networkidle")
        verify_link(page, "login")
        assert page.get_by_role("heading", name="Your orders").is_visible()

        # A clearly named local fixture exercises tracking independently of Stripe.
        # It is not a payment simulation on the production store.
        with SessionLocal() as db:
            site = db.get(Site, site_id)
            customer = customers.customer_for(db, site, email)
            order = Order(tenant_id=site.tenant_id, channel_id=site.channel_id, email=email,
                number="LOCAL-FIXTURE-" + uuid4().hex[:6], idempotency_key=uuid4().hex,
                currency="USD", subtotal_minor=2995, tax_minor=205, total_minor=3200, payment_status="paid")
            db.add(order)
            db.flush()
            link = SiteOrder(tenant_id=site.tenant_id, site_id=site.id, order_id=order.id, customer_id=customer.id)
            db.add(link)
            db.commit()
            order_id, order_number = link.id, order.number
        admin.goto(base + f"/admin/sites/{site_id}/customers")
        tracking = admin.locator(f'form[action="/admin/sites/{site_id}/shipments/{order_id}"]')
        tracking.locator('select[name="status"]').select_option("in_transit")
        tracking.locator('input[name="carrier"]').fill("Local test carrier")
        tracking.locator('input[name="tracking_number"]').fill("FIXTURE-TRACKING-123")
        tracking.locator('input[name="note"]').fill("Local browser fixture — not a real shipment")
        tracking.get_by_role("button", name="Add tracking update").click()
        admin.wait_for_load_state("networkidle")
        admin.screenshot(path=str(output / "merchant-customers-tracking.png"), full_page=True)

        for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            page.goto(base + "/sites/h24you/account")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(output / f"{device}-account.png"), full_page=True)
            page.get_by_role("link", name=order_number).click()
            assert page.get_by_text("Local test carrier FIXTURE-TRACKING-123", exact=True).is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(output / f"{device}-tracking.png"), full_page=True)
        page.goto(base + "/sites/h24you/account")
        page.get_by_role("button", name="Unsubscribe from marketing").click()
        page.wait_for_load_state("networkidle")
        assert page.get_by_text("Marketing: unsubscribed", exact=True).is_visible()
        with SessionLocal() as db:
            customer = customers.customer_for(db, db.get(Site, site_id), email)
            consent = db.scalar(select(MarketingConsent).where(MarketingConsent.site_id == site_id, MarketingConsent.customer_id == customer.id))
            assert consent.status == "unsubscribed"
        page.get_by_role("button", name="Sign out", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert page.get_by_role("button", name="Email me a sign-in link").is_visible()
        browser.close()
    assert not errors, errors
    print({"status": "passed", "flows": ["double opt-in", "single-use login", "owned order history", "merchant tracking", "unsubscribe", "logout"], "browser_errors": errors})


if __name__ == "__main__":
    main()

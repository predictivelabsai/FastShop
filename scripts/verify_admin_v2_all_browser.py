"""Sequential release acceptance on one disposable database and one local ASGI app.

Run: uv run python scripts/verify_admin_v2_all_browser.py
External payment and refund boundaries are local doubles, never live provider proof.
All merchant sessions enter through the real /login form. No session state is saved.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import sys
import tempfile
import threading
import time
from functools import partial
from pathlib import Path

import verify_admin_v2_browser as foundation
import verify_admin_v2_catalog_browser as catalogue
import verify_admin_v2_editor_browser as editor
import verify_admin_v2_gaps_browser as gaps
import verify_admin_v2_growth_browser as growth
import verify_admin_v2_orders_browser as orders
from playwright.sync_api import Error, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/admin-v2/final"


class LocalCheckoutGateway(growth.LocalTaxGateway):
    """A deterministic local hosted-payment double with explicit completion."""

    def __init__(self):
        self.responses = {}
        self.commands = {}

    def create_checkout(self, attempt_id, command):
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import CheckoutAttempt, CommerceQuote

        with SessionLocal() as db:
            attempt = db.scalar(select(CheckoutAttempt).where(CheckoutAttempt.id == attempt_id))
            quote = db.get(CommerceQuote, attempt.quote_id)
            local_tax = bool(quote.snapshot_json.get("local_tax"))
            session_id = "cs_test_" + attempt_id.replace("-", "")
            self.commands[session_id] = command
            self.responses[session_id] = {"id": session_id, "livemode": False, "mode": "payment",
                "client_reference_id": attempt_id, "metadata": command["session"]["metadata"],
                "currency": "usd", "customer": "cus_local", "amount_total": quote.total_minor,
                "total_details": {"amount_tax": 0 if local_tax else quote.tax_minor,
                                  "amount_shipping": quote.shipping_minor},
                "automatic_tax": {"enabled": False} if local_tax else {"status": "complete"},
                "status": "open", "payment_status": "unpaid",
                "url": "https://checkout.stripe.com/c/pay/" + session_id}
            return self.responses[session_id]

    def checkout_status(self, session_id):
        return self.responses[session_id]

    def payment_intent_status(self, intent_id):
        row = next(row for row in self.responses.values() if row.get("payment_intent") == intent_id)
        command = self.commands[row["id"]]["session"]
        return {"id": intent_id, "livemode": False, "status": "succeeded", "customer": "cus_local",
            "payment_method": "pm_local", "currency": "usd", "amount_received": row["amount_total"],
            "setup_future_usage": command.get("payment_intent_data", {}).get("setup_future_usage"),
            "created": int(time.time())}

    def expire_checkout(self, session_id):
        self.responses[session_id]["status"] = "expired"


GATEWAY = LocalCheckoutGateway()


def complete_checkout(page, device, name):
    review = page.url
    page.route("https://checkout.stripe.com/**", lambda route: route.fulfill(
        content_type="text/html", body="<h1>Local provider simulation. No payment made.</h1>"))

    def local_handoff(route):
        # Playwright routes only the first URL of an HTTP redirect chain. Keep
        # the real click and application POST, verify its provider redirect, then
        # make that a fresh navigation that the external provider double catches.
        response = route.fetch(max_redirects=0)
        destination = response.headers.get("location", "")
        assert response.status == 303 and destination.startswith("https://checkout.stripe.com/")
        route.fulfill(status=200, content_type="text/html", body="<script>location.replace(" +
            json.dumps(destination) + ")</script>")

    page.route("**/checkout/*/pay", local_handoff)
    page.get_by_role("button", name="Continue to Stripe test checkout", exact=True).click()
    page.wait_for_url("https://checkout.stripe.com/**")
    session_id = page.url.rsplit("/", 1)[-1]
    GATEWAY.responses[session_id].update(status="complete", payment_status="paid",
        payment_intent="pi_local_" + session_id)
    page.goto(review)
    page.get_by_role("button", name="Check payment status", exact=True).click()
    expect(page.get_by_role("heading", name="Payment confirmed", exact=True)).to_be_visible()
    capture(page, device, name + "-payment-confirmed")


def discount_checkout(browser, base, fixture, code, device, expected_saving=None, shipping=None,
                      complete_payment=True):
    """Extend the existing quote journey through confirmed local payment."""
    context = browser.new_context(base_url=base, viewport={"width": 390 if device == "mobile" else 1440,
        "height": 844 if device == "mobile" else 900}, reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto("/sites/" + fixture["slug"] + "/products/canvas-tote")
        page.get_by_label("Quantity", exact=True).fill("2")
        with page.expect_response(lambda response: response.url.endswith("/cart/add")):
            page.get_by_role("button", name="Add to bag", exact=True).click()
        wait_for_bag(page)
        page.goto("/sites/" + fixture["slug"] + "/cart")
        page.get_by_label("Discount code", exact=True).fill(code)
        page.get_by_role("button", name="Save discount code", exact=True).click()
        page.get_by_role("link", name="Continue to checkout", exact=True).click()
        fill_delivery(page, code or ("shipping" if complete_payment else "abandoned"))
        page.get_by_role("button", name="Review shipping & tax", exact=True).click()
        expect(page.get_by_role("heading", name="Review your order", exact=True)).to_be_visible()
        if expected_saving:
            expect(page.get_by_text("Savings: " + expected_saving, exact=True)).to_be_visible()
        if shipping:
            expect(page.get_by_text("Shipping: " + shipping, exact=True)).to_be_visible()
        name = "checkout-" + (code.lower() or ("shipping" if complete_payment else "abandoned"))
        capture(page, device, name)
        text = page.locator("body").inner_text()
        if complete_payment:
            complete_checkout(page, device, name)
        return text
    finally:
        context.close()


def fill_delivery(page, name):
    for label, value in (("Recipient's full name", "Alex Buyer"),
        ("Email", "shopper-" + name.lower() + "-" + str(time.time_ns()) + "@example.test"),
        ("Street address", "123 Test Street"), ("City", "Los Angeles"), ("ZIP code", "90001")):
        page.get_by_label(label, exact=True).fill(value)
    page.get_by_role("combobox", name="State", exact=True).select_option("CA")


def wait_for_bag(page):
    frame = page.locator('iframe[title="Your bag, quantities and discount code"]')
    # Liquid hides the duplicate bag heading inside its drawer. Wait for the
    # embedded document to finish rather than navigating away mid-request.
    expect(frame.content_frame.get_by_role("heading", name="Your bag", exact=True,
        include_hidden=True)).to_be_attached()
    page.wait_for_function("""() => document.querySelector(
        'iframe[title="Your bag, quantities and discount code"]')?.contentDocument?.readyState === 'complete'""")


class ObservedBrowser:
    """Observe every page, iframe request and extra staff/shopper context."""

    def __init__(self, browser):
        self.browser = browser
        self.errors = []
        self.downloads = set()
        self.contexts = []

    def new_context(self, **kwargs):
        context = self.browser.new_context(**kwargs)
        context.set_default_timeout(12000)
        self.contexts.append(context)
        context.on("page", self.observe)
        context.on("response", self.response)
        context.on("requestfailed", self.request_failed)
        return context

    def response(self, response):
        if "attachment" in response.headers.get("content-disposition", ""):
            self.downloads.add(response.url)
        if response.status >= 400:
            self.errors.append(f"HTTP {response.status}: {response.url}")

    def request_failed(self, request):
        # Chromium reports successful navigation downloads as ERR_ABORTED.
        if request.failure == "net::ERR_ABORTED" and request.url in self.downloads:
            return
        self.errors.append(f"Request failed: {request.method} {request.url}: {request.failure}")

    def observe(self, page):
        page.on("pageerror", lambda error: self.errors.append(f"Uncaught: {error}"))
        page.on("console", lambda message: self.errors.append(
            f"Console: {message.text}") if message.type == "error" else None)

    def assert_clean(self):
        assert not self.errors, "\n".join(self.errors)


def capture(page, device, name):
    page.evaluate("document.fonts.ready")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_function("window.scrollY === 0")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name
    page.screenshot(path=str(OUTPUT / f"{device}-{name}.png"), full_page=True)


def foundation_journey(page, site_id, device):
    catalogue.login(page)
    capture(page, device, "stores")
    prefix = f"/admin/sites/{site_id}"
    page.goto(prefix)
    expect(page.get_by_role("heading", name="Home", exact=True)).to_be_visible()
    capture(page, device, "home")
    page.keyboard.press("Control+k")
    search = page.get_by_role("combobox", name="Search store")
    search.fill(foundation.ORDER_NUMBER)
    result = page.get_by_role("listbox").get_by_role("option").first
    expect(result).to_contain_text(foundation.ORDER_NUMBER)
    page.keyboard.press("ArrowDown")
    capture(page, device, "global-search")
    page.keyboard.press("Enter")
    page.wait_for_url(f"**{prefix}/orders/*")
    expect(page.get_by_role("main")).to_contain_text(foundation.ORDER_NUMBER)
    if device == "mobile":
        toggle = page.get_by_role("button", name="Open navigation", exact=True)
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        page.keyboard.press("Escape")
        expect(toggle).to_have_attribute("aria-expanded", "false")


def settings_extra(page, prefix, device):
    page.goto(prefix + "/settings")
    capture(page, device, "settings-index")
    for section in ("general", "payments", "checkout", "domains", "policies", "billing"):
        response = page.goto(prefix + "/settings/" + section)
        assert response.status == 200, (section, response.status)
        expect(page.get_by_role("main")).to_be_visible()
        capture(page, device, "settings-" + section)
        if section == "general":
            brand = page.get_by_label("Brand name", exact=True)
            old = brand.input_value()
            brand.fill(old + " reviewed")
            bar = page.get_by_role("region", name="Unsaved changes")
            expect(bar).to_be_visible()
            bar.get_by_role("button", name="Discard", exact=True).click()
            expect(brand).to_have_value(old)
            brand.fill(old + " reviewed")
            page.get_by_role("button", name="Save draft settings", exact=True).click()
            page.goto(prefix + "/settings/general")
            expect(page.get_by_label("Brand name", exact=True)).to_have_value(old + " reviewed")
        elif section == "checkout":
            page.get_by_label("Enable customer accounts", exact=True).check()
            page.get_by_role("main").get_by_role("button", name="Save", exact=True).click()
            expect(page.get_by_label("Enable customer accounts", exact=True)).to_be_checked()
        elif section == "policies":
            page.get_by_role("textbox", name="Refund", exact=True).fill("Contact us within 30 days to arrange a return.")
            page.get_by_role("main").get_by_role("button", name="Save", exact=True).click()
            page.reload()
            expect(page.get_by_role("textbox", name="Refund", exact=True)).to_have_value(
                "Contact us within 30 days to arrange a return.")
        elif section == "payments":
            page.get_by_role("link", name="Review provider configuration", exact=True).click()
            expect(page.get_by_role("main")).to_contain_text("Sandbox enabled")
            capture(page, device, "settings-payment-readiness")
        elif section == "domains":
            page.get_by_role("link", name="Manage domain and go-live checks", exact=True).click()
            expect(page.get_by_role("main")).to_contain_text("Bind the custom domain")
            expect(page.get_by_role("button", name="Save custom domain", exact=True)).to_be_disabled()
            capture(page, device, "settings-domain-publication-guard")
        elif section == "billing":
            page.get_by_role("link", name="Manage plan and billing", exact=True).click()
            expect(page.get_by_role("heading", name="Billing", exact=True)).to_be_visible()
            expect(page.get_by_text("Billing setup is incomplete.", exact=False)).to_be_visible()
            capture(page, device, "settings-account-billing")
    page.goto(prefix + "/settings/notifications")
    page.get_by_role("button", name="Send test to my email", exact=True).click()
    expect(page.get_by_role("main")).to_contain_text("queued")
    capture(page, device, "notification-test-queued")


def remaining_orders(page, fixture, device):
    prefix = "/admin/sites/" + fixture["site"]
    page.goto(prefix + "/orders/" + fixture["link"])
    packing = page.get_by_role("link", name="Print packing slip", exact=True)
    if not packing.count():
        packing = page.get_by_role("link", name="Packing slip", exact=True)
    with page.expect_popup() as popup:
        packing.click()
    slip = popup.value
    expect(slip.get_by_role("main")).to_contain_text(orders.PRODUCT)
    capture(slip, device, "packing-slip")
    slip.close()
    page.goto(prefix + "/abandoned-checkouts")
    expect(page.get_by_role("heading", name="Abandoned checkouts", exact=True)).to_be_visible()
    capture(page, device, "abandoned-checkouts")
    page.goto(prefix + "/customers")
    with page.expect_download() as event:
        page.get_by_role("link", name="Export", exact=True).click()
    source = Path(event.value.path()).read_bytes()
    page.get_by_role("link", name="Import", exact=True).click()
    page.get_by_label("Choose CSV file", exact=True).set_input_files({
        "name": "customers.csv", "mimeType": "text/csv", "buffer": source})
    page.get_by_role("button", name="Preview import", exact=True).click()
    page.get_by_role("button", name="Import valid customers", exact=True).click()
    expect(page.get_by_role("main")).to_contain_text("Import result")
    capture(page, device, "customer-import-result")


def inspect_navigation(page, prefix, device):
    """Visit every real sidebar link and reject release placeholders."""
    page.goto(prefix)
    links = page.get_by_role("navigation", name="Main navigation", include_hidden=True)
    targets = links.locator("a[href]").evaluate_all("nodes => nodes.map(n => n.getAttribute('href'))")
    for target in dict.fromkeys(targets):
        assert "/coming-soon/" not in target, target
        response = page.goto(target)
        assert response.status == 200, (target, response.status)
    capture(page, device, "navigation-last-screen")


def role_checks(browser, base, page, fixture, device):
    from sqlalchemy import select

    from app import content
    from app.auth import hash_admin_password
    from app.db import SessionLocal
    from app.models import Membership, Site, SitePage, User

    prefix = f"/admin/sites/{fixture['id']}"
    page.goto(prefix + "/settings/users")
    row = page.get_by_role("row").filter(has_text="staff-" + device + "@example.test")
    row.locator('select[name="role"]').select_option("merchant")
    row.get_by_role("button", name="Change role", exact=True).click()
    expect(row.locator('select[name="role"]')).to_have_value("merchant")
    row.locator('select[name="role"]').select_option("editor")
    row.get_by_role("button", name="Change role", exact=True).click()
    expect(row.locator('select[name="role"]')).to_have_value("editor")
    own = page.get_by_role("row").filter(has_text=foundation.EMAIL)
    membership_id = own.locator('input[name="membership_id"]').first.input_value()
    csrf = page.locator('input[name="csrf_token"]').first.input_value()
    denied = page.request.post(prefix + "/settings/users/role", form={
        "csrf_token": csrf, "membership_id": membership_id, "role": "editor"})
    assert denied.status in (400, 403) and "at least one administrator" in denied.text()
    page.reload()
    expect(own.locator('select[name="role"]')).to_have_value("admin")
    capture(page, device, "staff-role-changed-last-admin-preserved")
    with SessionLocal() as db:
        target_page = db.scalar(select(SitePage.id).where(SitePage.site_id == fixture["id"]))
        outsider = User(email="outsider-" + device + "@example.test", name="Other workspace",
            password_hash=hash_admin_password("LocalOtherBrowser2026$LongPassword"))
        db.add(outsider)
        db.flush()
        content.create_site(db, outsider.id, "Other workspace", "other-" + device)
        db.commit()
    for email, password, outsider in (("staff-" + device + "@example.test",
            "LocalStaffBrowser2026$LongPassword", False),
            ("outsider-" + device + "@example.test", "LocalOtherBrowser2026$LongPassword", True)):
        context = browser.new_context(base_url=base, viewport=page.viewport_size)
        second = context.new_page()
        second.goto("/login?next=/admin/sites")
        second.get_by_label("Email address", exact=True).fill(email)
        second.get_by_label("Password", exact=True).fill(password)
        second.get_by_role("button", name="Sign in", exact=True).click()
        second.wait_for_url("**/admin/sites")
        families = ("", "/products", "/collections", "/inventory", "/orders", "/drafts",
            "/abandoned-checkouts", "/customers", "/discounts", "/subscriptions", "/analytics",
            "/content/pages", "/content/blog-posts", "/content/menus", "/content/files", "/editor",
            "/settings", "/settings/general", "/settings/users", "/settings/payments",
            "/settings/checkout", "/settings/shipping", "/settings/taxes", "/settings/notifications",
            "/settings/domains", "/settings/policies", "/settings/billing", "/build", "/pages",
            "/media", "/menus", "/snippets", "/reviews", "/inbox", "/commerce", "/revenue",
            "/golive", "/samples", "/deliveries", "/integrations", "/placeholders", "/editor/api",
            "/editor/api/document", "/editor/api/resources",
            "/editor/api/media", "/editor/api/revisions", "/editor/api/theme-settings") if outsider else ("/settings/users",)
        for family in families:
            response = second.request.get(prefix + family)
            assert response.status in (400, 403, 404), (email, family, response.status)
        if not outsider:
            second.goto(prefix + "/editor?page=" + target_page)
            token = second.evaluate("window.FASTSHOP_EDITOR.csrf_token")
            before = second.request.get(prefix + "/editor/api/document?page=" + target_page).json()
            denied = second.request.post(prefix + "/editor/api/publish?page=" + target_page,
                headers={"X-CSRF-Token": token, "Content-Type": "application/json"}, data="{}")
            assert denied.status == 403 and "access denied" in denied.text(), denied.text()
            after = second.request.get(prefix + "/editor/api/document?page=" + target_page).json()
            assert before == after, "Denied publish changed the editor document"
            for action, values in (("invite", {"email": "denied@example.test", "role": "admin"}),
                ("role", {"membership_id": membership_id, "role": "editor"})):
                denied = second.request.post(prefix + "/settings/users/" + action,
                    form={"csrf_token": token, **values})
                assert denied.status == 400 and "Only administrators" in denied.text(), denied.text()
            capture(second, device, "editor-role-cannot-publish")
        context.close()
    with SessionLocal() as db:
        owner_role = db.scalar(select(Membership.role).join(User).where(
            Membership.tenant_id == db.get(Site, fixture["id"]).tenant_id,
            User.email == foundation.EMAIL))
        assert owner_role == "admin"
    return {"foreign_tenant_url_families_denied": len(families),
        "editor_publish_denied_without_mutation": True,
        "editor_staff_invite_and_role_change_denied": True,
        "staff_role_changes_persisted": True, "last_administrator_guard": True}


def prepare_liquid():
    from sqlalchemy import select

    from app import commerce
    from app.db import SessionLocal
    from app.models import Product, ProductVariant, Site, Stock, Warehouse

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
        config = commerce.settings_for(db, site, create=True)
        config.mode, config.customer_accounts_enabled = "sandbox", True
        config.shipping_minor, config.tax_registration_reviewed = 500, True
        config.origin_json = {"country": "EE", "line1": "1 Workshop Street", "city": "Tallinn", "postal_code": "10111"}
        products = list(db.scalars(select(Product).where(Product.tenant_id == site.tenant_id)))
        config.product_tax_codes_json = {product.id: "txcd_00000000" for product in products}
        config.subscription_product_ids_json = [product.id for product in products if product.slug == "hydrogen-tablets"]
        warehouse = Warehouse(tenant_id=site.tenant_id, code="ALL-BROWSER", name="Local acceptance warehouse")
        db.add(warehouse)
        db.flush()
        for variant in db.scalars(select(ProductVariant).where(ProductVariant.tenant_id == site.tenant_id)):
            db.add(Stock(warehouse_id=warehouse.id, variant_id=variant.id, quantity=100))
        db.commit()
        return site.id


def liquid_shopper(browser, base, merchant, site_id, device):
    from sqlalchemy import select

    from app import customer_services
    from app.db import SessionLocal
    from app.models import CustomerChallenge, ShopCustomer, SubscriptionContract

    context = browser.new_context(base_url=base, viewport=merchant.viewport_size, reduced_motion="reduce")
    page = context.new_page()
    root = "/sites/h24you-liquid"
    email = ""
    try:
        page.goto(root + "/")
        capture(page, device, "liquid-home")
        for subscription in (False, True):
            path = "/products/hydrogen-tablets" if subscription else "/products/h2-4-you-one"
            page.goto(root + path)
            banner = page.locator('#h-cookie button[data-consent="none"]')
            if banner.is_visible():
                banner.click()
            if subscription:
                page.locator('input[name="subscription"][value="on"]').first.check()
            capture(page, device, "liquid-product-" + str(subscription).lower())
            with page.expect_response(lambda response: response.url.endswith("/cart/add")):
                page.get_by_role("button", name=re.compile(r"^Add to cart", re.I)).first.click()
            wait_for_bag(page)
            page.goto(root + "/cart")
            capture(page, device, "liquid-cart-" + str(subscription).lower())
            page.get_by_role("link", name="Continue to checkout", exact=True).click()
            fill_delivery(page, "liquid" + device + str(subscription))
            email = page.get_by_label("Email", exact=True).input_value()
            if subscription:
                page.locator('input[name="subscription_consent"]').check()
            capture(page, device, "liquid-delivery-" + str(subscription).lower())
            page.get_by_role("button", name="Review shipping & tax", exact=True).click()
            expect(page.get_by_role("heading", name="Review your order", exact=True)).to_be_visible()
            capture(page, device, "liquid-review-" + str(subscription).lower())
            complete_checkout(page, device, "liquid-" + str(subscription).lower())
            if not subscription:
                page.get_by_role("button", name="Start a new bag", exact=True).click()
        page.goto(root + "/account")
        page.get_by_role("main").get_by_label("Email address", exact=True).fill(email)
        page.get_by_role("button", name="Email me a sign-in link", exact=True).click()
        expect(page.get_by_role("heading", name="Check your email", exact=True)).to_be_visible()
        # Read only the disposable outbox challenge, in place of delivering mail.
        with SessionLocal() as db:
            customer = db.scalar(select(ShopCustomer).where(ShopCustomer.site_id == site_id,
                ShopCustomer.email == email))
            challenge = db.scalar(select(CustomerChallenge).where(CustomerChallenge.customer_id == customer.id)
                .order_by(CustomerChallenge.created_at.desc()))
            challenge_id, token = challenge.id, customer_services.challenge_token(challenge)
            contract_id = db.scalar(select(SubscriptionContract.id).where(
                SubscriptionContract.customer_id == customer.id))
        page.goto(root + "/account/verify/" + challenge_id + "#token=" + token)
        page.get_by_role("button", name="Confirm", exact=True).click()
        expect(page.get_by_role("main")).to_contain_text(email)
        capture(page, device, "liquid-account")
        page.locator('a[href*="/account/orders/"]').first.click()
        expect(page.get_by_role("main")).to_contain_text("paid")
        capture(page, device, "liquid-account-order")
        page.goto(root + "/account/subscriptions")
        expect(page.get_by_role("main")).to_contain_text("Status: active")
        capture(page, device, "liquid-account-subscriptions")
        page.locator('a[href*="/account/subscriptions/"]').first.click()
        expect(page.get_by_role("heading", name="Manage subscription", exact=True)).to_be_visible()
        capture(page, device, "liquid-account-subscription-detail")
        prefix = f"/admin/sites/{site_id}/subscriptions"
        merchant.goto(prefix)
        merchant.get_by_label("Search subscriptions", exact=True).fill(email)
        merchant.get_by_role("button", name="Filter", exact=True).click()
        capture(merchant, device, "subscriptions-index")
        merchant.get_by_role("link", name=email, exact=True).click()
        expect(merchant).to_have_url(base + prefix + "/" + contract_id)
        for action in ("Skip next delivery", "Pause subscription", "Resume subscription", "Cancel subscription"):
            with SessionLocal() as db:
                before = db.get(SubscriptionContract, contract_id)
                due, version = before.next_due_at, before.version
            merchant.get_by_role("button", name=action, exact=True).click()
            expect(merchant.get_by_role("main")).to_contain_text("Subscription updated")
            with SessionLocal() as db:
                updated = db.get(SubscriptionContract, contract_id)
                assert updated.version > version
                expected_state = {"Skip next delivery": "active", "Pause subscription": "paused",
                    "Resume subscription": "active", "Cancel subscription": "cancelled"}[action]
                assert updated.state == expected_state
                if action == "Skip next delivery":
                    assert updated.next_due_at > due
            capture(merchant, device, "subscriptions-" + action.split()[0].lower())
        page.goto(root + "/account/subscriptions")
        expect(page.get_by_role("main")).to_contain_text("Status: cancelled")
    except BaseException:
        page.screenshot(path=str(OUTPUT / f"{device}-liquid-shopper-failure.png"), full_page=True)
        print("Liquid failure:", page.url, page.locator("body").inner_text()[:3000], flush=True)
        raise
    finally:
        context.close()


def public_content_and_ranges(browser, base, page, fixture, device):
    from sqlalchemy import select

    from app import site_menus
    from app.db import SessionLocal
    from app.models import Site

    prefix = f"/admin/sites/{fixture['id']}"
    page.goto(prefix + "/analytics")
    for preset in ("1", "7", "90", "custom"):
        page.get_by_role("combobox", name="Date range", exact=True).select_option(preset)
        if preset == "custom":
            page.get_by_label("From", exact=True).fill("2026-01-01")
            page.get_by_label("To", exact=True).fill("2026-12-31")
        page.get_by_role("button", name="Apply", exact=True).click()
        expect(page.get_by_role("main")).to_contain_text("Compared with")
        capture(page, device, "analytics-" + preset)
    shopper = browser.new_context(base_url=base, viewport=page.viewport_size)
    public = shopper.new_page()
    try:
        public.goto("/sites/" + fixture["slug"] + "/blog/everyday-companion")
        consent = public.get_by_role("button", name="Decline", exact=True)
        if consent.is_visible():
            consent.click()
        expect(public.get_by_role("heading", name="A useful everyday companion", exact=True)).to_be_visible()
        expect(public.get_by_role("main")).to_contain_text("A closer look at our everyday canvas tote.")
        capture(public, device, "published-blog-storefront")
        public.goto("/sites/" + fixture["slug"] + "/products/canvas-tote")
        if device == "mobile":
            public.get_by_role("button", name="Menu", exact=True).click()
        expect(public.get_by_role("link", name="Our story", exact=True).first).to_be_visible()
        with SessionLocal() as db:
            expected = site_menus.rendered_navigation(db, db.scalar(select(Site).where(
                Site.id == fixture["id"])), "header")
        actual = public.locator("#site-navigation a").all_text_contents()
        assert actual == [item["label"] for item in expected], (actual, expected)
        assert actual != ["Our story", "Canvas tote", "Home"], "Menu reorder did not reach storefront"
        capture(public, device, "published-menu-storefront")
    finally:
        shopper.close()


def main():
    import uvicorn

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-all-browser-", dir=ROOT / "output") as data_dir:
        foundation.isolate(data_dir, port)
        from app import checkout_payments, order_admin_routes, store_checkout_routes
        from app.db import engine
        from app.main import app

        order_admin_routes.StripeGateway = orders.LocalRefundGateway
        store_checkout_routes.StripeGateway = lambda *args, **kwargs: GATEWAY
        checkout_payments.handoff = partial(checkout_payments.handoff,
            gateway_factory=lambda site: GATEWAY)
        checkout_payments.cancel = partial(checkout_payments.cancel,
            gateway_factory=lambda site: GATEWAY)
        growth.checkout = discount_checkout
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for module in (foundation, catalogue, orders, growth, editor):
            module.OUTPUT = OUTPUT
        home_site = foundation.seed_order()
        editor_fixture = editor.fixtures()
        liquid_site = prepare_liquid()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        server_errors = []

        def serve():
            # The Windows proactor raises transport callback errors on a normal
            # browser socket close. A selector serves HTTP without that race;
            # Playwright retains its own subprocess-capable main-thread loop.
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(lambda _, context: server_errors.append(
                str(context.get("exception") or context.get("message"))))
            try:
                loop.run_until_complete(server.serve())
            finally:
                loop.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        results = []
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Unified acceptance server did not start")
                thread.join(0.05)
            base = f"http://127.0.0.1:{port}"
            with sync_playwright() as pw:
                try:
                    native = pw.chromium.launch(headless=True)
                except Error:
                    native = pw.chromium.launch(channel="chrome", headless=True)
                browser = ObservedBrowser(native)
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        context = browser.new_context(base_url=base, viewport={"width": width, "height": height},
                            reduced_motion="reduce", accept_downloads=True)
                        page = context.new_page()
                        page.set_default_timeout(12000)
                        foundation_journey(page, home_site, device)
                        print(device + ": foundation passed", flush=True)
                        browser.assert_clean()
                        site_id = catalogue.seed_store("all-" + device)
                        prefix = f"/admin/sites/{site_id}"
                        for name in ("product", "csv", "collections", "inventory"):
                            getattr(catalogue, "verify_" + name)(page, prefix, device)
                            results.append(device + ": catalogue " + name)
                            browser.assert_clean()
                            print(device + ": catalogue " + name + " passed", flush=True)
                        fixture = orders.seed_store("all" + device)
                        orders.journey(browser, base, fixture, device, width, height)
                        remaining_orders(page, fixture, device)
                        results.append(device + ": orders, refunds, returns, drafts, customers")
                        browser.assert_clean()
                        print(device + ": orders passed", flush=True)
                        growth_fixture = growth.seed_store("all-" + device)
                        growth.verify_device(browser, base, growth_fixture, device, width, height, [])
                        prefix = f"/admin/sites/{growth_fixture['id']}"
                        discount_checkout(browser, base, growth_fixture, "", device, complete_payment=False)
                        page.goto(prefix + "/abandoned-checkouts")
                        expect(page.get_by_role("main")).to_contain_text("shopper-abandoned-")
                        capture(page, device, "abandoned-checkouts-recorded")
                        settings_extra(page, prefix, device)
                        roles = role_checks(browser, base, page, growth_fixture, device)
                        results.append({"device": device, "roles": roles})
                        public_content_and_ranges(browser, base, page, growth_fixture, device)
                        inspect_navigation(page, prefix, device)
                        liquid_shopper(browser, base, page, liquid_site, device)
                        results.append(device + ": Liquid home, one-time and subscription product/cart/delivery/"
                            "confirmed local payment; email challenge sign-in; account/order/subscription pages; "
                            "merchant subscription skip/pause/resume/cancel with persisted state")
                        results.append(device + ": analytics 1/7/30/90-day and custom ranges, CSV; "
                            "published blog content and dragged header menu order visible on storefront")
                        results.append(device + ": discounts, shipping, tax, content, staff, settings")
                        browser.assert_clean()
                        print(device + ": growth, roles and Liquid shopper passed", flush=True)
                        context.close()
                        gap_fixture = gaps.seed_store("all-" + device)
                        results.append(gaps.journey(browser, base, gap_fixture, device, width, height))
                        browser.assert_clean()
                        print(device + ": all ten functional gaps passed", flush=True)
                    editor.journey(browser, base, *editor_fixture)
                    results.append("visual editor: drag, nesting, inline, styling, undo/redo, save, publish, restore")
                    browser.assert_clean()
                    assert not server_errors, server_errors
                except BaseException:
                    for index, context in enumerate(browser.contexts):
                        for page_index, failed in enumerate(context.pages):
                            try:
                                failed.screenshot(path=str(OUTPUT / f"failure-{index}-{page_index}.png"), full_page=True)
                                print("Failure page:", failed.url, failed.locator("body").inner_text()[:3000], flush=True)
                            except Error:
                                pass
                    raise
                finally:
                    native.close()
            browser.assert_clean()
            assert not server_errors, server_errors
            summary = {"status": "passed", "journeys": results, "browser_errors": browser.errors,
                       "screenshots": str(OUTPUT), "count": len([path for path in OUTPUT.glob("*.png")
                           if "failure" not in path.stem and not path.stem.startswith("review-")]),
                       "payment_boundary": "Local provider doubles; no live Stripe payment or email delivery",
                       "isolation": "One fresh disposable SQLite database and one app instance",
                       "successful_downloads": len(browser.downloads)}
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()
        assert not thread.is_alive(), "Acceptance app did not stop cleanly"
        assert not server_errors, server_errors
        summary.update(server_errors=server_errors, server_stopped=True)
        (OUTPUT / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary))


if __name__ == "__main__":
    main()

"""Reusable merchant-only demo acceptance for the isolated builder verifier."""

from playwright.sync_api import expect
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import CommerceMail, Order, OutboxEvent, PaymentTransaction, Stock


def verify_demo_flow(page, site_id, output):
    models = (CommerceMail, Order, OutboxEvent, PaymentTransaction, Stock)
    with SessionLocal() as db:
        before = [db.scalar(select(func.count()).select_from(model)) for model in models]
    root = settings.public_url + f"/admin/sites/{site_id}/demo"
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(root)
    page.get_by_role("button", name="Start private demo", exact=True).click()
    expect(page.get_by_role("heading", name="Sample shop", exact=True)).to_be_visible()
    product = page.locator('.e-card').filter(has=page.get_by_role("heading", name="Sample tea · Original", exact=True))
    product.locator('[name=quantity]').fill('2')
    product.locator('[name=subscription]').select_option('on')
    product.get_by_role("button", name="Set bag quantity").click()
    page.get_by_label("I agree to simulated marketing emails").check()
    page.get_by_role("button", name="Request demo welcome offer", exact=True).click()
    page.get_by_role("button", name="Confirm demo newsletter", exact=True).click()
    expect(page.get_by_text("DEMO-WELCOME: 10% off one first order of sample merchandise.", exact=True)).to_be_visible()
    page.get_by_role("link", name="Checkout", exact=True).click()
    page.get_by_label("Use DEMO-WELCOME (confirm in local inbox first)").check()
    page.get_by_label("I authorize simulated monthly deliveries for recurring items").check()
    page.get_by_role("button", name="Calculate simulated total", exact=True).click()
    expect(page.get_by_role("heading", name="Review simulated order", exact=True)).to_be_visible()
    for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.get_by_role("heading", name="Review simulated order", exact=True).scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(output / f"{device}-demo-checkout.png"))
    page.get_by_role("button", name="Simulate approved payment", exact=True).click()
    page.get_by_role("button", name="Send local demo sign-in", exact=True).click()
    page.get_by_role("button", name="Confirm demo login", exact=True).click()
    page.get_by_role("link", name="My account", exact=True).click()
    expect(page.get_by_role("heading", name="DEMO-0001", exact=True)).to_be_visible()
    page.get_by_role("button", name="Skip next demo delivery", exact=True).click()
    page.get_by_role("button", name="Pause demo subscription", exact=True).click()
    page.get_by_role("button", name="Resume demo subscription", exact=True).click()
    page.get_by_label("Frequency", exact=True).select_option("2")
    page.get_by_role("button", name="Save demo frequency", exact=True).click()
    page.get_by_label("Flavour", exact=True).select_option("sample-tea-mint")
    page.get_by_role("button", name="Save demo flavour", exact=True).click()
    page.get_by_role("button", name="Update simulated payment method", exact=True).click()
    page.get_by_label("US state", exact=True).select_option("TX")
    page.get_by_label("ZIP", exact=True).fill("78701")
    page.get_by_role("button", name="Save demo delivery address", exact=True).click()
    page.get_by_role("button", name="Simulate failed renewal", exact=True).click()
    page.get_by_role("button", name="Resume demo subscription", exact=True).click()
    page.get_by_role("button", name="Simulate next renewal", exact=True).click()
    expect(page.get_by_role("heading", name="DEMO-0002", exact=True)).to_be_visible()
    page.get_by_role("link", name="Tracking", exact=True).click()
    order = page.locator('.e-card').filter(has=page.get_by_role("heading", name="DEMO-0001", exact=True))
    order.get_by_role("button", name="Mark simulated shipped", exact=True).click()
    order.get_by_role("button", name="Mark simulated delivered", exact=True).click()
    page.get_by_role("link", name="My account", exact=True).click()
    expect(page.get_by_text("delivered · DEMO-TRACK-DEMO-0001", exact=True)).to_be_visible()
    for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.get_by_role("heading", name="Order history", exact=True).scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(output / f"{device}-demo-account.png"))
    page.get_by_role("button", name="Cancel demo subscription", exact=True).click()
    expect(page.get_by_role("button", name="Simulate next renewal", exact=True)).to_have_count(0)
    page.get_by_role("link", name="Local inbox", exact=True).click()
    for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.get_by_role("heading", name="Local demo inbox", exact=True).scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(output / f"{device}-demo-inbox.png"))
    with SessionLocal() as db:
        after = [db.scalar(select(func.count()).select_from(model)) for model in models]
    assert before == after, "Demo wrote into real commerce/fulfillment tables"

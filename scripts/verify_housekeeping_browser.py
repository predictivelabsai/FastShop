"""Capture the requested housekeeping storefront and commerce screenshots."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Site


def main():
    base = os.getenv("FASTSHOP_VERIFY_BASE", "http://127.0.0.1:5037").rstrip("/")
    output = Path("output/playwright/housekeeping")
    output.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
        if not site:
            raise RuntimeError("The isolated H2 4 You Liquid fixture was not seeded.")
        site.status = "published"
        site_id = site.id
        db.commit()

    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        storefront = page.goto(base + "/sites/h24you-liquid/")
        assert storefront and storefront.status == 200
        for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            footer = page.locator(".metal-footer")
            footer.scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"{device}-footer.png"), full_page=False)

        login = page.goto(base + "/login?next=/admin/sites")
        assert login and login.status == 200
        page.locator('input[name="email"]').fill(os.environ["FASTSHOP_ADMIN_EMAIL"])
        page.locator('input[name="password"]').fill(os.environ["FASTSHOP_ADMIN_PASSWORD"])
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.wait_for_url("**/admin/sites")
        commerce = page.goto(f"{base}/admin/sites/{site_id}/commerce")
        assert commerce and commerce.status == 200
        assert page.get_by_role("link", name="Manage in Payments", exact=True).is_visible()
        for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.screenshot(path=str(output / f"{device}-commerce.png"), full_page=False)
        context.close()
        browser.close()
    assert not errors, errors
    print({"result": "passed", "screenshots": sorted(path.name for path in output.glob("*.png"))})


if __name__ == "__main__":
    main()

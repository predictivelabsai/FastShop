"""Local-only merchant settings verification; no Stripe calls or saved auth state."""

import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5036")
    args = parser.parse_args()
    if not args.base.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise SystemExit("Use an isolated local database for this mutating check.")
    output = Path("output/playwright/phase2-commerce-settings")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(args.base + "/login?next=/admin/sites")
        page.locator('input[name="email"]').fill(os.environ["FASTSHOP_ADMIN_EMAIL"])
        page.locator('input[name="password"]').fill(os.environ["FASTSHOP_ADMIN_PASSWORD"])
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.wait_for_url("**/admin/sites")
        page.get_by_role("link", name="Open store →").first.click()
        site_url = page.url
        assert page.locator(".a-shell").is_visible()
        page.screenshot(path=str(output / "desktop-phase1-site-settings.png"), full_page=True)
        page.goto(site_url + "/commerce")
        assert "FastShop" in page.title()
        assert page.get_by_text("Stripe test key: required", exact=True).is_visible()
        page.locator('input[name="shipping_minor"]').fill("1000")
        page.locator('input[name="origin_line1"]').fill("Local test warehouse")
        page.locator('input[name="origin_city"]').fill("Tallinn")
        page.locator('input[name="origin_postal_code"]').fill("10111")
        page.get_by_role("button", name="Save commerce settings", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert page.locator('input[name="shipping_minor"]').input_value() == "1000"
        for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(output / f"{device}-commerce-settings.png"), full_page=True)
        page.goto(site_url)
        page.screenshot(path=str(output / "mobile-phase1-site-settings.png"), full_page=True)
        context.close()
        browser.close()
    assert not errors, errors
    print({"result": "passed", "checks": "FastShop branding, settings save, desktop/mobile layout", "errors": errors})


if __name__ == "__main__":
    main()

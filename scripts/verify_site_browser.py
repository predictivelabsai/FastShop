"""Chrome acceptance evidence for Phase 1; never saves browser credentials/state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright


def load_page_media(page):
    """Exercise native lazy loading before capturing the entire document."""
    page.evaluate("document.fonts.ready")
    page.evaluate("window.scrollTo(0, 0)")
    height = page.evaluate("document.documentElement.scrollHeight")
    for offset in range(0, height, 600):
        page.evaluate("offset => window.scrollTo(0, offset)", offset)
        page.wait_for_timeout(80)
    page.wait_for_function("""() => Array.from(document.images)
        .filter(image => image.getClientRects().length)
        .every(image => image.complete && image.naturalWidth > 0)""")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(100)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5033")
    parser.add_argument("--merchant", action="store_true")
    parser.add_argument("--output", default="output/playwright/h24you-phase1")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    paths = ["/", "/shop", "/collections/hydrogen-tablets", "/collections/hydrogen-water-bottles",
             "/products/hydrogen-tablets", "/products/hydroxy-go", "/pages/science", "/blogs/learn",
             "/blogs/learn/what-is-molecular-hydrogen", "/blogs/learn/timing-and-consistency",
             "/blogs/learn/how-to-read-hydrogen-research", "/pages/about-us", "/pages/contact",
             "/pages/terms-and-conditions", "/pages/privacy-policy", "/pages/faq", "/pages/returns-and-refunds"]
    failures, checks = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for device, width, height in [("desktop", 1440, 1000), ("ipad", 834, 1112), ("mobile", 390, 844)]:
            context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
            page = context.new_page()
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.on("response", lambda response: failures.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
            page.goto(args.base + "/sites/h24you/")
            page.locator('#h-cookie button[data-consent="none"]').click()
            assert page.evaluate("window.fastshopConsent.analytics === false && window.fastshopConsent.marketing === false")
            page.evaluate("localStorage.setItem('fastshop-offer:' + document.querySelector('.h-site').dataset.site, 'true')")
            for path in paths:
                response = page.goto(args.base + "/sites/h24you" + path)
                assert response.status == 200, path
                page.wait_for_load_state("networkidle")
                assert page.locator("h1").count() == 1, path
                assert page.locator('link[rel="canonical"]').count() == 1
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (device, path)
                assert page.locator("img").evaluate_all("images => images.every(i => i.hasAttribute('alt') && i.getAttribute('alt').length)")
                load_page_media(page)
                name = path.strip("/").replace("/", "-") or "home"
                page.screenshot(path=str(out / f"{device}-{name}.png"), full_page=True)
                checks.append({"device": device, "path": path, "status": response.status, "title": page.title()})
            page.goto(args.base + "/sites/h24you/pages/science")
            page.get_by_role("button", name="Exercise", exact=True).click()
            assert page.locator('[data-research-theme="Exercise"]:visible').count() == 2
            assert page.locator('[data-research-theme="Reviews"]:visible').count() == 0
            page.get_by_role("button", name="Cookie preferences", exact=True).click()
            page.locator("#h-cookie summary").click()
            page.screenshot(path=str(out / f"{device}-cookie-preferences.png"), full_page=False)
            page.locator('[data-consent="none"]').click()
            if device == "mobile":
                page.get_by_role("button", name="Menu", exact=True).click()
                assert page.locator(".h-nav").is_visible()
                page.screenshot(path=str(out / "mobile-open-menu.png"))
                page.keyboard.press("Escape")
                assert not page.locator(".h-nav").is_visible()
            page.goto(args.base + "/sites/h24you/products/hydrogen-tablets")
            page.get_by_role("combobox", name="Choose your option").select_option(label="Raspberry")
            assert page.get_by_role("button", name="Add to cart — coming in Phase 2").is_disabled()
            page.get_by_role("button", name="View gallery image 2", exact=True).click()
            assert "water-placeholder" in page.locator('[data-gallery-main] img').get_attribute("src")
            context.close()
        if args.merchant:
            if not args.base.startswith(("http://127.0.0.1:", "http://localhost:")):
                raise RuntimeError("Merchant mutation checks are limited to an isolated local database.")
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.goto(args.base + "/login?next=/admin/sites")
            page.locator('input[name="email"]').fill(os.environ["FASTSHOP_ADMIN_EMAIL"])
            page.locator('input[name="password"]').fill(os.environ["FASTSHOP_ADMIN_PASSWORD"])
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/admin/sites")
            page.screenshot(path=str(out / "merchant-sites.png"), full_page=True)
            slug = "browser-" + uuid4().hex[:10]
            page.locator('input[name="name"]').fill("Browser acceptance site")
            page.locator('input[name="slug"]').fill(slug)
            page.get_by_role("button", name="Create site", exact=True).click()
            page.wait_for_url("**/admin/sites/*")
            page.get_by_role("link", name="Browser acceptance site", exact=True).click()
            page.locator('input[name="title"]').fill("Our browser-tested home")
            page.locator('select[name="new_section"]').select_option("text")
            page.get_by_role("button", name="Save draft", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert page.locator("details.e-section").count() == 2
            page.locator("details.e-section").last.locator("summary").click()
            page.locator('textarea[name="section_1_heading"]').fill("Created without code")
            page.locator('textarea[name="section_1_body"]').fill("The merchant created this page, edited a section and published the result.")
            page.get_by_role("button", name="Publish page", exact=True).click()
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(out / "merchant-page-editor.png"), full_page=True)
            visitor = context.new_page()
            visitor.goto(args.base + f"/sites/{slug}/")
            assert visitor.get_by_text("Created without code", exact=True).is_visible()
            visitor.close()
            page.get_by_role("link", name="← All pages and settings").click()
            page.get_by_role("link", name="Media library", exact=True).click()
            page.locator('input[name="title"]').fill("Acceptance image")
            page.locator('input[name="alt"]').fill("Water texture for browser acceptance")
            page.locator('input[type="file"]').set_input_files("static/h24you/water-placeholder.webp")
            page.get_by_role("button", name="Upload image", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert page.get_by_role("heading", name="Acceptance image", exact=True).is_visible()
            page.screenshot(path=str(out / "merchant-media-library.png"), full_page=True)
            checks.append({"merchant": "create site, edit sections, publish, verify public page, upload image", "status": "passed"})
            context.close()
        browser.close()
    report = {"base": args.base, "checks": checks, "failures": failures}
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": failures, "output": str(out)}))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

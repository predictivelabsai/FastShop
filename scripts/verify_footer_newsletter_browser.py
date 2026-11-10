"""Capture desktop and mobile Liquid footer evidence from the local storefront."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5025")
    parser.add_argument("--output", default="output/playwright/footer-newsletter")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
            page = browser.new_page(viewport={"width": width, "height": height})
            response = page.goto(args.base.rstrip("/") + "/sites/h24you-liquid/")
            assert response and response.status == 200
            footer = page.locator("footer")
            expect(footer.get_by_role("heading", name="Notes from the glass")).to_be_visible()
            expect(footer.get_by_placeholder("Your email address")).to_be_visible()
            expect(footer.get_by_role("button", name="Send my code")).to_be_visible()
            expect(footer).to_contain_text("Unsubscribe anytime")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(output / f"{device}.png"), full_page=True)
            page.close()
        browser.close()


if __name__ == "__main__":
    main()

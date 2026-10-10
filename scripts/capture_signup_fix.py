"""Capture the public auth-page fix at the required desktop and mobile sizes."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def capture(base_url: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    routes = (("signup", "/signup"), ("login", "/login"))
    viewports = (("desktop", 1440, 900), ("mobile", 390, 844))
    expected_footer_links = {
        "/marketing/#how-it-works",
        "/marketing/#commerce",
        "/marketing/#pricing",
        "/marketing/#faq",
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for page_name, route in routes:
                for device, width, height in viewports:
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        reduced_motion="reduce",
                    )
                    page = context.new_page()
                    page.on(
                        "console",
                        lambda message: failures.append(message.text)
                        if message.type == "error"
                        else None,
                    )
                    page.on("pageerror", lambda error: failures.append(str(error)))
                    response = page.goto(base_url.rstrip("/") + route)
                    assert response and response.status == 200
                    page.wait_for_load_state("networkidle")
                    page.evaluate("document.fonts.ready")
                    page.evaluate("window.scrollTo(0, 0)")

                    assert not page.evaluate(
                        "document.documentElement.scrollWidth > window.innerWidth + 1"
                    )
                    footer_links = set(
                        page.locator('.m-footer nav[aria-label="Page links"] a')
                        .evaluate_all("links => links.map(link => link.getAttribute('href'))")
                    )
                    assert footer_links == expected_footer_links
                    if page_name == "signup":
                        assert page.locator("main h1").count() == 1
                        assert page.locator('.m-footer a[href="/signup"]').count() == 0
                        assert page.locator(".m-google-link svg").count() == 1

                    stem = f"{page_name}-{device}"
                    page.screenshot(path=str(output_dir / f"{stem}-fold.png"))
                    page.screenshot(path=str(output_dir / f"{stem}-full.png"), full_page=True)
                    context.close()
        finally:
            browser.close()

    if failures:
        raise RuntimeError("Browser console errors: " + " | ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5033")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/playwright/signup-fix"),
    )
    args = parser.parse_args()
    capture(args.base_url, args.output)


if __name__ == "__main__":
    main()

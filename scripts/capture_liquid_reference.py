"""Capture the locally running, unmodified-layout Lovable reference for comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PAGES = (
    ("/", "home"),
    ("/shop", "shop"),
    ("/products/h2-4-you-one", "one"),
    ("/products/hydrogen-tablets", "fizz"),
    ("/products/taste-the-range", "range"),
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5173")
    parser.add_argument("--output", default="output/playwright/h24you-liquid")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    evidence = []
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for width in (375, 768, 1024, 1440):
            for route, name in PAGES:
                context = browser.new_context(
                    viewport={"width": width, "height": 1000}, reduced_motion="reduce"
                )
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                response = page.goto(args.base.rstrip("/") + route)
                assert response.status == 200, (name, width, response.status)
                expect(page.get_by_role("heading", level=1)).to_be_visible()
                page.evaluate("document.fonts.ready")
                page.evaluate("document.querySelectorAll('img').forEach(i => i.loading='eager')")
                # Reference has its own scrolling shell. Exercise it to reveal lazy sections.
                page.evaluate("""async () => {
                    const shell = document.querySelector('.direction-scroll-content');
                    const target = shell || document.scrollingElement;
                    for (let y = 0; y < target.scrollHeight; y += 650) {
                        target.scrollTop = y;
                        await new Promise(requestAnimationFrame);
                    }
                    target.scrollTop = 0;
                    await new Promise(requestAnimationFrame);
                }""")
                page.evaluate("document.querySelectorAll('img').forEach(i => i.loading='eager')")
                page.wait_for_function(
                    "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
                )
                if name == "home":
                    page.wait_for_function("""Array.from(document.querySelectorAll(
                        '.metal-stat span[aria-label]')).every(e =>
                        e.textContent.trim() === e.getAttribute('aria-label'))""")
                # The reference uses a fixed scroll shell. Temporarily expose its full
                # content for Chrome's full-page capture, preserving viewport width.
                page.add_style_tag(content="""
                    .direction-docked-shell { position: relative !important;
                        inset: auto !important; height: auto !important; overflow: visible !important; }
                    .direction-scroll-content { overflow: visible !important; flex: auto !important; }
                """)
                page.screenshot(path=str(out / f"ref-{name}-{width}.png"), full_page=True)
                evidence.append({
                    "page": name,
                    "width": width,
                    "title": page.get_by_role("heading", level=1).inner_text(),
                    "image_count": page.locator("img").count(),
                    "overflow": page.evaluate("document.documentElement.scrollWidth > innerWidth"),
                })
                context.close()
        browser.close()
    report = {"screenshots": len(evidence), "pages": evidence, "errors": errors}
    (out / "reference-results.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"screenshots": len(evidence), "errors": errors}))


if __name__ == "__main__":
    main()

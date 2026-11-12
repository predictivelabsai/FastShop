"""Verify Liquid Styled words through the editor, canvas preview and public storefront.

Run from the repository root with ``python scripts/verify_brand_words_browser.py``.
The verifier creates a disposable local database and does not use provider credentials.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from sqlalchemy import select
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/brand-words"


def fixtures() -> tuple[str, str]:
    """Return the seeded Liquid site and its Fizz product page."""
    from app.db import SessionLocal
    from app.models import Site, SitePage

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
        assert site is not None, "The disposable bootstrap did not seed h24you-liquid."
        page = db.scalar(
            select(SitePage).where(
                SitePage.site_id == site.id,
                SitePage.tenant_id == site.tenant_id,
                SitePage.path == "/products/hydrogen-tablets",
            )
        )
        assert page is not None, "The disposable bootstrap did not seed the Fizz product page."
        site.status = "published"
        db.commit()
        return site.id, page.id


def sign_in(page, base: str) -> None:
    page.goto(base + "/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")


def capture(page, path: Path) -> None:
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(path), full_page=True)


def styled_word_fields(page, key: str):
    return page.locator(f'[data-styled-word-field][data-styled-word-key="{key}"]')


def configure_styled_words(page) -> None:
    """Add a custom-colour Share rule through the merchant editor controls."""
    page.locator('[data-mode="theme"]').click()
    expect(page.locator("[data-styled-word-add]")).to_be_visible()

    words = styled_word_fields(page, "word")
    expect(words).to_have_count(2)
    expect(words.nth(0)).to_have_value("One")
    expect(words.nth(1)).to_have_value("Fizz")

    page.locator("[data-styled-word-add]").click()
    expect(words).to_have_count(3)
    words.nth(2).fill("Share")
    styled_word_fields(page, "italic").nth(2).check()
    styled_word_fields(page, "bold").nth(2).check()
    page.locator("[data-styled-word-colour-mode]").nth(2).select_option("custom")
    page.locator("[data-styled-word-colour-hex]").nth(2).fill("#1e6fe0")

    with page.expect_response(lambda response: "/theme-settings" in response.url):
        page.locator("#save-theme").click()
    expect(page.locator("#save-status")).to_have_text("Saved")


def assert_canvas_preview(page):
    frame = page.locator("#editor-canvas").content_frame
    share_copy = frame.locator(".fs-rich-content").filter(
        has_text="Keep the One for yourself"
    ).first
    expect(share_copy).to_be_visible()
    expect(share_copy.locator(".fs-styled-word")).to_have_count(1)
    expect(frame.locator('.fs-styled-word[data-styled-word-colour="#1e6fe0"]')).to_contain_text(
        "Share"
    )
    expect(frame.locator(".fs-styled-word:visible").filter(has_text="Fizz").first).to_be_visible()
    return share_copy


def publish(page) -> None:
    page.locator("#publish").click()
    expect(page.locator("#publish-dialog")).to_be_visible()
    with page.expect_response(lambda response: "/publish" in response.url):
        page.locator("#confirm-publish").click()
    expect(page.locator("#save-status")).to_have_text("Published")


def assert_live_storefront(page, base: str) -> None:
    response = page.goto(base + "/sites/h24you-liquid/products/hydrogen-tablets")
    assert response is not None and response.status == 200
    share_copy = page.locator(".fs-rich-content").filter(
        has_text="Keep the One for yourself"
    ).first
    expect(share_copy).to_be_visible()
    expect(share_copy.locator(".fs-styled-word")).to_have_count(1)
    expect(page.locator('.fs-styled-word[data-styled-word-colour="#1e6fe0"]')).to_contain_text(
        "Share"
    )
    expect(page.locator(".fs-styled-word:visible").filter(has_text="Fizz").first).to_be_visible()


def verify_device(browser, base: str, site_id: str, page_id: str, device: str,
                  width: int, height: int, *, configure: bool, errors: list[str]) -> None:
    context = browser.new_context(
        viewport={"width": width, "height": height}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    sign_in(page, base)
    page.goto(base + f"/admin/sites/{site_id}/editor?page={page_id}")
    expect(page.locator("#editor-canvas")).to_be_visible()
    expect(page.locator("#save-status")).to_have_text("Saved")

    if configure:
        configure_styled_words(page)
    else:
        page.locator('[data-mode="theme"]').click()
        expect(page.locator("[data-styled-word-add]")).to_be_visible()
        expect(styled_word_fields(page, "word").nth(0)).to_have_value("One")
        expect(styled_word_fields(page, "word").nth(1)).to_have_value("Fizz")
        expect(styled_word_fields(page, "word").nth(2)).to_have_value("Share")

    share_preview = assert_canvas_preview(page)
    if device == "mobile":
        styled_words = page.locator(".theme-styled-words")
        expect(styled_words).to_be_visible()
        styled_words.screenshot(path=str(OUTPUT / f"{device}-editor-styled-words.png"))
        page.locator("#mobile-preview").click()
    else:
        capture(page, OUTPUT / f"{device}-editor-styled-words.png")
    share_preview.screenshot(path=str(OUTPUT / f"{device}-canvas-preview.png"))

    if configure:
        publish(page)

    storefront = context.new_page()
    assert_live_storefront(storefront, base)
    capture(storefront, OUTPUT / f"{device}-live-storefront.png")
    context.close()


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-brand-words-browser-") as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, page_id = fixtures()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated Styled words browser server failed to start")
                thread.join(0.05)
            errors: list[str] = []
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    verify_device(
                        browser, f"http://127.0.0.1:{port}", site_id, page_id,
                        "desktop", 1440, 1000, configure=True, errors=errors,
                    )
                    verify_device(
                        browser, f"http://127.0.0.1:{port}", site_id, page_id,
                        "mobile", 390, 844, configure=False, errors=errors,
                    )
                finally:
                    browser.close()
            assert not errors, errors
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "screenshots": str(OUTPUT),
                        "count": 6,
                        "browser_errors": errors,
                    }
                )
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

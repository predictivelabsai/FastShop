"""Capture the shared rich-text editors on the generic merchant admin surface."""

from __future__ import annotations

import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "playwright" / "rich-text"


def seed_store() -> tuple[str, str]:
    """Create a neutral merchant, product and policy fixture for browser capture."""
    from sqlalchemy import select

    from app import catalog_admin, content
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == EMAIL))
        assert user is not None
        site = content.create_site(db, user.id, "Paperfield Goods", "paperfield-goods")
        product = catalog_admin.create_product(db, site, {
            "name": "Field notebook",
            "slug": "field-notebook",
            "description_html": (
                "<h2>Made for everyday notes</h2><p>A durable notebook for plans and lists.</p>"
                "<ul><li>Lay-flat pages</li><li>Recycled cover</li></ul>"
            ),
        })
        settings = dict(site.settings_json or {})
        settings["policies"] = {
            **(settings.get("policies") or {}),
            "privacy": "<h2>Privacy at a glance</h2><p>We use details to fulfil orders.</p>",
        }
        site.settings_json = settings
        site.published_settings_json = dict(settings)
        db.commit()
        return site.id, product.id


def login(page) -> None:
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")


def assert_toolbar(editor) -> None:
    expect(editor).to_have_attribute("data-rich-text-ready", "true")
    expect(editor.locator("[data-rich-content]")).to_be_visible()
    expect(editor.get_by_role("button", name="Bold")).to_be_visible()
    expect(editor.get_by_role("button", name="Italic")).to_be_visible()
    expect(editor.get_by_role("button", name="Bulleted list")).to_be_visible()
    expect(editor.get_by_role("button", name="Numbered list")).to_be_visible()
    expect(editor.get_by_role("button", name="Insert or edit link")).to_be_visible()
    expect(editor.locator("[data-rich-block]")).to_be_visible()


def capture(page, editor, device: str, name: str) -> None:
    editor.scroll_into_view_if_needed()
    expect(editor).to_be_visible()
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name
    editor.screenshot(path=str(OUTPUT / f"{device}-{name}.png"), timeout=60_000)


def capture_policy_editor(page, site_id: str, device: str) -> None:
    response = page.goto(f"/admin/sites/{site_id}/settings/general")
    assert response and response.status == 200
    expect(page.get_by_role("heading", name="Brand and shared content")).to_be_visible()
    editor = page.locator(".admin-rich-text").filter(
        has=page.locator('input[name="policy_privacy"]')
    )
    assert_toolbar(editor)
    expect(editor.locator("[data-rich-content]")).to_contain_text("Privacy at a glance")
    capture(page, editor, device, "policy-editor")


def capture_product_editor(page, site_id: str, product_id: str, device: str) -> None:
    response = page.goto(f"/admin/sites/{site_id}/products/{product_id}")
    assert response and response.status == 200
    expect(page.get_by_role("heading", name="Field notebook", exact=True)).to_be_visible()
    editor = page.locator(".admin-rich-text").filter(
        has=page.locator('input[name="description_html"]')
    )
    assert_toolbar(editor)
    expect(editor.locator("[data-rich-content]")).to_contain_text("Made for everyday notes")
    capture(page, editor, device, "product-editor")


def verify_device(browser, base: str, site_id: str, product_id: str, device: str,
                  width: int, height: int, errors: list[str]) -> None:
    context = browser.new_context(base_url=base, viewport={"width": width, "height": height},
                                  reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(15_000)
    page.on("pageerror", lambda error: errors.append(f"{device}: {error}"))
    try:
        login(page)
        capture_policy_editor(page, site_id, device)
        capture_product_editor(page, site_id, product_id, device)
    except BaseException:
        page.screenshot(path=str(OUTPUT / f"{device}-failure.png"), full_page=True)
        raise
    finally:
        context.close()


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-rich-text-browser-") as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, product_id = seed_store()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error", access_log=False,
        ))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local rich-text browser server failed to start")
                thread.join(0.05)
            errors: list[str] = []
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Error:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                        verify_device(browser, f"http://127.0.0.1:{port}", site_id, product_id,
                                      device, width, height, errors)
                finally:
                    browser.close()
            assert not errors, errors
            for failure in OUTPUT.glob("*-failure.png"):
                failure.unlink()
            print(f"Captured rich-text screenshots in {OUTPUT}")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

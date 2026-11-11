"""Capture shared rich-text editors and policy rendering on desktop and mobile.

Run from the repository root with ``uv run python scripts/verify_rich_text_browser.py``.
The app uses a disposable local database and no provider credentials.
"""

from __future__ import annotations

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
OUTPUT = ROOT / "output/playwright/rich-text"


def fixtures():
    from app import content
    from app.db import SessionLocal
    from app.models import Site

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
        assert site is not None
        page = content.create_page(
            db,
            site,
            "Privacy and returns",
            "/pages/rich-text-browser",
            document={
                "version": 1,
                "title": "Privacy and returns",
                "blocks": [{
                    "id": "legal-copy",
                    "type": "text",
                    "heading": "Privacy details",
                    "body": "<h2>How we use your details</h2><p>We use information to fulfil orders.</p>"
                            "<ul><li>Order updates</li><li>Customer support</li></ul>",
                }],
            },
        )
        settings = dict(site.settings_json or {})
        settings["policies"] = {
            **(settings.get("policies") or {}),
            "privacy": "<h2>Privacy at a glance</h2><p>We use your details to run this shop.</p>"
                       "<ul><li>Process orders</li><li>Answer questions</li></ul>",
        }
        site.status = "published"
        site.settings_json = settings
        site.published_settings_json = settings
        db.commit()
        return site.id, page.id


def sign_in(page, base):
    page.goto(base + "/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")


def capture_editor(page, locator, path):
    locator.scroll_into_view_if_needed()
    expect(locator).to_be_visible()
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(150)
    locator.screenshot(path=str(path))


def exercise_policy_editor(page, base, site_id, device):
    page.goto(base + f"/admin/sites/{site_id}/settings/general")
    expect(page.get_by_role("heading", name="Brand and shared content")).to_be_visible()
    editor = page.locator(".admin-rich-text").filter(
        has=page.locator('input[name="policy_privacy"]')
    )
    content = editor.locator("[data-rich-content]")
    content.select_text()
    editor.locator("[data-rich-block]").select_option("p")
    content.fill("Privacy rights and choices")
    content.select_text()
    editor.locator('[data-rich-command="bold"]').click()
    bold_html = content.evaluate("element => element.innerHTML")
    if device == "desktop":
        assert "<strong>" in bold_html or "<b>" in bold_html, bold_html
    content.select_text()
    editor.locator("[data-rich-block]").select_option("h2")
    expect(content).to_contain_text("Privacy rights and choices")
    rich_html = content.evaluate("element => element.innerHTML")
    assert "<h2>" in rich_html, rich_html
    capture_editor(page, editor, OUTPUT / f"{device}-policy-editor.png")
    if device == "desktop":
        page.get_by_role("button", name="Save draft settings").click()
        expect(page.get_by_role("heading", name="Brand and shared content")).to_be_visible()


def exercise_legal_editor(page, base, site_id, page_id, device):
    page.goto(base + f"/admin/sites/{site_id}/pages/{page_id}")
    page.locator(".e-section summary").first.click()
    editor = page.locator(".admin-rich-text").filter(
        has=page.locator('input[name="section_0_body"]')
    )
    expect(editor.locator("[data-rich-content]")).to_contain_text("How we use your details")
    capture_editor(page, editor, OUTPUT / f"{device}-legal-page-editor.png")


def exercise_visual_inspector(page, base, site_id, page_id, device):
    page.goto(base + f"/admin/sites/{site_id}/editor?page={page_id}")
    frame = page.locator("#editor-canvas").content_frame
    expect(frame.locator('[data-block-id="legal-copy"]')).to_be_visible()
    tree_row = page.locator('.tree-row[data-block-id="legal-copy"]')
    if device == "mobile":
        page.evaluate('document.querySelector(\'.tree-row[data-block-id="legal-copy"]\').click()')
    else:
        tree_row.click()
    rich = page.locator("#settings-content [data-rich-text]").first
    expect(rich).to_have_attribute("data-rich-text-ready", "true")
    expect(rich.locator("[data-rich-content]")).to_contain_text("How we use your details")
    capture_editor(page, rich, OUTPUT / f"{device}-visual-inspector.png")
    field = frame.locator('[data-field="body"][data-field-type="richtext"]').first
    expect(field).to_be_visible()
    field.evaluate("element => element.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }))")
    expect(field).to_have_attribute("contenteditable", "true")
    expect(frame.locator('.fs-rich-tools [data-rich-command="bold"]')).to_be_visible()


def exercise_storefront(page, base, site_id, device):
    response = page.goto(base + "/sites/h24you-liquid/pages/privacy-policy")
    assert response and response.status == 200
    expect(page.get_by_role("heading", name="Privacy at a glance")).to_be_visible()
    expect(page.locator(".liquid-policy-content ul li")).to_have_count(2)
    page.locator("#h-cookie").evaluate("element => { element.hidden = true; }")
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(OUTPUT / f"{device}-storefront-policy.png"), full_page=True)


def main():
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-rich-text-browser-") as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, page_id = fixtures()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated rich-text browser server failed to start")
                thread.join(0.05)
            errors = []
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                        context = browser.new_context(
                            viewport={"width": width, "height": height}, reduced_motion="reduce"
                        )
                        page = context.new_page()
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        sign_in(page, f"http://127.0.0.1:{port}")
                        exercise_policy_editor(page, f"http://127.0.0.1:{port}", site_id, device)
                        exercise_legal_editor(page, f"http://127.0.0.1:{port}", site_id, page_id, device)
                        exercise_visual_inspector(page, f"http://127.0.0.1:{port}", site_id, page_id, device)
                        exercise_storefront(page, f"http://127.0.0.1:{port}", site_id, device)
                        context.close()
                    assert not errors, errors
                finally:
                    browser.close()
            print(f"Captured rich-text desktop and mobile screenshots in {OUTPUT}")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

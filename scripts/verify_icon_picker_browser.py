"""Exercise the merchant icon picker against an isolated Liquid store."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "playwright" / "icons"


def block_rows(blocks):
    for block in blocks:
        yield block
        yield from block_rows(block.get("children", []))


def benefits_block(document):
    return next(
        block
        for block in block_rows(document.get("blocks", []))
        if block.get("id") == "why" and block.get("type") == "liquid_benefits"
    )


def editor_url(site_id, page_id):
    return f"/admin/sites/{site_id}/editor?page={page_id}"


def fetch_document(page, site_id, page_id):
    response = page.request.get(
        f"/admin/sites/{site_id}/editor/api/document", params={"page": page_id}
    )
    if response.status != 200:
        raise AssertionError(f"Could not load editor document: HTTP {response.status}")
    return response.json()


def login(page, email, password):
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(email)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    expect(page.locator(".a-shell")).to_be_visible()


def select_benefits(page):
    if page.viewport_size["width"] <= 760:
        page.locator('[data-mode="sections"]').click()
    page.locator('#left-content .tree-row[data-block-id="why"]').click()
    expect(page.locator("#selection-header h2")).to_have_text("Benefits")


def open_item(page, index):
    details = page.locator(
        f'#settings-content details[data-repeater="items"][data-index="{index}"]'
    )
    expect(details).to_be_attached()
    if details.get_attribute("open") is None:
        details.locator("summary").click()
    return details


def icon_trigger(page, index):
    return page.locator(f'#settings-content [data-icon="items.{index}.icon"]')


def open_picker(page, index):
    trigger = icon_trigger(page, index)
    expect(trigger).to_be_visible()
    trigger.click()
    dialog = page.locator("#icon-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_label("Search icons", exact=True)).to_be_focused()
    expect(dialog.locator("#icon-grid").get_by_role("button").first).to_be_visible()
    return trigger, dialog


def select_droplet_with_keyboard(page, dialog):
    search = dialog.get_by_label("Search icons", exact=True)
    search.fill("droplet")
    droplet = dialog.get_by_role("button", name="Droplet", exact=True)
    expect(droplet).to_be_visible()
    search.fill("")
    choices = dialog.locator("#icon-grid").get_by_role("button")
    droplet.focus()
    page.keyboard.press("Home")
    expect(choices.first).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(choices.nth(1)).to_be_focused()
    droplet.focus()
    page.keyboard.press("Enter")
    expect(dialog).to_be_hidden()


def save(page):
    with page.expect_response(
        lambda response: (
            response.request.method == "PUT"
            and urlsplit(response.url).path.endswith("/editor/api/draft")
        )
    ) as response_info:
        page.get_by_role("button", name="Save", exact=True).click()
    response = response_info.value
    if response.status != 200:
        raise AssertionError(f"Draft save failed (HTTP {response.status})")
    expect(page.locator("#save-status")).to_have_text("Saved")
    expect(page.locator("#editor-error")).to_be_hidden()


def main():
    os.environ.pop("FASTSHOP_BROWSER_DB_URL", None)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fastshop-icon-picker-") as data_dir:
        isolate(data_dir, port)
        import uvicorn
        from sqlalchemy import select

        from app import content
        from app.db import SessionLocal, engine
        from app.main import app
        from app.models import Site

        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
            if site is None:
                raise AssertionError("Isolated application did not seed h24you-liquid")
            home = next(page for page in content.site_pages(db, site) if page.path == "/")
            site_id, home_id = site.id, home.id

        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        browser = None
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated editor server failed to start")
                thread.join(0.05)

            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Error:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(
                    base_url=f"http://127.0.0.1:{port}",
                    viewport={"width": 1440, "height": 900},
                    reduced_motion="reduce",
                )
                page = context.new_page()
                page.set_default_timeout(20_000)
                browser_errors = []
                page.on("pageerror", lambda error: browser_errors.append(error))
                login(page, EMAIL, PASSWORD)

                editor = editor_url(site_id, home_id)
                page.goto(editor)
                expect(page.locator("#save-status")).to_have_text("Saved")
                document = fetch_document(page, site_id, home_id)
                items = benefits_block(document["document"]).get("items", [])
                if not items:
                    raise AssertionError("The seeded benefits section has no items")
                index = 0
                legacy_theme = items[index].get("theme")

                select_benefits(page)
                open_item(page, index)
                trigger, dialog = open_picker(page, index)
                page.screenshot(path=str(OUTPUT / "desktop-picker.png"), full_page=True)
                page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()
                expect(trigger).to_be_focused()

                _, dialog = open_picker(page, index)
                select_droplet_with_keyboard(page, dialog)
                updated_trigger = icon_trigger(page, index)
                expect(updated_trigger).to_contain_text("droplet")
                save(page)

                saved_item = benefits_block(fetch_document(page, site_id, home_id)["document"])[
                    "items"
                ][index]
                if saved_item.get("icon") != "lucide:droplet":
                    raise AssertionError("The selected icon did not persist in the draft")
                if saved_item.get("theme") != legacy_theme:
                    raise AssertionError("Choosing an icon changed the benefit theme")

                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(editor)
                expect(page.locator("#save-status")).to_have_text("Saved")
                select_benefits(page)
                open_item(page, index)
                _, mobile_dialog = open_picker(page, index)
                expect(
                    mobile_dialog.get_by_role("button", name="Droplet", exact=True)
                ).to_be_visible()
                page.screenshot(path=str(OUTPUT / "mobile-picker.png"), full_page=True)
                context.close()
                browser.close()
                browser = None
                if browser_errors:
                    raise AssertionError([str(error) for error in browser_errors])

                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "item_index": index,
                            "theme": legacy_theme,
                            "screenshots": str(OUTPUT),
                        }
                    )
                )
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Error:
                    pass
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

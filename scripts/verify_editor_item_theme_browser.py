"""Verify Liquid benefit item theme and heading edits in the visual editor."""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/editor-item-theme"
LOCALE_KEY = re.compile(r"^[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*$")


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


def contains_item_locale_key(document, schema):
    for block in block_rows(document.get("blocks", [])):
        fields = schema.get(block.get("type"), {}).get("fields", [])
        repeater = next(
            (
                field
                for field in fields
                if field.get("id") == "items" and field.get("type") == "repeater"
            ),
            {},
        )
        allowed = {
            field.get("id")
            for field in repeater.get("item_fields", repeater.get("fields", []))
        }
        for item in block.get("items", []):
            if isinstance(item, dict) and any(
                LOCALE_KEY.fullmatch(key) and key not in allowed for key in item
            ):
                return True
    return False


def editor_url(site_id, page_id):
    return f"/admin/sites/{site_id}/editor?page={page_id}"


def fetch_document(page, site_id, page_id):
    response = page.request.get(
        f"/admin/sites/{site_id}/editor/api/document", params={"page": page_id}
    )
    if response.status != 200:
        raise AssertionError(f"Could not load editor document: HTTP {response.status}")
    return response.json()


def select_benefits(page):
    if page.viewport_size["width"] <= 760:
        page.locator('[data-mode="sections"]').click()
    page.locator('#left-content .tree-row[data-block-id="why"]').click()
    expect(page.locator("#selection-header h2")).to_have_text("Benefits")
    expect(page.locator('#settings-content [data-setting="heading"]')).to_have_value(
        "Why drink hydrogen water?"
    )


def open_item(page, index):
    details = page.locator(
        f'#settings-content details[data-repeater="items"][data-index="{index}"]'
    )
    expect(details).to_be_attached()
    if not details.evaluate("element => element.open"):
        details.locator("summary").click()
    return details


def save_and_capture(page, captured, schema):
    before = len(captured)
    with page.expect_response(
        lambda response: response.request.method == "PUT"
        and urlsplit(response.url).path.endswith("/editor/api/draft")
    ) as response_info:
        page.get_by_role("button", name="Save", exact=True).click()
    response = response_info.value
    expect(page.locator("#save-status")).to_have_text("Saved")
    expect(page.locator("#editor-error")).to_be_hidden()
    if response.status != 200:
        raise AssertionError(f"Draft save failed (HTTP {response.status})")
    if len(captured) <= before:
        raise AssertionError("No editor draft request was captured")
    payload = captured[-1]
    if not isinstance(payload, dict) or not isinstance(payload.get("document"), dict):
        raise AssertionError("The editor draft request did not contain a document")
    if contains_item_locale_key(payload["document"], schema):
        raise AssertionError("The browser sent a locale key at item level")
    return payload["document"]


def login(page, email, password):
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(email)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    expect(page.locator(".a-shell")).to_be_visible()


def main():
    os.environ.pop("FASTSHOP_BROWSER_DB_URL", None)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fastshop-editor-item-theme-") as data_dir:
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
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
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
                login(page, EMAIL, PASSWORD)
                captured = []

                def capture_request(request):
                    if (
                        request.method == "PUT"
                        and urlsplit(request.url).path.endswith("/editor/api/draft")
                    ):
                        captured.append(request.post_data_json)

                page.on("request", capture_request)
                editor = editor_url(site_id, home_id)
                page.goto(editor)
                expect(page.locator("#save-status")).to_have_text("Saved")
                expect(page.locator("#editor-error")).to_be_hidden()

                initial = fetch_document(page, site_id, home_id)
                schema = initial.get("schema", {})
                if "blocks" in schema:
                    schema = schema["blocks"]
                item_rows = benefits_block(initial["document"]).get("items", [])
                if not item_rows:
                    raise AssertionError("The seeded benefits section has no items")
                index = min(4, len(item_rows) - 1)
                original_item = item_rows[index]
                select_benefits(page)
                open_item(page, index)

                theme = page.locator(f'#settings-content [data-setting="items.{index}.theme"]')
                old_theme = theme.input_value()
                theme_options = theme.locator("option").evaluate_all(
                    "options => options.map(option => option.value)"
                )
                new_theme = next((value for value in theme_options if value != old_theme), None)
                if new_theme is None:
                    raise AssertionError("The benefits item theme select has no alternate value")
                heading = page.locator(
                    f'#settings-content [data-setting="items.{index}.heading"]'
                )
                body = page.locator(f'#settings-content [data-setting="items.{index}.body"]')
                original_heading = heading.input_value()
                original_body = body.input_value()

                theme.select_option(value=new_theme)
                open_item(page, index)
                expect(
                    page.locator(f'#settings-content [data-setting="items.{index}.heading"]')
                ).to_have_value(original_heading)
                expect(
                    page.locator(f'#settings-content [data-setting="items.{index}.body"]')
                ).to_have_value(original_body)
                themed_document = save_and_capture(page, captured, schema)
                themed_item = benefits_block(themed_document)["items"][index]
                if themed_item.get("theme") != new_theme:
                    raise AssertionError(
                        f"The benefits item theme did not save: expected {new_theme!r}, "
                        f"received {themed_item.get('theme')!r}"
                    )
                if themed_item.get("heading") != original_item.get("heading"):
                    raise AssertionError("Changing the theme changed the item heading")
                if themed_item.get("body") != original_item.get("body"):
                    raise AssertionError("Changing the theme changed the item body")

                page.goto(editor)
                expect(page.locator("#save-status")).to_have_text("Saved")
                expect(page.locator("#editor-error")).to_be_hidden()
                reloaded_theme_item = benefits_block(
                    fetch_document(page, site_id, home_id)["document"]
                )["items"][index]
                if reloaded_theme_item.get("theme") != new_theme:
                    raise AssertionError("The saved theme did not survive editor reload")
                if reloaded_theme_item.get("heading") != original_item.get("heading"):
                    raise AssertionError("The item heading changed after editor reload")
                if reloaded_theme_item.get("body") != original_item.get("body"):
                    raise AssertionError("The item body changed after editor reload")

                select_benefits(page)
                open_item(page, index)
                page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)

                heading = page.locator(
                    f'#settings-content [data-setting="items.{index}.heading"]'
                )
                body = page.locator(f'#settings-content [data-setting="items.{index}.body"]')
                heading_before_edit = heading.input_value()
                body_before_edit = body.input_value()
                edited_heading = f"{heading_before_edit} (Browser check)"
                heading.fill(edited_heading)
                heading_document = save_and_capture(page, captured, schema)
                heading_item = benefits_block(heading_document)["items"][index]
                saved_heading = heading_item.get("heading")
                locale = initial.get("default_locale", "en")
                saved_heading_text = (
                    saved_heading.get(locale, "") if isinstance(saved_heading, dict) else saved_heading
                )
                if saved_heading_text != edited_heading:
                    raise AssertionError("The side panel heading edit did not save")
                if heading_item.get("theme") != new_theme:
                    raise AssertionError("The heading edit changed the saved theme")
                if heading_item.get("body") != original_item.get("body"):
                    raise AssertionError("The heading edit changed the item body")

                page.goto(editor)
                expect(page.locator("#save-status")).to_have_text("Saved")
                expect(page.locator("#editor-error")).to_be_hidden()
                saved_item = benefits_block(
                    fetch_document(page, site_id, home_id)["document"]
                )["items"][index]
                saved_heading = saved_item.get("heading")
                saved_heading_text = (
                    saved_heading.get(locale, "") if isinstance(saved_heading, dict) else saved_heading
                )
                if saved_heading_text != edited_heading:
                    raise AssertionError("The side panel heading edit did not survive editor reload")
                if saved_item.get("theme") != new_theme:
                    raise AssertionError("The theme changed after the heading edit")
                if saved_item.get("body") != original_item.get("body"):
                    raise AssertionError("The item body changed after the heading edit")

                select_benefits(page)
                open_item(page, index)
                expect(
                    page.locator(f'#settings-content [data-setting="items.{index}.heading"]')
                ).to_have_value(edited_heading)
                expect(
                    page.locator(f'#settings-content [data-setting="items.{index}.body"]')
                ).to_have_value(body_before_edit)
                page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(editor)
                expect(page.locator("#save-status")).to_have_text("Saved")
                expect(page.locator("#editor-error")).to_be_hidden()
                select_benefits(page)
                open_item(page, index)
                page.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
                browser.close()
                browser = None

                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "item_index": index,
                            "theme": new_theme,
                            "saves": len(captured),
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

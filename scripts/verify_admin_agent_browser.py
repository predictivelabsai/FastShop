"""Browser check for the admin store assistant using isolated local data."""
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
OUTPUT = ROOT / "output/playwright/admin-agent"


def main():
    import os

    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-admin-agent-") as data_dir:
        isolate(data_dir, port)
        os.environ["XAI_API_KEY"] = ""
        os.environ["FASTSHOP_AGENT_FAKE"] = "1"
        from app import store_agent
        from app.db import SessionLocal, engine
        from app.main import app
        from app.models import Product, Site

        real_run_turn = store_agent.run_turn
        demo_product_id = ""
        demo_product_name = ""

        class FakeTransport:
            def __init__(self, product_id, product_name, change_requested):
                self.product_id = product_id
                self.product_name = product_name
                self.change_requested = change_requested
                self.called = False

            def complete(self, _messages, _tools):
                if self.called:
                    return {"content": "I checked the store data and prepared the requested change."}
                self.called = True
                if self.change_requested:
                    return {"tool_calls": [{"id": "browser-update", "type": "function",
                        "function": {"name": "update_product", "arguments": json.dumps({
                            "product_id": self.product_id,
                            "name": self.product_name + " Updated"})}}]}
                return {"tool_calls": [{"id": "browser-read", "type": "function",
                    "function": {"name": "list_products", "arguments": json.dumps({
                        "query": "", "limit": 10})}}]}

        def fake_run_turn(db, site_row, user, message, *, page_id, selection=None,
                          conversation_id=None, transport=None):
            events = real_run_turn(db, site_row, user, message, page_id=page_id,
                selection=selection, conversation_id=conversation_id,
                transport=FakeTransport(demo_product_id, demo_product_name,
                                        "lower the price" in message.casefold()))
            return events

        store_agent.run_turn = fake_run_turn

        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
            demo_product = db.scalar(select(Product).where(
                Product.tenant_id == site.tenant_id).order_by(Product.name, Product.id))
            demo_product_id, demo_product_name = demo_product.id, demo_product.name
            site_id = site.id
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
            log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated admin assistant server failed to start")
                thread.join(0.05)
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(base_url=f"http://127.0.0.1:{port}",
                    viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                page = context.new_page()
                page.goto("/login?next=/admin/sites")
                page.get_by_label("Email address", exact=True).fill(EMAIL)
                page.get_by_label("Password", exact=True).fill(PASSWORD)
                page.get_by_role("button", name="Sign in", exact=True).click()
                page.wait_for_url("**/admin/sites")
                page.goto(f"/admin/sites/{site_id}/products")
                page.get_by_role("button", name="Ask assistant").click()
                expect(page.locator(".agent-drawer").get_by_text("AI provider not configured")).to_be_visible()
                expect(page.locator(".agent-drawer [data-agent-form]")).to_have_count(0)
                page.keyboard.press("Escape")
                expect(page.locator(".agent-overlay")).to_be_hidden()
                page.goto(f"/admin/sites/{site_id}/assistant")
                expect(page.locator("#admin-main").get_by_text("AI provider not configured")).to_be_visible()
                full_chat = page.locator('[data-agent-root]')
                expect(full_chat.locator('[data-agent-form]')).to_have_count(0)
                from dataclasses import replace

                import app.config
                app.config.settings = replace(app.config.settings, xai_api_key="browser-test-key")
                page.reload()
                full_chat = page.locator('[data-agent-root]')
                expect(full_chat.locator("#agent-suggestions")).to_be_visible()
                full_chat.locator('[data-agent-form] textarea').fill("Which products sold best this month?")
                full_chat.locator('[data-agent-form]').get_by_role("button", name="Send message").click()
                expect(page.locator(".agent-step").first).to_contain_text("List Products")
                expect(page.locator(".agent-message.assistant").first).to_contain_text("checked the store data")
                expect(page.locator(".agent-changes summary")).to_contain_text("1 edit")
                page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)

                full_chat.locator('[data-agent-form] textarea').fill("Lower the price of this product")
                full_chat.locator('[data-agent-form]').get_by_role("button", name="Send").click()
                card = page.locator(".agent-actions")
                expect(card).to_contain_text("Review this change")
                card.get_by_role("button", name="Confirm").click()
                expect(card).to_contain_text("Confirmed")

                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 900})
                page.goto(f"/admin/sites/{site_id}/products")
                launch = page.get_by_role("button", name="Ask assistant")
                launch.click()
                expect(page.locator(".agent-drawer")).to_be_visible()
                expect(page.locator(".agent-drawer")).to_have_css("width", "420px")
                expect(page.locator(".agent-drawer textarea")).to_be_focused()
                heading = page.locator("main h1").first
                expect(heading).to_be_visible()
                assert page.locator(".agent-overlay").evaluate("node => getComputedStyle(node).backgroundColor === 'rgba(0, 0, 0, 0)'")
                assert page.locator(".agent-scrim").count() == 0
                assert heading.evaluate("node => { const r = node.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return top === node || node.contains(top); }")
                page.screenshot(path=str(OUTPUT / "drawer-desktop.png"), full_page=False)
                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.locator(".agent-drawer")).to_have_css("width", "390px")
                page.screenshot(path=str(OUTPUT / "drawer-mobile.png"), full_page=False)
                page.locator("[data-agent-close]").last.click()
                expect(page.locator(".agent-overlay")).to_be_hidden()
                expect(launch).to_be_focused()
                launch.focus()
                page.keyboard.press("Control+j")
                expect(page.locator(".agent-drawer")).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator(".agent-overlay")).to_be_hidden()
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()
    print(json.dumps({"status": "passed", "screenshots": str(OUTPUT)}))


if __name__ == "__main__":
    main()

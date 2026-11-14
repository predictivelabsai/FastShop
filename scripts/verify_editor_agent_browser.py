"""Exercise the visual editor assistant against an isolated development database."""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/editor-agent"


def main():
    import os

    import uvicorn
    sys.path.insert(0, str(ROOT))
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-editor-agent-") as data_dir:
        isolate(data_dir, port)
        os.environ["FASTSHOP_AGENT_FAKE"] = "1"
        os.environ["FASTSHOP_AGENT_FAKE_DELAY"] = "1.5"
        from sqlalchemy import select

        from app import content
        from app.db import SessionLocal, engine
        from app.main import app
        from app.models import AgentMessage, ProductVariant, Site, VariantChannelListing
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
            home = next(page for page in content.site_pages(db, site) if page.path == "/")
            site_id, page_id = site.id, home.id
            listing_pair = db.execute(select(ProductVariant, VariantChannelListing).join(
                VariantChannelListing, VariantChannelListing.variant_id == ProductVariant.id
            ).where(ProductVariant.tenant_id == site.tenant_id,
                    VariantChannelListing.channel_id == site.channel_id).limit(1)).first()
            assert listing_pair, "The seeded store needs a priced product variant"
            variant, listing = listing_pair
            variant_id, initial_price = variant.id, listing.price_minor
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
            log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated editor server failed to start")
                thread.join(0.05)
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(
                    base_url=f"http://127.0.0.1:{port}", viewport={"width": 1440, "height": 900},
                    reduced_motion="reduce",
                )
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}/login?next=/admin/sites")
                page.get_by_label("Email address", exact=True).fill(EMAIL)
                page.get_by_label("Password", exact=True).fill(PASSWORD)
                page.get_by_role("button", name="Sign in", exact=True).click()
                page.wait_for_url("**/admin/sites")
                page.goto(f"/admin/sites/{site_id}/editor?page={page_id}")
                page.locator('[data-mode="assistant"]').click()
                assistant = page.locator(".agent-panel")
                expect(assistant.get_by_role("button", name="Create an FAQ page about hydrogen")).to_be_visible()
                expect(assistant.get_by_role("button", name="Audit the site's SEO and improve the home page metadata")).to_be_visible()
                expect(assistant.get_by_role("button", name="Review shipping, tax, and store policies")).to_be_visible()
                expect(assistant.get_by_role("button", name="Which products sold best this month?")).to_be_visible()
                page.screenshot(path=str(OUTPUT / "pinned-composer.png"), full_page=True)
                sent = []
                page.on("request", lambda request: sent.append(request.post_data_json)
                        if request.method == "POST" and request.url.endswith("/editor/agent") else None)
                page.locator("#agent-input").fill("Change the selected headline")
                page.locator("#agent-form").press("Enter")
                expect(page.locator(".agent-step.is-done")).to_be_visible()
                page.screenshot(path=str(OUTPUT / "streaming.png"), full_page=True)
                expect(page.locator(".agent-message.assistant")).to_contain_text("updated")
                page.locator("#agent-input").fill("Now make the subheading shorter")
                page.locator("#agent-form").press("Enter")
                expect(page.locator(".agent-message.user")).to_have_count(2)
                expect(page.locator(".agent-message.assistant").last).to_contain_text("updated")
                history = page.request.get(f"/admin/sites/{site_id}/editor/agent/history").json()
                assert sent[1]["conversation_id"] == history["conversation_id"]
                assert [message["role"] for message in history["messages"]].count("user") == 2
                page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)
                restore = page.locator("[data-restore-message]").last
                expect(restore).to_be_visible()
                restore.click()
                page.get_by_role("button", name="Restore draft").click()
                page.wait_for_timeout(300)
                page.locator("#save").click()
                if page.locator("#conflict-dialog").is_visible():
                    page.locator("#conflict-reload").click()
                expect(page.locator("#save-status")).to_contain_text("Saved")
                def propose_price(prompt):
                    previous_cards = page.locator(".agent-action-card").count()
                    page.locator("#agent-input").fill(prompt)
                    page.locator("#agent-form").press("Enter")
                    expect(page.locator(".agent-action-card")).to_have_count(previous_cards + 1)
                    card = page.locator(".agent-action-card").last
                    expect(card).to_be_visible()
                    expect(card.get_by_role("button", name="Confirm")).to_be_enabled(timeout=10000)
                    return card

                decline_card = propose_price("Read the product and propose a price change for me to decline")
                history = page.request.get(f"/admin/sites/{site_id}/editor/agent/history").json()
                price_message = next(message for message in history["messages"]
                                     if any(action["summary"].startswith("Change price")
                                            for action in message.get("actions", [])))
                assert {event["label"] for event in price_message["events"]} >= {
                    "Get Product", "Set Variant Price"}
                page.screenshot(path=str(OUTPUT / "cards-desktop.png"), full_page=True)
                decline_card.get_by_role("button", name="Decline").click()
                expect(decline_card).to_contain_text("Declined")
                with SessionLocal() as db:
                    current = db.scalar(select(VariantChannelListing).where(
                        VariantChannelListing.variant_id == variant_id,
                        VariantChannelListing.channel_id == site.channel_id))
                    assert current.price_minor == initial_price

                confirm_card = propose_price("Read the product and propose a price change")
                page.set_viewport_size({"width":390,"height":844})
                page.screenshot(path=str(OUTPUT / "cards-mobile.png"), full_page=True)
                confirm_card.get_by_role("button", name="Confirm").click()
                expect(confirm_card).to_contain_text("Done")
                with SessionLocal() as db:
                    current = db.scalar(select(VariantChannelListing).where(
                        VariantChannelListing.variant_id == variant_id,
                        VariantChannelListing.channel_id == site.channel_id))
                    assert current.price_minor == initial_price + 2000

                history = page.request.get(f"/admin/sites/{site_id}/editor/agent/history").json()
                conversation_id = history["conversation_id"]
                with SessionLocal() as db:
                    for index in range(18):
                        db.add_all([
                            AgentMessage(tenant_id=site.tenant_id, site_id=site.id,
                                conversation_id=conversation_id, role="user", content=f"Earlier request {index + 1}"),
                            AgentMessage(tenant_id=site.tenant_id, site_id=site.id,
                                conversation_id=conversation_id, role="assistant", content=f"Earlier response {index + 1}"),
                        ])
                    db.commit()
                page.reload()
                page.locator('[data-mode="assistant"]').click()
                page.locator(".agent-messages").evaluate("el => el.scrollTop = el.scrollHeight")
                page.screenshot(path=str(OUTPUT / "pinned-composer.png"), full_page=True)
                metrics = page.evaluate("""() => {
                    const panel = document.querySelector('.agent-panel');
                    const messages = document.querySelector('.agent-messages');
                    const form = document.querySelector('#agent-form');
                    const panelRect = panel.getBoundingClientRect();
                    const formRect = form.getBoundingClientRect();
                    return {overflowing: messages.scrollHeight > messages.clientHeight,
                        composerBottom: formRect.bottom, panelBottom: panelRect.bottom};
                }""")
                assert metrics["overflowing"], metrics
                assert abs(metrics["composerBottom"] - metrics["panelBottom"]) < 4, metrics
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()
    print(json.dumps({"status":"passed","screenshots":str(OUTPUT)}))


if __name__ == "__main__":
    main()

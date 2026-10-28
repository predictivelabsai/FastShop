"""Check upstream widgets, Liquid consent, hotspots and merchant navigation locally."""

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

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/h24you-sync"
KINDS = ("section_heading", "tabs", "announcement_bar", "pull_quote", "image_hotspots", "recently_viewed")


def fixtures():
    from sqlalchemy import select

    from app import content
    from app.db import SessionLocal
    from app.models import Site

    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
        site.status = "published"
        page = content.create_page(
            db,
            site,
            "Upstream feature check",
            "/pages/upstream-feature-check",
            document={"version": 1, "title": "Upstream feature check", "blocks": []},
        )
        db.commit()
        return site.id, page.id


def capture(page, name, *, full_page=False):
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name
    page.screenshot(path=str(OUTPUT / f"{name}.png"), full_page=full_page)


def add_widget(page, frame, kind):
    page.locator("[data-mode='widgets']").click()
    search = page.get_by_role("searchbox", name="Search widgets")
    search.fill(kind.replace("_", " "))
    tile = page.locator(f'[data-widget-type="{kind}"]')
    expect(tile).to_be_visible()
    tile.click()
    expect(frame.locator(f'[data-block-type="{kind}"]')).to_be_visible()


def journey(browser, base, site_id, page_id):
    context = browser.new_context(base_url=base, viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    errors = []
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    expect(page.locator(".a-shell")).to_be_visible()
    page.goto(f"/admin/sites/{site_id}/editor?page={page_id}")
    frame = page.locator("#editor-canvas").content_frame
    expect(frame.locator(".fs-editor-hint")).to_be_attached()
    for kind in KINDS[:4]:
        add_widget(page, frame, kind)
    add_widget(page, frame, "image_hotspots")
    page.locator("[data-setting='image']").fill("/static/img/storefront-object.jpg")
    preview = page.locator("[data-hotspot-stage] img")
    expect(preview).to_be_visible()
    image_bounds = preview.bounding_box()
    assert image_bounds
    preview.click(position={"x": image_bounds["width"] * 0.62, "y": image_bounds["height"] * 0.38})
    expect(page.locator('[data-setting="items.0.x"]')).to_have_value("62")
    expect(page.locator('[data-setting="items.0.y"]')).to_have_value("38")
    add_widget(page, frame, "recently_viewed")
    page.locator("#save").click()
    expect(page.locator("#save-status")).to_contain_text("Saved")
    page.reload()
    expect(frame.locator("[data-block-type]")).to_have_count(len(KINDS))
    capture(page, "editor-desktop")
    page.locator("#publish").click()
    page.locator("#confirm-publish").click()
    expect(page.locator("#save-status")).to_contain_text("Published")
    consent_key = f"fastshop-consent:{site_id}"
    recent_key = f"fastshop:recently-viewed:{site_id}"
    live_url = "/sites/h24you-liquid/pages/upstream-feature-check"
    storefront = context.new_page()
    storefront.on("pageerror", lambda error: errors.append(str(error)))
    storefront.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    storefront.goto(live_url)
    expect(storefront.locator("main .fs-widget-section_heading")).to_be_visible()
    expect(storefront.locator("main .fs-pack2-tab-list")).to_be_visible()
    expect(storefront.locator("main [data-announcement]")).to_be_visible()
    expect(storefront.locator("main .fs-widget-pull_quote")).to_be_visible()
    expect(storefront.locator("main .fs-pack-hotspot-stage")).to_be_visible()
    expect(storefront.locator("[data-recently-viewed]")).to_be_hidden()
    expect(storefront.locator("#h-cookie")).to_be_visible()
    for script in ("widgets-pack1.js", "widgets-pack2.js", "widgets-pack3.js", "widgets-pack4.js", "site-liquid-consent.js"):
        expect(storefront.locator(f'script[src*="{script}"]')).to_have_count(1)
    capture(storefront, "storefront-consent", full_page=True)
    storefront.set_viewport_size({"width": 390, "height": 844})
    capture(storefront, "storefront-mobile", full_page=True)
    storefront.set_viewport_size({"width": 1440, "height": 900})
    banner = storefront.locator("#h-cookie")
    banner.locator("button[data-consent='all']").click()
    expect(banner).to_be_hidden()
    expect(storefront.locator("[data-recently-viewed]")).to_be_hidden()
    assert storefront.evaluate("key => JSON.parse(localStorage.getItem(key)).preferences", consent_key) is True

    storefront.goto("/sites/h24you-liquid/products/hydrogen-tablets")
    assert storefront.evaluate("key => JSON.parse(localStorage.getItem(key))", recent_key), storefront.evaluate(
        """site => ({consent: window.fastshopConsent, markers: [...document.querySelectorAll('[data-recent-product]')]
        .map(node => ({product: node.dataset.recentProduct, site: node.dataset.siteId})),
        scripts: [...document.scripts].map(script => script.src).filter(src => src.includes('widgets-pack4')),
        stored: localStorage.getItem(`fastshop-consent:${site}`)})""",
        site_id,
    )
    storefront.goto(live_url)
    recent = storefront.locator("[data-recently-viewed]")
    expect(recent).to_be_visible()
    expect(recent).to_contain_text("Fizz")
    storefront.locator("[data-cookie-open]").click()
    banner.locator("details").evaluate("element => { element.open = true; }")
    storefront.locator("#consent-preferences").uncheck()
    banner.locator("button[data-consent='custom']").click()
    expect(recent).to_be_hidden()
    assert storefront.evaluate("key => localStorage.getItem(key)", recent_key) is None
    storefront.locator("[data-cookie-open]").click()
    banner.locator("button[data-consent='none']").click()
    assert storefront.evaluate("key => JSON.parse(localStorage.getItem(key)).preferences", consent_key) is False
    capture(storefront, "storefront-consent-declined", full_page=True)

    admin = context.new_page()
    admin.goto("/admin/sites")
    expect(admin.locator(".a-shell")).to_be_visible()
    entry_routes = {
        "/admin/products": "/products",
        "/admin/orders": "/orders",
        "/admin/customers": "/customers",
        "/admin/inventory": "/inventory",
        "/admin/promotions": "/discounts",
        "/admin/content": "/content/pages",
        "/admin/channels": "/settings/channels",
        "/admin/integrations": "/settings/integrations",
        "/admin/assistant": "/assistant",
        "/developers": "/settings/developers",
    }
    for entry, destination in entry_routes.items():
        admin.goto(entry)
        expect(admin).to_have_url(re.compile(
            rf"{re.escape(base)}/admin/sites/[^/]+{re.escape(destination)}(?:\?.*)?$"
        ))
        expect(admin.locator(".a-shell")).to_be_visible()
    admin.goto("/admin/products")
    capture(admin, "merchant-entry-products")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"/admin/sites/{site_id}/editor?page={page_id}")
    capture(page, "editor-mobile")
    assert not errors, errors
    context.close()
    return {
        "status": "passed",
        "widgets": list(KINDS),
        "merchant_entry_routes": len(entry_routes),
        "browser_errors": errors,
        "screenshots": 6,
    }


def main():
    import uvicorn

    sys.path.insert(0, str(ROOT))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="h24you-sync-browser-") as data_dir:
        os.environ.pop("FASTSHOP_BROWSER_DB_URL", None)
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated browser server failed to start")
                thread.join(0.05)
            site_id, page_id = fixtures()
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Error:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                try:
                    result = journey(browser, f"http://127.0.0.1:{port}", site_id, page_id)
                finally:
                    browser.close()
            print(json.dumps(result), flush=True)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()
            assert not thread.is_alive(), "Browser server did not stop cleanly"


if __name__ == "__main__":
    main()

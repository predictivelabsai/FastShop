"""Isolated guided-builder desktop/mobile acceptance; no external calls."""

import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from app.config import settings


def main():
    if settings.db_url or not str(settings.data_dir).startswith("/tmp/fastshop-") or settings.public_url != "http://127.0.0.1:5044" or settings.xai_api_key:
        raise SystemExit("Requires an isolated /tmp/fastshop-* DB, empty DB_URL/XAI_API_KEY and public URL http://127.0.0.1:5044")
    from app.content import create_site
    from app.db import SessionLocal
    from app.main import app
    from app.models import User
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = create_site(db, owner.id, "Everyday Studio", "everyday-studio")
        db.commit()
        site_id = site.id
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=5044, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("Verifier server failed to start")
    output = Path("output/playwright/dual-flow")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(settings.public_url + "/") else route.abort())
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(settings.public_url + "/login")
            page.locator('[name=email]').fill(settings.admin_email)
            page.locator('[name=password]').fill(settings.admin_password)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.goto(settings.public_url + f"/admin/sites/{site_id}/samples")
            page.get_by_role("button", name="Fill missing fields with sample data", exact=True).click()
            expect(page.locator('[name=company]')).to_have_value("Example Studio — DEMO")
            page.locator('[name=company]').fill("Everyday Studio — sample merchant")
            page.locator('[name=shipping_minor]').fill("1400")
            page.locator('[name=reviewed][value=shipping_minor]').check()
            page.get_by_role("button", name="Save merchant details", exact=True).click()
            expect(page.locator('[name=shipping_minor]')).to_have_value("1400")
            page.screenshot(path=str(output / "desktop-merchant-details.png"))
            page.goto(settings.public_url + f"/admin/sites/{site_id}/build")
            expect(page.get_by_text("Guided presets · AI provider not configured")).to_be_visible()
            page.get_by_role("button", name="Warm", exact=True).click()
            expect(page.get_by_text("Applied the warm preset to your draft.")).to_be_visible()
            preview = page.frame_locator('.b-preview')
            expect(preview.locator('.h-site')).to_have_attribute('data-themed', 'true')
            page.locator('.b-preview').scroll_into_view_if_needed()
            preview.locator('[data-consent=none]').click()
            page.get_by_role("button", name="Select section", exact=True).click()
            preview.locator('[data-builder-section]').first.click()
            expect(page.locator('[name=section_id]')).not_to_have_value("")
            selected_section = page.locator('[name=section_id]').input_value()
            page.evaluate("window.scrollTo(0, 0)")
            page.locator('[name=prompt]').fill("Headline: A quieter everyday")
            page.get_by_role("button", name="Update draft", exact=True).click()
            expect(preview.get_by_role("heading", name="A quieter everyday", exact=True)).to_be_visible()
            expect(page.locator('[name=section_id]')).to_have_value(selected_section)
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(output / "desktop-chat-preview.png"))
            page.get_by_role("link", name="Design controls", exact=True).click()
            expect(page.locator('[name=accent]')).to_have_value('#26543d')
            page.locator('[name=accent]').fill('#654321')
            page.get_by_role("button", name="Save design", exact=True).click()
            expect(page.locator('[name=accent]')).to_have_value('#654321')
            page.screenshot(path=str(output / "desktop-classical-design.png"))
            page.get_by_role("button", name="Undo latest change", exact=True).click()
            page.get_by_role("link", name="Design controls", exact=True).click()
            expect(page.locator('[name=accent]')).to_have_value('#26543d')
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.get_by_role("link", name="Chat", exact=True).click()
            page.screenshot(path=str(output / "mobile-chat.png"))
            page.get_by_role("button", name="Preview", exact=True).click()
            expect(page.locator('.b-preview')).to_be_visible()
            page.locator('.b-canvas').scroll_into_view_if_needed()
            page.screenshot(path=str(output / "mobile-preview.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.goto(settings.public_url + f"/admin/sites/{site_id}/samples")
            page.screenshot(path=str(output / "mobile-merchant-details.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            from scripts.verify_demo_browser import verify_demo_flow
            verify_demo_flow(page, site_id, output)
            page.goto(settings.public_url + f"/admin/sites/{site_id}/build")
            page.locator('[name=prompt]').fill("Business: Independent tea studio")
            page.get_by_role("button", name="Update draft", exact=True).click()
            expect(page.locator('.b-brief')).to_contain_text("Independent tea studio")
            page.locator('[name=prompt]').fill("Audience: US tea drinkers")
            page.get_by_role("button", name="Update draft", exact=True).click()
            expect(page.locator('.b-brief')).to_contain_text("US tea drinkers")
            page.locator('[name=prompt]').fill("Shipping: 1600")
            page.get_by_role("button", name="Update draft", exact=True).click()
            expect(page.get_by_role("heading", name="Review merchant changes", exact=True)).to_be_visible()
            from app.commerce import settings_for
            from app.models import Site
            with SessionLocal() as db:
                assert settings_for(db, db.get(Site, site_id)).shipping_minor == 1400
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.locator('.b-review').scroll_into_view_if_needed()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(output / f"{device}-merchant-review.png"))
            page.get_by_label("I reviewed every proposed change").check()
            page.get_by_role("button", name="Approve merchant changes", exact=True).click()
            expect(page.get_by_text("Merchant proposal approved", exact=True)).to_be_visible()
            with SessionLocal() as db:
                assert settings_for(db, db.get(Site, site_id)).shipping_minor == 1600
            browser.close()
        assert not errors, errors
        print({"status": "passed", "mode": "guided presets, not real LLM", "screenshots": str(output), "shared_draft_undo": True})
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()

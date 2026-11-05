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
        from app.db import SessionLocal, engine
        from app.main import app
        from app import content
        from app.models import Site
        from sqlalchemy import select
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
            home = next(page for page in content.site_pages(db, site) if page.path == "/")
            site_id, page_id = site.id, home.id
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
            log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
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
                context = browser.new_context(base_url=f"http://127.0.0.1:{port}", viewport={"width":1440,"height":900}, reduced_motion="reduce")
                page = context.new_page(); page.goto(f"http://127.0.0.1:{port}/login?next=/admin/sites")
                page.get_by_label("Email address", exact=True).fill(EMAIL)
                page.get_by_label("Password", exact=True).fill(PASSWORD)
                page.get_by_role("button", name="Sign in", exact=True).click(); page.wait_for_url("**/admin/sites")
                page.goto(f"/admin/sites/{site_id}/editor?page={page_id}")
                page.locator('[data-mode="assistant"]').click()
                page.locator("#agent-input").fill("Change the selected headline")
                page.locator("#agent-form").press("Enter")
                expect(page.locator(".agent-message.assistant")).to_contain_text("updated")
                frame = page.locator("#editor-canvas").content_frame
                expect(frame.locator("body")).to_contain_text("Assistant preview update")
                page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)
                restore = page.locator("[data-restore-message]").last
                expect(restore).to_be_visible()
                restore.click(); page.get_by_role("button", name="Restore draft").click()
                page.wait_for_timeout(300)
                page.locator("#save").click()
                if page.locator("#conflict-dialog").is_visible():
                    page.locator("#conflict-reload").click()
                expect(page.locator("#save-status")).to_contain_text("Saved")
                page.set_viewport_size({"width":390,"height":844})
                page.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
                browser.close()
        finally:
            server.should_exit = True; thread.join(timeout=10); engine.dispose()
    print(json.dumps({"status":"passed","screenshots":str(OUTPUT)}))


if __name__ == "__main__":
    main()

"""Capture store deletion screens against a disposable local database."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate, seed_order

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/delete-store"


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-delete-store-") as data_dir:
        os.environ["FASTSHOP_BROWSER_DB_URL"] = ""
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id = seed_order()
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
                    raise RuntimeError("Local verification server failed to start")
                thread.join(0.05)
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Error:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                    context = browser.new_context(
                        base_url=f"http://127.0.0.1:{port}",
                        viewport={"width": width, "height": height},
                        reduced_motion="reduce",
                    )
                    page = context.new_page()
                    page.goto("/login?next=/admin/sites")
                    page.get_by_label("Email address", exact=True).fill(EMAIL)
                    page.get_by_label("Password", exact=True).fill(PASSWORD)
                    page.get_by_role("button", name="Sign in", exact=True).click()
                    page.wait_for_url("**/admin/sites")

                    page.goto(f"/admin/sites/{site_id}/settings/general")
                    danger = page.locator(".danger-zone")
                    expect(danger).to_contain_text("Danger zone")
                    danger.scroll_into_view_if_needed()
                    danger.screenshot(path=str(OUTPUT / f"{device}-danger-zone.png"))

                    page.goto(f"/admin/sites/{site_id}/settings/delete")
                    confirmation = page.locator("[data-delete-confirmation]")
                    expect(confirmation.locator('input[name="confirmation"]')).to_be_visible()
                    confirmation.screenshot(path=str(OUTPUT / f"{device}-confirmation.png"))
                    context.close()
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            engine.dispose()

    print(f"Captured deletion screens in {OUTPUT}")


if __name__ == "__main__":
    main()

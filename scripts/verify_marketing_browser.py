"""Capture the marketing hub at desktop and mobile admin viewport sizes."""

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
OUTPUT = ROOT / "output/playwright/marketing"


def login(page, path: str) -> None:
    page.goto(f"/login?next={path}")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url(f"**{path}")


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    os.environ["FASTSHOP_BROWSER_DB_URL"] = ""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-marketing-") as data_dir:
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
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        context = browser.new_context(
                            base_url=f"http://127.0.0.1:{port}",
                            viewport={"width": width, "height": height},
                        )
                        page = context.new_page()
                        page.set_default_timeout(15000)
                        login(page, "/admin/sites")
                        response = page.goto(f"/admin/sites/{site_id}/marketing")
                        assert response and response.status == 200
                        expect(page.get_by_role("heading", name="Marketing", exact=True)).to_be_visible()
                        for title in (
                            "Search engine optimisation",
                            "URL redirects",
                            "Tracking and pixels",
                            "Discounts",
                            "Email subscribers",
                        ):
                            expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                        page.add_style_tag(content="[data-agent-launcher]{display:none!important}")
                        page.evaluate("document.fonts.ready.then(() => true)")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                        page.screenshot(path=str(OUTPUT / f"{device}.png"), full_page=True)
                        context.close()
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()
    print(f"Marketing hub screenshots saved to {OUTPUT}")


if __name__ == "__main__":
    main()

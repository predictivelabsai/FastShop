"""Capture store deletion screens against a disposable local database."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/delete-store"
EMAIL = "delete-store-browser@example.test"
PASSWORD = "LocalDelete2026$"


def isolate(data_dir: str, port: int) -> None:
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "DB_URL": "",
        "FASTSHOP_ENV": "development",
        "FASTSHOP_AUTO_CREATE_SCHEMA": "1",
        "FASTSHOP_DATA_DIR": data_dir,
        "FASTSHOP_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "FASTSHOP_HOST": "127.0.0.1",
        "FASTSHOP_PORT": str(port),
        "FASTSHOP_ADMIN_EMAIL": EMAIL,
        "FASTSHOP_ADMIN_PASSWORD": PASSWORD,
        "FASTSHOP_ADMIN_PASSWORD_HASH": "",
        "FASTSHOP_ALLOW_PASSWORD_LOGIN": "1",
        "FASTSHOP_ENABLE_SCHEDULER": "0",
        "FASTSHOP_IMAGE_PROVIDER": "placeholders",
        "FASTSHOP_SESSION_SECRET": uuid4().hex + uuid4().hex,
    })
    for key in (
        "XAI_API_KEY", "POSTMARK_API_TOKEN", "POSTMARK_SERVER_TOKEN",
        "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "FASTSME_API_TOKEN",
        "FASTERP_API_TOKEN", "FASTERP_BASE_URL", "FASTERP_COMPANY_ID",
        "FASTSHOP_CONNECTOR_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
        "FASTSHOP_BILLING_STRIPE_SECRET_KEY", "FASTSHOP_BILLING_STRIPE_WEBHOOK_SECRET",
        "FASTSHOP_ENCRYPTION_KEY", "FASTSHOP_STORE_KEY_ENCRYPTION_KEY",
        "PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "PAYPAL_MODE",
    ):
        os.environ[key] = ""


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-delete-store-") as data_dir:
        isolate(data_dir, port)
        from sqlalchemy import select

        from app.db import SessionLocal, engine
        from app.main import app
        from app.models import Site

        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == "h24you"))
            if not site:
                raise RuntimeError("The disposable H2 4 You site was not seeded")
            site_id = site.id

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
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                    context = browser.new_context(
                        base_url=f"http://127.0.0.1:{port}",
                        viewport={"width": width, "height": height},
                        reduced_motion="reduce",
                    )
                    page = context.new_page()
                    page.goto("/login")
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

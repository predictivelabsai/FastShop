"""Capture the merchant PayPal and email connection pages on desktop and mobile."""

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
OUTPUT = ROOT / "output/playwright/integrations-connect"
EMAIL = "integrations-browser@example.test"
PASSWORD = "LocalIntegrationsBrowser2026$"


def prepare(port: int, data_dir: str) -> None:
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ.pop("FASTSHOP_PUBLIC_URL", None)
    os.environ.update({
        "DB_URL": "", "FASTSHOP_ENV": "development",
        "FASTSHOP_AUTO_CREATE_SCHEMA": "1", "FASTSHOP_DATA_DIR": data_dir,
        "FASTSHOP_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "FASTSHOP_SESSION_SECRET": uuid4().hex + uuid4().hex,
        "FASTSHOP_ADMIN_EMAIL": EMAIL, "FASTSHOP_ADMIN_PASSWORD": PASSWORD,
        "FASTSHOP_ADMIN_PASSWORD_HASH": "", "FASTSHOP_ALLOW_PASSWORD_LOGIN": "1",
        "FASTSHOP_ENABLE_SCHEDULER": "0", "FASTSHOP_IMAGE_PROVIDER": "placeholders",
        "XAI_API_KEY": "", "POSTMARK_API_TOKEN": "", "POSTMARK_SERVER_TOKEN": "",
        "STRIPE_SECRET_KEY": "", "STRIPE_WEBHOOK_SECRET": "",
        "STRIPE_PUBLISHABLE_KEY": "", "PAYPAL_CLIENT_ID": "", "PAYPAL_SECRET": "",
        "PAYPAL_MODE": "",
    })
    for key in tuple(os.environ):
        if key.startswith("FASTSHOP_PAYPAL_"):
            os.environ[key] = ""


def seed_site() -> str:
    from sqlalchemy import select

    from app import content
    from app.auth import hash_admin_password
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == EMAIL))
        if not user:
            user = User(email=EMAIL, name="Browser merchant")
            db.add(user)
            db.flush()
        user.password_hash = hash_admin_password(PASSWORD)
        site = content.create_site(db, user.id, "Integration preview", "integration-preview")
        site_id = site.id
        db.commit()
        return site_id


def login(page, destination: str) -> None:
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    page.goto(destination)
    expect(page.locator(".a-shell")).to_be_visible()


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", 0))
        port = socket_probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-integrations-") as data_dir:
        prepare(port, data_dir)
        from app.db import engine
        from app.main import app

        try:
            site_id = seed_site()
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                log_level="error", access_log=False))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local integrations verification server failed to start")
                thread.join(0.05)
            errors: list[str] = []
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Exception:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                        context = browser.new_context(
                            base_url=f"http://127.0.0.1:{port}",
                            viewport={"width": width, "height": height},
                            reduced_motion="reduce")
                        page = context.new_page()
                        page.on("pageerror", lambda exc: errors.append(str(exc)))
                        login(page, f"/admin/sites/{site_id}/settings/integrations/paypal")
                        for name, path in (
                            ("paypal", f"/admin/sites/{site_id}/settings/integrations/paypal"),
                            ("email", f"/admin/sites/{site_id}/settings/integrations/email"),
                        ):
                            response = page.goto(path)
                            assert response and response.status == 200, path
                            expect(page.get_by_role("heading", name="Connection details")).to_be_visible()
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth + 1")
                            page.screenshot(path=str(OUTPUT / f"{device}-{name}.png"),
                                            full_page=True)
                        context.close()
                finally:
                    browser.close()
            assert not errors, errors
            print("Saved PayPal and email screenshots for desktop and mobile to "
                  + str(OUTPUT))
        finally:
            if "server" in locals():
                server.should_exit = True
                thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

"""Capture campaign, confirmation, automation and subscriber admin pages."""

from __future__ import annotations

import secrets
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fasthtml.common import Input, fast_app
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "playwright" / "campaigns"


def main():
    import sys

    sys.path.insert(0, str(ROOT))
    from app import (
        content,
        marketing_automation_routes,
        marketing_campaign_routes,
        marketing_email,
        marketing_subscriber_routes,
    )
    from app.admin_ui import AdminContextMiddleware, admin_page, admin_scope
    from app.models import Base, MarketingSubscriber, User
    from app.services import CommerceError

    OUTPUT.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="campaign-browser@example.test", name="Store owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Campaign preview store", "campaign-" + uuid4().hex[:8])
        site.settings_json = {**site.settings_json, "address": "15 Market Lane, Bristol"}
        site.published_settings_json = {**site.published_settings_json,
                                       "address": "15 Market Lane, Bristol",
                                       "timezone": "Europe/London"}
        db.add(MarketingSubscriber(
            tenant_id=site.tenant_id, site_id=site.id, email="reader@example.test",
            status="subscribed", source="footer", consent_evidence="I agree to receive emails.",
            unsubscribe_token=secrets.token_urlsafe(32),
        ))
        db.flush()
        campaign = marketing_email.create_campaign(db, site, owner.id, {
            "name": "Spring studio notes", "subject": "A note from the studio",
            "preheader": "New work and a date for your diary",
            "body_html": (
                "<p>Hello,</p><p>We have added new work to the shop and would love you to take a look.</p>"
                "<p><a href=\"https://example.test/collection\">Explore the collection</a></p>"
            ),
        })
        campaign_id = campaign.id
        db.commit()

    state = SimpleNamespace(user_id=owner.id)

    def actor(_session):
        return state.user_id

    def csrf(session):
        session.setdefault("csrf_token", secrets.token_urlsafe(24))
        return Input(type="hidden", name="csrf_token", value=session["csrf_token"])

    def check_csrf(session, form):
        if not session.get("csrf_token") or not secrets.compare_digest(
            session["csrf_token"], str(form.get("csrf_token", "")),
        ):
            raise CommerceError("Your session expired. Reload and try again.")

    def shell(title, *children):
        scope = admin_scope.get() or {}
        return admin_page(title, *children, site=site, sites=[site], user=owner,
                          path=scope.get("path", ""))

    def error(exc):
        return Response(str(exc), status_code=400)

    app, rt = fast_app(secret_key="marketing-campaign-browser-only")
    app.add_middleware(AdminContextMiddleware)
    marketing_campaign_routes.SessionLocal = sessions
    marketing_automation_routes.SessionLocal = sessions
    marketing_subscriber_routes.SessionLocal = sessions
    marketing_campaign_routes.register_marketing_campaign_routes(rt, actor, csrf, check_csrf, shell, error)
    marketing_automation_routes.register_marketing_automation_routes(rt, actor, csrf, check_csrf, shell, error)
    marketing_subscriber_routes.register_marketing_subscriber_routes(rt, actor, csrf, check_csrf, shell, error)

    pages = {}
    base = f"/admin/sites/{site.id}/marketing"
    with TestClient(app, follow_redirects=False) as client:
        routes = {
            "campaign-editor": (base + f"/campaigns/{campaign_id}", "Email campaign"),
            "send-confirm": (base + f"/campaigns/{campaign_id}/send-confirm", "Confirm campaign send"),
            "automations": (base + "/automations", "Abandoned checkout emails"),
            "subscribers": (base + "/subscribers", "Email subscribers"),
        }
        for key, (path, _label) in routes.items():
            response = client.get(path)
            assert response.status_code == 200, f"{key} page returned {response.status_code}"
            pages[key] = response.text

    class CaptureHandler(SimpleHTTPRequestHandler):
        captured = pages

        def do_GET(self):
            key = self.path.removeprefix("/capture/").split("?", 1)[0]
            if key in self.captured:
                body = self.captured[key].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, _format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(CaptureHandler, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
            except Exception:
                browser = playwright.chromium.launch(headless=True)
            try:
                viewports = (("desktop", 1440, 1000), ("mobile", 390, 844))
                for device, width, height in viewports:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    for key, (_path, label) in routes.items():
                        response = page.goto(origin + "/capture/" + key, wait_until="networkidle")
                        assert response and response.status == 200
                        expect(page.get_by_role("heading", name=label, exact=False).first).to_be_visible()
                        page.evaluate("document.fonts.ready")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), key
                        page.screenshot(
                            path=str(OUTPUT / f"{key}-{device}.png"), full_page=True,
                        )
                    page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        engine.dispose()
    print(f"Captured {len(routes) * 2} marketing screenshots in {OUTPUT}")


if __name__ == "__main__":
    main()

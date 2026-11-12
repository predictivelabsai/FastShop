"""Capture subscriber admin and storefront states for desktop and mobile review."""

from __future__ import annotations

import re
import secrets
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from fasthtml.common import Input, fast_app
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    content,
    customer_routes,
    marketing_subscriber_routes,
    marketing_subscribers,
    site_ui,
)
from app.admin_ui import AdminContextMiddleware, admin_page, admin_scope  # noqa: E402
from app.models import Base, Site, SitePage, User  # noqa: E402
from app.services import CommerceError  # noqa: E402

OUTPUT = ROOT / "output" / "playwright" / "subscribers"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="subscriber-browser@example.test", name="Store owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Newsletter store", "newsletter-store")
        home = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
            SitePage.path == "/",
        ))
        if not home:
            home = content.create_page(
                db, site, "Newsletter store", "/", kind="home",
                document={"title": "Newsletter store", "sections": []},
            )
        content.save_page(db, site, home.id, owner.id, home.draft_json,
                          home.version, action="publish")
        site.status = "published"
        site.settings_json = {
            **site.settings_json,
            "generic_labels": {
                **(site.settings_json or {}).get("generic_labels", {}),
                "newsletter_title": "Letters from the studio",
                "newsletter_body": "A short note about new work, once a month.",
                "newsletter_success": "Thanks for signing up. Check your email to confirm.",
                "newsletter_error": "Please check your email and consent, then try again.",
                "newsletter_unsubscribe_title": "Unsubscribe from marketing emails",
                "newsletter_unsubscribe_instruction": "Confirm below to stop marketing emails from this store.",
                "newsletter_unsubscribe_cta": "Unsubscribe",
                "newsletter_unsubscribed": "You will no longer receive marketing emails from this store.",
            },
        }
        site.published_settings_json = site.settings_json
        unsubscribable, _ = marketing_subscribers.upsert_subscriber(
            db, site, "reader@example.test", source="footer", status="subscribed",
            evidence="I agree to receive marketing emails from Newsletter store.",
        )
        unsubscribe_token = unsubscribable.unsubscribe_token
        db.commit()

    state = SimpleNamespace(user_id=owner.id)

    def actor(_session):
        return state.user_id

    def csrf(session):
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        return Input(type="hidden", name="csrf_token", value=session["csrf_token"])

    def check_csrf(session, form):
        if not session.get("csrf_token") or not secrets.compare_digest(
            session["csrf_token"], str(form.get("csrf_token", "")),
        ):
            raise CommerceError("Your session expired. Reload and try again.")

    def shell_page(title, *children):
        scope = admin_scope.get() or {}
        return admin_page(title, *children, site=site, sites=[site], user=owner,
                          path=scope.get("path", ""))

    def error(exc):
        return Response(str(exc), status_code=400)

    app, rt = fast_app(secret_key="marketing-subscriber-browser-test-only")
    app.add_middleware(AdminContextMiddleware)
    customer_routes.SessionLocal = sessions
    from app import storefront_ui

    storefront_ui.SessionLocal = sessions
    customer_routes.dispatch_mail = lambda _message_id: False
    marketing_subscriber_routes.SessionLocal = sessions
    customer_routes.register_customer_routes(rt, actor, csrf, check_csrf, shell_page, error)
    marketing_subscriber_routes.register_marketing_subscriber_routes(
        rt, actor, csrf, check_csrf, shell_page, error,
    )

    @rt("/sites/{slug}/", methods=["GET"])
    def home_page(session, request, slug: str):
        with sessions() as db:
            current_site = db.scalar(select(Site).where(Site.slug == slug))
            current_home = db.scalar(select(SitePage).where(
                SitePage.site_id == current_site.id, SitePage.tenant_id == current_site.tenant_id,
                SitePage.path == "/",
            ))
            csrf(session)
            base = "/sites/" + current_site.slug
            return site_ui.storefront(
                db, current_site, current_home, base, session["csrf_token"],
                "https://example.test" + base + "/",
                newsletter_result=request.query_params.get("newsletter", ""),
            )

    captured = {}
    with TestClient(app) as client:
        admin_page_response = client.get(f"/admin/sites/{site.id}/marketing/subscribers")
        assert admin_page_response.status_code == 200
        captured["admin"] = admin_page_response.text

        storefront = client.get(f"/sites/{site.slug}/")
        token = re.search(r'name="csrf_token" value="([^"]+)"', storefront.text).group(1)
        signup = client.post(f"/sites/{site.slug}/newsletter", data={
            "csrf_token": token,
            "email": "new-reader@example.test",
            "marketing_consent": "on",
            "newsletter_source": "footer",
        })
        assert signup.status_code == 200 and "Thanks for signing up." in signup.text
        captured["footer-success"] = signup.text

        unsubscribe = client.get(
            f"/sites/{site.slug}/unsubscribe?token={unsubscribe_token}",
        )
        assert unsubscribe.status_code == 200
        captured["unsubscribe"] = unsubscribe.text

    class CaptureHandler(SimpleHTTPRequestHandler):
        pages = captured

        def do_GET(self):
            key = self.path.removeprefix("/capture/").split("?", 1)[0]
            if key in self.pages:
                body = self.pages[key].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, _format, *args):
            return

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(CaptureHandler, directory=str(ROOT)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                page = browser.new_page(viewport={"width": width, "height": height})
                for key, label in (("admin", "Email subscribers"),
                                   ("footer-success", "Thanks for signing up."),
                                   ("unsubscribe", "Confirm below to stop marketing emails")):
                    response = page.goto(origin + "/capture/" + key, wait_until="networkidle")
                    assert response and response.status == 200
                    expect(page.get_by_text(label, exact=False).first).to_be_visible()
                    consent = page.locator('[data-consent="all"]')
                    if consent.is_visible():
                        consent.click()
                    if key == "footer-success":
                        newsletter = page.locator("#footer.h-footer-newsletter")
                        newsletter.scroll_into_view_if_needed()
                        expect(newsletter.get_by_text("Thanks for signing up.")).to_be_visible()
                        newsletter.screenshot(path=str(OUTPUT / f"{key}-{device}.png"))
                    else:
                        page.screenshot(
                            path=str(OUTPUT / f"{key}-{device}.png"),
                            full_page=key == "admin",
                        )
                        if key == "admin":
                            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
                                f"Horizontal overflow on admin at {device} width."
                            )
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        engine.dispose()


if __name__ == "__main__":
    main()

"""Default merchant entry acceptance on fresh SQLite, with no saved browser state.

Run after shell, routing and HTTP-test integration:
    python scripts/verify_admin_v2_entry_browser.py
Every emitted PNG must be visually inspected before accepting the results.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import verify_admin_v2_browser as foundation
from playwright.sync_api import Error, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/admin-v2/entry-points"
PASSWORD = "LocalEntryBrowser2026$LongPassword"


@contextmanager
def disposable_storage():
    # Inherit workspace ACLs. Python 3.14's mode-0700 temporary directories
    # cannot be reopened by the restricted Windows execution token.
    directory = OUTPUT / ("disposable-" + uuid4().hex)
    directory.mkdir(mode=0o755)
    try:
        yield str(directory)
    finally:
        assert directory.resolve().parent == OUTPUT.resolve()
        shutil.rmtree(directory)


def fixtures():
    from app import content
    from app.auth import hash_admin_password
    from app.db import SessionLocal
    from app.models import Membership, Tenant, User

    result = {}
    with SessionLocal() as db:
        for count in (0, 1, 2):
            email = f"entry-{count}@example.test"
            user = User(email=email, name="Browser merchant",
                        password_hash=hash_admin_password(PASSWORD),
                        email_verified_at=datetime.now(UTC))
            db.add(user)
            db.flush()
            if count == 0:
                tenant = Tenant(name="Browser empty workspace", slug="entry-empty")
                db.add(tenant)
                db.flush()
                db.add(Membership(tenant_id=tenant.id, user_id=user.id, role="admin"))
            sites = [content.create_site(db, user.id, f"Browser store {index + 1}",
                     f"entry-{count}-{index}") for index in range(count)]
            result[count] = {"email": email, "sites": [site.id for site in sites]}
        db.commit()
    return result


def observe(context, errors):
    def watch(page):
        page.on("pageerror", lambda error: errors.append(f"page: {page.url}: {error}"))
        page.on("console", lambda message: errors.append(
            f"console: {page.url}: {message.text}") if message.type == "error" else None)
        page.on("response", lambda response: errors.append(
            f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
        page.on("requestfailed", lambda request: errors.append(
            f"request: {request.url}: {request.failure}"))
    context.on("page", watch)


def capture(page, device, name, screenshots):
    expect(page.locator(".a-shell")).to_be_visible()
    expect(page.get_by_role("main")).to_be_visible()
    page.evaluate("document.fonts.ready")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name
    path = OUTPUT / f"{device}-{name}.png"
    page.screenshot(path=str(path), full_page=True)
    screenshots.append(path.name)


def sidebar(page, device):
    if device == "mobile":
        toggle = page.get_by_role("button", name="Open navigation", exact=True)
        if toggle.get_attribute("aria-expanded") != "true":
            toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
    return page.get_by_role("complementary", name="Store administration")


def click_section(page, device, label, destination):
    sidebar(page, device).get_by_role("link", name=label, exact=True).click()
    page.wait_for_url("**" + destination)
    expect(page.locator(".a-shell")).to_be_visible()
    expect(page.get_by_role("complementary", name="Store administration", include_hidden=True)
           .get_by_role("link", name=label, exact=True, include_hidden=True)
           ).to_have_attribute("aria-current", "page")


def crawl(page, seeds):
    """Recursively follow rendered shell anchors, including expanded subnavigation.

    Only merchant GET links are queued. Forms and external/public links are never
    submitted. Fragments are removed and query variants retained. A hard ceiling
    fails an unexpectedly unbounded graph rather than silently truncating it.
    """
    pending = deque(seeds)
    visited = set()
    base = page.url
    origin = urlsplit(base).netloc
    while pending:
        target = pending.popleft()
        if target in visited:
            continue
        assert len(visited) < 300, "Rendered shell graph exceeded 300 URLs"
        probe = page.request.get(target)
        assert probe.status == 200, (target, probe.status)
        visited.add(target)
        if "attachment" in probe.headers.get("content-disposition", "").lower():
            probe.dispose()
            continue
        probe.dispose()
        response = page.goto(target)
        assert response is not None and response.status == 200, (
            target, response.status if response else "no response")
        assert urlsplit(page.url).path != "/login", target
        if "/preview/" in urlsplit(page.url).path:
            # Storefront previews intentionally render the public document.
            expect(page.get_by_role("main")).to_be_visible()
            continue
        expect(page.locator(".a-shell")).to_be_visible()
        # The shell owns navigation; main-content links include store pickers,
        # Settings directories and utility pages which must also remain usable.
        links = page.locator(".a-shell a[href]").evaluate_all(
            "nodes => nodes.map(node => node.getAttribute('href'))")
        for href in links:
            parsed = urlsplit(urljoin(page.url, href))
            if parsed.netloc != origin or not (
                parsed.path == "/developers" or parsed.path.startswith("/admin/")):
                continue
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            assert "/coming-soon/" not in path, path
            if path not in visited:
                pending.append(path)
    return sorted(visited)


def journey(browser, base, fixture, device, viewport, errors, screenshots):
    results = {}
    for count in (0, 1, 2):
        context = browser.new_context(base_url=base, viewport=viewport, reduced_motion="reduce")
        observe(context, errors)
        page = context.new_page()
        page.set_default_timeout(15000)
        sites = fixture[count]["sites"]
        destination = f"/admin/sites/{sites[0]}" if count == 1 else "/admin/sites"
        try:
            foundation.login(page, destination, fixture[count]["email"], PASSWORD)
            capture(page, device, f"default-{count}-stores", screenshots)
            if count == 0:
                expect(page.get_by_role("heading", name="Create your first store", exact=True)
                       ).to_be_visible()
                expect(page.get_by_role("button", name="Create site", exact=True)).to_be_visible()
            if count == 1:
                prefix = destination
                for label, suffix in (("Products", "/products"), ("Orders", "/orders"),
                                      ("Content", "/content/pages"), ("Settings", "/settings"),
                                      ("Assistant", "/assistant"), ("Home", "")):
                    click_section(page, device, label, prefix + suffix)
                capture(page, device, "sidebar-home", screenshots)
                if device == "mobile":
                    toggle = page.get_by_role("button", name="Open navigation", exact=True)
                    sidebar(page, device)
                    capture(page, device, "navigation-open", screenshots)
                    page.keyboard.press("Escape")
                    expect(toggle).to_have_attribute("aria-expanded", "false")
            if count == 2:
                page.get_by_role("main").get_by_role("link", name="Browser store 1", exact=True).click()
                page.wait_for_url(f"**/admin/sites/{sites[0]}")
                side = sidebar(page, device)
                side.locator(".a-store-switcher > summary").click()
                switcher = side.get_by_role("navigation", name="Switch store")
                capture(page, device, "store-switcher", screenshots)
                switcher.get_by_role("link", name="Browser store 2", exact=True).click()
                page.wait_for_url(f"**/admin/sites/{sites[1]}")
                capture(page, device, "switched-home", screenshots)
                page.goto("/admin/products")
                page.wait_for_url(f"**/admin/sites/{sites[1]}/products")
                side = sidebar(page, device)
                side.locator(".a-store-switcher > summary").click()
                side.get_by_role("navigation", name="Switch store").get_by_role(
                    "link", name="All stores", exact=True).click()
                page.wait_for_url("**/admin/sites")
                results["crawled_links"] = crawl(page, ["/admin/sites"])
            results[f"default_{count}_stores"] = destination
        except BaseException:
            path = OUTPUT / f"{device}-{count}-failure.png"
            page.screenshot(path=str(path), full_page=True)
            screenshots.append(path.name)
            raise
        finally:
            context.close()
    return results


def main():
    import uvicorn

    sys.path.insert(0, str(ROOT))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # This entry verifier must never inherit the optional external database
    # override supported by older reusable release journeys.
    os.environ.pop("FASTSHOP_BROWSER_DB_URL", None)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    assert port != 5025, "Port 5025 is reserved"
    errors, screenshots, results, server_errors = [], [], {}, []
    with disposable_storage() as data_dir:
        foundation.isolate(data_dir, port)
        os.environ["DB_URL"] = "sqlite:///" + (Path(data_dir) / "entry.sqlite3").as_posix()
        os.environ["FASTSHOP_AUTO_CREATE_SCHEMA"] = "1"
        from app.db import engine
        from app.main import app

        assert engine.dialect.name == "sqlite"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))

        def serve():
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(lambda _, context: server_errors.append(
                str(context.get("exception") or context.get("message"))))
            try:
                loop.run_until_complete(server.serve())
            finally:
                loop.close()

        thread = threading.Thread(target=serve, daemon=True)
        status = "failed"
        try:
            fixture = fixtures()
            thread.start()
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Entry acceptance server did not start")
                thread.join(0.05)
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        results[device] = journey(browser, f"http://127.0.0.1:{port}", fixture,
                            device, {"width": width, "height": height}, errors, screenshots)
                finally:
                    browser.close()
            assert not errors, errors
            assert not server_errors, server_errors
            status = "passed"
        finally:
            server.should_exit = True
            if thread.ident is not None:
                thread.join(timeout=10)
            engine.dispose()
            if thread.is_alive() or server_errors or errors:
                status = "failed"
            summary = {"status": status, "results": results, "browser_errors": errors,
                       "server_errors": server_errors, "server_stopped": not thread.is_alive(),
                       "screenshots": screenshots, "screenshot_count": len(screenshots),
                       "port": port, "isolation": "Fresh disposable SQLite; no external providers"}
            (OUTPUT / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary), flush=True)
        assert not thread.is_alive(), "Entry acceptance server did not stop"
        assert not server_errors, server_errors


if __name__ == "__main__":
    main()

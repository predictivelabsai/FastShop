"""Chrome acceptance evidence for Phase 1; never saves browser credentials/state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

from playwright.sync_api import sync_playwright


def load_page_media(page):
    """Exercise native lazy loading before capturing the entire document."""
    page.evaluate("document.fonts.ready")
    page.evaluate("window.scrollTo(0, 0)")
    height = page.evaluate("document.documentElement.scrollHeight")
    for offset in range(0, height, 600):
        page.evaluate("offset => window.scrollTo(0, offset)", offset)
        page.wait_for_timeout(80)
    page.wait_for_function("""() => Array.from(document.images)
        .filter(image => image.getClientRects().length)
        .every(image => image.complete && image.naturalWidth > 0)""")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(100)


def verify_landing(base: str, output: str):
    """Capture the static marketing surface without touching merchant state."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    checks = []
    allowed_origin = (urlsplit(base).scheme, urlsplit(base).netloc)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
            context = browser.new_context(
                viewport={"width": width, "height": height},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.on(
                "response",
                lambda response: failures.append(f"HTTP {response.status}: {response.url}")
                if response.status >= 400
                else None,
            )
            outbound = []
            page.on(
                "request",
                lambda request, outbound=outbound: outbound.append(request.url)
                if (urlsplit(request.url).scheme, urlsplit(request.url).netloc) != allowed_origin
                else None,
            )
            response = page.goto(base.rstrip("/") + "/marketing/")
            assert response.status == 200
            page.wait_for_load_state("networkidle")
            assert page.locator("h1").count() == 1
            assert "short description" in page.locator("h1").inner_text().lower()
            assert page.get_by_role("link", name="Create your workspace", exact=True).first.get_attribute("href") == "/signup"
            assert page.locator("script").count() == 0
            assert page.locator("iframe").count() == 0
            assert page.locator('[src*="analytics"], [href*="analytics"]').count() == 0
            assert not outbound, outbound
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(out / f"{device}.png"), full_page=True)
            checks.append(
                {
                    "device": device,
                    "width": width,
                    "status": response.status,
                    "overflow": False,
                    "outbound_requests": 0,
                }
            )
            context.close()
        browser.close()
    report = {"base": base, "checks": checks, "failures": failures}
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": failures, "output": str(out)}))
    if failures:
        raise SystemExit(1)


def verify_platform_root(base: str, output: str):
    """Capture the platform-root landing and prefixed legacy demo routes."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
            context = browser.new_context(
                viewport={"width": width, "height": height},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: failures.append(str(error)))
            for name, path, stylesheet in (
                ("landing-root", "/", "/static/marketing.css"),
                ("demo-root", "/demo", "/static/site.css"),
                ("demo-products", "/demo/products", "/static/site.css"),
            ):
                response = page.goto(base.rstrip("/") + path)
                assert response.status == 200
                page.wait_for_load_state("networkidle")
                assert page.locator("h1").count() == 1
                assert page.locator(f'link[href="{stylesheet}"]').count() == 1
                load_page_media(page)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(out / f"{device}-{name}.png"), full_page=True)
                checks.append(
                    {
                        "device": device,
                        "width": width,
                        "path": path,
                        "status": response.status,
                        "stylesheet": stylesheet,
                        "overflow": False,
                    }
                )
            context.close()
        browser.close()
    report = {"base": base, "checks": checks, "failures": failures}
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": failures, "output": str(out)}))
    if failures:
        raise SystemExit(1)


def _available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def signup_server(signup_open: bool, *, include_data_dir: bool = False):
    """Run one isolated app process because frozen settings cannot change in-process."""
    port = _available_port()
    data_dir = tempfile.mkdtemp(prefix="fastshop-signup-browser-")
    try:
        environment = os.environ.copy()
        environment.update(
            {
                "DB_URL": "",
                "FASTSHOP_ENV": "development",
                "FASTSHOP_AUTO_CREATE_SCHEMA": "1",
                "FASTSHOP_DATA_DIR": data_dir,
                "FASTSHOP_SIGNUP_OPEN": "1" if signup_open else "0",
                "XAI_API_KEY": "",
                "POSTMARK_API_TOKEN": "",
                "POSTMARK_SERVER_TOKEN": "",
                "STRIPE_SECRET_KEY": "",
                "STRIPE_WEBHOOK_SECRET": "",
                "FASTSHOP_BILLING_STRIPE_SECRET_KEY": "",
                "FASTSHOP_BILLING_STRIPE_WEBHOOK_SECRET": "",
                "FASTSHOP_BILLING_PRICE_BASIC": "",
                "FASTSHOP_BILLING_PRICE_PRO": "",
            }
        )
        environment.pop("FASTSHOP_ADMIN_EMAIL", None)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "web_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    detail = process.stderr.read() if process.stderr else ""
                    raise RuntimeError("Signup browser server stopped early: " + detail)
                try:
                    with urlopen(base + "/healthz", timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, URLError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Timed out starting the signup browser server.")
            yield (base, data_dir) if include_data_dir else base
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    finally:
        for attempt in range(10):
            try:
                shutil.rmtree(data_dir)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.2)


def verify_signup(output: str):
    """Capture both frozen kill-switch states in isolated local app processes."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for state, is_open in (("closed", False), ("form", True)):
            with signup_server(is_open) as base:
                allowed_origin = (urlsplit(base).scheme, urlsplit(base).netloc)
                for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        reduced_motion="reduce",
                    )
                    page = context.new_page()
                    page.on("pageerror", lambda error: failures.append(str(error)))
                    page.on(
                        "response",
                        lambda response: failures.append(f"HTTP {response.status}: {response.url}")
                        if response.status >= 400
                        else None,
                    )
                    outbound = []
                    page.on(
                        "request",
                        lambda request, outbound=outbound, allowed_origin=allowed_origin: outbound.append(request.url)
                        if (urlsplit(request.url).scheme, urlsplit(request.url).netloc)
                        != allowed_origin
                        else None,
                    )
                    response = page.goto(base + "/signup")
                    assert response.status == 200
                    page.wait_for_load_state("networkidle")
                    assert page.locator("h1").count() == 1
                    if is_open:
                        assert "create your fastshop workspace" in page.locator("h1").inner_text().lower()
                        assert page.locator('form[action="/signup"]').count() == 1
                        assert page.locator('input[name="csrf_token"]').get_attribute("value")
                    else:
                        assert "public signup is currently closed" in page.locator("h1").inner_text().lower()
                        assert page.locator('form[action="/signup"]').count() == 0
                    assert page.locator("script").count() == 0
                    assert page.locator("iframe").count() == 0
                    assert page.locator('[src*="analytics"], [href*="analytics"]').count() == 0
                    assert not outbound, outbound
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-{state}.png"), full_page=True)
                    checks.append(
                        {
                            "state": state,
                            "device": device,
                            "width": width,
                            "status": response.status,
                            "overflow": False,
                            "outbound_requests": 0,
                            "scripts": 0,
                        }
                    )
                    context.close()
        browser.close()
    report = {"checks": checks, "failures": failures}
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": failures, "output": str(out)}))
    if failures:
        raise SystemExit(1)


def verify_onboarding(output: str):
    """Exercise signup through generated and skipped onboarding outcomes."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    checks = []

    def capture(page, state, device, width, height):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_load_state("networkidle")
        assert page.locator("h1").count() == 1
        assert page.locator("iframe").count() == 0
        assert page.locator('[src*="analytics"], [href*="analytics"]').count() == 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(out / f"{device}-{state}.png"), full_page=True)
        checks.append(
            {
                "state": state,
                "device": device,
                "width": width,
                "overflow": False,
                "iframes": 0,
                "analytics_markers": 0,
            }
        )

    def create_workspace(page, base, suffix):
        page.goto(base + "/signup")
        page.get_by_label("Your name", exact=True).fill(f"Onboarding {suffix}")
        page.get_by_label("Email address", exact=True).fill(
            f"onboarding-{suffix.lower()}@example.test"
        )
        password = "correct-horse-battery-staple"
        page.get_by_label("Password", exact=True).fill(password)
        page.get_by_label("Confirm password", exact=True).fill(password)
        page.get_by_role("button", name="Create workspace", exact=True).click()
        page.wait_for_url("**/admin/onboarding/*")

    def save_brief(page):
        page.get_by_label("Describe your business", exact=True).fill(
            "A thoughtful home-goods shop for apartment dwellers who value useful, "
            "long-lasting objects."
        )
        page.get_by_label("What do you sell? (optional)", exact=True).fill("online shop")
        page.get_by_label("Warm and natural", exact=True).check()
        page.get_by_role("button", name="Save and continue", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert page.get_by_role("heading", name="Your brief is ready", exact=True).is_visible()

    with signup_server(True, include_data_dir=True) as server, sync_playwright() as pw:
        base, data_dir = server
        allowed_origin = (urlsplit(base).scheme, urlsplit(base).netloc)
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for outcome in ("generated", "skipped", "failed"):
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
            )
            page = context.new_page()
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.on(
                "response",
                lambda response: failures.append(f"HTTP {response.status}: {response.url}")
                if response.status >= 400
                else None,
            )
            outbound = []
            page.on(
                "request",
                lambda request, outbound=outbound: outbound.append(request.url)
                if (urlsplit(request.url).scheme, urlsplit(request.url).netloc)
                != allowed_origin
                else None,
            )
            create_workspace(page, base, outcome)
            for device, width, height in (
                ("desktop", 1440, 1000),
                ("mobile", 390, 844),
            ):
                capture(page, f"wizard-brief-{outcome}", device, width, height)
            page.set_viewport_size({"width": 1440, "height": 1000})
            save_brief(page)
            for device, width, height in (
                ("desktop", 1440, 1000),
                ("mobile", 390, 844),
            ):
                capture(page, f"wizard-choice-{outcome}", device, width, height)
            page.set_viewport_size({"width": 1440, "height": 1000})
            if outcome == "failed":
                connection = sqlite3.connect(Path(data_dir) / "fastshop.sqlite3")
                try:
                    connection.execute(
                        "UPDATE onboarding_states SET status = 'failed', "
                        "failure_code = 'browser_fixture' WHERE status = 'ready'"
                    )
                    connection.commit()
                finally:
                    connection.close()
                page.reload()
                assert page.get_by_role(
                    "heading", name="Something went wrong", exact=True
                ).is_visible()
                for device, width, height in (
                    ("desktop", 1440, 1000),
                    ("mobile", 390, 844),
                ):
                    capture(page, "failed", device, width, height)
            elif outcome == "generated":
                page.get_by_role("button", name="Generate with AI", exact=True).click()
                page.wait_for_url("**/admin/sites/*")
                assert page.get_by_role("status").inner_text().startswith(
                    "Draft created with guided presets"
                )
            else:
                page.get_by_role(
                    "button", name="Skip — start with the clean template", exact=True
                ).click()
                page.wait_for_url("**/admin/sites/*")
                assert page.get_by_role("status").inner_text().startswith(
                    "Clean template kept"
                )
            if outcome != "failed":
                for device, width, height in (
                    ("desktop", 1440, 1000),
                    ("mobile", 390, 844),
                ):
                    capture(page, outcome, device, width, height)
            unexpected = [
                url for url in outbound if urlsplit(url).netloc != "cdn.jsdelivr.net"
            ]
            assert not unexpected, unexpected
            assert all("analytics" not in url.lower() for url in outbound)
            context.close()
        browser.close()
    report = {"checks": checks, "failures": failures}
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": failures, "output": str(out)}))
    if failures:
        raise SystemExit(1)


def verify_plans(output: str):
    """Capture Phase 5d plan/usage evidence in one isolated local app process."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            reduced_motion="reduce",
        )
        page = context.new_page()
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.on(
            "response",
            lambda response: failures.append(f"HTTP {response.status}: {response.url}")
            if response.status >= 400
            else None,
        )
        try:
            with signup_server(True) as base:
                # Signup provisions the first site on the free plan (2 sites).
                email = "plans-browser-" + uuid4().hex[:8] + "@example.test"
                page.goto(base + "/signup")
                page.get_by_label("Your name", exact=True).fill("Plans quota workspace")
                page.get_by_label("Email address", exact=True).fill(email)
                page.get_by_label("Password", exact=True).fill("correct-horse-battery-staple")
                page.get_by_label("Confirm password", exact=True).fill(
                    "correct-horse-battery-staple"
                )
                page.get_by_role("button", name="Create workspace", exact=True).click()
                page.wait_for_url("**/admin/onboarding/*")

                # Second site creation succeeds and fills the free site quota.
                page.goto(base + "/admin/sites")
                assert page.get_by_text("Plan & usage — Free", exact=False).is_visible()
                assert page.get_by_text("Sites: 1 of 2", exact=True).is_visible()
                page.locator('input[name="name"]').fill("Quota second site")
                page.locator('input[name="slug"]').fill("quota-second-" + uuid4().hex[:8])
                page.get_by_role("button", name="Create site", exact=True).click()
                page.wait_for_url("**/admin/sites/*")

                # The usage panel now reads the exhausted count; reads stay open.
                page.goto(base + "/admin/sites")
                assert page.get_by_text("Sites: 2 of 2", exact=True).is_visible()
                for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-plans-usage.png"), full_page=True)

                # A third site creation is refused with a merchant-readable notice.
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.locator('input[name="name"]').fill("Blocked third site")
                page.locator('input[name="slug"]').fill("quota-blocked-" + uuid4().hex[:8])
                page.get_by_role("button", name="Create site", exact=True).click()
                page.wait_for_load_state("networkidle")
                status = page.get_by_role("status").inner_text()
                assert "Your Free plan allows 2 sites" in status, status
                assert "you are now using 2 of 2" in status, status
                for device, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(
                        path=str(out / f"{device}-site-quota-blocked.png"), full_page=True
                    )
                checks.append(
                    {
                        "merchant": (
                            "plan usage panel reads account counts and the third site "
                            "creation is refused with a readable free-plan notice"
                        ),
                        "status": "passed",
                    }
                )
        finally:
            context.close()
            browser.close()
    report = {"checks": checks, "failures": failures}
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": failures, "output": str(out)}))
    if failures:
        raise SystemExit(1)


def verify_billing(output: str):
    """Capture Phase 5e's safe unconfigured merchant billing state."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            reduced_motion="reduce",
        )
        page = context.new_page()
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.on(
            "response",
            lambda response: failures.append(f"HTTP {response.status}: {response.url}")
            if response.status >= 400
            else None,
        )
        try:
            with signup_server(True) as base:
                email = "billing-browser-" + uuid4().hex[:8] + "@example.test"
                page.goto(base + "/signup")
                page.get_by_label("Your name", exact=True).fill("Billing workspace")
                page.get_by_label("Email address", exact=True).fill(email)
                page.get_by_label("Password", exact=True).fill(
                    "correct-horse-battery-staple"
                )
                page.get_by_label("Confirm password", exact=True).fill(
                    "correct-horse-battery-staple"
                )
                page.get_by_role("button", name="Create workspace", exact=True).click()
                page.wait_for_url("**/admin/onboarding/*")
                page.goto(base + "/admin/billing")
                assert page.get_by_role("heading", name="Billing", exact=True).is_visible()
                assert page.get_by_text(
                    "Billing setup is incomplete", exact=False
                ).first.is_visible()
                unavailable = page.get_by_role(
                    "button", name="Currently unavailable", exact=True
                )
                assert unavailable.count() == 2
                assert unavailable.first.is_disabled()
                assert unavailable.last.is_disabled()
                for device, width, height in (
                    ("desktop", 1440, 1000),
                    ("mobile", 390, 844),
                ):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth + 1"
                    )
                    page.screenshot(
                        path=str(out / f"{device}-billing-unconfigured.png"),
                        full_page=True,
                    )
                checks.append(
                    {
                        "merchant": (
                            "billing view keeps plan and quota access visible while dedicated "
                            "Stripe credentials and Price IDs are unconfigured"
                        ),
                        "status": "passed",
                    }
                )
        finally:
            context.close()
            browser.close()
    report = {"checks": checks, "failures": failures}
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": failures, "output": str(out)}))
    if failures:
        raise SystemExit(1)


def verify_design_audit(base: str, output: str):
    """Capture the key public, merchant, builder, billing and operator surfaces."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    checks = []
    skipped = []
    sizes = (("desktop", 1440, 1000), ("mobile", 390, 844))

    def capture(page, surface, device, width, height, *, status=200, fold=False):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready")
        page.evaluate("window.scrollTo(0, 0)")
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > innerWidth + 1"
        )
        suffix = "-fold" if fold else ""
        filename = f"{surface}-{device}{suffix}.png"
        page.screenshot(path=str(out / filename), full_page=not fold)
        checks.append(
            {
                "surface": surface,
                "device": device,
                "width": width,
                "url": page.url,
                "status": status,
                "overflow": overflow,
                "capture": "viewport" if fold else "full-page",
                "file": filename,
            }
        )
        assert not overflow, f"Horizontal overflow on {surface} at {width}px"

    def capture_url(page, surface, device, width, height, path, *, landing=False):
        response = page.goto(base.rstrip("/") + path)
        assert response and response.status == 200, (surface, response.status if response else None)
        page.wait_for_load_state("networkidle")
        if landing:
            load_page_media(page)
        capture(page, surface, device, width, height, status=response.status)
        if landing:
            capture(page, surface, device, width, height, status=response.status, fold=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        try:
            for device, width, height in sizes:
                context = browser.new_context(
                    viewport={"width": width, "height": height}, reduced_motion="reduce"
                )
                page = context.new_page()
                page.on("pageerror", lambda error: failures.append(str(error)))
                capture_url(page, "marketing", device, width, height, "/", landing=True)
                capture_url(page, "signup", device, width, height, "/signup")
                capture_url(page, "login", device, width, height, "/login")
                context.close()

            merchant = browser.new_context(
                viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
            )
            page = merchant.new_page()
            page.on("pageerror", lambda error: failures.append(str(error)))
            email = "design-audit-" + uuid4().hex[:8] + "@example.test"
            page.goto(base.rstrip("/") + "/signup")
            page.get_by_label("Your name", exact=True).fill("Design audit workspace")
            page.get_by_label("Email address", exact=True).fill(email)
            page.get_by_label("Password", exact=True).fill("correct-horse-battery-staple")
            page.get_by_label("Confirm password", exact=True).fill(
                "correct-horse-battery-staple"
            )
            page.get_by_role("button", name="Create workspace", exact=True).click()
            page.wait_for_url("**/admin/onboarding/*")
            onboarding_url = page.url
            for device, width, height in sizes:
                capture(page, "onboarding", device, width, height)

            page.goto(base.rstrip("/") + "/admin/sites")
            dashboard_url = page.url
            for device, width, height in sizes:
                capture(page, "dashboard", device, width, height)

            page.goto(base.rstrip("/") + "/admin/billing")
            billing_url = page.url
            for device, width, height in sizes:
                capture(page, "billing", device, width, height)
            merchant.close()

            operator = browser.new_context(
                viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
            )
            page = operator.new_page()
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.goto(base.rstrip("/") + "/login?next=/admin/sites")
            page.locator('input[name="email"]').fill(
                os.environ.get("FASTSHOP_ADMIN_EMAIL", "admin@fastshop.example")
            )
            page.locator('input[name="password"]').fill(
                os.environ.get("FASTSHOP_ADMIN_PASSWORD", "FastShop2026$")
            )
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/admin/sites")
            h24_card = page.locator(".e-card").filter(
                has=page.get_by_role("heading", name="H2 4 You", exact=True)
            )
            h24_card.get_by_role("link", name="Open editor →", exact=True).click()
            page.get_by_role("link", name="Build with AI →", exact=True).click()
            page.wait_for_url("**/admin/sites/*/build")
            builder_url = page.url
            for device, width, height in sizes:
                capture(page, "builder", device, width, height)

            plans_response = page.goto(base.rstrip("/") + "/admin/platform/plans")
            if (
                plans_response
                and plans_response.status == 200
                and page.get_by_role("heading", name="Plans & quotas", exact=True).count()
            ):
                plans_url = page.url
                for device, width, height in sizes:
                    capture(
                        page,
                        "plans-console",
                        device,
                        width,
                        height,
                        status=plans_response.status,
                    )
            else:
                plans_url = base.rstrip("/") + "/admin/platform/plans"
                skipped.append(
                    {
                        "surface": "plans-console",
                        "url": plans_url,
                        "reason": "Platform operator access is not available without operator configuration.",
                    }
                )
            operator.close()
        finally:
            browser.close()

    report = {
        "base": base,
        "urls": {
            "marketing": base.rstrip("/") + "/",
            "signup": base.rstrip("/") + "/signup",
            "login": base.rstrip("/") + "/login",
            "onboarding": onboarding_url,
            "dashboard": dashboard_url,
            "builder": builder_url,
            "billing": billing_url,
            "plans-console": plans_url,
        },
        "checks": checks,
        "skipped": skipped,
        "failures": failures,
    }
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": failures, "output": str(out)}))
    if failures:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5033")
    parser.add_argument("--merchant", action="store_true")
    parser.add_argument("--merchant-only", action="store_true", help="Rerun merchant flows after storefront verification")
    parser.add_argument("--blog", action="store_true", help="Include blog taxonomy and editorial workflow evidence")
    parser.add_argument("--embeds", action="store_true", help="Include Phase 1c embed and snippet publication evidence")
    parser.add_argument("--generation", action="store_true", help="Include Phase 2a brief-to-draft generation")
    parser.add_argument("--refinement", action="store_true", help="Include Phase 2b block-diff review workflow")
    parser.add_argument("--woocommerce", action="store_true", help="Include Phase 3a fixture-backed WooCommerce migration")
    parser.add_argument("--shopify", action="store_true", help="Include Phase 3b fixture-backed Shopify migration")
    parser.add_argument("--wordpress", action="store_true", help="Include Phase 3c fixture-backed WordPress migration")
    parser.add_argument("--csv-merchant-feed", action="store_true", help="Include Phase 3d CSV import and Merchant Center feed")
    parser.add_argument("--golive", action="store_true", help="Include Phase 4a publish, domain and sandbox-commerce gates")
    parser.add_argument("--live-credentials", action="store_true", help="Include Phase 4b operator live-credential acceptance")
    parser.add_argument("--order-management", action="store_true", help="Include Phase 4c real order operations and revenue reporting")
    parser.add_argument("--webhook-reliability", action="store_true", help="Include Phase 4d provider re-sync and delivery recovery")
    parser.add_argument("--landing", action="store_true", help="Capture the static Phase 5a marketing landing")
    parser.add_argument(
        "--platform-root",
        action="store_true",
        help="Capture the platform-root landing and /demo mapping",
    )
    parser.add_argument("--signup", action="store_true", help="Capture closed and open Phase 5b signup states")
    parser.add_argument(
        "--onboarding",
        action="store_true",
        help="Capture Phase 5c generated and skipped onboarding flows",
    )
    parser.add_argument(
        "--plans",
        action="store_true",
        help="Include Phase 5d plan, quota and metering enforcement evidence",
    )
    parser.add_argument(
        "--billing",
        action="store_true",
        help="Include Phase 5e platform billing disabled-state evidence",
    )
    parser.add_argument(
        "--design-audit",
        action="store_true",
        help="Capture all key product surfaces against one already-running app",
    )
    parser.add_argument("--output", default="output/playwright/h24you-phase1")
    args = parser.parse_args()
    if args.design_audit:
        if args.output == "output/playwright/h24you-phase1":
            args.output = "output/playwright/design-audit"
        verify_design_audit(args.base, args.output)
        return
    if args.onboarding:
        if args.output == "output/playwright/h24you-phase1":
            args.output = "output/playwright/phase-onboarding-wizard"
        verify_onboarding(args.output)
        return
    if args.plans:
        if args.output == "output/playwright/h24you-phase1":
            args.output = "output/playwright/phase5d-plans"
        verify_plans(args.output)
        return
    if args.billing:
        if args.output == "output/playwright/h24you-phase1":
            args.output = "output/playwright/phase5e-billing"
        verify_billing(args.output)
        return
    if args.signup:
        if args.output == "output/playwright/h24you-phase1":
            args.output = "output/playwright/phase5b-signup"
        verify_signup(args.output)
        return
    if args.landing:
        if args.output == "output/playwright/h24you-phase1":
            args.output = "output/playwright/phase5a-landing"
        with signup_server(True) as base:
            verify_landing(base, args.output)
        return
    if args.platform_root:
        if args.output == "output/playwright/h24you-phase1":
            args.output = "output/playwright/phase-landing-root"
        verify_platform_root(args.base, args.output)
        return
    if sum((args.woocommerce, args.shopify, args.wordpress, args.csv_merchant_feed)) > 1:
        raise RuntimeError("Run one fixture-backed connector browser flow at a time.")
    if (args.generation or args.refinement or args.woocommerce or args.shopify or args.wordpress or args.csv_merchant_feed or args.golive or args.live_credentials or args.order_management or args.webhook_reliability) and not (args.merchant or args.merchant_only):
        raise RuntimeError("Generation, refinement and connector checks require --merchant against an isolated local database.")
    if args.live_credentials and not args.golive:
        raise RuntimeError("Live-credential browser verification also requires --golive to prepare the reviewed fixture site.")
    if args.woocommerce and not os.getenv("FASTSHOP_WOOCOMMERCE_FIXTURE_PATH"):
        raise RuntimeError("WooCommerce browser verification requires FASTSHOP_WOOCOMMERCE_FIXTURE_PATH.")
    if args.shopify and not os.getenv("FASTSHOP_SHOPIFY_FIXTURE_PATH"):
        raise RuntimeError("Shopify browser verification requires FASTSHOP_SHOPIFY_FIXTURE_PATH.")
    if args.wordpress and not os.getenv("FASTSHOP_WORDPRESS_FIXTURE_PATH"):
        raise RuntimeError("WordPress browser verification requires FASTSHOP_WORDPRESS_FIXTURE_PATH.")
    if args.embeds:
        if not (args.merchant or args.merchant_only) or not args.base.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise RuntimeError("Embed/snippet checks require --merchant against an isolated local database.")
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import Site

        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == "h24you"))
            if not site:
                raise RuntimeError("The H2 4 You fixture is not available.")
            site.status = "published"
            db.commit()
    if args.order_management and args.output == "output/playwright/h24you-phase1":
        args.output = "output/playwright/phase4c-order-management"
    if args.webhook_reliability and args.output == "output/playwright/h24you-phase1":
        args.output = "output/playwright/phase4d-webhook-reliability"
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    paths = ["/", "/shop", "/collections/hydrogen-tablets", "/collections/hydrogen-water-bottles",
             "/products/hydrogen-tablets", "/products/hydroxy-go", "/pages/science", "/blogs/learn",
             "/blogs/learn/what-is-molecular-hydrogen", "/blogs/learn/timing-and-consistency",
             "/blogs/learn/how-to-read-hydrogen-research", "/pages/about-us", "/pages/contact",
             "/pages/terms-and-conditions", "/pages/privacy-policy", "/pages/faq", "/pages/returns-and-refunds"]
    failures, checks = [], []
    if args.blog:
        paths.extend(["/blog", "/blog/category/the-basics"])
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for device, width, height in ([] if args.merchant_only else [("desktop", 1440, 1000), ("ipad", 834, 1112), ("mobile", 390, 844)]):
            context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
            page = context.new_page()
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.on("response", lambda response: failures.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
            page.goto(args.base + "/sites/h24you/")
            page.locator('#h-cookie button[data-consent="none"]').click()
            assert page.evaluate("window.fastshopConsent.analytics === false && window.fastshopConsent.marketing === false")
            page.evaluate("localStorage.setItem('fastshop-offer:' + document.querySelector('.h-site').dataset.site, 'true')")
            for path in paths:
                response = page.goto(args.base + "/sites/h24you" + path)
                assert response.status == 200, path
                page.wait_for_load_state("networkidle")
                assert page.locator("h1").count() == 1, path
                assert page.locator('link[rel="canonical"]').count() == 1
                if args.blog and path == "/blog/category/the-basics":
                    assert page.locator(".h-articles article").count() == 1
                    assert page.get_by_role("navigation", name="Article categories").get_by_role("link", name="THE BASICS", exact=True).get_attribute("aria-current") == "page"
                    assert page.locator('link[rel="canonical"]').get_attribute("href").endswith(path)
                    assert page.locator(".h-blog-byline").inner_text() == "By H2 4 You team"
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (device, path)
                assert page.locator("img").evaluate_all("images => images.every(i => i.hasAttribute('alt') && i.getAttribute('alt').length)")
                load_page_media(page)
                name = path.strip("/").replace("/", "-") or "home"
                page.screenshot(path=str(out / f"{device}-{name}.png"), full_page=True)
                checks.append({"device": device, "path": path, "status": response.status, "title": page.title()})
            page.goto(args.base + "/sites/h24you/pages/science")
            page.get_by_role("button", name="Exercise", exact=True).click()
            assert page.locator('[data-research-theme="Exercise"]:visible').count() == 2
            assert page.locator('[data-research-theme="Reviews"]:visible').count() == 0
            page.get_by_role("button", name="Cookie preferences", exact=True).click()
            page.locator("#h-cookie summary").click()
            page.screenshot(path=str(out / f"{device}-cookie-preferences.png"), full_page=False)
            page.locator('[data-consent="none"]').click()
            if device == "mobile":
                page.get_by_role("button", name="Menu", exact=True).click()
                assert page.locator(".h-nav").is_visible()
                page.screenshot(path=str(out / "mobile-open-menu.png"))
                page.keyboard.press("Escape")
                assert not page.locator(".h-nav").is_visible()
            page.goto(args.base + "/sites/h24you/products/hydrogen-tablets")
            page.get_by_role("combobox", name="Choose your option").select_option(label="Raspberry")
            assert page.get_by_role("button", name="Add to cart — coming in Phase 2").is_disabled()
            page.get_by_role("button", name="View gallery image 2", exact=True).click()
            assert "water-placeholder" in page.locator('[data-gallery-main] img').get_attribute("src")
            context.close()
        if args.merchant or args.merchant_only:
            if not args.base.startswith(("http://127.0.0.1:", "http://localhost:")):
                raise RuntimeError("Merchant mutation checks are limited to an isolated local database.")
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            if args.wordpress:
                fixture_image = Path(__file__).resolve().parents[1] / "static" / "h24you" / "water-placeholder.webp"
                context.route(
                    "https://images.wordpress.example/**",
                    lambda route: route.fulfill(path=str(fixture_image), content_type="image/webp"),
                )
            page = context.new_page()
            page.goto(args.base + "/login?next=/admin/sites")
            page.locator('input[name="email"]').fill(os.environ["FASTSHOP_ADMIN_EMAIL"])
            page.locator('input[name="password"]').fill(os.environ["FASTSHOP_ADMIN_PASSWORD"])
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/admin/sites")
            page.screenshot(path=str(out / "merchant-sites.png"), full_page=True)
            if args.golive:
                from sqlalchemy import select

                from app import commerce
                from app.db import SessionLocal
                from app.models import (
                    Product,
                    ProductVariant,
                    Site,
                    SitePage,
                    VariantChannelListing,
                )

                blocked_slug = "golive-blocked-" + uuid4().hex[:8]
                page.locator('input[name="name"]').fill("Blocked go-live review")
                page.locator('input[name="slug"]').fill(blocked_slug)
                page.get_by_role("button", name="Create site", exact=True).click()
                page.wait_for_url("**/admin/sites/*")
                page.get_by_role("link", name="Go-live review", exact=True).click()
                assert page.get_by_role("heading", name="Go-live review", exact=True).is_visible()
                assert page.locator(".g-check-fail").count() >= 1
                page.get_by_label("Review reason", exact=True).fill(
                    "Browser review confirms this incomplete site must remain private."
                )
                page.get_by_label("I reviewed the checklist and the public snapshots.", exact=True).check()
                page.get_by_role("button", name="Publish site", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("status").inner_text().startswith("Publication blocked")
                page.screenshot(path=str(out / "desktop-golive-publish-blocked.png"), full_page=True)

                with SessionLocal() as db:
                    h24 = db.scalar(select(Site).where(Site.slug == "h24you"))
                    if not h24:
                        raise RuntimeError("The H2 4 You fixture site is unavailable.")
                    h24_id = h24.id
                page.goto(args.base + f"/admin/sites/{h24_id}/golive")
                assert page.locator("#publish").is_visible()
                assert page.locator(".g-check-fail").count() >= 1  # commerce remains intentionally incomplete
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-golive-checklist.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_label("Review reason", exact=True).fill(
                    "All publication snapshots, menus and compliance results were reviewed in the browser flow."
                )
                page.get_by_label("I reviewed the checklist and the public snapshots.", exact=True).check()
                page.get_by_role("button", name="Publish site", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("status").inner_text() == "Site published after readiness review."
                domain = "browser-golive-" + uuid4().hex[:8] + ".example.test"
                page.get_by_label("Custom hostname", exact=True).fill(domain)
                page.get_by_role("button", name="Save custom domain", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert domain in page.get_by_role("status").inner_text()

                with SessionLocal() as db:
                    h24 = db.get(Site, h24_id)
                    for path in ("/pages/privacy-policy", "/pages/terms-and-conditions", "/pages/returns-and-refunds"):
                        policy = db.scalar(select(SitePage).where(
                            SitePage.site_id == h24.id,
                            SitePage.tenant_id == h24.tenant_id,
                            SitePage.path == path,
                        ))
                        document = dict(policy.draft_json)
                        document["blocks"] = [dict(block) for block in policy.draft_json["blocks"]]
                        document["blocks"][0]["body"] = "Reviewed browser-verification policy for this sandbox storefront."
                        policy.draft_json = document
                        policy.published_json = dict(document)
                    products = list(db.scalars(select(Product).where(
                        Product.tenant_id == h24.tenant_id,
                        Product.is_published.is_(True),
                    )))
                    variants = list(db.scalars(select(ProductVariant).where(
                        ProductVariant.tenant_id == h24.tenant_id,
                        ProductVariant.product_id.in_([product.id for product in products]),
                        ProductVariant.is_active.is_(True),
                    )))
                    priced = set(db.scalars(select(VariantChannelListing.variant_id).where(
                        VariantChannelListing.channel_id == h24.channel_id,
                    )))
                    for variant in variants:
                        if variant.id not in priced:
                            db.add(VariantChannelListing(
                                variant_id=variant.id,
                                channel_id=h24.channel_id,
                                currency="USD",
                                price_minor=2500,
                            ))
                    config = commerce.settings_for(db, h24, create=True)
                    config.mode = "disabled"
                    config.origin_json = {"country": "EE", "line1": "Browser warehouse", "city": "Tallinn", "postal_code": "10111"}
                    config.shipping_minor = 900
                    config.allowed_states_json = ["CA", "NY"]
                    config.tax_registration_reviewed = True
                    config.product_tax_codes_json = {product.id: "txcd_00000000" for product in products}
                    config.version += 1
                    h24.version += 1
                    db.commit()
                page.reload()
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("button", name="Enable sandbox commerce", exact=True).is_enabled()
                page.get_by_role("button", name="Enable sandbox commerce", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("status").inner_text() == "Sandbox commerce enabled. Live payments remain unavailable."
                assert page.get_by_role("button", name="Disable sandbox commerce", exact=True).is_visible()
                assert page.get_by_text("sandbox-commerce-enable · approved", exact=False).is_visible()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-golive-commerce-enabled.png"), full_page=True)
                checks.append({
                    "merchant": "go-live checklist blocks an incomplete publish, approves a ready publish, binds a domain and enables sandbox commerce",
                    "status": "passed",
                })
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(args.base + "/admin/sites")
            if args.live_credentials:
                from sqlalchemy import select

                from app import live_credentials
                from app.db import SessionLocal
                from app.models import Site, User

                secret_key = "sk_live_browser_fixture_1234"
                webhook_secret = "whsec_browser_fixture_5678"
                with SessionLocal() as db:
                    live_site = db.scalar(select(Site).where(Site.slug == "h24you"))
                    operator = db.scalar(select(User).where(User.email == os.environ["FASTSHOP_ADMIN_EMAIL"].lower()))
                    if not live_site or not operator:
                        raise RuntimeError("The operator fixture site is unavailable.")
                    live_site_id = live_site.id

                anonymous = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
                denied = anonymous.goto(args.base + f"/admin/platform/sites/{live_site_id}/live-credentials")
                assert denied.status == 400
                assert secret_key not in anonymous.content() and webhook_secret not in anonymous.content()
                anonymous.context.close()

                page.goto(args.base + f"/admin/platform/sites/{live_site_id}/live-credentials")
                page.get_by_label("Stripe live secret key", exact=True).fill(secret_key)
                page.get_by_label("Webhook signing secret", exact=True).fill(webhook_secret)
                page.get_by_role("button", name="Store encrypted credentials", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("status").inner_text().startswith("Live credentials stored encrypted")
                assert page.get_by_text("sk_live_****1234", exact=False).is_visible()
                assert secret_key not in page.content() and webhook_secret not in page.content()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-live-credentials-stored.png"), full_page=True)

                with SessionLocal() as db:
                    live_site = db.get(Site, live_site_id)
                    credential = live_credentials.credential_for(db, live_site)
                    live_credentials.mark_verified(db, live_site.id, operator.id, credential.id)
                    db.commit()
                page.reload()
                page.wait_for_load_state("networkidle")
                page.get_by_label("Acceptance reason", exact=True).fill(
                    "Browser fixture confirms the reviewed site and mocked live credential verification."
                )
                page.get_by_label("I confirm the published site, bound domain, sandbox checkout, and verified live account are ready.", exact=True).check()
                page.get_by_role("button", name="Accept live payments", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("status").inner_text() == "Live payments accepted for this site."
                assert page.get_by_text("Live payments are approved by the platform operator.", exact=True).is_visible()
                assert secret_key not in page.content() and webhook_secret not in page.content()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-live-credentials-accepted.png"), full_page=True)
                page.goto(args.base + f"/admin/sites/{live_site_id}/integrations")
                assert page.get_by_text("Live payments: approved by operator", exact=True).is_visible()
                assert secret_key not in page.content() and webhook_secret not in page.content()
                checks.append({
                    "operator": "live credentials are stored without echo, masked, explicitly accepted, and reduced to an approval badge on merchant integrations",
                    "status": "passed",
                })
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(args.base + "/admin/sites")
            if args.order_management:
                from datetime import UTC, datetime

                from sqlalchemy import select

                from app.db import SessionLocal
                from app.models import (
                    Order,
                    OrderLine,
                    OutboxEvent,
                    PaymentTransaction,
                    ShopCustomer,
                    Site,
                    SiteOrder,
                )

                with SessionLocal() as db:
                    order_site = db.scalar(select(Site).where(Site.slug == "h24you"))
                    if not order_site:
                        raise RuntimeError("The H2 4 You order fixture site is unavailable.")
                    customer = ShopCustomer(
                        tenant_id=order_site.tenant_id,
                        site_id=order_site.id,
                        email="browser-order@example.test",
                        name="Browser order customer",
                        verified_at=datetime.now(UTC),
                    )
                    db.add(customer)
                    db.flush()
                    order = Order(
                        tenant_id=order_site.tenant_id,
                        channel_id=order_site.channel_id,
                        number="FS-BROWSER-4C-" + uuid4().hex[:6].upper(),
                        idempotency_key="browser-order-" + uuid4().hex,
                        email=customer.email,
                        currency="USD",
                        subtotal_minor=7400,
                        discount_minor=400,
                        shipping_minor=900,
                        tax_minor=610,
                        total_minor=8510,
                        payment_status="partially_refunded",
                        shipping_address_json={
                            "name": customer.name,
                            "line1": "801 Commerce Street",
                            "city": "Dallas",
                            "state": "TX",
                            "postal_code": "75202",
                            "country": "US",
                        },
                    )
                    db.add(order)
                    db.flush()
                    db.add(OrderLine(
                        order_id=order.id,
                        sku="BROWSER-4C",
                        product_name="Hydrogen tablet bundle",
                        variant_name="Raspberry",
                        quantity=2,
                        unit_price_minor=3700,
                        total_minor=7400,
                    ))
                    link = SiteOrder(
                        tenant_id=order_site.tenant_id,
                        site_id=order_site.id,
                        order_id=order.id,
                        customer_id=customer.id,
                        stripe_checkout_id="cs_test_browser4c",
                    )
                    db.add(link)
                    db.flush()
                    db.add_all([
                        PaymentTransaction(
                            order_id=order.id,
                            provider="stripe:" + order_site.id,
                            external_id="pi_browser4c_" + uuid4().hex[:8],
                            kind="charge",
                            status="succeeded",
                            currency="USD",
                            amount_minor=8510,
                        ),
                        PaymentTransaction(
                            order_id=order.id,
                            provider="stripe:" + order_site.id,
                            external_id="re_browser4c_" + uuid4().hex[:8],
                            kind="refund",
                            status="succeeded",
                            currency="USD",
                            amount_minor=1200,
                        ),
                        OutboxEvent(
                            tenant_id=order_site.tenant_id,
                            topic="order.confirmed",
                            aggregate_id=order.id,
                            payload_json={"order_id": order.id, "site_id": order_site.id},
                        ),
                    ])
                    order_site_id, order_number = order_site.id, order.number
                    db.commit()

                orders_url = args.base + f"/admin/sites/{order_site_id}/orders"
                page.goto(orders_url)
                assert page.get_by_role("heading", name="Orders", exact=True).is_visible()
                assert page.get_by_role("link", name=order_number, exact=True).is_visible()
                assert "demo" not in page.locator(".o-table-wrap").inner_text().lower()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-orders-list.png"), full_page=True)

                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("link", name=order_number, exact=True).click()
                page.locator('select[name="target"]').select_option("fulfilled")
                page.locator('input[name="carrier"]').fill("UPS")
                page.locator('input[name="tracking_number"]').fill("1ZBROWSER4C")
                page.locator('input[name="tracking_url"]').fill("https://www.ups.com/track?loc=en_US")
                page.locator('input[name="note"]').fill("Packed and handed to the carrier.")
                page.get_by_role("button", name="Save fulfillment update", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("status").inner_text() == "Fulfillment status updated."
                assert page.get_by_text("Fulfilled", exact=True).count() >= 1
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-order-detail.png"), full_page=True)

                page.locator('input[name="amount_minor"]').fill("500")
                page.locator('input[name="reason"]').fill("Browser verification exact partial refund.")
                page.get_by_role("button", name="Review refund", exact=True).click()
                assert page.get_by_role("heading", name="Confirm refund", exact=True).is_visible()
                assert page.get_by_text("Refund $5.00", exact=False).is_visible()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-refund-confirm.png"), full_page=True)

                page.goto(args.base + f"/admin/sites/{order_site_id}/revenue")
                assert page.get_by_role("heading", name="Revenue report", exact=True).is_visible()
                assert page.get_by_text("−$12.00", exact=True).is_visible()
                assert page.locator('script[src*="google"],script[src*="analytics"]').count() == 0
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-revenue-report.png"), full_page=True)
                checks.append({
                    "merchant": "real order list, legal fulfillment transition, exact refund confirmation, and server-rendered revenue reporting",
                    "status": "passed",
                })
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(args.base + "/admin/sites")
            if args.webhook_reliability:
                from datetime import UTC, datetime

                from sqlalchemy import select

                from app.db import SessionLocal
                from app.models import (
                    Order,
                    OutboxEvent,
                    PaymentTransaction,
                    ShopCustomer,
                    Site,
                    SiteOrder,
                )

                with SessionLocal() as db:
                    reliability_site = db.scalar(select(Site).where(Site.slug == "h24you"))
                    if not reliability_site:
                        raise RuntimeError("The H2 4 You reliability fixture site is unavailable.")
                    customer = ShopCustomer(
                        tenant_id=reliability_site.tenant_id,
                        site_id=reliability_site.id,
                        email="browser-reliability@example.test",
                        name="Webhook recovery customer",
                        verified_at=datetime.now(UTC),
                    )
                    db.add(customer)
                    db.flush()
                    order = Order(
                        tenant_id=reliability_site.tenant_id,
                        channel_id=reliability_site.channel_id,
                        number="FS-BROWSER-4D-" + uuid4().hex[:6].upper(),
                        idempotency_key="browser-reliability-" + uuid4().hex,
                        email=customer.email,
                        currency="USD",
                        subtotal_minor=6200,
                        shipping_minor=800,
                        tax_minor=560,
                        total_minor=7560,
                        payment_status="pending",
                        shipping_address_json={
                            "name": customer.name,
                            "line1": "89 Recovery Lane",
                            "city": "Austin",
                            "state": "TX",
                            "postal_code": "78701",
                            "country": "US",
                        },
                    )
                    db.add(order)
                    db.flush()
                    link = SiteOrder(
                        tenant_id=reliability_site.tenant_id,
                        site_id=reliability_site.id,
                        order_id=order.id,
                        customer_id=customer.id,
                        stripe_checkout_id="cs_test_browser4d",
                    )
                    db.add(link)
                    db.flush()
                    db.add_all([
                        PaymentTransaction(
                            order_id=order.id,
                            provider="stripe:" + reliability_site.id,
                            external_id="pi_browser4d_" + uuid4().hex[:8],
                            kind="charge",
                            status="pending",
                            currency="USD",
                            amount_minor=7560,
                        ),
                        OutboxEvent(
                            tenant_id=reliability_site.tenant_id,
                            topic="order.confirmed",
                            aggregate_id=order.id,
                            payload_json={"order_id": order.id, "site_id": reliability_site.id},
                            status="dead",
                            attempts=8,
                            last_error="FastERP did not accept the order after bounded retries.",
                        ),
                        OutboxEvent(
                            tenant_id=reliability_site.tenant_id,
                            topic="order.refunded",
                            aggregate_id=order.id,
                            payload_json={"order_id": order.id, "site_id": reliability_site.id},
                            status="failed",
                            attempts=3,
                            last_error="Legacy delivery failure awaiting operator review.",
                        ),
                    ])
                    reliability_site_id = reliability_site.id
                    reliability_link_id = link.id
                    db.commit()

                detail_url = args.base + f"/admin/sites/{reliability_site_id}/orders/{reliability_link_id}"
                page.goto(detail_url)
                assert page.get_by_role("heading", name="Re-sync provider status", exact=True).is_visible()
                assert page.get_by_role("button", name="Re-pull Stripe status", exact=True).is_visible()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-provider-resync.png"), full_page=True)

                page.goto(args.base + f"/admin/sites/{reliability_site_id}/deliveries")
                assert page.get_by_role("heading", name="Delivery issues", exact=True).is_visible()
                assert page.get_by_text("FastERP did not accept", exact=False).is_visible()
                assert page.get_by_role("button", name="Requeue", exact=True).count() == 2
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-delivery-issues.png"), full_page=True)
                checks.append({
                    "merchant": "one-order Stripe re-sync and tenant-scoped dead-letter recovery are visible without exposing provider secrets",
                    "status": "passed",
                })
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(args.base + "/admin/sites")
            if args.woocommerce or args.shopify or args.wordpress or args.csv_merchant_feed:
                from sqlalchemy import select

                from app.db import SessionLocal
                from app.models import Site

                with SessionLocal() as db:
                    connector_site = db.scalar(select(Site).where(Site.slug == "h24you"))
                    if connector_site is None:
                        raise RuntimeError("The H2 4 You fixture site is unavailable.")
                    connector_site_id = connector_site.id
                connector_name = "csv" if args.csv_merchant_feed else "wordpress" if args.wordpress else "shopify" if args.shopify else "woocommerce"
                connector_label = "CSV catalog & Merchant Center" if args.csv_merchant_feed else "WordPress" if args.wordpress else "Shopify" if args.shopify else "WooCommerce"
                page.goto(args.base + f"/admin/sites/{connector_site_id}/integrations")
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("heading", name=connector_label, exact=True).is_visible()
                if not args.csv_merchant_feed:
                    fixture_message = "Development fixture site ready." if args.wordpress else "Development fixture store ready."
                    assert page.get_by_text(fixture_message, exact=True).is_visible()
                if args.wordpress:
                    with page.expect_download() as download_info:
                        page.get_by_role("link", name="Download published WXR XML", exact=True).click()
                    assert download_info.value.suggested_filename.endswith("-published.xml")
                if args.csv_merchant_feed:
                    with page.expect_download() as download_info:
                        page.get_by_role("link", name="Download Merchant Center XML feed", exact=True).click()
                    assert download_info.value.suggested_filename.endswith("-merchant-center.xml")
                    with page.expect_download() as report_info:
                        page.get_by_role("link", name="Download feed validation report", exact=True).click()
                    assert report_info.value.suggested_filename.endswith("-merchant-center-review.csv")
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-{connector_name}-integrations.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                if args.csv_merchant_feed:
                    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "csv_catalog_edge_cases.csv"
                    page.get_by_label("Catalog CSV", exact=True).set_input_files(str(fixture))
                    page.get_by_role("button", name="Preview CSV import", exact=True).click()
                else:
                    page.get_by_role("button", name="Run import dry run", exact=True).click()
                page.wait_for_url("**/integrations?**plan=**")
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("heading", name="Reviewed import plan", exact=True).is_visible()
                resource_label = "Posts" if args.wordpress else "Products"
                resource_row = page.locator(".i-counts tbody tr").filter(has_text=resource_label)
                assert resource_row.is_visible()
                assert resource_row.locator("td").all_inner_texts() == ["2", "2", "0", "0"]
                page.get_by_text("Warnings and unmapped items", exact=False).click()
                warning_text = "normalized rows" if args.csv_merchant_feed else "shortcode" if args.wordpress else "Archived gift wrap"
                assert page.get_by_text(warning_text, exact=False).is_visible()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-{connector_name}-dry-run.png"), full_page=True)
                page.get_by_label("I reviewed these counts, samples, and warnings.", exact=True).check()
                page.get_by_role("button", name="Apply this reviewed plan", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.get_by_text("Applied reviewed plan successfully:", exact=False).is_visible()
                assert page.locator(".i-state-applied").is_visible()
                if args.csv_merchant_feed:
                    for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                        page.set_viewport_size({"width": width, "height": height})
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                        page.screenshot(path=str(out / f"{device}-csv-completed.png"), full_page=True)
                checks.append({
                    "merchant": f"{connector_label} fixture dry run renders and the exact reviewed plan applies once",
                    "status": "passed",
                })
                if args.wordpress:
                    from app.models import ExternalMapping

                    with SessionLocal() as db:
                        article_mapping = db.scalar(select(ExternalMapping).where(
                            ExternalMapping.tenant_id == connector_site.tenant_id,
                            ExternalMapping.site_id == connector_site_id,
                            ExternalMapping.system == "wordpress",
                            ExternalMapping.resource_type == "post",
                            ExternalMapping.external_id == "101",
                        ))
                        if article_mapping is None:
                            raise RuntimeError("The imported WordPress article mapping is unavailable.")
                        imported_article_id = article_mapping.local_id
                    page.goto(
                        args.base
                        + f"/admin/sites/{connector_site_id}/build?page={imported_article_id}"
                    )
                    page.wait_for_load_state("networkidle")
                    preview = page.frame_locator("iframe.b-preview")
                    preview.locator("h1").wait_for(state="attached")
                    assert preview.locator("h1").text_content() == "A field guide to spring water"
                    cookie = preview.locator("#h-cookie")
                    cookie.evaluate("element => { element.hidden = true; }")
                    for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                        page.set_viewport_size({"width": width, "height": height})
                        if device == "mobile":
                            page.get_by_role("button", name="Preview", exact=True).click()
                            assert page.locator("iframe.b-preview").is_visible()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                        page.screenshot(
                            path=str(out / f"{device}-wordpress-imported-article-builder.png"),
                            full_page=True,
                        )
                    checks.append({
                        "merchant": "Imported WordPress article is visible in the FastShop builder",
                        "status": "passed",
                    })
                page.goto(args.base + "/admin/sites")
                page.set_viewport_size({"width": 1440, "height": 1000})
            if args.generation:
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("heading", name="Generate a site from a brief", exact=True).scroll_into_view_if_needed()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(out / "desktop-generation-brief.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(out / "mobile-generation-brief.png"), full_page=True)
                generated_name = "Moss & Kiln " + uuid4().hex[:6]
                page.get_by_label("Business name", exact=True).fill(generated_name)
                page.locator('select[name="kind"]').select_option("online shop")
                page.locator('textarea[name="audience"]').fill(
                    "People choosing useful, quietly expressive objects for compact homes"
                )
                page.locator('select[name="tone"]').select_option("warm and natural")
                page.get_by_role("button", name="Generate private draft", exact=True).click()
                page.wait_for_url("**/admin/sites/*/build?**")
                page.wait_for_load_state("networkidle")
                assert page.get_by_role("status").get_by_text(
                    "Private draft generated with guided presets.", exact=True
                ).is_visible()
                assert page.locator('select[name="section_id"] option').count() >= 4
                preview = page.frame_locator("iframe.b-preview")
                preview.locator("h1").wait_for(state="attached")
                assert generated_name in preview.locator("h1").text_content()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-generated-site-editor.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="Resolve imagery", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert "Imagery resolved:" in page.get_by_role("status").first.inner_text()
                preview_url = page.locator("iframe.b-preview").get_attribute("src")
                generated_home = context.new_page()
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    generated_home.set_viewport_size({"width": width, "height": height})
                    generated_home.goto(args.base + preview_url)
                    load_page_media(generated_home)
                    generated_images = generated_home.locator("main img")
                    assert generated_images.count() >= 2
                    assert generated_images.evaluate_all(
                        "images => images.every(image => image.src.includes('/site-media/'))"
                    )
                    assert generated_home.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth + 1"
                    )
                    generated_home.screenshot(
                        path=str(out / f"{device}-generated-home.png"), full_page=True
                    )
                generated_home.close()
                page.get_by_role("link", name="Media library", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.get_by_text("Image direction:", exact=False).count() >= 2
                assert page.locator(".e-card img").count() >= 2
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(
                        path=str(out / f"{device}-generated-media-library.png"),
                        full_page=True,
                    )
                checks.append({
                    "merchant": "guided brief creates a private draft, resolves owned placeholder images, exposes them in the media library and reruns resolution idempotently",
                    "status": "passed",
                })
                page.goto(args.base + "/admin/sites")
                page.set_viewport_size({"width": 1440, "height": 1000})
            slug = "browser-" + uuid4().hex[:10]
            page.locator('input[name="name"]').fill("Browser acceptance site")
            page.locator('input[name="slug"]').fill(slug)
            page.get_by_role("button", name="Create site", exact=True).click()
            page.wait_for_url("**/admin/sites/*")
            page.get_by_role("link", name="Browser acceptance site", exact=True).click()
            page.locator('input[name="title"]').fill("Our browser-tested home")
            page.locator('select[name="new_section"]').select_option("text")
            page.get_by_role("button", name="Save draft", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert page.locator("details.e-section").count() == 2
            page.locator("details.e-section").last.locator("summary").click()
            page.locator('textarea[name="section_1_heading"]').fill("Created without code")
            page.locator('textarea[name="section_1_body"]').fill("The merchant created this page, edited a section and published the result.")
            page.get_by_role("button", name="Publish page", exact=True).click()
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(out / "merchant-page-editor.png"), full_page=True)
            visitor = context.new_page()
            visitor.goto(args.base + f"/sites/{slug}/")
            assert visitor.get_by_text("Created without code", exact=True).is_visible()
            visitor.close()
            page.get_by_role("link", name="← All pages and settings").click()
            site_editor_url = page.url
            if args.refinement:
                page.goto(site_editor_url + "/build")
                preview = page.frame_locator("iframe.b-preview")
                original_heading = preview.locator("h1").text_content()
                page.get_by_label("Describe your site or a change").fill("Headline: Rejected browser preview")
                page.get_by_role("button", name="Prepare update", exact=True).click()
                page.get_by_role("heading", name="Review block edits", exact=True).wait_for()
                assert preview.locator("h1").text_content() == original_heading
                page.get_by_role("button", name="Reject", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert preview.locator("h1").text_content() == original_heading

                accepted_heading = "Accepted from the guided preview"
                page.get_by_label("Describe your site or a change").fill("Headline: " + accepted_heading)
                page.get_by_role("button", name="Prepare update", exact=True).click()
                page.get_by_role("heading", name="Review block edits", exact=True).last.wait_for()
                preview.locator("h1").wait_for(state="visible")
                assert preview.locator("h1").text_content() == original_heading
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-refinement-preview.png"), full_page=True)
                page.get_by_role("button", name="Accept all edits", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert preview.locator("h1").text_content() == accepted_heading
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.screenshot(path=str(out / "desktop-refinement-accepted.png"), full_page=True)
                checks.append({
                    "merchant": "guided block preview rejects without changes and accepts the exact heading edit",
                    "status": "passed",
                })
                page.goto(site_editor_url)
            page.get_by_role("link", name="Menus", exact=True).click()
            header_menu = page.locator(".e-card").filter(has=page.get_by_role("heading", name="Header", exact=True))
            header_menu.get_by_text("Add an item", exact=True).click()
            add_form = header_menu.locator("details").last
            add_form.get_by_label("Label (en)", exact=True).fill("Our introduction")
            add_form.get_by_label("Target type", exact=True).select_option("anchor")
            add_form.get_by_label("Page", exact=True).select_option("/")
            add_form.get_by_label("Block (for block anchors)", exact=True).select_option(index=1)
            add_form.get_by_role("button", name="Add item", exact=True).click()
            page.wait_for_load_state("networkidle")
            row = header_menu.locator("details").filter(has=page.get_by_text("5. Our introduction", exact=True))
            row.locator("summary").click()
            row.get_by_role("button", name="Move up", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert header_menu.get_by_text("4. Our introduction", exact=True).is_visible()
            header_menu.get_by_role("button", name="Publish menu", exact=True).click()
            page.wait_for_load_state("networkidle")
            header_menu.locator("details").first.locator("summary").click()
            page.screenshot(path=str(out / "desktop-menu-editor.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(out / "mobile-menu-editor.png"), full_page=True)
            visitor = context.new_page()
            visitor.goto(args.base + f"/sites/{slug}/")
            link = visitor.locator("#site-navigation").get_by_role("link", name="Our introduction", exact=True)
            fragment = link.get_attribute("href").split("#")[1]
            assert visitor.locator("#" + fragment).count() == 1
            visitor.close()
            checks.append({"merchant": "menu anchor add, reorder, publish, public target and desktop/mobile editor", "status": "passed"})
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(site_editor_url)
            page.get_by_role("link", name="Media library", exact=True).click()
            page.locator('input[name="title"]').first.fill("Acceptance image")
            page.locator('input[name="alt"]').first.fill("Water texture for browser acceptance")
            page.locator('input[type="file"]').set_input_files("static/h24you/water-placeholder.webp")
            page.get_by_role("button", name="Upload image", exact=True).click()
            page.wait_for_load_state("networkidle")
            assert page.get_by_role("heading", name="Acceptance image", exact=True).is_visible()
            page.screenshot(path=str(out / "merchant-media-library.png"), full_page=True)
            media_url = page.get_by_label("Image URL — use in a section").input_value()
            library_url = page.url
            page.goto(site_editor_url)
            page.get_by_role("link", name="Our browser-tested home", exact=True).click()
            page.locator('select[name="new_section"]').select_option("product")
            page.get_by_role("button", name="Save draft", exact=True).click()
            page.wait_for_load_state("networkidle")
            for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.goto(library_url)
                assert page.locator(".e-card img").evaluate("i => i.complete && i.naturalWidth > 0")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(out / f"{device}-media-library.png"), full_page=True)
                page.goto(site_editor_url)
                page.get_by_role("link", name="Our browser-tested home", exact=True).click()
                section = page.locator("details.e-section").first
                if section.get_attribute("open") is None:
                    section.locator("summary").click()
                section.get_by_label("Pick from library: image", exact=True).select_option(media_url)
                assert section.get_by_label("Image URL", exact=True).input_value() == media_url
                section.get_by_label("Pick from library: poster", exact=True).select_option(media_url)
                assert section.get_by_label("Poster URL", exact=True).input_value() == media_url
                product = page.locator("details.e-section").last
                if product.get_attribute("open") is None:
                    product.locator("summary").click()
                product.get_by_label("Gallery image URLs (one per line)", exact=True).fill("")
                product.get_by_label("Pick from library: gallery", exact=True).select_option(media_url)
                product.get_by_label("Pick from library: gallery", exact=True).select_option(media_url)
                assert product.get_by_label("Gallery image URLs (one per line)", exact=True).input_value() == media_url + "\n" + media_url
                product.locator("summary").click()
                section.get_by_label("Alt text", exact=True).fill("Reusable water image " + device)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(out / f"{device}-media-picker.png"), full_page=True)
                page.get_by_role("button", name="Save draft", exact=True).click()
                page.wait_for_load_state("networkidle")
                assert page.locator('input[name="section_0_image"]').input_value() == media_url
                assert page.locator('input[name="section_0_alt"]').input_value() == "Reusable water image " + device
            page.goto(library_url)
            page.get_by_role("button", name="Delete media", exact=True).click()
            assert page.get_by_role("status").inner_text().startswith("Cannot delete referenced media")
            checks.append({"merchant": "media upload, browse, pick and save alt desktop/mobile; referenced deletion refused", "status": "passed"})
            if args.embeds:
                context.route("https://embed.example.test/**", lambda route: route.fulfill(
                    content_type="text/html",
                    body="<!doctype html><title>Embed fixture</title><style>body{font:24px sans-serif;padding:32px}</style><p>Secure embed fixture</p>",
                ))
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(args.base + "/admin/sites")
                h24_card = page.locator(".e-card").filter(has=page.get_by_role("heading", name="H2 4 You", exact=True))
                h24_card.get_by_role("link", name="Open editor →", exact=True).click()
                h24_editor_url = page.url
                page.get_by_role("link", name="Follow the questions.", exact=True).click()
                page.locator('select[name="new_section"]').select_option("embed")
                page.get_by_role("button", name="Save draft", exact=True).click()
                page.wait_for_load_state("networkidle")
                embed_section = page.locator("details.e-section").last
                embed_section.locator("summary").click()
                embed_section.locator('textarea[name$="_heading"]').fill("Watch the research briefing")
                embed_section.locator('textarea[name$="_body"]').fill("A provider-neutral HTTPS embed rendered inside the strict storefront sandbox.")
                embed_section.get_by_label("Embed URL (HTTPS)", exact=True).fill("https://embed.example.test/widget")
                page.get_by_role("button", name="Publish page", exact=True).click()
                page.wait_for_load_state("networkidle")
                page.goto(h24_editor_url + "/snippets")
                head_card = page.locator(".e-card").filter(has=page.get_by_role("heading", name="Document head", exact=True))
                head_card.get_by_label("Snippet markup", exact=True).fill('<meta name="phase1c-browser" content="published">')
                head_card.get_by_label("Enable this placement", exact=True).check()
                head_card.get_by_role("button", name="Publish snippet", exact=True).click()
                page.wait_for_load_state("networkidle")
                foot_card = page.locator(".e-card").filter(has=page.get_by_role("heading", name="After footer", exact=True))
                foot_card.get_by_label("Snippet markup", exact=True).fill("<script>window.__phase1cSnippetLoaded = true</script>")
                foot_card.get_by_label("Enable this placement", exact=True).check()
                foot_card.get_by_role("button", name="Publish snippet", exact=True).click()
                page.wait_for_load_state("networkidle")
                snippets_url = page.url
                for device, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(snippets_url)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    page.screenshot(path=str(out / f"{device}-snippet-editor.png"), full_page=True)
                    page.goto(args.base + "/sites/h24you/pages/science")
                    page.wait_for_load_state("networkidle")
                    assert page.locator('head meta[name="phase1c-browser"]').get_attribute("content") == "published"
                    frame = page.locator('iframe[src="https://embed.example.test/widget"]')
                    assert frame.is_visible()
                    assert frame.get_attribute("sandbox") == "allow-scripts"
                    assert frame.get_attribute("referrerpolicy") == "no-referrer"
                    assert "allow-same-origin" not in frame.get_attribute("sandbox")
                    frame.scroll_into_view_if_needed()
                    embedded_fixture = page.frame_locator('iframe[src="https://embed.example.test/widget"]').get_by_text(
                        "Secure embed fixture", exact=True,
                    )
                    embedded_fixture.wait_for(state="visible")
                    assert page.evaluate("window.__phase1cSnippetLoaded") is None
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    load_page_media(page)
                    page.screenshot(path=str(out / f"{device}-embed-block.png"), full_page=True)
                page.get_by_role("button", name="Cookie preferences", exact=True).click()
                page.locator("#h-cookie summary").click()
                page.locator("#consent-analytics").check()
                page.locator('[data-consent="custom"]').click()
                page.wait_for_function("window.__phase1cSnippetLoaded === true")
                checks.append({"merchant": "embed desktop/mobile plus published and consent-gated snippets", "status": "passed"})
            context.close()
        browser.close()
    report = {"base": args.base, "checks": checks, "failures": failures}
    (out / ("verification-merchant.json" if args.merchant_only else "verification.json")).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": failures, "output": str(out)}))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

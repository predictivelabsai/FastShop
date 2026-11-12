"""Verify Liquid typography defaults, editor preview and draft persistence."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/typography"
FRAME_ROOT = ".h-site[data-pack='liquid']"
DESKTOP_VIEWPORT = {"width": 2200, "height": 1100}


def editor_url(site_id, page_id):
    return f"/admin/sites/{site_id}/editor?page={page_id}"


def login(page, email, password):
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(email)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    expect(page.locator(".a-shell")).to_be_visible()


def open_theme(page):
    page.locator('[data-mode="theme"]').click()
    expect(page.locator("#left-title")).to_have_text("Theme settings")
    field = page.locator('[data-setting="typography.roles.footer_heading.size"]')
    expect(field).to_be_visible()
    return field


def canvas(page):
    frame = page.frame_locator("#editor-canvas")
    expect(frame.locator(FRAME_ROOT)).to_be_visible()
    expect(frame.locator(".liquid-footer-title")).to_be_visible()
    return frame


def select_device(page, device):
    page.locator(f'[data-device="{device}"]').click()
    expect(page.locator("#editor-canvas")).to_have_attribute("data-device", device)
    expect(page.locator("#canvas-device")).to_have_text(device.title())


def element_typography(frame, selector):
    element = frame.locator(selector).first
    expect(element).to_be_attached()
    return element.evaluate(
        """element => {
            const style = getComputedStyle(element);
            return {
              font_family: style.fontFamily,
              font_size: style.fontSize,
              font_weight: style.fontWeight,
              computed_line_height: style.lineHeight,
              letter_spacing: style.letterSpacing,
            };
          }"""
    )


def typography_snapshot(frame):
    root = frame.locator(FRAME_ROOT)
    expect(root).to_be_visible()
    roles = root.evaluate(
        """element => {
            const style = getComputedStyle(element);
            const roles = [
              "h1", "h2", "h3", "body", "small", "eyebrow", "button", "nav",
              "footer-heading", "footer-body",
            ];
            return Object.fromEntries(roles.map(role => {
              const prefix = `--liquid-type-${role}`;
              return [role.replaceAll("-", "_"), {
                size: style.getPropertyValue(`${prefix}-size`).trim(),
                mobile_size: style.getPropertyValue(`${prefix}-size-mobile`).trim(),
                weight: style.getPropertyValue(`${prefix}-weight`).trim(),
                line_height: style.getPropertyValue(`${prefix}-line-height`).trim(),
                letter_spacing: style.getPropertyValue(`${prefix}-letter-spacing`).trim(),
              }];
            }));
          }"""
    )
    components = {
        "hero": element_typography(frame, ".metal-hero-title"),
        "body": element_typography(frame, FRAME_ROOT),
        "button": element_typography(frame, ".metal-cta"),
        "nav": element_typography(frame, '.metal-header nav[aria-label="Main"] a'),
        "footer_title": element_typography(frame, ".liquid-footer-title"),
        "footer_body": element_typography(frame, ".liquid-footer-title + .fs-rich-content"),
    }
    viewport = root.evaluate("element => ({width: innerWidth, height: innerHeight})")
    return {"viewport": viewport, "roles": roles, "components": components}


def assert_default_typography(snapshot, device):
    expected_roles = {
        "h1": ("80px", "56px", "400", "1.08", "0em"),
        "h2": ("48px", "40px", "400", "1", "0em"),
        "h3": ("24px", "24px", "400", "1.2", "0em"),
        "body": ("16px", "16px", "400", "1.5", "0em"),
        "small": ("12px", "12px", "400", "1.5", "0em"),
        "eyebrow": ("11px", "11px", "600", "1.5", "0.2em"),
        "button": ("14px", "14px", "600", "1.5", "0em"),
        "nav": ("14px", "30px", "400", "1.5", "0em"),
        "footer_heading": ("36px", "36px", "400", "1.1", "0em"),
        "footer_body": ("16px", "16px", "400", "1.5", "0em"),
    }
    actual_roles = {
        role: (values["size"], values["mobile_size"], values["weight"],
               values["line_height"], values["letter_spacing"])
        for role, values in snapshot["roles"].items()
    }
    if actual_roles != expected_roles:
        raise AssertionError(f"Unexpected Liquid typography token defaults: {actual_roles}")

    expected_components = {
        "hero": ('Liquid Italiana', "80px" if device == "desktop" else "56px", "400",
                 "86.4px" if device == "desktop" else "60.48px"),
        "body": ('Liquid Quicksand', "16px", "400", "24px"),
        "button": ('Liquid Quicksand', "14px", "600", "21px"),
        "nav": ('Liquid Quicksand', "14px", "400", "21px"),
        "footer_title": ('Liquid Fraunces', "36px", "400", "39.6px"),
        "footer_body": ('Liquid Quicksand', "16px", "400", "24px"),
    }
    for name, (family, size, weight, line_height) in expected_components.items():
        values = snapshot["components"][name]
        if (
            family not in values["font_family"]
            or values["font_size"] != size
            or values["font_weight"] != weight
            or values["computed_line_height"] != line_height
            or values["letter_spacing"] != "normal"
        ):
            raise AssertionError(f"Unexpected default {name} typography: {values}")

    width = snapshot["viewport"]["width"]
    if device == "desktop" and width < 1440:
        raise AssertionError(f"Desktop canvas is too narrow to test desktop styles: {width}px")
    if device == "mobile" and width != 390:
        raise AssertionError(f"Mobile canvas width should be 390px, got {width}px")


def capture(page, frame, filename, setting):
    page.locator(f'[data-setting="{setting}"]').scroll_into_view_if_needed()
    frame.locator(".liquid-footer-title").scroll_into_view_if_needed()
    page.wait_for_timeout(100)
    page.screenshot(path=str(OUTPUT / filename), full_page=True)


def assert_live_font(frame, selector, expected_family):
    family = element_typography(frame, selector)["font_family"]
    if expected_family not in family:
        raise AssertionError(f"Expected {expected_family!r} in {selector}, got {family!r}")


def wait_for_editor(page):
    expect(page.locator("#save-status")).to_have_text("Saved")
    expect(page.locator("#editor-error")).to_be_hidden()
    return canvas(page)


def main():
    os.environ.pop("FASTSHOP_BROWSER_DB_URL", None)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fastshop-typography-") as data_dir:
        isolate(data_dir, port)
        import uvicorn
        from sqlalchemy import select

        from app import content
        from app.db import SessionLocal, engine
        from app.main import app
        from app.models import Site

        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == "h24you-liquid"))
            if site is None:
                raise AssertionError("Isolated application did not seed h24you-liquid")
            home = next(page for page in content.site_pages(db, site) if page.path == "/")
            site_id, home_id = site.id, home.id

        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        browser = None
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Isolated typography server failed to start")
                thread.join(0.05)

            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except Error:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(
                    base_url=f"http://127.0.0.1:{port}",
                    viewport=DESKTOP_VIEWPORT,
                    reduced_motion="reduce",
                )
                page = context.new_page()
                page.set_default_timeout(20_000)
                login(page, EMAIL, PASSWORD)
                editor = editor_url(site_id, home_id)
                page.goto(editor)
                frame = wait_for_editor(page)
                open_theme(page)
                select_device(page, "desktop")

                defaults_desktop = typography_snapshot(frame)
                assert_default_typography(defaults_desktop, "desktop")
                capture(page, frame, "defaults-desktop.png", "typography.roles.footer_heading.size")

                select_device(page, "mobile")
                frame = canvas(page)
                defaults_mobile = typography_snapshot(frame)
                assert_default_typography(defaults_mobile, "mobile")
                capture(page, frame, "defaults-mobile.png", "typography.roles.footer_heading.mobile_size")

                select_device(page, "desktop")
                frame = canvas(page)
                field = open_theme(page)
                expect(field).to_have_value("36px")
                mobile_field = page.locator(
                    '[data-setting="typography.roles.footer_heading.mobile_size"]'
                )
                expect(mobile_field).to_have_value("36px")
                display_font = page.locator('[data-setting="typography.display_font"]')
                body_font = page.locator('[data-setting="typography.body_font"]')
                ui_font = page.locator('[data-setting="typography.ui_font"]')
                display_font.select_option("fraunces")
                page.wait_for_timeout(100)
                assert_live_font(frame, ".metal-hero-title", "Liquid Fraunces")
                display_font.select_option("theme")
                body_font.select_option("system_serif")
                page.wait_for_timeout(100)
                assert_live_font(frame, FRAME_ROOT, "Georgia")
                body_font.select_option("quicksand")
                ui_font.select_option("italiana")
                page.wait_for_timeout(100)
                assert_live_font(frame, ".metal-cta", "Liquid Italiana")
                ui_font.select_option("quicksand")
                csrf_token = page.evaluate("window.FASTSHOP_EDITOR.csrf_token")
                if not csrf_token:
                    raise AssertionError("The editor did not expose a CSRF token")
                theme_requests = []

                def capture_theme_request(request):
                    if (
                        request.method == "PUT"
                        and urlsplit(request.url).path.endswith("/editor/api/theme-settings")
                    ):
                        theme_requests.append(
                            {"headers": request.headers, "payload": request.post_data_json}
                        )

                page.on("request", capture_theme_request)
                field.fill("28px")
                expect(page.locator("#save-status")).to_have_text("Unsaved changes")
                expect(frame.locator(".liquid-footer-title")).to_have_css("font-size", "28px")
                before_save = typography_snapshot(frame)
                if before_save["roles"]["footer_heading"]["size"] != "28px":
                    raise AssertionError("The iframe did not receive the unsaved footer heading size")

                select_device(page, "mobile")
                frame = canvas(page)
                mobile_field.fill("24px")
                expect(frame.locator(".liquid-footer-title")).to_have_css("font-size", "24px")
                before_save_mobile = typography_snapshot(frame)
                if before_save_mobile["roles"]["footer_heading"]["mobile_size"] != "24px":
                    raise AssertionError("The iframe did not receive the unsaved mobile footer heading size")

                with page.expect_response(
                    lambda response: response.request.method == "PUT"
                    and urlsplit(response.url).path.endswith("/editor/api/theme-settings")
                ) as response_info:
                    page.locator("#save-theme").click()
                response = response_info.value
                if response.status != 200:
                    raise AssertionError(f"Theme settings save failed (HTTP {response.status})")
                expect(page.locator("#save-status")).to_have_text("Saved")
                if len(theme_requests) != 1:
                    raise AssertionError(f"Expected one theme settings request, got {len(theme_requests)}")
                request = theme_requests[0]
                if request["headers"].get("x-csrf-token") != csrf_token:
                    raise AssertionError("The theme settings request did not carry the editor CSRF token")
                saved_size = request["payload"].get("theme_settings", {}).get("typography", {}).get(
                    "roles", {}
                ).get("footer_heading", {}).get("size")
                if saved_size != "28px":
                    raise AssertionError(f"Theme settings payload saved {saved_size!r}, not '28px'")
                saved_mobile_size = request["payload"]["theme_settings"]["typography"]["roles"][
                    "footer_heading"
                ]["mobile_size"]
                if saved_mobile_size != "24px":
                    raise AssertionError(
                        f"Theme settings payload saved mobile {saved_mobile_size!r}, not '24px'"
                    )

                page.goto(editor)
                frame = wait_for_editor(page)
                select_device(page, "desktop")
                reloaded_field = open_theme(page)
                expect(reloaded_field).to_have_value("28px")
                expect(frame.locator(".liquid-footer-title")).to_have_css("font-size", "28px")
                edited_desktop = typography_snapshot(frame)
                if edited_desktop["roles"]["footer_heading"]["size"] != "28px":
                    raise AssertionError("The saved desktop footer heading size did not persist")
                capture(page, frame, "edited-desktop.png", "typography.roles.footer_heading.size")

                select_device(page, "mobile")
                frame = canvas(page)
                expect(open_theme(page)).to_have_value("28px")
                expect(mobile_field).to_have_value("24px")
                expect(frame.locator(".liquid-footer-title")).to_have_css("font-size", "24px")
                edited_mobile = typography_snapshot(frame)
                if edited_mobile["roles"]["footer_heading"]["mobile_size"] != "24px":
                    raise AssertionError("The saved mobile footer heading size did not persist")
                capture(page, frame, "edited-mobile.png", "typography.roles.footer_heading.mobile_size")

                verification = {
                    "status": "passed",
                    "defaults": {"desktop": defaults_desktop, "mobile": defaults_mobile},
                    "edited": {
                        "desktop": edited_desktop,
                        "mobile": edited_mobile,
                        "footer_heading_size": "28px",
                        "footer_heading_mobile_size": "24px",
                    },
                    "theme_settings_request": {
                        "csrf_header": "present",
                        "footer_heading_size": saved_size,
                        "footer_heading_mobile_size": saved_mobile_size,
                    },
                    "screenshots": [
                        "defaults-desktop.png",
                        "defaults-mobile.png",
                        "edited-desktop.png",
                        "edited-mobile.png",
                    ],
                }
                (OUTPUT / "verification.json").write_text(
                    json.dumps(verification, indent=2, sort_keys=True), encoding="utf-8"
                )
                print(json.dumps({"status": "passed", "output": str(OUTPUT)}))
                context.close()
                browser.close()
                browser = None
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Error:
                    pass
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

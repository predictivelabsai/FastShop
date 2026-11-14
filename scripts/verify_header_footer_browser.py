"""Verify Liquid header and footer controls in the visual editor and live storefront."""

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
OUTPUT = ROOT / "output/playwright/header-footer"
FRAME_ROOT = ".h-site[data-pack='liquid'][data-site]"
DESKTOP_VIEWPORT = {"width": 2200, "height": 1100}

DEFAULT_PROPERTIES = {
    "--liquid-header-background": "oklch(0.19 0.02 285)",
    "--liquid-header-text": "oklch(0.96 0.007 255)",
    "--liquid-header-link": "oklch(0.96 0.007 255)",
    "--liquid-header-link-hover": "oklch(0.96 0.007 255)",
    "--liquid-header-border": "oklch(0.43 0.03 305)",
    "--liquid-header-padding-y": "16px",
    "--liquid-header-logo-size": "144px",
    "--liquid-header-sticky": "1",
    "--liquid-header-mobile-menu-background": "oklch(0.25 0.025 305)",
    "--liquid-header-mobile-menu-text": "oklch(0.96 0.007 255)",
    "--liquid-header-mobile-menu-link": "oklch(0.96 0.007 255)",
    "--liquid-header-mobile-menu-link-hover": "oklch(0.96 0.007 255)",
    "--liquid-announcement-background": "oklch(0.25 0.025 305)",
    "--liquid-announcement-text": "oklch(0.96 0.007 255)",
    "--liquid-announcement-title-colour": (
        "linear-gradient(115deg, oklch(0.72 0.07 35), oklch(0.96 0.025 50) 28%, "
        "oklch(0.8 0.065 35) 48%, oklch(0.93 0.04 55) 70%, oklch(0.7 0.07 30))"
    ),
    "--liquid-announcement-request-colour": "oklch(0.83 0.027 20)",
    "--liquid-footer-background": "oklch(0.15 0.018 285)",
    "--liquid-footer-text": "oklch(0.96 0.007 255)",
    "--liquid-footer-link": "oklch(0.96 0.007 255)",
    "--liquid-footer-link-hover": "oklch(0.96 0.007 255)",
    "--liquid-footer-divider": "color-mix(in oklch,oklch(0.96 0.007 255) 15%,transparent)",
    "--liquid-footer-padding-y": "64px",
    "--liquid-footer-logo-size": "144px",
    "--liquid-footer-column-gap": "64px",
    "--liquid-footer-newsletter-background": "transparent",
    "--liquid-footer-newsletter-input-background": "transparent",
    "--liquid-footer-newsletter-input-text": "oklch(0.96 0.007 255)",
    "--liquid-footer-newsletter-input-border": "oklch(0.43 0.03 305)",
    "--liquid-footer-newsletter-button-background": (
        "linear-gradient(115deg, oklch(0.72 0.07 35), oklch(0.96 0.025 50) 28%, "
        "oklch(0.8 0.065 35) 48%, oklch(0.93 0.04 55) 70%, oklch(0.7 0.07 30))"
    ),
    "--liquid-footer-newsletter-button-text": "oklch(0.18 0.025 258)",
    "--liquid-footer-newsletter-heading-size": "clamp(22px,2.2vw,32px)",
    "--liquid-footer-bottom-background": "transparent",
    "--liquid-footer-bottom-legal-text": "oklch(0.96 0.007 255)",
    "--liquid-footer-bottom-legal-link": "oklch(0.96 0.007 255)",
    "--liquid-footer-bottom-disclaimer": "oklch(0.96 0.007 255)",
    "--liquid-footer-bottom-divider": "color-mix(in oklch,oklch(0.96 0.007 255) 15%,transparent)",
}

EDITED_SETTINGS = {
    "header": {
        "background": "#123456", "text": "#f8fafc", "link": "#dbeafe",
        "link_hover": "#fef3c7", "border": "#456789", "padding_y": "24px",
        "logo_size": "168px", "sticky": False,
    },
    "announcement": {"background": "#234567", "text": "#f8fafc"},
    "footer": {
        "background": "#102030", "text": "#e2e8f0", "link": "#bae6fd",
        "link_hover": "#fef3c7", "divider": "#345678", "padding_y": "72px",
        "logo_size": "156px", "column_gap": "48px",
    },
    "newsletter": {
        "background": "#203040", "input_background": "#ffffff",
        "input_text": "#102030", "input_border": "#456789",
        "button_background": "#dbeafe", "button_text": "#102030",
        "heading_size": "standard",
    },
    "bottom": {
        "background": "#09111a", "legal_text": "#d8e1ea", "legal_link": "#ffffff",
        "disclaimer": "#b9c6d2", "divider": "#2d2e38",
    },
}

EDITED_PROPERTIES = {
    "--liquid-header-background": "#123456",
    "--liquid-header-text": "#f8fafc",
    "--liquid-header-link": "#dbeafe",
    "--liquid-header-link-hover": "#fef3c7",
    "--liquid-header-border": "#456789",
    "--liquid-header-padding-y": "24px",
    "--liquid-header-logo-size": "168px",
    "--liquid-header-sticky": "0",
    "--liquid-header-mobile-menu-background": "#123456",
    "--liquid-header-mobile-menu-text": "#f8fafc",
    "--liquid-header-mobile-menu-link": "#dbeafe",
    "--liquid-header-mobile-menu-link-hover": "#fef3c7",
    "--liquid-announcement-background": "#234567",
    "--liquid-announcement-text": "#f8fafc",
    "--liquid-announcement-title-colour": "#f8fafc",
    "--liquid-announcement-request-colour": "#f8fafc",
    "--liquid-footer-background": "#102030",
    "--liquid-footer-text": "#e2e8f0",
    "--liquid-footer-link": "#bae6fd",
    "--liquid-footer-link-hover": "#fef3c7",
    "--liquid-footer-divider": "#345678",
    "--liquid-footer-padding-y": "72px",
    "--liquid-footer-logo-size": "156px",
    "--liquid-footer-column-gap": "48px",
    "--liquid-footer-newsletter-background": "#203040",
    "--liquid-footer-newsletter-input-background": "#ffffff",
    "--liquid-footer-newsletter-input-text": "#102030",
    "--liquid-footer-newsletter-input-border": "#456789",
    "--liquid-footer-newsletter-button-background": "#dbeafe",
    "--liquid-footer-newsletter-button-text": "#102030",
    "--liquid-footer-newsletter-heading-size": "clamp(28px,3vw,40px)",
    "--liquid-footer-bottom-background": "#09111a",
    "--liquid-footer-bottom-legal-text": "#d8e1ea",
    "--liquid-footer-bottom-legal-link": "#ffffff",
    "--liquid-footer-bottom-disclaimer": "#b9c6d2",
    "--liquid-footer-bottom-divider": "#345678",
}

EDITED_INPUTS = EDITED_SETTINGS | {
    "bottom": {key: value for key, value in EDITED_SETTINGS["bottom"].items() if key != "divider"},
}


def editor_url(site_id, page_id):
    return f"/admin/sites/{site_id}/editor?page={page_id}"


def login(page, email, password):
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(email)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")
    expect(page.locator(".a-shell")).to_be_visible()


def canvas(page):
    frame = page.frame_locator("#editor-canvas")
    expect(frame.locator(FRAME_ROOT)).to_be_visible()
    expect(frame.locator(".metal-header")).to_be_visible()
    expect(frame.locator(".metal-footer")).to_be_visible()
    expect(frame.locator(".fs-editor-tools")).to_have_count(1)
    return frame


def wait_for_editor(page):
    expect(page.locator("#save-status")).to_have_text("Saved")
    expect(page.locator("#editor-error")).to_be_hidden()
    return canvas(page)


def assert_canvas_module_parses(page):
    """Parse the ES module and its imports in-browser before relying on the canvas."""
    page.evaluate(
        """async () => {
            await import(`/static/editor/canvas.js?header-footer-parse=${Date.now()}`);
            return true;
        }"""
    )


def select_device(page, device):
    page.locator(f'[data-device="{device}"]').click()
    expect(page.locator("#editor-canvas")).to_have_attribute("data-device", device)
    expect(page.locator("#canvas-device")).to_have_text(device.title())


def reset_canvas_scroll(frame):
    frame.locator(FRAME_ROOT).evaluate(
        """() => {
            window.scrollTo(0, 0);
            window.dispatchEvent(new Event("scroll"));
        }"""
    )


def open_theme(page):
    page.locator('[data-mode="theme"]').click()
    expect(page.locator("#left-title")).to_have_text("Theme settings")
    panel = page.locator(".theme-header-footer")
    expect(panel).to_be_visible()
    if not panel.evaluate("element => element.open"):
        panel.locator("summary").click()
    sections = panel.locator(".theme-header-footer-section")
    expect(sections).to_have_count(5)
    for index in range(5):
        section = sections.nth(index)
        if not section.evaluate("element => element.open"):
            section.locator("summary").click()
    expect(page.locator('[data-setting="header_footer.header.background"]')).to_be_visible()
    expect(page.locator('[data-setting="header_footer.bottom.disclaimer"]')).to_be_visible()


def set_setting(page, path, value):
    field = page.locator(f'[data-setting="{path}"]')
    expect(field).to_be_visible()
    if isinstance(value, bool):
        if value:
            field.check()
        else:
            field.uncheck()
    elif field.get_attribute("type") == "color":
        field.evaluate(
            """(element, value) => {
                element.value = value;
                element.dispatchEvent(new Event("input", {bubbles: true}));
            }""",
            value,
        )
    elif field.evaluate("element => element.tagName") == "SELECT":
        field.select_option(value)
    else:
        field.fill(value)
    if isinstance(value, bool):
        assert field.is_checked() is value
    else:
        expect(field).to_have_value(value)


def chrome_snapshot(root):
    names = list(DEFAULT_PROPERTIES)
    return root.evaluate(
        """(element, properties) => {
            const style = getComputedStyle(element);
            const select = (selector) => {
                const item = element.querySelector(selector);
                if (!item) throw new Error(`Missing chrome element: ${selector}`);
                return getComputedStyle(item);
            };
            const topBars = select(".metal-top-bars");
            const header = select(".metal-header");
            const announcement = select(".metal-offer");
            const footer = select(".metal-footer");
            const footerGrid = select(".liquid-footer-grid");
            const headerLogo = select(".liquid-header-logo");
            const footerLogo = select(".liquid-footer-logo");
            const newsletter = select(".liquid-newsletter-block");
            const input = select(".liquid-email-row input");
            const button = select(".liquid-email-row button");
            const bottom = select(".liquid-footer-bottom");
            return {
                properties: Object.fromEntries(properties.map(name => [name, style.getPropertyValue(name).trim()])),
                sticky: element.dataset.headerSticky,
                top_bars: {position: topBars.position, background: topBars.backgroundColor, color: topBars.color},
                header: {background: header.backgroundColor, color: header.color, padding_top: select(".liquid-header-row").paddingTop},
                announcement: {background: announcement.backgroundColor, color: announcement.color},
                footer: {background: footer.backgroundColor, color: footer.color},
                footer_grid: {padding_top: footerGrid.paddingTop, column_gap: footerGrid.columnGap},
                logos: {header_width: headerLogo.width, footer_width: footerLogo.width},
                newsletter: {
                    background: newsletter.backgroundColor,
                    input_background: input.backgroundColor,
                    input_border: input.borderTopColor,
                    input_text: input.color,
                    button_background: button.backgroundColor,
                    button_text: button.color,
                },
                bottom: {background: bottom.backgroundColor, border: bottom.borderTopColor, color: bottom.color},
            };
        }""",
        names,
    )


def mobile_menu_snapshot(page):
    root = page.locator(FRAME_ROOT)
    toggle = root.locator("button[data-liquid-menu]")
    menu = root.locator("#liquid-mobile-menu")
    expect(toggle).to_be_visible()
    toggle.click()
    expect(menu).to_be_visible()
    link = menu.locator("a:visible").first
    expect(link).to_be_visible()
    before_hover = link.evaluate("element => getComputedStyle(element).color")
    link.hover()
    return {
        "background": menu.evaluate("element => getComputedStyle(element).backgroundColor"),
        "text": menu.evaluate("element => getComputedStyle(element).color"),
        "link": before_hover,
        "link_hover": link.evaluate("element => getComputedStyle(element).color"),
    }


def assert_default_mobile_menu(snapshot, chrome):
    assert snapshot == {
        "background": chrome["announcement"]["background"],
        "text": chrome["announcement"]["color"],
        "link": chrome["announcement"]["color"],
        "link_hover": chrome["announcement"]["color"],
    }


def assert_edited_mobile_menu(snapshot):
    assert snapshot == {
        "background": "rgb(18, 52, 86)",
        "text": "rgb(248, 250, 252)",
        "link": "rgb(219, 234, 254)",
        "link_hover": "rgb(254, 243, 199)",
    }


def assert_default_chrome(snapshot, device):
    expected_properties = DEFAULT_PROPERTIES | {
        "--liquid-header-padding-y": "16px" if device == "desktop" else "10px",
    }
    if snapshot["properties"] != expected_properties:
        raise AssertionError(
            f"Unexpected default header and footer properties: {snapshot['properties']}"
        )
    assert snapshot["sticky"] == "true"
    assert snapshot["top_bars"]["position"] == "sticky"
    expected_header_padding = "16px" if device == "desktop" else "10px"
    if snapshot["header"]["padding_top"] != expected_header_padding:
        raise AssertionError(f"Unexpected default header padding: {snapshot['header']}")
    assert snapshot["footer_grid"]["padding_top"] == "64px"
    assert snapshot["footer_grid"]["column_gap"] == ("64px" if device == "desktop" else "40px")
    assert snapshot["logos"] == {"header_width": "144px", "footer_width": "144px"}


def assert_edited_chrome(snapshot, device):
    if snapshot["properties"] != EDITED_PROPERTIES:
        raise AssertionError(
            f"Unexpected edited header and footer properties: {snapshot['properties']}"
        )
    assert snapshot["sticky"] == "false"
    assert snapshot["top_bars"]["position"] == "static"
    assert snapshot["top_bars"]["background"] == "rgb(18, 52, 86)"
    assert snapshot["top_bars"]["color"] == "rgb(248, 250, 252)"
    assert snapshot["header"]["background"] == "rgb(18, 52, 86)"
    assert snapshot["header"]["color"] == "rgb(248, 250, 252)"
    if snapshot["header"]["padding_top"] != "24px":
        raise AssertionError(
            f"Unexpected edited header padding for {device}: {snapshot['header']}"
        )
    assert snapshot["announcement"]["background"] == "rgb(35, 69, 103)"
    assert snapshot["announcement"]["color"] == "rgb(248, 250, 252)"
    assert snapshot["footer"]["background"] == "rgb(16, 32, 48)"
    assert snapshot["footer"]["color"] == "rgb(226, 232, 240)"
    expected_footer_grid = {
        "padding_top": "72px", "column_gap": "48px" if device == "desktop" else "40px",
    }
    if snapshot["footer_grid"] != expected_footer_grid:
        raise AssertionError(
            f"Unexpected edited footer grid for {device}: {snapshot['footer_grid']}"
        )
    assert snapshot["logos"] == {"header_width": "168px", "footer_width": "156px"}
    assert snapshot["newsletter"] == {
        "background": "rgb(32, 48, 64)",
        "input_background": "rgb(255, 255, 255)",
        "input_border": "rgb(69, 103, 137)",
        "input_text": "rgb(16, 32, 48)",
        "button_background": "rgb(219, 234, 254)",
        "button_text": "rgb(16, 32, 48)",
    }
    assert snapshot["bottom"]["background"] == "rgb(9, 17, 26)"
    assert snapshot["bottom"]["border"] == "rgb(52, 86, 120)"
    assert snapshot["bottom"]["color"] == "rgb(216, 225, 234)"


def capture(page, frame, filename, setting):
    page.locator(f'[data-setting="{setting}"]').scroll_into_view_if_needed()
    frame.locator(".liquid-footer-title").scroll_into_view_if_needed()
    page.screenshot(path=str(OUTPUT / filename), full_page=True)


def wait_for_edited_preview(frame):
    expect(frame.locator(FRAME_ROOT)).to_have_attribute("data-header-sticky", "false")
    expect(frame.locator(".metal-top-bars")).to_have_css("background-color", "rgb(18, 52, 86)")
    expect(frame.locator(".metal-header")).to_have_css("background-color", "rgb(18, 52, 86)")
    expect(frame.locator(".metal-offer")).to_have_css("background-color", "rgb(35, 69, 103)")
    expect(frame.locator(".metal-footer")).to_have_css("background-color", "rgb(16, 32, 48)")


def assert_editor_settings(page):
    for section, fields in EDITED_SETTINGS.items():
        for field, expected in fields.items():
            locator = page.locator(f'[data-setting="header_footer.{section}.{field}"]')
            if isinstance(expected, bool):
                assert locator.is_checked() is expected
            else:
                expect(locator).to_have_value(expected)


def publish(page):
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlsplit(response.url).path.endswith("/editor/api/publish")
    ) as response_info:
        page.locator("#publish").click()
        page.locator("#confirm-publish").click()
    response = response_info.value
    if response.status != 200:
        raise AssertionError(f"Publishing header and footer settings failed (HTTP {response.status})")
    expect(page.locator("#save-status")).to_have_text("Published")


def main():
    os.environ.pop("FASTSHOP_BROWSER_DB_URL", None)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fastshop-header-footer-") as data_dir:
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
                    raise RuntimeError("Isolated header and footer server failed to start")
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
                assert_canvas_module_parses(page)
                frame = wait_for_editor(page)
                open_theme(page)

                select_device(page, "desktop")
                reset_canvas_scroll(frame)
                defaults_desktop = chrome_snapshot(frame.locator(FRAME_ROOT))
                assert_default_chrome(defaults_desktop, "desktop")
                capture(page, frame, "defaults-desktop.png", "header_footer.header.background")

                select_device(page, "mobile")
                frame = canvas(page)
                reset_canvas_scroll(frame)
                defaults_mobile = chrome_snapshot(frame.locator(FRAME_ROOT))
                assert_default_chrome(defaults_mobile, "mobile")
                capture(page, frame, "defaults-mobile.png", "header_footer.footer.column_gap")
                default_public = context.new_page()
                default_public.set_viewport_size({"width": 390, "height": 844})
                response = default_public.goto("/sites/h24you-liquid/")
                if response is None or response.status != 200:
                    raise AssertionError("The default mobile Liquid storefront did not return HTTP 200")
                default_mobile_menu = mobile_menu_snapshot(default_public)
                assert_default_mobile_menu(default_mobile_menu, defaults_mobile)
                default_public.close()

                select_device(page, "desktop")
                frame = canvas(page)
                reset_canvas_scroll(frame)
                open_theme(page)
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
                for section, fields in EDITED_INPUTS.items():
                    for field, value in fields.items():
                        set_setting(page, f"header_footer.{section}.{field}", value)
                expect(page.locator("#save-status")).to_have_text("Unsaved changes")
                wait_for_edited_preview(frame)
                previewed = chrome_snapshot(frame.locator(FRAME_ROOT))
                assert_edited_chrome(previewed, "desktop")

                with page.expect_response(
                    lambda response: response.request.method == "PUT"
                    and urlsplit(response.url).path.endswith("/editor/api/theme-settings")
                ) as response_info:
                    page.locator("#save-theme").click()
                response = response_info.value
                if response.status != 200:
                    raise AssertionError(f"Header and footer settings save failed (HTTP {response.status})")
                expect(page.locator("#save-status")).to_have_text("Saved")
                if len(theme_requests) != 1:
                    raise AssertionError(f"Expected one theme settings request, got {len(theme_requests)}")
                request = theme_requests[0]
                if request["headers"].get("x-csrf-token") != csrf_token:
                    raise AssertionError("The theme settings request did not carry the editor CSRF token")
                if request["payload"].get("theme_settings", {}).get("header_footer") != EDITED_SETTINGS:
                    raise AssertionError("The theme settings request did not contain every edited chrome setting")

                page.goto(editor)
                frame = wait_for_editor(page)
                open_theme(page)
                assert_editor_settings(page)
                select_device(page, "desktop")
                frame = canvas(page)
                reset_canvas_scroll(frame)
                edited_desktop = chrome_snapshot(frame.locator(FRAME_ROOT))
                assert_edited_chrome(edited_desktop, "desktop")
                capture(page, frame, "edited-desktop.png", "header_footer.header.padding_y")

                select_device(page, "mobile")
                frame = canvas(page)
                reset_canvas_scroll(frame)
                edited_mobile = chrome_snapshot(frame.locator(FRAME_ROOT))
                assert_edited_chrome(edited_mobile, "mobile")
                capture(page, frame, "edited-mobile.png", "header_footer.footer.column_gap")

                select_device(page, "desktop")
                publish(page)
                response = page.goto("/sites/h24you-liquid/")
                if response is None or response.status != 200:
                    raise AssertionError("The published Liquid storefront did not return HTTP 200")
                public_desktop = chrome_snapshot(page.locator(FRAME_ROOT))
                assert_edited_chrome(public_desktop, "desktop")

                page.set_viewport_size({"width": 390, "height": 844})
                response = page.reload()
                if response is None or response.status != 200:
                    raise AssertionError("The published mobile Liquid storefront did not return HTTP 200")
                public_mobile = chrome_snapshot(page.locator(FRAME_ROOT))
                assert_edited_chrome(public_mobile, "mobile")
                public_mobile_menu = mobile_menu_snapshot(page)
                assert_edited_mobile_menu(public_mobile_menu)
                if page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
                    raise AssertionError("Published mobile storefront has horizontal overflow")

                verification = {
                    "status": "passed",
                    "defaults": {"desktop": defaults_desktop, "mobile": defaults_mobile},
                    "edited": {"desktop": edited_desktop, "mobile": edited_mobile},
                    "published": {
                        "desktop": public_desktop,
                        "mobile": public_mobile,
                        "mobile_menu": public_mobile_menu,
                    },
                    "theme_settings_request": {
                        "csrf_header": "present",
                        "header_background": EDITED_SETTINGS["header"]["background"],
                        "footer_background": EDITED_SETTINGS["footer"]["background"],
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

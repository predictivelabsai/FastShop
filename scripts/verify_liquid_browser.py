"""Liquid browser evidence on a deliberately isolated local FastShop instance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5025")
    parser.add_argument("--output", default="output/playwright/h24you-liquid")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    base = args.base.rstrip("/") + "/sites/h24you-liquid"
    checks, errors = [], []

    def check(condition, label):
        checks.append({"check": label, "passed": bool(condition)})
        if not condition:
            raise AssertionError(label)

    def media(page):
        page.evaluate("document.fonts.ready")
        page.evaluate("document.querySelectorAll('img').forEach(i => i.loading='eager')")
        page.wait_for_function(
            "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
        )
        for y in range(0, page.evaluate("document.documentElement.scrollHeight"), 700):
            page.evaluate("(y) => scrollTo(0,y)", y)
            page.wait_for_timeout(40)
        page.evaluate("scrollTo(0,0)")
        page.wait_for_timeout(200)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for width in (375, 768, 1024, 1440):
            context = browser.new_context(
                viewport={"width": width, "height": 1000}, reduced_motion="reduce", has_touch=True
            )
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            for route, name in (
                ("/", "home"),
                ("/shop", "shop"),
                ("/products/h2-4-you-one", "one"),
                ("/products/hydrogen-tablets", "fizz"),
                ("/products/taste-the-range", "range"),
            ):
                response = page.goto(base + route)
                check(response.status == 200, f"{name} {width}: HTTP 200")
                media(page)
                check(page.locator("h1").count() == 1, f"{name} {width}: one h1")
                check(
                    page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"),
                    f"{name} {width}: no horizontal overflow",
                )
                check(
                    page.locator("footer")
                    .inner_text()
                    .count("not intended to diagnose, treat, cure, or prevent any disease.")
                    == 1,
                    f"{name} {width}: FDA disclaimer",
                )
                check(
                    page.locator("img").evaluate_all(
                        "images => images.every(i => i.src.startsWith(location.origin))"
                    ),
                    f"{name} {width}: local image assets",
                )
                if name == "home":
                    check(
                        page.locator(".metal-hero-media img").evaluate(
                            "e => getComputedStyle(e).animationName"
                        )
                        == "none",
                        f"home {width}: reduced motion",
                    )
                    check(
                        page.locator("main > section").evaluate_all("els => els.map(e => e.id)")
                        == ["", "science", "products", "why", "reviews", "learn"],
                        f"home {width}: reference section order",
                    )
                    check(
                        page.locator(".metal-review-card:not([data-carousel-clone])").count() == 8,
                        f"home {width}: eight marked samples",
                    )
                if name in {"one", "fizz", "range"}:
                    gallery = page.get_by_role("group", name="Choose product photo", exact=True)
                    photos = gallery.get_by_role("button")
                    photos.last.click()
                    expect(photos.last).to_have_attribute("aria-pressed", "true")
                    page.get_by_role("button", name="Next product photo", exact=True).click()
                    expect(photos.first).to_have_attribute("aria-pressed", "true")
                    page.get_by_role("button", name="Previous product photo", exact=True).click()
                    expect(photos.last).to_have_attribute("aria-pressed", "true")
                    photos.first.click()
                    check(True, f"{name} {width}: gallery thumbnails and arrows loop")
                    gallery_view = page.locator(".bottle-gallery-viewport")
                    gallery_view.focus()
                    gallery_view.press("ArrowLeft")
                    expect(photos.last).to_have_attribute("aria-pressed", "true")
                    gallery_view.press("ArrowRight")
                    expect(photos.first).to_have_attribute("aria-pressed", "true")
                    check(True, f"{name} {width}: gallery keyboard loops")
                    if width == 375:
                        rect = gallery_view.bounding_box()
                        start = rect["x"] + rect["width"] - 30
                        end = rect["x"] + 30
                        y = rect["y"] + rect["height"] / 2
                        cdp = context.new_cdp_session(page)
                        cdp.send("Input.dispatchTouchEvent", {
                            "type": "touchStart", "touchPoints": [{"x": start, "y": y}]
                        })
                        for i in range(1, 9):
                            cdp.send("Input.dispatchTouchEvent", {
                                "type": "touchMove",
                                "touchPoints": [{"x": start + (end - start) * i / 8, "y": y}],
                            })
                        cdp.send("Input.dispatchTouchEvent", {
                            "type": "touchEnd", "touchPoints": []
                        })
                        expect(photos.nth(1)).to_have_attribute("aria-pressed", "true")
                        photos.first.click()
                        check(True, f"{name} 375: gallery touch swipe")
                        if name != "range":
                            page.locator("footer").scroll_into_view_if_needed()
                            expect(page.locator("[data-product-sticky]")).to_be_visible()
                            check(True, f"{name} 375: mobile sticky purchase appears")
                    if name == "one":
                        technology = page.locator('details[name="liquid-technology"]')
                        technology.nth(0).locator("summary").click()
                        technology.nth(1).locator("summary").click()
                        expect(technology.nth(0)).not_to_have_attribute("open", "")
                        expect(technology.nth(1)).to_have_attribute("open", "")
                        technology.nth(1).locator("summary").click()
                        check(True, f"one {width}: About accordion rows are exclusive")
                    if name == "fizz":
                        check(
                            page.get_by_role("link", name="More questions? See all FAQs").count()
                            == 1,
                            f"fizz {width}: future FAQ destination retained",
                        )
                    page.evaluate("scrollTo(0,0)")
                if name != "shop":
                    page.locator('[data-liquid-dot="0"]').click()
                    expect(page.locator('[data-liquid-dot="0"]')).to_have_attribute("aria-pressed", "true")
                    if name in {"one", "fizz", "range"}:
                        page.locator("#reviews").screenshot(path=str(out / f"{name}-reviews-{width}.png"))
                    page.evaluate("scrollTo(0,0)")
                    page.wait_for_timeout(200)
                page.screenshot(path=str(out / f"{name}-{width}.png"), full_page=True)
            page.goto(base + "/")
            media(page)
            viewport = page.locator("[data-liquid-carousel]")
            viewport.scroll_into_view_if_needed()
            page.get_by_role("button", name="Show reviews starting at 1", exact=True).click()
            expect(page.locator('[data-liquid-dot="0"]')).to_have_attribute("aria-pressed", "true")
            page.get_by_role("button", name="Previous reviews", exact=True).click()
            expect(page.locator('[data-liquid-dot="7"]')).to_have_attribute("aria-pressed", "true")
            page.wait_for_timeout(180)
            page.get_by_role("button", name="Next reviews", exact=True).click()
            expect(page.locator('[data-liquid-dot="0"]')).to_have_attribute("aria-pressed", "true")
            check(True, f"{width}: carousel arrows loop both ways")
            viewport.focus()
            viewport.press("ArrowRight")
            expect(page.locator('[data-liquid-dot="1"]')).to_have_attribute("aria-pressed", "true")
            viewport.press("End")
            expect(page.locator('[data-liquid-dot="7"]')).to_have_attribute("aria-pressed", "true")
            viewport.press("ArrowRight")
            expect(page.locator('[data-liquid-dot="0"]')).to_have_attribute("aria-pressed", "true")
            check(True, f"{width}: carousel keyboard loops")
            check(
                viewport.evaluate("e => getComputedStyle(e).outlineStyle") != "none",
                f"{width}: keyboard focus visible",
            )
            if width == 375:
                page.get_by_role("button", name="Show reviews starting at 8", exact=True).click()
                page.wait_for_timeout(200)
                rect = viewport.bounding_box()
                cdp = context.new_cdp_session(page)
                y = rect["y"] + min(rect["height"] / 2, 200)
                start, end = rect["x"] + rect["width"] - 25, rect["x"] + 25
                cdp.send(
                    "Input.dispatchTouchEvent",
                    {"type": "touchStart", "touchPoints": [{"x": start, "y": y}]},
                )
                for i in range(1, 9):
                    cdp.send(
                        "Input.dispatchTouchEvent",
                        {
                            "type": "touchMove",
                            "touchPoints": [{"x": start + (end - start) * i / 8, "y": y}],
                        },
                    )
                    page.wait_for_timeout(25)
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                expect(page.locator('[data-liquid-dot="0"]')).to_have_attribute(
                    "aria-pressed", "true"
                )
                check(True, "375: native touch swipe loops from last review")
                page.evaluate("scrollTo(0,0)")
                page.get_by_role("button", name="Open menu", exact=True).click()
                expect(page.locator("#liquid-mobile-menu")).to_be_visible()
                check(
                    page.locator("main").evaluate("e=>e.inert"),
                    "375: menu isolates background focus",
                )
                page.keyboard.press("Escape")
                expect(page.get_by_role("button", name="Open menu", exact=True)).to_be_focused()
                check(True, "375: Escape returns mobile menu focus")
                page.emulate_media(reduced_motion="no-preference")
                page.get_by_role("button", name="Play background motion", exact=True).click()
                check(
                    page.locator(".metal-hero-media img").evaluate(
                        "e=>getComputedStyle(e).animationPlayState"
                    )
                    == "running",
                    "375: hero motion plays",
                )
                page.get_by_role("button", name="Pause background motion", exact=True).click()
                check(
                    page.locator(".metal-hero-media img").evaluate(
                        "e=>getComputedStyle(e).animationPlayState"
                    )
                    == "paused",
                    "375: hero motion pauses",
                )
                page.emulate_media(reduced_motion="reduce")
            page.locator("#reviews").screenshot(path=str(out / f"reviews-{width}.png"))
            page.evaluate("scrollTo(0,0)")
            page.get_by_role("button", name="Request your code", exact=True).click()
            expect(page.locator("#liquid-offer-form")).to_be_visible()
            expect(page.locator("#offer-email")).to_be_focused()
            check(True, f"{width}: offer form opens and receives focus")
            page.get_by_role("button", name="Dismiss first order offer", exact=True).click()
            page.reload()
            expect(page.locator("[data-liquid-offer]")).to_be_hidden()
            check(True, f"{width}: offer dismissal survives reload")
            if width == 375:
                page.locator(".metal-flavor-column").last.click()
                expect(page.locator('input[type="radio"][name="variant_id"]').last).to_be_checked()
                page.locator(".metal-product-card").first.get_by_role(
                    "button", name="Add to cart"
                ).click()
                expect(page.locator(".fs-cart-dialog")).to_be_visible()
                expect(
                    page.frame_locator(".fs-cart-dialog iframe").locator("body")
                ).to_contain_text("Peach")
                expect(
                    page.frame_locator(".fs-cart-dialog iframe").locator("body")
                ).to_contain_text("$25.20")
                check(True, "375: Home adds Peach subscription to FastShop drawer at $25.20")
                page.get_by_role("button", name="Close bag", exact=True).click()
                page.goto(base + "/shop")
                page.locator(".shop-product").nth(1).get_by_role(
                    "button", name="Add to cart"
                ).click()
                expect(
                    page.frame_locator(".fs-cart-dialog iframe").locator("body")
                ).to_contain_text("$250.00")
                check(True, "375: Shop adds One to the same FastShop cart")
                page.get_by_role("button", name="Close bag", exact=True).click()
                page.locator(".shop-product").nth(2).get_by_role(
                    "button", name="Add to cart"
                ).click()
                expect(
                    page.frame_locator(".fs-cart-dialog iframe").locator("body")
                ).to_contain_text("Taste the Range")
                expect(
                    page.frame_locator(".fs-cart-dialog iframe").locator("body")
                ).to_contain_text("$95.20")
                check(True, "375: Shop adds Taste the Range at $95.20")
                page.get_by_role("button", name="Close bag", exact=True).click()
                page.screenshot(path=str(out / "shop-cart-after-add-375.png"))
            context.close()
        context = browser.new_context(
            java_script_enabled=False, viewport={"width": 375, "height": 844}
        )
        page = context.new_page()
        page.goto(base + "/")
        check(
            page.get_by_role("heading", level=1).inner_text() == "Your water, upgraded.",
            "no JavaScript: server-rendered Home copy",
        )
        page.locator(".metal-product-card").nth(1).get_by_role("button", name="Add to cart").click()
        expect(page.locator("body")).to_contain_text("$250.00")
        check(page.url.endswith("/cart"), "no JavaScript: native cart form works")
        context.close()
        for slug, amount in (("h2-4-you-one", "$250.00"), ("hydrogen-tablets", "$25.20"), ("taste-the-range", "$95.20")):
            context = browser.new_context(java_script_enabled=False)
            page = context.new_page()
            page.goto(base + "/products/" + slug)
            page.locator("[data-product-submit]").click()
            expect(page.locator("body")).to_contain_text(amount)
            check(page.url.endswith("/cart"), f"no JavaScript: {slug} default purchase")
            context.close()
        # Every purchase case gets a fresh cart, so another case cannot mask a wrong total.
        for slug, variant_index, quantity, subscription, expected_price in (
            ("h2-4-you-one", 1, 2, False, "$450.00"),
            ("h2-4-you-one", 2, 3, False, "$637.50"),
            ("h2-4-you-one", 3, 5, False, "$1,000.00"),
            ("hydrogen-tablets", 3, 2, True, "$50.40"),
            ("hydrogen-tablets", 1, 2, False, "$56.00"),
            ("taste-the-range", 0, 1, False, "$95.20"),
        ):
            context = browser.new_context(viewport={"width": 375, "height": 1000})
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/products/" + slug)
            purchase = page.locator("[data-product-purchase]")
            if slug == "h2-4-you-one":
                purchase.locator("[data-bundle-option]").nth(variant_index).click()
                if variant_index == 3:
                    purchase.get_by_role("button", name="Increase quantity", exact=True).click()
            elif slug == "hydrogen-tablets":
                purchase.locator(".metal-flavor-column").nth(variant_index).click()
                if not subscription:
                    purchase.locator('input[name="subscription"][value="off"]').check()
                purchase.get_by_role("button", name="Increase quantity", exact=True).click()
                facts = page.get_by_text("Ingredients & Supplement Facts", exact=True)
                facts.click()
                expect(page.locator(".tablet-facts")).to_contain_text("PLACEHOLDER")
                check(True, f"{slug}: final-label placeholder remains visible in facts")
            panel_price = purchase.locator("[data-product-total]").inner_text()
            purchase.locator("[data-product-submit]").click()
            expect(page.locator(".fs-cart-dialog")).to_be_visible()
            cart = page.frame_locator(".fs-cart-dialog iframe").locator("body")
            expect(cart).to_contain_text(expected_price)
            if slug == "hydrogen-tablets":
                expect(cart).to_contain_text("Peach" if variant_index == 3 else "Raspberry")
                if subscription:
                    expect(cart).to_contain_text("Monthly subscription")
            check(
                float(panel_price.replace("$", "").replace(",", ""))
                == float(expected_price.replace("$", "").replace(",", "")),
                f"{slug}: quantity {quantity} panel/cart parity at {expected_price}",
            )
            context.close()
        browser.close()
    check(not errors, "no browser JavaScript errors")
    report = {
        "passed": len(checks),
        "failed": 0,
        "checks": checks,
        "errors": errors,
        "comparison": "Locally running Lovable reference captures are stored alongside FastShop captures with the ref- prefix.",
    }
    (out / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "failed": 0}))


if __name__ == "__main__":
    main()

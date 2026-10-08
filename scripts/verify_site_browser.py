"""Chrome acceptance evidence for Phase 1; never saves browser credentials/state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5033")
    parser.add_argument("--merchant", action="store_true")
    parser.add_argument("--merchant-only", action="store_true", help="Rerun merchant flows after storefront verification")
    parser.add_argument("--blog", action="store_true", help="Include blog taxonomy and editorial workflow evidence")
    parser.add_argument("--embeds", action="store_true", help="Include Phase 1c embed and snippet publication evidence")
    parser.add_argument("--output", default="output/playwright/h24you-phase1")
    args = parser.parse_args()
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
            page = context.new_page()
            page.goto(args.base + "/login?next=/admin/sites")
            page.locator('input[name="email"]').fill(os.environ["FASTSHOP_ADMIN_EMAIL"])
            page.locator('input[name="password"]').fill(os.environ["FASTSHOP_ADMIN_PASSWORD"])
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/admin/sites")
            page.screenshot(path=str(out / "merchant-sites.png"), full_page=True)
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

"""Full-site Liquid evidence and M3 progressive enhancement checks, without mail sends."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from capture_liquid_reference import PAGES
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5025")
    parser.add_argument("--output", default="output/playwright/h24you-liquid")
    args = parser.parse_args()
    origin = args.base.rstrip("/")
    base = origin + "/sites/h24you-liquid"
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    checks, errors, screenshots, links, placeholders = [], [], [], set(), set()

    def check(value, label):
        checks.append({"check": label, "passed": bool(value)})

    def ready(page):
        page.evaluate("document.fonts.ready")
        page.evaluate("document.querySelectorAll('img').forEach(i => i.loading='eager')")
        page.wait_for_function(
            "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
        )
        page.evaluate("""async () => {
            for (let y=0; y<document.documentElement.scrollHeight; y+=700) {
                scrollTo(0,y); await new Promise(requestAnimationFrame);
            }
            scrollTo(0,0); await new Promise(requestAnimationFrame);
        }""")

    def collect(page):
        for item in page.get_by_role("link", include_hidden=True).evaluate_all(
            """els => els.map(e => ({href:e.getAttribute('href'),
                text:e.getAttribute('aria-label') || e.textContent.trim()}))"""
        ):
            href = item["href"]
            if href == "#":
                placeholders.add(item["text"])
            elif href and urlsplit(urljoin(page.url, href)).netloc == urlsplit(origin).netloc:
                links.add(urljoin(page.url, href))

    def anchors(page, width, name):
        targets = page.locator('main a[href*="#"], nav.faq-category-nav a[href*="#"]')
        hrefs = list(dict.fromkeys(targets.evaluate_all("els => els.map(e=>e.href)")))
        for href in hrefs:
            path, fragment = urldefrag(href)
            if not fragment or path != urldefrag(page.url)[0]:
                continue
            target = page.locator(f'[id="{unquote(fragment)}"]')
            check(target.count() == 1, f"{name} {width}: anchor {fragment} exists")
            if not target.count():
                continue
            page.goto(href)
            page.wait_for_function(
                """id => {
                const e=document.getElementById(id), bars=document.querySelector('.metal-top-bars');
                return e && e.getBoundingClientRect().top >= bars.getBoundingClientRect().bottom-1;
            }""",
                arg=unquote(fragment),
            )
            top = target.bounding_box()["y"]
            bottom = page.locator(".metal-top-bars").bounding_box()
            clearance = bottom["y"] + bottom["height"]
            if name == "faq":
                nav = page.get_by_role("navigation", name="FAQ categories").bounding_box()
                clearance = max(clearance, nav["y"] + nav["height"])
            check(top >= clearance - 1, f"{name} {width}: anchor {fragment} clears sticky bars")
            check(
                top <= clearance + 80
                or page.evaluate(
                    "scrollY + innerHeight >= document.documentElement.scrollHeight - 2"
                ),
                f"{name} {width}: anchor {fragment} scrolls to target",
            )

    def faq(page, width):
        data = page.locator('script[type="application/ld+json"]').all_text_contents()
        schema = next(json.loads(s) for s in data if json.loads(s).get("@type") == "FAQPage")
        questions = schema["mainEntity"]
        check(bool(questions), f"faq {width}: FAQPage JSON-LD populated")
        for question in questions:
            text = question["name"]
            trigger = page.get_by_text(text, exact=True)
            check(trigger.count() >= 1, f"faq {width}: HTML question {text}")
            check(
                question["acceptedAnswer"]["text"] in page.locator("main").text_content(),
                f"faq {width}: JSON-LD answer matches HTML {text}",
            )
        rows = page.locator(".faq-row")
        check(
            rows.count() == len(questions), f"faq {width}: JSON-LD and HTML question counts match"
        )
        first = rows.first.locator("summary, button").first
        first.click()
        check(rows.first.locator(".faq-answer").is_visible(), f"faq {width}: answer opens")
        first.click()
        check(not rows.first.locator(".faq-answer").is_visible(), f"faq {width}: answer closes")
        search = page.get_by_role("searchbox")
        search.fill("zz-no-matching-question-zz")
        expect(rows.first).not_to_be_visible()
        check(
            page.get_by_role("link", name="Contact us", exact=True).count() > 0,
            f"faq {width}: empty search exposes contact",
        )
        search.fill(questions[0]["name"])
        expect(rows.first).to_be_visible()
        check(True, f"faq {width}: live search restores matching question")
        search.fill("")

    def contact(page, width):
        form = page.locator("form.contact-form")
        token = form.locator('input[name="csrf_token"]')
        check(bool(token.input_value()), f"contact {width}: CSRF token present")
        action = urljoin(page.url, form.get_attribute("action") or page.url)
        bad_csrf = page.request.post(action, form={"csrf_token": "invalid"})
        check(bad_csrf.status in (400, 403), f"contact {width}: rejects invalid CSRF")
        response = page.request.post(action, form={"csrf_token": token.input_value()})
        check(response.status in (200, 400, 422), f"contact {width}: validates empty submission")
        check(
            "valid email and a message" in response.text(),
            f"contact {width}: server validation error returned",
        )
        check("Thank you," not in response.text(), f"contact {width}: no false success")
        page.get_by_role("button", name="Send message", exact=True).click()
        check(
            page.locator('[aria-invalid="true"], :invalid').count() > 0,
            f"contact {width}: invalid fields exposed",
        )

    def balance(page, width):
        scale = page.locator(".why-story .why-balance")
        expect(scale).to_have_attribute("data-stage", "5")
        check(True, f"why {width}: reduced motion shows complete balance")
        page.emulate_media(reduced_motion="no-preference")
        steps = page.locator(".why-steps li")
        check(steps.count() == 6, f"why {width}: six source balance steps")
        for index in list(range(6)) + list(reversed(range(6))):
            steps.nth(index).evaluate("""async step => {
                step.scrollIntoView({block:'center',behavior:'instant'});
                for(let i=0;i<3;i++) {
                    await new Promise(requestAnimationFrame);
                    const bars=document.querySelector('.metal-top-bars');
                    const visual=document.querySelector('.why-story-visual');
                    const top=bars.offsetHeight;
                    const line=innerWidth<1024 ? Math.max(top+80,visual.getBoundingClientRect().bottom+40) : top+(innerHeight-top)*.45;
                    scrollBy({top:step.getBoundingClientRect().top-line+2,behavior:'instant'});
                }
            }""")
            expect(scale).to_have_attribute("data-stage", str(index))
        check(True, f"why {width}: balance progresses and reverses through all six steps")
        page.emulate_media(reduced_motion="reduce")
        for chapter in ("what-is-hydrogen", "free-radicals", "what-hydrogen-does"):
            page.goto(base + "/why-hydrogen#" + chapter)
            expect(page.locator(f'.why-index a[href="#{chapter}"]')).to_have_attribute(
                "aria-current", "true"
            )
        check(True, f"why {width}: chapter index follows reading position")

    def science(page, context, width):
        deck = page.get_by_role("region", name="Myths and facts", exact=True)
        count = deck.locator(".why-myth-card").count()
        progress = deck.locator(".why-deck-count")
        deck.get_by_role("button", name="Previous myth", exact=True).click()
        expect(progress).to_have_text(f"{count} / {count}")
        deck.get_by_role("button", name="Next myth", exact=True).click()
        expect(progress).to_have_text(f"1 / {count}")
        current = deck.locator('.why-myth-card[data-current="true"]')
        current.locator("[data-reveal]").click()
        expect(current).to_have_attribute("data-flipped", "true")
        current.locator("[data-next]").click()
        expect(progress).to_have_text(f"2 / {count}")
        deck.get_by_role("button", name="Previous myth", exact=True).press("ArrowLeft")
        expect(progress).to_have_text(f"1 / {count}")
        deck.get_by_role("button", name="Next myth", exact=True).press("ArrowRight")
        expect(progress).to_have_text(f"2 / {count}")
        check(True, f"science {width}: myth arrows wrap, reveal, next and keyboard work")
        if width == 375:
            stack = deck.locator(".why-deck-stack")
            stack.scroll_into_view_if_needed()
            rect = stack.bounding_box()
            cdp = context.new_cdp_session(page)
            x, y = rect["x"] + rect["width"] - 30, rect["y"] + rect["height"] / 2
            cdp.send(
                "Input.dispatchTouchEvent",
                {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]},
            )
            for distance in (30, 60, 90, 120):
                cdp.send(
                    "Input.dispatchTouchEvent",
                    {"type": "touchMove", "touchPoints": [{"x": x - distance, "y": y}]},
                )
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            expect(progress).to_have_text(f"3 / {count}")
            check(True, "science 375: myth touch swipe advances")
            cdp.detach()
        filters = page.get_by_role("group", name="Filter studies")
        for button in filters.get_by_role("button").all():
            key = button.get_attribute("data-filter")
            button.click()
            expect(button).to_have_attribute("aria-pressed", "true")
            cells = page.locator(".why-study-cell")
            expected = cells.evaluate_all(
                "(els,key)=>els.filter(e=>key==='all'||e.dataset.tags.split(' ').includes(key)).length",
                key,
            )
            check(
                page.locator(".why-study-cell:visible").count() == expected,
                f"science {width}: source research filter {key}",
            )
        filters.locator('[data-filter="all"]').click()
        card = page.locator(".why-study").first
        card.focus()
        card.press("Enter")
        expect(card).to_have_attribute("data-flipped", "true")
        source = card.get_by_role("link", name="Read the study ↗", exact=True)
        check(
            source.get_attribute("href").startswith("https://"),
            f"science {width}: study exposes external source",
        )
        card.get_by_role("button", name="↻ Back", exact=True).click()
        expect(card).to_have_attribute("data-flipped", "false")
        card.press("Space")
        expect(card).to_have_attribute("data-flipped", "true")
        check(True, f"science {width}: research cards flip with keyboard and return")
        panel = page.locator("details.why-sources")
        panel.locator("summary").click()
        expect(panel.locator(".why-sources-body")).to_be_visible()
        check(
            source.get_attribute("href")
            in panel.get_by_role("link").evaluate_all("els=>els.map(e=>e.href)"),
            f"science {width}: study source matches source register",
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for width in (375, 768, 1024, 1440):
            for route, name in PAGES:
                context = browser.new_context(
                    viewport={"width": width, "height": 1000},
                    reduced_motion="reduce",
                    has_touch=True,
                )
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on(
                    "console",
                    lambda e: (
                        errors.append(f"{e.location.get('url', '')}: {e.text}")
                        if e.type == "error"
                        else None
                    ),
                )
                try:
                    response = page.goto(base + route)
                    check(response.status == 200, f"{name} {width}: HTTP 200")
                    expect(page.get_by_role("heading", level=1)).to_be_visible()
                    ready(page)
                    check(page.locator("h1").count() == 1, f"{name} {width}: one h1")
                    check(
                        page.evaluate("document.documentElement.scrollWidth <= innerWidth+1"),
                        f"{name} {width}: no horizontal overflow",
                    )
                    check(
                        page.locator("img").evaluate_all(
                            "els => els.every(e=>e.complete && e.naturalWidth>0)"
                        ),
                        f"{name} {width}: all images load",
                    )
                    check(
                        page.locator("video").evaluate_all("els=>els.every(e=>e.paused)"),
                        f"{name} {width}: reduced motion pauses videos",
                    )
                    check(
                        page.evaluate(
                            "document.getAnimations().every(a => a.playState !== 'running')"
                        ),
                        f"{name} {width}: reduced motion stops decorative animations",
                    )
                    collect(page)
                    page.screenshot(path=str(out / f"{name}-{width}.png"), full_page=True)
                    screenshots.append(f"{name}-{width}.png")
                    page.keyboard.press("Tab")
                    check(
                        page.evaluate("""() => {
                        const s=getComputedStyle(document.activeElement);
                        return s.outlineStyle!=='none' || s.boxShadow!=='none';
                    }"""),
                        f"{name} {width}: keyboard focus visible",
                    )
                    if name in {"faq", "why-hydrogen", "learn", "why-hydrogen-science"}:
                        anchors(page, width, name)
                        page.goto(base + route)
                    if name == "faq":
                        faq(page, width)
                    if name == "contact":
                        contact(page, width)
                    if name == "why-hydrogen":
                        balance(page, width)
                    if name == "why-hydrogen-science":
                        science(page, context, width)
                    if width == 375:
                        page.get_by_role("button", name="Open menu", exact=True).click()
                        expect(page.locator("#liquid-mobile-menu")).to_be_visible()
                        collect(page)
                        page.keyboard.press("Escape")
                        expect(
                            page.get_by_role("button", name="Open menu", exact=True)
                        ).to_be_focused()
                        check(True, f"{name} {width}: mobile menu closes and returns focus")
                except Exception as exc:
                    errors.append(f"{name} {width}: {exc}")
                finally:
                    context.close()

        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        page.goto(base + "/faq")
        check(page.locator(".faq-row").count() > 0, "FAQ without JS: questions present")
        for row in page.locator(".faq-row").all():
            summary = row.locator("summary")
            if summary.count():
                summary.click()
            check(row.locator(".faq-answer").is_visible(), "FAQ without JS: answer reachable")
        context.close()

        context = browser.new_context(reduced_motion="reduce")
        page = context.new_page()
        page.goto(base + "/")
        cards = page.locator(".home-learn a[href]")
        card_urls = cards.evaluate_all("els=>els.map(e=>e.href)")
        for width in (375, 768, 1024, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            for href in card_urls:
                page.goto(base + "/")
                ready(page)
                card = page.locator(f'.home-learn a[href="{href.removeprefix(origin)}"]')
                card.scroll_into_view_if_needed()
                saved_top = card.bounding_box()["y"]
                card.click()
                expect(page).to_have_url(href)
                fragment = unquote(urlsplit(href).fragment)
                if fragment:
                    page.wait_for_function("""id => {
                        const y=document.getElementById(id).getBoundingClientRect().top;
                        const bottom=document.querySelector('.metal-top-bars').getBoundingClientRect().bottom;
                        return y >= bottom-1 && y < bottom+160;
                    }""", arg=fragment)
                    check(True, f"Home Learn {width}: correct chapter position {fragment}")
                page.go_back()
                expect(page).to_have_url(base + "/")
                # The compact header changes document height. Verify the reader's
                # card position, rather than an absolute document scroll offset.
                page.wait_for_function("""({href,top}) => {
                    const card=[...document.querySelectorAll('.home-learn a[href]')]
                        .find(a=>a.href===href);
                    return card && Math.abs(card.getBoundingClientRect().top-top)<5;
                }""", arg={"href": href, "top": saved_top})
                check(True, f"Home Learn {width}: Back restores reading position {href}")
                page.go_forward()
                expect(page).to_have_url(href)
                if fragment:
                    page.wait_for_function("""id => {
                        const y=document.getElementById(id).getBoundingClientRect().top;
                        const bottom=document.querySelector('.metal-top-bars').getBoundingClientRect().bottom;
                        return y >= bottom-1 && y < bottom+160;
                    }""", arg=fragment)
                check(True, f"Home Learn {width}: Forward restores chapter {href}")
        check(len(card_urls) == 3, "Home has three working Learn cards")
        for href in sorted(links):
            response = context.request.get(urldefrag(href)[0])
            check(response.status < 400, f"internal link: {href}")
            fragment = unquote(urlsplit(href).fragment)
            if fragment and response.status == 200:
                page.goto(href)
                check(page.locator(f'[id="{fragment}"]').count() == 1, f"internal fragment: {href}")
        response = context.request.get(base + "/4")
        check(
            response.status == 200 and urlsplit(response.url).path == urlsplit(base + "/").path,
            "/4 redirects to Home",
        )
        context.close()
        browser.close()
    report = {
        "checks": len(checks),
        "passed": sum(c["passed"] for c in checks),
        "failed": [c["check"] for c in checks if not c["passed"]],
        "screenshots": len(screenshots),
        "internal_links": len(links),
        "placeholder_links": sorted(placeholders),
        "errors": errors,
        "results": checks,
    }
    (out / "m3-browser-results.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    assert not report["failed"] and not errors, "See m3-browser-results.json"


if __name__ == "__main__":
    main()

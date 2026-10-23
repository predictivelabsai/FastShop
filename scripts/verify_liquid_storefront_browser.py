"""Capture public Liquid flows and assert the local Google font cuts in Chrome."""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5025/sites/h24you-liquid"
OUT = Path("output/playwright/h24you-liquid/storefront-pass")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for width in (375, 768, 1024, 1440):
            context = browser.new_context(viewport={"width": width, "height": 1000},
                                          reduced_motion="reduce")
            page = context.new_page()
            def capture(name, page=page, width=width, context=context):
                page.evaluate("document.fonts.ready")
                page.evaluate("document.querySelectorAll('img').forEach(i => i.loading='eager')")
                page.wait_for_function("Array.from(document.images).every(i => i.complete)")
                fonts = page.evaluate("""() => [...new Set([...document.querySelectorAll('body *')]
                    .filter(e=>[...e.childNodes].some(n=>n.nodeType===3 && n.textContent.trim())
                         && getComputedStyle(e).display!=='none' && e.getClientRects().length)
                    .map(e=>getComputedStyle(e).fontFamily))].sort()""")
                assert fonts and all(f.startswith(('"Liquid ', "Liquid ")) for f in fonts), fonts
                faces = page.evaluate("""() => [...document.fonts].filter(f=>f.status==='loaded')
                    .map(f=>({family:f.family,weight:f.weight,style:f.style}))""")
                assert faces and all("Liquid " in f["family"] for f in faces), faces
                assert page.evaluate("performance.getEntriesByType('resource').some(r=>r.name.includes('/fonts/liquid/google/') && r.name.includes('.woff2'))")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
                assert "FastShop" not in page.locator("body").inner_text()
                page.screenshot(path=str(OUT / f"{name}-{width}.png"), full_page=True)
                cdp = context.new_cdp_session(page)
                cdp.send("DOM.enable")
                cdp.send("CSS.enable")
                nodes = cdp.send("DOM.getFlattenedDocument", {"depth":-1,"pierce":True})["nodes"]
                parents = {n["parentId"] for n in nodes if n["nodeType"] == 3 and n["nodeValue"].strip()}
                rendered = set()
                fallbacks = []
                for node in nodes:
                    if node["nodeId"] not in parents or node["nodeName"] in ("SCRIPT", "STYLE", "TITLE"):
                        continue
                    for face in cdp.send("CSS.getPlatformFontsForNode", {"nodeId":node["nodeId"]})["fonts"]:
                        if face["glyphCount"]:
                            rendered.add(face["familyName"])
                            if not face["familyName"].startswith(("Fraunces", "Quicksand", "Italiana")):
                                fallbacks.append({"font":face["familyName"], "node":node.get("attributes"),
                                    "text":[n["nodeValue"] for n in nodes if n.get("parentId")==node["nodeId"] and n["nodeType"]==3]})
                cdp.detach()
                assert rendered and not fallbacks, fallbacks
                row = {"rendered_fonts": sorted(rendered), "page":name,"width":width,"fonts":fonts,"loaded_faces":faces}
                print(json.dumps(row))
                results.append(row)
            for path, name in [("/", "home"), ("/products/h2-4-you-one", "product"),
                               ("/cart", "cart-empty"), ("/account", "account"),
                               ("/account/subscriptions", "subscriptions"), ("/missing-page", "404")]:
                page.goto(BASE + path)
                capture(name)
            page.goto(BASE + "/")
            page.locator('[data-cart-open]').click()
            page.locator('.fs-cart-dialog[open]').wait_for()
            page.wait_for_timeout(800)
            capture("drawer-empty")
            page.keyboard.press("Escape")
            # Native requests preserve the exact CSRF and server price boundaries.
            for path in ("/products/hydrogen-tablets", "/products/h2-4-you-one"):
                page.goto(BASE + path)
                form = page.locator('form[action$="/cart/add"]').first
                data = form.evaluate("f=>Object.fromEntries(new FormData(f))")
                if "tablets" in path:
                    data["subscription"] = "on"
                response = context.request.post(BASE + "/cart/add", form=data)
                assert response.ok, response.text()
            page.goto(BASE + "/cart")
            capture("cart-filled")
            page.locator('[data-cart-open]').click()
            page.wait_for_timeout(800)
            frame = page.frame_locator('.fs-cart-dialog iframe')
            assert frame.locator('.liquid-cart-line').count() == 2
            capture("drawer-filled")
            page.keyboard.press("Escape")
            page.goto(BASE + "/checkout")
            capture("checkout")
            cookie = next(c["value"] for c in context.cookies() if c["name"] == "session_")
            session = json.loads(base64.b64decode(cookie.split(".")[0]))
            cart_id = next(v for k, v in session.items() if k.startswith("cart:"))
            environment = os.environ | {"PYTHONUTF8":"1", "DB_URL":"",
                "FASTSHOP_DATA_DIR":str(OUT / "fixture-config"), "FASTSHOP_ENV":"development"}
            fixture = subprocess.run([sys.executable, "scripts/liquid_storefront_fixtures.py", cart_id],
                env=environment, capture_output=True, text=True, check=True)
            data = json.loads(fixture.stdout)
            attempt = data["attempt"]
            page.goto(BASE + "/checkout/" + attempt)
            assert "Review your order" in page.locator("h1").inner_text()
            capture("checkout-review")
            # No hosted payment handoff is made.
            page.get_by_role("button", name="Cancel checkout", exact=True).click()
            capture("checkout-cancelled")
            page.goto(BASE + "/account/verify/" + data["challenge"])
            page.locator('main [name="token"]').fill(data["token"])
            page.get_by_role("button", name="Confirm", exact=True).click()
            capture("account-signed-in")
            page.goto(BASE + "/account/subscriptions")
            assert "Your subscriptions" in page.locator("h1").inner_text()
            capture("subscriptions")
            page.goto(BASE + "/checkout/missing")
            capture("checkout-error")
            context.close()
        context = browser.new_context(java_script_enabled=False,
            viewport={"width":375,"height":1000}, reduced_motion="reduce")
        page = context.new_page()
        page.goto(BASE + "/products/h2-4-you-one")
        page.locator('form[action$="/cart/add"]').first.get_by_role("button", name="Add to cart", exact=True).click()
        assert page.locator('.liquid-cart-line').count() == 1
        page.get_by_role("button", name="Increase quantity", exact=True).click()
        assert page.locator('main input[name="quantity"][type="number"]').input_value() == "2"
        capture("cart-native", page=page, width=375, context=context)
        page.get_by_role("button", name="Decrease quantity", exact=True).click()
        assert page.locator('main input[name="quantity"][type="number"]').input_value() == "1"
        page.get_by_role("button", name="Remove", exact=True).click()
        assert page.locator('.liquid-cart-line').count() == 0
        assert "first fizz" in page.locator('main').inner_text()
        context.close()
        browser.close()
    (OUT / "font-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

"""Real catalogue journeys on disposable storage, without environment-file loading.

Run: uv run python scripts/verify_admin_v2_catalog_browser.py
Each viewport receives its own generic merchant store. No session state is saved.
"""

from __future__ import annotations

import io
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/admin-v2/p2"
PRODUCT_TITLE = "Everyday canvas tote"


def seed_store(device: str) -> str:
    from sqlalchemy import select

    from app import content
    from app.db import SessionLocal
    from app.models import User, Warehouse

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == EMAIL))
        assert user is not None
        site = content.create_site(db, user.id, "Northfield Supply", f"catalogue-{device}")
        db.add(Warehouse(tenant_id=site.tenant_id, code="MAIN", name="Main warehouse"))
        db.commit()
        return site.id


def product_image() -> bytes:
    """A tiny original upload fixture held in memory, never a temporary repo file."""
    image = Image.new("RGB", (640, 640), "#e9e5df")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((210, 110, 430, 330), radius=85, outline="#78644d", width=25)
    draw.rounded_rectangle((130, 250, 510, 560), radius=30, fill="#b5a182")
    draw.text((250, 385), "NORTHFIELD", fill="#40382f")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def screenshot(page, device: str, name: str) -> None:
    expect(page.get_by_role("main")).to_be_visible()
    page.evaluate("document.fonts.ready")
    page.evaluate("window.scrollTo(0, 0)")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
        f"Horizontal overflow on {device} {name}"
    )
    page.screenshot(path=str(OUTPUT / f"{device}-{name}.png"), full_page=True, timeout=60000)


def login(page) -> None:
    page.goto("/login?next=/admin/sites")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url("**/admin/sites")


def verify_device(browser, base: str, site_id: str, device: str,
                  width: int, height: int, errors: list[str]) -> None:
    context = browser.new_context(base_url=base, viewport={"width": width, "height": height},
                                  reduced_motion="reduce", accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(10_000)
    page.on("pageerror", lambda error: errors.append(f"{device}: {error}"))
    try:
        login(page)
        prefix = f"/admin/sites/{site_id}"
        verify_product(page, prefix, device)
        verify_csv(page, prefix, device)
        verify_collections(page, prefix, device)
        verify_inventory(page, prefix, device)
    except BaseException:
        page.screenshot(path=str(OUTPUT / f"{device}-failure.png"), full_page=True)
        raise
    finally:
        context.close()


def verify_product(page, prefix: str, device: str) -> None:
    page.goto(prefix + "/products")
    expect(page.get_by_role("heading", name="Add your first product", exact=True)).to_be_visible()
    screenshot(page, device, "products-empty")
    page.get_by_role("link", name="Add product", exact=True).first.click()
    page.get_by_label("Title", exact=True).fill(PRODUCT_TITLE)
    page.get_by_role("textbox", name="Description", exact=True).fill(
        "A dependable canvas tote for the everyday essentials.")
    page.get_by_label("Short description", exact=True).fill("Room for every day.")
    page.get_by_label("Price", exact=True).fill("29.95")
    page.get_by_label("Compare-at price", exact=True).fill("39.95")
    page.get_by_label("Cost per item", exact=True).fill("12.00")
    page.get_by_label("Status", exact=True).select_option("active")
    page.get_by_label("Online store", exact=True).check()
    page.get_by_role("textbox", name="Vendor", exact=True).fill("Northfield")
    page.get_by_label("Product type", exact=True).fill("Bags")
    page.get_by_role("textbox", name="Add tag", exact=True).fill("everyday, canvas")
    page.get_by_role("textbox", name="Add tag", exact=True).press("Enter")
    expect(page.get_by_role("button", name="Remove tag canvas", exact=True)).to_be_visible()
    page.get_by_label("SEO title", exact=True).fill("Everyday canvas tote | Northfield Supply")
    page.get_by_label("SEO description", exact=True).fill("A dependable canvas tote, made for everyday essentials.")
    page.get_by_label("URL handle", exact=True).fill("everyday-canvas-tote")
    page.get_by_role("button", name="Add options like size or colour", exact=True).click()
    # Option/variant controls below use labels supplied by the vanilla catalogue client.
    page.get_by_label("Option name", exact=True).fill("Colour")
    page.get_by_label("Option values", exact=True).fill("Natural, Charcoal")
    page.get_by_label("Option values", exact=True).press("Tab")
    expect(page.get_by_role("row").filter(has_text="Natural")).to_be_visible()
    page.get_by_label("Price Natural", exact=True).fill("31.95")
    page.get_by_label("SKU Natural", exact=True).fill("TOTE-NAT")
    page.get_by_label("Quantity Natural", exact=True).fill("12")
    page.get_by_label("Price Charcoal", exact=True).fill("34.95")
    page.get_by_label("SKU Charcoal", exact=True).fill("TOTE-CHR")
    page.get_by_label("Quantity Charcoal", exact=True).fill("7")
    page.get_by_label("Upload product images", exact=True).set_input_files({
        "name": "northfield-tote.png", "mimeType": "image/png", "buffer": product_image(),
    })
    expect(page.get_by_alt_text("northfield-tote.png", exact=True)).to_be_visible()
    screenshot(page, device, "product-new")
    page.get_by_role("button", name="Save product", exact=True).click()
    page.wait_for_url(f"**{prefix}/products/*")
    expect(page.get_by_role("heading", name=PRODUCT_TITLE, exact=True)).to_be_visible()
    expect(page.get_by_label("Price Natural", exact=True)).to_have_value("31.95")
    expect(page.get_by_label("SKU Natural", exact=True)).to_have_value("TOTE-NAT")
    expect(page.get_by_label("Quantity Natural", exact=True)).to_have_value("12")
    expect(page.get_by_alt_text(PRODUCT_TITLE, exact=True)).to_be_visible()
    expect(page.get_by_label("Compare-at price", exact=True)).to_have_value("39.95")
    expect(page.get_by_label("Cost per item", exact=True)).to_have_value("12.00")
    page.locator('textarea[name="short_description"]').fill("Made for everyday journeys.")
    page.get_by_role("button", name="Save product", exact=True).click()
    page.reload()
    expect(page.locator('textarea[name="short_description"]')).to_have_value(
        "Made for everyday journeys.")
    expect(page.get_by_label("SKU Natural", exact=True)).to_have_value("TOTE-NAT")
    screenshot(page, device, "product-editor")
    page.goto(prefix + "/products")
    page.get_by_label("Search products", exact=True).fill("canvas")
    page.get_by_text("Add filter", exact=True).click()
    page.get_by_role("textbox", name="Vendor", exact=True).fill("Northfield")
    page.get_by_label("Tag", exact=True).fill("everyday")
    page.locator(".a-filter-search input").press("Enter")
    expect(page.locator('.a-filter-status')).to_have_text("Results updated")
    expect(page.get_by_role("link", name=PRODUCT_TITLE, exact=True)).to_be_visible()
    screenshot(page, device, "products-filtered")
    page.get_by_label(f"Select {PRODUCT_TITLE}", exact=True).check()
    page.get_by_label("Bulk action", exact=True).select_option("draft")
    page.get_by_role("button", name="Apply", exact=True).click()
    row = page.get_by_role("row").filter(has_text=PRODUCT_TITLE)
    expect(row).to_contain_text("Draft")
    screenshot(page, device, "products-draft")


def verify_csv(page, prefix: str, device: str) -> None:
    page.goto(prefix + "/products")
    with page.expect_download() as download_event:
        page.get_by_role("link", name="Export all", exact=True).click()
    download = download_event.value
    assert download.suggested_filename.endswith(".csv")
    csv_data = Path(download.path()).read_bytes()
    assert b"Variant SKU" in csv_data and b"TOTE-NAT" in csv_data
    page.get_by_role("link", name="Import", exact=True).click()
    page.get_by_label("CSV file", exact=True).set_input_files({
        "name": "catalogue-roundtrip.csv", "mimeType": "text/csv", "buffer": csv_data,
    })
    page.get_by_role("button", name="Preview import", exact=True).click()
    expect(page.get_by_role("heading", name="Import preview", exact=True).last).to_be_visible()
    expect(page.get_by_role("main")).to_contain_text("everyday-canvas-tote")
    screenshot(page, device, "csv-preview")
    page.get_by_role("button", name="Import products", exact=True).click()
    expect(page.get_by_role("heading", name="Import complete", exact=True)).to_be_visible()
    page.get_by_role("link", name="View products", exact=True).click()
    expect(page.get_by_role("link", name=PRODUCT_TITLE, exact=True)).to_have_count(1)


def verify_collections(page, prefix: str, device: str) -> None:
    page.goto(prefix + "/collections")
    page.get_by_role("link", name="Create collection", exact=True).first.click()
    page.get_by_label("Title", exact=True).fill("Everyday essentials")
    page.get_by_label("Description", exact=True).fill("Useful things for every day.")
    page.get_by_label("URL handle", exact=True).fill("everyday-essentials")
    page.get_by_label("Search products", exact=True).fill("canvas")
    page.get_by_label("Choose product", exact=True).select_option(label=PRODUCT_TITLE)
    page.get_by_role("button", name="Add product", exact=True).click()
    expect(page.get_by_role("main")).to_contain_text(PRODUCT_TITLE)
    page.get_by_label("Published on online store", exact=True).check()
    page.get_by_role("button", name="Save collection", exact=True).last.click()
    page.wait_for_url(f"**{prefix}/collections/*")
    expect(page.get_by_label("Title", exact=True)).to_have_value("Everyday essentials")
    screenshot(page, device, "collection-manual")
    page.goto(prefix + "/collections/new")
    page.get_by_label("Title", exact=True).fill("Canvas collection")
    page.get_by_label("URL handle", exact=True).fill("canvas-collection")
    page.get_by_label("Collection type", exact=True).select_option("automated")
    page.get_by_role("button", name="Add condition", exact=True).click()
    page.get_by_label("Rule field", exact=True).select_option("vendor")
    page.get_by_label("Rule value", exact=True).fill("Northfield")
    page.get_by_role("button", name="Preview matches", exact=True).click()
    expect(page.get_by_role("status").filter(has_text="matching product")).to_contain_text("1 matching product")
    screenshot(page, device, "collection-automated-preview")
    page.get_by_role("button", name="Save collection", exact=True).last.click()
    page.wait_for_url(f"**{prefix}/collections/*")
    expect(page.get_by_label("Title", exact=True)).to_have_value("Canvas collection")
    screenshot(page, device, "collection-automated")
    page.goto(prefix + "/collections")
    expect(page.get_by_role("link", name="Everyday essentials", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Canvas collection", exact=True)).to_be_visible()
    screenshot(page, device, "collections-index")


def verify_inventory(page, prefix: str, device: str) -> None:
    page.goto(prefix + "/inventory")
    page.get_by_label("Search inventory", exact=True).fill("TOTE-NAT")
    page.locator(".a-filter-search input").press("Enter")
    expect(page.locator(".a-filter-status")).to_have_text("Results updated")
    expect(page.get_by_role("main")).to_contain_text("12 available")
    page.get_by_text("Adjust inventory", exact=True).click()
    page.get_by_label("Mode", exact=True).select_option("adjust")
    page.get_by_label("Quantity", exact=True).fill("3")
    page.get_by_label("Reason", exact=True).select_option("received")
    page.get_by_label("Note", exact=True).fill("Local browser receipt")
    screenshot(page, device, "inventory-adjust")
    page.get_by_role("button", name="Save adjustment", exact=True).click()
    # Adjustment returns to the inventory index, so apply the SKU filter again.
    page.get_by_label("Search inventory", exact=True).fill("TOTE-NAT")
    page.locator(".a-filter-search input").press("Enter")
    expect(page.locator(".a-filter-status")).to_have_text("Results updated")
    expect(page.get_by_role("main")).to_contain_text("15 available")
    page.get_by_role("button", name="Adjustment history", exact=True).click()
    expect(page.get_by_role("row").filter(has_text="Local browser receipt")).to_contain_text("+3")
    screenshot(page, device, "inventory-history")
    page.get_by_role("dialog", name="Adjustment history").get_by_role("button", name="Close", exact=True).click()
    page.get_by_label("Low-stock threshold", exact=True).fill("20")
    page.get_by_role("button", name="Save threshold", exact=True).click()
    page.get_by_text("Add filter", exact=True).click()
    page.get_by_label("Low stock only", exact=True).check()
    page.locator(".a-filter-search input").press("Enter")
    expect(page.locator(".a-filter-status")).to_have_text("Results updated")
    expect(page.get_by_role("main")).to_contain_text("TOTE-NAT")
    screenshot(page, device, "inventory-low-stock")
    page.goto(prefix)
    expect(page.get_by_role("link", name="2 variants low in stock", exact=True)).to_be_visible()
    screenshot(page, device, "home-low-stock")
    failure = OUTPUT / f"{device}-failure.png"
    failure.unlink(missing_ok=True)


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-catalogue-browser-") as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local verification server failed to start")
                thread.join(0.05)
            errors: list[str] = []
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        site_id = seed_store(device)
                        verify_device(browser, f"http://127.0.0.1:{port}", site_id,
                                      device, width, height, errors)
                finally:
                    browser.close()
            assert not errors, errors
            print(json.dumps({"status": "passed", "screenshots": str(OUTPUT),
                              "count": len(list(OUTPUT.glob("*.png"))), "browser_errors": errors}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()

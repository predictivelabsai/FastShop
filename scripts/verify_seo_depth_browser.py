"""Capture the entity SEO editor and SEO health page at desktop and mobile sizes."""

from __future__ import annotations

import copy
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright
from verify_admin_v2_browser import EMAIL, PASSWORD, isolate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/seo-depth"


def seed_seo_example() -> tuple[str, str]:
    from sqlalchemy import select

    from app import catalog_admin, content
    from app.db import SessionLocal
    from app.models import ProductVariant, SiteMedia, SitePage, User, VariantChannelListing

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == EMAIL))
        if not user:
            raise RuntimeError("The disposable browser user was not created")
        site = content.create_site(db, user.id, "SEO sample store", "seo-sample-store")

        site.status = "published"
        site.hostname = "browser-foundation.example.test"
        seo_settings = {
            "seo_indexing": True,
            "seo_title_format": "{page} | {store}",
            "seo_default_description": "A sample storefront for browser verification.",
        }
        site.settings_json = (site.settings_json or {}) | seo_settings
        site.published_settings_json = (site.published_settings_json or {}) | seo_settings

        home = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
            SitePage.path == "/",
        ))
        home.published_json = copy.deepcopy(home.draft_json) | {
            "seo_title": "Browser acceptance",
            "seo_description": "A sample storefront for browser verification.",
        }

        image_bytes = (ROOT / "static/img/product-cactus.jpg").read_bytes()
        image = SiteMedia(
            tenant_id=site.tenant_id,
            site_id=site.id,
            title="Sample planter image",
            alt="A terracotta planter with a small green cactus",
            content_type="image/jpeg",
            storage_key="seo-depth-sample.jpg",
            size=len(image_bytes),
            data=image_bytes,
        )
        db.add(image)
        db.flush()
        image_url = f"/site-media/{site.id}/{image.id}"
        product = catalog_admin.create_product(db, site, {
            "name": "Sample terracotta planter",
            "slug": "sample-terracotta-planter",
            "status": "active",
            "is_published": True,
            "short_description": "A terracotta planter for indoor plants.",
            "description_html": "A terracotta planter for indoor plants.",
            "seo_title": "Sample terracotta planter for indoor plants",
            "seo_description": "A terracotta planter with a smooth finish for indoor plants.",
            "options": [],
            "variants": [],
            "images": [{"media_id": image.id, "image_url": image_url, "alt": image.alt}],
            "collection_ids": [],
        })
        variant = db.scalar(select(ProductVariant).where(
            ProductVariant.product_id == product.id,
            ProductVariant.tenant_id == site.tenant_id,
        ))
        db.add(VariantChannelListing(
            variant_id=variant.id,
            channel_id=site.channel_id,
            currency="USD",
            price_minor=2495,
        ))
        product_page = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
            SitePage.product_id == product.id,
        ))
        extra_seo = {
            "seo_social_title": "Sample planter, ready for sharing",
            "seo_social_image": image_url,
            "seo_noindex": True,
        }
        product_page.draft_json = copy.deepcopy(product_page.draft_json or {}) | extra_seo
        product_page.published_json = copy.deepcopy(product_page.draft_json)
        db.commit()
        return site.id, product.id


def login(page, path: str) -> None:
    page.goto(f"/login?next={path}")
    page.get_by_label("Email address", exact=True).fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url(f"**{path}")


def main() -> None:
    import uvicorn

    sys.path.insert(0, str(ROOT))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="fastshop-seo-depth-") as data_dir:
        isolate(data_dir, port)
        from app.db import engine
        from app.main import app

        site_id, product_id = seed_seo_example()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error", access_log=False,
        ))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local verification server failed to start")
                thread.join(0.05)

            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Error:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for device, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
                        context = browser.new_context(
                            base_url=f"http://127.0.0.1:{port}",
                            viewport={"width": width, "height": height},
                        )
                        page = context.new_page()
                        page.set_default_timeout(15000)
                        login(page, "/admin/sites")

                        response = page.goto(f"/admin/sites/{site_id}/products/{product_id}")
                        assert response and response.status == 200
                        expect(page.get_by_role("heading", name="Search engine listing")).to_be_visible()
                        expect(page.locator(".c-seo-preview")).to_be_visible()
                        if device == "desktop":
                            expect(page.get_by_label("SEO title")).to_have_value(
                                "Sample terracotta planter for indoor plants",
                            )
                            page.get_by_label("SEO title").fill(
                                "Sample terracotta planter search title",
                            )
                            page.get_by_label("Meta description").fill(
                                "A clear planter description for shoppers and search results.",
                            )
                            expect(page.locator("[data-seo-preview-title]")).to_contain_text(
                                "Sample terracotta planter search title",
                            )
                        page.add_style_tag(content="[data-agent-launcher]{display:none!important}")
                        if device == "desktop":
                            page.get_by_role("region", name="Unsaved changes").get_by_role(
                                "button", name="Save", exact=True,
                            ).click()
                            page.wait_for_url(f"**/admin/sites/{site_id}/products/{product_id}*")
                            response = page.goto(f"/admin/sites/{site_id}/products/{product_id}")
                            assert response and response.status == 200
                        expect(page.get_by_label("SEO title")).to_have_value(
                            "Sample terracotta planter search title",
                        )
                        page.add_style_tag(content="[data-agent-launcher]{display:none!important}")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                        page.locator("[data-seo-editor]").screenshot(
                            path=str(OUTPUT / f"{device}-product-seo.png"),
                        )

                        response = page.goto(f"/admin/sites/{site_id}/marketing/seo-health")
                        assert response and response.status == 200
                        expect(page.get_by_role("heading", name="SEO health", exact=True)).to_be_visible()
                        expect(page.locator(".a-seo-health-row").first).to_be_visible()
                        page.add_style_tag(content="[data-agent-launcher]{display:none!important}")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                        page.screenshot(path=str(OUTPUT / f"{device}-seo-health.png"), full_page=True)
                        context.close()
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()

    print(f"SEO depth screenshots saved to {OUTPUT}")


if __name__ == "__main__":
    main()

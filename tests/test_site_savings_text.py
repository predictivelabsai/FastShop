"""Storefront text placeholders follow the savings saved in Settings -> Commerce."""

import copy

from sqlalchemy import select
from starlette.testclient import TestClient

from app import commerce
from app.db import SessionLocal
from app.main import app
from app.models import Site, SitePage
from app.site_savings_text import fill


def test_placeholders_fill_strings_inside_documents():
    savings = {"{subscription_saving}": "15%", "{first_order_saving}": "20%"}
    document = {"blocks": [{"body": "Save {subscription_saving}", "items": ["{first_order_saving} off"]}],
                "count": 3, "title": "No placeholder {here}"}
    assert fill(document, savings) == {
        "blocks": [{"body": "Save 15%", "items": ["20% off"]}], "count": 3, "title": "No placeholder {here}"}


def test_block_text_shows_the_saved_percentage():
    from app.config import settings
    from scripts.demo_site import seed_demo_site

    with SessionLocal() as db:
        seed_demo_site(db, settings.admin_email)
        db.commit()
    client = TestClient(app)
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "northstar"))
        page = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
        original = copy.deepcopy(page.published_json)
        config = commerce.settings_for(db, site, create=True)
        saved_percent = config.subscription_discount_percent
        document = copy.deepcopy(original)
        block = next(b for b in document["blocks"] if isinstance(b.get("heading"), str))
        block["heading"] = "Subscribers save {subscription_saving}"
        page.published_json = document
        db.commit()
    try:
        assert "Subscribers save 10%" in client.get("/sites/northstar/").text
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == "northstar"))
            commerce.settings_for(db, site).subscription_discount_percent = 15
            db.commit()
        text = client.get("/sites/northstar/").text
        assert "Subscribers save 15%" in text and "{subscription_saving}" not in text
    finally:
        with SessionLocal() as db:
            site = db.scalar(select(Site).where(Site.slug == "northstar"))
            db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/")).published_json = original
            commerce.settings_for(db, site).subscription_discount_percent = saved_percent
            db.commit()

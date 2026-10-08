"""Byte-level HTML regression against pre-block seeded storefront renders."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from fastcore.xml import to_xml
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.content import create_site, site_pages
from app.models import Base, SiteMenu, User
from app.site_seed import seed_h24you
from app.site_ui import storefront

GOLDEN = Path(__file__).parent / "fixtures" / "phase0_storefront_sha256.json"


def seeded_render_hashes(render=storefront):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    hashes = {}
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="render@example.test", name="Render fixture")
        db.add(user)
        db.flush()
        h24you = seed_h24you(db, user.email)
        demo = create_site(db, user.id, "Demo storefront", "demo-render")
        # SQLite timestamps have second precision; avoid random ID tie-breaks
        # in the article listing by fixing the fixture's publication ordering.
        for index, page in enumerate(site_pages(db, h24you)):
            page.created_at = datetime(2026, 1, 1) + timedelta(seconds=index)
        for page in site_pages(db, demo):
            page.published_json = page.draft_json
        db.flush()
        from app.site_menus import menus_for
        assert all({menu.name for menu in menus_for(db, site)} == {"header", "footer"} for site in (h24you, demo))
        for site in (h24you, demo):
            for page in site_pages(db, site):
                for preview in (False, True):
                    rendered = render(
                        db, site, page, f"/sites/{site.slug}", "fixed-csrf",
                        f"https://example.test{page.path}", preview=preview,
                    )
                    markup = "".join(str(to_xml(node)) for node in rendered if node is not None)
                    # Database-generated IDs are the only volatile HTML data.
                    markup = re.sub(r'data-builder-section="[^"]*"', 'data-builder-section="BLOCK"', markup)
                    markup = re.sub(
                        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32}",
                        "UUID", markup,
                    )
                    hashes[f"{site.slug}:{page.path}:{'preview' if preview else 'published'}"] = (
                        hashlib.sha256(markup.encode()).hexdigest()
                    )
    engine.dispose()
    return hashes


def test_seeded_storefront_html_matches_before_blocks():
    assert seeded_render_hashes() == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_seeded_storefront_html_matches_before_menus():
    golden = GOLDEN.with_name("phase0b_storefront_sha256.json")
    assert seeded_render_hashes() == json.loads(golden.read_text(encoding="utf-8"))


def test_legacy_fallback_matches_all_before_menu_renders():
    def without_menus(db, site, *args, **kwargs):
        db.execute(delete(SiteMenu).where(SiteMenu.site_id == site.id, SiteMenu.tenant_id == site.tenant_id))
        return storefront(db, site, *args, **kwargs)

    golden = GOLDEN.with_name("phase0b_storefront_sha256.json")
    assert seeded_render_hashes(without_menus) == json.loads(golden.read_text(encoding="utf-8"))


def test_media_registration_preserves_all_golden_renders():
    from app.site_media import backfill_media, media_for

    def registered(db, site, *args, **kwargs):
        assert backfill_media(db, site) == 0
        if site.slug == "h24you":
            assert media_for(db, site)
        return storefront(db, site, *args, **kwargs)

    assert seeded_render_hashes(registered) == json.loads(GOLDEN.read_text(encoding="utf-8"))

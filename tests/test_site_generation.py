import copy
import re
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import content
from app.compliance import scan_document
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Base, Product, Site, SiteMedia, SiteMenu, SitePage, User
from app.services import CommerceError
from app.site_blocks import normalize_document
from app.site_generation import (
    MerchantBrief,
    apply_plan,
    create_generated_site,
    generate_plan,
    generation_key,
    guided_plan,
    validate_plan,
)
from app.site_theme import CHOICES, PRESETS


@pytest.fixture
def generation_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="generation@example.test", name="Generation owner")
        db.add(owner)
        db.flush()
        yield db, owner


def brief(kind="online shop"):
    return MerchantBrief(
        business_name="North Star Goods",
        kind=kind,
        audience="design-conscious people furnishing small homes",
        tone="warm and natural",
    )


def test_guided_plan_uses_canonical_boundaries_and_shop_products():
    plan, source = generate_plan(brief(), force_guided=True)
    assert source == "guided"
    assert {page["path"] for page in plan["pages"]} >= {
        "/", "/pages/about-us", "/pages/contact", "/blogs/learn", "/shop"
    }
    assert len(plan["products"]) == 3
    assert plan["theme"] in PRESETS.values()
    assert all(plan["theme"][key] in values for key, values in CHOICES.items())
    for page in plan["pages"]:
        document = normalize_document({
            "version": 1, "title": page["title"], "description": page["description"],
            "blocks": page["blocks"],
        })
        assert not scan_document(document)["banned"]
        assert not scan_document(document)["diseases"]
        assert all(block["id"] for block in document["blocks"])
    paths = {page["path"] for page in plan["pages"]}
    assert all(item["path"] in paths for items in plan["menus"].values() for item in items)


def test_non_shop_guided_plan_has_no_catalog_seeds():
    plan, _ = generate_plan(brief("SaaS"), force_guided=True)
    assert plan["products"] == []
    assert "/shop" not in {page["path"] for page in plan["pages"]}


def test_llm_path_repairs_invalid_plan_twice_at_most():
    valid = guided_plan(brief())
    calls = []

    def fixture(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            result = copy.deepcopy(valid)
            result["theme"]["font"] = "unsupported"
            return result
        return copy.deepcopy(valid)

    plan, source = generate_plan(brief(), provider=fixture)
    assert source == "llm"
    assert plan["theme"]["font"] == valid["theme"]["font"]
    assert len(calls) == 2
    assert "failed validation" in calls[1]

    failed_calls = []

    def always_invalid(prompt):
        failed_calls.append(prompt)
        return {"unsupported": True}

    with pytest.raises(CommerceError, match="after two repairs"):
        generate_plan(brief(), provider=always_invalid)
    assert len(failed_calls) == 3


def test_plan_rejects_unsafe_media_and_banned_claims():
    unsafe = guided_plan(brief())
    unsafe["pages"][0]["blocks"][0]["image"] = "https://example.test/generated.jpg"
    with pytest.raises(CommerceError, match="approved local placeholder"):
        validate_plan(unsafe)
    unsafe = guided_plan(brief())
    unsafe["pages"][0]["blocks"][0]["body"] = {"en": "A guaranteed miracle."}
    with pytest.raises(CommerceError, match="cannot be published"):
        validate_plan(unsafe)
    with pytest.raises(CommerceError, match="provider secrets"):
        generate_plan(MerchantBrief(
            "Secret test", "SaaS", "teams using sk-abcdefghijklmnop", "minimal"
        ), force_guided=True)


def test_apply_plan_is_draft_tenant_scoped_and_idempotent(generation_db):
    db, owner = generation_db
    plan, source = generate_plan(brief(), force_guided=True)
    site = create_generated_site(db, owner.id, brief(), plan, source)
    db.flush()
    page_count = db.scalar(select(func.count()).select_from(SitePage).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id
    ))
    product_count = db.scalar(select(func.count()).select_from(Product).where(
        Product.tenant_id == site.tenant_id
    ))
    menu_count = db.scalar(select(func.count()).select_from(SiteMenu).where(
        SiteMenu.site_id == site.id, SiteMenu.tenant_id == site.tenant_id
    ))
    version = site.version
    assert site.status == "draft"
    assert page_count == len(plan["pages"])
    assert product_count == len(plan["products"])
    assert menu_count == 2
    assert all(menu.published_items_json is None for menu in db.scalars(select(SiteMenu).where(
        SiteMenu.site_id == site.id, SiteMenu.tenant_id == site.tenant_id
    )))
    assert site.settings_json["design"] == plan["theme"]
    assert "design" not in site.published_settings_json

    same = apply_plan(
        db, site, owner.id, plan, expected_version=version,
        key=generation_key(brief()), source=source,
    )
    assert same.version == version
    assert db.scalar(select(func.count()).select_from(SitePage).where(
        SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id
    )) == page_count
    with pytest.raises(CommerceError, match="already has generated content"):
        apply_plan(db, site, owner.id, plan, expected_version=version, key="different", source=source)

    other = content.create_site(db, owner.id, "Other tenant", "other-" + uuid4().hex[:8])
    other_pages = len(content.site_pages(db, other))
    merchant_page = content.create_page(
        db, site, "Merchant addition", "/pages/merchant-addition", document={
            "title": "Merchant addition",
            "sections": [{"type": "text", "heading": "Keep me", "body": "Merchant copy"}],
        },
    )
    apply_plan(
        db, site, owner.id, plan, expected_version=version,
        key="different", source=source, retry=True, brief=brief(),
    )
    assert len(content.site_pages(db, other)) == other_pages
    assert all(page.tenant_id == site.tenant_id for page in content.site_pages(db, site))
    assert content.site_page(db, site, merchant_page.id).path == "/pages/merchant-addition"


def signed_in():
    client = TestClient(app)
    response = client.get("/login?next=/admin/sites")
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    response = client.post("/login", data={
        "csrf_token": token, "email": settings.admin_email, "password": settings.admin_password,
    })
    assert response.status_code == 200
    return client, token


def test_generation_route_creates_draft_and_lands_in_builder(monkeypatch):
    import app.site_generation as generation

    original = generation.generate_plan
    monkeypatch.setattr(
        generation, "generate_plan", lambda value: original(value, force_guided=True)
    )
    client, token = signed_in()
    name = "Route generation " + uuid4().hex[:8]
    response = client.post("/admin/sites/generate", data={
        "csrf_token": token, "business_name": name, "kind": "local service",
        "audience": "busy homeowners", "tone": "minimal and precise",
    })
    assert response.status_code == 200
    assert "/build?" in str(response.url)
    assert "Private draft generated with guided presets" in response.text
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.name == name))
        assert site is not None and site.status == "draft"
        assert site.settings_json["site_generation"]["source"] == "guided"
        pages = content.site_pages(db, site)
        assert len(pages) == 4
        assert not list(db.scalars(select(Product).where(Product.tenant_id == site.tenant_id)))
        media_count = db.scalar(select(func.count()).select_from(SiteMedia).where(
            SiteMedia.tenant_id == site.tenant_id, SiteMedia.site_id == site.id
        ))
        site_id, version, page_id = site.id, site.version, pages[0].id
    action = f"/admin/sites/{site_id}/build/imagery"
    rejected = client.post(action, data={"version": version, "page_id": page_id})
    assert rejected.status_code == 400
    assert "session expired" in rejected.text.lower()
    response = client.post(action, data={
        "csrf_token": token, "version": version, "page_id": page_id,
    })
    assert response.status_code == 200
    assert "Imagery resolved:" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SiteMedia).where(
            SiteMedia.site_id == site_id
        )) == media_count


def test_generation_route_surfaces_invalid_brief_as_notice():
    client, token = signed_in()
    response = client.post("/admin/sites/generate", data={
        "csrf_token": token, "business_name": "", "kind": "SaaS",
        "audience": "teams", "tone": "minimal and precise",
    })
    assert response.status_code == 200
    assert response.url.path == "/admin/sites"
    assert "Enter business name" in response.text


def test_offline_generation_evals_cover_both_paths():
    from evals.site_generation import CASES, run

    summary = run()
    assert len(CASES) >= 10
    assert summary["runs"] == len(CASES) * 2
    assert summary["passed"] == summary["runs"]
    assert {row["mode"] for row in summary["cases"]} == {"guided", "mocked-llm"}

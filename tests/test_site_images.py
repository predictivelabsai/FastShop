import copy
import io

import pytest
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import content
from app.models import Base, Membership, SiteMedia, Tenant, User, new_id
from app.services import CommerceError
from app.site_blocks import normalize_document, resolve_text
from app.site_generation import MerchantBrief, create_generated_site, generate_plan
from app.site_images import (
    MAX_IMAGES_PER_RESOLUTION,
    image_provider,
    register_image_provider,
    resolve_site_imagery,
)


def brief():
    return MerchantBrief(
        "Image seam shop",
        "online shop",
        "people furnishing compact homes",
        "warm and natural",
    )


@pytest.fixture
def image_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="site-images@example.test", name="Image seam owner")
        db.add(owner)
        db.flush()
        # Platform-tenant precedent (app/seed.py): image-seam tests provision
        # extra sites for one owner, so the account holds a pro-tier membership
        # and app/plans.py free-account quotas never limit this coverage.
        platform = Tenant(name="Image seam platform", slug=f"images-{new_id()}", plan="pro")
        db.add(platform)
        db.flush()
        db.add(Membership(tenant_id=platform.id, user_id=owner.id, role="admin"))
        db.flush()
        yield db, owner


def _visual_urls(db, site):
    result = {}
    for page in content.site_pages(db, site):
        document = normalize_document(page.draft_json)
        for block in document["blocks"]:
            if block["type"] in {"hero", "split", "product"}:
                result[(page.path, block["id"])] = resolve_text(
                    block.get("image", ""), "en"
                )
    return result


def test_default_provider_resolves_owned_media_and_is_idempotent(image_db):
    db, owner = image_db
    plan, source = generate_plan(brief(), force_guided=True)
    site = create_generated_site(db, owner.id, brief(), plan, source)
    urls = _visual_urls(db, site)
    assert len(urls) == len(plan["imagery"]) <= MAX_IMAGES_PER_RESOLUTION
    assert all(url.startswith(f"/site-media/{site.id}/") for url in urls.values())
    content.validate_media_ownership(db, site, list(urls.values()))

    rows = list(db.scalars(select(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.site_id == site.id,
    )))
    assert len(rows) == len(plan["imagery"])
    assert all(row.is_placeholder and row.content_type == "image/webp" and row.data for row in rows)
    assert all(row.title.startswith("Image direction · ") for row in rows)
    count = len(rows)

    result = resolve_site_imagery(db, site, owner.id)
    assert result.total == count
    assert result.provider_calls == 0
    assert db.scalar(select(func.count()).select_from(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.site_id == site.id,
    )) == count


def test_static_placeholder_resolution_skips_provider_and_media_rows(image_db):
    db, owner = image_db
    plan, source = generate_plan(brief(), force_guided=True)
    for item in plan["imagery"]:
        item["placeholder_url"] = "/static/h24you/water-placeholder.webp"

    class FailingProvider:
        def generate(self, direction, size, key):
            raise AssertionError("static placeholders must not call the provider")

    site = create_generated_site(
        db, owner.id, brief(), plan, source, image_provider=FailingProvider()
    )
    assert set(_visual_urls(db, site).values()) == {
        "/static/h24you/water-placeholder.webp"
    }
    assert not list(db.scalars(select(SiteMedia).where(
        SiteMedia.tenant_id == site.tenant_id,
        SiteMedia.site_id == site.id,
    )))


def test_resolution_preserves_manual_image_and_rejects_foreign_media(image_db):
    db, owner = image_db
    plan, source = generate_plan(brief(), force_guided=True)
    site = create_generated_site(db, owner.id, brief(), plan, source)
    home = next(page for page in content.site_pages(db, site) if page.path == "/")
    document = normalize_document(home.draft_json)
    document["blocks"][0]["image"] = {"en": "/static/h24you/bottle-placeholder.webp"}
    home.draft_json = document
    db.flush()
    result = resolve_site_imagery(db, site, owner.id)
    assert result.preserved == 1
    assert resolve_site_imagery(db, site, owner.id).preserved == 1
    assert _visual_urls(db, site)[("/", "home-hero")] == (
        "/static/h24you/bottle-placeholder.webp"
    )

    other = content.create_site(db, owner.id, "Other images", "other-images")
    foreign = SiteMedia(
        tenant_id=other.tenant_id,
        site_id=other.id,
        title="Foreign",
        alt="Foreign image",
        content_type="image/webp",
        storage_key="foreign.webp",
        size=3,
        data=b"abc",
    )
    db.add(foreign)
    db.flush()
    foreign_url = f"/site-media/{other.id}/{foreign.id}"

    class ForeignProvider:
        def generate(self, direction, size, key):
            return foreign_url

    unsafe_plan = copy.deepcopy(plan)
    with pytest.raises(CommerceError, match="belonging to this site"):
        create_generated_site(
            db,
            owner.id,
            MerchantBrief("Unsafe image site", "online shop", "homeowners", "minimal"),
            unsafe_plan,
            source,
            image_provider=ForeignProvider(),
        )


def test_provider_registry_is_operator_selected_and_bytes_are_validated():
    image = io.BytesIO()
    Image.new("RGB", (400, 320), "#336699").save(image, "PNG")

    class FixtureProvider:
        def generate(self, direction, size, key):
            return image.getvalue()

    register_image_provider("fixture-provider", lambda _theme: FixtureProvider())
    name, provider = image_provider("fixture-provider")
    assert name == "fixture-provider"
    assert provider.generate("A quiet studio", (400, 320), "key").startswith(b"\x89PNG")
    with pytest.raises(CommerceError, match="not registered"):
        image_provider("merchant-supplied-provider")

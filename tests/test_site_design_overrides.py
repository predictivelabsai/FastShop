"""Tenant-scoped, sanitised storefront design overrides."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import content
from app import site_design_overrides as overrides
from app.models import Base, SiteMedia, User
from app.services import CommerceError


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="design-overrides@example.test", name="Design owner")
        other_owner = User(email="other-design-overrides@example.test", name="Other owner")
        db.add_all([owner, other_owner])
        db.flush()
        site = content.create_site(db, owner.id, "Design overrides", "design-overrides")
        other_site = content.create_site(db, other_owner.id, "Other design", "other-design")
        db.commit()
        yield db, owner, site, other_owner, other_site
    engine.dispose()


def test_sanitiser_scopes_rules_and_allows_media_and_supports(workspace):
    db, owner, site, _, _ = workspace
    media = SiteMedia(
        tenant_id=site.tenant_id,
        site_id=site.id,
        title="Surface texture",
        alt="Surface texture",
        content_type="image/webp",
        storage_key="surface.webp",
    )
    db.add(media)
    db.flush()

    raw_css = """
        .hero, .card:hover { background-image: url('/static/h24you/liquid/tablets.jpg'); }
        .texture { background-image: url('/site-media/{site_id}/{media_id}'); }
        @media (max-width: 700px) { .hero { padding: 1rem; } }
        @supports (display: grid) { .grid { display: grid; } }
    """
    css = overrides.validate(db, site.id, owner.id, raw_css.replace("{site_id}", site.id).replace(
        "{media_id}", media.id,
    ))

    assert "[data-fs-site-root] .hero" in css
    assert "[data-fs-site-root] .card:hover" in css
    assert "[data-fs-site-root] .texture" in css
    assert "@media (max-width: 700px)" in css
    assert "@supports (display: grid)" in css
    assert "/static/h24you/liquid/tablets.jpg" in css
    assert f"/site-media/{site.id}/{media.id}" in css


@pytest.mark.parametrize(("css", "message"), [
    ("@import url('/static/theme.css');", "@import"),
    ("@font-face { src: url('/static/font.woff2'); }", "@font-face"),
    ("@namespace svg url('/static/ns');", "@namespace"),
    ("@charset 'utf-8';", "@charset"),
    ("body { color: red; }", "body"),
    (":root { color: red; }", ":root"),
    (".card { background: url('https://example.test/tracker.png'); }", "same-origin"),
    (".card { background: url('data:image/svg+xml,evil'); }", "same-origin"),
    (".card { background: url('javascript:alert(1)'); }", "same-origin"),
    (".card { width: expression(alert(1)); }", "expression"),
    (".card { behavior: url('/static/legacy.htc'); }", "behavior"),
    (".card { -moz-binding: url('/static/legacy.xml'); }", "-moz-binding"),
    (".card { color: red; }</style><script>alert(1)</script>", "</style"),
])
def test_sanitiser_rejects_unsafe_css(workspace, css, message):
    db, owner, site, _, _ = workspace

    with pytest.raises(CommerceError, match=message):
        overrides.validate(db, site.id, owner.id, css)


def test_sanitiser_rejects_foreign_site_media_and_oversized_css(workspace):
    db, owner, site, _, other_site = workspace
    foreign_media = SiteMedia(
        tenant_id=other_site.tenant_id,
        site_id=other_site.id,
        title="Foreign media",
        alt="Foreign media",
        content_type="image/webp",
        storage_key="foreign.webp",
    )
    db.add(foreign_media)
    db.flush()

    with pytest.raises(CommerceError, match="site media"):
        overrides.validate(
            db,
            site.id,
            owner.id,
            f".card {{ background: url('/site-media/{other_site.id}/{foreign_media.id}'); }}",
        )
    with pytest.raises(CommerceError, match="50 KB"):
        overrides.validate(db, site.id, owner.id, ".card { color: red; }" * 3_000)


def test_draft_and_published_css_are_independent(workspace):
    db, owner, site, _, _ = workspace
    draft = overrides.save_draft(db, site.id, owner.id, ".hero { color: red; }", 0)

    assert draft.published_css is None
    assert "red" in overrides.css_for_storefront(db, site, preview=True)
    assert overrides.css_for_storefront(db, site) == ""

    published = overrides.publish(db, site.id, owner.id, draft.version, note="First release")
    changed = overrides.save_draft(db, site.id, owner.id, ".hero { color: blue; }", published.version)

    assert "blue" in overrides.css_for_storefront(db, site, preview=True)
    assert "red" in overrides.css_for_storefront(db, site)
    assert changed.published_version == 1
    assert overrides.history(db, site.id, owner.id)[0].note == "First release"


def test_revert_publishes_a_new_version_and_keeps_the_audit_history(workspace):
    db, owner, site, _, _ = workspace
    first = overrides.save_draft(db, site.id, owner.id, ".hero { color: red; }", 0)
    first = overrides.publish(db, site.id, owner.id, first.version, note="Red")
    second = overrides.save_draft(db, site.id, owner.id, ".hero { color: blue; }", first.version)
    second = overrides.publish(db, site.id, owner.id, second.version, note="Blue")
    original = overrides.history(db, site.id, owner.id)[-1]

    reverted = overrides.revert(db, site.id, owner.id, original.id, second.version, note="Restore red")
    history = overrides.history(db, site.id, owner.id)

    assert "red" in reverted.draft_css
    assert "red" in reverted.published_css
    assert reverted.published_version == 3
    assert len(history) == 3
    assert history[0].note == "Restore red"
    assert history[0].author_id == owner.id


def test_history_keeps_the_latest_fifty_published_versions(workspace):
    db, owner, site, _, _ = workspace
    version = 0
    for number in range(51):
        saved = overrides.save_draft(
            db,
            site.id,
            owner.id,
            f".hero {{ --design-override-version: {number}; }}",
            version,
        )
        published = overrides.publish(db, site.id, owner.id, saved.version)
        version = published.version

    history = overrides.history(db, site.id, owner.id)
    assert len(history) == 50
    assert history[0].publication_number == 51
    assert history[-1].publication_number == 2


def test_service_membership_and_tenant_scoping(workspace):
    db, owner, site, other_owner, other_site = workspace
    saved = overrides.save_draft(db, site.id, owner.id, ".hero { color: red; }", 0)
    overrides.publish(db, site.id, owner.id, saved.version)

    with pytest.raises(CommerceError, match="access denied"):
        overrides.get(db, site.id, other_owner.id)
    with pytest.raises(CommerceError, match="access denied"):
        overrides.history(db, site.id, other_owner.id)
    assert overrides.get(db, other_site.id, other_owner.id).draft_css == ""

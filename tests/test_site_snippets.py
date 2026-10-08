"""Snippet trust boundary, publication, consent classification, routes, and injection."""

import copy
import re
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import content
from app import site_snippets as snippets
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Base, Channel, Membership, Site, SiteSnippet, Tenant, User
from app.services import CommerceError


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="snippets@example.test", name="Owner")
        db.add(owner)
        db.flush()
        site = content.create_site(db, owner.id, "Snippets", "snippets-test")
        other = content.create_site(db, owner.id, "Other", "other-snippets")
        db.commit()
        yield db, owner, site, other
    engine.dispose()


@pytest.mark.parametrize(("markup", "classification"), [
    ("<meta name='verification' content='ok'>", "none"),
    ("<script type='application/ld+json'>{\"@type\":\"Organization\"}</script>", "none"),
    ("<script src='https://metrics.example.test/client.js'></script>", "analytics"),
    ("<script>window.fbq('track', 'PageView')</script>", "marketing"),
])
def test_consent_classification(markup, classification):
    assert snippets.inspect_content(markup) == classification


@pytest.mark.parametrize("markup", [
    "<script src='http://example.test/tag.js'></script>",
    "<img src='/tracking.gif'>",
    "<a href='javascript:alert(1)'>Bad</a>",
    "<img src='https://example.test/x' onload='alert(1)'>",
    "<iframe srcdoc='<script>alert(1)</script>'></iframe>",
    "</head><body>replacement</body>",
])
def test_unsafe_snippet_markup_is_rejected(markup):
    with pytest.raises(CommerceError):
        snippets.inspect_content(markup)


def test_mailto_is_allowed_only_for_links():
    assert snippets.inspect_content("<a href='mailto:hello@example.test'>Email us</a>") == "none"
    with pytest.raises(CommerceError):
        snippets.inspect_content("<img src='mailto:hello@example.test'>")


def test_draft_publication_is_site_scoped_and_fails_closed(workspace):
    db, _, site, other = workspace
    draft = {"content": "<meta name='phase1c' content='draft'>", "enabled": True, "note": {"en": "Verification", "et": "Kinnitus"}}
    snippet = snippets.put_snippet(db, site, "head", draft)
    assert snippets.published_snippets(db, site) == {placement: [] for placement in snippets.PLACEMENTS}
    snippets.put_snippet(db, site, "head", draft, publish=True)
    site.status = "published"
    assert snippets.published_snippets(db, site)["head"][0]["content"] == draft["content"]
    assert snippets.published_snippets(db, site, preview=True)["head"] == []
    assert snippets.published_snippets(db, other)["head"] == []
    changed = copy.deepcopy(draft) | {"content": "<meta name='phase1c' content='new draft'>"}
    snippets.put_snippet(db, site, "head", changed)
    assert snippet.published_json["content"] == draft["content"]


def test_snippets_bypass_claim_scanning_at_the_merchant_trust_boundary(workspace):
    db, _, site, _ = workspace
    markup = "<meta name='merchant-note' content='cure cancer'>"
    snippet = snippets.put_snippet(db, site, "head", {"content": markup, "enabled": True, "note": ""}, publish=True)
    assert snippet.published_json["content"] == markup


def test_site_delete_cascades_snippets():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        tenant = Tenant(slug="snippet-cascade", name="Cascade")
        db.add(tenant)
        db.flush()
        channel = Channel(tenant_id=tenant.id, slug="us", name="US", currency="USD", country_code="US", locale="en")
        db.add(channel)
        db.flush()
        site = Site(tenant_id=tenant.id, channel_id=channel.id, slug="snippet-cascade", name="Cascade")
        db.add(site)
        db.flush()
        site_id = site.id
        snippets.put_snippet(db, site, "foot", {"content": "<p>Safe</p>", "enabled": True, "note": ""})
        db.commit()
        db.execute(text("PRAGMA foreign_keys=ON"))
        db.execute(delete(Site).where(Site.id == site_id, Site.tenant_id == tenant.id))
        db.flush()
        assert db.scalar(select(SiteSnippet).where(SiteSnippet.site_id == site_id)) is None
    engine.dispose()


def _login(client):
    response = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    login = client.post("/login", data={
        "csrf_token": token,
        "email": settings.admin_email,
        "password": settings.admin_password,
    })
    assert login.status_code == 200
    return token


def test_snippet_forms_require_csrf_membership_version_and_inject_only_public_source():
    client = TestClient(app)
    token = _login(client)
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Snippet browser", "snippet-" + uuid4().hex[:10])
        page = next(page for page in content.site_pages(db, site) if page.path == "/")
        page.published_json = copy.deepcopy(page.draft_json)
        site.status = "published"
        site_id, slug, version, tenant_id, owner_id = site.id, site.slug, site.version, site.tenant_id, owner.id
        db.commit()
    base = f"/admin/sites/{site_id}/snippets"
    response = client.get(base)
    assert response.status_code == 200 and "Executable scripts stay inert" in response.text
    data = {
        "csrf_token": token,
        "version": version,
        "action": "publish",
        "enabled": "on",
        "note": "Browser verification",
        "content": '<meta name="phase1c-snippet" content="published">',
    }
    assert client.post(base + "/head", data=data | {"csrf_token": "bad"}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert snippets.get_snippet(db, db.get(Site, site_id), "head") is None
    response = client.post(base + "/head", data=data, follow_redirects=False)
    assert response.status_code == 303 and "Snippet+published" in response.headers["location"]
    assert client.post(base + "/head", data=data, follow_redirects=False).status_code == 303
    source = client.get(f"/sites/{slug}/").text
    head = source.split("</head>", 1)[0]
    assert '<meta name="phase1c-snippet" content="published">' in head
    assert source.count("phase1c-snippet") == 1
    assert "/static/site-snippets.js" not in source
    with SessionLocal() as db:
        membership = db.scalar(select(Membership).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == owner_id,
        ))
        membership.role = "editor"
        fresh_version = db.get(Site, site_id).version
        db.commit()
    denied = client.post(base + "/foot", data=data | {"version": fresh_version}, follow_redirects=False)
    assert denied.status_code == 303 and "access+denied" in denied.headers["location"]


def test_script_snippet_is_encoded_and_never_present_as_executable_source_before_consent():
    client = TestClient(app)
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Gated snippets", "gated-" + uuid4().hex[:10])
        page = next(page for page in content.site_pages(db, site) if page.path == "/")
        page.published_json = copy.deepcopy(page.draft_json)
        site.status = "published"
        raw = "<script>window.__phase1cLoaded = true</script>"
        snippets.put_snippet(db, site, "foot", {"content": raw, "enabled": True, "note": ""}, publish=True)
        slug = site.slug
        db.commit()
    source = client.get(f"/sites/{slug}/").text
    assert raw not in source
    assert 'data-consent="analytics"' in source
    assert "/static/site-snippets.js" in source


def test_pre_footer_and_foot_placements_surround_only_the_storefront_footer():
    client = TestClient(app)
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Placed snippets", "placed-" + uuid4().hex[:10])
        page = next(page for page in content.site_pages(db, site) if page.path == "/")
        page.published_json = copy.deepcopy(page.draft_json)
        site.status = "published"
        snippets.put_snippet(db, site, "pre-footer", {
            "content": '<div id="phase1c-pre-footer">Before</div>', "enabled": True, "note": "",
        }, publish=True)
        snippets.put_snippet(db, site, "foot", {
            "content": '<div id="phase1c-foot">After</div>', "enabled": True, "note": "",
        }, publish=True)
        slug = site.slug
        db.commit()
    source = client.get(f"/sites/{slug}/").text
    footer_start = source.index('<footer class="h-footer">')
    footer_end = source.index("</footer>", footer_start)
    assert source.index('id="phase1c-pre-footer"') < footer_start
    assert source.index('id="phase1c-foot"') > footer_end

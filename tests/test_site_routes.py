import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app import content
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Site, SiteChangeSet, SitePage, User
from scripts.demo_site import ARTICLES, seed_demo_site


@pytest.fixture(scope="module", autouse=True)
def editorial_fixture():
    with SessionLocal() as db:
        seed_demo_site(db, settings.admin_email)
        db.commit()


def signed_in():
    client = TestClient(app)
    response = client.get("/login?next=/admin/sites")
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    response = client.post("/login", data={"csrf_token": token, "email": settings.admin_email,
                                           "password": settings.admin_password})
    assert response.status_code == 200
    return client, token


def sample_store():
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "northstar"))
        pages = list(db.scalars(select(SitePage).where(SitePage.site_id == site.id)))
        return site, pages


def test_all_sample_pages_render_and_articles_have_references():
    client = TestClient(app)
    _, pages = sample_store()
    assert len(pages) == 17
    for page in pages:
        response = client.get("/sites/northstar" + page.path)
        assert response.status_code == 200, page.path
        assert "noindex,nofollow" in response.text
        assert response.text.count('rel="canonical"') == 1
    for article in ARTICLES:
        assert len(article["parts"]) == 2
        response = client.get("/sites/northstar/blogs/learn/" + article["slug"])
        assert article["title"] in response.text
        assert "https://example.test/guide" in response.text
        assert "References" in response.text
        assert "Northstar Goods team" in response.text
    homepage = client.get("/sites/northstar/")
    assert 'href="/sites/northstar/pages/shipping-policy"' in homepage.text


def test_draft_preview_requires_real_site_membership():
    site, pages = sample_store()
    client = TestClient(app)
    response = client.get(f"/admin/sites/{site.id}/preview/{pages[0].id}")
    assert response.status_code == 400
    assert "Sign in" in response.text
    authorized, _ = signed_in()
    response = authorized.get(f"/admin/sites/{site.id}/preview/{pages[0].id}")
    assert response.status_code == 200


def test_contact_persists_before_delivery_and_rate_limits(monkeypatch):
    import app.site_routes as routes
    monkeypatch.setattr(routes, "deliver", lambda entry, config, **_kwargs: "failed")
    client = TestClient(app)
    page = client.get("/sites/northstar/pages/contact")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    form = {"csrf_token": token, "name": "Test Visitor", "email": "visitor@example.test", "message": "A test question about the preview.", "website": ""}
    response = client.post("/sites/northstar/contact", data={**form, "csrf_token": "wrong"})
    assert response.status_code == 400
    response = client.post("/sites/northstar/contact", data=form)
    assert response.status_code == 200
    assert "Your message is saved" in response.text
    assert client.post("/sites/northstar/contact", data=form).status_code == 429
    from app.models import SiteContact
    with SessionLocal() as db:
        message = db.scalar(select(SiteContact).where(SiteContact.email == "visitor@example.test"))
        assert message.status == "failed"


def test_new_site_is_private_and_neutral(monkeypatch):
    import app.plans as plans

    monkeypatch.setattr(plans, "ensure_sites", lambda *args, **kwargs: None)
    from uuid import uuid4
    client, token = signed_in()
    slug = "test-" + uuid4().hex[:12]
    response = client.post("/admin/sites", data={"csrf_token": token, "name": "Another brand", "slug": slug})
    assert response.status_code == 200
    assert "Another brand" in response.text
    with SessionLocal() as db:
        saved = db.scalar(select(Site).where(Site.slug == slug))
        assert saved.settings_json["name"] == "Another brand"
        assert saved.settings_json["claims"] == []
        assert saved.settings_json["facts"] == []
    assert TestClient(app).get(f"/sites/{slug}/").status_code == 404


def test_no_phase2_purchase_or_marketing_submission_in_preview():
    response = TestClient(app).get("/sites/northstar/products/daily-vitamins")
    assert re.search(r'<button[^>]*disabled[^>]*>Add to cart</button>', response.text)
    assert 'action="/cart/add"' not in response.text
    assert 'action="/checkout"' not in response.text
    assert "Subscribe" in response.text
    assert 'name="marketing_email"' not in response.text


def test_custom_domain_resolves_only_its_site_and_blocks_demo_commerce():
    site, _ = sample_store()
    with SessionLocal() as db:
        saved = db.get(Site, site.id)
        previous_status = saved.status
        saved.hostname = "store-preview.example.test"
        db.commit()
    try:
        client = TestClient(app, base_url="https://store-preview.example.test")
        assert client.get("/pages/science").status_code == 404
        with SessionLocal() as db:
            db.get(Site, site.id).status = "published"
            db.commit()
        page = client.get("/pages/science")
        assert page.status_code == 200
        assert 'href="https://store-preview.example.test/pages/science"' in page.text
        assert 'href="/pages/contact"' in page.text
        for path in ("/checkout", "/cart", "/account", "/api/v1/products", "/sites/northstar/"):
            assert client.get(path).status_code == 404
        assert client.get("/healthz").status_code == 200
    finally:
        with SessionLocal() as db:
            saved = db.get(Site, site.id)
            saved.hostname = None
            saved.status = previous_status
            db.commit()


def test_custom_host_housekeeping_routes_follow_publication_and_include_published_pages():
    site, pages = sample_store()
    home = next(page for page in pages if page.path == "/")
    hostname = "store-index.example.test"
    with SessionLocal() as db:
        saved = db.get(Site, site.id)
        original_status, original_hostname = saved.status, saved.hostname
        saved_pages = list(db.scalars(select(SitePage).where(SitePage.site_id == site.id)))
        original_publications = {page.id: page.published_json for page in saved_pages}
        saved.hostname = hostname
        saved.status = "draft"
        for page in saved_pages:
            page.published_json = None
        db.get(SitePage, home.id).published_json = home.draft_json
        db.commit()

    client = TestClient(app, base_url=f"https://{hostname}")
    try:
        robots = client.get("/robots.txt")
        assert robots.status_code == 200
        assert "Disallow: /" in robots.text
        draft_map = client.get("/sitemap.xml")
        assert draft_map.status_code == 200
        assert "<loc>" not in draft_map.text

        with SessionLocal() as db:
            db.get(Site, site.id).status = "published"
            db.commit()
        robots = client.get("/robots.txt")
        assert "Allow: /" in robots.text
        assert f"Sitemap: https://{hostname}/sitemap.xml" in robots.text
        sitemap = client.get("/sitemap.xml")
        assert sitemap.status_code == 200
        assert f"<loc>https://{hostname}/</loc>" in sitemap.text
        assert "/cart" not in sitemap.text
    finally:
        with SessionLocal() as db:
            saved = db.get(Site, site.id)
            saved.status, saved.hostname = original_status, original_hostname
            for page_id, published_json in original_publications.items():
                db.get(SitePage, page_id).published_json = published_json
            db.commit()


def test_seo_settings_require_csrf_and_publish_store_metadata():
    site, _ = sample_store()
    client, token = signed_in()
    path = f"/admin/sites/{site.id}/settings/seo"
    page = client.get(path)
    assert page.status_code == 200
    assert "Search appearance" in page.text
    assert "Custom robots.txt" in page.text
    with SessionLocal() as db:
        saved = db.get(Site, site.id)
        version = saved.version
        home = db.scalar(select(SitePage).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id, SitePage.path == "/",
        ))
        home_id = home.id
    form = {
        "csrf_token": token,
        "version": str(version),
        "action": "publish",
        "home_title": "Storefront title",
        "home_description": "Storefront description",
        "title_format": "{page} | {store}",
        "default_description": "A store description",
        "social_image": "",
        "social_title": "A social title",
        "indexing": "on",
        "robots_override": "",
        "google_verification": "verify_123",
        "bing_verification": "bing-123",
    }
    rejected = client.post(path, data=form | {"csrf_token": "wrong"})
    assert rejected.status_code == 400
    response = client.post(path, data=form, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        saved = db.get(Site, site.id)
        home = db.get(SitePage, home_id)
        assert saved.settings_json["seo_default_description"] == "A store description"
        assert saved.published_settings_json["seo_google_verification"] == "verify_123"
        assert saved.published_settings_json["seo_bing_verification"] == "bing-123"
        assert home.published_json["seo_title"] == "Storefront title"
        assert home.published_json["seo_description"] == "Storefront description"


def test_unpublished_upload_is_private_even_on_public_preview_site():
    from app.models import SiteMedia
    site, _ = sample_store()
    with SessionLocal() as db:
        media = SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title="Private draft",
            alt="Private draft", content_type="image/webp", storage_key="private.webp", size=3, data=b"abc")
        db.add(media)
        db.commit()
        media_id = media.id
    url = f"/site-media/{site.id}/{media_id}"
    assert TestClient(app).get(url).status_code == 404
    merchant, _ = signed_in()
    response = merchant.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"


def test_commerce_settings_keep_fastshop_brand_and_require_csrf():
    site, _ = sample_store()
    client, token = signed_in()
    path = f"/admin/sites/{site.id}/commerce"
    response = client.get(path)
    assert response.status_code == 200
    assert "US commerce · FastShop" in response.text
    assert 'href="/admin/sites/' + site.id + '/commerce/payments">Manage in Payments</a>' in response.text
    assert any(status in response.text for status in (
        "Connected in test mode", "Connected in live mode", "Not connected",
    ))
    assert "Stripe test key: required" not in response.text
    assert "Webhook signing secret: required" not in response.text
    assert "Live payments are disabled." not in response.text
    assert 'aria-label="Store administration"' in response.text
    assert re.search(r'href="/static/admin/admin\.css\?v=\d+"', response.text)
    assert TestClient(app).get(path).status_code == 400
    form = {"csrf_token": token, "version": "1", "mode": "disabled", "origin_country": "EE",
        "site_version": str(site.version), "shipping_minor": "",
        "free_shipping_threshold_minor": "7500", "states": ["CA", "NY"]}
    assert client.post(path, data=form | {"csrf_token": "bad"}).status_code == 400
    assert client.post(path, data=form | {"mode": "live"}).status_code == 400
    assert client.post(path, data=form).status_code == 200
    response = client.post(path, data=form)
    assert response.status_code == 400
    assert "changed" in response.text


def test_stripe_payments_page_requires_membership_and_csrf():
    from uuid import uuid4

    site, _ = sample_store()
    with SessionLocal() as db:
        stored = db.get(Site, site.id)
        stored.settings_json = {"stripe_tax_status": {"test": {
            "status": "active", "missing_fields": [], "checked_at": datetime.now(UTC).isoformat()}}}
        db.commit()
    client, token = signed_in()
    path = f"/admin/sites/{site.id}/commerce/payments"
    page = client.get(path)
    assert page.status_code == 200 and "Connect Stripe" in page.text
    assert 'class="payment-status-grid"' in page.text
    assert "Not connected" in page.text and "Step 1" in page.text
    assert 'href="https://dashboard.stripe.com/test/apikeys"' in page.text
    assert "Stripe Tax: on by default" in page.text
    assert "Stripe Tax: active" in page.text
    assert "advanced" in page.text and "manual fallback" in page.text
    invalid_keys = client.post(path + "/connect", data={
        "csrf_token": token,
        "mode": "test",
        "secret_key": "sk_live_wrong_mode_12345678",
        "publishable_key": "pk_live_wrong_mode_12345678",
        "webhook_secret": "whsec_browser_manual_1234",
    }, follow_redirects=True)
    assert invalid_keys.status_code == 200
    assert 'role="alert"' in invalid_keys.text
    assert "valid Stripe test secret or restricted key" in invalid_keys.text
    rejected = client.post(path + "/connect", data={"csrf_token": "wrong", "mode": "test"},
                           follow_redirects=False)
    assert rejected.status_code == 303 and "session+expired" in rejected.headers["location"]

    with SessionLocal() as db:
        owner = User(email="stripe-other-owner-" + uuid4().hex[:8] + "@example.test", name="Other owner")
        db.add(owner)
        db.flush()
        other_site = content.create_site(db, owner.id, "Other Stripe site", "stripe-other-" + uuid4().hex[:8])
        other_id = other_site.id
        db.commit()
    denied = client.get(f"/admin/sites/{other_id}/commerce/payments")
    assert denied.status_code == 400


def test_golive_actions_use_csrf_prg_and_record_blocked_review():
    from uuid import uuid4

    client, token = signed_in()
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == settings.admin_email))
        site = content.create_site(db, owner.id, "Go-live route", "golive-route-" + uuid4().hex[:8])
        site.settings_json = {"stripe_tax_status": {"test": {
            "status": "pending", "missing_fields": ["head_office.address.city"],
            "checked_at": datetime.now(UTC).isoformat()}}}
        db.commit()
        site_id, version = site.id, site.version
    page = client.get(f"/admin/sites/{site_id}/golive")
    assert page.status_code == 200
    assert "Go-live review" in page.text and "Publish site" in page.text
    assert "Missing fields: head_office.address.city" in page.text
    assert "Payments" in page.text and "Checkout off" in page.text
    assert "sandbox" not in page.text.lower()
    assert page.text.count("Test checkout configuration is complete") == 1
    commerce_status = client.get(f"/admin/sites/{site_id}/commerce").text
    payments_status = client.get(f"/admin/sites/{site_id}/commerce/payments").text
    assert "Checkout status: Checkout off" in commerce_status
    assert "Checkout off" in payments_status

    form = {
        "csrf_token": token,
        "version": str(version),
        "reason": "The merchant reviewed the current public snapshots.",
        "confirmed": "on",
    }
    rejected = client.post(
        f"/admin/sites/{site_id}/golive/publish",
        data=form | {"csrf_token": "wrong"},
        follow_redirects=False,
    )
    assert rejected.status_code == 303 and "/golive?" in rejected.headers["location"]
    blocked = client.post(
        f"/admin/sites/{site_id}/golive/publish",
        data=form,
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    with SessionLocal() as db:
        saved = db.get(Site, site_id)
        audit = db.scalar(select(SiteChangeSet).where(
            SiteChangeSet.site_id == site_id,
            SiteChangeSet.source == "golive",
        ))
        assert saved.status == "draft"
        assert audit.after_json["audit"]["decision"] == "blocked"
    activity_page = client.get(f"/admin/sites/{site_id}/golive").text
    assert "sandbox" not in activity_page.lower()

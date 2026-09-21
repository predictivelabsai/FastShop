import re

from sqlalchemy import select
from starlette.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Site, SitePage
from app.site_articles import ARTICLES


def signed_in():
    client = TestClient(app)
    response = client.get("/login?next=/admin/sites")
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    response = client.post("/login", data={"csrf_token": token, "email": settings.admin_email,
                                           "password": settings.admin_password})
    assert response.status_code == 200
    return client, token


def h24():
    with SessionLocal() as db:
        site = db.scalar(select(Site).where(Site.slug == "h24you"))
        pages = list(db.scalars(select(SitePage).where(SitePage.site_id == site.id)))
        return site, pages


def test_all_brief_pages_render_and_articles_have_required_length():
    client = TestClient(app)
    _, pages = h24()
    assert len(pages) == 17
    for page in pages:
        response = client.get("/sites/h24you" + page.path)
        assert response.status_code == 200, page.path
        assert "noindex,nofollow" in response.text
        assert response.text.count('rel="canonical"') == 1
    for article in ARTICLES:
        count = sum(len((h + " " + b).split()) for h, b in article["parts"])
        assert 600 <= count <= 900
        response = client.get("/sites/h24you/blogs/learn/" + article["slug"])
        assert "Studies referenced" in response.text
        assert "H2 4 You team" in response.text


def test_draft_preview_requires_real_site_membership():
    site, pages = h24()
    client = TestClient(app)
    response = client.get(f"/admin/sites/{site.id}/preview/{pages[0].id}")
    assert response.status_code == 400
    assert "Sign in" in response.text
    authorized, _ = signed_in()
    response = authorized.get(f"/admin/sites/{site.id}/preview/{pages[0].id}")
    assert response.status_code == 200


def test_contact_persists_before_delivery_and_rate_limits(monkeypatch):
    import app.site_routes as routes
    monkeypatch.setattr(routes, "deliver", lambda entry, config: "failed")
    client = TestClient(app)
    page = client.get("/sites/h24you/pages/contact")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    form = {"csrf_token": token, "name": "Test Visitor", "email": "visitor@example.test", "message": "A test question about the preview.", "website": ""}
    response = client.post("/sites/h24you/contact", data={**form, "csrf_token": "wrong"})
    assert response.status_code == 400
    response = client.post("/sites/h24you/contact", data=form)
    assert response.status_code == 200
    assert "Your message is saved" in response.text
    assert client.post("/sites/h24you/contact", data=form).status_code == 429
    from app.models import SiteContact
    with SessionLocal() as db:
        message = db.scalar(select(SiteContact).where(SiteContact.email == "visitor@example.test"))
        assert message.status == "failed"


def test_new_site_is_private_and_neutral():
    from uuid import uuid4
    client, token = signed_in()
    slug = "test-" + uuid4().hex[:12]
    response = client.post("/admin/sites", data={"csrf_token": token, "name": "Another brand", "slug": slug})
    assert response.status_code == 200
    assert "Another brand" in response.text
    assert "Hydroxy" not in response.text
    assert TestClient(app).get(f"/sites/{slug}/").status_code == 404


def test_no_phase2_purchase_or_marketing_submission_in_preview():
    response = TestClient(app).get("/sites/h24you/products/hydrogen-tablets")
    assert "Add to cart — coming in Phase 2" in response.text
    assert 'action="/cart/add"' not in response.text
    assert 'action="/checkout"' not in response.text
    assert "Subscribe and save 10%" in response.text
    assert 'name="marketing_email"' not in response.text


def test_custom_domain_resolves_only_its_site_and_blocks_demo_commerce():
    site, _ = h24()
    with SessionLocal() as db:
        db.get(Site, site.id).hostname = "h24-preview.example.test"
        db.commit()
    try:
        client = TestClient(app, base_url="https://h24-preview.example.test")
        page = client.get("/pages/science")
        assert page.status_code == 200
        assert 'href="https://h24-preview.example.test/pages/science"' in page.text
        assert 'href="/pages/contact"' in page.text
        for path in ("/checkout", "/cart", "/account", "/api/v1/products", "/sites/h24you/"):
            assert client.get(path).status_code == 404
        assert client.get("/healthz").status_code == 200
    finally:
        with SessionLocal() as db:
            db.get(Site, site.id).hostname = None
            db.commit()


def test_unpublished_upload_is_private_even_on_public_preview_site():
    from app.models import SiteMedia
    site, _ = h24()
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
    site, _ = h24()
    client, token = signed_in()
    path = f"/admin/sites/{site.id}/commerce"
    response = client.get(path)
    assert response.status_code == 200
    assert "US commerce — FastShop" in response.text
    assert 'class="brand-mark"' in response.text
    assert 'href="/static/site.css"' in response.text
    assert TestClient(app).get(path).status_code == 400
    form = {"csrf_token": token, "version": "1", "mode": "disabled", "origin_country": "EE",
        "shipping_minor": "", "free_shipping_threshold_minor": "7500", "states": ["CA", "NY"]}
    assert client.post(path, data=form | {"csrf_token": "bad"}).status_code == 400
    assert client.post(path, data=form | {"mode": "live"}).status_code == 400
    assert client.post(path, data=form).status_code == 200
    response = client.post(path, data=form)
    assert response.status_code == 400
    assert "Settings changed" in response.text

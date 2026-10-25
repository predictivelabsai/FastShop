"""Custom data, storefront preferences and redirect regressions on isolated storage."""
import base64
import json
from types import SimpleNamespace

import pytest
from fasthtml.common import Div, Input, fast_app, to_xml
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import (
    catalog_admin,
    content,
    content_gap_routes,
    custom_data,
    store_preferences,
    url_redirects,
)
from app.config import settings
from app.content_gap_models import CustomDataValue
from app.models import Base, Membership, SiteMedia, SitePage, User
from app.services import CommerceError


@pytest.fixture
def workspace(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        user = User(email="gaps@example.test", name="Merchant")
        editor = User(email="gaps-editor@example.test", name="Editor")
        foreign = User(email="gaps-other@example.test", name="Other")
        db.add_all([user, editor, foreign])
        db.flush()
        site = content.create_site(db, user.id, "Gap shop", "gap-shop")
        other = content.create_site(db, foreign.id, "Other shop", "other-gap-shop")
        site.status = "preview"
        db.add(Membership(tenant_id=site.tenant_id, user_id=editor.id, role="editor"))
        product = catalog_admin.create_product(db, site, {"name": "Custom product", "slug": "custom-product"})
        alien = catalog_admin.create_product(db, other, {"name": "Private product"})
        media = SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title="Photo", alt="Photo",
                          content_type="image/png", storage_key="test", size=0)
        db.add(media)
        db.commit()
    monkeypatch.setattr(content_gap_routes, "SessionLocal", sessions)
    actor = {"id": user.id}
    app, rt = fast_app(secret_key="isolated-content-tests", default_hdrs=False)
    def csrf(session):
        session["csrf_token"] = "content-test-csrf"
        return Input(type="hidden", name="csrf_token", value="content-test-csrf")
    def check_csrf(session, form):
        if form.get("csrf_token") != session.get("csrf_token") or not session.get("csrf_token"):
            raise CommerceError("Invalid CSRF token.")
    content_gap_routes.register_content_gap_routes(rt, lambda s: actor["id"], csrf, check_csrf,
        lambda title, *children: Div(*children), lambda e: Response(str(e), 400))
    with TestClient(app) as client:
        yield SimpleNamespace(sessions=sessions, site=site, other=other, product=product, alien=alien,
            media=media, actor=actor, user=user, editor=editor, foreign=foreign, client=client)
    engine.dispose()


def definition(db, site, kind="single_line_text", **kwargs):
    return custom_data.save_definition(db, site, {"owner_type": "product", "namespace": "custom",
        "key": kind, "name": kind, "value_type": kind, **kwargs})


def test_social_sharing_image_uses_absolute_site_url(workspace):
    w = workspace
    with w.sessions() as db:
        from app.models import Site
        site = db.get(Site, w.site.id)
        site.hostname = "sharing.example.test"
        preferences = store_preferences.save_preferences(db, site, {"social_image_id": w.media.id})
        head = str(to_xml(store_preferences.head_nodes(db, site, preferences)))
        assert 'content="https://sharing.example.test/site-media/' in head
        assert w.media.id in head


@pytest.mark.parametrize("kind,value,expected", [
    ("single_line_text", "Hello", "Hello"), ("multi_line_text", "Hello\nworld", "Hello\nworld"),
    ("rich_text", '<p onclick="evil()">Safe</p><script>bad()</script>', "<p>Safe</p>"),
    ("integer", "12", 12), ("decimal", "12.35", "12.35"), ("boolean", "false", False),
    ("date", "2026-10-25", "2026-10-25"), ("url", "https://example.test/a", "https://example.test/a"),
    ("colour", "#AbC123", "#abc123"), ("json", '{"x":[1]}', {"x": [1]}),
])
def test_custom_data_types(workspace, kind, value, expected):
    w = workspace
    with w.sessions() as db:
        definition(db, w.site, kind)
        custom_data.save_values(db, w.site, "product", w.product.id, {"custom." + kind: value})
        assert custom_data.values(db, w.site, "product", w.product.id)["custom." + kind] == expected


def test_custom_data_validation_atomicity_and_site_isolation(workspace):
    w = workspace
    with w.sessions() as db:
        definition(db, w.site, "integer", validation={"required": True, "min": 1, "max": 3, "choices": [1, 2]})
        for invalid in ("", "NaN", "1.5", "0", "3", "4", True):
            with pytest.raises(CommerceError):
                custom_data.save_values(db, w.site, "product", w.product.id, {"custom.integer": invalid})
        assert not list(db.scalars(select(CustomDataValue)))
        custom_data.save_values(db, w.site, "product", w.product.id, {"custom.integer": "2"})
        with pytest.raises(CommerceError, match="not found"):
            custom_data.save_values(db, w.site, "product", w.alien.id, {"custom.integer": "1"})
        with pytest.raises(CommerceError, match="Unknown"):
            custom_data.save_values(db, w.site, "product", w.product.id, {"foreign.key": "1"})
        assert custom_data.definitions(db, w.other) == []
        duplicate = dict(owner_type="product", namespace="custom", key="integer", name="Duplicate", value_type="integer")
        with pytest.raises(CommerceError, match="already defined"):
            custom_data.save_definition(db, w.site, duplicate)


def test_references_dynamic_sources_and_product_api(workspace):
    w = workspace
    with w.sessions() as db:
        for kind, value in [("product_reference", w.product.id), ("image_reference", w.media.id),
                            ("file_reference", w.media.id), ("single_line_text", "Dynamic heading")]:
            definition(db, w.site, kind)
            custom_data.save_values(db, w.site, "product", w.product.id, {"custom." + kind: value})
        page = db.scalar(select(SitePage).where(SitePage.product_id == w.product.id))
        document = {"title": "Template", "blocks": [{"type": "hero", "heading": "metafield:custom.single_line_text",
            "image": "metafield:custom.image_reference"}]}
        resolved = custom_data.resolve_dynamic_document(db, w.site, page, document)
        assert resolved["blocks"][0]["heading"] == "Dynamic heading"
        assert resolved["blocks"][0]["image"] == f"/site-media/{w.site.id}/{w.media.id}"
        assert document["blocks"][0]["heading"].startswith("metafield:")
        sources = custom_data.compatible_sources(db, w.site)
        assert [v["source"] for v in sources["image"]] == ["metafield:custom.image_reference"]
        assert "metafield:custom.single_line_text" in [v["source"] for v in sources["text"]]
        from app.api import product_metafields
        assert product_metafields(db, w.product)[w.site.slug]["custom.single_line_text"] == "Dynamic heading"
        with pytest.raises(CommerceError):
            custom_data.save_values(db, w.site, "product", w.product.id, {"custom.product_reference": w.alien.id})


@pytest.mark.parametrize("owner_type", custom_data.OWNER_TYPES)
def test_owner_types_and_pinned_definitions(workspace, owner_type):
    w = workspace
    with w.sessions() as db:
        data = {"owner_type": owner_type, "namespace": "detail", "key": "note", "name": "Note",
                "value_type": "single_line_text", "pinned_order": 2}
        row = custom_data.save_definition(db, w.site, data)
        assert row.owner_type == owner_type
        assert custom_data.definitions(db, w.site, owner_type)[0].id == row.id
        with pytest.raises(CommerceError):
            custom_data.owner(db, w.site, owner_type, "unknown")


def test_preferences_hash_metadata_and_consent(workspace):
    w = workspace
    with w.sessions() as db:
        row = store_preferences.save_preferences(db, w.site, {"homepage_title": "New homepage", "meta_description": "Description",
            "password_enabled": True, "password": "store-pass-123", "analytics_id": "G-ABC1234567",
            "meta_pixel_id": "123456789", "social_image_id": w.media.id, "favicon_id": w.media.id})
        assert row.password_hash != "store-pass-123"
        assert "store-pass-123" not in row.password_hash
        assert store_preferences.password_matches("store-pass-123", row)
        assert not store_preferences.password_matches("wrong", row)
        page = SimpleNamespace(path="/")
        doc = {"title": "Old"}
        store_preferences.metadata(db, w.site, page, doc)
        assert doc == {"title": "New homepage", "description": "Description"}
        w.site.status = "published"
        html = str(to_xml(Div(*store_preferences.head_nodes(db, w.site, row))))
        assert "store-preferences.js" in html and 'data-analytics="G-ABC1234567"' in html
        assert "googletagmanager.com" not in html
        assert store_preferences.get_preferences(db, w.other) is None
        for key, value in [("analytics_id", "<script>"), ("meta_pixel_id", "evil"), ("favicon_id", "foreign")]:
            with pytest.raises(CommerceError):
                store_preferences.save_preferences(db, w.site, {key: value})


def test_password_gate_blocks_all_shopper_routes_and_bypasses_members(workspace, monkeypatch):
    w = workspace
    monkeypatch.setattr("app.db.SessionLocal", w.sessions)
    with w.sessions() as db:
        row = store_preferences.save_preferences(db, w.site, {"password_enabled": True, "password": "store-pass-123"})
        db.commit()
        token = store_preferences.access_token(w.site, row)
    for path in ("/", "/cart", "/checkout", "/account", "/products/custom-product"):
        scope = {"path": "/sites/" + w.site.slug + path, "headers": []}
        assert store_preferences.storefront_gate(scope).status_code == 303
        scope["headers"] = [(b"cookie", f"fastshop_store_{w.site.id}={token}".encode())]
        assert store_preferences.storefront_gate(scope) is None
    for path in ("/admin/sites/x", "/login", "/healthz", "/api/webhooks/stripe", f"/sites/{w.site.slug}/password"):
        assert store_preferences.storefront_gate({"path": path, "headers": []}) is None
    encoded = base64.b64encode(json.dumps({"user_id": w.user.id}).encode())
    signed = TimestampSigner(settings.session_secret).sign(encoded).decode()
    scope = {"path": f"/sites/{w.site.slug}/", "headers": [(b"cookie", f"session_={signed}".encode())]}
    assert store_preferences.storefront_gate(scope) is None
    scope["headers"] = [(b"cookie", b"session_=forged")]
    assert store_preferences.storefront_gate(scope).status_code == 303


def test_redirects_csv_chains_loops_and_automatic_handle_changes(workspace):
    w = workspace
    with w.sessions() as db:
        row = url_redirects.save_redirect(db, w.site, "/old", "/new")
        for source, target in [("/new", "/old"), ("/new", "/third"), ("/first", "/old"), ("/self", "/self")]:
            with pytest.raises(CommerceError):
                url_redirects.save_redirect(db, w.site, source, target)
        assert url_redirects.redirects(db, w.other) == []
        csv = url_redirects.export_csv(db, w.site)
        assert "/old,/new" in csv
        results = url_redirects.import_csv(db, w.site, "Redirect from,Redirect to\n/one,/two\n/two,/three\n")
        assert "id" in results[0] and "error" in results[1]
        url_redirects.save_redirect(db, w.site, "/old", "/changed", row.id)
        updated = catalog_admin.update_product(db, w.site, w.product.id, {"handle": "changed-handle"}, expected_version=w.product.version)
        assert url_redirects.destination(db, w.site, "/products/custom-product") == "/products/changed-handle"
        catalog_admin.update_product(db, w.site, w.product.id, {"handle": "third-handle"}, expected_version=updated.version)
        assert url_redirects.destination(db, w.site, "/products/custom-product") == "/products/third-handle"
        catalog_admin.update_product(db, w.site, w.product.id, {"handle": "fourth-handle", "create_redirect": False}, expected_version=updated.version)
        assert url_redirects.destination(db, w.site, "/products/third-handle") is None
        page = content.create_page(db, w.site, "Article", "/blog/old-article")
        url_redirects.change_page_path(db, w.site, page, "/blog/new-article")
        assert url_redirects.destination(db, w.site, "/blog/old-article") == "/blog/new-article"


@pytest.mark.parametrize("path", ["settings/custom-data", "preferences", "redirects"])
def test_routes_roles_tenant_isolation_and_csrf(workspace, path):
    w = workspace
    base = f"/admin/sites/{w.site.id}/"
    assert w.client.get(base + path).status_code == 200
    assert w.client.get(f"/admin/sites/{w.other.id}/" + path).status_code == 400
    assert w.client.post(base + path, data={}).status_code == 400
    w.actor["id"] = w.editor.id
    assert w.client.get(base + path).status_code == 200
    assert w.client.post(base + path, data={"csrf_token": "content-test-csrf"}).status_code == 400


def test_custom_data_and_redirect_route_roundtrip(workspace):
    w = workspace
    base = f"/admin/sites/{w.site.id}"
    w.client.get(base + "/settings/custom-data")
    payload = {"csrf_token": "content-test-csrf", "owner_type": "product", "namespace": "custom",
               "key": "caption", "name": "Caption", "value_type": "single_line_text", "validation": "{}"}
    assert w.client.post(base + "/settings/custom-data", data=payload).status_code == 200
    values_path = base + "/custom-data/product/" + w.product.id
    assert w.client.post(values_path, data={"csrf_token": "content-test-csrf", "custom.caption": "Stored"}).status_code == 200
    assert 'value="Stored"' in w.client.get(values_path).text
    assert w.client.post(values_path, data={"custom.caption": "No"}).status_code == 400
    w.actor["id"] = w.editor.id
    assert w.client.post(values_path, data={"csrf_token": "content-test-csrf", "custom.caption": "No"}).status_code == 400
    w.actor["id"] = w.user.id
    assert w.client.post(base + "/redirects", data={"csrf_token": "content-test-csrf", "source": "/old", "target": "/new"}).status_code == 200
    assert "/old,/new" in w.client.get(base + "/redirects/export").text
    response = w.client.post(base + "/redirects/import", data={"csrf_token": "content-test-csrf"},
        files={"file": ("redirects.csv", b"Redirect from,Redirect to\n/imported,/target\n", "text/csv")})
    assert "Imported 1 redirects" in response.text


def test_password_form_csrf_and_cookie(workspace):
    w = workspace
    with w.sessions() as db:
        store_preferences.save_preferences(db, w.site, {"password_enabled": True, "password": "store-pass-123"})
        db.commit()
    path = f"/sites/{w.site.slug}/password"
    assert w.client.get(path).status_code == 200
    assert "session expired" not in w.client.get(path).text
    bad = w.client.post(path, data={"password": "store-pass-123"}, follow_redirects=False)
    assert "Invalid CSRF" in bad.text
    good = w.client.post(path, data={"csrf_token": "content-test-csrf", "password": "store-pass-123"}, follow_redirects=False)
    assert good.status_code == 303
    assert "HttpOnly" in good.headers["set-cookie"]
    assert f"fastshop_store_{w.site.id}=" in good.headers["set-cookie"]


def test_boolean_numeric_input_rejected_and_definition_identity_frozen(workspace):
    w = workspace
    with w.sessions() as db:
        row = definition(db, w.site, "boolean")
        for value in (1, 0, 1.0, [], {}):
            with pytest.raises(CommerceError):
                custom_data.save_values(db, w.site, "product", w.product.id, {"custom.boolean": value})
        custom_data.save_values(db, w.site, "product", w.product.id, {"custom.boolean": True})
        for changed in ({"owner_type": "customer"}, {"key": "changed"}, {"namespace": "changed"}, {"value_type": "integer"}):
            with pytest.raises(CommerceError, match="cannot change"):
                custom_data.save_definition(db, w.site, {"owner_type": "product", "namespace": "custom",
                    "key": "boolean", "name": "Boolean", "value_type": "boolean", **changed}, row.id)

def test_values_for_every_owner_and_foreign_site_denials(workspace):
    from app.models import Order, ShopCustomer, SiteOrder
    w = workspace
    with w.sessions() as db:
        collection = catalog_admin.save_collection(db, w.site, {"title": "Collection", "handle": "collection"})
        customer = ShopCustomer(tenant_id=w.site.tenant_id, site_id=w.site.id,
                                email="customer@example.test", name="Customer")
        order = Order(tenant_id=w.site.tenant_id, channel_id=w.site.channel_id, number="EDIT-1",
                      idempotency_key="custom-data-order", email="customer@example.test",
                      currency="USD", subtotal_minor=0, total_minor=0)
        db.add_all([customer, order])
        db.flush()
        site_order = SiteOrder(tenant_id=w.site.tenant_id, site_id=w.site.id,
                               order_id=order.id, customer_id=customer.id)
        db.add(site_order)
        db.flush()
        page = db.scalar(select(SitePage).where(SitePage.product_id == w.product.id))
        owners = {"product": w.product.id, "variant": w.product.variants[0].id,
                  "collection": collection.id, "customer": customer.id,
                  "order": site_order.id, "page": page.id}
        for kind, identifier in owners.items():
            custom_data.save_definition(db, w.site, {"owner_type": kind, "namespace": "custom",
                "key": "note", "name": "Note", "value_type": "rich_text"})
            custom_data.save_values(db, w.site, kind, identifier,
                {"custom.note": '<p onmouseover="x()">Safe</p><script>bad()</script>'})
            assert custom_data.values(db, w.site, kind, identifier)["custom.note"] == "<p>Safe</p>"
            with pytest.raises(CommerceError):
                custom_data.save_values(db, w.other, kind, identifier, {})
            card = content_gap_routes.custom_data_card(db, w.site, kind, identifier, Input(name="csrf_token"))
            html = str(to_xml(card))
            assert "&lt;p&gt;Safe&lt;/p&gt;" in html and "onmouseover" not in html


def test_public_redirect_is_301_and_real_page_wins(workspace, monkeypatch):
    from app import site_routes
    from app.models import Site
    w = workspace
    monkeypatch.setattr(site_routes, "SessionLocal", w.sessions)
    app, rt = fast_app(secret_key="isolated-public-content-tests", default_hdrs=False)
    site_routes.register_site_routes(rt)
    with w.sessions() as db:
        db.get(Site, w.site.id).status = "published"
        url_redirects.save_redirect(db, w.site, "/previous", "/current")
        db.commit()
    with TestClient(app) as client:
        response = client.get(f"/sites/{w.site.slug}/previous", follow_redirects=False)
        assert response.status_code == 301
        assert response.headers["location"] == f"/sites/{w.site.slug}/current"
        with w.sessions() as db:
            page = content.create_page(db, w.site, "Real page", "/previous",
                document={"title": "Real page", "sections": [{"type": "text", "heading": "Real page"}]})
            page.published_json = page.draft_json
            db.commit()
        response = client.get(f"/sites/{w.site.slug}/previous", follow_redirects=False)
        assert response.status_code == 200 and "Real page" in response.text


def test_same_store_absolute_redirects_cannot_hide_chains(workspace):
    w = workspace
    with w.sessions() as db:
        url_redirects.save_redirect(db, w.site, "/first", "/second")
        w.site.hostname = "shop.example.test"
        with pytest.raises(CommerceError, match="chains"):
            url_redirects.save_redirect(db, w.site, "/second", "https://shop.example.test/first")
        with pytest.raises(CommerceError, match="chains"):
            url_redirects.save_redirect(db, w.site, "/second", settings.public_url.replace("http://", "https://") + "/sites/" + w.site.slug + "/first")


def test_theme_dynamic_image_sources_render_without_mutating_snapshot(workspace):
    from app import theme_library
    from app.site_editor_routes import render_document
    w = workspace
    with w.sessions() as db:
        definition(db, w.site, "image_reference")
        definition(db, w.site, "single_line_text")
        custom_data.save_values(db, w.site, "product", w.product.id,
            {"custom.image_reference": w.media.id, "custom.single_line_text": "Snapshot dynamic heading"})
        page = db.scalar(select(SitePage).where(SitePage.product_id == w.product.id))
        page.draft_json = content.validate_document({"title": "Product template", "sections": [
            {"type": "hero", "heading": "metafield:custom.single_line_text", "image": "metafield:custom.image_reference"}]})
        from app.models import Site
        site = db.get(Site, w.site.id)
        theme = theme_library.duplicate(db, site, w.user.id)
        preview_site, preview_page = theme_library.context(db, site, theme.id, page.id)
        content.validate_media_ownership(db, preview_site, preview_page.draft_json)
        html = render_document(db, preview_site, preview_page, preview_page.draft_json, "token")
        assert "Snapshot dynamic heading" in html
        assert f"/site-media/{w.site.id}/{w.media.id}" in html
        saved = next(p for p in theme.snapshot_json["pages"] if p["id"] == page.id)
        assert saved["document"]["blocks"][0]["image"] == "metafield:custom.image_reference"

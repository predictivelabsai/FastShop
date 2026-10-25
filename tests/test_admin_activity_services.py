"""Real admin services produce scoped fixed audit summaries, including child-only writes."""
from contextlib import contextmanager

from sqlalchemy import select

from app import (
    catalog_admin,
    content,
    customer_admin,
    draft_orders,
    growth_settings,
    order_admin,
    order_management,
    site_builder_services,
)
from app.admin_activity import activities_for, activity_context
from app.discount_routes import save as save_discount
from app.models import Membership, OrderLine, Site, VariantChannelListing, Warehouse
from tests.test_order_management import workspace

SECRET = "PrivatePayloadNeverInAudit"


def assert_logged(db, site, kind, identifier=None, action=None):
    rows = activities_for(db, site, object_type=kind, object_id=identifier or "", action=action or "")
    assert rows, f"Missing {kind} activity for {identifier}"
    assert all(SECRET not in row.summary and len(row.summary) <= 300 for row in rows)
    assert all(row.site_id == site.id and row.tenant_id == site.tenant_id for row in rows)
    assert all(row.summary == f"{row.object_type.replace('_', ' ').capitalize()} {row.action}." for row in rows)
    return rows


def test_main_catalogue_inventory_customer_discount_draft_content_settings_staff_services():
    with workspace() as w:
        db, site, actor_id = w.db, w.site, w.owner.id
        with activity_context(db, site, actor_id):
            product = catalog_admin.create_product(db, site, {"name": SECRET, "slug": "audit-product"})
            collection = catalog_admin.save_collection(db, site, {"title": SECRET, "handle": "audit-collection"})
            warehouse = Warehouse(tenant_id=site.tenant_id, code="AUDIT", name=SECRET)
            db.add(warehouse)
            db.flush()
            catalog_admin.adjust_inventory(db, site, product.variants[0].id, warehouse.id,
                                           8, reason="received", note=SECRET, user_id=actor_id)
            customer = customer_admin.save_customer(db, site, {"email": "audit@example.test", "name": SECRET})
            discount = save_discount(db, site, {"title": SECRET, "method": "automatic", "value": 10})
            draft = draft_orders.create(db, site)
            page = content.create_page(db, site, "Audit page", "/audit-page")
            content.save_page(db, site, page.id, actor_id,
                {"title": "Audit page", "sections": [{"type": "text", "heading": "Audit page", "body": SECRET}]},
                page.version, "publish")
            site_builder_services.apply_operations(db, site.id, actor_id,
                site_builder_services.snapshot(db, site), [{"op": "brand", "values": {"tagline": SECRET}}])
            membership = db.scalar(select(Membership).where(Membership.tenant_id == site.tenant_id,
                                                            Membership.user_id == w.merchant.id))
            growth_settings.change_staff(db, site, actor_id, membership.id, role="editor")
            db.commit()
        for kind, identifier in [("product", product.id), ("collection", collection.id), ("inventory", None),
                                 ("customer", customer.id), ("discount", discount.id), ("draft", draft.id),
                                 ("settings", site.id), ("staff", membership.id)]:
            assert_logged(db, site, kind, identifier)
        assert_logged(db, site, "page", page.id, "published")
        assert all(row.actor_id == actor_id for row in activities_for(db, site))


def test_child_only_price_address_comments_and_fulfilment_log_parent_objects():
    with workspace() as w:
        db, site, actor_id = w.db, w.site, w.owner.id
        product = catalog_admin.create_product(db, site, {"name": "Price only", "slug": "price-only",
            "variants": [{"sku": "AUDIT-PRICE", "price_minor": 100}]})
        db.commit()
        with activity_context(db, site, actor_id):
            catalog_admin.update_product(db, site, product.id, {"variants": [
                {"id": product.variants[0].id, "price_minor": 250}]}, expected_version=product.version)
            customer_admin.save_address(db, site, w.customer.id,
                {"line1": SECRET, "city": "Austin", "country": "US"})
            customer_admin.add_comment(db, site, w.customer.id, actor_id, SECRET)
            order_admin.add_comment(db, site, actor_id, w.link.id, SECRET)
            line = db.scalar(select(OrderLine).where(OrderLine.order_id == w.order.id))
            line.quantity, line.unit_price_minor, line.total_minor = 3, 3000, 9000
            db.flush()
            order_admin.fulfil_items(db, site, actor_id, w.link.id, {line.id: 1},
                carrier="UPS", tracking_number="AUDIT-1", tracking_url="https://ups.com/track",
                notify=False, request_key="audit-fulfilment-0001")
            db.commit()
            first_count = len(activities_for(db, site, object_type="order", object_id=w.order.id))
            # Payment and fulfilment status remain unchanged on this second partial shipment.
            order_admin.fulfil_items(db, site, actor_id, w.link.id, {line.id: 1},
                carrier="UPS", tracking_number="AUDIT-2", tracking_url="https://ups.com/track",
                notify=False, request_key="audit-fulfilment-0002")
            db.commit()
        assert db.scalar(select(VariantChannelListing.price_minor).where(
            VariantChannelListing.variant_id == product.variants[0].id)) == 250
        assert_logged(db, site, "product", product.id, "updated")
        assert len(assert_logged(db, site, "customer", w.customer.id)) >= 2
        assert len(assert_logged(db, site, "order", w.order.id)) > first_count


def test_order_return_and_refund_services_use_transaction_context_without_payloads():
    with workspace() as w:
        db, site, actor_id = w.db, w.site, w.owner.id
        with activity_context(db, site, actor_id):
            order_admin.update_order(db, site, actor_id, w.link.id, note=SECRET, tags=["reviewed"])
            line = db.scalar(select(OrderLine).where(OrderLine.order_id == w.order.id))
            line.fulfilled_quantity = line.quantity
            db.flush()
            returned = order_admin.create_return(db, site, actor_id, w.link.id, {line.id: 1}, SECRET)
            db.commit()
        assert_logged(db, site, "order", w.order.id)
        assert_logged(db, site, "return", returned.id)

        @contextmanager
        def sessions():
            with w.sessions() as session:
                with activity_context(session, site, actor_id):
                    yield session

        class Gateway:
            def __init__(self, site):
                pass

            def create_refund(self, command_id, payload):
                return {"id": "re_" + command_id, "payment_intent": payload["payment_intent"],
                    "amount": payload["amount"], "currency": "usd", "livemode": False, "status": "succeeded"}

        command = order_management.refund(site.id, actor_id, w.link.id, 1000, SECRET,
            "audit-refund-request-0001", sessions=sessions, gateway_factory=Gateway)
        db.expire_all()
        assert_logged(db, site, "refund", command.id)
        assert all(row.actor_id == actor_id for row in activities_for(db, site))


def test_collector_excludes_foreign_changes_and_unattributed_sessions():
    with workspace() as w:
        db = w.db
        other = content.create_site(db, w.editor.id, "Foreign", "foreign-audit")
        db.commit()
        with activity_context(db, w.site, w.owner.id):
            foreign_product = catalog_admin.create_product(db, other,
                {"name": SECRET, "slug": "foreign-product"})
            db.commit()
        assert activities_for(db, w.site, object_id=foreign_product.id) == []
        assert activities_for(db, other) == []
        local_product = catalog_admin.create_product(db, w.site, {"name": "Unattributed", "slug": "unattributed"})
        db.commit()
        assert activities_for(db, w.site, object_id=local_product.id) == []
        with activity_context(db, w.site, w.editor.id):
            db.get(Site, w.site.id).name = "Editor change"
            db.commit()
        assert_logged(db, w.site, "settings", w.site.id)

def test_shipping_rate_delete_route_is_scoped_csrf_protected_and_audited(monkeypatch):
    from fasthtml.common import Div, Input, fast_app
    from starlette.responses import Response
    from starlette.testclient import TestClient

    from app import growth_settings_routes
    from app.models import ShippingRate, ShippingZone
    from app.services import CommerceError

    with workspace() as w:
        zone = ShippingZone(tenant_id=w.site.tenant_id, site_id=w.site.id, name=SECRET,
                            countries_json=["US"])
        w.db.add(zone)
        w.db.flush()
        rate = ShippingRate(tenant_id=w.site.tenant_id, site_id=w.site.id, zone_id=zone.id,
                            name=SECRET, amount_minor=500)
        w.db.add(rate)
        w.db.commit()
        rate_id = rate.id
        actor = {"id": w.owner.id}

        @contextmanager
        def sessions():
            with w.sessions() as db:
                with activity_context(db, w.site, actor["id"]):
                    yield db

        monkeypatch.setattr(growth_settings_routes, "SessionLocal", sessions)
        app, rt = fast_app(secret_key="audit-shipping-route-only", default_hdrs=False)

        def check_csrf(session, form):
            if form.get("csrf_token") != "shipping-audit-csrf":
                raise CommerceError("Invalid CSRF token.")

        growth_settings_routes.register_growth_settings_routes(rt, lambda s: actor["id"],
            lambda s: Input(name="csrf_token", value="shipping-audit-csrf"), check_csrf,
            lambda title, *children: Div(*children), lambda e: Response(str(e), 400))
        path = f"/admin/sites/{w.site.id}/settings/shipping/rates"
        with TestClient(app) as client:
            payload = {"action": "delete", "rate_id": rate.id}
            assert client.post(path, data=payload, follow_redirects=False).status_code == 400
            payload["csrf_token"] = "shipping-audit-csrf"
            actor["id"] = w.editor.id
            assert client.post(path, data=payload, follow_redirects=False).status_code == 400
            actor["id"] = w.owner.id
            assert client.post(path, data=payload, follow_redirects=False).status_code == 303
        w.db.expire_all()
        assert w.db.get(ShippingRate, rate_id) is None
        assert_logged(w.db, w.site, "settings", rate_id, "deleted")

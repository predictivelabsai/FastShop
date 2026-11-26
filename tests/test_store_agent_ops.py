"""Store agent reads remain scoped and writes wait for merchant confirmation."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import catalog_admin, content, customer_admin
from app.models import (
    AgentAction,
    Base,
    CatalogueSettings,
    Discount,
    InventoryAdjustment,
    NotificationTemplate,
    OrderLine,
    Product,
    ShopCustomer,
    SiteCommerceSettings,
    SiteIntegrationCredential,
    Stock,
    TaxRate,
    User,
    VariantChannelListing,
    Warehouse,
)
from app.services import CommerceError
from app.store_agent import run_turn
from app.store_agent_ops import (
    confirm_action,
    decline_action,
    operator_capabilities,
    register_routes,
    register_tools,
)


class FakeTransport:
    def __init__(self, *replies):
        self.replies = iter(replies)

    def complete(self, messages, tools):
        return next(self.replies)


def call(name, args):
    return {"tool_calls": [{"id": "store-call", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


def test_store_action_is_pending_until_confirmed_and_executes_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="ops@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Ops", "ops-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        page = content.site_pages(db, site)[0]
        events = list(run_turn(db, site, user, "Rename it", page_id=page.id, transport=FakeTransport(
            call("update_product", {"product_id": product.id, "name": "Raspberry crate"}),
            {"content": "I proposed a product update for confirmation."})))
        action = db.scalar(select(AgentAction))
        assert action.status == "pending"
        assert product.name == "Raspberry box"
        assert any(event["type"] == "action_pending" and event["id"] == action.id for event in events)
        result = confirm_action(db, site, user, action.id)
        assert result.status == "confirmed"
        assert product.name == "Raspberry crate"
        confirm_action(db, site, user, action.id)
        assert product.name == "Raspberry crate"
    engine.dispose()


def test_operator_capabilities_return_tenant_scoped_routes_and_protected_guidance():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="directory@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Directory", "directory-test")
        taxes = operator_capabilities(site, "tax")
        assert taxes["capabilities"] == [{
            "domain": "Taxes",
            "route": f"/admin/sites/{site.id}/settings/taxes",
            "mode": "review",
            "guidance": "Tax changes require a merchant review card.",
        }]
        protected = operator_capabilities(site, "delete store")["capabilities"]
        assert protected[0]["mode"] == "protected"
        assert protected[0]["route"] == f"/admin/sites/{site.id}/settings"
        assert "not available" in protected[0]["guidance"]
    engine.dispose()


def test_integration_setup_guides_keep_credentials_in_the_protected_form():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="integration-guide@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Guides", "integration-guides")
        guide = register_tools(db, site, user.id, "conversation")["get_integration_setup_guide"].handler(
            {"provider": "shopify"}
        )
        assert guide == {
            "label": "Shopify import",
            "route": f"/admin/sites/{site.id}/settings/integrations/importers/shopify",
            "requirements": ["Shopify store domain", "Admin API access token from a custom app"],
            "next_steps": ["Enter the details in the protected connection form.",
                           "Run a preview import before applying it."],
            "provider": "shopify",
            "credential_entry_is_protected": True,
            "assistant_can_not_receive_or_reveal_credentials": True,
        }
        stripe = register_tools(db, site, user.id, "conversation")["get_integration_setup_guide"].handler(
            {"provider": "stripe"})
        assert stripe["route"] == f"/admin/sites/{site.id}/settings/payments"
        with pytest.raises(CommerceError, match="Choose Stripe"):
            register_tools(db, site, user.id, "conversation")["get_integration_setup_guide"].handler(
                {"provider": "unknown"}
            )
    engine.dispose()


def test_customer_audience_preview_is_read_only_and_never_targets_a_campaign():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="audience@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Audience", "audience-test")
        db.add_all([
            ShopCustomer(tenant_id=site.tenant_id, site_id=site.id, name="Consented VIP",
                         email="vip@example.test", tags_json=["VIP", "Early"],
                         email_marketing_consent=True),
            ShopCustomer(tenant_id=site.tenant_id, site_id=site.id, name="Unsubscribed VIP",
                         email="former-vip@example.test", tags_json=["VIP"],
                         email_marketing_consent=False),
            ShopCustomer(tenant_id=site.tenant_id, site_id=site.id, name="Other customer",
                         email="other@example.test", tags_json=["Other"],
                         email_marketing_consent=True),
        ])
        db.flush()

        tools = register_tools(db, site, user.id, "audience-preview")
        result = tools["preview_customer_audience"].handler({"filters": {"tag": "VIP"}})

        assert tools["preview_customer_audience"].kind == "read"
        assert result == {
            "filters": {"tag": "VIP"},
            "matched_customers": 2,
            "email_marketing_consent_customers": 1,
            "not_subscribed_customers": 1,
            "orders": 0,
            "paid_spend_minor": 0,
            "currency": "USD",
            "top_tags": [{"tag": "VIP", "customers": 2}, {"tag": "Early", "customers": 1}],
            "member_details_included": False,
            "note": "This is a planning preview only. It does not create a recipient list or send email.",
        }
        assert db.scalars(select(AgentAction)).all() == []
        assert "preview_customer_audience" in tools
    engine.dispose()


def test_customer_segment_waits_for_confirmation_and_never_creates_recipients():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="segments@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Segments", "segments-test")
        db.add(ShopCustomer(tenant_id=site.tenant_id, site_id=site.id, name="VIP customer",
                            email="vip-segment@example.test", tags_json=["VIP"]))
        db.flush()

        tools = register_tools(db, site, user.id, "segment-proposal")
        proposed = tools["save_customer_segment"].handler({
            "name": "VIP customers", "filters": {"tag": "VIP"},
        })
        action = db.get(AgentAction, proposed["pending_action"])

        assert tools["save_customer_segment"].kind == "store_write"
        assert customer_admin.segments_for(db, site) == []
        assert action.payload_json["filters"] == {"tag": "VIP"}
        assert action.payload_json["review"]["changes"][0]["field"] == "Segment"

        confirmed = confirm_action(db, site, user, action.id)
        listed = tools["list_customer_segments"].handler({})

        assert confirmed.status == "confirmed"
        assert listed == {
            "segments": [{
                "id": confirmed.result_json["segment_id"],
                "name": "VIP customers",
                "filters": {"tag": "VIP"},
                "matched_customers": 1,
                "route": f"/admin/sites/{site.id}/customers?segment={confirmed.result_json['segment_id']}",
            }],
            "customer_details_included": False,
            "segments_changed": False,
        }
        assert db.scalars(select(AgentAction)).all() == [action]
    engine.dispose()


def test_product_content_audit_is_read_only_and_flags_only_real_gaps():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="product-audit@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Product audit", "product-audit-test")
        incomplete = catalog_admin.create_product(db, site, {"name": "Incomplete product"})
        incomplete.description_html = ""
        incomplete.short_description = ""
        incomplete.seo_title = ""
        incomplete.seo_description = ""
        db.flush()

        tools = register_tools(db, site, user.id, "product-audit")
        result = tools["audit_product_content"].handler({"product_id": incomplete.id})

        assert tools["audit_product_content"].kind == "read"
        assert result["audited_products"] == 1
        assert result["products_with_issues"] == 1
        finding = result["products"][0]
        assert finding["product_id"] == incomplete.id
        assert {"missing_description", "missing_short_description", "missing_image",
                "missing_seo_title", "missing_seo_description"} <= set(
                    finding["issues"]
                )
        assert "missing_storefront_page" not in finding["issues"]
        assert result["catalogue_changed"] is False
        assert db.get(Product, incomplete.id).description_html == ""
        assert db.scalars(select(AgentAction)).all() == []
    engine.dispose()


def test_content_edits_remain_draft_only_and_reject_foreign_pages():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="content-agent@example.test", name="Merchant")
        other = User(email="content-agent-other@example.test", name="Other merchant")
        db.add_all([user, other])
        db.flush()
        site = content.create_site(db, user.id, "Content", "content-agent-test")
        other_site = content.create_site(db, other.id, "Other content", "other-content-agent-test")
        page = content.site_pages(db, site)[0]
        before = copy.deepcopy(page.draft_json)
        page.published_json = copy.deepcopy(before)

        events = list(run_turn(db, site, user, "Update the heading", page_id=page.id,
            transport=FakeTransport(
                call("update_block", {"page_id": page.id, "block_id": before["blocks"][0]["id"],
                    "fields": {"heading": "Draft heading only"}}),
                {"content": "Updated the draft."},
            )))
        assert not [event for event in events if event["type"] == "tool_result" and '"error"' in event["summary"]]
        assert page.draft_json["blocks"][0]["heading"] == "Draft heading only"
        assert page.published_json == before

        foreign_page = content.site_pages(db, other_site)[0]
        foreign_before = copy.deepcopy(foreign_page.draft_json)
        events = list(run_turn(db, site, user, "Update the other page", page_id=page.id,
            transport=FakeTransport(
                call("update_block", {"page_id": foreign_page.id,
                    "block_id": foreign_before["blocks"][0]["id"],
                    "fields": {"heading": "Not permitted"}}),
                {"content": "That page is unavailable."},
            )))
        assert any(event["type"] == "tool_result" and '"error"' in event["summary"]
                   for event in events)
        assert foreign_page.draft_json == foreign_before
    engine.dispose()


def test_checkout_settings_wait_for_confirmation_with_exact_review_and_stale_guard():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="checkout-agent@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Checkout", "checkout-agent-test")
        settings = SiteCommerceSettings(
            tenant_id=site.tenant_id,
            site_id=site.id,
            customer_accounts_enabled=False,
            shipping_minor=500,
            free_shipping_threshold_minor=5000,
            version=4,
        )
        db.add(settings)
        db.flush()

        action = _propose_tool(db, site, user, "update_checkout_settings", {
            "customer_accounts_enabled": True,
            "shipping_minor": 750,
            "free_shipping_threshold_minor": 9000,
        })
        assert action.status == "pending"
        assert action.payload_json["review"]["changes"] == [
            {"field": "Customer accounts", "before": "no", "after": "yes"},
            {"field": "Default shipping", "before": "$5.00", "after": "$7.50"},
            {"field": "Free shipping threshold", "before": "$50.00", "after": "$90.00"},
        ]
        assert (settings.customer_accounts_enabled, settings.shipping_minor,
                settings.free_shipping_threshold_minor, settings.version) == (False, 500, 5000, 4)

        result = confirm_action(db, site, user, action.id)
        assert result.status == "confirmed", result.result_json
        assert (settings.customer_accounts_enabled, settings.shipping_minor,
                settings.free_shipping_threshold_minor, settings.version) == (True, 750, 9000, 5)
        confirm_action(db, site, user, action.id)
        assert settings.version == 5

        stale = _propose_tool(db, site, user, "update_checkout_settings", {
            "shipping_minor": 1000,
        })
        settings.shipping_minor = 800
        settings.version += 1
        db.flush()
        assert confirm_action(db, site, user, stale.id).status == "failed"
        assert settings.shipping_minor == 800
    engine.dispose()


def test_notification_template_waits_for_confirmation_and_stays_tenant_scoped():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="notification-agent@example.test", name="Merchant")
        other = User(email="notification-agent-other@example.test", name="Other merchant")
        db.add_all([user, other])
        db.flush()
        site = content.create_site(db, user.id, "Notifications", "notification-agent-test")
        other_site = content.create_site(db, other.id, "Other notifications", "other-notification-agent-test")
        template = NotificationTemplate(
            tenant_id=site.tenant_id,
            site_id=site.id,
            event="order_confirmation",
            subject="Order received",
            body="Hello {{ customer_name }}",
            enabled=True,
        )
        foreign = NotificationTemplate(
            tenant_id=other_site.tenant_id,
            site_id=other_site.id,
            event="refund",
            subject="Foreign refund",
            body="Foreign body",
            enabled=True,
        )
        db.add_all([template, foreign])
        db.flush()

        action = _propose_tool(db, site, user, "update_notification_template", {
            "event": "order_confirmation",
            "subject": "Order {{ order_number }} confirmed",
            "body": "Hello {{ customer_name }}, thanks for your order.",
            "enabled": False,
        })
        assert action.status == "pending"
        assert action.payload_json["review"]["changes"] == [
            {"field": "Subject", "before": "Order received",
             "after": "Order {{ order_number }} confirmed"},
            {"field": "Message", "before": "Hello {{ customer_name }}",
             "after": "Hello {{ customer_name }}, thanks for your order."},
            {"field": "Enabled", "before": "yes", "after": "no"},
        ]
        assert (template.subject, template.body, template.enabled) == (
            "Order received", "Hello {{ customer_name }}", True)

        result = confirm_action(db, site, user, action.id)
        assert result.status == "confirmed", result.result_json
        assert (template.subject, template.body, template.enabled) == (
            "Order {{ order_number }} confirmed", "Hello {{ customer_name }}, thanks for your order.", False)
        confirm_action(db, site, user, action.id)
        assert (template.subject, template.body, template.enabled) == (
            "Order {{ order_number }} confirmed", "Hello {{ customer_name }}, thanks for your order.", False)

        local_refund = _propose_tool(db, site, user, "update_notification_template", {
            "event": "refund",
            "subject": "Local refund",
            "body": "Hello {{ customer_name }}",
            "enabled": True,
        })
        assert local_refund.payload_json["review"]["changes"][0]["before"] == "not set"
        assert confirm_action(db, site, user, local_refund.id).status == "confirmed"
        assert db.scalar(select(NotificationTemplate).where(
            NotificationTemplate.tenant_id == site.tenant_id,
            NotificationTemplate.site_id == site.id,
            NotificationTemplate.event == "refund",
        )).subject == "Local refund"
        assert (foreign.subject, foreign.body, foreign.enabled) == ("Foreign refund", "Foreign body", True)

        full_message = "Order update: " + "x" * 200
        long_message = _propose_tool(db, site, user, "update_notification_template", {
            "event": "shipping_confirmation",
            "subject": "Shipping update",
            "body": full_message,
            "enabled": True,
        })
        assert long_message.payload_json["review"]["changes"][1]["after"] == full_message

        stale = _propose_tool(db, site, user, "update_notification_template", {
            "event": "order_confirmation",
            "subject": "Stale subject",
            "body": "Hello {{ customer_name }}",
            "enabled": True,
        })
        template.body = "Changed elsewhere"
        db.flush()
        assert confirm_action(db, site, user, stale.id).status == "failed"
        assert template.subject == "Order {{ order_number }} confirmed"
    engine.dispose()


def test_checkout_and_notification_capabilities_are_review_gated_and_protected_settings_stay_hidden():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="protected-agent@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Protected", "protected-agent-test")
        settings = SiteCommerceSettings(tenant_id=site.tenant_id, site_id=site.id, mode="live")
        db.add(settings)
        db.flush()

        assert operator_capabilities(site, "checkout")["capabilities"][0]["mode"] == "review"
        assert operator_capabilities(site, "notifications")["capabilities"][0]["mode"] == "review"
        protected = operator_capabilities(site, "live payment mode")["capabilities"]
        assert protected[0]["mode"] == "protected"
        assert protected[0]["route"] == f"/admin/sites/{site.id}/settings/payments"

        tools = register_tools(db, site, user.id, "protected-agent-read")
        assert {"update_checkout_settings", "update_notification_template"} <= set(tools)
        assert not {"set_payment_mode", "set_payment_credentials", "update_staff_permissions", "delete_store"} & set(tools)
        assert set(tools["get_store_settings"].handler({})["payments"]) == {"connected"}

        before_actions = set(db.scalars(select(AgentAction.id)))
        with pytest.raises(CommerceError, match="supported checkout"):
            tools["update_checkout_settings"].handler({
                "customer_accounts_enabled": True,
                "mode": "live",
            })
        with pytest.raises(CommerceError, match="supported notification"):
            tools["update_notification_template"].handler({
                "event": "refund",
                "subject": "Refund update",
                "body": "Hello {{ customer_name }}",
                "enabled": True,
                "live_mode": True,
            })
        assert set(db.scalars(select(AgentAction.id))) == before_actions
    engine.dispose()


def test_store_agent_product_description_is_sanitised_on_confirm():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="rich-agent@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Rich agent", "rich-agent")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        dirty = '<h3>Ingredients</h3><p onclick="x()">Carefully chosen.</p><script>alert(1)</script>' \
                '<ul><li>Raspberry</li></ul>'
        action = _propose_tool(db, site, user, "update_product", {
            "product_id": product.id, "description": dirty,
        })
        assert action.status == "pending" and not product.description_html
        confirm_action(db, site, user, action.id)
        assert product.description_html == '<h3>Ingredients</h3><p>Carefully chosen.</p><ul><li>Raspberry</li></ul>'
        assert "alert(1)" not in product.description_html
    engine.dispose()


def test_store_product_reads_reject_cross_tenant_ids():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="owner@example.test", name="Owner")
        other = User(email="other@example.test", name="Other")
        db.add_all([owner, other])
        db.flush()
        site = content.create_site(db, owner.id, "Owner", "owner-test")
        other_site = content.create_site(db, other.id, "Other", "other-test")
        product = catalog_admin.create_product(db, other_site, {"name": "Private item"})
        page = content.site_pages(db, site)[0]
        events = list(run_turn(db, site, owner, "Read it", page_id=page.id, transport=FakeTransport(
            call("get_product", {"product_id": product.id}), {"content": "That item is unavailable."})))
        assert any(event["type"] == "tool_result" and '"error"' in event["summary"] for event in events)
        assert db.scalar(select(Product).where(Product.id == product.id)).name == "Private item"
    engine.dispose()


def test_operating_read_tools_return_bounded_store_data_without_writes():
    from app.models import ShopCustomer
    from tests.test_order_management import add_order

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="operating-reads@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Operations", "operations-test")
        customer = ShopCustomer(tenant_id=site.tenant_id, site_id=site.id,
            email="buyer@example.test", name="Buyer")
        db.add(customer)
        db.flush()
        now = datetime.now(UTC)
        current, current_link = add_order(
            db, site, customer, "OPS-CURRENT", 1200, created_at=now - timedelta(days=1),
        )
        current.status = "partially_fulfilled"
        previous, previous_link = add_order(
            db, site, customer, "OPS-PREVIOUS", 500, created_at=now - timedelta(days=8),
        )
        previous.status = "fulfilled"
        db.add(OrderLine(order_id=current.id, sku="OPS-LINE", product_name="Operating box",
            variant_name="Standard", quantity=3, fulfilled_quantity=1, unit_price_minor=400,
            total_minor=1200))
        product = catalog_admin.create_product(db, site, {"name": "Low stock box"})
        variant = product.variants[0]
        variant.sku = "LOW-STOCK"
        warehouse = Warehouse(tenant_id=site.tenant_id, code="operations", name="Operations")
        db.add_all([
            warehouse,
            CatalogueSettings(tenant_id=site.tenant_id, site_id=site.id, low_stock_threshold=2),
        ])
        db.flush()
        db.add(Stock(warehouse_id=warehouse.id, variant_id=variant.id, quantity=3, allocated=1))
        verified_at = now - timedelta(hours=1)
        synced_at = now - timedelta(minutes=30)
        db.add_all([
            SiteIntegrationCredential(tenant_id=site.tenant_id, site_id=site.id,
                provider="shopify", mode="live", encrypted_payload=b"not-for-results",
                masked_hints_json={"token": "not-for-results"}, verified_at=verified_at),
            # The assistant's own model key is protected and never listed to the model.
            SiteIntegrationCredential(tenant_id=site.tenant_id, site_id=site.id,
                provider="assistant_model", encrypted_payload=b"model-key-sentinel",
                masked_hints_json={"api_key": "model-key-sentinel"}, verified_at=synced_at),
        ])
        db.flush()

        tools = register_tools(db, site, user.id, "operating-reads")
        names = {"sales_comparison", "fulfilment_queue", "low_stock_variants", "integration_status",
                 "merchandising_brief", "customer_support_brief"}
        assert names <= set(tools)
        assert all(tools[name].kind == "read" for name in names)
        assert all(tools[name].max_output_chars == 1800 for name in names - {
            "merchandising_brief", "customer_support_brief"})
        assert tools["merchandising_brief"].max_output_chars == 4800
        assert tools["customer_support_brief"].max_output_chars == 3200
        for name in names - {"sales_comparison", "customer_support_brief"}:
            assert tools[name].schema["properties"]["limit"]["maximum"] == 20

        comparison = tools["sales_comparison"].handler({"period": "7d"})
        assert comparison["currency"] == "USD"
        assert comparison["current"]["net_revenue_minor"] == 1200
        assert comparison["previous"]["net_revenue_minor"] == 500
        assert comparison["change"]["net_revenue_minor"] == 700

        queue = tools["fulfilment_queue"].handler({})
        assert queue["orders"] == [{
            "id": current_link.id,
            "number": "OPS-CURRENT",
            "status": "partially_fulfilled",
            "items_remaining": 2,
            "created_at": current.created_at.isoformat(),
        }]

        low_stock = tools["low_stock_variants"].handler({})
        assert low_stock == {"threshold": 2, "variants": [{
            "product": "Low stock box",
            "variant": variant.name,
            "sku": "LOW-STOCK",
            "available": 2,
        }]}

        integrations = tools["integration_status"].handler({})
        assert integrations == {"integrations": [{
            "provider": "shopify",
            "status": "connected",
            "mode": "live",
            "verified_at": verified_at.replace(tzinfo=None).isoformat(),
        }], "integrations_route": f"/admin/sites/{site.id}/integrations"}
        assert "model-key-sentinel" not in json.dumps(integrations)
        merchandising = tools["merchandising_brief"].handler({"period": "7d"})
        assert merchandising["period"] == "7d"
        assert merchandising["revenue"]["net_revenue_minor"] == 1200
        assert merchandising["low_stock"] == low_stock["variants"]
        assert merchandising["catalogue"] == [{
            "product_id": product.id,
            "name": "Low stock box",
            "status": "draft",
            "variants": 1,
            "from_price_minor": None,
        }]
        support = tools["customer_support_brief"].handler({"customer_id": customer.id})
        assert support["customer"] == {
            "id": customer.id,
            "name": "Buyer",
            "email": "buyer@example.test",
            "tags": [],
            "note": "",
        }
        assert [row["id"] for row in support["orders"]] == [current_link.id, previous_link.id]
        assert list(db.scalars(select(AgentAction))) == []
    engine.dispose()


def test_operating_read_tools_stay_tenant_scoped_and_never_expose_integration_secrets():
    from app.models import ShopCustomer
    from tests.test_order_management import add_order

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="local-operations@example.test", name="Local")
        other = User(email="foreign-operations@example.test", name="Foreign")
        db.add_all([owner, other])
        db.flush()
        site = content.create_site(db, owner.id, "Local operations", "local-operations")
        foreign_site = content.create_site(db, other.id, "Foreign operations", "foreign-operations")
        customer = ShopCustomer(tenant_id=site.tenant_id, site_id=site.id,
            email="local-buyer@example.test")
        foreign_customer = ShopCustomer(tenant_id=foreign_site.tenant_id, site_id=foreign_site.id,
            email="foreign-buyer@example.test")
        db.add_all([customer, foreign_customer])
        db.flush()
        now = datetime.now(UTC)
        local_order, local_link = add_order(
            db, site, customer, "LOCAL-ORDER", 1100, created_at=now - timedelta(days=1),
        )
        foreign_order, foreign_link = add_order(
            db, foreign_site, foreign_customer, "FOREIGN-ORDER", 99999,
            created_at=now - timedelta(days=1),
        )
        db.add_all([
            OrderLine(order_id=local_order.id, sku="LOCAL-SKU", product_name="Local product",
                variant_name="Standard", quantity=1, unit_price_minor=1100, total_minor=1100),
            OrderLine(order_id=foreign_order.id, sku="FOREIGN-SKU", product_name="Foreign product",
                variant_name="Standard", quantity=1, unit_price_minor=99999, total_minor=99999),
        ])
        local_product = catalog_admin.create_product(db, site, {"name": "Local low stock"})
        foreign_product = catalog_admin.create_product(
            db, foreign_site, {"name": "FOREIGN-LOW-STOCK-SENTINEL"},
        )
        local_variant = local_product.variants[0]
        local_variant.sku = "LOCAL-LOW"
        foreign_variant = foreign_product.variants[0]
        foreign_variant.sku = "FOREIGN-LOW-SKU"
        local_warehouse = Warehouse(tenant_id=site.tenant_id, code="local", name="Local")
        foreign_warehouse = Warehouse(tenant_id=foreign_site.tenant_id, code="foreign", name="Foreign")
        db.add_all([
            local_warehouse,
            foreign_warehouse,
            CatalogueSettings(tenant_id=site.tenant_id, site_id=site.id, low_stock_threshold=2),
            CatalogueSettings(tenant_id=foreign_site.tenant_id, site_id=foreign_site.id,
                low_stock_threshold=2),
        ])
        db.flush()
        db.add_all([
            Stock(warehouse_id=local_warehouse.id, variant_id=local_variant.id, quantity=2),
            Stock(warehouse_id=foreign_warehouse.id, variant_id=foreign_variant.id, quantity=0),
            SiteIntegrationCredential(tenant_id=site.tenant_id, site_id=site.id,
                provider="local-provider", encrypted_payload=b"local-secret-sentinel",
                masked_hints_json={"token": "local-secret-sentinel"}),
            SiteIntegrationCredential(tenant_id=foreign_site.tenant_id, site_id=foreign_site.id,
                provider="FOREIGN-PROVIDER-SENTINEL", encrypted_payload=b"foreign-secret-sentinel",
                masked_hints_json={"token": "foreign-secret-sentinel"}),
        ])
        db.flush()

        tools = register_tools(db, site, owner.id, "tenant-scoped-reads")
        results = {
            "sales": tools["sales_comparison"].handler({"period": "7d"}),
            "queue": tools["fulfilment_queue"].handler({}),
            "stock": tools["low_stock_variants"].handler({}),
            "integrations": tools["integration_status"].handler({}),
            "merchandising": tools["merchandising_brief"].handler({}),
            "support": tools["customer_support_brief"].handler({"customer_id": customer.id}),
        }
        rendered = json.dumps(results, default=str)
        assert results["sales"]["current"]["net_revenue_minor"] == 1100
        assert [row["id"] for row in results["queue"]["orders"]] == [local_link.id]
        assert results["stock"]["variants"] == [{
            "product": "Local low stock",
            "variant": local_variant.name,
            "sku": "LOCAL-LOW",
            "available": 2,
        }]
        assert results["integrations"] == {"integrations": [{
            "provider": "local-provider",
            "status": "connected",
            "mode": "default",
            "verified_at": None,
        }], "integrations_route": f"/admin/sites/{site.id}/integrations"}
        assert results["merchandising"]["catalogue"] == [{
            "product_id": local_product.id,
            "name": "Local low stock",
            "status": "draft",
            "variants": 1,
            "from_price_minor": None,
        }]
        assert [row["id"] for row in results["support"]["orders"]] == [local_link.id]
        for sentinel in (
            foreign_link.id,
            "FOREIGN-ORDER",
            "FOREIGN-LOW-STOCK-SENTINEL",
            "FOREIGN-LOW-SKU",
            "FOREIGN-PROVIDER-SENTINEL",
            "local-secret-sentinel",
            "foreign-secret-sentinel",
            "foreign-cursor-sentinel",
            "encrypted_payload",
            "masked_hints_json",
        ):
            assert sentinel not in rendered
    engine.dispose()


def test_operating_read_tools_return_useful_empty_results():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="empty-operations@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Empty operations", "empty-operations")
        tools = register_tools(db, site, user.id, "empty-operating-reads")

        comparison = tools["sales_comparison"].handler({"period": "7d"})
        assert comparison["current"]["orders"] == 0
        assert comparison["previous"]["orders"] == 0
        assert comparison["message"] == "No paid orders were found in either period."
        assert tools["fulfilment_queue"].handler({}) == {
            "orders": [], "message": "No orders are awaiting fulfilment.",
        }
        assert tools["low_stock_variants"].handler({}) == {
            "threshold": 5,
            "variants": [],
            "message": "No tracked variants are at or below the low-stock threshold.",
        }
        assert tools["integration_status"].handler({}) == {
            "integrations": [], "message": "No integrations are configured for this store.",
            "integrations_route": f"/admin/sites/{site.id}/integrations",
        }
    engine.dispose()


def _propose_update(db, site, user, product, name="Raspberry crate"):
    existing_ids = set(db.scalars(select(AgentAction.id)).all())
    page = content.site_pages(db, site)[0]
    list(run_turn(db, site, user, "Update it", page_id=page.id, transport=FakeTransport(
        call("update_product", {"product_id": product.id, "name": name}),
        {"content": "I proposed a product update for confirmation."})))
    return db.scalar(select(AgentAction).where(AgentAction.id.not_in(existing_ids)))


def _propose_tool(db, site, user, name, args):
    existing_ids = set(db.scalars(select(AgentAction.id)).all())
    page = content.site_pages(db, site)[0]
    list(run_turn(db, site, user, "Please do this", page_id=page.id, transport=FakeTransport(
        call(name, args), {"content": "I proposed this change for confirmation."})))
    return db.scalar(select(AgentAction).where(AgentAction.id.not_in(existing_ids)))


def test_decline_leaves_product_unchanged():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="decline@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Decline", "decline-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        action = _propose_update(db, site, user, product)
        assert decline_action(db, site, user, action.id).status == "declined"
        assert product.name == "Raspberry box"
        assert confirm_action(db, site, user, action.id).status == "declined"
        assert product.name == "Raspberry box"
    engine.dispose()


def test_double_confirm_executes_once_and_stale_version_refuses():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="stale@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Stale", "stale-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        action = _propose_update(db, site, user, product)
        confirm_action(db, site, user, action.id)
        version = product.version
        confirm_action(db, site, user, action.id)
        assert product.name == "Raspberry crate"
        assert product.version == version

        stale = _propose_update(db, site, user, product, "Overwritten name")
        catalog_admin.update_product(db, site, product.id, {"description": "Changed elsewhere"},
                                     expected_version=product.version)
        assert confirm_action(db, site, user, stale.id).status == "failed"
        assert product.name == "Raspberry crate"
    engine.dispose()


def test_action_older_than_24_hours_expires_without_execution():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="expired@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Expired", "expired-test")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        action = _propose_update(db, site, user, product)
        action.created_at = datetime.now(UTC) - timedelta(hours=24, seconds=1)
        db.flush()
        assert confirm_action(db, site, user, action.id).status == "expired"
        assert product.name == "Raspberry box"
    engine.dispose()


def test_membership_and_tenant_scope_are_required_for_decisions():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="scope-owner@example.test", name="Owner")
        stranger = User(email="scope-stranger@example.test", name="Stranger")
        other = User(email="scope-other@example.test", name="Other")
        db.add_all([owner, stranger, other])
        db.flush()
        site = content.create_site(db, owner.id, "Scope", "scope-test")
        other_site = content.create_site(db, other.id, "Other", "scope-other")
        product = catalog_admin.create_product(db, site, {"name": "Raspberry box"})
        foreign_product = catalog_admin.create_product(db, other_site, {"name": "Private item"})
        action = _propose_update(db, site, owner, product)
        with pytest.raises(CommerceError, match="membership"):
            confirm_action(db, site, stranger, action.id)
        with pytest.raises(CommerceError, match="not found"):
            confirm_action(db, other_site, other, action.id)
        assert action.status == "pending"
        assert product.name == "Raspberry box"
        assert foreign_product.name == "Private item"
    engine.dispose()


@pytest.mark.parametrize("decision", ["confirm", "decline"])
def test_decision_routes_require_csrf(decision):
    routes = {}
    def register(path, **_kwargs):
        return lambda endpoint: routes.__setitem__(path, endpoint)

    register_routes(register, lambda _session: "user", lambda _session: "token",
                    lambda _session, _form: (_ for _ in ()).throw(CommerceError("CSRF rejected")),
                    lambda *args: None, lambda exc: {"error": str(exc)})
    endpoint = routes[f"/admin/sites/{{site_id}}/agent/actions/{{action_id}}/{decision}"]

    class Request:
        session = {}

        async def form(self):
            return {}

    import asyncio
    result = asyncio.run(endpoint("site", "action", Request()))
    assert result == {"error": "CSRF rejected"}


def test_create_product_proposal_is_read_only_and_confirmation_creates_draft_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="create-product@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Create product", "create-product-test")
        before = db.scalar(select(Product.id).where(Product.tenant_id == site.tenant_id))
        action = _propose_tool(db, site, user, "create_product", {
            "name": "Berry box", "description": "Fresh berries", "variants": [
                {"name": "Small", "price_minor": 1200}, {"name": "Large", "price_minor": 2400}]})
        assert db.scalar(select(Product.id).where(Product.tenant_id == site.tenant_id)) == before
        assert action.status == "pending"
        confirm_action(db, site, user, action.id)
        created = db.scalar(select(Product).where(Product.tenant_id == site.tenant_id,
                                                   Product.name == "Berry box"))
        assert created.status == "draft"
        assert sorted(v.price_minor for v in db.scalars(select(VariantChannelListing).where(
            VariantChannelListing.variant_id.in_([v.id for v in created.variants])))) == [1200, 2400]
        confirm_action(db, site, user, action.id)
        assert len(list(db.scalars(select(Product).where(Product.tenant_id == site.tenant_id,
                                                         Product.name == "Berry box")))) == 1
    engine.dispose()


def test_inventory_proposal_is_read_only_and_confirmation_adjusts_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="inventory@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Inventory", "inventory-test")
        product = catalog_admin.create_product(db, site, {"name": "Berry box"})
        variant = product.variants[0]
        warehouse = Warehouse(tenant_id=site.tenant_id, code="main", name="Main")
        db.add(warehouse)
        db.flush()
        action = _propose_tool(db, site, user, "adjust_inventory", {
            "variant_id": variant.id, "warehouse_id": warehouse.id, "delta": 5, "reason": "received"})
        assert db.scalar(select(Stock).where(Stock.variant_id == variant.id)) is None
        assert db.scalar(select(InventoryAdjustment.id)) is None
        confirm_action(db, site, user, action.id)
        stock = db.scalar(select(Stock).where(Stock.variant_id == variant.id))
        assert stock.quantity == 5
        assert len(list(db.scalars(select(InventoryAdjustment).where(
            InventoryAdjustment.variant_id == variant.id)))) == 1
        confirm_action(db, site, user, action.id)
        assert stock.quantity == 5
    engine.dispose()


def test_discount_create_update_and_deactivate_proposals_are_read_only_and_execute_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="discount@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Discount", "discount-test")
        create_args = {"title": "Ten off", "type": "amount_off_order", "method": "code",
                       "value_type": "percentage", "value": 10, "codes": "TENOFF"}
        action = _propose_tool(db, site, user, "create_discount", create_args)
        assert db.scalar(select(Discount.id).where(Discount.site_id == site.id)) is None
        confirm_action(db, site, user, action.id)
        assert action.status == "confirmed", action.result_json
        rule = db.scalar(select(Discount).where(Discount.site_id == site.id))
        assert rule.title == "Ten off" and rule.value == 10
        confirm_action(db, site, user, action.id)
        assert len(list(db.scalars(select(Discount).where(Discount.site_id == site.id)))) == 1

        update = _propose_tool(db, site, user, "update_discount", {
            "discount_id": rule.id, "title": "Twenty off", "type": "amount_off_order",
            "method": "code", "value_type": "percentage", "value": 20, "codes": "TWENTY"})
        assert rule.title == "Ten off" and rule.value == 10
        confirm_action(db, site, user, update.id)
        assert rule.title == "Twenty off" and rule.value == 20
        confirm_action(db, site, user, update.id)
        assert rule.value == 20

        deactivate = _propose_tool(db, site, user, "deactivate_discount", {"discount_id": rule.id})
        assert rule.is_active is True
        confirm_action(db, site, user, deactivate.id)
        assert rule.is_active is False
        confirm_action(db, site, user, deactivate.id)
        assert rule.is_active is False
    engine.dispose()


def test_shipping_proposal_is_read_only_and_confirmation_applies_once():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="shipping@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Shipping", "shipping-test")
        action = _propose_tool(db, site, user, "update_shipping_settings", {
            "flat_rate_minor": 650, "free_shipping_threshold_minor": 8000})
        assert db.scalar(select(SiteCommerceSettings.id).where(
            SiteCommerceSettings.site_id == site.id)) is None
        confirm_action(db, site, user, action.id)
        assert action.status == "confirmed", action.result_json
        settings = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id))
        assert (settings.shipping_minor, settings.free_shipping_threshold_minor) == (650, 8000)
        version = settings.version
        confirm_action(db, site, user, action.id)
        assert settings.version == version
    engine.dispose()


def test_policy_updates_wait_for_confirmation_and_match_the_policies_settings_page():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="policy-agent@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Policies", "policy-agent-test")
        site.status = "published"
        site.settings_json = {"policies": {"privacy": "Old policy.", "terms": "Keep these terms."}}
        action = _propose_tool(db, site, user, "update_store_policies", {"values": {
            "privacy": "  We respect your privacy.  ",
            "shipping_policy": "Orders ship in two business days.",
        }})
        assert action.status == "pending"
        assert site.settings_json["policies"]["privacy"] == "Old policy."
        result = confirm_action(db, site, user, action.id)
        assert result.status == "confirmed", result.result_json
        # The same keys and plain text that Settings, Policies edits.
        assert site.settings_json["policies"] == {
            "privacy": "We respect your privacy.", "terms": "Keep these terms.",
            "shipping_policy": "Orders ship in two business days."}
        before = len(db.scalars(select(AgentAction)).all())
        with pytest.raises(CommerceError, match="supported store policies"):
            register_tools(db, site, user.id, "policy-check")["update_store_policies"].handler(
                {"values": {"cookies": "Not a FastShop policy."}})
        assert len(db.scalars(select(AgentAction)).all()) == before
        version = site.version
        confirm_action(db, site, user, action.id)
        assert site.version == version
    engine.dispose()


def test_tax_rate_updates_wait_for_confirmation_and_refuse_stale_proposals():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="tax-agent@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Tax", "tax-agent-test")
        action = _propose_tool(db, site, user, "set_tax_rate", {
            "country": "US", "region": "CA", "name": "California sales tax",
            "rate_bps": 725, "prices_include_tax": False,
        })
        assert db.scalar(select(TaxRate.id).where(TaxRate.site_id == site.id)) is None
        result = confirm_action(db, site, user, action.id)
        assert result.status == "confirmed", result.result_json
        rate = db.scalar(select(TaxRate).where(TaxRate.site_id == site.id))
        assert (rate.country, rate.region, rate.rate_bps, rate.prices_include_tax) == (
            "US", "CA", 725, False)

        stale = _propose_tool(db, site, user, "set_tax_rate", {
            "country": "US", "region": "CA", "name": "California sales tax",
            "rate_bps": 800, "prices_include_tax": False,
        })
        rate.rate_bps = 750
        db.flush()
        assert confirm_action(db, site, user, stale.id).status == "failed"
        assert rate.rate_bps == 750
    engine.dispose()


def test_customer_update_is_pending_scoped_and_single_execution():
    from tests.test_order_management import workspace

    with workspace() as w:
        original_consent = w.customer.email_marketing_consent
        action = _propose_tool(w.db, w.site, w.owner, "update_customer", {
            "customer_id": w.customer.id, "tags": ["vip"], "note": "Call before delivery"})
        assert w.customer.tags_json == [] and w.customer.note == ""
        confirm_action(w.db, w.site, w.owner, action.id)
        assert action.status == "confirmed" and w.customer.tags_json == ["vip"]
        assert w.customer.note == "Call before delivery"
        assert w.customer.email_marketing_consent is original_consent
        confirm_action(w.db, w.site, w.owner, action.id)
        assert w.customer.tags_json == ["vip"]

        tools = register_tools(w.db, w.site, w.owner.id, "no-direct-email")
        # FastShop has no merchant-message mail kind, so the assistant drafts replies instead.
        assert "send_customer_email" not in tools


def test_fulfil_order_proposal_and_confirmation_use_shipment_path():
    from sqlalchemy import func

    from app.models import OrderLine, ShipmentEvent
    from tests.test_order_management import workspace

    with workspace() as w:
        line = w.db.scalar(select(OrderLine).where(OrderLine.order_id == w.order.id))
        action = _propose_tool(w.db, w.site, w.owner, "fulfil_order", {
            "order_id": w.link.id, "carrier": "UPS", "tracking_number": "TRACK-123"})
        assert line.fulfilled_quantity == 0
        assert w.db.scalar(select(func.count(ShipmentEvent.id))) == 0
        confirm_action(w.db, w.site, w.owner, action.id)
        assert action.status == "confirmed" and line.fulfilled_quantity == line.quantity
        assert w.db.scalar(select(func.count(ShipmentEvent.id))) == 1
        confirm_action(w.db, w.site, w.owner, action.id)
        assert w.db.scalar(select(func.count(ShipmentEvent.id))) == 1


def test_refund_order_validates_remainder_and_confirms_with_fake_gateway():
    from tests.test_order_management import workspace

    class Gateway:
        calls = []

        def __init__(self, _site):
            pass

        def create_refund(self, command_id, payload):
            self.calls.append((command_id, payload))
            return {"id": "re_" + command_id, "payment_intent": payload["payment_intent"],
                    "amount": payload["amount"], "currency": "usd", "status": "succeeded",
                    "livemode": False}

    with workspace() as w:
        before = w.db.scalar(select(AgentAction.id))
        _propose_tool(w.db, w.site, w.owner, "refund_order", {
            "order_id": w.link.id, "amount_minor": 10001,
            "reason": "Customer requested a refund."})
        assert w.db.scalar(select(AgentAction.id)) == before
        action = _propose_tool(w.db, w.site, w.owner, "refund_order", {
            "order_id": w.link.id, "amount_minor": 2500,
            "reason": "Customer requested a refund."})
        assert "25.00" in action.summary and "USD" in action.summary
        assert not Gateway.calls
        result = confirm_action(w.db, w.site, w.owner, action.id, gateway_factory=Gateway)
        assert result.status == "confirmed", result.result_json
        assert result.result_json["amount_minor"] == 2500
        assert len(Gateway.calls) == 1
        confirm_action(w.db, w.site, w.owner, action.id, gateway_factory=Gateway)
        assert len(Gateway.calls) == 1


def test_subscription_tools_use_versioned_merchant_change():
    from datetime import UTC, datetime, timedelta

    from app.models import CheckoutAttempt, CommerceQuote, SubscriptionContract
    from tests.test_order_management import workspace

    with workspace() as r:
        quote = CommerceQuote(tenant_id=r.site.tenant_id, site_id=r.site.id, fingerprint="agent-sub",
            provider_id="agent-sub", currency="USD", subtotal_minor=10000, discount_minor=0,
            shipping_minor=0, tax_minor=0, total_minor=10000,
            expires_at=datetime.now(UTC) + timedelta(days=1), snapshot_json={})
        r.db.add(quote)
        r.db.flush()
        attempt = CheckoutAttempt(tenant_id=r.site.tenant_id, site_id=r.site.id,
            customer_id=r.customer.id, quote_id=quote.id, request_key="agent-sub-attempt",
            request_hash="agent-sub", state="paid", order_id=r.order.id, expires_at=quote.expires_at)
        r.db.add(attempt)
        r.db.flush()
        subscription = SubscriptionContract(tenant_id=r.site.tenant_id, site_id=r.site.id,
            customer_id=r.customer.id, initial_attempt_id=attempt.id, stripe_customer_id="cus_fixture",
            payment_method_id="pm_fixture", state="active", interval_months=1, anchor_day=1,
            next_due_at=datetime.now(UTC) + timedelta(days=30), lines_json=[], destination_json={},
            recipient_name="Buyer", consent_json={}, version=1)
        r.db.add(subscription)
        r.db.flush()
        action = _propose_tool(r.db, r.site, r.owner, "pause_subscription", {
            "contract_id": subscription.id})
        assert subscription.state == "active"
        confirm_action(r.db, r.site, r.owner, action.id)
        assert action.status == "confirmed" and subscription.state == "paused"
        confirm_action(r.db, r.site, r.owner, action.id)
        assert subscription.version == 2

        stale = _propose_tool(r.db, r.site, r.owner, "resume_subscription", {
            "contract_id": subscription.id})
        subscription.version += 1
        r.db.flush()
        confirm_action(r.db, r.site, r.owner, stale.id)
        assert stale.status == "failed" and subscription.state == "paused"


def test_new_order_customer_tools_refuse_stale_proposals():
    from datetime import UTC, datetime, timedelta

    from app.models import OrderLine
    from tests.test_order_management import workspace

    with workspace() as w:
        order_action = _propose_tool(w.db, w.site, w.owner, "fulfil_order", {
            "order_id": w.link.id, "carrier": "UPS", "tracking_number": "TRACK-STALE"})
        w.order.updated_at = datetime.now(UTC) + timedelta(minutes=1)
        w.db.flush()
        assert confirm_action(w.db, w.site, w.owner, order_action.id).status == "failed"
        line = w.db.scalar(select(OrderLine).where(OrderLine.order_id == w.order.id))
        assert line.fulfilled_quantity == 0

        customer_action = _propose_tool(w.db, w.site, w.owner, "update_customer", {
            "customer_id": w.customer.id, "tags": ["vip"], "note": "Stale"})
        w.customer.updated_at = datetime.now(UTC) + timedelta(minutes=1)
        w.db.flush()
        assert confirm_action(w.db, w.site, w.owner, customer_action.id).status == "failed"
        assert w.customer.tags_json == []


def test_new_tools_reject_foreign_tenant_entities():
    from app.models import ShopCustomer
    from tests.test_order_management import workspace

    with workspace() as w:
        other_user = User(email="foreign-agent@example.test", name="Foreign")
        w.db.add(other_user)
        w.db.flush()
        foreign_site = content.create_site(w.db, other_user.id, "Foreign", "foreign-agent")
        foreign_customer = ShopCustomer(tenant_id=foreign_site.tenant_id, site_id=foreign_site.id,
            email="foreign@example.test")
        w.db.add(foreign_customer)
        w.db.flush()
        from tests.test_order_management import add_order
        _foreign_order, foreign_link = add_order(w.db, foreign_site, foreign_customer,
            "FOREIGN-ORDER", 1000)
        for name, args in (
            ("fulfil_order", {"order_id": foreign_link.id, "carrier": "UPS", "tracking_number": "123"}),
            ("refund_order", {"order_id": foreign_link.id, "amount_minor": 100,
                               "reason": "Customer requested a refund."}),
            ("update_customer", {"customer_id": foreign_customer.id, "tags": [], "note": ""}),
        ):
            before = set(w.db.scalars(select(AgentAction.id)))
            _propose_tool(w.db, w.site, w.owner, name, args)
            assert set(w.db.scalars(select(AgentAction.id))) == before

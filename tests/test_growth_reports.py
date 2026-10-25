"""Seeded reporting, merchant schedule actions and HTTP boundary coverage."""

import csv
import io
import re
from datetime import UTC, date, datetime, timedelta

import pytest
from fasthtml.common import FastHTML, Input
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.responses import Response
from starlette.testclient import TestClient

from app import growth_reports, growth_reports_routes, subscriptions
from app.models import CommerceQuote, Membership, OrderLine, SubscriptionEvent
from app.services import CommerceError
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_order_management import add_order, workspace
from tests.test_subscriptions import contract  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


def test_seeded_reports_compare_previous_and_exclude_unpaid_and_currency():
    with workspace() as w:
        day = date(2026, 10, 10)
        w.order.created_at = datetime(2026, 10, 10, tzinfo=UTC)
        w.order.discount_minor = 500
        add_order(w.db, w.site, w.customer, "PRIOR", 3000,
                  created_at=datetime(2026, 10, 9, tzinfo=UTC))
        second, _ = add_order(w.db, w.site, w.customer, "SECOND", 5000,
                  created_at=datetime(2026, 10, 10, 23, 59, tzinfo=UTC))
        w.db.add(OrderLine(order_id=second.id, sku="SKU", product_name="Other product",
            variant_name="Default", quantity=4, unit_price_minor=1250, total_minor=5000))
        add_order(w.db, w.site, w.customer, "UNPAID", 99999, payment_status="pending",
                  created_at=datetime(2026, 10, 10, tzinfo=UTC))
        add_order(w.db, w.site, w.customer, "EURO", 99999, currency="EUR",
                  created_at=datetime(2026, 10, 10, tzinfo=UTC))
        # A normal order without a SiteOrder must never enter site reporting.
        _, detached = add_order(w.db, w.site, w.customer, "UNLINKED", 88888,
                  created_at=datetime(2026, 10, 10, tzinfo=UTC))
        w.db.delete(detached)
        w.db.flush()
        report = growth_reports.report(w.db, w.site, day, day)
        summary = report["summary"]
        assert (summary.gross_sales_minor, summary.order_count,
                summary.average_order_value_minor) == (15000, 2, 7500)
        assert summary.excluded_currency_orders == 1
        assert report["previous"]["summary"].gross_sales_minor == 3000
        assert report["daily"] == [(day, 15000)]
        assert report["top_products"][0] == {
            "name": "Order product", "revenue_minor": 9000, "units": 1}
        assert report["top_units"][0]["name"] == "Other product"
        assert report["returning_rate"] == 10000
        assert report["customers"] == 1
        assert report["discounts"] == [
            ("Discounted orders", [10000, 500, 1]), ("No discount", [5000, 0, 1])]
        rows = list(csv.reader(io.StringIO(growth_reports.report_csv(report))))
        assert ["summary", "gross_sales_minor", "15000", "USD", str(day), str(day)] in rows
        assert any(row[:3] == ["product_units", "Other product", "4"] for row in rows)


def test_empty_series_ranges_and_csv_formula_escaping():
    with workspace() as w:
        start = date(2000, 1, 1)
        report = growth_reports.report(w.db, w.site, start, start + timedelta(days=2))
        assert report["daily"] == [(start + timedelta(days=n), 0) for n in range(3)]
        assert report["returning_rate"] == 0
        assert report["top_products"] == []
        report["top_products"] = [{"name": "=DANGEROUS()", "revenue_minor": 10, "units": 1}]
        assert "'=DANGEROUS()" in growth_reports.report_csv(report)
        assert growth_reports.date_range("7", today=start) == (start - timedelta(days=6), start)
        assert growth_reports.date_range("custom", "2000-01-01", "2000-01-03") == (
            start, start + timedelta(days=2))
        for preset, first, last in (("bad", "", ""), ("custom", "oops", ""),
                                    ("custom", "2001-01-01", "2000-01-01"),
                                    ("custom", "2000-01-01", "2002-01-01")):
            with pytest.raises(CommerceError):
                growth_reports.date_range(preset, first, last)


def test_free_shipping_savings_are_included_in_discount_report():
    with workspace() as w:
        day = w.order.created_at.date()
        quote = CommerceQuote(tenant_id=w.site.tenant_id, site_id=w.site.id,
            fingerprint="shipping-report", provider_id="fixture", currency="USD",
            subtotal_minor=w.order.subtotal_minor, discount_minor=0, shipping_minor=0,
            tax_minor=0, total_minor=w.order.total_minor, expires_at=datetime.now(UTC),
            snapshot_json={"discounts": [{"title": "Free delivery", "type": "free_shipping",
                                          "amount_minor": 650}]})
        w.db.add(quote)
        w.db.flush()
        from app.models import SiteOrder
        link = w.db.scalar(select(SiteOrder).where(SiteOrder.order_id == w.order.id))
        link.quote_id = quote.id
        w.db.flush()
        report = growth_reports.report(w.db, w.site, day, day)
        assert report["discounts"] == [("Free delivery", [w.order.total_minor, 650, 1])]


def test_variants_roll_up_to_one_product(ready):  # noqa: F811
    from app.models import Product, ProductVariant
    r = ready
    variant = ProductVariant(tenant_id=r.site.tenant_id, product_id=r.variant.product_id,
        sku="REPORT-SECOND", name="Second option")
    r.db.add(variant)
    r.db.flush()
    order, _ = add_order(r.db, r.site, r.customer, "VARIANT-REPORT", 800)
    for option, quantity, total in ((r.variant, 2, 200), (variant, 3, 600)):
        r.db.add(OrderLine(order_id=order.id, variant_id=option.id, sku=option.sku,
            product_name="Stored label · " + option.name, variant_name=option.name,
            quantity=quantity, unit_price_minor=total // quantity, total_minor=total))
    r.db.flush()
    report = growth_reports.report(r.db, r.site, order.created_at.date(), order.created_at.date())
    title = r.db.get(Product, r.variant.product_id).name
    product = next(row for row in report["top_products"] if row["name"] == title)
    assert product == {"name": title, "revenue_minor": 800, "units": 5}


def test_merchant_actions_share_idempotency_versions_and_actor_audit(contract):
    r, sub = contract
    owner = r.db.scalar(select(Membership).where(Membership.tenant_id == r.site.tenant_id,
                                                Membership.role == "admin"))
    actor_id = owner.user_id
    original_version = sub.version
    original_due = sub.next_due_at
    for _ in range(2):
        subscriptions.merchant_change(r.db, r.site, actor_id, sub.id, "skip",
            version=original_version, request_key="merchant-skip-request-01")
    assert sub.version == original_version + 1
    assert sub.next_due_at != original_due
    event = r.db.scalar(select(SubscriptionEvent).where(SubscriptionEvent.action == "skip"))
    assert event.details_json["merchant_actor_id"] == actor_id
    with pytest.raises(CommerceError, match="Reload"):
        subscriptions.merchant_change(r.db, r.site, actor_id, sub.id, "pause",
            version=original_version, request_key="merchant-stale-request")
    for action in ("pause", "resume", "cancel"):
        subscriptions.merchant_change(r.db, r.site, actor_id, sub.id, action,
            version=sub.version, request_key="merchant-command-" + action)
    assert sub.state == "cancelled"
    with pytest.raises(CommerceError, match="cancelled"):
        subscriptions.merchant_change(r.db, r.site, actor_id, sub.id, "resume",
            version=sub.version, request_key="merchant-cancelled-resume")


def test_merchant_cancellation_available_for_inactive_customer_and_role_scoped(contract):
    r, sub = contract
    owner = r.db.scalar(select(Membership).where(Membership.tenant_id == r.site.tenant_id,
                                                Membership.role == "admin"))
    with pytest.raises(CommerceError, match="access denied"):
        subscriptions.merchant_change(r.db, r.site, "another-user", sub.id, "cancel",
            version=sub.version, request_key="merchant-foreign-request")
    with pytest.raises(CommerceError, match="not found"):
        subscriptions.merchant_change(r.db, r.site, owner.user_id, "foreign-contract", "cancel",
            version=sub.version, request_key="merchant-foreign-contract")
    r.customer.is_active = False
    r.customer.verified_at = None
    subscriptions.merchant_change(r.db, r.site, owner.user_id, sub.id, "cancel",
        version=sub.version, request_key="merchant-inactive-cancel")
    assert sub.state == "cancelled"


def isolated_client(monkeypatch, db, actor_id):
    """Exercise the registrar without importing the bootstrapping app.main."""
    app = FastHTML(secret_key="test-only-session-key")
    monkeypatch.setattr(growth_reports_routes, "SessionLocal",
                        sessionmaker(bind=db.bind, expire_on_commit=False))

    def csrf(session):
        session["csrf_token"] = "growth-test-csrf"
        return Input(type="hidden", name="csrf_token", value=session["csrf_token"])

    def check_csrf(session, form):
        if form.get("csrf_token") != session.get("csrf_token") or not session.get("csrf_token"):
            raise CommerceError("Your session expired.")

    growth_reports_routes.register_growth_reports_routes(app.route, lambda session: actor_id,
        csrf, check_csrf, lambda title, *children: (title, *children),
        lambda exc: Response(str(exc), status_code=400))
    return TestClient(app)


def test_analytics_http_csv_and_editor_cannot_read_reports(monkeypatch):
    with workspace() as w:
        client = isolated_client(monkeypatch, w.db, w.owner.id)
        path = f"/admin/sites/{w.site.id}/analytics"
        page = client.get(path)
        assert page.status_code == 200
        assert "Sales over time" in page.text and "Top products by units" in page.text
        assert "conversion" not in page.text.lower()
        assert '<svg viewBox="0 0 600 128"' in page.text
        exported = client.get(path + "?format=csv")
        assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
        assert "gross_sales_minor" in exported.text
        editor = isolated_client(monkeypatch, w.db, w.editor.id)
        assert editor.get(path).status_code == 400
        assert editor.get(f"/admin/sites/{w.site.id}/subscriptions").status_code == 400


def test_subscription_http_csrf_and_commands(monkeypatch, contract):
    r, sub = contract
    owner = r.db.scalar(select(Membership).where(Membership.tenant_id == r.site.tenant_id,
                                                Membership.role == "admin"))
    r.db.commit()
    client = isolated_client(monkeypatch, r.db, owner.user_id)
    path = f"/admin/sites/{r.site.id}/subscriptions/{sub.id}"
    page = client.get(path)
    assert page.status_code == 200 and "Pause subscription" in page.text
    fields = {key: re.search(r'name="' + key + r'" value="([^"]+)"', page.text).group(1)
              for key in ("csrf_token", "version", "request_key")}
    assert client.post(path + "/pause", data=fields | {"csrf_token": "bad"}).status_code == 400
    assert client.get(path.replace(sub.id, "foreign")).status_code == 400
    result = client.post(path + "/pause", data=fields, follow_redirects=False)
    assert result.status_code == 303
    r.db.expire_all()
    assert sub.state == "paused"
    assert "Resume subscription" in client.get(path).text

"""Checkout boundaries for growth rule reservations, rates and inclusive totals."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest
from fasthtml.common import Div, Input, fast_app
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response
from starlette.testclient import TestClient

from app import checkout_services, commerce, customer_services, discounts
from app.growth_settings_models import TaxRate
from app.models import CommerceQuote, DiscountCode, Membership
from app.services import CommerceError
from tests.test_checkout_services import prepare, ready  # noqa: F401
from tests.test_growth_discounts import lines, rule
from tests.test_us_commerce import DESTINATION, shop  # noqa: F401


def test_allocation_ties_are_stable_under_cart_reordering(shop):
    original = lines(shop)[0]
    cart = [replace(original, reference=ref, quantity=1, amount_minor=1) for ref in ["Z", "A", "M"]]
    allocated = discounts.allocate(cart, list(range(3)), 2)
    reordered = discounts.allocate(list(reversed(cart)), list(range(3)), 2)
    assert {line.reference: line.amount_minor for line in allocated} == {"Z": 1, "A": 0, "M": 0}
    assert {line.reference: line.amount_minor for line in reordered} == {
        line.reference: line.amount_minor for line in allocated
    }


def test_merchandise_discounts_recompute_shipping_threshold(ready):  # noqa: F811
    config = commerce.settings_for(ready.db, ready.site)
    config.free_shipping_threshold_minor = 5500
    rule(ready.db, ready.site, value=10)
    quote = commerce.quote_order(
        ready.db, ready.site, config, ready.selections, DESTINATION, ready.gateway
    )
    assert quote.discount_minor == 599
    assert quote.shipping_minor == 1000  # 5990 before, 5391 after discount.
    assert quote.total_minor == 5391 + 1000 + 205


@pytest.mark.parametrize("discount_percent", [0, 10, 100])
def test_inclusive_tax_preserves_gross_total_with_shipping(ready, discount_percent):  # noqa: F811
    db, site = ready.db, ready.site
    if discount_percent:
        rule(db, site, value=discount_percent)
    db.add(
        TaxRate(
            tenant_id=site.tenant_id,
            site_id=site.id,
            country="US",
            rate_bps=2000,
            prices_include_tax=True,
        )
    )
    db.flush()
    config = commerce.settings_for(db, site)
    config.free_shipping_threshold_minor = 999999
    quote = commerce.quote_order(db, site, config, ready.selections, DESTINATION, ready.gateway)
    saving = (5990 * discount_percent + 50) // 100
    assert quote.total_minor == 5990 - saving + 1000
    assert quote.discount_minor == saving
    assert (
        quote.subtotal_minor - quote.discount_minor + quote.shipping_minor + quote.tax_minor
        == quote.total_minor
    )
    assert (
        sum(row["amount_minor"] for row in quote.snapshot_json["lines"])
        + quote.shipping_minor
        + quote.tax_minor
        == quote.total_minor
    )
    assert quote.snapshot_json["shipping_minor"] == quote.shipping_minor
    assert quote.snapshot_json["local_tax"]["prices_include_tax"]


def test_pending_checkouts_hold_limit_until_cancelled(ready):  # noqa: F811
    db, site = ready.db, ready.site
    row = rule(db, site, usage_limit=1, method="code")
    db.add(DiscountCode(tenant_id=site.tenant_id, site_id=site.id, discount_id=row.id, code="LAST"))
    other = customer_services.customer_for(db, site, "second-checkout@example.test", create=True)
    db.commit()
    first = prepare(ready, code="LAST")
    db.commit()
    with pytest.raises(CommerceError, match="not eligible"):
        checkout_services.prepare(
            db,
            site,
            other.id,
            ready.selections,
            DESTINATION,
            ready.gateway,
            request_key="other-checkout-key-01",
            code="LAST",
        )
    assert discounts.pending_attempts(db, site, row) == [first]
    checkout_services.cancel_unstarted(db, site, first.id)
    second = checkout_services.prepare(
        db,
        site,
        other.id,
        ready.selections,
        DESTINATION,
        ready.gateway,
        request_key="other-checkout-key-01",
        code="LAST",
    )
    assert second.id != first.id


def test_unknown_provider_outcome_keeps_discount_hold_after_expiry(ready):  # noqa: F811
    db, site = ready.db, ready.site
    row = rule(db, site, usage_limit=1)
    attempt = prepare(ready)
    attempt.expires_at = datetime.now(UTC) - timedelta(hours=1)
    attempt.state = "creating"
    db.flush()
    assert discounts.pending_attempts(db, site, row) == [attempt]
    assert not discounts.apply(
        db, site, lines((db, site, commerce.settings_for(db, site), ready.variant)), 1000
    )[2]
    attempt.state = "prepared"
    db.flush()
    assert discounts.pending_attempts(db, site, row) == []


class ShellParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.in_shell = False
        self.actions_in_shell = False

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "div":
            self.depth += 1
            if data.get("id") == "discount-shell":
                self.in_shell = self.depth
        if tag == "form" and data.get("action", "").endswith("/action"):
            self.actions_in_shell = bool(self.in_shell)

    def handle_endtag(self, tag):
        if tag == "div":
            if self.in_shell == self.depth:
                self.in_shell = False
            self.depth -= 1


def test_discount_form_round_trip_csrf_scope_and_actions_inside_shell(ready, monkeypatch):  # noqa: F811
    from app import discount_routes

    db, site = ready.db, ready.site
    engine = db.get_bind()
    actor_id = db.scalar(select(Membership.user_id))
    app, rt = fast_app(secret_key="growth-discount-tests")
    monkeypatch.setattr(
        discount_routes, "SessionLocal", lambda: Session(engine, expire_on_commit=False)
    )

    def csrf(session, data):
        if data.get("csrf_token") != "test-token":
            raise CommerceError("CSRF rejected")

    discount_routes.register_discount_routes(
        rt,
        lambda session: actor_id,
        lambda session: Input(type="hidden", name="csrf_token", value="test-token"),
        csrf,
        lambda title, *children: Div(*children, id="discount-shell"),
        lambda exc: Response(str(exc), status_code=400),
    )
    base = f"/admin/sites/{site.id}/discounts"
    data = {
        "title": "Welcome",
        "type": "amount_off_order",
        "method": "code",
        "value_type": "percentage",
        "value": "10",
        "applies_to": "all",
        "eligibility": "all",
        "minimum_type": "none",
        "codes": "WELCOME",
        "is_active": "on",
    }
    with TestClient(app) as client:
        create = client.get(base + "/new?type=amount_off_order")
        assert create.status_code == 200 and 'name="csrf_token"' in create.text
        assert client.post(base + "/new", data=data).status_code == 400
        assert (
            client.post(
                "/admin/sites/foreign/discounts/new", data=data | {"csrf_token": "test-token"}
            ).status_code
            == 400
        )
        response = client.post(
            base + "/new", data=data | {"csrf_token": "test-token"}, follow_redirects=False
        )
        assert response.status_code == 303, response.text
        target = response.headers["location"]
        detail = client.get(target)
        assert detail.status_code == 200 and 'value="WELCOME"' in detail.text
        parser = ShellParser()
        parser.feed(detail.text)
        assert parser.actions_in_shell
        assert (
            client.post(
                target,
                data=data | {"csrf_token": "test-token", "value": "20"},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert client.post(target + "/action", data={"action": "deactivate"}).status_code == 400
        assert (
            client.post(
                target + "/action",
                data={"csrf_token": "test-token", "action": "deactivate"},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert client.post(base + "/bulk", data={"csrf_token": "test-token", "ids": target.rsplit("/", 1)[-1], "action": "activate"}, follow_redirects=False).status_code == 303
        pending_rule = discounts.code_rule(db, site, "WELCOME")
        pending_rule.is_active = True
        db.commit()
        attempt = prepare(ready, code="WELCOME")
        db.commit()
        response = client.post(
            target + "/action", data={"csrf_token": "test-token", "action": "delete"}
        )
        assert response.status_code == 400 and "reserved by a checkout" in response.text
        assert db.get(CommerceQuote, attempt.quote_id).snapshot_json["discounts"]

"""Customer service scoping, consent, address book, filters and import contracts."""

import csv
import io
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import content
from app import customer_admin as customers
from app.customer_services import customer_for, signed_in_customer, unsubscribe
from app.models import (
    Base,
    CustomerAddress,
    CustomerComment,
    CustomerSegment,
    MarketingConsent,
    Order,
    PaymentTransaction,
    ShopCustomer,
    SiteOrder,
    User,
)
from app.services import CommerceError


@pytest.fixture
def customer_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="customer-owner@example.test", name="Merchant")
        outsider = User(email="customer-other@example.test", name="Other merchant")
        db.add_all([owner, outsider])
        db.flush()
        site = content.create_site(db, owner.id, "Customer shop", "customer-service-shop")
        other = content.create_site(db, outsider.id, "Other customer shop", "other-customer-shop")
        customer = customers.save_customer(db, site, {"email": "Alice@Example.test", "name": "Alice Adams", "tags": "VIP"})
        secret = customers.save_customer(db, other, {"email": "private@example.test", "name": "Private customer"})
        db.commit()
        yield SimpleNamespace(db=db, sessions=sessions, site=site, other=other, owner=owner, customer=customer, secret=secret)
    engine.dispose()


def order_for(w, *, total=1000, currency="USD", payment_status="paid", customer=None):
    customer = customer or w.customer
    site = w.other if customer.id == w.secret.id else w.site
    count = w.db.scalar(select(func.count(Order.id)))
    order = Order(tenant_id=site.tenant_id, channel_id=site.channel_id,
        number=f"TEST-{count}", idempotency_key=f"customer-order-{count}", email=customer.email,
        status="unfulfilled", payment_status=payment_status, currency=currency,
        subtotal_minor=total, total_minor=total)
    w.db.add(order)
    w.db.flush()
    link = SiteOrder(tenant_id=site.tenant_id, site_id=site.id, order_id=order.id, customer_id=customer.id)
    w.db.add(link)
    w.db.flush()
    return order, link


def test_customer_contact_email_idempotency_and_validation(customer_db):
    w = customer_db
    saved = customers.save_customer(w.db, w.site, {"email": " ALICE@example.test ", "name": "Alice New", "phone": "+372555123", "note": "Prefers email", "tags": "VIP, repeat, VIP"})
    assert saved.id == w.customer.id
    assert (saved.name, saved.phone, saved.note, saved.tags_json) == ("Alice New", "+372555123", "Prefers email", ["VIP", "repeat"])
    other = customers.save_customer(w.db, w.site, {"email": "other@example.test"})
    with pytest.raises(CommerceError, match="already uses"):
        customers.save_customer(w.db, w.site, {"email": other.email}, saved.id)
    with pytest.raises(CommerceError, match="valid email"):
        customers.save_customer(w.db, w.site, {"email": "bad"})
    with pytest.raises(CommerceError, match="50 tags"):
        customers.tags("x" * 81)
    with pytest.raises(CommerceError, match="account state"):
        customers.save_customer(w.db, w.site, {"email": saved.email, "account_state": "fake"}, saved.id)


def test_consent_records_source_time_and_canonical_marketing(customer_db):
    w = customer_db
    c = customers.save_customer(w.db, w.site, {"email": w.customer.email, "email_marketing_consent": True, "sms_marketing_consent": True}, w.customer.id)
    assert c.email_marketing_consent and c.sms_marketing_consent
    assert c.email_consent_source == c.sms_consent_source == "admin"
    assert c.email_consent_at and c.sms_consent_at
    consent = w.db.scalar(select(MarketingConsent).where(MarketingConsent.customer_id == c.id))
    assert consent.status == "subscribed" and consent.consent_json["source"] == "admin"
    first = c.email_consent_at
    customers.save_customer(w.db, w.site, {"email": c.email, "email_marketing_consent": True}, c.id)
    assert c.email_consent_at == first
    unsubscribe(w.db, w.site, c)
    w.db.flush()
    assert customers.customer_rows(w.db, w.site, {"consent": "subscribed"}) == []
    assert "no" in customers.export_csv(w.db, w.site)
    customers.save_customer(w.db, w.site, {"email": c.email, "email_marketing_consent": True}, c.id)
    assert consent.status == "subscribed"
    customers.save_customer(w.db, w.site, {"email": c.email, "email_marketing_consent": False, "sms_marketing_consent": False}, c.id)
    assert consent.status == "unsubscribed" and consent.unsubscribed_at


def test_disabled_account_blocks_existing_customer_session(customer_db):
    from datetime import UTC, datetime

    w = customer_db
    w.customer.verified_at = datetime.now(UTC)
    session = {"customer:" + w.site.id: w.customer.id}
    assert signed_in_customer(w.db, w.site, session)
    customers.save_customer(w.db, w.site, {"email": w.customer.email, "account_state": "disabled"}, w.customer.id)
    assert signed_in_customer(w.db, w.site, session) is None
    customers.save_customer(w.db, w.site, {"email": w.customer.email, "account_state": "active"}, w.customer.id)
    assert signed_in_customer(w.db, w.site, session)


def test_address_book_default_edit_delete_and_isolation(customer_db):
    w = customer_db
    data = {"name": "Home", "line1": "1 High Street", "city": "London", "country": "gb", "postal_code": "N1 1AA"}
    first = customers.save_address(w.db, w.site, w.customer.id, data)
    assert first.is_default and first.country == "GB"
    second = customers.save_address(w.db, w.site, w.customer.id, {**data, "name": "Work", "is_default": True})
    assert second.is_default and not first.is_default
    customers.save_address(w.db, w.site, w.customer.id, {**data, "city": "York"}, second.id)
    assert second.city == "York" and second.is_default
    customers.save_address(w.db, w.site, w.customer.id, {}, second.id, delete=True)
    assert first.is_default
    assert len(customers.addresses_for(w.db, w.site, w.customer)) == 1
    with pytest.raises(CommerceError, match="Customer not found"):
        customers.save_address(w.db, w.site, w.secret.id, data)
    with pytest.raises(CommerceError, match="Address not found"):
        customers.save_address(w.db, w.other, w.secret.id, data, first.id)
    with pytest.raises(CommerceError, match="two-letter"):
        customers.save_address(w.db, w.site, w.customer.id, {**data, "country": "1!"})


@pytest.mark.parametrize("key,limit", list(customers.ADDRESS_LIMITS.items()))
def test_address_limits_match_database_columns(customer_db, key, limit):
    w = customer_db
    data = {"line1": "1 Main Road", "city": "London", "country": "GB", key: "x" * (limit + 1)}
    with pytest.raises(CommerceError, match="no longer than"):
        customers.save_address(w.db, w.site, w.customer.id, data)
    assert customers.addresses_for(w.db, w.site, w.customer) == []


def test_customer_search_filters_stats_sort_and_timeline(customer_db):
    w = customer_db
    order, link = order_for(w, total=2000)
    order_for(w, total=500, payment_status="pending")
    order_for(w, total=900, currency="EUR")
    order_for(w, total=9999, customer=w.secret)
    w.db.add(PaymentTransaction(order_id=order.id, provider="stripe:" + w.site.id, external_id="customer-refund", kind="refund", status="succeeded", currency="USD", amount_minor=500))
    customers.save_address(w.db, w.site, w.customer.id, {"line1": "1 Street", "city": "Tallinn", "country": "EE"})
    customers.add_comment(w.db, w.site, w.customer.id, w.owner.id, "Handle with care")
    w.db.flush()
    rows = customers.customer_rows(w.db, w.site, {"q": "Alice", "orders_min": "3", "orders_max": "3", "spent_min": "1500", "spent_max": "1500", "tag": "VIP", "location": "tallinn"})
    assert len(rows) == 1 and rows[0].orders == 3 and rows[0].spent == 1500
    assert "Private" not in customers.export_csv(w.db, w.site)
    assert customers.customer_rows(w.db, w.site, {"q": "%"}) == []
    assert customers.customer_rows(w.db, w.site, {"spent_max": "1499"}) == []
    timeline = customers.timeline_for(w.db, w.site, w.customer)
    assert len(timeline) == 5 and any(item[2] == "Handle with care" for item in timeline)
    for filters in ({"orders_min": "-1"}, {"spent_min": "no"}):
        with pytest.raises(CommerceError):
            customers.customer_rows(w.db, w.site, filters)


def test_segment_is_scoped_and_updates_by_name(customer_db):
    w = customer_db
    first = customers.save_segment(w.db, w.site, "VIP customers", {"tag": "VIP", "unknown": "ignore"})
    again = customers.save_segment(w.db, w.site, "VIP customers", {"orders_min": "2"})
    assert first.id == again.id and again.filters_json == {"orders_min": "2"}
    assert customers.segments_for(w.db, w.other) == []
    assert w.db.scalar(select(func.count(CustomerSegment.id))) == 1


def test_csv_preview_apply_errors_and_idempotency(customer_db):
    w = customer_db
    payload = b'First Name,Last Name,Email,Accepts Email Marketing,Tags,Default Address Address1,Default Address City,Default Address Country Code\nBea,Brown,bea@example.test,yes,VIP,2 New Road,York,GB\nBad,Email,not-an-email,no,,,,\nDuplicate,Row,bea@example.test,no,,,,\nBad,Consent,badconsent@example.test,maybe,,,,\nBad,Address,badaddress@example.test,no,,Street,,GB\n'
    preview = customers.import_csv(w.db, w.site, payload)
    assert sum(bool(r["error"]) for r in preview) == 4
    assert customer_for(w.db, w.site, "bea@example.test") is None
    result = customers.import_csv(w.db, w.site, payload, apply=True)
    assert sum(bool(r["error"]) for r in result) == 4
    bea = customer_for(w.db, w.site, "bea@example.test")
    assert bea and bea.email_marketing_consent and bea.email_consent_source == "admin"
    address = customers.addresses_for(w.db, w.site, bea)[0]
    assert address.city == "York"
    customers.import_csv(w.db, w.site, payload, apply=True)
    assert w.db.scalar(select(func.count(ShopCustomer.id)).where(ShopCustomer.site_id == w.site.id)) == 2
    assert w.db.scalar(select(func.count(CustomerAddress.id)).where(CustomerAddress.customer_id == bea.id)) == 1
    assert customer_for(w.db, w.site, "badaddress@example.test") is None
    assert customer_for(w.db, w.other, "bea@example.test") is None


@pytest.mark.parametrize("payload", [b"Name\nNo email\n", b"Email\n\xff\n", b"Email\n" + b"a" * (2 * 1024 * 1024)], ids=["missing-header", "invalid-encoding", "too-large"])
def test_csv_rejects_invalid_upload(customer_db, payload):
    with pytest.raises(CommerceError):
        customers.import_csv(customer_db.db, customer_db.site, payload)


def test_csv_roundtrip_and_spreadsheet_formula_protection(customer_db):
    w = customer_db
    customers.save_customer(w.db, w.site, {"email": w.customer.email, "name": "=DANGEROUS", "note": "@formula"}, w.customer.id)
    source = customers.export_csv(w.db, w.site)
    row = next(csv.DictReader(io.StringIO(source)))
    assert row["First Name"] == "'=DANGEROUS" and row["Note"] == "'@formula"
    assert row["Email"] == w.customer.email
    assert all(not r["error"] for r in customers.import_csv(w.db, w.site, source.encode(), apply=True))
    assert w.db.scalar(select(func.count(ShopCustomer.id)).where(ShopCustomer.site_id == w.site.id)) == 1


def test_customer_writes_and_import_preserve_caller_transaction(customer_db):
    w = customer_db
    with pytest.raises(CommerceError, match="60 characters"):
        customers.save_customer(w.db, w.site, {"email": "rejected@example.test", "phone": "0" * 61})
    w.db.commit()
    assert customer_for(w.db, w.site, "rejected@example.test") is None
    payload = b"First Name,Email\nReversible,rollback@example.test\n"
    customers.import_csv(w.db, w.site, payload, apply=True)
    assert customer_for(w.db, w.site, "rollback@example.test")
    w.db.rollback()
    assert customer_for(w.db, w.site, "rollback@example.test") is None


def test_bulk_tags_and_comments_do_not_cross_tenants(customer_db):
    w = customer_db
    with pytest.raises(CommerceError):
        customers.bulk_tag(w.db, w.site, [w.customer.id, w.secret.id], "new")
    assert w.customer.tags_json == ["VIP"]
    customers.bulk_tag(w.db, w.site, [w.customer.id], "new,VIP")
    assert w.customer.tags_json == ["VIP", "new"]
    with pytest.raises(CommerceError):
        customers.add_comment(w.db, w.site, w.secret.id, w.owner.id, "Private")
    assert w.db.scalar(select(func.count(CustomerComment.id))) == 0
    with pytest.raises(CommerceError):
        customers.add_comment(w.db, w.site, w.customer.id, w.owner.id, " ")

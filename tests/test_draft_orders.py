"""Draft completion uses the real checkout reservation and order finaliser."""

import pytest
from sqlalchemy import event, func, select

from app import checkout_services, content
from app import draft_orders as drafts
from app.integrations.commerce_email import render_message
from app.models import (
    CheckoutAttempt,
    CommerceMail,
    CommerceQuote,
    DraftOrder,
    InventoryReservation,
    Order,
    OutboxEvent,
    PaymentTransaction,
    ShopCustomer,
    User,
)
from app.services import CommerceError
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_us_commerce import shop  # noqa: F401


def draft_with_lines(r, quantity=2):
    row = drafts.create(r.db, r.site)
    drafts.add_line(r.db, r.site, row.id, {"variant_id": r.variant.id, "quantity": quantity}, version=row.version)
    drafts.add_line(r.db, r.site, row.id, {"title": "Gift wrapping", "unit_price_minor": 105, "quantity": 1}, version=row.version)
    drafts.save(r.db, r.site, row.id, {"customer_id": r.customer.id,
        "discount_minor": 101, "shipping_minor": 250}, version=row.version)
    r.db.commit()
    return row


def test_draft_complete_reserves_once_and_records_manual_payment(ready):
    r = ready
    draft = draft_with_lines(r)
    assert r.stock.allocated == 0
    with pytest.raises(CommerceError, match="outside FastShop"):
        drafts.complete(r.db, r.site, draft.id)
    result = drafts.complete(r.db, r.site, draft.id, mark_as_paid=True)
    r.db.commit()
    assert result.status == "completed"
    assert result.order_id and result.site_order_id
    order = r.db.get(Order, result.order_id)
    assert order.total_minor == 6244
    assert order.subtotal_minor == 6095 and order.discount_minor == 101
    assert sum(line.total_minor for line in order.lines) == 5994
    assert any(line.variant_id is None and line.product_name == "Gift wrapping" for line in order.lines)
    assert r.stock.allocated == 2 and r.stock.quantity == 5
    assert drafts.complete(r.db, r.site, draft.id, mark_as_paid=True).order_id == order.id
    assert r.db.scalar(select(func.count(Order.id))) == 1
    assert r.db.scalar(select(func.count(InventoryReservation.id))) == 1
    assert r.db.scalar(select(InventoryReservation.state)) == "committed"
    payment = r.db.scalar(select(PaymentTransaction))
    assert payment.provider == "manual:"+r.site.id and payment.amount_minor == 6244
    attempt = r.db.scalar(select(CheckoutAttempt))
    assert attempt.state == "paid" and attempt.stripe_session_id is None
    assert r.db.scalar(select(func.count(OutboxEvent.id))) == 1
    assert r.db.scalar(select(func.count(CommerceMail.id))) == 1
    with pytest.raises(CommerceError, match="cannot be edited"):
        drafts.add_line(r.db, r.site, draft.id, {"title": "Later"}, version=draft.version)


def test_failed_completion_rolls_back_partial_reservations(ready):
    r = ready
    row = draft_with_lines(r, quantity=6)
    with pytest.raises(CommerceError, match="not enough stock"):
        drafts.complete(r.db, r.site, row.id, mark_as_paid=True)
    r.db.commit()
    r.db.refresh(r.stock)
    assert r.stock.allocated == 0
    assert r.db.scalar(select(func.count(Order.id))) == 0
    assert r.db.scalar(select(func.count(CheckoutAttempt.id))) == 0
    assert r.db.get(DraftOrder, row.id).status == "open"


def test_invoice_queue_has_real_renderer_no_pay_link_or_stock_hold(ready):
    r = ready
    row = draft_with_lines(r)
    mail = drafts.invoice(r.db, r.site, row.id, version=row.version)
    r.db.commit()
    subject, body = render_message(r.db, mail, r.site, r.customer)
    assert "Draft invoice" in subject and "Gift wrapping" in body
    assert "no online payment link" in body and "does not confirm receipt" in body
    assert "https://" not in body
    assert r.stock.allocated == 0
    with pytest.raises(CommerceError, match="changed"):
        drafts.invoice(r.db, r.site, row.id, version=row.version-1)
    drafts.complete(r.db, r.site, row.id, mark_as_paid=True)
    assert render_message(r.db, mail, r.site, r.customer) is None


def test_draft_scope_price_validation_versions_and_filter_pagination(ready):
    r = ready
    row = draft_with_lines(r)
    owner = User(email="draft-other@example.test", name="Other")
    r.db.add(owner)
    r.db.flush()
    other = content.create_site(r.db, owner.id, "Other store", "other-draft-store")
    for fn in (lambda: drafts.owned(r.db, other, row.id),
               lambda: drafts.add_line(r.db, other, drafts.create(r.db, other).id,
                   {"variant_id": r.variant.id}, version=1),
               lambda: drafts.customer(r.db, other, r.customer.id)):
        with pytest.raises(CommerceError):
            fn()
    with pytest.raises(CommerceError, match="changed"):
        drafts.save(r.db, r.site, row.id, {}, version=1)
    with pytest.raises(CommerceError, match="price"):
        drafts.add_line(r.db, r.site, row.id, {"title": "Invalid", "unit_price_minor": -1}, version=row.version)
    for _ in range(26):
        drafts.create(r.db, r.site)
    r.db.flush()
    rows, count = drafts.list_drafts(r.db, r.site, page=1)
    assert len(rows) == 25 and count == 27
    assert len(drafts.list_drafts(r.db, r.site, page=2)[0]) == 2
    assert drafts.list_drafts(r.db, r.site, q=r.customer.email)[1] == 1
    assert drafts.list_drafts(r.db, other)[1] == 1


def test_new_customer_remove_line_discount_cap_and_stock_pk_order(ready):
    r = ready
    row = draft_with_lines(r)
    custom = next(line for line in drafts.lines_for(r.db, r.site, row) if not line.variant_id)
    drafts.remove_line(r.db, r.site, row.id, custom.id, version=row.version)
    drafts.save(r.db, r.site, row.id, {"customer_email": "NEW@EXAMPLE.TEST", "customer_name": "New person",
        "discount_minor": 100000}, version=row.version)
    assert r.db.get(ShopCustomer, row.customer_id).email == "new@example.test"
    with pytest.raises(CommerceError, match="discount"):
        drafts.complete(r.db, r.site, row.id, mark_as_paid=True)
    drafts.save(r.db, r.site, row.id, {"customer_id": row.customer_id}, version=row.version)
    sql = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "stocks" in statement and "SELECT" in statement:
            sql.append(statement)
    event.listen(r.db.bind, "before_cursor_execute", capture)
    try:
        drafts.complete(r.db, r.site, row.id, mark_as_paid=True)
    finally:
        event.remove(r.db.bind, "before_cursor_execute", capture)
    assert any("ORDER BY stocks.id" in statement for statement in sql)


def test_abandoned_uses_real_checkout_records_and_tenant_scope(ready):
    r = ready
    assert drafts.abandoned(r.db, r.site) == []
    from tests.test_us_commerce import DESTINATION
    attempt = checkout_services.prepare(r.db, r.site, r.customer.id, r.selections,
        DESTINATION, r.gateway, request_key="abandoned-real-attempt")
    assert drafts.abandoned(r.db, r.site)[0][0].id == attempt.id
    attempt.state = "paid"
    r.db.flush()
    assert drafts.abandoned(r.db, r.site) == []

@pytest.mark.parametrize("tracked,backorder,allocated", [(False, False, 0), (True, True, 5)])
def test_draft_completion_preserves_catalogue_inventory_policy(ready, tracked, backorder, allocated):
    r = ready
    r.variant.track_inventory = tracked
    r.variant.continue_selling_when_out_of_stock = backorder
    draft = draft_with_lines(r, quantity=6)
    drafts.complete(r.db, r.site, draft.id, mark_as_paid=True)
    r.db.flush()
    r.db.refresh(r.stock)
    assert r.stock.allocated == allocated
    assert r.stock.quantity == 5


def test_manual_checkout_replay_fingerprint_and_confirmation(ready):
    r = ready
    draft = draft_with_lines(r)
    drafts.complete(r.db, r.site, draft.id, mark_as_paid=True)
    attempt = r.db.scalar(select(CheckoutAttempt))
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    link = checkout_services.complete_manual_order(r.db, r.site, r.customer.id, quote.snapshot_json,
        request_key=attempt.request_key, currency=quote.currency, subtotal_minor=quote.subtotal_minor,
        discount_minor=quote.discount_minor, shipping_minor=quote.shipping_minor)
    assert link.order_id == draft.order_id
    with pytest.raises(CommerceError, match="different details"):
        checkout_services.complete_manual_order(r.db, r.site, r.customer.id, quote.snapshot_json,
            request_key=attempt.request_key, currency=quote.currency, subtotal_minor=quote.subtotal_minor,
            discount_minor=quote.discount_minor, shipping_minor=quote.shipping_minor+1)
    message = r.db.scalar(select(CommerceMail).where(CommerceMail.kind == "order_confirmation"))
    subject, body = render_message(r.db, message, r.site, r.customer)
    assert "Sandbox" not in subject
    assert "Payment was recorded manually" in body
    assert "No online charge was made" in body


def test_caller_rollback_removes_order_reservation_quote_and_mail(ready):
    r = ready
    draft = draft_with_lines(r)
    draft_id = draft.id
    assert drafts.complete(r.db, r.site, draft_id, mark_as_paid=True).order_id
    r.db.rollback()
    r.db.refresh(r.stock)
    assert r.stock.allocated == 0
    for model in (Order, CheckoutAttempt, CommerceQuote, CommerceMail, InventoryReservation, OutboxEvent):
        assert r.db.scalar(select(func.count()).select_from(model)) == 0
    assert r.db.get(DraftOrder, draft_id).status == "open"


def test_empty_config_origin_uses_tenant_warehouse_and_rechecks_catalogue(ready):
    r = ready
    draft = draft_with_lines(r)
    from app import commerce
    from app.models import Product
    commerce.settings_for(r.db, r.site).origin_json = {}
    r.db.get(Product, r.variant.product_id).status = "archived"
    with pytest.raises(CommerceError, match="no longer available"):
        drafts.complete(r.db, r.site, draft.id, mark_as_paid=True)
    r.db.get(Product, r.variant.product_id).status = "active"
    drafts.complete(r.db, r.site, draft.id, mark_as_paid=True)
    assert r.stock.allocated == 2


def test_draft_uses_channel_currency_and_manual_boundary_rejects_bad_totals(ready):
    r = ready
    from app.models import Channel
    r.db.get(Channel, r.site.channel_id).currency = "GBP"
    draft = drafts.create(r.db, r.site)
    assert draft.currency == "GBP"
    drafts.add_line(r.db, r.site, draft.id, {"title": "Consultation", "unit_price_minor": 900}, version=draft.version)
    drafts.save(r.db, r.site, draft.id, {"customer_id": r.customer.id}, version=draft.version)
    drafts.complete(r.db, r.site, draft.id, mark_as_paid=True)
    assert r.db.get(Order, draft.order_id).currency == "GBP"
    with pytest.raises(CommerceError, match="amounts"):
        checkout_services.complete_manual_order(r.db, r.site, r.customer.id, {},
            request_key="manual-test-boundary", currency="GBP", subtotal_minor=-1,
            discount_minor=0, shipping_minor=0)

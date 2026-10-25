"""Sandbox gift tender totals, provider payloads, ledger idempotency and isolation."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import checkout_payments, checkout_services, gift_cards
from app.commerce_gap_models import GiftCardEntry
from app.models import CommerceQuote, Membership, PaymentTransaction, User
from app.services import CommerceError
from tests.test_checkout_services import Gateway, ready  # noqa: F401
from tests.test_order_management import workspace
from tests.test_us_commerce import DESTINATION, shop  # noqa: F401


def owner(r):
    user = User(email='gift-merchant@example.test', name='Gift merchant')
    r.db.add(user)
    r.db.flush()
    r.db.add(Membership(user_id=user.id, tenant_id=r.site.tenant_id, role='admin'))
    r.db.flush()
    return user


def prepare(r, code='SANDBOX-GIFT', key='gift-checkout-request-001'):
    return checkout_services.prepare(r.db, r.site, r.customer.id, r.selections,
        DESTINATION, r.gateway, request_key=key, recipient_name='Gift buyer', gift_card_code=code)


def test_gift_tender_keeps_checkout_provider_and_order_totals_consistent(ready):
    r = ready
    merchant = owner(r)
    card = gift_cards.issue(r.db, r.site, merchant.id, 'SANDBOX-GIFT', 500)
    attempt = prepare(r)
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    gross = quote.snapshot_json['gross_total_minor']
    assert quote.total_minor == gross-500
    command = checkout_payments.payment_command(r.site, attempt, quote, r.customer)['session']
    assert sum(line['quantity']*line['price_data']['unit_amount'] for line in command['line_items']) == quote.total_minor
    assert command['shipping_options'][0]['shipping_rate_data']['fixed_amount']['amount'] == 0
    assert command['automatic_tax']['enabled'] is False
    result = dict(id='cs_test_gift', livemode=False, mode='payment', client_reference_id=attempt.id,
        metadata={'site_id':r.site.id, 'quote_id':quote.id}, currency='usd', amount_total=quote.total_minor,
        automatic_tax={'enabled':False}, total_details={'amount_tax':0, 'amount_shipping':0})
    assert checkout_payments.matches_quote(result, attempt, quote)
    assert not checkout_payments.matches_quote(result, attempt, quote, live_mode=True)
    assert card.balance_minor == 500
    assert gift_cards.reserved(r.db, r.site, card.id) == 500
    link = checkout_services._paid_order(r.db, r.site, attempt, quote, {'payment_intent':'pi_gift'})
    r.db.flush()
    from app.models import Order
    order = r.db.get(Order, link.order_id)
    assert order.total_minor == gross
    assert sum(r.db.scalars(select(PaymentTransaction.amount_minor).where(PaymentTransaction.order_id == order.id))) == gross
    assert card.balance_minor == 0
    gift_cards.redeem(r.db, r.site, order, quote)
    assert r.db.scalar(select(func.count(GiftCardEntry.id)).where(GiftCardEntry.kind == 'redemption')) == 1
    gift_cards.restore_full_refund(r.db, r.site, order)
    gift_cards.restore_full_refund(r.db, r.site, order)
    assert card.balance_minor == 500
    assert r.db.scalar(select(func.count(GiftCardEntry.id)).where(GiftCardEntry.kind == 'refund')) == 1


def test_gift_reservations_disable_adjustment_and_release(ready):
    r = ready
    merchant = owner(r)
    card = gift_cards.issue(r.db, r.site, merchant.id, 'SANDBOX-GIFT', 500)
    attempt = prepare(r)
    with pytest.raises(CommerceError, match='reserved'):
        gift_cards.adjust(r.db, r.site, merchant.id, card.id, -1, 'Reduce', 'gift-adjust-request-001')
    with pytest.raises(CommerceError, match='reserved'):
        gift_cards.disable(r.db, r.site, merchant.id, card.id)
    checkout_services.cancel_unstarted(r.db, r.site, attempt.id)
    gift_cards.adjust(r.db, r.site, merchant.id, card.id, -100, 'Reduce', 'gift-adjust-request-001')
    gift_cards.adjust(r.db, r.site, merchant.id, card.id, -100, 'Reduce', 'gift-adjust-request-001')
    assert card.balance_minor == 400
    gift_cards.disable(r.db, r.site, merchant.id, card.id)
    with pytest.raises(CommerceError, match='unavailable'):
        prepare(r, key='gift-checkout-request-002')


def test_expiry_roles_tenant_scope_and_full_tender(ready):
    r = ready
    merchant = owner(r)
    card = gift_cards.issue(r.db, r.site, merchant.id, 'SANDBOX-GIFT', 100000)
    attempt = prepare(r)
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    assert quote.total_minor == 0
    assert quote.snapshot_json['gift_card']['amount_minor'] == quote.snapshot_json['gross_total_minor']
    checkout_services.cancel_unstarted(r.db, r.site, attempt.id)
    card.expires_at = datetime.now(UTC)-timedelta(days=1)
    with pytest.raises(CommerceError, match='unavailable'):
        prepare(r, key='gift-checkout-request-002')
    with workspace() as foreign:
        with pytest.raises(CommerceError):
            gift_cards.owned(r.db, r.site, foreign.link.id)
    editor = User(email='gift-editor@example.test', name='Editor')
    r.db.add(editor)
    r.db.flush()
    r.db.add(Membership(user_id=editor.id, tenant_id=r.site.tenant_id, role='editor'))
    r.db.flush()
    with pytest.raises(CommerceError):
        gift_cards.issue(r.db, r.site, editor.id, 'EDITOR-GIFT', 100)


def test_gift_only_order_refund_uses_no_provider_and_is_idempotent(ready):
    r = ready
    merchant = owner(r)
    card = gift_cards.issue(r.db, r.site, merchant.id, 'SANDBOX-GIFT', 100000)
    attempt = prepare(r)
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    from sqlalchemy.orm import sessionmaker

    from app.models import SiteOrder
    r.db.commit()
    def forbidden_gateway(*args, **kwargs):
        raise AssertionError('A gift-only checkout must not call a provider.')
    url = checkout_payments.handoff(r.site.id, r.customer.id, attempt.id,
        sessions=sessionmaker(r.db.bind, expire_on_commit=False), gateway_factory=forbidden_gateway)
    assert url.endswith('/checkout/'+attempt.id)
    r.db.expire_all()
    assert attempt.state == 'paid'
    link = r.db.scalar(select(SiteOrder).where(SiteOrder.order_id == attempt.order_id))
    spent = quote.snapshot_json['gift_card']['amount_minor']
    assert card.balance_minor == 100000-spent
    card.enabled = False
    order = gift_cards.refund_gift_only_order(r.db, r.site, merchant.id, link.id, 'Customer requested refund')
    assert order.payment_status == 'refunded' and card.balance_minor == 100000
    gift_cards.refund_gift_only_order(r.db, r.site, merchant.id, link.id, 'Customer requested refund')
    assert card.balance_minor == 100000 and not card.enabled
    assert r.db.scalar(select(func.count(GiftCardEntry.id)).where(GiftCardEntry.kind == 'refund')) == 1

def test_existing_full_line_refund_sends_net_capture_and_restores_gift(ready, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app import order_admin
    from tests.test_order_admin import RefundGateway
    r = ready
    merchant = owner(r)
    card = gift_cards.issue(r.db, r.site, merchant.id, 'SANDBOX-GIFT', 500)
    attempt = prepare(r)
    quote = r.db.get(CommerceQuote, attempt.quote_id)
    link = checkout_services._paid_order(r.db, r.site, attempt, quote, {'payment_intent':'pi_gift'})
    r.db.flush()
    from app.models import Order
    order = r.db.get(Order, link.order_id)
    r.db.commit()
    selected = {line.id:line.quantity for line in order.lines}
    preview = order_admin.preview_refund(r.db, r.site, link.id, selected, order.shipping_minor)
    assert preview['amount_minor'] == quote.total_minor
    r.db.commit()
    RefundGateway.calls = []
    from fasthtml.common import Div, Input, fast_app
    from starlette.responses import Response
    from starlette.testclient import TestClient

    from app import order_admin_routes
    sessions = sessionmaker(r.db.bind, expire_on_commit=False)
    monkeypatch.setattr(order_admin_routes, 'SessionLocal', sessions)
    monkeypatch.setattr(order_admin_routes, 'StripeGateway', RefundGateway)
    app, rt = fast_app(secret_key='gift-refund-test-only')
    def csrf(session):
        return Input(type='hidden', name='csrf_token', value='gift-refund-csrf')
    def check_csrf(session, form):
        if form.get('csrf_token') != 'gift-refund-csrf':
            raise CommerceError('Invalid CSRF token.')
    order_admin_routes.register_order_admin_routes(rt, lambda session:merchant.id, csrf,
        check_csrf, lambda title,*children:Div(*children), lambda exc:Response(str(exc), status_code=400))
    values = {'qty_'+identifier:str(quantity) for identifier, quantity in selected.items()}
    values.update(shipping=f'{order.shipping_minor/100:.2f}', reason='Customer requested a complete refund.')
    path = f'/admin/sites/{r.site.id}/orders/{link.id}/refund-items'
    with TestClient(app) as client:
        review = client.get(path, params=values)
        assert review.status_code == 200 and 'Review refund' in review.text
        values.update(confirmed='on', request_key='gift-full-refund-request-001',
                      expected_amount_minor=str(quote.total_minor), csrf_token='gift-refund-csrf')
        result = client.post(path, data=values, follow_redirects=False)
        assert result.status_code == 303 and 'succeeded' in result.headers['location']
    assert RefundGateway.calls[0][1]['amount'] == quote.total_minor
    r.db.expire_all()
    assert card.balance_minor == 500
    assert order.payment_status == 'refunded'
    from app import order_management
    class ResyncGateway:
        def __init__(self, site):
            self.site = site
        def checkout_status(self, session_id):
            return dict(id=session_id, livemode=False, metadata={'site_id':r.site.id},
                currency='usd', amount_total=quote.total_minor, payment_intent='pi_gift')
        def payment_intent_status(self, identifier):
            return dict(id=identifier, status='succeeded', livemode=False,
                metadata={'site_id':r.site.id}, currency='usd', amount_received=quote.total_minor)
    link.stripe_checkout_id = 'cs_test_gift'
    r.db.commit()
    assert order_management.resync_order(r.site.id, merchant.id, link.id,
        sessions=sessions, gateway_factory=ResyncGateway) == 'refunded'
    r.db.expire_all()
    assert card.balance_minor == 500


def test_partial_item_refund_exhausting_provider_keeps_gift_until_explicit_full_refund(monkeypatch):
    from types import SimpleNamespace

    from app import order_admin
    from tests.test_order_admin import RefundGateway
    with workspace() as w:
        line = w.order.lines[0]
        line.quantity, line.unit_price_minor, line.total_minor = 10, 1000, 10000
        w.order.subtotal_minor, w.order.shipping_minor, w.order.tax_minor = 10000, 0, 0
        stripe = w.db.scalar(select(PaymentTransaction).where(PaymentTransaction.order_id == w.order.id))
        stripe.amount_minor = 1000
        card = gift_cards.issue(w.db, w.site, w.owner.id, 'PARTIAL-GIFT', 9000)
        gift_cards.redeem(w.db, w.site, w.order, SimpleNamespace(snapshot_json={
            'gift_card':{'id':card.id,'amount_minor':9000}}))
        w.db.commit()
        RefundGateway.calls = []
        command = order_admin.refund_items(w.site.id, w.owner.id, w.link.id, {line.id:1},
            reason='Refund one of ten purchased items.', request_key='gift-partial-refund-001',
            sessions=w.sessions, gateway_factory=RefundGateway)
        assert command.state == 'succeeded' and RefundGateway.calls[0][1]['amount'] == 1000
        w.db.expire_all()
        assert line.refunded_quantity == 1 and w.order.payment_status == 'partially_refunded'
        assert card.balance_minor == 0
        assert w.db.scalar(select(func.count(GiftCardEntry.id)).where(GiftCardEntry.kind == 'refund')) == 0
        assert order_admin.detail(w.db, w.site, w.link.id)['gift_only_payment'] is True
        # The explicit full gift remainder action is now available without making
        # another provider call. Repeating it cannot restore the card twice.
        from fasthtml.common import Div, Input, fast_app
        from starlette.responses import Response
        from starlette.testclient import TestClient

        from app import commerce_gap_routes
        monkeypatch.setattr(commerce_gap_routes, 'SessionLocal', w.sessions)
        app, rt = fast_app(secret_key='gift-remainder-test-only')
        def csrf(session):
            return Input(type='hidden', name='csrf_token', value='gift-remainder-csrf')
        def check_csrf(session, form):
            if form.get('csrf_token') != 'gift-remainder-csrf':
                raise CommerceError('Invalid CSRF token.')
        commerce_gap_routes.register_commerce_gap_routes(rt, lambda session:w.owner.id, csrf,
            check_csrf, lambda title,*children:Div(*children), lambda exc:Response(str(exc), status_code=400))
        path = f'/admin/sites/{w.site.id}/orders/{w.link.id}/gift-card-refund'
        w.db.commit()
        with TestClient(app) as client:
            assert client.get(path).status_code == 200
            assert client.post(path, data={'reason':'Refund remainder'}).status_code == 400
            form = {'csrf_token':'gift-remainder-csrf', 'reason':'Refund all remaining gift payment'}
            assert client.post(path, data=form, follow_redirects=False).status_code == 303
            assert client.post(path, data=form, follow_redirects=False).status_code == 303
        w.db.expire_all()
        assert card.balance_minor == 9000 and w.order.payment_status == 'refunded'
        assert len(RefundGateway.calls) == 1
        assert w.db.scalar(select(func.count(GiftCardEntry.id)).where(GiftCardEntry.kind == 'refund')) == 1

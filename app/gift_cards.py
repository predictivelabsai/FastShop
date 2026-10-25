"""Gift cards are a sandbox payment tender, never a merchandise discount."""
import re
from datetime import UTC, date, datetime

from sqlalchemy import select

from app import checkout_services, commerce, content
from app.catalog_admin import _atomic
from app.commerce_gap_models import GiftCard, GiftCardEntry
from app.models import CheckoutAttempt, CommerceQuote, PaymentTransaction, ShopCustomer
from app.services import CommerceError


def _sandbox(db, site):
    if commerce.payment_mode(db, site) != 'sandbox':
        raise CommerceError('Gift cards are available in sandbox mode only.')


def _manager(db, site, actor_id):
    content.owned_site(db, site.id, actor_id, publish=True)
    checkout_services.lock_site(db, site)
    _sandbox(db, site)


def owned(db, site, identifier):
    card = db.scalar(select(GiftCard).where(GiftCard.id == identifier,
        GiftCard.site_id == site.id, GiftCard.tenant_id == site.tenant_id).with_for_update())
    if not card:
        raise CommerceError('Gift card not found.')
    return card


def list_cards(db, site, search=''):
    query = select(GiftCard).where(GiftCard.site_id == site.id, GiftCard.tenant_id == site.tenant_id)
    if search:
        query = query.where(GiftCard.code.contains(str(search).strip().upper(), autoescape=True))
    return list(db.scalars(query.order_by(GiftCard.created_at.desc())))


@_atomic
def issue(db, site, actor_id, code, amount_minor, *, expiry='', customer_id=None, note=''):
    _manager(db, site, actor_id)
    code = str(code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9-]{8,64}', code):
        raise CommerceError('Use a gift card code of 8 to 64 letters, digits or hyphens.')
    if type(amount_minor) is not int or not 0 < amount_minor <= 100_000_000:
        raise CommerceError('Enter a positive initial value in minor units.')
    if db.scalar(select(GiftCard.id).where(GiftCard.site_id == site.id, GiftCard.code == code)):
        raise CommerceError('This gift card code already exists.')
    expires_at = None
    if expiry:
        try:
            expires_at = datetime.combine(date.fromisoformat(str(expiry)), datetime.max.time(), UTC)
        except ValueError as exc:
            raise CommerceError('Use a valid expiry date.') from exc
        if expires_at <= datetime.now(UTC):
            raise CommerceError('Choose an expiry date in the future.')
    if customer_id and not db.scalar(select(ShopCustomer.id).where(ShopCustomer.id == customer_id,
            ShopCustomer.tenant_id == site.tenant_id, ShopCustomer.site_id == site.id)):
        raise CommerceError('Customer not found.')
    if len(str(note)) > 500:
        raise CommerceError('Use a note of at most 500 characters.')
    card = GiftCard(tenant_id=site.tenant_id, site_id=site.id, code=code,
        initial_minor=amount_minor, balance_minor=amount_minor, customer_id=customer_id or None,
        expires_at=expires_at, note=str(note))
    db.add(card)
    db.flush()
    db.add(GiftCardEntry(tenant_id=site.tenant_id, site_id=site.id, gift_card_id=card.id,
        actor_id=actor_id, request_key='issue:'+card.id, kind='issue', amount_minor=amount_minor,
        balance_minor=amount_minor, note=str(note)))
    return card


def reserved(db, site, card_id):
    """Only prepared or unresolved provider attempts hold a gift card balance."""
    snapshots = db.scalars(select(CommerceQuote.snapshot_json).join(CheckoutAttempt,
        CheckoutAttempt.quote_id == CommerceQuote.id).where(CheckoutAttempt.site_id == site.id,
        CheckoutAttempt.tenant_id == site.tenant_id,
        CheckoutAttempt.state.in_(checkout_services.ACTIVE)))
    return sum(s.get('gift_card', {}).get('amount_minor', 0) for s in snapshots
               if s.get('gift_card', {}).get('id') == card_id)


@_atomic
def adjust(db, site, actor_id, card_id, amount_minor, note, request_key):
    _manager(db, site, actor_id)
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', str(request_key)):
        raise CommerceError('Invalid adjustment request key.')
    card = owned(db, site, card_id)
    if type(amount_minor) is not int or not amount_minor or not str(note).strip() or len(str(note)) > 500:
        raise CommerceError('Enter an adjustment in minor units and a reason of up to 500 characters.')
    previous = db.scalar(select(GiftCardEntry).where(GiftCardEntry.site_id == site.id,
        GiftCardEntry.request_key == request_key, GiftCardEntry.tenant_id == site.tenant_id))
    if previous:
        if (previous.gift_card_id, previous.amount_minor, previous.note) != (card.id, amount_minor, note):
            raise CommerceError('This adjustment key was used for different details.')
        return previous
    balance = card.balance_minor + amount_minor
    if balance < reserved(db, site, card.id) or balance > 100_000_000:
        raise CommerceError('The adjustment would spend reserved funds or exceed the balance limit.')
    card.balance_minor = balance
    row = GiftCardEntry(tenant_id=site.tenant_id, site_id=site.id, gift_card_id=card.id,
        actor_id=actor_id, request_key=request_key, kind='adjustment', amount_minor=amount_minor,
        balance_minor=balance, note=note)
    db.add(row)
    db.flush()
    return row


@_atomic
def disable(db, site, actor_id, card_id):
    _manager(db, site, actor_id)
    card = owned(db, site, card_id)
    if reserved(db, site, card.id):
        raise CommerceError('Resolve reserved checkouts before disabling this gift card.')
    card.enabled = False
    key = 'disable:'+card.id
    if not db.scalar(select(GiftCardEntry.id).where(GiftCardEntry.site_id == site.id,
                                                  GiftCardEntry.request_key == key)):
        db.add(GiftCardEntry(tenant_id=site.tenant_id, site_id=site.id, gift_card_id=card.id,
            actor_id=actor_id, request_key=key, kind='disable', amount_minor=0,
            balance_minor=card.balance_minor))
    return card


def apply_to_quote(db, site, customer, quote, code):
    if not code:
        return
    _sandbox(db, site)
    if any(line.get('subscription') for line in quote.snapshot_json['lines']):
        raise CommerceError('Gift cards cannot fund recurring orders.')
    card = db.scalar(select(GiftCard).where(GiftCard.site_id == site.id,
        GiftCard.tenant_id == site.tenant_id, GiftCard.code == str(code).strip().upper()).with_for_update())
    if (not card or not card.enabled or card.currency != quote.currency
            or card.expires_at and checkout_services.utc(card.expires_at) <= datetime.now(UTC)
            or card.customer_id and card.customer_id != customer.id):
        raise CommerceError('This gift card is unavailable.')
    amount = min(quote.total_minor, card.balance_minor-reserved(db, site, card.id))
    if amount <= 0:
        raise CommerceError('This gift card has no available balance.')
    quote.snapshot_json = quote.snapshot_json | {'gift_card': {'id': card.id, 'amount_minor': amount},
                                                'gross_total_minor': quote.total_minor}
    quote.total_minor -= amount


def redeem(db, site, order, quote):
    tender = quote.snapshot_json.get('gift_card')
    if not tender:
        return
    _sandbox(db, site)
    key = 'redeem:'+order.id
    if db.scalar(select(GiftCardEntry.id).where(GiftCardEntry.site_id == site.id,
                                               GiftCardEntry.request_key == key)):
        return
    card = owned(db, site, tender['id'])
    amount = tender['amount_minor']
    if not card.enabled or card.balance_minor < amount:
        raise CommerceError('Gift card payment requires merchant reconciliation.')
    card.balance_minor -= amount
    db.add(GiftCardEntry(tenant_id=site.tenant_id, site_id=site.id, gift_card_id=card.id,
        order_id=order.id, request_key=key, kind='redemption', amount_minor=-amount,
        balance_minor=card.balance_minor))
    db.add(PaymentTransaction(order_id=order.id, provider='gift:'+site.id,
        external_id=key, kind='charge', status='succeeded', currency=order.currency, amount_minor=amount))


def restore_full_refund(db, site, order):
    """Restore the gift tender once, after explicit full-order refund intent."""
    entries = list(db.scalars(select(GiftCardEntry).where(GiftCardEntry.site_id == site.id,
        GiftCardEntry.tenant_id == site.tenant_id, GiftCardEntry.order_id == order.id,
        GiftCardEntry.kind == 'redemption').order_by(GiftCardEntry.gift_card_id)))
    for entry in entries:
        key = 'restore:'+entry.id
        if db.scalar(select(GiftCardEntry.id).where(GiftCardEntry.site_id == site.id,
                                                   GiftCardEntry.request_key == key)):
            continue
        card = owned(db, site, entry.gift_card_id)
        amount = -entry.amount_minor
        card.balance_minor += amount
        db.add(GiftCardEntry(tenant_id=site.tenant_id, site_id=site.id, gift_card_id=card.id,
            order_id=order.id, request_key=key, kind='refund', amount_minor=amount,
            balance_minor=card.balance_minor))
        db.add(PaymentTransaction(order_id=order.id, provider='gift:'+site.id,
            external_id=key, kind='refund', status='succeeded', currency=order.currency, amount_minor=amount))
    db.flush()

def full_refund_selected(db, site, link, order):
    """Successful item and shipping selections must cover the whole purchase."""
    from app.models import RefundCommand
    if not order.lines or any(line.refunded_quantity < line.quantity for line in order.lines):
        return False
    selections = db.scalars(select(RefundCommand.selection_json).where(
        RefundCommand.site_order_id == link.id, RefundCommand.site_id == site.id,
        RefundCommand.tenant_id == site.tenant_id, RefundCommand.state == 'succeeded'))
    return sum((selection or {}).get('shipping_minor', 0) for selection in selections) >= order.shipping_minor


@_atomic
def refund_gift_only_order(db, site, actor_id, link_id, reason):
    """Refund the full gift remainder once other payment tenders are reversed."""
    from app import order_management
    from app.models import OrderComment
    _manager(db, site, actor_id)
    link, order, _ = order_management.owned_order(db, site, link_id, lock=True)
    reason = str(reason).strip()
    if not reason or len(reason) > 500:
        raise CommerceError('Record a refund reason of up to 500 characters.')
    payments = list(db.scalars(select(PaymentTransaction).where(
        PaymentTransaction.order_id == order.id, PaymentTransaction.status == 'succeeded',
        PaymentTransaction.currency == order.currency)))
    other_remaining = sum(p.amount_minor * (-1 if p.kind == 'refund' else 1)
        for p in payments if p.provider != 'gift:'+site.id and p.kind in {'charge', 'refund'})
    if (not any(p.provider == 'gift:'+site.id and p.kind == 'charge' for p in payments)
            or other_remaining > 0):
        raise CommerceError('Refund the remaining payment from other tenders before restoring the gift card.')
    if order.payment_status == 'refunded':
        return order
    restore_full_refund(db, site, order)
    order.payment_status = 'refunded'
    db.add(OrderComment(tenant_id=site.tenant_id, site_id=site.id, site_order_id=link.id,
        author_id=actor_id, internal=True, body='Gift card payment refunded in full: '+reason))
    db.flush()
    return order

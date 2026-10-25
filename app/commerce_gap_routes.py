"""Additive merchant pages for order editing and gift cards."""
import json
import secrets
from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    A,
    Button,
    Div,
    Form,
    Input,
    Label,
    Option,
    P,
    Select,
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Tr,
)
from sqlalchemy import select
from starlette.responses import RedirectResponse

from app import content, gift_cards, order_edits
from app import order_management as orders
from app.admin_ui import card, page_header
from app.commerce_gap_models import GiftCardEntry, OrderEdit
from app.db import SessionLocal
from app.models import ProductVariant
from app.order_admin_ui import assets
from app.services import CommerceError, money


def register_commerce_gap_routes(rt, actor, csrf, check_csrf, shell, error):
    def access(db, session, site_id):
        user_id = actor(session)
        return content.owned_site(db, site_id, user_id, publish=True), user_id

    def field(label, name, value='', **attrs):
        return Label(label, Input(name=name, value=value, **attrs))

    def token(session):
        return csrf(session)

    def panel(*children):
        return Div(*assets(), *children, cls='p3-orders')

    def redirect(url, notice=''):
        return RedirectResponse(url + ('?' + urlencode({'notice': str(notice)[:500]}) if notice else ''), 303)

    @rt('/admin/sites/{site_id}/gift-cards', methods=['GET'])
    def get(session, site_id: str, q: str = '', notice: str = ''):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                root = f'/admin/sites/{site.id}/gift-cards'
                rows = gift_cards.list_cards(db, site, q)
                return shell('Gift cards', panel(page_header('Gift cards'), P(notice) if notice else None,
                    P('Sandbox gift cards reduce the payment due and keep a balance history.'),
                    Form(field('Search codes', 'q', q), Button('Search'), method='get'),
                    card(H2('Gift cards'), Table(Thead(Tr(Th('Code'), Th('Balance'), Th('Status'))),
                        Tbody(*[Tr(Td(A(c.code, href=root+'/'+c.id)), Td(money(c.balance_minor, c.currency)),
                            Td('Active' if c.enabled else 'Disabled')) for c in rows]))),
                    card(H2('Issue gift card'), Form(token(session),
                        field('Code', 'code', required=True, minlength=8, maxlength=64),
                        field('Initial value (minor units)', 'amount_minor', type='number', min=1, required=True),
                        field('Expiry', 'expiry', type='date'), field('Customer ID (optional)', 'customer_id'),
                        Label('Note', Textarea(name='note', maxlength=500)), Button('Issue gift card'),
                        action=root, method='post', cls='p3-form'))))
        except CommerceError as exc:
            return error(exc)

    @rt('/admin/sites/{site_id}/gift-cards', methods=['POST'])
    async def post(session, request, site_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                row = gift_cards.issue(db, site, user_id, form.get('code'), int(form.get('amount_minor', 0)),
                    expiry=form.get('expiry', ''), customer_id=form.get('customer_id'), note=form.get('note', ''))
                db.commit()
                return redirect(f'/admin/sites/{site_id}/gift-cards/{row.id}', 'Gift card issued.')
        except (CommerceError, ValueError, TypeError) as exc:
            return error(exc)

    @rt('/admin/sites/{site_id}/gift-cards/{card_id}', methods=['GET'])
    def get(session, site_id: str, card_id: str, notice: str = ''):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                row = gift_cards.owned(db, site, card_id)
                root = f'/admin/sites/{site_id}/gift-cards/{row.id}'
                history = list(db.scalars(select(GiftCardEntry).where(GiftCardEntry.site_id == site.id,
                    GiftCardEntry.tenant_id == site.tenant_id, GiftCardEntry.gift_card_id == row.id)
                    .order_by(GiftCardEntry.created_at.desc())))
                return shell('Gift card', panel(page_header(row.code, back_href=root.rsplit('/', 1)[0]),
                    P(notice) if notice else None, card(H2(money(row.balance_minor, row.currency)),
                        P('Active' if row.enabled else 'Disabled'), P(row.note),
                        P('Expiry: '+str(row.expires_at or 'None'))),
                    card(H2('Adjust balance'), Form(token(session),
                        Input(type='hidden', name='request_key', value=secrets.token_hex(16)),
                        field('Adjustment (minor units)', 'amount_minor', type='number', required=True),
                        field('Reason', 'note', required=True, maxlength=500), Button('Adjust balance'),
                        action=root+'/adjust', method='post', cls='p3-form')),
                    Form(token(session), Button('Disable gift card', disabled=not row.enabled),
                         action=root+'/disable', method='post'),
                    card(H2('Balance history'), Table(Thead(Tr(Th('Action'), Th('Amount'), Th('Balance'), Th('Note'))),
                        Tbody(*[Tr(Td(e.kind.capitalize()), Td(money(e.amount_minor, row.currency)),
                            Td(money(e.balance_minor, row.currency)), Td(e.note)) for e in history])))))
        except CommerceError as exc:
            return error(exc)

    @rt('/admin/sites/{site_id}/gift-cards/{card_id}/{operation}', methods=['POST'])
    async def post(session, request, site_id: str, card_id: str, operation: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                if operation == 'adjust':
                    gift_cards.adjust(db, site, user_id, card_id, int(form.get('amount_minor', 0)),
                                      form.get('note', ''), form.get('request_key', ''))
                elif operation == 'disable':
                    gift_cards.disable(db, site, user_id, card_id)
                else:
                    raise CommerceError('Unknown gift card action.')
                db.commit()
            return redirect(f'/admin/sites/{site_id}/gift-cards/{card_id}', 'Gift card updated.')
        except (CommerceError, ValueError, TypeError) as exc:
            return error(exc)

    @rt('/admin/sites/{site_id}/orders/{link_id}/edit', methods=['GET'])
    def get(session, site_id: str, link_id: str, notice: str = ''):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                link, order, _ = orders.owned_order(db, site, link_id)
                root = f'/admin/sites/{site_id}/orders/{link.id}'
                variants = list(db.scalars(select(ProductVariant).where(
                    ProductVariant.tenant_id == site.tenant_id, ProductVariant.is_active.is_(True))
                    .order_by(ProductVariant.sku).limit(500)))
                edits = list(db.scalars(select(OrderEdit).where(OrderEdit.site_id == site.id,
                    OrderEdit.tenant_id == site.tenant_id, OrderEdit.site_order_id == link.id)
                    .order_by(OrderEdit.created_at.desc(), OrderEdit.id.desc())))
                settlement = []
                if edits:
                    edit = edits[0]
                    amount = edit.difference_minor
                    settlement.append(card(H2('Payment difference'),
                        P(('Amount owing: ' if amount > 0 else 'Refund due: ' if amount < 0 else 'Settled: ')
                          + money(abs(amount), order.currency)),
                        P('Settlement: '+edit.settlement),
                        Form(token(session), Input(type='hidden', name='edit_id', value=edit.id),
                             Button('Mark paid manually', name='action', value='paid'),
                             Button('Send invoice', name='action', value='invoice'),
                             action=root+'/edit/settle', method='post') if amount > 0 and edit.settlement != 'paid' else None,
                        A('Open refund flow', href=root+'#refund') if amount < 0 else None))
                return shell('Edit order', panel(page_header('Edit '+order.number, back_href=root),
                    P(notice) if notice else None,
                    P('Set a quantity to zero to remove an item. Amounts and line discounts use minor currency units. '
                      'Existing shipping and tax remain unchanged. Review the payment difference after saving.'),
                    *settlement, card(H2('Items'), Form(token(session),
                        Input(type='hidden', name='request_key', value=secrets.token_hex(16)),
                        Input(type='hidden', name='version', value=order_edits.version(order)),
                        *[Div(P(line.product_name+' / '+line.variant_name),
                            field('Quantity', 'quantity_'+line.id, line.quantity, type='number', min=0),
                            field('Line discount (minor units)', 'discount_'+line.id,
                                  max(0, line.quantity*line.unit_price_minor-line.total_minor), type='number', min=0))
                          for line in order.lines],
                        H2('Add a variant'), Label('Variant', Select(Option('Choose a variant', value=''),
                            *[Option(v.sku+' / '+v.name, value=v.id) for v in variants], name='variant_id')),
                        field('Variant quantity', 'variant_quantity', 1, type='number', min=1),
                        H2('Add a custom item'), field('Item name', 'custom_name', maxlength=220),
                        field('Price (minor units)', 'custom_price', 0, type='number', min=0),
                        field('Custom item quantity', 'custom_quantity', 1, type='number', min=1),
                        field('Custom item discount (minor units)', 'custom_discount', 0, type='number', min=0),
                        Label('Reason for edit', Textarea(name='reason', required=True, maxlength=500)),
                        Button('Save order edit'), action=root+'/edit', method='post', cls='p3-form'))))
        except CommerceError as exc:
            return error(exc)

    @rt('/admin/sites/{site_id}/orders/{link_id}/edit', methods=['POST'])
    async def post(session, request, site_id: str, link_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                if form.get('selections'):
                    selections = json.loads(form['selections'])
                else:
                    selections = [dict(line_id=key[9:], quantity=int(value),
                        discount_minor=int(form.get('discount_'+key[9:], 0))) for key, value in form.items()
                        if key.startswith('quantity_')]
                    if form.get('variant_id'):
                        selections.append(dict(variant_id=form['variant_id'], quantity=int(form.get('variant_quantity', 1))))
                    if form.get('custom_name'):
                        selections.append(dict(name=form['custom_name'], quantity=int(form.get('custom_quantity', 1)),
                            price_minor=int(form.get('custom_price', 0)), discount_minor=int(form.get('custom_discount', 0))))
                order_edits.commit_edit(db, site, user_id, link_id, selections, form.get('reason', ''),
                                        form.get('request_key', ''), form.get('version', ''))
                db.commit()
            return redirect(f'/admin/sites/{site_id}/orders/{link_id}/edit', 'Order edit saved.')
        except (CommerceError, ValueError, TypeError) as exc:
            return error(exc)

    @rt('/admin/sites/{site_id}/orders/{link_id}/edit/settle', methods=['POST'])
    async def post(session, request, site_id: str, link_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                order_edits.settle(db, site, user_id, link_id, form.get('edit_id'), form.get('action'))
                db.commit()
            return redirect(f'/admin/sites/{site_id}/orders/{link_id}/edit', 'Payment difference updated.')
        except CommerceError as exc:
            return error(exc)

    @rt('/admin/sites/{site_id}/orders/{link_id}/gift-card-refund', methods=['GET'])
    def get(session, site_id: str, link_id: str):
        try:
            with SessionLocal() as db:
                site, _ = access(db, session, site_id)
                link, order, _ = orders.owned_order(db, site, link_id)
                root = f'/admin/sites/{site_id}/orders/{link.id}'
                return shell('Refund gift card', panel(page_header('Refund gift card payment', back_href=root),
                    card(H2(order.number), P('Restore the gift card payment in full. '
                        'This action does not change fulfilment or restock items.'),
                        Form(token(session), Label('Refund reason', Textarea(name='reason', required=True, maxlength=500)),
                             Button('Refund gift card payment'), action=root+'/gift-card-refund', method='post'))))
        except CommerceError as exc:
            return error(exc)

    @rt('/admin/sites/{site_id}/orders/{link_id}/gift-card-refund', methods=['POST'])
    async def post(session, request, site_id: str, link_id: str):
        try:
            form = await request.form()
            check_csrf(session, form)
            with SessionLocal() as db:
                site, user_id = access(db, session, site_id)
                gift_cards.refund_gift_only_order(db, site, user_id, link_id, form.get('reason', ''))
                db.commit()
            return redirect(f'/admin/sites/{site_id}/orders/{link_id}', 'Gift card payment refunded.')
        except CommerceError as exc:
            return error(exc)

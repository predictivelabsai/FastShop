"""Isolated merchant route authorisation and CSRF for both commerce gaps."""
import re
from types import SimpleNamespace

import pytest
from fasthtml.common import Div, Input, fast_app
from starlette.responses import Response
from starlette.testclient import TestClient

from app import commerce_gap_routes
from app.services import CommerceError
from tests.test_order_management import workspace


@pytest.fixture
def browser(monkeypatch):
    with workspace() as w:
        monkeypatch.setattr(commerce_gap_routes, 'SessionLocal', w.sessions)
        app, rt = fast_app(secret_key='commerce-gap-tests-only')
        current = {'id': w.owner.id}
        def csrf(session):
            return Input(type='hidden', name='csrf_token', value='commerce-gap-csrf')
        def check_csrf(session, form):
            if form.get('csrf_token') != 'commerce-gap-csrf':
                raise CommerceError('Invalid CSRF token.')
        commerce_gap_routes.register_commerce_gap_routes(rt, lambda s:current['id'], csrf,
            check_csrf, lambda title, *children: Div(*children),
            lambda exc: Response(str(exc), status_code=400))
        with TestClient(app) as client:
            yield SimpleNamespace(**vars(w), client=client, actor=current, csrf='commerce-gap-csrf')


def test_gift_card_routes_crud_csrf_roles_and_foreign_objects(browser):
    w = browser
    root = f'/admin/sites/{w.site.id}/gift-cards'
    assert w.client.get(root).status_code == 200
    assert w.client.post(root, data={'code':'ROUTE-GIFT','amount_minor':'500'}).status_code == 400
    response = w.client.post(root, data={'csrf_token':w.csrf, 'code':'ROUTE-GIFT','amount_minor':'500'}, follow_redirects=False)
    assert response.status_code == 303
    detail = response.headers['location'].split('?')[0]
    assert 'ROUTE-GIFT' in w.client.get(detail).text
    assert w.client.post(detail+'/adjust', data={'amount_minor':'100'}).status_code == 400
    assert w.client.post(detail+'/adjust', data={'csrf_token':w.csrf, 'amount_minor':'100',
        'note':'Extra credit','request_key':'route-adjustment-key-001'}, follow_redirects=False).status_code == 303
    assert '$6.00' in w.client.get(detail).text
    assert w.client.post(detail+'/disable', data={'csrf_token':w.csrf}, follow_redirects=False).status_code == 303
    assert 'Disabled' in w.client.get(detail).text
    assert w.client.get(root+'/foreign-card').status_code == 400
    assert w.client.get('/admin/sites/foreign-site/gift-cards').status_code == 400
    w.actor['id'] = w.editor.id
    assert w.client.get(root).status_code == 400
    assert w.client.post(root, data={'csrf_token':w.csrf,'code':'EDITOR-GIFT','amount_minor':'500'}).status_code == 400


def test_order_edit_routes_prg_csrf_roles_and_scope(browser):
    w = browser
    root = f'/admin/sites/{w.site.id}/orders/{w.link.id}/edit'
    response = w.client.get(root)
    assert response.status_code == 200 and 'Reason for edit' in response.text
    version = re.search(r'name="version" value="([^"]+)"', response.text).group(1)
    line = w.order.lines[0]
    form = {'version':version,'request_key':'route-edit-request-key-001', 'reason':'Add gift wrapping',
        'quantity_'+line.id:'1', 'discount_'+line.id:'0','custom_name':'Gift wrapping',
        'custom_price':'500', 'custom_quantity':'1', 'custom_discount':'0'}
    assert w.client.post(root, data=form).status_code == 400
    form['csrf_token'] = w.csrf
    assert w.client.post(root, data=form, follow_redirects=False).status_code == 303
    result = w.client.get(root)
    assert 'Amount owing:' in result.text and 'Gift wrapping' in result.text
    assert w.client.get(f'/admin/sites/{w.site.id}/orders/foreign-order/edit').status_code == 400
    assert w.client.post(root+'/settle', data={'action':'paid'}).status_code == 400
    w.actor['id'] = w.editor.id
    assert w.client.get(root).status_code == 400
    assert w.client.post(root, data=form).status_code == 400

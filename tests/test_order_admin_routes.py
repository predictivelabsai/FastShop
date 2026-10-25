"""Route contracts for the P3 merchant order workspace."""
import re
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from fasthtml.common import Div, Input, fast_app
from sqlalchemy import select
from starlette.responses import Response
from starlette.testclient import TestClient

from app import content, order_admin_routes
from app.integrations.stripe_commerce import StripeGateway
from app.models import Order, ReturnRequest, SiteOrder, User
from app.services import CommerceError
from tests.test_order_management import workspace


@pytest.fixture
def web(monkeypatch):
    with workspace() as w:
        monkeypatch.setattr(order_admin_routes, "SessionLocal", w.sessions)
        app, rt = fast_app(secret_key="p3-routes-only")
        state = SimpleNamespace(user_id=w.owner.id)
        def actor(session):
            return state.user_id
        def csrf(session):
            return Input(name="csrf_token", value="p3-csrf", type="hidden")
        def check_csrf(session, form):
            if form.get("csrf_token") != "p3-csrf":
                raise CommerceError("Your session expired.")
        order_admin_routes.register_order_admin_routes(rt, actor, csrf, check_csrf,
            lambda title, *children: Div(*children), lambda exc: Response(str(exc), status_code=400))
        with TestClient(app) as client:
            yield SimpleNamespace(**vars(w), client=client, state=state,
                                  base=f"/admin/sites/{w.site.id}/orders", detail=f"/admin/sites/{w.site.id}/orders/{w.link.id}")


def post(w, path, values=None):
    return w.client.post(w.detail + path, data={"csrf_token": "p3-csrf", **(values or {})},
                         follow_redirects=False)


def test_index_filters_pagination_export_and_scope(web):
    w = web
    page = w.client.get(w.base)
    assert page.status_code == 200 and w.order.number in page.text
    assert "Abandoned checkouts" in page.text and "Fulfilment" in page.text
    assert w.order.number not in w.client.get(w.base + "?q=missing-order").text
    assert w.order.number in w.client.get(w.base + "?q=ORDER-SKU&per_page=1&page=1").text
    assert w.order.number not in w.client.get(w.base + "?payment=pending").text
    exported = w.client.get(w.base + "/export?q=ORDER-SKU")
    assert exported.status_code == 200 and w.order.number in exported.text
    assert "attachment" in exported.headers["content-disposition"]
    with w.sessions() as db:
        user = User(email="foreign-p3@example.test", name="Other")
        db.add(user)
        db.flush()
        other = content.create_site(db, user.id, "Other", "foreign-orders-p3")
        db.commit()
    assert w.client.get(f"/admin/sites/{other.id}/orders").status_code == 400
    w.state.user_id = w.editor.id
    assert w.client.get(w.base).status_code == 400


def test_detail_comments_tags_notes_archive_and_csrf(web):
    w = web
    assert w.client.get(w.detail).status_code == 200
    assert "Timeline" in w.client.get(w.detail).text
    assert w.client.post(w.detail + "/comments", data={"body": "Rejected"}, follow_redirects=False).status_code == 303
    assert "Rejected" not in w.client.get(w.detail).text
    assert post(w, "/comments", {"body": "Customer requested a morning delivery."}).status_code == 303
    assert "morning delivery" in w.client.get(w.detail).text
    assert post(w, "/tags", {"tags": "VIP, Wholesale"}).status_code == 303
    assert post(w, "/note", {"note": "Leave with reception"}).status_code == 303
    assert post(w, "/archive", {"archived": "1"}).status_code == 303
    with w.sessions() as db:
        link = db.get(SiteOrder, w.link.id)
        assert set(link.tags_json) == {"VIP", "Wholesale"}
        assert link.note == "Leave with reception" and link.is_archived
    assert w.order.number in w.client.get(w.base + "?view=archived&tag=VIP").text
    assert post(w, "/archive", {"archived": "0"}).status_code == 303
    slip = w.client.get(w.detail + "/packing-slip")
    assert slip.status_code == 200 and "Packing slip" in slip.text and "ORDER-SKU" in slip.text


def test_line_fulfilment_is_idempotent_and_invalid_quantity_is_rejected(web):
    w = web
    line = w.order.lines[0]
    values = {"qty_" + line.id: "1", "carrier": "UPS", "tracking_number": "1ZP3ROUTE",
              "tracking_url": "https://ups.com/track?tracknum=1ZP3ROUTE", "request_key": "route-fulfil-idempotent"}
    assert post(w, "/fulfil-items", values).status_code == 303
    assert post(w, "/fulfil-items", values).status_code == 303
    with w.sessions() as db:
        order = db.get(Order, w.order.id)
        assert order.status == "fulfilled" and order.lines[0].fulfilled_quantity == 1
    bad = post(w, "/fulfil-items", values | {"qty_" + line.id: "not-a-number"})
    assert "whole" in bad.headers["location"]
    assert "1ZP3ROUTE" in w.client.get(w.detail).text


def test_return_lifecycle_and_foreign_return_rejection(web):
    w = web
    line = w.order.lines[0]
    post(w, "/fulfil-items", {"qty_" + line.id: "1", "carrier": "UPS", "tracking_number": "1ZRETURN",
                             "request_key": "route-return-fulfil"})
    assert post(w, "/returns", {"qty_" + line.id: "1", "reason": "Wrong item received"}).status_code == 303
    with w.sessions() as db:
        row = db.scalar(select(ReturnRequest).where(ReturnRequest.site_order_id == w.link.id))
        assert row and row.status == "requested"
        return_id = row.id
    assert post(w, f"/returns/{return_id}/status", {"target": "approved"}).status_code == 303
    assert post(w, f"/returns/{return_id}/status", {"target": "received"}).status_code == 303
    assert "Refund return" in w.client.get(w.detail).text
    assert w.client.get(w.detail + "/refund-items?return_id=foreign").status_code == 400
    assert post(w, f"/returns/{return_id}/status", {"target": "closed"}).status_code == 303
    with w.sessions() as db:
        assert db.get(ReturnRequest, return_id).status == "closed"


def test_refund_review_exact_confirmation_and_gateway_idempotency(web, monkeypatch):
    w = web
    calls = []
    def respond(request):
        payload = parse_qs(request.content.decode())
        calls.append(payload)
        return httpx.Response(200, json={"id": "re_p3_route", "payment_intent": "pi_order_fixture",
            "amount": int(payload["amount"][0]), "currency": "usd", "status": "succeeded", "livemode": False})
    monkeypatch.setenv(f"FASTSHOP_STRIPE_{w.site.id.upper()}_SECRET_KEY", "sk_test_p3_route")
    monkeypatch.setattr(order_admin_routes, "StripeGateway", lambda site: StripeGateway(
        site, transport=httpx.MockTransport(respond)))
    values = {"qty_" + w.order.lines[0].id: "1", "shipping": "1.00",
              "reason": "Customer returned an unopened item."}
    review = w.client.get(w.detail + "/refund-items", params=values)
    assert review.status_code == 200 and "Review refund" in review.text
    expected = re.search('name="expected_amount_minor" value="([^"]+)"', review.text).group(1)
    key = re.search('name="request_key" value="([^"]+)"', review.text).group(1)
    assert post(w, "/refund-items", values | {"request_key": key,
                "expected_amount_minor": expected}).status_code == 303
    assert calls == []
    assert post(w, "/refund-items", values | {"request_key": key, "confirmed": "on",
                "expected_amount_minor": "1"}).status_code == 303
    assert calls == []
    valid = values | {"request_key": key, "confirmed": "on", "expected_amount_minor": expected}
    assert post(w, "/refund-items", valid).status_code == 303
    assert len(calls) == 1 and int(calls[0]["amount"][0]) == int(expected)
    assert post(w, "/refund-items", valid).status_code == 303
    assert len(calls) == 1


def test_bulk_selection_export_and_role_csrf(web):
    w = web
    response = w.client.post(w.base + "/bulk", data={"csrf_token": "p3-csrf",
        "ids": [w.link.id], "action": "archive"}, follow_redirects=False)
    assert response.status_code == 303
    with w.sessions() as db:
        assert db.get(SiteOrder, w.link.id).is_archived
    exported = w.client.post(w.base + "/bulk", data={"csrf_token": "p3-csrf",
        "ids": [w.link.id], "action": "export"})
    assert exported.status_code == 200 and w.order.number in exported.text
    w.state.user_id = w.editor.id
    w.client.post(w.base + "/bulk", data={"csrf_token": "p3-csrf",
        "ids": [w.link.id], "action": "unarchive"}, follow_redirects=False)
    with w.sessions() as db:
        assert db.get(SiteOrder, w.link.id).is_archived


@pytest.mark.parametrize("endpoint,values", [
    ("/comments", {"body": "Rejected note"}), ("/tags", {"tags": "Rejected"}),
    ("/note", {"note": "Rejected note"}), ("/archive", {"archived": "1"}),
    ("/fulfil-items", {"carrier": "UPS", "tracking_number": "REJECTED", "request_key": "route-no-csrf-fulfil"}),
    ("/returns", {"reason": "Rejected return"}),
    ("/returns/foreign/status", {"target": "approved"}),
    ("/refund-items", {"confirmed": "on", "request_key": "route-no-csrf-refund"}),
])
def test_every_mutation_requires_csrf_before_service_calls(web, endpoint, values):
    response = web.client.post(web.detail + endpoint, data=values, follow_redirects=False)
    assert response.status_code == 303 and "session" in response.headers["location"]
    with web.sessions() as db:
        link = db.get(SiteOrder, web.link.id)
        assert not link.is_archived and not link.tags_json and not link.note


def test_refund_rejects_negative_fractional_and_foreign_line_quantities(web):
    w = web
    for values in ({"qty_foreign": "1"}, {"qty_" + w.order.lines[0].id: "-1"},
                   {"qty_" + w.order.lines[0].id: "1.5"}):
        response = w.client.get(w.detail + "/refund-items", params=values | {
            "reason": "A rejected malformed request."})
        assert response.status_code == 400
    assert w.client.get(w.detail + "/refund-items", params={"qty_" + w.order.lines[0].id: "1",
        "shipping": "0.001", "reason": "A rejected malformed shipping request."}).status_code == 400

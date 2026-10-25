"""Merchant customer routes protect personal data and every mutation boundary."""

import json
from types import SimpleNamespace

import pytest
from fasthtml.common import Div, Input, fast_app
from sqlalchemy import func, select
from starlette.responses import Response
from starlette.testclient import TestClient

from app import customer_admin as customers
from app import customer_admin_routes
from app.models import CustomerAddress, CustomerComment, Membership, ShopCustomer, User
from app.services import CommerceError
from tests.test_customer_admin import customer_db as customer_db


@pytest.fixture
def customer_web(customer_db, monkeypatch):
    w = customer_db
    editor = User(email="customer-editor@example.test", name="Editor")
    w.db.add(editor)
    w.db.flush()
    w.db.add(Membership(tenant_id=w.site.tenant_id, user_id=editor.id, role="editor"))
    for index in range(27):
        customers.save_customer(w.db, w.site, {"email": f"person{index:02}@example.test", "name": f"Person {index:02}"})
    w.db.commit()
    monkeypatch.setattr(customer_admin_routes, "SessionLocal", w.sessions)
    app, rt = fast_app(secret_key="customer-tests-only")
    current_actor = {"id": w.owner.id}

    def check_csrf(session, form):
        if form.get("csrf_token") != "customers-test-token":
            raise CommerceError("Invalid CSRF token.")

    customer_admin_routes.register_customer_admin_routes(rt, lambda session: current_actor["id"],
        lambda session: Input(type="hidden", name="csrf_token", value="customers-test-token"),
        check_csrf, lambda title, *children: Div(*children), lambda exc: Response(str(exc), status_code=400))
    with TestClient(app) as client:
        yield SimpleNamespace(**vars(w), client=client, editor=editor, actor=current_actor,
                              csrf="customers-test-token", base=f"/admin/sites/{w.site.id}/customers")


def test_customer_index_search_filter_pagination_and_export(customer_web):
    w = customer_web
    response = w.client.get(w.base)
    assert response.status_code == 200
    assert response.text.count('name="customer_ids"') == 25
    assert 'data-select-all=""' in response.text
    assert response.text.count('data-row-select=""') == 25
    assert "Page 1 · 28 customers" in response.text
    page = w.client.get(w.base + "?page=2")
    assert page.text.count('name="customer_ids"') == 3
    assert w.client.get(w.base + "?q=alice&tag=VIP").text.count('name="customer_ids"') == 1
    assert w.client.get(w.base + "?orders_min=1").text.count('name="customer_ids"') == 0
    assert w.client.get(w.base + "?orders_min=-1").status_code == 400
    assert w.client.get(w.base + "?spent_min_amount=1.001").status_code == 400
    assert w.client.get(w.base + "?spent_min_amount=0.01").text.count('name="customer_ids"') == 0
    assert w.client.get(w.base + "?page=invalid").status_code == 400
    export = w.client.get(w.base + "/export?q=alice")
    assert export.status_code == 200
    assert "First Name,Last Name,Email,Accepts Email Marketing" in export.text
    assert "Alice" in export.text and "Person" not in export.text
    assert export.headers["cache-control"] == "private, no-store"
    assert "Private customer" not in response.text


def test_customer_routes_reject_foreign_site_customer_address_segment(customer_web):
    w = customer_web
    other = f"/admin/sites/{w.other.id}/customers"
    for path in ("", "/new", "/import", "/export", "/mail", "/" + w.secret.id):
        assert w.client.get(other + path).status_code == 400
    assert w.client.get(w.base + "/" + w.secret.id).status_code == 400
    assert w.client.get(w.base + "?segment=foreign").status_code == 400
    assert w.client.post(w.base + "/" + w.secret.id, data={"csrf_token": w.csrf, "email": "new@example.test", "account_state": "active"}).status_code == 400
    assert w.client.post(w.base + "/" + w.customer.id + "/addresses/foreign", data={"csrf_token": w.csrf, "action": "delete"}).status_code == 400


@pytest.mark.parametrize("suffix", ["/new", "/bulk", "/segments", "/import", "/CUSTOMER", "/CUSTOMER/comments", "/CUSTOMER/addresses", "/CUSTOMER/addresses/foreign"])
def test_every_customer_write_requires_csrf_and_manager(customer_web, suffix):
    w = customer_web
    url = w.base + suffix.replace("CUSTOMER", w.customer.id)
    assert w.client.post(url, data={}).status_code == 400
    w.actor["id"] = w.editor.id
    assert w.client.post(url, data={"csrf_token": w.csrf}).status_code == 400


def test_customer_create_edit_address_consent_comments_and_bulk_tags(customer_web):
    w = customer_web
    assert w.client.get(w.base + "/new").status_code == 200
    form = {"csrf_token": w.csrf, "name": "New Customer", "email": "new@example.test", "phone": "+37255555", "note": "Ring bell", "tags": "VIP", "account_state": "active", "email_marketing_consent": "on", "sms_marketing_consent": "on"}
    response = w.client.post(w.base + "/new", data=form, follow_redirects=False)
    assert response.status_code == 303
    with w.sessions() as db:
        c = db.scalar(select(ShopCustomer).where(ShopCustomer.email == "new@example.test"))
        assert c.email_consent_source == c.sms_consent_source == "admin"
        customer_id = c.id
    detail = w.base + "/" + customer_id
    assert "New Customer" in w.client.get(detail).text
    assert w.client.post(detail + "/comments", data={"csrf_token": w.csrf, "body": "Customer called"}).status_code == 200
    assert "Customer called" in w.client.get(detail).text
    address = {"csrf_token": w.csrf, "name": "Office", "line1": "1 Main Road", "city": "Tallinn", "postal_code": "10001", "country": "EE", "is_default": "on"}
    assert w.client.post(detail + "/addresses", data=address).status_code == 200
    with w.sessions() as db:
        a = db.scalar(select(CustomerAddress).where(CustomerAddress.customer_id == customer_id))
        assert a.is_default and a.line1 == "1 Main Road"
        address_id = a.id
    address["line1"] = "2 Main Road"
    assert w.client.post(detail + "/addresses/" + address_id, data=address).status_code == 200
    assert "2 Main Road" in w.client.get(detail).text
    assert w.client.post(detail + "/addresses/" + address_id, data={"csrf_token": w.csrf, "action": "delete"}).status_code == 200
    assert w.client.post(w.base + "/bulk", data={"csrf_token": w.csrf, "customer_ids": customer_id, "tags": "wholesale"}).status_code == 200
    form.update(name="Edited customer", account_state="disabled")
    del form["email_marketing_consent"]
    del form["sms_marketing_consent"]
    assert w.client.post(detail, data=form).status_code == 200
    with w.sessions() as db:
        c = db.get(ShopCustomer, customer_id)
        assert c.name == "Edited customer" and not c.is_active
        assert not c.email_marketing_consent and not c.sms_marketing_consent
        assert db.scalar(select(func.count(CustomerAddress.id)).where(CustomerAddress.customer_id == c.id)) == 0
        assert db.scalar(select(CustomerComment.body).where(CustomerComment.customer_id == c.id)) == "Customer called"


def test_saved_customer_segment_and_csv_preview_apply(customer_web):
    w = customer_web
    response = w.client.post(w.base + "/segments", data={"csrf_token": w.csrf, "name": "VIP view", "filters_json": json.dumps({"tag": "VIP", "q": "Alice"})}, follow_redirects=False)
    assert response.status_code == 303
    page = w.client.get(response.headers["location"])
    assert page.text.count('name="customer_ids"') == 1
    assert "VIP view" in page.text
    assert w.client.post(w.base + "/segments", data={"csrf_token": w.csrf, "name": "Bad", "filters_json": "[]"}).status_code == 400
    payload = "First Name,Last Name,Email,Accepts Email Marketing\nCSV,Customer,csv@example.test,yes\nInvalid,,bad,no\n"
    preview = w.client.post(w.base + "/import", data={"csrf_token": w.csrf}, files={"file": ("customers.csv", payload, "text/csv")})
    assert preview.status_code == 200 and "Dry-run preview" in preview.text
    assert "Enter a valid email address" in preview.text
    with w.sessions() as db:
        assert db.scalar(select(ShopCustomer.id).where(ShopCustomer.email == "csv@example.test")) is None
    for _ in range(2):
        result = w.client.post(w.base + "/import", data={"csrf_token": w.csrf, "csv_text": payload, "apply": "yes"})
        assert result.status_code == 200 and "Import result" in result.text
    with w.sessions() as db:
        assert db.scalar(select(func.count(ShopCustomer.id)).where(ShopCustomer.email == "csv@example.test")) == 1
    assert w.client.get(w.base + "/mail").status_code == 200
    assert "Email queue" in w.client.get(w.base).text
    w.actor["id"] = w.editor.id
    assert w.client.get(w.base).status_code == 200
    assert w.client.get(w.base + "/new").status_code == 400

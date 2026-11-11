"""Subscriber persistence, public consent and merchant workflows."""

import re
import secrets
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fasthtml.common import Div, Input, fast_app
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from starlette.testclient import TestClient

from app import content, customer_services, marketing_subscriber_routes, marketing_subscribers
from app.admin_ui import AdminContextMiddleware, admin_page, admin_scope
from app.db import SessionLocal
from app.models import (
    Base,
    CustomerChallenge,
    MarketingConsent,
    MarketingSubscriber,
    ShopCustomer,
    Site,
    SitePage,
    User,
)
from app.services import CommerceError
from tests.test_checkout_services import ready  # noqa: F401
from tests.test_customer_routes import csrf, customer_site  # noqa: F401
from tests.test_store_checkout_routes import storefront  # noqa: F401
from tests.test_us_commerce import DESTINATION, shop  # noqa: F401


@pytest.fixture
def subscriber_data():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        owner = User(email="subscriber-owner@example.test", name="Subscriber owner")
        other_owner = User(email="subscriber-other@example.test", name="Other owner")
        db.add_all((owner, other_owner))
        db.flush()
        site = content.create_site(db, owner.id, "Subscriber shop", "subscribers-" + uuid4().hex[:8])
        other_site = content.create_site(
            db, other_owner.id, "Other subscriber shop", "other-subscribers-" + uuid4().hex[:8],
        )
        db.commit()
    yield SimpleNamespace(engine=engine, sessions=sessions, site=site, other_site=other_site,
                          owner=owner, other_owner=other_owner)
    engine.dispose()


@pytest.fixture
def subscriber_admin(subscriber_data, monkeypatch):
    w = subscriber_data
    with w.sessions() as db:
        owner = db.scalar(select(User).where(User.email == "subscriber-owner@example.test"))
        db.commit()
    state = SimpleNamespace(user_id=owner.id)

    def actor(_session):
        return state.user_id

    def csrf(session):
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        return Input(type="hidden", name="csrf_token", value=session["csrf_token"])

    def check_csrf(session, form):
        if not session.get("csrf_token") or not secrets.compare_digest(
            session["csrf_token"], str(form.get("csrf_token", "")),
        ):
            raise CommerceError("Your session expired. Reload and try again.")

    def shell(title, *children):
        scope = admin_scope.get() or {}
        return admin_page(title, *children, site=w.site, sites=[w.site], user=owner,
                          path=scope.get("path", ""))

    app, rt = fast_app(secret_key="subscriber-routes-test-only")
    app.add_middleware(AdminContextMiddleware)
    monkeypatch.setattr(marketing_subscriber_routes, "SessionLocal", w.sessions)
    marketing_subscriber_routes.register_marketing_subscriber_routes(
        rt, actor, csrf, check_csrf, shell, lambda exc: Response(str(exc), status_code=400),
    )

    @rt("/sites/{slug}/", methods=["GET"])
    def get(session, slug: str):
        return Div(csrf(session))

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=w.sessions, site=w.site,
                              other_site=w.other_site, owner=owner, other_owner=w.other_owner,
                              state=state)


def test_subscriber_records_normalize_and_deduplicate_per_site(subscriber_data):
    w = subscriber_data
    with w.sessions() as db:
        site = db.get(type(w.site), w.site.id)
        first, created = marketing_subscribers.upsert_subscriber(
            db, site, "  Reader@Example.Test ", source="footer", status="pending",
            evidence="I agree to receive marketing emails.",
        )
        db.commit()
        repeated, repeated_created = marketing_subscribers.upsert_subscriber(
            db, site, "reader@example.test", source="footer", status="pending",
            evidence="I agree to receive marketing emails.",
        )
        db.commit()
        assert created and not repeated_created
        assert first.id == repeated.id
        assert first.email == "reader@example.test"
        assert len(first.unsubscribe_token) >= 32
        assert first.status == "pending"
        assert marketing_subscribers.counts(db, site) == {
            "pending": 1, "subscribed": 0, "unsubscribed": 0, "total": 1,
        }


def test_subscriber_records_are_isolated_by_site_and_tenant(subscriber_data):
    w = subscriber_data
    with w.sessions() as db:
        for site in (db.get(type(w.site), w.site.id), db.get(type(w.other_site), w.other_site.id)):
            marketing_subscribers.upsert_subscriber(
                db, site, "same@example.test", source="admin", status="subscribed",
                evidence="I have consent.",
            )
        db.commit()
        rows = list(db.scalars(select(MarketingSubscriber).order_by(MarketingSubscriber.site_id)))

    assert len(rows) == 2
    assert rows[0].email == rows[1].email == "same@example.test"
    assert rows[0].site_id != rows[1].site_id
    assert rows[0].tenant_id != rows[1].tenant_id


def test_subscriber_migration_revision_id_fits_alembic_limit():
    migration = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261111_0042_marketing_subscribers.py"
    source = migration.read_text(encoding="utf-8")
    assert 'revision = "20261111_0042"' in source
    assert len("20261111_0042") <= 32


def test_footer_signup_is_idempotent_and_renders_merchant_copy(customer_site):
    site, client, base = customer_site
    with SessionLocal() as db:
        site = db.get(Site, site.id)
        labels = {
            **(site.settings_json or {}).get("generic_labels", {}),
            "newsletter_title": "Notes from the shop",
            "newsletter_body": "New arrivals and useful updates, once a month.",
            "newsletter_success": "Footer welcome sent.",
            "newsletter_error": "Please check the address and try again.",
        }
        settings = {
            **site.settings_json,
            "generic_labels": labels,
        }
        site.settings_json = settings
        site.published_settings_json = settings
        site.status = "published"
        db.commit()

    page = client.get(base + "/")
    assert "Notes from the shop" in page.text
    assert "New arrivals and useful updates, once a month." in page.text
    assert f'action="{base}/newsletter"' in page.text
    data = {"csrf_token": csrf(page), "email": "footer-reader@example.test",
            "marketing_consent": "on", "newsletter_source": "footer"}
    first = client.post(base + "/newsletter", data=data)
    repeated = client.post(base + "/newsletter", data=data)
    assert first.status_code == repeated.status_code == 200
    assert "Footer welcome sent." in first.text
    assert "Footer welcome sent." in repeated.text
    with SessionLocal() as db:
        rows = list(db.scalars(select(MarketingSubscriber).where(
            MarketingSubscriber.site_id == site.id,
            MarketingSubscriber.email == "footer-reader@example.test",
        )))
        challenges = list(db.scalars(select(CustomerChallenge).where(
            CustomerChallenge.site_id == site.id, CustomerChallenge.purpose == "newsletter",
        )))
    assert len(rows) == len(challenges) == 1
    assert rows[0].status == "pending" and rows[0].source == "footer"

    invalid = client.post(base + "/newsletter", data=data | {"email": "invalid"})
    assert invalid.status_code == 200
    assert "Please check the address and try again." in invalid.text


def test_footer_honeypot_discards_submission(customer_site):
    site, client, base = customer_site
    page = client.get(base + "/")
    response = client.post(base + "/newsletter", data={
        "csrf_token": csrf(page), "email": "bot@example.test", "marketing_consent": "on",
        "newsletter_source": "footer", "website": "filled-by-bot",
    })

    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(MarketingSubscriber.id).where(
            MarketingSubscriber.site_id == site.id)) is None
        assert db.scalar(select(ShopCustomer.id).where(
            ShopCustomer.site_id == site.id, ShopCustomer.email == "bot@example.test")) is None


def test_account_opt_in_syncs_after_double_opt_in(customer_site):
    site, client, base = customer_site
    with SessionLocal() as db:
        site = db.get(Site, site.id)
        settings = {
            **site.settings_json,
            "account_copy": {
                **(site.settings_json or {}).get("account_copy", {}),
                "check_email": "Offer code sent.",
            },
        }
        site.settings_json = settings
        site.published_settings_json = settings
        db.commit()
    page = client.get(base + "/")
    request = client.post(base + "/newsletter", data={
        "csrf_token": csrf(page), "email": "account-reader@example.test",
        "marketing_consent": "on", "newsletter_source": "offer",
    })
    assert request.status_code == 200 and "Offer code sent." in request.text
    with SessionLocal() as db:
        challenge = db.scalar(select(CustomerChallenge).where(
            CustomerChallenge.site_id == site.id, CustomerChallenge.purpose == "newsletter",
        ))
        challenge_id, token = challenge.id, customer_services.challenge_token(challenge)
        subscriber = db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.site_id == site.id,
            MarketingSubscriber.email == "account-reader@example.test",
        ))
        assert subscriber.status == "pending" and subscriber.source == "account"
    verification = client.get(base + "/account/verify/" + challenge_id)
    confirmed = client.post(base + "/account/verify/" + challenge_id,
        data={"csrf_token": csrf(verification), "token": token})
    assert confirmed.status_code == 200
    with SessionLocal() as db:
        subscriber = db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.site_id == site.id,
            MarketingSubscriber.email == "account-reader@example.test",
        ))
        customer = db.scalar(select(ShopCustomer).where(
            ShopCustomer.site_id == site.id, ShopCustomer.email == "account-reader@example.test",
        ))
        consent = db.scalar(select(MarketingConsent).where(
            MarketingConsent.site_id == site.id, MarketingConsent.customer_id == customer.id,
        ))
        assert subscriber.status == "subscribed" and subscriber.source == "account"
        assert customer.email_marketing_consent is True
        assert consent.status == "subscribed"


def test_unsubscribe_token_works_on_hostname_and_rejects_bad_tokens(customer_site):
    site, client, _base = customer_site
    host = "unsubscribe-" + uuid4().hex[:10] + ".example.test"
    with SessionLocal() as db:
        site = db.get(Site, site.id)
        labels = {
            **(site.published_settings_json or {}).get("generic_labels", {}),
            "newsletter_unsubscribe_title": "Unsubscribe from marketing emails",
            "newsletter_unsubscribe_instruction": "Confirm below to stop marketing emails from this store.",
            "newsletter_unsubscribe_cta": "Unsubscribe",
            "newsletter_unsubscribed": "You will no longer receive marketing emails from this store.",
        }
        site.published_settings_json = {
            **(site.published_settings_json or {}), "generic_labels": labels,
        }
        site.hostname = host
        subscriber, _ = marketing_subscribers.upsert_subscriber(
            db, site, "reader-to-unsubscribe@example.test", source="footer", status="subscribed",
            evidence="I agree to receive marketing emails.",
        )
        token = subscriber.unsubscribe_token
        db.commit()

    bad = client.get("/unsubscribe?token=invalid", headers={"host": host})
    assert bad.status_code == 404
    page = client.get("/unsubscribe?token=" + token, headers={"host": host})
    assert page.status_code == 200 and "Confirm below" in page.text
    response = client.post("/unsubscribe?token=" + token, headers={"host": host}, data={
        "csrf_token": csrf(page), "token": token,
    })
    assert response.status_code == 200 and "You will no longer receive" in response.text
    with SessionLocal() as db:
        subscriber = db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.site_id == site.id,
            MarketingSubscriber.email == "reader-to-unsubscribe@example.test",
        ))
        assert subscriber.status == "unsubscribed"


def test_checkout_opt_in_syncs_subscriber_and_customer_consent(storefront):
    r, client, base, _gateway = storefront
    page_record = r.db.scalar(select(SitePage).where(
        SitePage.site_id == r.site.id, SitePage.tenant_id == r.site.tenant_id,
    ))
    page_record.published_json = {"kind": "newsletter"}
    r.db.commit()
    page = client.get(base + "/cart")
    page = client.post(base + "/cart/add", data={
        "csrf_token": csrf(page), "variant_id": r.variant.id, "quantity": "1",
    })
    page = client.get(base + "/checkout")
    assert 'name="marketing_consent"' in page.text
    version = re.search(r'name="version" value="(\d+)"', page.text).group(1)
    data = DESTINATION | {
        "csrf_token": csrf(page), "version": version, "email": "checkout-reader@example.test",
        "full_name": "Checkout Reader", "marketing_consent": "on",
    }
    review = client.post(base + "/checkout", data=data)
    assert review.status_code == 200 and "Review your order" in review.text
    subscriber = r.db.scalar(select(MarketingSubscriber).where(
        MarketingSubscriber.site_id == r.site.id,
        MarketingSubscriber.email == "checkout-reader@example.test",
    ))
    customer = r.db.scalar(select(ShopCustomer).where(
        ShopCustomer.site_id == r.site.id, ShopCustomer.email == "checkout-reader@example.test",
    ))
    assert subscriber.status == "subscribed" and subscriber.source == "checkout"
    assert subscriber.consent_evidence == "I agree to receive marketing emails"
    assert customer.email_marketing_consent is True


def test_admin_subscriber_list_is_owned_and_tenant_scoped(subscriber_admin):
    w = subscriber_admin
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        other_site = db.get(Site, w.other_site.id)
        own, _ = marketing_subscribers.upsert_subscriber(
            db, site, "own@example.test", source="footer", status="subscribed", evidence="I consent.",
        )
        marketing_subscribers.upsert_subscriber(
            db, other_site, "foreign@example.test", source="footer", status="subscribed", evidence="I consent.",
        )
        db.commit()
    path = f"/admin/sites/{w.site.id}/marketing/subscribers"
    response = w.client.get(path)
    assert response.status_code == 200
    assert "own@example.test" in response.text
    assert "foreign@example.test" not in response.text
    unsubscribed = w.client.post(path + f"/{own.id}/unsubscribe",
        data={"csrf_token": csrf(response)}, follow_redirects=False)
    assert unsubscribed.status_code == 303
    with w.sessions() as db:
        assert db.get(MarketingSubscriber, own.id).status == "unsubscribed"
    page = w.client.get(path)
    resubscribed = w.client.post(path + f"/{own.id}/subscribe",
        data={"csrf_token": csrf(page), "consent": "on"}, follow_redirects=False)
    assert resubscribed.status_code == 303
    with w.sessions() as db:
        row = db.get(MarketingSubscriber, own.id)
        assert row.status == "subscribed" and row.source == "admin"

    w.state.user_id = w.other_owner.id
    denied = w.client.get(path)
    assert denied.status_code == 400 and "access denied" in denied.text.lower()


def test_admin_manual_add_requires_consent(subscriber_admin):
    w = subscriber_admin
    page = w.client.get(f"/sites/{w.site.slug}/")
    data = {"csrf_token": csrf(page), "email": "manual@example.test"}
    rejected = w.client.post(f"/admin/sites/{w.site.id}/marketing/subscribers/add", data=data)
    assert rejected.status_code == 400 and "consent" in rejected.text.lower()
    created = w.client.post(f"/admin/sites/{w.site.id}/marketing/subscribers/add",
        data=data | {"consent": "on"}, follow_redirects=False)
    assert created.status_code == 303
    with w.sessions() as db:
        subscriber = db.scalar(select(MarketingSubscriber).where(
            MarketingSubscriber.site_id == w.site.id,
            MarketingSubscriber.email == "manual@example.test",
        ))
        assert subscriber.status == "subscribed" and subscriber.source == "admin"
        assert subscriber.consent_evidence.startswith("I have consent")


def test_admin_csv_export_escapes_formulas_and_requires_csrf(subscriber_admin):
    w = subscriber_admin
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        marketing_subscribers.upsert_subscriber(
            db, site, "+SUM(1+1)@example.test", source="import", status="subscribed",
            evidence="I have consent.",
        )
        db.commit()
    path = f"/admin/sites/{w.site.id}/marketing/subscribers/export"
    page = w.client.get(f"/admin/sites/{w.site.id}/marketing/subscribers")
    rejected = w.client.post(path, data={})
    assert rejected.status_code == 400
    response = w.client.post(path, data={"csrf_token": csrf(page)}, follow_redirects=False)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert "'+sum(1+1)@example.test" in response.text


def test_admin_csv_import_deduplicates_and_reports_counts(subscriber_admin):
    w = subscriber_admin
    path = f"/admin/sites/{w.site.id}/marketing/subscribers/import"
    page = w.client.get(f"/admin/sites/{w.site.id}/marketing/subscribers")
    payload = "email\nfirst@example.test\nFIRST@example.test\ninvalid\nsecond@example.test\n"
    response = w.client.post(path, data={"csrf_token": csrf(page), "consent": "on"},
        files={"file": ("subscribers.csv", payload, "text/csv")}, follow_redirects=False)
    assert response.status_code == 303
    assert "Added+2" in response.headers["location"]
    assert "skipped+1+duplicates" in response.headers["location"]
    with w.sessions() as db:
        rows = list(db.scalars(select(MarketingSubscriber).where(
            MarketingSubscriber.site_id == w.site.id,
        )))
        assert {row.email for row in rows} == {"first@example.test", "second@example.test"}
        assert {row.source for row in rows} == {"import"}


def test_admin_csv_import_requires_consent_and_limits_file_size(subscriber_admin):
    w = subscriber_admin
    path = f"/admin/sites/{w.site.id}/marketing/subscribers/import"
    page = w.client.get(f"/admin/sites/{w.site.id}/marketing/subscribers")
    token = csrf(page)
    payload = "email\nreader@example.test\n"
    missing_consent = w.client.post(path, data={"csrf_token": token},
        files={"file": ("subscribers.csv", payload, "text/csv")})
    assert missing_consent.status_code == 400 and "consent" in missing_consent.text.lower()
    oversized = w.client.post(path, data={"csrf_token": token, "consent": "on"},
        files={"file": ("subscribers.csv", "email\n" + "a" * 1_000_010, "text/csv")})
    assert oversized.status_code == 400 and "1 MB" in oversized.text


def test_store_agent_can_read_only_subscriber_counts(subscriber_data):
    from app.store_agent_ops import register_tools

    w = subscriber_data
    with w.sessions() as db:
        site = db.get(Site, w.site.id)
        owner = db.scalar(select(User).where(User.email == "subscriber-owner@example.test"))
        marketing_subscribers.upsert_subscriber(
            db, site, "active@example.test", source="footer", status="subscribed", evidence="I consent.",
        )
        marketing_subscribers.upsert_subscriber(
            db, site, "pending@example.test", source="account", status="pending", evidence="I consent.",
        )
        tools = register_tools(db, site, owner.id, "subscriber-counts-test")
        result = tools["marketing_subscriber_counts"].handler({})

    assert tools["marketing_subscriber_counts"].kind == "read"
    assert result == {"pending": 1, "subscribed": 1, "unsubscribed": 0, "total": 2}
    assert not any("email" in key for key in result)

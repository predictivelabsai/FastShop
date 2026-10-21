"""Bounded, idempotent demo cutover and non-destructive legacy reads."""
import copy
import json

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models import Base, Membership, Site, SiteCommerceSettings, SitePage, Tenant, User
from app.seed import seed
from app.services import CommerceError
from app.site_seed_liquid import bootstrap_liquid_demo, seed_h24you_liquid


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        seed(db, "operator@example.test")
        site = seed_h24you_liquid(db, "operator@example.test")
        tenant = Tenant(slug="h24you-kinetic", name="Kinetic")
        db.add(tenant)
        db.flush()
        previous = Site(tenant_id=tenant.id, channel_id=site.channel_id,
            slug="h24you-kinetic", name="Legacy", hostname="h24you.fastsme.com", status="preview",
            settings_json={"theme_pack": "kinetic"})
        db.add(previous)
        for role in ("merchant", "admin", "editor"):
            user = User(email=role + "@example.test", name=role, password_hash="unchanged")
            db.add(user)
            db.flush()
            db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
        db.commit()
        yield db, site, previous
    engine.dispose()


def test_cutover_unset_is_noop(database, monkeypatch):
    db, site, previous = database
    monkeypatch.delenv("FASTSHOP_H24YOU_LIQUID_HOSTNAME", raising=False)
    before = (site.hostname, site.status, previous.hostname, previous.status)
    bootstrap_liquid_demo(db, site)
    assert (site.hostname, site.status, previous.hostname, previous.status) == before
    assert db.scalar(select(Membership).where(
        Membership.tenant_id == site.tenant_id, Membership.role == "merchant")) is None


def test_cutover_is_idempotent_and_preserves_roles_content_passwords(database, monkeypatch):
    db, site, previous = database
    monkeypatch.setenv("FASTSHOP_H24YOU_LIQUID_HOSTNAME", "H24YOU.FASTSME.COM.")
    docs = [(p.id, copy.deepcopy(p.draft_json), copy.deepcopy(p.published_json))
        for p in db.scalars(select(SitePage).where(SitePage.site_id == site.id))]
    config = db.scalar(select(SiteCommerceSettings).where(SiteCommerceSettings.site_id == site.id))
    db.delete(config)
    db.flush()
    bootstrap_liquid_demo(db, site)
    db.commit()
    assert site.hostname == "h24you.fastsme.com" and site.status == "published"
    assert previous.hostname is None and previous.status == "draft"
    assert previous.settings_json == {"theme_pack": "kinetic"}
    config = db.scalar(select(SiteCommerceSettings).where(SiteCommerceSettings.site_id == site.id))
    assert config.mode == "sandbox" and config.customer_accounts_enabled
    assert site.published_settings_json["sandbox_demo"]
    for role in ("merchant", "admin", "editor"):
        user = db.scalar(select(User).where(User.email == role + "@example.test"))
        assert user.password_hash == "unchanged"
        assert db.scalar(select(Membership.role).where(
            Membership.tenant_id == site.tenant_id, Membership.user_id == user.id)) == role
    versions = (site.version, previous.version, config.version)
    seed_h24you_liquid(db, "operator@example.test")
    db.commit()
    assert (site.version, previous.version, config.version) == versions
    for page_id, draft, published in docs:
        page = db.get(SitePage, page_id)
        assert page.draft_json == draft and page.published_json == published


@pytest.mark.parametrize("hostname", ["https://example.com", "bad", "127.0.0.1", "a.test/path"])
def test_invalid_hostname_fails_before_modification(database, monkeypatch, hostname):
    db, site, previous = database
    monkeypatch.setenv("FASTSHOP_H24YOU_LIQUID_HOSTNAME", hostname)
    with pytest.raises(CommerceError):
        bootstrap_liquid_demo(db, site)
    assert site.hostname is None and previous.hostname == "h24you.fastsme.com"


def test_kinetic_documents_read_without_rewrite_but_writes_stay_strict(database):
    db, site, previous = database
    page = db.scalar(select(SitePage).where(SitePage.site_id == site.id))
    legacy = {"title": "Kinetic", "sections": [
        {"type": kind, "heading": kind} for kind in ("statement", "marquee", "story", "showcase")
    ]}
    payload = json.dumps(legacy)
    db.execute(text("UPDATE site_pages SET draft_json=:doc, published_json=:doc WHERE id=:id"),
        {"doc": payload, "id": page.id})
    db.commit()
    db.expire(page)
    assert page.draft_json == legacy and page.published_json == legacy
    page.title = "A metadata change"
    db.commit()
    assert json.loads(db.execute(text("SELECT draft_json FROM site_pages WHERE id=:id"),
        {"id": page.id}).scalar_one()) == legacy
    with pytest.raises(CommerceError):
        page.draft_json = {**legacy, "title": "New write"}
    db.rollback()


def test_membership_repair_is_bounded_and_dry_run(database, monkeypatch, capsys):
    from scripts import ensure_admin_access as repair

    db, site, previous = database
    for email in repair.ACCOUNTS:
        db.add(User(email=email, name=email, password_hash="unchanged"))
    db.commit()
    # Exercise the script against this disposable database, including dry run.
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(repair, "SessionLocal", sessionmaker(bind=db.bind))
    monkeypatch.setenv("DRY_RUN", "1")
    repair.main()
    assert "no writes" in capsys.readouterr().out
    monkeypatch.delenv("DRY_RUN")
    repair.main()
    repair.main()
    for email, role in repair.ACCOUNTS.items():
        user = db.scalar(select(User).where(User.email == email))
        memberships = list(db.scalars(select(Membership).where(Membership.user_id == user.id)))
        assert {m.tenant_id for m in memberships} == {site.tenant_id, previous.tenant_id}
        assert {m.role for m in memberships} == {role}
        assert user.password_hash == "unchanged"


def test_merchant_copilot_and_outbox_only_use_membership_tenants(database):
    from app import ai, main
    from app.integrations.fasterp import deliver_pending
    from app.models import OutboxEvent

    db, site, previous = database
    merchant = db.scalar(select(User).where(User.email == "merchant@example.test"))
    outsider = User(email="outsider@example.test", name="No memberships")
    db.add(outsider)
    db.flush()
    assert main.membership_tenants(db, {"id": outsider.id}) == set()
    assert main.membership_tenants(db, {"id": merchant.id}) == {previous.tenant_id}
    own = OutboxEvent(tenant_id=site.tenant_id, topic="local.test", aggregate_id="test")
    other = OutboxEvent(tenant_id=previous.tenant_id, topic="local.test", aggregate_id="other")
    db.add_all([own, other])
    db.flush()
    assert "1 pending" in ai.answer(db, "/sync", "/admin", True, tenant_ids={site.tenant_id})
    assert "0 pending" in ai.answer(db, "/sync", "/admin", True, tenant_ids=set())
    assert "Northstar" not in ai.answer(db, "/products", "/admin", True, tenant_ids={site.tenant_id})
    assert "harbour" not in ai.answer(db, "/products", "/admin", True, tenant_ids={site.tenant_id}).lower()
    assert deliver_pending(db, tenant_ids={site.tenant_id}) == (1, 0)
    assert own.status == "delivered" and other.status == "pending"

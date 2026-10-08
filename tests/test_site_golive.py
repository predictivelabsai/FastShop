import copy
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from app import commerce, content, site_golive, site_snippets
from app.config import settings
from app.models import (
    Base,
    Category,
    Membership,
    Product,
    ProductType,
    ProductVariant,
    SiteChangeSet,
    SitePage,
    User,
    VariantChannelListing,
)
from app.site_context import SiteHostMiddleware


@contextmanager
def workspace():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email=settings.admin_email, name="Operator")
        merchant = User(email="merchant@example.test", name="Merchant")
        db.add_all([owner, merchant])
        db.flush()
        site = content.create_site(db, owner.id, "Go-live test", "golive-test")
        db.add(Membership(tenant_id=site.tenant_id, user_id=merchant.id, role="merchant"))
        db.commit()
        yield db, engine, owner, merchant, site


def publish_page(db, site, path, *, reviewed_policy=False):
    page = db.scalar(select(SitePage).where(
        SitePage.site_id == site.id,
        SitePage.tenant_id == site.tenant_id,
        SitePage.path == path,
    ))
    document = copy.deepcopy(page.draft_json)
    if reviewed_policy:
        document["blocks"][0]["body"] = "Reviewed policy for this merchant and storefront."
        page.draft_json = copy.deepcopy(document)
    document["published_at"] = "2026-10-08T00:00:00+00:00"
    page.published_json = document
    return page


def prepare_publication(db, site):
    for path in ("/", "/shop", "/pages/about-us", "/pages/contact"):
        publish_page(db, site, path)
    db.flush()


def prepare_commerce(db, site):
    for path in site_golive.COMMERCE_POLICY_PATHS:
        publish_page(db, site, path, reviewed_policy=True)
    category = Category(tenant_id=site.tenant_id, slug="ready", name="Ready")
    kind = ProductType(tenant_id=site.tenant_id, slug="physical", name="Physical")
    db.add_all([category, kind])
    db.flush()
    product = Product(
        tenant_id=site.tenant_id,
        category_id=category.id,
        product_type_id=kind.id,
        slug="ready-product",
        name="Ready product",
        is_published=True,
    )
    db.add(product)
    db.flush()
    variant = ProductVariant(
        tenant_id=site.tenant_id,
        product_id=product.id,
        sku="READY-1",
        name="Standard",
        is_active=True,
    )
    db.add(variant)
    db.flush()
    db.add(VariantChannelListing(
        variant_id=variant.id,
        channel_id=site.channel_id,
        currency="USD",
        price_minor=2500,
    ))
    page = content.create_page(
        db,
        site,
        "Ready product",
        "/products/ready-product",
        "product",
        {"title": "Ready product", "sections": [{"type": "product"}]},
    )
    page.product_id = product.id
    page.published_json = copy.deepcopy(page.draft_json)
    config = commerce.settings_for(db, site, create=True)
    config.origin_json = {
        "country": "EE",
        "line1": "Test warehouse",
        "city": "Tallinn",
        "postal_code": "10111",
    }
    config.shipping_minor = 900
    config.allowed_states_json = ["CA"]
    config.tax_registration_reviewed = True
    config.product_tax_codes_json = {product.id: "txcd_00000000"}
    db.flush()
    return config, variant


def test_readiness_avoids_draft_compliance_menu_and_price_false_positives():
    with workspace() as (db, _, _, _, site):
        initial = site_golive.assess(db, site)
        assert not initial.passed
        assert {check.key for check in initial.failures} == {"required_pages", "menus"}

        prepare_publication(db, site)
        assert site_golive.assess(db, site).passed

        home = db.scalar(select(SitePage).where(SitePage.site_id == site.id, SitePage.path == "/"))
        home.draft_json = copy.deepcopy(home.draft_json)
        home.draft_json["blocks"][0]["body"] = "Clinically proven to cure diabetes."
        compliance_check = next(check for check in site_golive.assess(db, site).checks if check.key == "compliance")
        assert not compliance_check.passed
        home.draft_json = copy.deepcopy(home.published_json)

        config, variant = prepare_commerce(db, site)
        report = site_golive.assess(db, site, commerce_requested=True)
        assert not report.passed
        assert {check.key for check in report.failures} == {"site_published", "custom_domain"}
        db.delete(db.scalar(select(VariantChannelListing).where(
            VariantChannelListing.variant_id == variant.id,
            VariantChannelListing.channel_id == site.channel_id,
        )))
        db.flush()
        price_check = next(check for check in site_golive.assess(db, site, commerce_requested=True).checks
                           if check.key == "channel_prices")
        assert not price_check.passed and variant.sku in price_check.explanation
        assert config.mode == "disabled"


def test_publish_blocking_and_development_operator_override_are_audited():
    with workspace() as (db, _, owner, merchant, site):
        merchant_decision = site_golive.publish_site(
            db,
            site.id,
            merchant.id,
            site.version,
            "Merchant reviewed the draft and requests publication.",
            override=True,
            environment="development",
        )
        assert not merchant_decision.applied and site.status == "draft"

        tenant_admin = User(email="tenant-admin@example.test", name="Tenant admin")
        db.add(tenant_admin)
        db.flush()
        db.add(Membership(tenant_id=site.tenant_id, user_id=tenant_admin.id, role="admin"))
        db.flush()
        tenant_admin_decision = site_golive.publish_site(
            db,
            site.id,
            tenant_admin.id,
            site.version,
            "Tenant administrator requests a development override.",
            override=True,
            environment="development",
        )
        assert not tenant_admin_decision.applied and site.status == "draft"

        production_decision = site_golive.publish_site(
            db,
            site.id,
            owner.id,
            site.version,
            "Operator reviewed the draft for production publication.",
            override=True,
            environment="production",
        )
        assert not production_decision.applied and site.status == "draft"

        development_decision = site_golive.publish_site(
            db,
            site.id,
            owner.id,
            site.version,
            "Operator accepts the known development-only gaps for browser review.",
            override=True,
            environment="development",
        )
        assert development_decision.applied and development_decision.overridden
        assert site.status == "published"
        events = list(db.scalars(select(SiteChangeSet).where(
            SiteChangeSet.site_id == site.id,
            SiteChangeSet.source == "golive",
        ).order_by(SiteChangeSet.created_at)))
        assert [site_golive.audit_event(event)["decision"] for event in events] == [
            "blocked", "blocked", "blocked", "overridden",
        ]
        assert site_golive.audit_event(events[-1])["checks"]


def test_consent_gated_snippet_requires_reviewed_published_privacy_policy():
    with workspace() as (db, _, _, _, site):
        prepare_publication(db, site)
        site_snippets.put_snippet(db, site, "foot", {
            "content": '<script src="https://analytics.example.test/tag.js"></script>',
            "enabled": True,
            "note": "Analytics fixture",
        }, publish=True)
        db.flush()
        check = next(item for item in site_golive.assess(db, site).checks
                     if item.key == "snippet_consent")
        assert not check.passed
        publish_page(db, site, "/pages/privacy-policy", reviewed_policy=True)
        db.flush()
        check = next(item for item in site_golive.assess(db, site).checks
                     if item.key == "snippet_consent")
        assert check.passed


def test_domain_capture_normalizes_and_enforces_global_uniqueness():
    with workspace() as (db, _, owner, _, site):
        prepare_publication(db, site)
        assert site_golive.publish_site(
            db, site.id, owner.id, site.version, "All publication checks were reviewed and passed."
        ).applied
        decision = site_golive.bind_hostname(db, site.id, owner.id, site.version, "Shop.Example.Test.")
        assert decision.applied and site.hostname == "shop.example.test"

        other = content.create_site(db, owner.id, "Other", "other-golive")
        prepare_publication(db, other)
        assert site_golive.publish_site(
            db, other.id, owner.id, other.version, "The second site publication checks were reviewed."
        ).applied
        try:
            site_golive.bind_hostname(db, other.id, owner.id, other.version, "shop.example.test")
        except Exception as exc:
            assert "already bound" in str(exc)
        else:
            raise AssertionError("duplicate hostname was accepted")


def test_commerce_enablement_is_gated_and_disable_restores_read_only_mode():
    with workspace() as (db, _, owner, _, site):
        config = commerce.settings_for(db, site, create=True)
        db.flush()
        blocked = site_golive.set_sandbox_commerce(
            db, site.id, owner.id, site.version, True, config_version=config.version
        )
        assert not blocked.applied and config.mode == "disabled"

        prepare_publication(db, site)
        config, _ = prepare_commerce(db, site)
        assert site_golive.publish_site(
            db, site.id, owner.id, site.version, "Publication and public snapshots were reviewed."
        ).applied
        site_golive.bind_hostname(db, site.id, owner.id, site.version, "checkout.example.test")
        enabled = site_golive.set_sandbox_commerce(
            db, site.id, owner.id, site.version, True, config_version=config.version
        )
        assert enabled.applied and config.mode == "sandbox"
        disabled = site_golive.set_sandbox_commerce(
            db, site.id, owner.id, site.version, False, config_version=config.version
        )
        assert disabled.applied and config.mode == "disabled"


def test_host_middleware_requires_publish_and_sandbox_transitions(monkeypatch):
    with workspace() as (db, engine, owner, _, site):
        site.hostname = "routing.example.test"
        config = commerce.settings_for(db, site, create=True)
        db.commit()

        import app.site_context as context_module

        monkeypatch.setattr(context_module, "SessionLocal", lambda: Session(engine))

        async def endpoint(scope, receive, send):
            await PlainTextResponse(scope["path"])(scope, receive, send)

        client = TestClient(SiteHostMiddleware(endpoint), base_url="https://routing.example.test")
        assert client.get("/").status_code == 404
        site.status = "preview"
        db.commit()
        assert client.get("/").status_code == 404
        site.status = "published"
        db.commit()
        assert client.get("/").text == "/sites/golive-test/"
        assert client.get("/cart").status_code == 404
        config.mode = "sandbox"
        db.commit()
        assert client.get("/cart").text == "/sites/golive-test/cart"
        config.mode = "disabled"
        db.commit()
        assert client.get("/cart").status_code == 404
        assert owner.id

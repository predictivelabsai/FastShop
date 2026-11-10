"""Derived, offline go-live checks and reviewed site transitions."""

from __future__ import annotations

import copy
import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import func, select, update

from app import compliance, content, site_menus, site_snippets
from app.config import settings
from app.models import (
    CheckoutAttempt,
    Membership,
    Product,
    ProductVariant,
    Site,
    SiteChangeSet,
    SiteCommerceSettings,
    SitePage,
    User,
    VariantChannelListing,
)
from app.services import CommerceError
from app.site_samples import pending_reviews

PUBLICATION_REQUIRED_PATHS = ("/",)
COMMERCE_POLICY_PATHS = (
    "/pages/privacy-policy",
    "/pages/terms-and-conditions",
    "/pages/returns-and-refunds",
)


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    label: str
    passed: bool
    explanation: str
    action_url: str
    scope: str = "publish"
    blocking: bool = False

    @property
    def classification(self) -> str:
        return "technical prerequisite" if self.blocking else "advisory"

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "passed": self.passed,
            "explanation": self.explanation,
            "action_url": self.action_url,
            "scope": self.scope,
            "blocking": self.blocking,
            "classification": self.classification,
        }


@dataclass(frozen=True)
class ReadinessReport:
    site_id: str
    commerce_requested: bool
    checks: tuple[ReadinessCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)

    @property
    def blocking_failures(self) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.failures if check.blocking)

    @property
    def warnings(self) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.failures if not check.blocking)

    def blocking_failures_for(self, scope: str) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.blocking_failures if check.scope == scope)

    def warnings_for(self, scope: str) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.warnings if check.scope == scope)

    def as_dict(self) -> dict:
        return {
            "site_id": self.site_id,
            "commerce_requested": self.commerce_requested,
            "passed": self.passed,
            "checks": [check.as_dict() for check in self.checks],
            "blocking_failures": [check.as_dict() for check in self.blocking_failures],
            "warnings": [check.as_dict() for check in self.warnings],
        }


@dataclass(frozen=True)
class TransitionDecision:
    applied: bool
    overridden: bool
    message: str
    report: ReadinessReport | None = None


def normalize_hostname(value: str) -> str:
    """Normalize a custom DNS hostname; schemes, ports, paths and IPs are rejected."""
    raw = str(value or "").strip().lower().rstrip(".")
    if not raw:
        return ""
    if "://" in raw or any(character in raw for character in "/?#@"):
        raise CommerceError("Enter only a hostname, without https://, a path or credentials.")
    try:
        host = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise CommerceError("Enter a valid DNS hostname.") from exc
    if len(host) > 253 or "." not in host:
        raise CommerceError("Enter a complete hostname such as shop.example.com.")
    labels = host.split(".")
    if any(
        not 1 <= len(label) <= 63
        or not label[0].isalnum()
        or not label[-1].isalnum()
        or any(not (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        raise CommerceError("Enter a valid DNS hostname with letters, numbers, dots and hyphens.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise CommerceError("Use a DNS hostname rather than an IP address.")
    public_host = (urlsplit(settings.public_url).hostname or "").lower()
    if host in {"localhost", public_host}:
        raise CommerceError("Choose a custom hostname that is not the platform hostname.")
    return host


def _hostname_available(db, site: Site) -> tuple[bool, str]:
    if not site.hostname:
        return True, f"No custom domain is bound; preview remains at /sites/{site.slug}/."
    try:
        hostname = normalize_hostname(site.hostname)
    except CommerceError as exc:
        return False, str(exc)
    conflict = db.scalar(
        select(Site.id).where(func.lower(Site.hostname) == hostname, Site.id != site.id)
    )
    if conflict:
        return False, "This hostname is already bound to another site."
    return True, f"{hostname} is normalized and unique."


def _checkout_url_check(site: Site, host_ok: bool) -> tuple[bool, str]:
    if site.hostname:
        if not host_ok:
            return False, "The custom hostname is invalid or unavailable for checkout return URLs."
        return True, f"Checkout return URLs will use https://{site.hostname}."

    root = settings.public_url.rstrip("/") + "/sites/" + site.slug
    try:
        parsed = urlsplit(root)
        hostname = parsed.hostname
        _ = parsed.port  # Validate the optional port in the configured preview URL.
    except ValueError:
        return False, "Configure an HTTPS store URL or a local preview host for checkout return URLs."
    local_http = parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1"}
    valid = bool(
        hostname
        and not any(character.isspace() for character in hostname)
        and (parsed.scheme == "https" or local_http)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )
    if not valid:
        return False, "Configure an HTTPS store URL or a local preview host for checkout return URLs."
    return True, f"Checkout return URLs will use {root}."


def _published_pages(db, site: Site) -> dict[str, SitePage]:
    return {
        page.path: page
        for page in content.site_pages(db, site)
        if page.published_json
    }


def _policy_ready(page: SitePage | None) -> bool:
    if not page or not page.published_json:
        return False
    return "placeholder" not in str(page.published_json).lower()


def _menu_check(db, site: Site, published_pages: dict[str, SitePage]) -> tuple[bool, str]:
    menus = {menu.name: menu for menu in site_menus.menus_for(db, site)}
    missing = [name for name in ("header", "footer") if not menus.get(name) or menus[name].published_items_json is None]
    if missing:
        return False, "Publish the " + " and ".join(missing) + " menu before site publication."
    for name in ("header", "footer"):
        items = menus[name].published_items_json or []
        try:
            site_menus.validate_targets(db, site, items, published=True)
        except CommerceError as exc:
            return False, f"The {name} menu is invalid: {exc}"
        unavailable = sorted({
            item["path"] for item in items
            if item.get("kind") in {"page", "anchor"} and item.get("path") not in published_pages
        })
        if unavailable:
            return False, f"The {name} menu links to unpublished pages: {', '.join(unavailable)}."
    return True, "Header and footer menus have published snapshots and reachable targets."


def _compliance_check(db, site: Site) -> tuple[bool, str]:
    advice = []
    for page in content.site_pages(db, site):
        findings = compliance.scan_document(page.draft_json)
        flagged = findings["banned"] + findings["diseases"] + findings["hype"]
        advice.extend(f"{page.path}: {term}" for term in flagged if f"{page.path}: {term}" not in advice)
    for claim in site.settings_json.get("claims", []):
        if not claim.get("approved"):
            continue
        findings = compliance.scan_text(str(claim.get("text", "")))
        flagged = findings["banned"] + findings["diseases"] + findings["hype"]
        advice.extend(f"approved benefit statement: {term}" for term in flagged
                      if f"approved benefit statement: {term}" not in advice)
    suffix = f" Copy advisory: review flagged wording ({', '.join(advice)})." if advice else ""
    return True, "Copy checks are advisory and do not block publication." + suffix


def _snippet_check(db, site: Site, published_pages: dict[str, SitePage]) -> tuple[bool, str]:
    gated = []
    for snippet in site_snippets.snippets_for(db, site):
        snapshot = snippet.published_json
        if snapshot and snapshot.get("enabled") and snapshot.get("content", "").strip():
            consent = site_snippets.inspect_content(snapshot["content"])
            if consent != "none":
                gated.append(consent)
    if not gated:
        return True, "No enabled published snippet needs analytics or marketing consent."
    if not _policy_ready(published_pages.get("/pages/privacy-policy")):
        return False, "Consent-gated snippets need a reviewed, published privacy policy."
    return True, "Consent-gated snippets use the storefront consent controls and a published privacy policy."


def assess(db, site: Site, *, commerce_requested: bool = False) -> ReadinessReport:
    """Compute the site checklist from persisted state without provider or network calls."""
    published_pages = _published_pages(db, site)
    missing_publication = [path for path in PUBLICATION_REQUIRED_PATHS if path not in published_pages]
    host_ok, host_explanation = _hostname_available(db, site)
    checkout_url_ok, checkout_url_explanation = _checkout_url_check(site, host_ok)
    menu_ok, menu_explanation = _menu_check(db, site, published_pages)
    compliance_ok, compliance_explanation = _compliance_check(db, site)
    reviews = pending_reviews(site.settings_json)
    checks = [
        ReadinessCheck(
            "required_pages",
            "Published home page",
            not missing_publication,
            "The home page has a published snapshot." if not missing_publication else
            "Publish the home page; a draft-only home cannot open the site.",
            f"/admin/sites/{site.id}/pages",
        ),
        ReadinessCheck(
            "hostname_unique",
            "Hostname is valid and unique",
            host_ok,
            host_explanation,
            f"/admin/sites/{site.id}/golive#domain",
        ),
        ReadinessCheck(
            "compliance",
            "Draft content passes compliance review",
            compliance_ok,
            compliance_explanation,
            f"/admin/sites/{site.id}/pages",
        ),
        ReadinessCheck(
            "merchant_review",
            "Merchant details are reviewed",
            not reviews,
            "No sample merchant values remain pending review." if not reviews else
            "Review or replace these sample values: " + ", ".join(reviews) + ".",
            f"/admin/sites/{site.id}/samples",
        ),
        ReadinessCheck(
            "menus",
            "Published menus have valid targets",
            menu_ok,
            menu_explanation,
            f"/admin/sites/{site.id}/menus",
        ),
    ]
    snippet_ok, snippet_explanation = _snippet_check(db, site, published_pages)
    checks.append(ReadinessCheck(
        "snippet_consent",
        "Snippet consent policy is ready",
        snippet_ok,
        snippet_explanation,
        f"/admin/sites/{site.id}/snippets",
    ))

    if commerce_requested:
        policy_missing = [path for path in COMMERCE_POLICY_PATHS if not _policy_ready(published_pages.get(path))]
        product_rows = list({product.id: product for product, page in db.execute(select(
            Product,
            SitePage,
        ).join(
            SitePage,
            (SitePage.product_id == Product.id)
            & (SitePage.site_id == site.id)
            & (SitePage.tenant_id == site.tenant_id),
        ).where(
            Product.tenant_id == site.tenant_id,
            Product.is_published.is_(True),
        )) if page.published_json}.values())
        product_ids = {product.id for product in product_rows}
        active_variants = list(db.scalars(select(ProductVariant).where(
            ProductVariant.tenant_id == site.tenant_id,
            ProductVariant.product_id.in_(product_ids) if product_ids else False,
            ProductVariant.is_active.is_(True),
        )))
        priced_variant_ids = set(db.scalars(select(VariantChannelListing.variant_id).where(
            VariantChannelListing.channel_id == site.channel_id,
            VariantChannelListing.variant_id.in_([variant.id for variant in active_variants])
            if active_variants else False,
        )))
        priced_variants = [variant for variant in active_variants if variant.id in priced_variant_ids]
        unpriced = [variant.sku for variant in active_variants if variant.id not in priced_variant_ids]
        config = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == site.id,
            SiteCommerceSettings.tenant_id == site.tenant_id,
        ))
        configuration_missing = []
        if not config:
            configuration_missing.append("saved commerce settings")
        else:
            origin = config.origin_json or {}
            if not all(str(origin.get(key, "")).strip() for key in ("line1", "city", "postal_code", "country")):
                configuration_missing.append("fulfilment origin")
            if config.shipping_minor is None:
                configuration_missing.append("shipping fee")
            if not config.allowed_states_json:
                configuration_missing.append("allowed destinations")
            if not config.tax_registration_reviewed:
                configuration_missing.append("tax registration review")
            if product_ids - set(config.product_tax_codes_json):
                configuration_missing.append("product tax codes")
        checks.extend([
            ReadinessCheck(
                "site_published",
                "Site is published",
                site.status == "published",
                "The reviewed site is public." if site.status == "published" else
                "Test checkout can run on the preview path. Publish the site to open it to the public.",
                f"/admin/sites/{site.id}/golive#publish",
                "commerce",
            ),
            ReadinessCheck(
                "custom_domain",
                "Custom domain is bound",
                bool(site.hostname) and host_ok,
                f"Checkout can use https://{site.hostname}." if site.hostname and host_ok else
                f"Test checkout remains available at /sites/{site.slug}/; bind a custom domain for public host traffic.",
                f"/admin/sites/{site.id}/golive#domain",
                "commerce",
            ),
            ReadinessCheck(
                "checkout_url",
                "Checkout return URL is valid",
                checkout_url_ok,
                checkout_url_explanation,
                f"/admin/sites/{site.id}/golive#domain",
                "commerce",
                blocking=True,
            ),
            ReadinessCheck(
                "commerce_policies",
                "Commerce policies are published",
                not policy_missing,
                "Privacy, terms and returns policies are reviewed and published." if not policy_missing else
                "Replace placeholder copy and publish: " + ", ".join(policy_missing) + ".",
                f"/admin/sites/{site.id}/pages",
                "commerce",
            ),
            ReadinessCheck(
                "catalog",
                "Published storefront products exist",
                bool(product_rows),
                f"{len(product_rows)} published product{'s' if len(product_rows) != 1 else ''} have published site pages." if product_rows else
                "Publish at least one product and its site product page.",
                f"/admin/sites/{site.id}/products",
                "commerce",
            ),
            ReadinessCheck(
                "priced_product",
                "At least one published product has a site-channel price",
                bool(priced_variants),
                "At least one published product has an active option with a site-channel price." if priced_variants else
                "Publish a product and site page with an active option that has a site-channel price.",
                f"/admin/sites/{site.id}/products",
                "commerce",
                blocking=True,
            ),
            ReadinessCheck(
                "channel_prices",
                "Active product options have channel prices",
                bool(active_variants) and not unpriced,
                f"{len(active_variants)} active product option{'s' if len(active_variants) != 1 else ''} have site-channel prices." if active_variants and not unpriced else
                ("Add a price for: " + ", ".join(unpriced) + "." if unpriced else "Add an active product option with a channel price."),
                f"/admin/sites/{site.id}/products",
                "commerce",
            ),
            ReadinessCheck(
                "commerce_configuration",
                "Test checkout configuration is complete",
                not configuration_missing,
                "Origin, shipping, destinations and reviewed tax settings are complete." if not configuration_missing else
                "Complete: " + ", ".join(configuration_missing) + ".",
                f"/admin/sites/{site.id}/commerce",
                "commerce",
            ),
        ])
    return ReadinessReport(site.id, commerce_requested, tuple(checks))


def _membership_role(db, site: Site, user_id: str) -> str:
    role = db.scalar(select(Membership.role).where(
        Membership.tenant_id == site.tenant_id,
        Membership.user_id == user_id,
    ))
    if role not in {"admin", "merchant"}:
        raise CommerceError("Site not found or access denied.")
    return role


def _is_operator(db, site: Site, user_id: str) -> bool:
    role = _membership_role(db, site, user_id)
    user = db.get(User, user_id)
    return bool(role == "admin" and user and user.email.lower() == settings.admin_email)


def _lock_site(db, site_id: str, user_id: str, version: int) -> Site:
    site = content.owned_site(db, site_id, user_id, publish=True)
    result = db.execute(update(Site).where(
        Site.id == site.id,
        Site.tenant_id == site.tenant_id,
        Site.version == version,
    ).values(version=Site.version).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise CommerceError("Site settings changed. Reload the checklist and try again.")
    db.refresh(site)
    return site


def _audit(
    db,
    site: Site,
    user_id: str,
    *,
    action: str,
    decision: str,
    reason: str,
    before: dict,
    report: ReadinessReport | None = None,
    overridden: bool = False,
) -> SiteChangeSet:
    event = {
        "schema": 1,
        "action": action,
        "decision": decision,
        "reason": reason,
        "actor_id": user_id,
        "overridden": overridden,
        "checks": [check.as_dict() for check in report.checks] if report else [],
        "blocking_failures": [check.as_dict() for check in getattr(report, "blocking_failures", ())],
        "warnings": [check.as_dict() for check in getattr(report, "warnings", ())],
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    current_mode = db.scalar(select(SiteCommerceSettings.mode).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    )) or "disabled"
    change = SiteChangeSet(
        tenant_id=site.tenant_id,
        site_id=site.id,
        user_id=user_id,
        source="golive",
        summary=f"{action}: {decision}"[:400],
        before_json={"version": before["version"], "state": before, "audit": event},
        after_json={"version": site.version, "state": {
            "version": site.version,
            "status": site.status,
            "hostname": site.hostname,
            "commerce_mode": current_mode,
        }, "audit": event},
        created_at=datetime.now(UTC),
    )
    db.add(change)
    db.flush()
    return change


def _state(site: Site, commerce_mode: str = "disabled") -> dict:
    return {
        "version": site.version,
        "status": site.status,
        "hostname": site.hostname,
        "commerce_mode": commerce_mode,
    }


def publish_site(
    db,
    site_id: str,
    user_id: str,
    version: int,
    reason: str,
    *,
    override: bool = False,
    environment: str | None = None,
) -> TransitionDecision:
    reason = str(reason or "").strip()
    if not 10 <= len(reason) <= 500:
        raise CommerceError("Explain the publication review in 10–500 characters.")
    site = _lock_site(db, site_id, user_id, version)
    operator = _is_operator(db, site, user_id)
    mode = db.scalar(select(SiteCommerceSettings.mode).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    )) or "disabled"
    before = _state(site, mode)
    report = assess(db, site)
    override_allowed = operator and (environment or settings.environment).lower() != "production"
    if report.failures and not (override and override_allowed):
        explanation = "Publication blocked: " + "; ".join(check.label for check in report.failures) + "."
        if override and not override_allowed:
            explanation = "Publication blocked: operator override is available only to administrators in development."
        _audit(db, site, user_id, action="publish", decision="blocked", reason=reason,
               before=before, report=report)
        return TransitionDecision(False, False, explanation, report)
    if site.status == "published":
        _audit(db, site, user_id, action="publish", decision="already-published", reason=reason,
               before=before, report=report, overridden=bool(report.failures))
        return TransitionDecision(False, bool(report.failures), "The site is already published.", report)
    from app.plans import ensure_published
    from app.plans import record as record_usage

    # Quota check (Phase 5d) right before the transition: readiness blocking
    # and idempotent republish attempts are evaluated first, and nothing is
    # flipped when the plan has no free publication slot.
    ensure_published(db, user_id)
    site.status = "published"
    site.version += 1
    db.flush()
    record_usage(db, site.tenant_id, "published", site_id=site.id, user_id=user_id)
    overridden = bool(report.failures)
    _audit(db, site, user_id, action="publish", decision="overridden" if overridden else "approved",
           reason=reason, before=before, report=report, overridden=overridden)
    message = "Site published with a development operator override." if overridden else "Site published after readiness review."
    compliance_check = next((check for check in report.checks if check.key == "compliance"), None)
    if compliance_check and "Copy advisory:" in compliance_check.explanation:
        message += " " + compliance_check.explanation
    return TransitionDecision(True, overridden, message, report)


def bind_hostname(db, site_id: str, user_id: str, version: int, hostname: str) -> TransitionDecision:
    site = _lock_site(db, site_id, user_id, version)
    if site.status != "published":
        raise CommerceError("Publish the reviewed site before binding its custom domain.")
    normalized = normalize_hostname(hostname)
    before = _state(site, db.scalar(select(SiteCommerceSettings.mode).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    )) or "disabled")
    conflict = db.scalar(select(Site.id).where(
        func.lower(Site.hostname) == normalized,
        Site.id != site.id,
    )) if normalized else None
    if conflict:
        raise CommerceError("That hostname is already bound to another site.")
    if site.hostname == normalized:
        return TransitionDecision(False, False, "The custom domain is unchanged.")
    site.hostname = normalized or None
    site.version += 1
    db.flush()
    action = "domain-bind" if normalized else "domain-unbind"
    _audit(db, site, user_id, action=action, decision="approved",
           reason="Merchant updated the custom domain binding.", before=before)
    message = f"Custom domain {normalized} bound. TLS remains platform-managed." if normalized else "Custom domain removed; the bounded preview path remains available."
    return TransitionDecision(True, False, message)


def set_sandbox_commerce(
    db,
    site_id: str,
    user_id: str,
    version: int,
    enabled: bool,
    *,
    config_version: int | None = None,
) -> TransitionDecision:
    site = _lock_site(db, site_id, user_id, version)
    config = db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    ).with_for_update().execution_options(populate_existing=True))
    if not config:
        raise CommerceError("Save commerce settings before changing checkout availability.")
    if config_version is not None and config.version != config_version:
        raise CommerceError("Commerce settings changed. Reload the checklist and try again.")
    before = _state(site, config.mode)
    if not enabled:
        if config.mode == "live":
            raise CommerceError("Only the platform operator can disable accepted live payments.")
        if config.mode == "disabled":
            return TransitionDecision(False, False, "Checkout is already off.")
        config.mode = "disabled"
        config.version += 1
        site.version += 1
        db.flush()
        _audit(db, site, user_id, action="sandbox-commerce-disable", decision="approved",
               reason="Merchant turned checkout off and restored the read-only storefront.", before=before)
        return TransitionDecision(True, False, "Checkout is off; the storefront is read-only.")
    report = assess(db, site, commerce_requested=True)
    blockers = report.blocking_failures_for("commerce")
    if blockers:
        _audit(db, site, user_id, action="sandbox-commerce-enable", decision="blocked",
               reason="Merchant requested Test mode.", before=before, report=report)
        return TransitionDecision(
            False,
            False,
            "Test mode is unavailable until these technical prerequisites pass: "
            + "; ".join(check.label for check in blockers) + ".",
            report,
        )
    if config.mode == "sandbox":
        return TransitionDecision(False, False, "Test mode is already active.", report)
    if config.mode != "disabled":
        raise CommerceError("Only Checkout off or Test mode is available here.")
    config.mode = "sandbox"
    config.version += 1
    site.version += 1
    db.flush()
    _audit(db, site, user_id, action="sandbox-commerce-enable", decision="approved",
           reason="Merchant requested Test mode after reviewing readiness warnings.",
           before=before, report=report)
    warnings = report.warnings_for("commerce")
    if warnings:
        message = "Test mode is on. Review: " + "; ".join(check.label for check in warnings) + "."
    else:
        message = "Test mode is on. Live payments remain off."
    return TransitionDecision(True, False, message, report)


def switch_live_payments(db, site_id: str, user_id: str, version: int,
                         config_version: int | None, enabled: bool) -> TransitionDecision:
    """Let a site merchant select live mode after Stripe's technical verification."""
    site = _lock_site(db, site_id, user_id, version)
    config = db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    ).with_for_update().execution_options(populate_existing=True))
    if not config:
        raise CommerceError("Save commerce settings before switching payment mode.")
    if config_version is not None and config.version != config_version:
        raise CommerceError("Commerce settings changed. Reload and try again.")
    before = _state(site, config.mode)
    in_flight = db.scalar(select(CheckoutAttempt.id).where(
        CheckoutAttempt.site_id == site.id,
        CheckoutAttempt.tenant_id == site.tenant_id,
        CheckoutAttempt.state.in_(("creating", "open")),
        CheckoutAttempt.expires_at > datetime.now(UTC),
    ).limit(1))
    if in_flight and (enabled and config.mode != "live" or not enabled and config.mode == "live"):
        raise CommerceError("A checkout is already in progress. Finish or let it expire before changing payment mode.")
    if enabled:
        if site.status != "published" or not site.hostname:
            raise CommerceError("Publish the site and bind its custom domain before switching on live payments. This is required for secure live checkout.")
        from app import live_credentials, stripe_connections
        connection = stripe_connections.connection_for(db, site, "live")
        credential = live_credentials.credential_for(db, site)
        connection_keys = stripe_connections.decrypted(db, site, connection) if connection else ("", "", "")
        if (not connection or not connection.verified_at or not connection.account_id
                or not credential or not credential.verified_at
                or not connection_keys[0].startswith(("sk_live_", "rk_live_"))
                or not connection_keys[2].startswith("pk_live_")
                or credential.id != connection.id and config.live_credential_id not in {None, credential.id}):
            raise CommerceError("Connect and verify a live Stripe secret and publishable key, with a webhook endpoint or signing secret, before switching on live payments.")
        if not (connection.webhook_endpoint_id or connection_keys[1]):
            raise CommerceError("Configure a live Stripe webhook endpoint or signing secret before switching on live payments.")
        if config.mode == "live":
            return TransitionDecision(False, False, "Live payments are already on.")
        config.mode = "live"
        config.live_credential_id = credential.id
        config.live_accepted_at = None
        config.live_accepted_by = None
        action, message = "merchant-live-enable", "Live payments are on. Readiness checks remain advisory."
    else:
        if config.mode != "live":
            return TransitionDecision(False, False, "Payments are already in test mode.")
        config.mode = "sandbox"
        config.live_credential_id = None
        config.live_accepted_at = None
        config.live_accepted_by = None
        action, message = "merchant-live-disable", "Payments are back in test mode."
    config.version += 1
    site.version += 1
    report = assess(db, site, commerce_requested=True)
    db.flush()
    _audit(db, site, user_id, action=action, decision="enabled" if enabled else "disabled",
           reason="Merchant changed the payment mode.", before=before, report=report)
    return TransitionDecision(True, False, message, report)


def audit_trail(db, site: Site, limit: int = 50) -> list[SiteChangeSet]:
    return list(db.scalars(select(SiteChangeSet).where(
        SiteChangeSet.site_id == site.id,
        SiteChangeSet.tenant_id == site.tenant_id,
        SiteChangeSet.source == "golive",
    ).order_by(SiteChangeSet.created_at.desc(), SiteChangeSet.id.desc()).limit(limit)))


def audit_event(change: SiteChangeSet) -> dict:
    return copy.deepcopy((change.after_json or {}).get("audit") or (change.before_json or {}).get("audit") or {})

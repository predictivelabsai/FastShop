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

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "passed": self.passed,
            "explanation": self.explanation,
            "action_url": self.action_url,
            "scope": self.scope,
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

    def as_dict(self) -> dict:
        return {
            "site_id": self.site_id,
            "commerce_requested": self.commerce_requested,
            "passed": self.passed,
            "checks": [check.as_dict() for check in self.checks],
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
    problems = []
    advice = []
    for page in content.site_pages(db, site):
        findings = compliance.scan_document(page.draft_json)
        blocked = findings["banned"] + findings["diseases"]
        if blocked:
            problems.append(f"{page.path}: {', '.join(blocked)}")
        if findings["hype"]:
            advice.extend(term for term in findings["hype"] if term not in advice)
    for claim in site.settings_json.get("claims", []):
        if not claim.get("approved"):
            continue
        findings = compliance.scan_text(str(claim.get("text", "")))
        blocked = findings["banned"] + findings["diseases"]
        if blocked:
            problems.append(f"approved benefit statement: {', '.join(blocked)}")
    if problems:
        return False, "Resolve compliance findings in draft content: " + "; ".join(problems) + "."
    suffix = f" Advisory wording found ({', '.join(advice)}); it does not block publication." if advice else ""
    return True, "Draft pages and approved benefit statements have no blocking claim findings." + suffix


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
            f"/admin/sites/{site.id}",
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
            f"/admin/sites/{site.id}",
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
                "Publish the reviewed site before enabling checkout.",
                f"/admin/sites/{site.id}/golive#publish",
                "commerce",
            ),
            ReadinessCheck(
                "custom_domain",
                "Custom domain is bound",
                bool(site.hostname) and host_ok,
                f"Checkout will use https://{site.hostname}." if site.hostname and host_ok else
                "Bind a unique custom domain before enabling checkout.",
                f"/admin/sites/{site.id}/golive#domain",
                "commerce",
            ),
            ReadinessCheck(
                "commerce_policies",
                "Commerce policies are published",
                not policy_missing,
                "Privacy, terms and returns policies are reviewed and published." if not policy_missing else
                "Replace placeholder copy and publish: " + ", ".join(policy_missing) + ".",
                f"/admin/sites/{site.id}",
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
                "Sandbox commerce configuration is complete",
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
    site.status = "published"
    site.version += 1
    db.flush()
    overridden = bool(report.failures)
    _audit(db, site, user_id, action="publish", decision="overridden" if overridden else "approved",
           reason=reason, before=before, report=report, overridden=overridden)
    message = "Site published with a development operator override." if overridden else "Site published after readiness review."
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
        raise CommerceError("Save sandbox commerce settings before changing checkout availability.")
    if config_version is not None and config.version != config_version:
        raise CommerceError("Commerce settings changed. Reload the checklist and try again.")
    before = _state(site, config.mode)
    if not enabled:
        if config.mode == "live":
            raise CommerceError("Only the platform operator can disable accepted live payments.")
        if config.mode == "disabled":
            return TransitionDecision(False, False, "Sandbox commerce is already disabled.")
        config.mode = "disabled"
        config.version += 1
        site.version += 1
        db.flush()
        _audit(db, site, user_id, action="sandbox-commerce-disable", decision="approved",
               reason="Merchant disabled sandbox checkout and restored the read-only storefront.", before=before)
        return TransitionDecision(True, False, "Sandbox commerce disabled; the storefront is read-only.")
    report = assess(db, site, commerce_requested=True)
    if report.failures:
        _audit(db, site, user_id, action="sandbox-commerce-enable", decision="blocked",
               reason="Merchant requested sandbox checkout.", before=before, report=report)
        return TransitionDecision(
            False,
            False,
            "Sandbox commerce blocked: " + "; ".join(check.label for check in report.failures) + ".",
            report,
        )
    if config.mode == "sandbox":
        return TransitionDecision(False, False, "Sandbox commerce is already enabled.", report)
    if config.mode != "disabled":
        raise CommerceError("Only disabled or sandbox mode is available in this slice.")
    config.mode = "sandbox"
    config.version += 1
    site.version += 1
    db.flush()
    _audit(db, site, user_id, action="sandbox-commerce-enable", decision="approved",
           reason="Merchant requested sandbox checkout after all readiness checks passed.",
           before=before, report=report)
    return TransitionDecision(True, False, "Sandbox commerce enabled. Live payments remain unavailable.", report)


def audit_trail(db, site: Site, limit: int = 50) -> list[SiteChangeSet]:
    return list(db.scalars(select(SiteChangeSet).where(
        SiteChangeSet.site_id == site.id,
        SiteChangeSet.tenant_id == site.tenant_id,
        SiteChangeSet.source == "golive",
    ).order_by(SiteChangeSet.created_at.desc(), SiteChangeSet.id.desc()).limit(limit)))


def audit_event(change: SiteChangeSet) -> dict:
    return copy.deepcopy((change.after_json or {}).get("audit") or (change.before_json or {}).get("audit") or {})

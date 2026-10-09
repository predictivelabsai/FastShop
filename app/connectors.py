"""Reusable, site-scoped migration connector registry and reviewed-plan workflow.

Connectors discover credentials only from operator-controlled configuration. A
merchant may request a bounded preview and confirm that exact stored snapshot,
but no route accepts origins, keys, secrets, page limits, or provider options.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select, update

from app.models import IntegrationPlan, Site
from app.services import CommerceError

PLAN_TTL = timedelta(minutes=30)
PLAN_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CredentialState:
    configured: bool
    message: str
    mode: str = "sandbox"


@dataclass(frozen=True)
class ConnectorCapabilities:
    imports: tuple[str, ...]
    exports: tuple[str, ...]
    max_pages: int
    max_items: int
    timeout_seconds: int


@dataclass(frozen=True)
class ExportArtifact:
    content: bytes
    media_type: str
    filename: str


@runtime_checkable
class Connector(Protocol):
    platform: str
    label: str
    capabilities: ConnectorCapabilities

    def credential_state(self, site: Site) -> CredentialState: ...

    def fetch(self, site: Site, *, transport: Any = None) -> dict: ...

    def dry_run(self, db, site: Site, user_id: str, *, transport: Any = None) -> tuple[dict, dict]: ...

    def apply_import(self, db, site: Site, user_id: str, payload: dict) -> dict: ...

    def export_bundle(self, db, site: Site) -> dict: ...


_REGISTRY: dict[str, Connector] = {}
_BUILTINS_LOADED = False


def register_connector(connector: Connector) -> None:
    if not isinstance(connector, Connector):
        raise TypeError("Connector does not implement the required protocol.")
    name = connector.platform.strip().lower()
    if not name or name in _REGISTRY:
        raise ValueError("Connector platform must be unique.")
    _REGISTRY[name] = connector


def _load_builtins() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    from app.integrations.csv_catalog import CsvImportConnector
    from app.integrations.shopify import ShopifyConnector
    from app.integrations.woocommerce import WooCommerceConnector
    from app.integrations.wordpress import WordPressConnector

    register_connector(CsvImportConnector())
    register_connector(ShopifyConnector())
    register_connector(WooCommerceConnector())
    register_connector(WordPressConnector())
    _BUILTINS_LOADED = True


def connector_for(platform: str) -> Connector:
    _load_builtins()
    connector = _REGISTRY.get(str(platform).strip().lower())
    if connector is None:
        raise CommerceError("Unknown integration connector.")
    return connector


def listed_connectors() -> list[Connector]:
    _load_builtins()
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def create_dry_run(db, site: Site, user_id: str, platform: str, *, transport: Any = None) -> IntegrationPlan:
    connector = connector_for(platform)
    state = connector.credential_state(site)
    if not state.configured:
        raise CommerceError(state.message)
    payload, report = connector.dry_run(db, site, user_id, transport=transport)
    now = datetime.now(UTC)
    plan = IntegrationPlan(
        tenant_id=site.tenant_id,
        site_id=site.id,
        user_id=user_id,
        platform=connector.platform,
        operation="import",
        schema_version=PLAN_SCHEMA_VERSION,
        version=1,
        status="pending",
        report_json=report,
        payload_json=payload,
        expires_at=now + PLAN_TTL,
    )
    db.add(plan)
    db.flush()
    return plan


def pending_plan(db, site: Site, plan_id: str, *, user_id: str | None = None) -> IntegrationPlan:
    query = select(IntegrationPlan).where(
        IntegrationPlan.id == plan_id,
        IntegrationPlan.tenant_id == site.tenant_id,
        IntegrationPlan.site_id == site.id,
    )
    if user_id is not None:
        query = query.where(IntegrationPlan.user_id == user_id)
    plan = db.scalar(query)
    if plan is None:
        raise CommerceError("Reviewed import plan not found.")
    return plan


def recent_plans(db, site: Site, *, limit: int = 8) -> list[IntegrationPlan]:
    return list(db.scalars(select(IntegrationPlan).where(
        IntegrationPlan.tenant_id == site.tenant_id,
        IntegrationPlan.site_id == site.id,
    ).order_by(IntegrationPlan.created_at.desc()).limit(limit)))


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


# ExternalMapping resource type that each connector's product rows use. Used to
# count how many of a reviewed plan's items would become brand-new products,
# so imports can be refused as a bounded whole before anything is written.
_PRODUCT_RESOURCE_TYPES = {
    "csv": "row",
    "shopify": "product",
    "woocommerce": "product",
}
_PRODUCT_PAYLOAD_KEYS = ("products", "rows")


def planned_new_products(db, site: Site, platform: str, payload: dict) -> int:
    """Count plan items that would create a product without an existing mapping."""
    from app.models import ExternalMapping

    resource_type = _PRODUCT_RESOURCE_TYPES.get(str(platform).strip().lower())
    if not resource_type:
        return 0
    rows: list = []
    for key in _PRODUCT_PAYLOAD_KEYS:
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, list):
            rows = value
            break
    external_ids = [str(row.get("external_id")) for row in rows if isinstance(row, dict) and row.get("external_id")]
    if not external_ids:
        return 0
    mapped = set(db.scalars(select(ExternalMapping.external_id).where(
        ExternalMapping.tenant_id == site.tenant_id,
        ExternalMapping.site_id == site.id,
        ExternalMapping.system == str(platform).strip().lower(),
        ExternalMapping.resource_type == resource_type,
        ExternalMapping.external_id.in_(external_ids),
    )))
    return sum(1 for external_id in external_ids if external_id not in mapped)


def apply_reviewed_plan(
    db,
    site: Site,
    user_id: str,
    plan_id: str,
    plan_version: int,
) -> tuple[IntegrationPlan, dict]:
    plan = pending_plan(db, site, plan_id, user_id=user_id)
    if plan.schema_version != PLAN_SCHEMA_VERSION:
        raise CommerceError("This import preview uses an unsupported version. Run a new dry run.")
    if plan.status != "pending" or plan.consumed_at is not None:
        raise CommerceError("This reviewed import plan has already been used.")
    if _aware(plan.expires_at) <= datetime.now(UTC):
        raise CommerceError("This import preview expired. Run a new dry run.")
    if plan.version != plan_version:
        raise CommerceError("This import preview changed. Reload before applying it.")

    claimed = db.execute(update(IntegrationPlan).where(
        IntegrationPlan.id == plan.id,
        IntegrationPlan.tenant_id == site.tenant_id,
        IntegrationPlan.site_id == site.id,
        IntegrationPlan.user_id == user_id,
        IntegrationPlan.status == "pending",
        IntegrationPlan.version == plan_version,
        IntegrationPlan.consumed_at.is_(None),
    ).values(status="applying", version=IntegrationPlan.version + 1))
    if claimed.rowcount != 1:
        raise CommerceError("This reviewed import plan has already been used.")
    db.flush()
    db.refresh(plan)

    # Quota enforcement (Phase 5d): a plan is applied or refused as one bounded
    # whole. The check runs after the claim but before the first write, so a
    # refusal leaves the preview pending and nothing half-imported (the caller
    # session rolls the status update back on the error).
    from app.plans import ensure_products

    ensure_products(db, user_id, additional=planned_new_products(db, site, plan.platform, plan.payload_json))

    result = connector_for(plan.platform).apply_import(db, site, user_id, plan.payload_json)
    created_products = int(result.get("products", {}).get("created", 0)) if isinstance(result, dict) else 0
    if created_products:
        from app.plans import record as record_usage

        record_usage(
            db, site.tenant_id, "product_created",
            site_id=site.id, user_id=user_id, quantity=created_products,
        )
    plan.status = "applied"
    plan.consumed_at = datetime.now(UTC)
    plan.report_json = {**plan.report_json, "applied": result}
    db.flush()
    return plan, result


def export_bundle(db, site: Site, platform: str) -> dict:
    return connector_for(platform).export_bundle(db, site)


def export_artifact(
    db,
    site: Site,
    platform: str,
    *,
    include_drafts: bool = False,
) -> ExportArtifact:
    """Return a download without performing a provider write."""
    connector = connector_for(platform)
    provider_export = getattr(connector, "export_artifact", None)
    if provider_export is not None:
        return provider_export(db, site, include_drafts=include_drafts)
    if include_drafts:
        raise CommerceError("This connector does not export CMS draft snapshots.")
    payload = json.dumps(connector.export_bundle(db, site), indent=2, sort_keys=True).encode()
    return ExportArtifact(
        content=payload,
        media_type="application/json",
        filename=f"fastshop-{site.id}-{connector.platform}-export.json",
    )


def export_review_artifact(db, site: Site, platform: str) -> ExportArtifact:
    """Return a connector-specific validation report without a remote write."""
    connector = connector_for(platform)
    provider_export = getattr(connector, "export_review_artifact", None)
    if provider_export is None:
        raise CommerceError("This connector does not provide a downloadable validation report.")
    return provider_export(db, site)

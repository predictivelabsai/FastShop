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
    from app.integrations.shopify import ShopifyConnector
    from app.integrations.woocommerce import WooCommerceConnector
    from app.integrations.wordpress import WordPressConnector

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

    result = connector_for(plan.platform).apply_import(db, site, user_id, plan.payload_json)
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

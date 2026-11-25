"""Encrypted, site-scoped connections that merchants manage themselves."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.live_credentials import decrypt_secret, encrypt_secret
from app.models import Site, SiteIntegrationCredential, new_id
from app.services import CommerceError


def _aad(site_id: str, credential_id: str) -> str:
    return f"fastshop:{site_id}:{credential_id}:site-integration"


def _encrypt(site_id: str, credential_id: str, payload: dict) -> bytes:
    return encrypt_secret(json.dumps(payload, separators=(",", ":")),
                          aad=_aad(site_id, credential_id))


def _decrypt(row: SiteIntegrationCredential) -> dict:
    try:
        return json.loads(decrypt_secret(row.encrypted_payload,
                                         aad=_aad(row.site_id, row.id)))
    except (ValueError, TypeError) as exc:
        raise CommerceError("Stored integration credentials are unavailable.") from exc


def credential_row(db, site: Site, provider: str):
    return db.scalar(select(SiteIntegrationCredential).where(
        SiteIntegrationCredential.site_id == site.id,
        SiteIntegrationCredential.tenant_id == site.tenant_id,
        SiteIntegrationCredential.provider == provider,
    ))


def payload_for(site: Site, provider: str, db=None) -> dict | None:
    """Return decrypted credentials internally, never from an admin page."""
    if db is not None:
        row = credential_row(db, site, provider)
        return _decrypt(row) if row and row.status == "connected" else None
    with SessionLocal() as session:
        row = credential_row(session, site, provider)
        return _decrypt(row) if row and row.status == "connected" else None


def importer_credentials(site: Site, platform: str) -> dict | None:
    """A store's saved importer credentials, or None to fall back to operator settings.

    Any storage problem (for example no encryption key on this server) also yields None, so
    an importer never fails because the store-level connection is unavailable.
    """
    try:
        return payload_for(site, platform)
    except Exception:  # noqa: BLE001 - absence and unavailability both mean "not saved"
        return None


def mask(value: str) -> str:
    value = str(value or "")
    return "••••" + value[-4:] if value else ""


def save(db, site: Site, provider: str, payload: dict, mode: str,
         hints: dict, status="connected"):
    row = credential_row(db, site, provider)
    credential_id = row.id if row else new_id()
    ciphertext = _encrypt(site.id, credential_id, payload)
    if row:
        row.mode = mode
        row.encrypted_payload = ciphertext
        row.masked_hints_json = hints
        row.status = status
        row.verified_at = datetime.now(UTC)
    else:
        db.add(SiteIntegrationCredential(
            id=credential_id, tenant_id=site.tenant_id, site_id=site.id,
            provider=provider, mode=mode, encrypted_payload=ciphertext,
            masked_hints_json=hints, status=status, verified_at=datetime.now(UTC),
        ))
    db.flush()
    return row


def disconnect(db, site: Site, provider: str) -> bool:
    row = credential_row(db, site, provider)
    if not row:
        return False
    db.delete(row)
    db.flush()
    return True

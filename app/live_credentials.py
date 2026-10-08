"""Encrypted, operator-approved Stripe live credentials and hard live-mode guards."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update

from app import content, site_golive
from app.config import settings
from app.models import (
    Membership,
    Site,
    SiteChangeSet,
    SiteCommerceSettings,
    SiteStripeLiveCredential,
    User,
    new_id,
)
from app.services import CommerceError

_SECRET_KEY = re.compile(r"sk_live_[A-Za-z0-9_]{8,}")
_WEBHOOK_SECRET = re.compile(r"whsec_[A-Za-z0-9_]{8,}")
_FORMAT = b"FSL1"


@dataclass(frozen=True)
class LiveCredentialState:
    configured: bool
    verified: bool
    accepted: bool
    credential_id: str = ""
    masked_key: str = ""
    created_by: str = ""
    created_at: datetime | None = None
    verified_at: datetime | None = None


def _key(material: str | None = None) -> bytes:
    configured = settings.store_key_encryption_key if material is None else material
    if len(configured.encode()) < 32:
        raise CommerceError("Live credential encryption is unavailable; configure the operator encryption key.")
    return hashlib.sha256(b"FastShop live credential encryption v1\x00" + configured.encode()).digest()


def encrypt_secret(value: str, *, aad: str, key_material: str | None = None) -> bytes:
    nonce = os.urandom(12)
    return _FORMAT + nonce + AESGCM(_key(key_material)).encrypt(nonce, value.encode(), aad.encode())


def decrypt_secret(value: bytes, *, aad: str, key_material: str | None = None) -> str:
    if not isinstance(value, bytes) or not value.startswith(_FORMAT) or len(value) < 33:
        raise CommerceError("Stored live credentials are unavailable.")
    try:
        return AESGCM(_key(key_material)).decrypt(
            value[4:16], value[16:], aad.encode()
        ).decode()
    except Exception as exc:
        raise CommerceError("Stored live credentials are unavailable.") from exc


def _aad(site_id: str, credential_id: str, field: str) -> str:
    return f"fastshop:{site_id}:{credential_id}:{field}"


def require_operator(db, site_id: str, user_id: str) -> Site:
    site = content.owned_site(db, site_id, user_id, publish=True)
    role = db.scalar(select(Membership.role).where(
        Membership.tenant_id == site.tenant_id,
        Membership.user_id == user_id,
    ))
    user = db.get(User, user_id)
    if role != "admin" or not user or not hmac.compare_digest(
        user.email.lower(), settings.admin_email
    ):
        raise CommerceError("Platform operator access is required.")
    return site


def credential_for(db, site: Site) -> SiteStripeLiveCredential | None:
    return db.scalar(select(SiteStripeLiveCredential).where(
        SiteStripeLiveCredential.site_id == site.id,
        SiteStripeLiveCredential.tenant_id == site.tenant_id,
    ))


def state_for(db, site: Site, config: SiteCommerceSettings | None = None) -> LiveCredentialState:
    credential = credential_for(db, site)
    config = config or db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    ))
    accepted = bool(
        config
        and config.mode == "live"
        and config.live_accepted_at
        and config.live_accepted_by
        and credential
        and credential.verified_at
        and config.live_credential_id == credential.id
    )
    if not credential:
        return LiveCredentialState(False, False, False)
    return LiveCredentialState(
        True,
        bool(credential.verified_at),
        accepted,
        credential.id,
        f"{credential.key_prefix}****{credential.key_last_four}",
        credential.created_by,
        credential.created_at,
        credential.verified_at,
    )


def store(db, site_id: str, user_id: str, secret_key: str, webhook_secret: str) -> SiteStripeLiveCredential:
    site = require_operator(db, site_id, user_id)
    secret_key = str(secret_key or "").strip()
    webhook_secret = str(webhook_secret or "").strip()
    if not _SECRET_KEY.fullmatch(secret_key) or not _WEBHOOK_SECRET.fullmatch(webhook_secret):
        raise CommerceError("Enter a Stripe live secret key and webhook signing secret.")
    config = db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    ).with_for_update())
    if not config:
        raise CommerceError("Save sandbox commerce settings before adding live credentials.")
    if config.mode == "live":
        raise CommerceError("Disable live payments before replacing credentials.")
    existing = credential_for(db, site)
    credential_id = existing.id if existing else new_id()
    encrypted_key = encrypt_secret(secret_key, aad=_aad(site.id, credential_id, "secret-key"))
    encrypted_webhook = encrypt_secret(
        webhook_secret, aad=_aad(site.id, credential_id, "webhook-secret")
    )
    if existing:
        credential = existing
        credential.secret_key_ciphertext = encrypted_key
        credential.webhook_secret_ciphertext = encrypted_webhook
        credential.key_prefix = "sk_live_"
        credential.key_last_four = secret_key[-4:]
        credential.created_by = user_id
        credential.created_at = datetime.now(UTC)
        credential.verified_at = None
        credential.verified_by = None
    else:
        credential = SiteStripeLiveCredential(
            id=credential_id,
            tenant_id=site.tenant_id,
            site_id=site.id,
            secret_key_ciphertext=encrypted_key,
            webhook_secret_ciphertext=encrypted_webhook,
            key_prefix="sk_live_",
            key_last_four=secret_key[-4:],
            created_by=user_id,
        )
        db.add(credential)
    config.live_accepted_at = None
    config.live_accepted_by = None
    config.live_credential_id = None
    config.version += 1
    db.flush()
    _audit(db, site, user_id, "live-credentials-store", "recorded", credential.id)
    return credential


def decrypted(db, site: Site, credential: SiteStripeLiveCredential | None = None) -> tuple[str, str]:
    credential = credential or credential_for(db, site)
    if not credential or credential.site_id != site.id or credential.tenant_id != site.tenant_id:
        raise CommerceError("Live Stripe credentials are not configured for this site.")
    return (
        decrypt_secret(credential.secret_key_ciphertext, aad=_aad(site.id, credential.id, "secret-key")),
        decrypt_secret(credential.webhook_secret_ciphertext, aad=_aad(site.id, credential.id, "webhook-secret")),
    )


def mark_verified(db, site_id: str, user_id: str, credential_id: str) -> SiteStripeLiveCredential:
    site = require_operator(db, site_id, user_id)
    credential = credential_for(db, site)
    if not credential or credential.id != credential_id:
        raise CommerceError("Live credentials changed. Reload before verification.")
    credential.verified_at = datetime.now(UTC)
    credential.verified_by = user_id
    db.flush()
    _audit(db, site, user_id, "live-credentials-verify", "verified", credential.id)
    return credential


def accept(db, site_id: str, user_id: str, site_version: int, config_version: int,
           credential_id: str, reason: str) -> None:
    reason = str(reason or "").strip()
    if not 10 <= len(reason) <= 500:
        raise CommerceError("Explain the live-payment acceptance in 10–500 characters.")
    site = require_operator(db, site_id, user_id)
    result = db.execute(update(Site).where(
        Site.id == site.id,
        Site.tenant_id == site.tenant_id,
        Site.version == site_version,
    ).values(version=Site.version).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise CommerceError("Site settings changed. Reload before accepting live payments.")
    db.refresh(site)
    config = db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    ).with_for_update().execution_options(populate_existing=True))
    credential = credential_for(db, site)
    if not config or config.version != config_version:
        raise CommerceError("Commerce settings changed. Reload before accepting live payments.")
    if config.mode != "sandbox":
        raise CommerceError("Sandbox commerce must be enabled before live acceptance.")
    if not credential or credential.id != credential_id or not credential.verified_at:
        raise CommerceError("Verify the current live credentials before acceptance.")
    report = site_golive.assess(db, site, commerce_requested=True)
    if not report.passed or site.status != "published" or not site.hostname:
        raise CommerceError("Live acceptance is blocked until every publication and commerce check passes.")
    before = _state(config)
    config.mode = "live"
    config.live_accepted_at = datetime.now(UTC)
    config.live_accepted_by = user_id
    config.live_credential_id = credential.id
    config.version += 1
    site.version += 1
    db.flush()
    _audit(db, site, user_id, "live-acceptance", "approved", credential.id,
           reason=reason, before=before, report=report.as_dict())


def disable(db, site_id: str, user_id: str, site_version: int, config_version: int,
            reason: str) -> None:
    reason = str(reason or "").strip()
    if not 10 <= len(reason) <= 500:
        raise CommerceError("Explain why live payments are being disabled in 10–500 characters.")
    site = require_operator(db, site_id, user_id)
    if site.version != site_version:
        raise CommerceError("Site settings changed. Reload before disabling live payments.")
    config = db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    ).with_for_update().execution_options(populate_existing=True))
    if not config or config.version != config_version:
        raise CommerceError("Commerce settings changed. Reload before disabling live payments.")
    if config.mode != "live":
        raise CommerceError("Live payments are not active.")
    before = _state(config)
    credential_id = config.live_credential_id or ""
    credential = credential_for(db, site)
    if credential and credential.id == credential_id:
        credential.verified_at = None
        credential.verified_by = None
    config.mode = "sandbox"
    config.live_accepted_at = None
    config.live_accepted_by = None
    config.live_credential_id = None
    config.version += 1
    site.version += 1
    db.flush()
    _audit(db, site, user_id, "live-disable", "approved", credential_id,
           reason=reason, before=before)


def effective_mode(db, site: Site, config: SiteCommerceSettings | None = None) -> str:
    config = config or db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    ))
    if not config:
        raise CommerceError("Commerce is not enabled for this store.")
    if config.mode == "sandbox":
        return "sandbox"
    if config.mode == "live" and site.status == "published" and bool(site.hostname):
        credential = credential_for(db, site)
        if (state_for(db, site, config).accepted and credential):
            decrypted(db, site, credential)
            return "live"
    raise CommerceError("Commerce is not enabled for this store.")


def live_secrets(db, site: Site, config: SiteCommerceSettings | None = None) -> tuple[str, str]:
    config = config or db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    ))
    if effective_mode(db, site, config) != "live":
        raise CommerceError("Live payments have not been accepted for this site.")
    credential = credential_for(db, site)
    if not credential or credential.id != config.live_credential_id:
        raise CommerceError("Accepted live credentials are unavailable.")
    return decrypted(db, site, credential)


def _state(config: SiteCommerceSettings) -> dict:
    return {
        "mode": config.mode,
        "version": config.version,
        "credential_id": config.live_credential_id,
        "accepted_by": config.live_accepted_by,
        "accepted_at": config.live_accepted_at.isoformat() if config.live_accepted_at else None,
    }


def _audit(db, site: Site, user_id: str, action: str, decision: str,
           credential_id: str, *, reason: str = "Operator-managed credential action.",
           before: dict | None = None, report: dict | None = None) -> SiteChangeSet:
    event = {
        "schema": 1,
        "action": action,
        "decision": decision,
        "reason": reason,
        "actor_id": user_id,
        "credential_id": credential_id,
        "checks": (report or {}).get("checks", []),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    config = db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id,
        SiteCommerceSettings.tenant_id == site.tenant_id,
    ))
    after = _state(config) if config else {"mode": "disabled", "version": 0}
    change = SiteChangeSet(
        tenant_id=site.tenant_id,
        site_id=site.id,
        user_id=user_id,
        source="live-acceptance",
        summary=f"{action}: {decision}"[:400],
        before_json={"version": (before or after).get("version", 0), "state": before or after, "audit": event},
        after_json={"version": after.get("version", 0), "state": after, "audit": event},
    )
    db.add(change)
    db.flush()
    return change

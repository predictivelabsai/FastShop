"""Encrypted, site-scoped PayPal and transactional email connections."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email.utils import formataddr, parseaddr

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.live_credentials import decrypt_secret, encrypt_secret
from app.models import Site, SiteIntegrationCredential, new_id
from app.services import CommerceError

PAYPAL_SANDBOX = "https://api-m.sandbox.paypal.com"
PAYPAL_LIVE = "https://api-m.paypal.com"
POSTMARK_API = "https://api.postmarkapp.com"


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


def mask(value: str) -> str:
    value = str(value or "")
    return "••••" + value[-4:] if value else ""


def paypal_token(client_id: str, secret: str, mode: str, *, client=None) -> bool:
    http = client or httpx
    host = PAYPAL_LIVE if mode == "live" else PAYPAL_SANDBOX
    response = http.post(host + "/v1/oauth2/token", auth=(client_id, secret),
                         data={"grant_type": "client_credentials"}, timeout=15)
    response.raise_for_status()
    return bool(response.json().get("access_token"))


def verify_paypal(client_id: str, secret: str, mode: str, *, client=None) -> bool:
    try:
        return paypal_token(client_id, secret, mode, client=client)
    except (httpx.HTTPError, ValueError):
        return False


def verify_postmark(token: str, sender: str, *, account_token="", client=None) -> tuple[bool, str]:
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}", sender.strip()):
        return False, "unverified"
    http = client or httpx
    headers = {"X-Postmark-Server-Token": token}
    try:
        server = http.get(POSTMARK_API + "/server", headers=headers, timeout=15)
        server.raise_for_status()
        if not account_token:
            return True, "not checked · account token required"
        account_headers = {"X-Postmark-Account-Token": account_token}
        signatures = http.get(POSTMARK_API + "/senders", headers=account_headers,
                              params={"count": 500, "offset": 0}, timeout=15)
        signatures.raise_for_status()
        domains = http.get(POSTMARK_API + "/domains", headers=account_headers,
                           params={"count": 500, "offset": 0}, timeout=15)
        domains.raise_for_status()
        address = sender.strip().lower()
        confirmed = False
        for item in signatures.json().get("SenderSignatures", []):
            candidate = str(item.get("EmailAddress", "")).lower()
            if candidate == address and item.get("Confirmed"):
                confirmed = True
        sender_domain = address.rpartition("@")[2]
        domain_confirmed = any(
            str(item.get("Name", "")).lower() == sender_domain and item.get("DKIMVerified")
            for item in domains.json().get("Domains", [])
        )
        if domain_confirmed:
            confirmed = True
        return True, "confirmed" if confirmed else "unconfirmed"
    except (httpx.HTTPError, ValueError):
        return False, "unverified"


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


def mail_settings(site: Site, db=None) -> dict:
    connected = payload_for(site, "email", db)
    if connected:
        return {key: connected.get(key, "") for key in ("token", "sender", "sender_name")}
    import os

    settings = site.published_settings_json or site.settings_json or {}
    store_name = str(settings.get("name") or site.name).strip()
    store_name = re.sub(r'["\r\n<>]', "", store_name)
    platform_sender = os.getenv("FASTSHOP_CONTACT_FROM") or os.getenv("FROM_EMAIL", "")
    _, platform_address = parseaddr(platform_sender)
    return {
        "token": os.getenv("POSTMARK_SERVER_TOKEN") or os.getenv("POSTMARK_API_TOKEN", ""),
        "sender": platform_address or platform_sender,
        "sender_name": store_name,
        "reply_to": str(settings.get("email") or "").strip(),
        "account_token": "",
    }


def send_test_email(site: Site, recipient: str, db, *, client=None) -> bool:
    config = payload_for(site, "email", db)
    if not config:
        return False
    http = client or httpx
    response = http.post(POSTMARK_API + "/email", headers={
        "X-Postmark-Server-Token": config["token"]}, json={
            "From": _from_address(config), "To": recipient,
            "Subject": f"Test email from {site.name}",
            "TextBody": f"This is a test email from {site.name}.",
            "MessageStream": "outbound",
        }, timeout=15)
    response.raise_for_status()
    return response.json().get("ErrorCode") == 0


def _from_address(config: dict) -> str:
    name = str(config.get("sender_name", "")).strip()
    sender = str(config.get("sender", "")).strip()
    return formataddr((name, sender)) if name else sender

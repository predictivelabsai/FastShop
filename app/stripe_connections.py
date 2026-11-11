"""Merchant-managed Stripe connection persistence and lifecycle."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select

from app import live_credentials
from app.integrations.stripe_commerce import StripeGateway
from app.models import Site, SiteCommerceSettings, SiteStripeConnection, SiteStripeLiveCredential
from app.services import CommerceError


def connection_for(db, site: Site, mode: str) -> SiteStripeConnection | None:
    return db.scalar(select(SiteStripeConnection).where(
        SiteStripeConnection.tenant_id == site.tenant_id,
        SiteStripeConnection.site_id == site.id,
        SiteStripeConnection.mode == mode,
    ))


def decrypted(db, site: Site, connection: SiteStripeConnection) -> tuple[str, str, str]:
    prefix = f"fastshop:{site.id}:{connection.id}:"
    return (
        live_credentials.decrypt_secret(connection.secret_key_ciphertext, aad=prefix + "secret-key"),
        live_credentials.decrypt_secret(connection.webhook_secret_ciphertext, aad=prefix + "webhook-secret"),
        live_credentials.decrypt_secret(connection.publishable_key_ciphertext, aad=prefix + "publishable-key"),
    )


def _validate(mode: str, secret: str, publishable: str) -> None:
    expected = "live" if mode == "live" else "test"
    if mode not in {"test", "live"}:
        raise CommerceError("Choose test or live mode.")
    if not re.fullmatch(rf"(?:sk|rk)_{expected}_[A-Za-z0-9_]{{8,}}", secret):
        raise CommerceError(f"Enter a valid Stripe {expected} secret or restricted key.")
    if not re.fullmatch(rf"pk_{expected}_[A-Za-z0-9_]{{8,}}", publishable):
        raise CommerceError(f"Enter a valid Stripe {expected} publishable key.")


def _live_mirror(db, site: Site, user_id: str, connection: SiteStripeConnection,
                 secret: str, webhook: str, publishable: str) -> None:
    credential = db.scalar(select(SiteStripeLiveCredential).where(
        SiteStripeLiveCredential.tenant_id == site.tenant_id,
        SiteStripeLiveCredential.site_id == site.id,
    ))
    credential_id = credential.id if credential else connection.id
    aad = f"fastshop:{site.id}:{credential_id}:"
    if credential is None:
        credential = SiteStripeLiveCredential(
            id=credential_id, tenant_id=site.tenant_id, site_id=site.id,
            secret_key_ciphertext=live_credentials.encrypt_secret(secret, aad=aad + "secret-key"),
            webhook_secret_ciphertext=live_credentials.encrypt_secret(webhook, aad=aad + "webhook-secret"),
            publishable_key=None, key_prefix=secret[:7], key_last_four=secret[-4:],
            created_by=user_id,
        )
        db.add(credential)
    else:
        credential.secret_key_ciphertext = live_credentials.encrypt_secret(secret, aad=aad + "secret-key")
        credential.webhook_secret_ciphertext = live_credentials.encrypt_secret(webhook, aad=aad + "webhook-secret")
        credential.publishable_key = None
        credential.key_prefix = secret[:7]
        credential.key_last_four = secret[-4:]
        credential.created_by = user_id
        credential.created_at = datetime.now(UTC)
        credential.verified_at = None
        credential.verified_by = None
    config = db.scalar(select(SiteCommerceSettings).where(
        SiteCommerceSettings.site_id == site.id, SiteCommerceSettings.tenant_id == site.tenant_id,
    ))
    if config:
        config.live_accepted_at = None
        config.live_accepted_by = None
        config.live_credential_id = None
        config.version += 1


def save(db, site: Site, user_id: str, mode: str, secret: str, publishable: str,
         webhook_secret: str, *, webhook_url: str = "", transport=None) -> SiteStripeConnection:
    secret, publishable, webhook_secret = (str(value or "").strip()
                                           for value in (secret, publishable, webhook_secret))
    _validate(mode, secret, publishable)
    gateway = StripeGateway.for_connection(secret, transport=transport)
    status = gateway.verify_connection()
    if status["mode"] != mode:
        raise CommerceError("The Stripe key does not match the selected mode.")
    endpoint_id = None
    if webhook_url:
        endpoint_id, webhook_secret = gateway.create_webhook_endpoint(webhook_url)
    elif not re.fullmatch(r"whsec_[A-Za-z0-9_]{8,}", webhook_secret):
        raise CommerceError("Enter a Stripe webhook signing secret when no HTTPS endpoint can be created.")

    existing = connection_for(db, site, mode)
    old_endpoint = existing.webhook_endpoint_id if existing else None
    old_secret = decrypted(db, site, existing)[0] if existing else ""
    connection_id = existing.id if existing else None
    if not connection_id:
        from app.models import new_id
        connection_id = new_id()
    aad = f"fastshop:{site.id}:{connection_id}:"
    encrypted = (
        live_credentials.encrypt_secret(secret, aad=aad + "secret-key"),
        live_credentials.encrypt_secret(publishable, aad=aad + "publishable-key"),
        live_credentials.encrypt_secret(webhook_secret, aad=aad + "webhook-secret"),
    )
    if existing:
        connection = existing
        connection.secret_key_ciphertext, connection.publishable_key_ciphertext, connection.webhook_secret_ciphertext = encrypted
    else:
        connection = SiteStripeConnection(
            id=connection_id, tenant_id=site.tenant_id, site_id=site.id, mode=mode,
            secret_key_ciphertext=encrypted[0], publishable_key_ciphertext=encrypted[1],
            webhook_secret_ciphertext=encrypted[2], created_by=user_id,
        )
        db.add(connection)
    connection.mode = mode
    connection.webhook_endpoint_id = endpoint_id
    connection.key_last_four, connection.publishable_last_four = secret[-4:], publishable[-4:]
    connection.account_id = status["account_id"]
    connection.account_name = str(status["account_name"] or "")[:200]
    connection.account_country = str(status["country"] or "")[:2]
    connection.verified_at = datetime.now(UTC)
    if mode == "live":
        _live_mirror(db, site, user_id, connection, secret, webhook_secret, publishable)
    db.flush()
    if old_endpoint and old_endpoint != endpoint_id:
        try:
            StripeGateway.for_connection(old_secret, transport=transport).delete_webhook_endpoint(old_endpoint)
        except CommerceError:
            pass
    return connection


def test_connection(db, site: Site, mode: str, *, transport=None) -> dict:
    connection = connection_for(db, site, mode)
    if not connection:
        raise CommerceError("Connect a Stripe account in this mode first.")
    secret, _, webhook = decrypted(db, site, connection)
    gateway = StripeGateway.for_connection(secret, webhook, transport=transport)
    result = gateway.connection_status()
    result.update({"account_id": connection.account_id, "account_name": connection.account_name,
        "country": connection.account_country, "verified_at": connection.verified_at,
        "endpoint_id": connection.webhook_endpoint_id})
    return result


def disconnect(db, site: Site, mode: str, *, transport=None) -> None:
    connection = connection_for(db, site, mode)
    if not connection:
        return
    if mode == "live":
        config = db.scalar(select(SiteCommerceSettings).where(
            SiteCommerceSettings.site_id == site.id,
            SiteCommerceSettings.tenant_id == site.tenant_id,
        ))
        if config and config.mode == "live":
            raise CommerceError("Disable live payments before disconnecting the accepted account.")
    secret, _, _ = decrypted(db, site, connection)
    if connection.webhook_endpoint_id:
        try:
            StripeGateway.for_connection(secret, transport=transport).delete_webhook_endpoint(
                connection.webhook_endpoint_id
            )
        except CommerceError:
            pass
    db.delete(connection)
    if mode == "live":
        credential = db.scalar(select(SiteStripeLiveCredential).where(
            SiteStripeLiveCredential.site_id == site.id,
            SiteStripeLiveCredential.tenant_id == site.tenant_id,
        ))
        if credential:
            db.delete(credential)

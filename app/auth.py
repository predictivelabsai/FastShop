"""Local development login and Google authorization-code OIDC helpers."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from collections import deque
from urllib.parse import urlencode

import httpx

from app.config import settings


def google_enabled() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorize_url(state: str, challenge: str) -> str:
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def exchange_code(code: str, verifier: str) -> dict[str, str] | None:
    try:
        with httpx.Client(timeout=15) as client:
            token_response = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.google_redirect_uri,
                    "code_verifier": verifier,
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token", "")
            identity_response = client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            identity_response.raise_for_status()
            identity = identity_response.json()
    except (httpx.HTTPError, ValueError):
        return None
    email = str(identity.get("email", "")).strip().lower()
    if not email or not identity.get("email_verified"):
        return None
    domain = email.rsplit("@", 1)[-1]
    if settings.google_allowed_domains and domain not in settings.google_allowed_domains:
        return None
    if settings.google_allowed_emails and email not in settings.google_allowed_emails:
        return None
    return {"email": email, "name": str(identity.get("name") or email.split("@", 1)[0])}


def hash_admin_password(password: str) -> str:
    if not 24 <= len(password) <= 1024:
        raise ValueError("Use an admin password between 24 and 1024 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return "pbkdf2_sha256:600000:" + salt.hex() + ":" + digest.hex()


def verify_admin_password(password: str, encoded: str) -> bool:
    if not 24 <= len(password) <= 1024:
        return False
    try:
        scheme, rounds, salt, expected = encoded.split(":")
        if scheme != "pbkdf2_sha256" or rounds != "600000" or len(salt) != 32 or len(expected) != 64:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
        return secrets.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def local_login_allowed() -> bool:
    if settings.is_production:
        return bool(settings.allow_password_login and settings.admin_password_hash
                    and settings.admin_email and not settings.admin_email.endswith(".example"))
    return bool(settings.admin_email and settings.admin_password)


_password_attempts = deque()
_password_lock = threading.Lock()


def password_attempt_allowed() -> bool:
    """Bound expensive password checks globally per worker; no spoofable IP headers."""
    if not settings.is_production:
        return True
    now = time.monotonic()
    with _password_lock:
        while _password_attempts and _password_attempts[0] <= now - 60:
            _password_attempts.popleft()
        if len(_password_attempts) >= 10:
            return False
        _password_attempts.append(now)
        return True


def valid_local_credentials(email: str, password: str) -> bool:
    return (
        local_login_allowed()
        and secrets.compare_digest(email.strip().lower().encode(), settings.admin_email.encode())
        and (verify_admin_password(password, settings.admin_password_hash) if settings.is_production
             else secrets.compare_digest(password.encode(), settings.admin_password.encode()))
    )

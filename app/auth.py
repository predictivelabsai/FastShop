"""Local development login and Google authorization-code OIDC helpers."""

from __future__ import annotations

import base64
import hashlib
import secrets
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


def local_login_allowed() -> bool:
    return not settings.is_production or bool(
        settings.admin_email and settings.admin_password and "example" not in settings.admin_email
    )


def valid_local_credentials(email: str, password: str) -> bool:
    return (
        local_login_allowed()
        and secrets.compare_digest(email.strip().lower(), settings.admin_email)
        and secrets.compare_digest(password, settings.admin_password)
    )

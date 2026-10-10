"""Transactional public-account signup, provisioning, and email verification."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update

from app import content, onboarding
from app.auth import hash_admin_password
from app.config import settings
from app.models import (
    CommerceMail,
    Membership,
    SignupAttempt,
    SignupEmailVerification,
    Site,
    User,
)
from app.services import CommerceError

SIGNUP_NAME_MAX_LENGTH = 160
SIGNUP_EMAIL_MAX_LENGTH = 320
SIGNUP_PASSWORD_MIN_LENGTH = 12
SIGNUP_PASSWORD_MAX_LENGTH = 1024
SIGNUP_SLUG_MAX_LENGTH = 61
SIGNUP_RATE_WINDOW_MINUTES = 15
SIGNUP_FAILURE_LIMIT = 5
SIGNUP_RESEND_WINDOW_MINUTES = 15
SIGNUP_RESEND_LIMIT = 3
SIGNUP_ATTEMPT_RETENTION_HOURS = 24
SIGNUP_VERIFICATION_TTL_HOURS = 24
SIGNUP_SLUG_CANDIDATE_LIMIT = 10_000

_EMAIL_PATTERN = re.compile(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+")


class SignupUnavailable(CommerceError):
    """Public-safe signup refusal; callers must not expose its internal cause."""


@dataclass(frozen=True)
class SignupResult:
    user: User
    site: Site
    message_id: str | None


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def normalized_name(value: str) -> str:
    name = " ".join(str(value).strip().split())
    if not name or len(name) > SIGNUP_NAME_MAX_LENGTH:
        raise CommerceError(
            f"Enter a name between 1 and {SIGNUP_NAME_MAX_LENGTH} characters."
        )
    return name


def normalized_email(value: str) -> str:
    email = str(value).strip().lower()
    if len(email) > SIGNUP_EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.fullmatch(email):
        raise CommerceError("Enter a valid email address.")
    return email


def validate_password(password: str, confirmation: str) -> str:
    password = str(password)
    if not SIGNUP_PASSWORD_MIN_LENGTH <= len(password) <= SIGNUP_PASSWORD_MAX_LENGTH:
        raise CommerceError(
            f"Use between {SIGNUP_PASSWORD_MIN_LENGTH} and "
            f"{SIGNUP_PASSWORD_MAX_LENGTH} characters."
        )
    if not secrets.compare_digest(password, str(confirmation)):
        raise CommerceError("The password confirmation does not match.")
    return password


def _identifier(kind: str, value: str) -> str:
    bounded = str(value).strip().lower()[:SIGNUP_EMAIL_MAX_LENGTH]
    return hmac.new(
        settings.session_secret.encode(),
        f"fastshop-signup-rate-v1:{kind}:{bounded}".encode(),
        hashlib.sha256,
    ).hexdigest()


def client_hash(client_address: str) -> str:
    return _identifier("client", str(client_address)[:128])


def email_hash(email: str) -> str:
    return _identifier("email", email)


def prune_attempts(db, *, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    db.execute(
        delete(SignupAttempt).where(
            SignupAttempt.created_at
            < now - timedelta(hours=SIGNUP_ATTEMPT_RETENTION_HOURS)
        )
    )


def rate_limited(db, scope: str, email: str, client_address: str) -> bool:
    if scope not in {"signup", "resend"}:
        raise ValueError("Unknown signup rate-limit scope.")
    now = datetime.now(UTC)
    prune_attempts(db, now=now)
    window_minutes = (
        SIGNUP_RATE_WINDOW_MINUTES
        if scope == "signup"
        else SIGNUP_RESEND_WINDOW_MINUTES
    )
    limit = SIGNUP_FAILURE_LIMIT if scope == "signup" else SIGNUP_RESEND_LIMIT
    conditions = [
        SignupAttempt.scope == scope,
        SignupAttempt.created_at >= now - timedelta(minutes=window_minutes),
    ]
    if scope == "signup":
        conditions.append(SignupAttempt.accepted.is_(False))
    client_count = db.scalar(
        select(func.count()).select_from(SignupAttempt).where(
            *conditions,
            SignupAttempt.client_hash == client_hash(client_address),
        )
    )
    address_count = db.scalar(
        select(func.count()).select_from(SignupAttempt).where(
            *conditions,
            SignupAttempt.email_hash == email_hash(email),
        )
    )
    return int(client_count or 0) >= limit or int(address_count or 0) >= limit


def record_attempt(
    db,
    scope: str,
    email: str,
    client_address: str,
    *,
    accepted: bool,
) -> SignupAttempt:
    row = SignupAttempt(
        scope=scope,
        client_hash=client_hash(client_address),
        email_hash=email_hash(email),
        accepted=accepted,
    )
    db.add(row)
    db.flush()
    return row


def site_slug_base(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        slug = "store"
    if not slug[0].isalpha():
        slug = "store-" + slug
    slug = slug[:SIGNUP_SLUG_MAX_LENGTH].rstrip("-")
    return slug if len(slug) >= 3 else (slug + "-store")[:SIGNUP_SLUG_MAX_LENGTH]


def unique_site_slug(db, name: str, *, start: int = 1) -> str:
    base = site_slug_base(name)
    for number in range(max(1, start), SIGNUP_SLUG_CANDIDATE_LIMIT + 1):
        suffix = "" if number == 1 else f"-{number}"
        candidate = base[: SIGNUP_SLUG_MAX_LENGTH - len(suffix)].rstrip("-") + suffix
        if not db.scalar(select(Site.id).where(Site.slug == candidate)):
            return candidate
    raise SignupUnavailable("No site address is available.")


def verification_token(verification: SignupEmailVerification) -> str:
    message = (
        "fastshop-signup-verification-v1:"
        f"{verification.tenant_id}:{verification.site_id}:"
        f"{verification.user_id}:{verification.id}"
    )
    return hmac.new(
        settings.session_secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def verification_url(verification: SignupEmailVerification) -> str:
    return settings.public_url + f"/signup/verify/{verification.tenant_id}/{verification.id}"


def queue_verification(db, site: Site, user: User) -> CommerceMail:
    membership = db.scalar(
        select(Membership.id).where(
            Membership.tenant_id == site.tenant_id,
            Membership.user_id == user.id,
            Membership.role == "admin",
        )
    )
    if not membership:
        raise CommerceError("Account does not own this workspace.")
    now = datetime.now(UTC)
    db.execute(
        update(SignupEmailVerification)
        .where(
            SignupEmailVerification.tenant_id == site.tenant_id,
            SignupEmailVerification.site_id == site.id,
            SignupEmailVerification.user_id == user.id,
            SignupEmailVerification.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    verification = SignupEmailVerification(
        tenant_id=site.tenant_id,
        site_id=site.id,
        user_id=user.id,
        token_hash="",
        expires_at=now + timedelta(hours=SIGNUP_VERIFICATION_TTL_HOURS),
    )
    db.add(verification)
    db.flush()
    verification.token_hash = hashlib.sha256(
        verification_token(verification).encode()
    ).hexdigest()
    message = CommerceMail(
        tenant_id=site.tenant_id,
        site_id=site.id,
        customer_id=None,
        user_id=user.id,
        kind="account_verification",
        dedupe_key="account-verification:" + verification.id,
        available_at=now,
        reference_json={"verification_id": verification.id},
    )
    db.add(message)
    db.flush()
    return message


def provision_password_signup(
    db,
    name: str,
    email: str,
    password: str,
    client_address: str,
    *,
    slug_start: int = 1,
) -> SignupResult:
    if db.scalar(select(User.id).where(User.email == email)):
        raise SignupUnavailable("Account already exists.")
    user = User(email=email, name=name, password_hash=hash_admin_password(password))
    db.add(user)
    db.flush()
    site = content.create_site(
        db,
        user.id,
        name,
        unique_site_slug(db, name, start=slug_start),
    )
    onboarding.create_state(db, site, user.id)
    message = queue_verification(db, site, user)
    record_attempt(db, "signup", email, client_address, accepted=True)
    return SignupResult(user=user, site=site, message_id=message.id)


def provision_google_signup(
    db,
    name: str,
    email: str,
    client_address: str,
    *,
    slug_start: int = 1,
) -> SignupResult:
    if db.scalar(select(User.id).where(User.email == email)):
        raise SignupUnavailable("Account already exists.")
    user = User(
        email=email,
        name=name,
        password_hash=None,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    db.flush()
    site = content.create_site(
        db,
        user.id,
        name,
        unique_site_slug(db, name, start=slug_start),
    )
    onboarding.create_state(db, site, user.id)
    record_attempt(db, "signup", email, client_address, accepted=True)
    return SignupResult(user=user, site=site, message_id=None)


def first_site_for_user(db, user_id: str) -> Site | None:
    return db.scalar(
        select(Site)
        .join(Membership, Membership.tenant_id == Site.tenant_id)
        .where(
            Membership.user_id == user_id,
            Membership.role.in_(["admin", "merchant", "editor"]),
            Site.tenant_id == Membership.tenant_id,
        )
        .order_by(Site.created_at, Site.id)
    )


def consume_verification(
    db, tenant_id: str, verification_id: str, supplied_token: str
) -> User:
    verification = db.scalar(
        select(SignupEmailVerification).where(
            SignupEmailVerification.id == str(verification_id)[:32],
            SignupEmailVerification.tenant_id == str(tenant_id)[:32],
        )
    )
    now = datetime.now(UTC)
    supplied_hash = hashlib.sha256(str(supplied_token)[:64].encode()).hexdigest()
    if (
        not verification
        or verification.consumed_at
        or utc(verification.expires_at) <= now
        or not hmac.compare_digest(verification.token_hash, supplied_hash)
    ):
        raise CommerceError("This verification link is invalid, expired or already used.")
    user = db.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.id == verification.user_id,
            Membership.tenant_id == verification.tenant_id,
            Membership.role.in_(["admin", "merchant"]),
        )
    )
    site_exists = db.scalar(
        select(Site.id).where(
            Site.id == verification.site_id,
            Site.tenant_id == verification.tenant_id,
        )
    )
    if not user or not site_exists:
        raise CommerceError("This verification link is invalid, expired or already used.")
    claimed = db.execute(
        update(SignupEmailVerification)
        .where(
            SignupEmailVerification.id == verification.id,
            SignupEmailVerification.tenant_id == verification.tenant_id,
            SignupEmailVerification.site_id == verification.site_id,
            SignupEmailVerification.user_id == user.id,
            SignupEmailVerification.consumed_at.is_(None),
            SignupEmailVerification.expires_at > now,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session="fetch")
    )
    if claimed.rowcount != 1:
        raise CommerceError("This verification link is invalid, expired or already used.")
    user.email_verified_at = user.email_verified_at or now
    db.flush()
    return user

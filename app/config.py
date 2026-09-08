"""Typed environment configuration for FastShop."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("FASTSHOP_ENV", "development")
    host: str = os.getenv("FASTSHOP_HOST", "0.0.0.0")
    port: int = int(os.getenv("FASTSHOP_PORT", "5025"))
    public_url: str = os.getenv("FASTSHOP_PUBLIC_URL", "http://localhost:5025").rstrip("/")
    session_secret: str = os.getenv("FASTSHOP_SESSION_SECRET") or secrets.token_hex(32)
    encryption_key: str = os.getenv("FASTSHOP_ENCRYPTION_KEY", "")
    data_dir: Path = Path(os.getenv("FASTSHOP_DATA_DIR", "data"))
    db_url: str = os.getenv("DB_URL", "")
    db_schema: str = os.getenv("DB_SCHEMA", "fast_shop")
    auto_create_schema: bool = _bool("FASTSHOP_AUTO_CREATE_SCHEMA", True)
    admin_email: str = os.getenv("FASTSHOP_ADMIN_EMAIL", "admin@fastshop.example").lower()
    admin_password: str = os.getenv("FASTSHOP_ADMIN_PASSWORD") or (
        "FastShop2026$"
        if os.getenv("FASTSHOP_ENV", "development").lower() != "production"
        else ""
    )
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_redirect_uri: str = os.getenv(
        "GOOGLE_REDIRECT_URI", "http://localhost:5025/auth/google/callback"
    )
    google_allowed_domains: tuple[str, ...] = tuple(
        value.strip().lower()
        for value in os.getenv("GOOGLE_ALLOWED_DOMAINS", "").split(",")
        if value.strip()
    )
    google_allowed_emails: tuple[str, ...] = tuple(
        value.strip().lower()
        for value in os.getenv("GOOGLE_ALLOWED_EMAILS", "").split(",")
        if value.strip()
    )
    model_provider: str = os.getenv("MODEL_PROVIDER", "xai")
    model_name: str = os.getenv("MODEL_NAME", "grok-4-1-fast-reasoning")
    xai_api_key: str = os.getenv("XAI_API_KEY", "")
    xai_base_url: str = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
    api_token: str = os.getenv("FASTSME_API_TOKEN", "")
    fasterp_base_url: str = os.getenv("FASTERP_BASE_URL", "https://erp.fastsme.com").rstrip("/")
    fasterp_api_token: str = os.getenv("FASTERP_API_TOKEN") or os.getenv(
        "FASTSHOP_CONNECTOR_TOKEN", ""
    )
    fasterp_company_id: str = os.getenv("FASTERP_COMPANY_ID", "")
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def database_url(self) -> str:
        if self.db_url:
            if self.db_url.startswith("postgres://"):
                return "postgresql+psycopg://" + self.db_url.removeprefix("postgres://")
            if self.db_url.startswith("postgresql://"):
                return "postgresql+psycopg://" + self.db_url.removeprefix("postgresql://")
            return self.db_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'fastshop.sqlite3').resolve()}"


settings = Settings()

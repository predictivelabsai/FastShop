"""Database engine, schema isolation, and session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base

IS_POSTGRES = settings.database_url.startswith("postgresql+")
if IS_POSTGRES and not settings.db_schema.replace("_", "").isalnum():
    raise RuntimeError("DB_SCHEMA must contain only letters, numbers, and underscores")

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=(
        {"options": f"-csearch_path={settings.db_schema}"}
        if IS_POSTGRES
        else {"check_same_thread": False}
    ),
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def prepare_schema() -> None:
    if IS_POSTGRES:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"'))
    if settings.auto_create_schema or not IS_POSTGRES:
        Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def health() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": "postgresql" if IS_POSTGRES else "sqlite",
        "schema": settings.db_schema if IS_POSTGRES else "main",
    }

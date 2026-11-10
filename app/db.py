"""Database engine, schema isolation, and session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base, CommerceMail

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


def _remember_queued_mail(session: Session, _flush_context) -> None:
    queued = session.info.setdefault("commerce_mail_after_commit", set())
    # after_flush still lists pre-flush "new" rows, and their generated ids now exist.
    queued.update(row.id for row in session.new if isinstance(row, CommerceMail) and row.id)


def _dispatch_committed_mail(session: Session) -> None:
    message_ids = session.info.pop("commerce_mail_after_commit", set())
    if not message_ids:
        return
    from app.integrations.commerce_email import dispatch_mail
    for message_id in message_ids:
        try:
            dispatch_mail(message_id)
        except Exception:  # noqa: BLE001 - the scheduled worker retries committed rows
            import logging
            logging.getLogger("fastshop.mail").exception("immediate mail dispatch failed")


def _forget_rolled_back_mail(session: Session) -> None:
    session.info.pop("commerce_mail_after_commit", None)


event.listen(SessionLocal, "after_flush", _remember_queued_mail)
event.listen(SessionLocal, "after_commit", _dispatch_committed_mail)
event.listen(SessionLocal, "after_rollback", _forget_rolled_back_mail)


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


# Register transactional, append-only admin audit collection.
from app import admin_activity  # noqa: E402,F401

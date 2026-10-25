"""Personal merchant views and deliberately small, append-only activity records."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, new_id


class SavedAdminView(TimestampMixin, Base):
    __tablename__ = "saved_admin_views"
    __table_args__ = (UniqueConstraint("site_id", "user_id", "resource", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    resource: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(80))
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    sort: Mapped[str] = mapped_column(String(40), default="")
    columns_json: Mapped[list] = mapped_column(JSON, default=list)


class AdminTablePreference(TimestampMixin, Base):
    __tablename__ = "admin_table_preferences"
    __table_args__ = (UniqueConstraint("site_id", "user_id", "resource"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    resource: Mapped[str] = mapped_column(String(20))
    columns_json: Mapped[list] = mapped_column(JSON, default=list)


class AdminActivity(Base):
    __tablename__ = "admin_activities"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    object_type: Mapped[str] = mapped_column(String(60), index=True)
    object_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    summary: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

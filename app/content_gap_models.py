"""Tenant and site scoped persistence for custom data and storefront preferences."""
from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, new_id


class CustomDataDefinition(TimestampMixin, Base):
    __tablename__ = "custom_data_definitions"
    __table_args__ = (UniqueConstraint("site_id", "owner_type", "namespace", "key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    owner_type: Mapped[str] = mapped_column(String(24))
    namespace: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    value_type: Mapped[str] = mapped_column(String(32))
    validation_json: Mapped[dict] = mapped_column(JSON, default=dict)
    pinned_order: Mapped[int] = mapped_column(Integer, default=0)


class CustomDataValue(TimestampMixin, Base):
    __tablename__ = "custom_data_values"
    __table_args__ = (UniqueConstraint("definition_id", "owner_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    definition_id: Mapped[str] = mapped_column(
        ForeignKey("custom_data_definitions.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    value_json: Mapped[object] = mapped_column(JSON)


class SitePreferences(TimestampMixin, Base):
    __tablename__ = "site_preferences"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), unique=True, index=True)
    homepage_title: Mapped[str] = mapped_column(String(240), default="")
    meta_description: Mapped[str] = mapped_column(String(500), default="")
    social_image_id: Mapped[str | None] = mapped_column(
        ForeignKey("site_media.id", ondelete="SET NULL"), nullable=True)
    favicon_id: Mapped[str | None] = mapped_column(
        ForeignKey("site_media.id", ondelete="SET NULL"), nullable=True)
    password_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str] = mapped_column(Text, default="")
    analytics_id: Mapped[str] = mapped_column(String(40), default="")
    meta_pixel_id: Mapped[str] = mapped_column(String(40), default="")


class UrlRedirect(TimestampMixin, Base):
    __tablename__ = "url_redirects"
    __table_args__ = (UniqueConstraint("site_id", "source"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(240))
    target: Mapped[str] = mapped_column(String(2048))

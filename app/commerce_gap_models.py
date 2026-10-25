"""Persistence for sandbox gift tenders and durable merchant order edits."""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, new_id


class OrderEdit(TimestampMixin, Base):
    __tablename__ = 'order_edits'
    __table_args__ = (UniqueConstraint('site_id', 'request_key'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey('sites.id'), index=True)
    site_order_id: Mapped[str] = mapped_column(ForeignKey('site_orders.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(500))
    before_minor: Mapped[int] = mapped_column(Integer)
    after_minor: Mapped[int] = mapped_column(Integer)
    difference_minor: Mapped[int] = mapped_column(Integer)
    settlement: Mapped[str] = mapped_column(String(24), default='pending')
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)


class GiftCard(TimestampMixin, Base):
    __tablename__ = 'gift_cards'
    __table_args__ = (UniqueConstraint('site_id', 'code'),
                      CheckConstraint('balance_minor >= 0'),
                      CheckConstraint('initial_minor > 0'))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey('sites.id'), index=True)
    code: Mapped[str] = mapped_column(String(64))
    initial_minor: Mapped[int] = mapped_column(Integer)
    balance_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default='USD')
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey('shop_customers.id'), nullable=True)
    note: Mapped[str] = mapped_column(Text, default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class GiftCardEntry(TimestampMixin, Base):
    __tablename__ = 'gift_card_entries'
    __table_args__ = (UniqueConstraint('site_id', 'request_key'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey('sites.id'), index=True)
    gift_card_id: Mapped[str] = mapped_column(ForeignKey('gift_cards.id'), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey('orders.id'), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    request_key: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(24))
    amount_minor: Mapped[int] = mapped_column(Integer)
    balance_minor: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(500), default='')

"""Commerce persistence model with Saleor-informed domain boundaries."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="active")


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="customer")


class Channel(TimestampMixin, Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    country_code: Mapped[str] = mapped_column(String(2), default="EE")
    locale: Mapped[str] = mapped_column(String(10), default="en")
    prices_include_tax: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Category(TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    slug: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ProductType(TimestampMixin, Base):
    __tablename__ = "product_types"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(160))
    is_shipping_required: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug"),
        Index("ix_products_tenant_published", "tenant_id", "is_published"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    product_type_id: Mapped[str] = mapped_column(ForeignKey("product_types.id"), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"), index=True)
    slug: Mapped[str] = mapped_column(String(180))
    name: Mapped[str] = mapped_column(String(220))
    subtitle: Mapped[str] = mapped_column(String(260), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    seo_title: Mapped[str] = mapped_column(String(240), default="")
    seo_description: Mapped[str] = mapped_column(String(320), default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    variants: Mapped[list[ProductVariant]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ProductVariant.sort_order"
    )
    category: Mapped[Category] = relationship()


class Attribute(TimestampMixin, Base):
    __tablename__ = "attributes"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(140))
    input_type: Mapped[str] = mapped_column(String(30), default="dropdown")


class AttributeValue(TimestampMixin, Base):
    __tablename__ = "attribute_values"
    __table_args__ = (UniqueConstraint("attribute_id", "slug"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    attribute_id: Mapped[str] = mapped_column(ForeignKey("attributes.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(140))
    value: Mapped[str] = mapped_column(String(200))


class ProductVariant(TimestampMixin, Base):
    __tablename__ = "product_variants"
    __table_args__ = (UniqueConstraint("tenant_id", "sku"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(180))
    attributes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    weight_grams: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    product: Mapped[Product] = relationship(back_populates="variants")


class VariantChannelListing(TimestampMixin, Base):
    __tablename__ = "variant_channel_listings"
    __table_args__ = (
        UniqueConstraint("variant_id", "channel_id"),
        CheckConstraint("price_minor >= 0", name="ck_variant_price_nonnegative"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    variant_id: Mapped[str] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    price_minor: Mapped[int] = mapped_column(Integer)
    compare_at_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_minor: Mapped[int] = mapped_column(Integer, default=0)


class Warehouse(TimestampMixin, Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(140))
    country_code: Mapped[str] = mapped_column(String(2), default="EE")


class Stock(TimestampMixin, Base):
    __tablename__ = "stocks"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "variant_id"),
        CheckConstraint("quantity >= 0", name="ck_stock_quantity_nonnegative"),
        CheckConstraint("allocated >= 0", name="ck_stock_allocated_nonnegative"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[str] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    allocated: Mapped[int] = mapped_column(Integer, default=0)


class Cart(TimestampMixin, Base):
    __tablename__ = "carts"
    __table_args__ = (UniqueConstraint("tenant_id", "token"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    token: Mapped[str] = mapped_column(String(64), default=new_id)
    email: Mapped[str] = mapped_column(String(320), default="")
    status: Mapped[str] = mapped_column(String(30), default="open")
    voucher_code: Mapped[str] = mapped_column(String(80), default="")
    lines: Mapped[list[CartLine]] = relationship(back_populates="cart", cascade="all, delete-orphan")


class CartLine(TimestampMixin, Base):
    __tablename__ = "cart_lines"
    __table_args__ = (UniqueConstraint("cart_id", "variant_id"), CheckConstraint("quantity > 0"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    cart_id: Mapped[str] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[str] = mapped_column(ForeignKey("product_variants.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    cart: Mapped[Cart] = relationship(back_populates="lines")
    variant: Mapped[ProductVariant] = relationship()


class Voucher(TimestampMixin, Base):
    __tablename__ = "vouchers"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(30), default="percentage")
    value: Mapped[int] = mapped_column(Integer, default=0)
    minimum_minor: Mapped[int] = mapped_column(Integer, default=0)
    usage_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        UniqueConstraint("tenant_id", "idempotency_key"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(40))
    token: Mapped[str] = mapped_column(String(64), default=new_id, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(30), default="unfulfilled")
    payment_status: Mapped[str] = mapped_column(String(30), default="pending")
    currency: Mapped[str] = mapped_column(String(3))
    subtotal_minor: Mapped[int] = mapped_column(Integer)
    discount_minor: Mapped[int] = mapped_column(Integer, default=0)
    shipping_minor: Mapped[int] = mapped_column(Integer, default=0)
    tax_minor: Mapped[int] = mapped_column(Integer, default=0)
    total_minor: Mapped[int] = mapped_column(Integer)
    shipping_address_json: Mapped[dict] = mapped_column(JSON, default=dict)
    fasterp_order_id: Mapped[str] = mapped_column(String(100), default="")
    lines: Mapped[list[OrderLine]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderLine(TimestampMixin, Base):
    __tablename__ = "order_lines"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[str | None] = mapped_column(ForeignKey("product_variants.id"), nullable=True)
    sku: Mapped[str] = mapped_column(String(100))
    product_name: Mapped[str] = mapped_column(String(220))
    variant_name: Mapped[str] = mapped_column(String(180))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_minor: Mapped[int] = mapped_column(Integer)
    total_minor: Mapped[int] = mapped_column(Integer)
    order: Mapped[Order] = relationship(back_populates="lines")


class PaymentTransaction(TimestampMixin, Base):
    __tablename__ = "payment_transactions"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="demo")
    external_id: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(30), default="charge")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(Integer)


class Review(TimestampMixin, Base):
    __tablename__ = "reviews"
    __table_args__ = (CheckConstraint("rating >= 1 AND rating <= 5"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    author_name: Mapped[str] = mapped_column(String(140))
    rating: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(180))
    body: Mapped[str] = mapped_column(Text, default="")
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)


class WishlistItem(TimestampMixin, Base):
    __tablename__ = "wishlist_items"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "product_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)


class OutboxEvent(TimestampMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_status_created", "status", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class ExternalMapping(TimestampMixin, Base):
    __tablename__ = "external_mappings"
    __table_args__ = (UniqueConstraint("tenant_id", "system", "resource_type", "local_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    system: Mapped[str] = mapped_column(String(50))
    resource_type: Mapped[str] = mapped_column(String(80))
    local_id: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(140))
    version: Mapped[str] = mapped_column(String(80), default="")


class ChatThread(TimestampMixin, Base):
    __tablename__ = "chat_threads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(180), default="New conversation")
    surface: Mapped[str] = mapped_column(String(30), default="shopper")


class ChatMessage(TimestampMixin, Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    route_context: Mapped[str] = mapped_column(String(240), default="")


class Site(TimestampMixin, Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    hostname: Mapped[str | None] = mapped_column(String(253), unique=True, nullable=True)
    theme: Mapped[str] = mapped_column(String(80), default="editorial-commerce")
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    published_settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)


class SitePage(TimestampMixin, Base):
    __tablename__ = "site_pages"
    __table_args__ = (UniqueConstraint("site_id", "path"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(240))
    title: Mapped[str] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(40), default="content")
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    draft_json: Mapped[dict] = mapped_column(JSON, default=dict)
    published_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class SiteRevision(TimestampMixin, Base):
    __tablename__ = "site_revisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("site_pages.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    content_json: Mapped[dict] = mapped_column(JSON)
    action: Mapped[str] = mapped_column(String(40), default="draft")


class SiteBuilderTurn(TimestampMixin, Base):
    __tablename__ = "site_builder_turns"
    __table_args__ = (UniqueConstraint("site_id", "command_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    command_id: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    provider: Mapped[str] = mapped_column(String(24), default="guided")
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    response_json: Mapped[dict] = mapped_column(JSON, default=dict)


class SiteChangeSet(TimestampMixin, Base):
    __tablename__ = "site_change_sets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(24))
    summary: Mapped[str] = mapped_column(String(400))
    before_json: Mapped[dict] = mapped_column(JSON)
    after_json: Mapped[dict] = mapped_column(JSON)


class DemoWorkspace(TimestampMixin, Base):
    __tablename__ = "demo_workspaces"
    __table_args__ = (UniqueConstraint("site_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DemoCommand(TimestampMixin, Base):
    __tablename__ = "demo_commands"
    __table_args__ = (UniqueConstraint("workspace_id", "command_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("demo_workspaces.id"), index=True)
    command_id: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)


class SiteMedia(TimestampMixin, Base):
    __tablename__ = "site_media"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    alt: Mapped[str] = mapped_column(String(400))
    content_type: Mapped[str] = mapped_column(String(80))
    storage_key: Mapped[str] = mapped_column(String(240))
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class SiteContact(TimestampMixin, Base):
    __tablename__ = "site_contacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    client_hash: Mapped[str] = mapped_column(String(64), default="", index=True)


class SiteCommerceSettings(TimestampMixin, Base):
    __tablename__ = "site_commerce_settings"
    __table_args__ = (
        UniqueConstraint("site_id"),
        CheckConstraint("shipping_minor IS NULL OR shipping_minor >= 0"),
        CheckConstraint("free_shipping_threshold_minor >= 0"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="disabled")
    origin_json: Mapped[dict] = mapped_column(JSON, default=lambda: {"country": "EE"})
    shipping_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    free_shipping_threshold_minor: Mapped[int] = mapped_column(Integer, default=7500)
    allowed_states_json: Mapped[list] = mapped_column(JSON, default=list)
    product_tax_codes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    subscription_product_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    tax_registration_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CommerceQuote(TimestampMixin, Base):
    __tablename__ = "commerce_quotes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    provider_id: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    subtotal_minor: Mapped[int] = mapped_column(Integer)
    discount_minor: Mapped[int] = mapped_column(Integer)
    shipping_minor: Mapped[int] = mapped_column(Integer)
    tax_minor: Mapped[int] = mapped_column(Integer)
    total_minor: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot_json: Mapped[dict] = mapped_column(JSON)


class ShopCustomer(TimestampMixin, Base):
    __tablename__ = "shop_customers"
    __table_args__ = (UniqueConstraint("site_id", "email"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(160), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stripe_customer_id: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerChallenge(TimestampMixin, Base):
    __tablename__ = "customer_challenges"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("shop_customers.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(40))
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_hash: Mapped[str] = mapped_column(String(64), index=True)
    consent_json: Mapped[dict] = mapped_column(JSON, default=dict)


class MarketingConsent(TimestampMixin, Base):
    __tablename__ = "marketing_consents"
    __table_args__ = (UniqueConstraint("site_id", "customer_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("shop_customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    consent_json: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerOffer(TimestampMixin, Base):
    __tablename__ = "customer_offers"
    __table_args__ = (UniqueConstraint("site_id", "customer_id"), UniqueConstraint("site_id", "code"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("shop_customers.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    percent: Mapped[int] = mapped_column(Integer, default=10)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True)


class CommerceMail(TimestampMixin, Base):
    __tablename__ = "commerce_mail"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("shop_customers.id"), index=True)
    challenge_id: Mapped[str | None] = mapped_column(ForeignKey("customer_challenges.id"), nullable=True)
    reference_json: Mapped[dict] = mapped_column(JSON, default=dict)
    kind: Mapped[str] = mapped_column(String(40))
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider_id: Mapped[str] = mapped_column(String(100), default="")


class SiteOrder(TimestampMixin, Base):
    __tablename__ = "site_orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("shop_customers.id"), index=True)
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("commerce_quotes.id"), nullable=True)
    stripe_checkout_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)


class ShipmentEvent(TimestampMixin, Base):
    __tablename__ = "shipment_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    site_order_id: Mapped[str] = mapped_column(ForeignKey("site_orders.id"), index=True)
    carrier: Mapped[str] = mapped_column(String(100), default="")
    tracking_number: Mapped[str] = mapped_column(String(100), default="")
    tracking_url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(500), default="")
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class CheckoutAttempt(TimestampMixin, Base):
    __tablename__ = "checkout_attempts"
    __table_args__ = (UniqueConstraint("site_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("shop_customers.id"), index=True)
    quote_id: Mapped[str] = mapped_column(ForeignKey("commerce_quotes.id"), unique=True)
    offer_id: Mapped[str | None] = mapped_column(ForeignKey("customer_offers.id"), nullable=True)
    request_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    provider_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="prepared", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stripe_session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True)


class InventoryReservation(TimestampMixin, Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (
        UniqueConstraint("attempt_id", "stock_id"),
        CheckConstraint("quantity > 0", name="ck_reservation_quantity_positive"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("checkout_attempts.id"), index=True)
    stock_id: Mapped[str] = mapped_column(ForeignKey("stocks.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24), default="held")


class SiteCart(TimestampMixin, Base):
    __tablename__ = "site_carts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    lines_json: Mapped[list] = mapped_column(JSON, default=list)
    discount_code: Mapped[str] = mapped_column(String(40), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    request_key: Mapped[str] = mapped_column(String(64), default=new_id)
    checkout_id: Mapped[str | None] = mapped_column(ForeignKey("checkout_attempts.id"), nullable=True)


class SubscriptionContract(TimestampMixin, Base):
    __tablename__ = "subscription_contracts"
    __table_args__ = (CheckConstraint("interval_months >= 1 AND interval_months <= 12"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("shop_customers.id"), index=True)
    initial_attempt_id: Mapped[str] = mapped_column(ForeignKey("checkout_attempts.id"), unique=True)
    stripe_customer_id: Mapped[str] = mapped_column(String(100))
    payment_method_id: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    interval_months: Mapped[int] = mapped_column(Integer, default=1)
    anchor_day: Mapped[int] = mapped_column(Integer)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lines_json: Mapped[list] = mapped_column(JSON)
    destination_json: Mapped[dict] = mapped_column(JSON)
    recipient_name: Mapped[str] = mapped_column(String(160))
    consent_json: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)


class SubscriptionCycle(TimestampMixin, Base):
    __tablename__ = "subscription_cycles"
    __table_args__ = (UniqueConstraint("contract_id", "due_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("subscription_contracts.id"), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(24), default="prepared", index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("checkout_attempts.id"), nullable=True, unique=True)
    payment_intent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    tax_transaction_id: Mapped[str] = mapped_column(String(100), default="")


class SubscriptionRecovery(TimestampMixin, Base):
    __tablename__ = "subscription_recoveries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("subscription_cycles.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("checkout_attempts.id"), unique=True)


class SubscriptionEvent(TimestampMixin, Base):
    __tablename__ = "subscription_events"
    __table_args__ = (UniqueConstraint("contract_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("subscription_contracts.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("shop_customers.id"))
    request_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(30))
    details_json: Mapped[dict] = mapped_column(JSON)


class SubscriptionPaymentSetup(TimestampMixin, Base):
    __tablename__ = "subscription_payment_setups"
    __table_args__ = (UniqueConstraint("contract_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("subscription_contracts.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("shop_customers.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default="pending")
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    command_json: Mapped[dict] = mapped_column(JSON)

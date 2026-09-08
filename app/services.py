"""Transactional commerce use cases shared by HTML, API, jobs, and AI."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Cart,
    CartLine,
    Category,
    Channel,
    Order,
    OrderLine,
    OutboxEvent,
    PaymentTransaction,
    Product,
    ProductVariant,
    Stock,
    Tenant,
    User,
    VariantChannelListing,
    Voucher,
    WishlistItem,
)
from app.seed import CHANNEL_SLUG, TENANT_SLUG


class CommerceError(ValueError):
    """A user-correctable commerce rule violation."""


@dataclass(frozen=True)
class ProductCard:
    product: Product
    price_minor: int
    compare_at_minor: int | None
    currency: str
    available: int


@dataclass(frozen=True)
class CartSummary:
    cart: Cart
    lines: list[dict]
    currency: str
    subtotal_minor: int
    discount_minor: int
    shipping_minor: int
    tax_minor: int
    total_minor: int


def tenant_channel(session: Session) -> tuple[Tenant, Channel]:
    tenant = session.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
    if not tenant:
        raise RuntimeError("FastShop is not seeded")
    channel = session.scalar(
        select(Channel).where(Channel.tenant_id == tenant.id, Channel.slug == CHANNEL_SLUG)
    )
    if not channel:
        raise RuntimeError("FastShop channel is missing")
    return tenant, channel


def money(minor: int, currency: str = "EUR") -> str:
    symbols = {"EUR": "€", "GBP": "£", "USD": "$"}
    return f"{symbols.get(currency, currency + ' ')}{minor / 100:,.2f}"


def products(
    session: Session,
    *,
    query: str = "",
    category: str = "",
    featured: bool | None = None,
) -> list[ProductCard]:
    tenant, channel = tenant_channel(session)
    statement = (
        select(Product)
        .options(selectinload(Product.variants))
        .join(Category, Category.id == Product.category_id)
        .where(Product.tenant_id == tenant.id, Product.is_published.is_(True))
        .order_by(Product.is_featured.desc(), Product.created_at, Product.name)
    )
    if query:
        term = f"%{query.strip()}%"
        statement = statement.where(
            or_(Product.name.ilike(term), Product.subtitle.ilike(term), Product.description.ilike(term))
        )
    if category:
        statement = statement.where(Category.slug == category)
    if featured is not None:
        statement = statement.where(Product.is_featured.is_(featured))
    result: list[ProductCard] = []
    for product in session.scalars(statement).unique():
        first_variant = next((variant for variant in product.variants if variant.is_active), None)
        if not first_variant:
            continue
        listing = session.scalar(
            select(VariantChannelListing).where(
                VariantChannelListing.variant_id == first_variant.id,
                VariantChannelListing.channel_id == channel.id,
            )
        )
        if not listing:
            continue
        available = session.scalar(
            select(func.coalesce(func.sum(Stock.quantity - Stock.allocated), 0)).where(
                Stock.variant_id == first_variant.id
            )
        )
        result.append(
            ProductCard(product, listing.price_minor, listing.compare_at_minor, listing.currency, available)
        )
    return result


def product_by_slug(session: Session, slug: str) -> Product | None:
    tenant, _ = tenant_channel(session)
    return session.scalar(
        select(Product)
        .options(selectinload(Product.variants))
        .where(Product.tenant_id == tenant.id, Product.slug == slug, Product.is_published.is_(True))
    )


def variant_price(session: Session, variant_id: str, channel_id: str) -> VariantChannelListing:
    listing = session.scalar(
        select(VariantChannelListing).where(
            VariantChannelListing.variant_id == variant_id,
            VariantChannelListing.channel_id == channel_id,
        )
    )
    if not listing:
        raise CommerceError("This variant is not for sale in the selected channel.")
    return listing


def available_stock(session: Session, variant_id: str) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(Stock.quantity - Stock.allocated), 0)).where(
                Stock.variant_id == variant_id
            )
        )
        or 0
    )


def get_or_create_cart(
    session: Session, token: str | None, user_id: str | None = None
) -> Cart:
    tenant, channel = tenant_channel(session)
    cart = None
    if token:
        cart = session.scalar(
            select(Cart)
            .options(selectinload(Cart.lines).selectinload(CartLine.variant))
            .where(Cart.tenant_id == tenant.id, Cart.token == token, Cart.status == "open")
        )
    if cart:
        if user_id and not cart.user_id:
            cart.user_id = user_id
        return cart
    cart = Cart(
        tenant_id=tenant.id,
        channel_id=channel.id,
        user_id=user_id,
        token=secrets.token_urlsafe(24),
    )
    session.add(cart)
    session.flush()
    return cart


def add_to_cart(session: Session, cart: Cart, variant_id: str, quantity: int = 1) -> Cart:
    if quantity < 1 or quantity > 25:
        raise CommerceError("Choose a quantity between 1 and 25.")
    variant = session.get(ProductVariant, variant_id)
    if not variant or not variant.is_active:
        raise CommerceError("That product option is unavailable.")
    current = session.scalar(
        select(CartLine).where(CartLine.cart_id == cart.id, CartLine.variant_id == variant.id)
    )
    target = quantity + (current.quantity if current else 0)
    if target > available_stock(session, variant.id):
        raise CommerceError("There is not enough stock for that quantity.")
    if current:
        current.quantity = target
    else:
        session.add(CartLine(cart_id=cart.id, variant_id=variant.id, quantity=quantity))
    session.flush()
    session.refresh(cart)
    return cart


def update_cart_line(session: Session, cart: Cart, line_id: str, quantity: int) -> None:
    line = session.scalar(select(CartLine).where(CartLine.id == line_id, CartLine.cart_id == cart.id))
    if not line:
        raise CommerceError("Cart line not found.")
    if quantity <= 0:
        session.delete(line)
    elif quantity <= available_stock(session, line.variant_id):
        line.quantity = min(quantity, 25)
    else:
        raise CommerceError("There is not enough stock for that quantity.")


def cart_summary(session: Session, cart: Cart) -> CartSummary:
    _, channel = tenant_channel(session)
    cart = session.scalar(
        select(Cart)
        .options(selectinload(Cart.lines).selectinload(CartLine.variant).selectinload(ProductVariant.product))
        .where(Cart.id == cart.id)
    )
    lines = []
    subtotal = 0
    for line in cart.lines:
        listing = variant_price(session, line.variant_id, cart.channel_id)
        line_total = listing.price_minor * line.quantity
        subtotal += line_total
        lines.append(
            {
                "line": line,
                "product": line.variant.product,
                "variant": line.variant,
                "unit_price_minor": listing.price_minor,
                "total_minor": line_total,
            }
        )
    discount = 0
    if cart.voucher_code:
        voucher = session.scalar(
            select(Voucher).where(
                Voucher.tenant_id == cart.tenant_id,
                func.upper(Voucher.code) == cart.voucher_code.upper(),
                Voucher.is_active.is_(True),
            )
        )
        if voucher and subtotal >= voucher.minimum_minor:
            if voucher.usage_limit is None or voucher.used_count < voucher.usage_limit:
                discount = (
                    subtotal * voucher.value // 100
                    if voucher.kind == "percentage"
                    else min(subtotal, voucher.value)
                )
    discounted = subtotal - discount
    shipping = 0 if discounted == 0 or discounted >= 7500 else 690
    # Seeded EU channel prices include VAT. This is the included tax component.
    tax = round(discounted * 22 / 122) if channel.prices_include_tax else round(discounted * 22 / 100)
    total = discounted + shipping + (0 if channel.prices_include_tax else tax)
    return CartSummary(cart, lines, channel.currency, subtotal, discount, shipping, tax, total)


def apply_voucher(session: Session, cart: Cart, code: str) -> None:
    code = code.strip().upper()
    voucher = session.scalar(
        select(Voucher).where(
            Voucher.tenant_id == cart.tenant_id,
            func.upper(Voucher.code) == code,
            Voucher.is_active.is_(True),
        )
    )
    if not voucher:
        raise CommerceError("That voucher code is not valid.")
    cart.voucher_code = code


def complete_checkout(
    session: Session,
    cart: Cart,
    *,
    email: str,
    full_name: str,
    address_line: str,
    city: str,
    postcode: str,
    country: str,
    idempotency_key: str,
) -> Order:
    existing = session.scalar(
        select(Order).where(Order.tenant_id == cart.tenant_id, Order.idempotency_key == idempotency_key)
    )
    if existing:
        return existing
    summary = cart_summary(session, cart)
    if not summary.lines:
        raise CommerceError("Your cart is empty.")
    if "@" not in email or not full_name.strip() or not address_line.strip() or not city.strip():
        raise CommerceError("Complete your contact and delivery details.")

    stock_rows: list[Stock] = []
    for item in sorted(summary.lines, key=lambda row: row["variant"].id):
        stock = session.scalar(
            select(Stock)
            .where(Stock.variant_id == item["variant"].id)
            .order_by(Stock.id)
            .with_for_update()
        )
        if not stock or stock.quantity - stock.allocated < item["line"].quantity:
            raise CommerceError(f"{item['product'].name} no longer has enough stock.")
        stock_rows.append(stock)

    count = session.scalar(select(func.count(Order.id)).where(Order.tenant_id == cart.tenant_id)) or 0
    order = Order(
        tenant_id=cart.tenant_id,
        channel_id=cart.channel_id,
        user_id=cart.user_id,
        number=f"FS-{datetime.now(UTC):%Y%m}-{count + 1001}",
        idempotency_key=idempotency_key,
        email=email.strip().lower(),
        currency=summary.currency,
        subtotal_minor=summary.subtotal_minor,
        discount_minor=summary.discount_minor,
        shipping_minor=summary.shipping_minor,
        tax_minor=summary.tax_minor,
        total_minor=summary.total_minor,
        shipping_address_json={
            "name": full_name.strip(),
            "line1": address_line.strip(),
            "city": city.strip(),
            "postcode": postcode.strip(),
            "country": country.strip().upper(),
        },
    )
    session.add(order)
    session.flush()
    for item, stock in zip(sorted(summary.lines, key=lambda row: row["variant"].id), stock_rows, strict=True):
        line = item["line"]
        stock.allocated += line.quantity
        session.add(
            OrderLine(
                order_id=order.id,
                variant_id=item["variant"].id,
                sku=item["variant"].sku,
                product_name=item["product"].name,
                variant_name=item["variant"].name,
                quantity=line.quantity,
                unit_price_minor=item["unit_price_minor"],
                total_minor=item["total_minor"],
            )
        )
    external_id = "demo_" + hashlib.sha256(order.id.encode()).hexdigest()[:18]
    session.add(
        PaymentTransaction(
            order_id=order.id,
            provider="demo",
            external_id=external_id,
            status="succeeded",
            currency=order.currency,
            amount_minor=order.total_minor,
        )
    )
    order.payment_status = "paid"
    cart.status = "completed"
    if cart.voucher_code:
        voucher = session.scalar(
            select(Voucher).where(
                Voucher.tenant_id == cart.tenant_id,
                func.upper(Voucher.code) == cart.voucher_code.upper(),
            )
        )
        if voucher:
            voucher.used_count += 1
    session.add(
        OutboxEvent(
            tenant_id=cart.tenant_id,
            topic="order.confirmed",
            aggregate_id=order.id,
            payload_json={
                "order_id": order.id,
                "number": order.number,
                "email": order.email,
                "currency": order.currency,
                "total_minor": order.total_minor,
            },
        )
    )
    session.flush()
    return order


def find_or_create_user(session: Session, email: str, name: str = "") -> User:
    clean = email.strip().lower()
    user = session.scalar(select(User).where(User.email == clean))
    if user:
        if name and not user.name:
            user.name = name
        return user
    user = User(email=clean, name=name or clean.split("@", 1)[0].title())
    session.add(user)
    session.flush()
    return user


def toggle_wishlist(session: Session, user_id: str, product_id: str) -> bool:
    tenant, _ = tenant_channel(session)
    item = session.scalar(
        select(WishlistItem).where(
            WishlistItem.tenant_id == tenant.id,
            WishlistItem.user_id == user_id,
            WishlistItem.product_id == product_id,
        )
    )
    if item:
        session.delete(item)
        return False
    session.add(WishlistItem(tenant_id=tenant.id, user_id=user_id, product_id=product_id))
    return True


def catalog_snapshot(session: Session) -> dict:
    cards = products(session)
    return {
        "product_count": len(cards),
        "featured_count": sum(card.product.is_featured for card in cards),
        "low_stock": [card.product.name for card in cards if card.available < 8],
        "price_range": (
            min((card.price_minor for card in cards), default=0),
            max((card.price_minor for card in cards), default=0),
        ),
    }


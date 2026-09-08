from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Base, Order, Stock
from app.seed import seed
from app.services import (
    add_to_cart,
    apply_voucher,
    cart_summary,
    complete_checkout,
    get_or_create_cart,
    money,
    products,
)


def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    seed(session)
    session.commit()
    return session


def test_money_uses_integer_minor_units():
    assert money(12900, "EUR") == "€129.00"
    assert money(199, "GBP") == "£1.99"


def test_voucher_shipping_and_included_vat_are_server_calculated():
    session = database()
    card = products(session)[0]
    cart = get_or_create_cart(session, None)
    add_to_cart(session, cart, card.product.variants[0].id)
    apply_voucher(session, cart, "welcome10")
    summary = cart_summary(session, cart)
    assert summary.discount_minor == summary.subtotal_minor // 10
    assert summary.total_minor == (
        summary.subtotal_minor - summary.discount_minor + summary.shipping_minor
    )
    assert summary.shipping_minor in {0, 690}
    assert 0 < summary.tax_minor < summary.total_minor


def test_checkout_is_idempotent_and_allocates_stock_once():
    session = database()
    variant = products(session)[0].product.variants[0]
    before = session.scalar(select(Stock.allocated).where(Stock.variant_id == variant.id))
    cart = get_or_create_cart(session, None)
    add_to_cart(session, cart, variant.id, 2)
    kwargs = {
        "email": "buyer@example.com",
        "full_name": "Buyer Example",
        "address_line": "1 Market Street",
        "city": "Tallinn",
        "postcode": "10111",
        "country": "EE",
        "idempotency_key": "test-checkout-1",
    }
    first = complete_checkout(session, cart, **kwargs)
    session.commit()
    second = complete_checkout(session, cart, **kwargs)
    after = session.scalar(select(Stock.allocated).where(Stock.variant_id == variant.id))
    assert first.id == second.id
    assert session.scalar(select(Order).where(Order.id == first.id)).payment_status == "paid"
    assert after == before + 2

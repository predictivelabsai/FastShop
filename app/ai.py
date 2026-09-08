"""Grounded shopper assistant and permission-aware merchant copilot."""

from __future__ import annotations

import json

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Order, OutboxEvent, Product, Stock
from app.services import catalog_snapshot, money, products

SHOPPER_PROMPT = """You are the FastShop shopping assistant for Northstar Goods.
Use only the supplied catalog facts. Help compare products, explain variants, delivery,
returns, and availability. You may suggest cart changes but never claim to have changed a cart,
placed an order, or processed payment. Keep answers concise and do not request payment details."""

MERCHANT_PROMPT = """You are the FastShop merchant copilot. Use only the supplied operational
facts. Explain catalog, stock, orders, promotions, and FastERP sync health. Never claim to have
performed a write; privileged actions require explicit confirmation in the normal UI."""


def _deterministic(session: Session, question: str, merchant: bool) -> str | None:
    command = question.strip().lower().split(maxsplit=1)[0]
    if command in {"/help", "help"}:
        return (
            "Try `/products`, `/stock`, `/orders`, or `/sync`. I can also compare products, "
            "explain delivery, and help you choose a variant."
        )
    if command == "/products":
        cards = products(session)
        return "Available now: " + "; ".join(
            f"{card.product.name} ({money(card.price_minor, card.currency)}, {card.available} in stock)"
            for card in cards
        )
    if command == "/stock":
        cards = products(session)
        low = [f"{card.product.name}: {card.available}" for card in cards if card.available < 15]
        return "Low-stock watch: " + (", ".join(low) if low else "no products are below 15 units.")
    if command == "/orders" and merchant:
        count = session.scalar(select(func.count(Order.id))) or 0
        paid = session.scalar(select(func.count(Order.id)).where(Order.payment_status == "paid")) or 0
        return f"There are {count} orders in FastShop; {paid} are paid."
    if command == "/sync" and merchant:
        pending = session.scalar(
            select(func.count(OutboxEvent.id)).where(OutboxEvent.status == "pending")
        ) or 0
        failed = session.scalar(
            select(func.count(OutboxEvent.id)).where(OutboxEvent.status == "failed")
        ) or 0
        return f"FastERP outbox: {pending} pending and {failed} failed events."
    lowered = question.lower()
    if "delivery" in lowered or "shipping" in lowered:
        return "Delivery is free above €75; otherwise it is €6.90. Orders ship from Tallinn with tracking."
    if "return" in lowered:
        return "Unused items can be returned within 30 days. Start from your order page after signing in."
    if "recommend" in lowered or "suggest" in lowered:
        cards = products(session, featured=True)
        return "A good place to start: " + ", ".join(card.product.name for card in cards[:3]) + "."
    return None


def answer(session: Session, question: str, route: str, merchant: bool = False) -> str:
    question = question.strip()[:1000]
    if not question:
        return "Ask me about products, variants, delivery, stock, orders, or FastERP sync."
    deterministic = _deterministic(session, question, merchant)
    if deterministic:
        return deterministic
    cards = products(session)
    facts = {
        "route": route[:240],
        "catalog": [
            {
                "name": card.product.name,
                "description": card.product.subtitle,
                "price": money(card.price_minor, card.currency),
                "available": card.available,
                "variants": [variant.name for variant in card.product.variants],
            }
            for card in cards
        ],
        "snapshot": catalog_snapshot(session),
    }
    if merchant:
        facts["orders"] = session.scalar(select(func.count(Order.id))) or 0
        facts["stock_units"] = session.scalar(select(func.sum(Stock.quantity - Stock.allocated))) or 0
        facts["products_total"] = session.scalar(select(func.count(Product.id))) or 0
    if not settings.xai_api_key:
        return (
            "I can answer deterministic questions without an AI key. Try `/products`, `/stock`, "
            "`/orders`, `/sync`, or ask about delivery and returns."
        )
    try:
        response = httpx.post(
            f"{settings.xai_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.xai_api_key}"},
            json={
                "model": settings.model_name,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": MERCHANT_PROMPT if merchant else SHOPPER_PROMPT},
                    {"role": "system", "content": "Grounding facts: " + json.dumps(facts)},
                    {"role": "user", "content": question},
                ],
            },
            timeout=30,
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return "The model is temporarily unavailable. Deterministic commands such as `/products` still work."


"""Storefront text placeholders that follow the store's saved savings.

Merchants write {subscription_saving} or {first_order_saving} anywhere in storefront text and
the page shows the percentage from Settings -> Commerce, so changing a saving never leaves old
wording behind. Saved text keeps the placeholder; only rendered pages show the number.
"""

from __future__ import annotations

from app import commerce

TOKENS = ("{subscription_saving}", "{first_order_saving}")


def values(db, site) -> dict[str, str]:
    config = commerce.settings_for(db, site)
    return {
        "{subscription_saving}": f"{commerce.subscription_percent(config)}%",
        "{first_order_saving}": f"{commerce.first_order_percent(config)}%",
    }


def fill(value, replacements: dict[str, str]):
    """Replace the placeholders in a string, or in every string inside dicts and lists."""
    if isinstance(value, str):
        if "{" not in value:
            return value
        for token, text in replacements.items():
            value = value.replace(token, text)
        return value
    if isinstance(value, dict):
        return {key: fill(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [fill(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(fill(item, replacements) for item in value)
    return value


def fill_for(db, site, value):
    return fill(value, values(db, site))

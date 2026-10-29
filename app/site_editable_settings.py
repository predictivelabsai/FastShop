"""Schema-driven, additive merchant settings shared by storefront theme packs."""
from __future__ import annotations

import copy

from app.services import CommerceError


def resolve_fields(values: dict, schema: list[dict]) -> dict:
    """Return declared setting values, using schema defaults only for absent keys."""
    if not isinstance(values, dict):
        values = {}
    result = {}
    for field in schema:
        key = field.get("id")
        default = field.get("default", "")
        value = values[key] if key in values else default
        if field.get("type", "text") not in {"text", "textarea", "url", "image", "email"}:
            raise CommerceError("Unsupported editable setting type.")
        if not isinstance(value, str) or len(value) > field.get("max_length", 30000):
            raise CommerceError("Editable settings must be bounded text values.")
        result[key] = value
    return result


def merge_fields(values: dict, submitted: dict, schema: list[dict]) -> dict:
    """Merge declared fields into JSON settings without dropping unrelated keys."""
    if not isinstance(submitted, dict):
        raise CommerceError("Send editable settings as an object.")
    allowed = {field["id"] for field in schema}
    if set(submitted) - allowed:
        raise CommerceError("Choose supported editable settings.")
    current = copy.deepcopy(values) if isinstance(values, dict) else {}
    current.update(resolve_fields(submitted, schema))
    return current

"""Pure block-diff derivation and application for chat refinement previews."""

from __future__ import annotations

import copy
import json
from uuid import uuid4

from app.services import CommerceError
from app.site_blocks import (
    TEXT_FIELDS,
    add_block,
    get_block,
    normalize_document,
    patch_block,
    remove_block,
    reorder_blocks,
)

BLOCK_INSERT_TYPES = {"hero", "text", "split", "products", "articles"}
BLOCK_FIELDS = TEXT_FIELDS | {
    "hidden",
    "subscription_preview",
    "items",
    "gallery",
    "role",
    "layout",
}
IMMEDIATE_OPERATIONS = {"brief", "navigation", "theme", "brand", "create_page"}
REFINEMENT_OPERATIONS = {
    "patch",
    "add",
    "remove",
    "reorder",
    "document",
    # Phase 0/1 provider compatibility. These are converted before storage.
    "section",
    "add_section",
    "remove_section",
}


def _page(snapshot: dict, page_id: str) -> dict:
    pages = snapshot.get("pages", {})
    if not isinstance(page_id, str) or page_id not in pages:
        raise CommerceError("Choose a page from this site.")
    return pages[page_id]


def _index(value, *, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise CommerceError("Choose a valid block position.")
    return value


def _apply_raw(document: dict, operation: dict, locale: str) -> dict:
    kind = operation.get("op")
    if kind in {"patch", "section"}:
        allowed = (
            {"op", "page_id", "block_id", "after", "before", "block_type", "op_id", "description"}
            if kind == "patch"
            else {"op", "page_id", "section_id", "values"}
        )
        if set(operation) - allowed:
            raise CommerceError("Invalid block patch.")
        block_id = operation.get("block_id") if kind == "patch" else operation.get("section_id")
        values = operation.get("after") if kind == "patch" else operation.get("values")
        if not isinstance(values, dict) or set(values) - BLOCK_FIELDS:
            raise CommerceError("Select an existing block and supported fields.")
        get_block(document, block_id)
        return patch_block(document, block_id, values, locale=locale)
    if kind in {"add", "add_section"}:
        allowed = (
            {"op", "page_id", "block", "index"}
            if kind == "add"
            else {"op", "page_id", "section"}
        )
        if set(operation) != allowed and not (kind == "add" and set(operation) == allowed - {"index"}):
            raise CommerceError("Invalid block addition.")
        block = operation.get("block") if kind == "add" else operation.get("section")
        if not isinstance(block, dict) or set(block) - (BLOCK_FIELDS | {"id", "type", "version"}):
            raise CommerceError("Choose a supported block.")
        if block.get("type") not in BLOCK_INSERT_TYPES:
            raise CommerceError("This block needs manual configuration.")
        result = add_block(document, block)
        target = operation.get("index", len(result["blocks"]) - 1)
        _index(target, maximum=len(result["blocks"]) - 1)
        added_id = result["blocks"][-1]["id"]
        ids = [item["id"] for item in result["blocks"] if item["id"] != added_id]
        ids.insert(target, added_id)
        return reorder_blocks(result, ids)
    if kind in {"remove", "remove_section"}:
        allowed = {"op", "page_id", "block_id"} if kind == "remove" else {"op", "page_id", "section_id"}
        if set(operation) != allowed:
            raise CommerceError("Invalid block removal.")
        block_id = operation.get("block_id") if kind == "remove" else operation.get("section_id")
        return remove_block(document, block_id)
    if kind == "reorder":
        allowed = {"op", "page_id", "ids"}
        if set(operation) != allowed:
            raise CommerceError("Invalid block reorder.")
        return reorder_blocks(document, operation["ids"])
    if kind == "document":
        if set(operation) != {"op", "page_id", "document"}:
            raise CommerceError("Invalid page document response.")
        return normalize_document(operation["document"])
    raise CommerceError("This action requires the normal merchant controls.")


def _display(value) -> str:
    if value is None:
        return "empty"
    if isinstance(value, dict):
        value = next(iter(value.values()), "")
    if isinstance(value, list):
        return f"{len(value)} entries"
    text = str(value).replace("\n", " ").strip()
    return f'“{text[:67]}…”' if len(text) > 68 else f'“{text}”'


def _description(kind: str, block_type: str, block_id: str, before=None, after=None) -> str:
    label = block_type.replace("_", " ").title()
    if kind == "patch":
        fields = list(after)
        if len(fields) == 1:
            field = fields[0].replace("_", " ")
            return f"Update {label} {field}: {_display(before[field])} → {_display(after[field])}"
        return f"Update {label}: " + ", ".join(field.replace("_", " ") for field in fields)
    if kind == "add":
        return f"Add {label} block"
    if kind == "remove":
        return f"Remove {label} block"
    return f"Reorder {len(after)} blocks"


def diff_documents(page_id: str, before_document: dict, after_document: dict, locale: str | None = None) -> list[dict]:
    """Return a minimal, independently decidable structural block diff."""
    before_doc = normalize_document(before_document)
    after_doc = normalize_document(after_document)
    for key in set(before_doc) | set(after_doc):
        if key not in {"blocks", "version"} and before_doc.get(key) != after_doc.get(key):
            raise CommerceError("Chat page-document edits may change blocks only.")
    before_blocks = {block["id"]: block for block in before_doc["blocks"]}
    after_blocks = {block["id"]: block for block in after_doc["blocks"]}
    before_ids = list(before_blocks)
    after_ids = list(after_blocks)
    common = [block_id for block_id in before_ids if block_id in after_blocks]
    if any(before_blocks[block_id]["type"] != after_blocks[block_id]["type"] for block_id in common):
        raise CommerceError("A block type cannot be changed; remove it and add a new block.")
    if locale:
        for block_id in common:
            old, new = before_blocks[block_id], after_blocks[block_id]
            changes = {field: copy.deepcopy(new.get(field)) for field in BLOCK_FIELDS if old.get(field) != new.get(field)}
            if changes:
                merged = patch_block({"version": 1, "blocks": [old]}, block_id, changes, locale=locale)["blocks"][0]
                after_blocks[block_id] = merged
        after_doc["blocks"] = [after_blocks[block["id"]] for block in after_doc["blocks"]]

    operations: list[dict] = []
    for index, block_id in reversed(list(enumerate(before_ids))):
        if block_id not in after_blocks:
            block = before_blocks[block_id]
            operations.append({
                "op_id": uuid4().hex,
                "op": "remove",
                "page_id": page_id,
                "block_id": block_id,
                "block_type": block["type"],
                "index": index,
                "before": copy.deepcopy(block),
                "after": None,
                "description": _description("remove", block["type"], block_id),
            })
    for block_id in common:
        before_block, after_block = before_blocks[block_id], after_blocks[block_id]
        before_values, after_values = {}, {}
        for field in sorted(BLOCK_FIELDS):
            old, new = before_block.get(field), after_block.get(field)
            if old != new:
                before_values[field] = copy.deepcopy(old)
                after_values[field] = copy.deepcopy(new)
        if before_block.get("version", 1) != after_block.get("version", 1):
            raise CommerceError("Chat cannot change block schema versions.")
        if after_values:
            operations.append({
                "op_id": uuid4().hex,
                "op": "patch",
                "page_id": page_id,
                "block_id": block_id,
                "block_type": before_block["type"],
                "before": before_values,
                "after": after_values,
                "description": _description("patch", before_block["type"], block_id, before_values, after_values),
            })
    for index, block_id in enumerate(after_ids):
        if block_id not in before_blocks:
            block = after_blocks[block_id]
            if block["type"] not in BLOCK_INSERT_TYPES:
                raise CommerceError("This block needs manual configuration.")
            operations.append({
                "op_id": uuid4().hex,
                "op": "add",
                "page_id": page_id,
                "block_id": block_id,
                "block_type": block["type"],
                "index": index,
                "before": None,
                "after": copy.deepcopy(block),
                "description": _description("add", block["type"], block_id),
            })
    before_common = [block_id for block_id in before_ids if block_id in after_blocks]
    after_common = [block_id for block_id in after_ids if block_id in before_blocks]
    if before_common != after_common:
        operations.append({
            "op_id": uuid4().hex,
            "op": "reorder",
            "page_id": page_id,
            "block_ids": after_common,
            "block_type": "multiple",
            "before": before_common,
            "after": after_common,
            "description": _description("reorder", "multiple", "", before_common, after_common),
        })
    if len(operations) > 12:
        raise CommerceError("Keep builder changes small and focused.")
    return operations


def derive_operations(snapshot: dict, operations: list[dict]) -> tuple[list[dict], list[dict]]:
    """Partition immediate commands and derive canonical block diffs from the rest."""
    if not isinstance(operations, list) or len(operations) > 12:
        raise CommerceError("Keep builder changes small and focused.")
    immediate: list[dict] = []
    desired: dict[str, dict] = {}
    page_order: list[str] = []
    locale = snapshot.get("settings", {}).get("default_locale", "en")
    for operation in operations:
        if not isinstance(operation, dict):
            raise CommerceError("Invalid builder operation.")
        kind = operation.get("op")
        if kind in IMMEDIATE_OPERATIONS:
            immediate.append(copy.deepcopy(operation))
            continue
        if kind not in REFINEMENT_OPERATIONS:
            raise CommerceError("This action requires the normal merchant controls.")
        page_id = operation.get("page_id")
        page = _page(snapshot, page_id)
        if page_id not in desired:
            desired[page_id] = normalize_document(page["document"])
            page_order.append(page_id)
        desired[page_id] = _apply_raw(desired[page_id], operation, locale)
    refinements = [
        item
        for page_id in page_order
        for item in diff_documents(page_id, _page(snapshot, page_id)["document"], desired[page_id], locale)
    ]
    if len(refinements) > 12 or len(json.dumps(refinements)) > 40000:
        raise CommerceError("Keep builder changes small and focused.")
    return immediate, refinements


def apply_operation(document: dict, operation: dict) -> dict:
    """Apply one trusted canonical preview op after verifying its before-state."""
    result = normalize_document(document)
    kind, block_id = operation["op"], operation.get("block_id")
    if kind == "patch":
        block = get_block(result, block_id)
        if block["type"] != operation["block_type"] or any(block.get(key) != value for key, value in operation["before"].items()):
            raise CommerceError("This block changed after the preview. Request the edit again.")
        return patch_block(result, block_id, operation["after"])
    if kind == "remove":
        if get_block(result, block_id) != operation["before"]:
            raise CommerceError("This block changed after the preview. Request the edit again.")
        return remove_block(result, block_id)
    if kind == "add":
        if block_id in {block["id"] for block in result["blocks"]}:
            raise CommerceError("This block was already added. Reload the preview.")
        result = add_block(result, operation["after"])
        ids = [block["id"] for block in result["blocks"] if block["id"] != block_id]
        ids.insert(min(operation["index"], len(ids)), block_id)
        return reorder_blocks(result, ids)
    if kind == "reorder":
        current = [block["id"] for block in result["blocks"]]
        expected = operation["before"]
        if [block_id for block_id in current if block_id in expected] != expected:
            raise CommerceError("These blocks changed order after the preview. Request the edit again.")
        ordered = iter(operation["after"])
        final = [next(ordered) if block_id in expected else block_id for block_id in current]
        return reorder_blocks(result, final)
    raise CommerceError("Invalid refinement operation.")

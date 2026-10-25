"""JSON storage adapter: normalize page documents without rewriting legacy rows."""
from sqlalchemy import JSON, TypeDecorator


class BlockDocumentJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        from app.site_blocks import normalize_document
        return normalize_document(value) if value is not None else None

    def process_result_value(self, value, dialect):
        from app.site_blocks import BLOCK_TYPES, normalize_document

        # Older deployed theme packs may contain section types absent from this
        # editor. Keep those persisted documents readable for listing, migration
        # and rollback. Rendering and all writes still use strict validation.
        legacy = value if isinstance(value, list) else (
            value.get("sections") if isinstance(value, dict) and "blocks" not in value else None
        )
        if isinstance(legacy, list) and any(
            isinstance(block, dict) and block.get("type") not in BLOCK_TYPES for block in legacy
        ):
            return value
        return normalize_document(value) if value is not None else None

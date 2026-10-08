"""JSON storage adapter: normalize page documents without rewriting legacy rows."""
from sqlalchemy import JSON, TypeDecorator


class BlockDocumentJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        from app.site_blocks import normalize_document
        return normalize_document(value) if value is not None else None

    def process_result_value(self, value, dialect):
        from app.site_blocks import normalize_document
        return normalize_document(value) if value is not None else None

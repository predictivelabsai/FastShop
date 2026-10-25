"""Isolated theme drafts and atomic, reversible promotion to the live site."""
import copy
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select, update

from app import content
from app.models import Site, SitePage, SiteRevision, new_id
from app.services import CommerceError
from app.theme_bulk_models import ThemeSnapshot


class ThemeConflict(CommerceError):
    status = 409

    def __init__(self, message, version):
        super().__init__(message)
        self.details = {"conflict": True, "version": version, "site_version": version}


def themes_for(db, site):
    return list(db.scalars(select(ThemeSnapshot).where(
        ThemeSnapshot.site_id == site.id, ThemeSnapshot.tenant_id == site.tenant_id)
        .order_by(ThemeSnapshot.created_at.desc(), ThemeSnapshot.id)))


def theme_for(db, site, theme_id):
    theme = db.scalar(select(ThemeSnapshot).where(
        ThemeSnapshot.id == theme_id, ThemeSnapshot.site_id == site.id,
        ThemeSnapshot.tenant_id == site.tenant_id).execution_options(populate_existing=True))
    if not theme:
        raise CommerceError("Theme not found.")
    return theme


def _name(value):
    value = str(value).strip()
    if not value or len(value) > 160:
        raise CommerceError("Give the theme a name of up to 160 characters.")
    return value


def snapshot(db, site, *, live=False):
    return {"theme": site.theme,
        "settings": copy.deepcopy(site.published_settings_json if live else site.settings_json),
        "pages": [{"id": p.id, "path": p.path, "title": p.title, "kind": p.kind,
            "product_id": p.product_id, "collection_id": p.collection_id,
            "document": copy.deepcopy(p.published_json if live else p.draft_json)}
            for p in content.site_pages(db, site)], "revisions": []}


def duplicate(db, site, user_id, name="Theme copy"):
    content.owned_site(db, site.id, user_id)
    row = ThemeSnapshot(tenant_id=site.tenant_id, site_id=site.id, name=_name(name),
                        snapshot_json=snapshot(db, site), created_at=datetime.now(UTC))
    db.add(row)
    db.flush()
    return row


def guard(db, theme, expected_version):
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise CommerceError("Supply the current theme version.")
    changed = db.execute(update(ThemeSnapshot).where(
        ThemeSnapshot.id == theme.id, ThemeSnapshot.site_id == theme.site_id,
        ThemeSnapshot.tenant_id == theme.tenant_id, ThemeSnapshot.version == expected_version)
        .values(version=ThemeSnapshot.version + 1).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.refresh(theme)
        raise ThemeConflict("This theme changed. Reload before saving your changes.", theme.version)
    db.refresh(theme)


def rename(db, site, user_id, theme_id, name, version):
    content.owned_site(db, site.id, user_id)
    theme = theme_for(db, site, theme_id)
    guard(db, theme, version)
    theme.name = _name(name)
    return theme


def remove(db, site, user_id, theme_id, version):
    content.owned_site(db, site.id, user_id, publish=True)
    theme = theme_for(db, site, theme_id)
    guard(db, theme, version)
    db.delete(theme)


def context(db, site, theme_id, page_id=""):
    """Return detached site/page objects: previews can never modify live ORM state."""
    theme = theme_for(db, site, theme_id)
    state = copy.deepcopy(theme.snapshot_json)
    pages = state.get("pages", [])
    selected = next((p for p in pages if p["id"] == page_id), None) if page_id else next(
        (p for p in pages if p["kind"] == "home"), pages[0] if pages else None)
    if selected is None:
        raise CommerceError("Page not found in this theme.")
    preview_site = SimpleNamespace(**{c.key: getattr(site, c.key) for c in Site.__table__.columns})
    preview_site.settings_json = state["settings"] or {}
    preview_site.published_settings_json = preview_site.settings_json
    preview_site.theme = state["theme"]
    preview_site.version = theme.version
    preview_site.theme_context_id = theme.id
    preview_page = SimpleNamespace(**{key: value for key, value in selected.items()
                                     if key != "document"})
    preview_page.tenant_id, preview_page.site_id = site.tenant_id, site.id
    preview_page.draft_json = selected["document"] or {"title": selected["title"], "blocks": []}
    preview_page.published_json = preview_page.draft_json
    preview_page.version = theme.version
    preview_page.created_at, preview_page.updated_at = theme.created_at, theme.updated_at
    return preview_site, preview_page


def edit(db, site, user_id, theme_id, page_id, document, version, *, design=None):
    content.owned_site(db, site.id, user_id)
    theme = theme_for(db, site, theme_id)
    if theme.kind != "draft":
        raise CommerceError("Duplicate a previous live theme before editing it.")
    state = copy.deepcopy(theme.snapshot_json)
    if design is not None:
        from app.site_theme import validate_theme
        state["settings"] = dict(state.get("settings") or {}, design=validate_theme(design))
    else:
        page = next((p for p in state["pages"] if p["id"] == page_id), None)
        if page is None:
            raise CommerceError("Page not found in this theme.")
        document = content.validate_document(document)
        content.validate_media_ownership(db, site, document)
        state.setdefault("revisions", []).append({"id": new_id(), "page_id": page_id,
            "document": copy.deepcopy(page["document"]), "action": "theme draft"})
        state["revisions"] = state["revisions"][-100:]
        page["document"] = document
    guard(db, theme, version)
    theme.snapshot_json = state
    db.flush()
    return theme


def publish(db, site, user_id, theme_id, version):
    """Promote all documents together; the caller commits the complete transaction."""
    from app.catalog_admin import _ensure_transaction
    from app.compliance import assert_document_compliant
    _ensure_transaction(db)
    with db.begin_nested():
        content.owned_site(db, site.id, user_id, publish=True)
        site = db.scalar(select(Site).where(Site.id == site.id, Site.tenant_id == site.tenant_id)
                         .with_for_update().execution_options(populate_existing=True))
        theme = theme_for(db, site, theme_id)
        state = copy.deepcopy(theme.snapshot_json)
        # Validate the complete snapshot before any document becomes live.
        for saved in state.get("pages", []):
            if saved["document"] is not None:
                saved["document"] = content.validate_document(saved["document"])
                content.validate_media_ownership(db, site, saved["document"])
                assert_document_compliant(saved["document"])
        guard(db, theme, version)
        previous = ThemeSnapshot(tenant_id=site.tenant_id, site_id=site.id,
            name="Previous live theme", kind="revision", snapshot_json=snapshot(db, site, live=True),
            created_at=datetime.now(UTC))
        db.add(previous)
        existing = {p.path: p for p in content.site_pages(db, site)}
        existing_by_id = {p.id: p for p in existing.values()}
        for saved in state.get("pages", []):
            # Catalogue handles may have changed since duplication. Preserve the
            # current owner's URL and match its stable identity before its path.
            page = existing_by_id.get(saved["id"]) or existing.get(saved["path"])
            if page is not None:
                existing.pop(page.path, None)
            if page is None:
                page = SitePage(tenant_id=site.tenant_id, site_id=site.id,
                    path=saved["path"], title=saved["title"], kind=saved["kind"],
                    product_id=saved.get("product_id"), collection_id=saved.get("collection_id"))
                db.add(page)
                db.flush()
            if saved["document"] is None:
                page.published_json = None
                page.version += 1
            else:
                content.save_page(db, site, page.id, user_id, saved["document"], page.version, "publish")
        for page in existing.values():
            page.published_json = None
            page.version += 1
            db.add(SiteRevision(tenant_id=site.tenant_id, site_id=site.id, page_id=page.id,
                author_id=user_id, content_json=page.draft_json, action="theme-unpublish"))
        site.theme = state["theme"]
        site.settings_json = copy.deepcopy(state["settings"] or {})
        site.published_settings_json = copy.deepcopy(site.settings_json)
        site.version += 1
        if site.status == "draft":
            site.status = "preview"
        db.flush()
        return previous

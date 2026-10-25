"""Site-scoped redirects. Reject chains and loops instead of surprising shoppers."""
import csv
import io
from urllib.parse import urlsplit

from sqlalchemy import select

from app import content
from app.content_gap_models import UrlRedirect
from app.models import SitePage
from app.services import CommerceError


def redirects(db, site, query=""):
    rows = db.scalars(select(UrlRedirect).where(UrlRedirect.site_id == site.id,
        UrlRedirect.tenant_id == site.tenant_id).order_by(UrlRedirect.source))
    return [r for r in rows if not query or query.casefold() in (r.source + " " + r.target).casefold()]


def save_redirect(db, site, source, target, redirect_id="", *, allow_live_source=False):
    source = content.page_path(str(source))
    target = content.safe_url(str(target))
    if not target or len(target) > 2048 or urlsplit(target).fragment or urlsplit(target).query:
        raise CommerceError("Use a destination path or HTTPS URL without query or fragment.")
    parsed = urlsplit(target)
    if parsed.hostname:
        from app.config import settings
        if parsed.hostname == (getattr(site, "hostname", None) or "").lower():
            target = parsed.path or "/"
        elif parsed.hostname == urlsplit(settings.public_url).hostname:
            prefix = "/sites/" + getattr(site, "slug", "")
            if parsed.path == prefix or parsed.path.startswith(prefix + "/"):
                target = parsed.path.removeprefix(prefix) or "/"
    if target.startswith("/"):
        target = content.page_path(target)
    if source == "/" or source == target:
        raise CommerceError("A redirect cannot replace the homepage or point to itself.")
    rows = redirects(db, site)
    row = next((r for r in rows if r.id == redirect_id), None)
    if redirect_id and row is None:
        raise CommerceError("Redirect not found.")
    other = [r for r in rows if r.id != redirect_id]
    if any(r.source == source for r in other):
        raise CommerceError("A redirect already uses this source.")
    if any(r.source == target or r.target == source for r in other):
        raise CommerceError("Redirect chains and loops are not allowed. Use the final destination.")
    if not allow_live_source and db.scalar(select(SitePage.id).where(
            SitePage.site_id == site.id, SitePage.tenant_id == site.tenant_id,
            SitePage.path == source, SitePage.published_json.is_not(None))):
        raise CommerceError("A published page already uses this source.")
    if not row:
        row = UrlRedirect(tenant_id=site.tenant_id, site_id=site.id)
        db.add(row)
    row.source, row.target = source, target
    db.flush()
    return row


def automatic_redirect(db, site, old_path, new_path, *, enabled=True):
    if not enabled or not old_path or old_path == new_path:
        return
    # Flatten existing historical aliases when a handle changes a second time.
    rows = redirects(db, site)
    for row in rows:
        if row.source == new_path:
            db.delete(row)
        elif row.target == old_path:
            row.target = new_path
    db.flush()
    existing = next((r for r in rows if r.source == old_path), None)
    save_redirect(db, site, old_path, new_path, existing.id if existing else "", allow_live_source=True)


def export_csv(db, site):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["Redirect from", "Redirect to"])
    writer.writerows((r.source, r.target) for r in redirects(db, site))
    return buffer.getvalue()


def import_csv(db, site, value):
    from app.catalog_admin import _ensure_transaction
    _ensure_transaction(db)
    if len(value.encode()) > 2_000_000:
        raise CommerceError("Use a CSV smaller than 2 MB.")
    reader = csv.DictReader(io.StringIO(value))
    if set(reader.fieldnames or []) != {"Redirect from", "Redirect to"}:
        raise CommerceError("CSV columns must be Redirect from and Redirect to.")
    results = []
    for index, item in enumerate(reader, 2):
        if index > 5001:
            raise CommerceError("Import at most 5,000 redirects at once.")
        try:
            with db.begin_nested():
                row = save_redirect(db, site, item["Redirect from"], item["Redirect to"])
            results.append({"row": index, "id": row.id})
        except CommerceError as exc:
            results.append({"row": index, "error": str(exc)})
    return results


def destination(db, site, path):
    # Public callers invoke this only after resolving real/static storefront routes.
    row = db.scalar(select(UrlRedirect).where(UrlRedirect.tenant_id == site.tenant_id,
                                             UrlRedirect.site_id == site.id,
                                             UrlRedirect.source == path))
    return row.target if row else None

def change_page_path(db, site, page, path, *, create_redirect=True):
    path = content.page_path(path)
    if path == page.path:
        return page
    if page.kind in {"product", "collection"}:
        raise CommerceError("Change this handle on the catalogue detail page.")
    if db.scalar(select(SitePage.id).where(SitePage.site_id == site.id,
            SitePage.tenant_id == site.tenant_id, SitePage.path == path, SitePage.id != page.id)):
        raise CommerceError("A page already uses this path.")
    automatic_redirect(db, site, page.path, path, enabled=create_redirect)
    page.path = path
    db.flush()
    return page

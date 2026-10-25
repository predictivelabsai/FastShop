"""Growth content orchestration through the established publication boundaries."""

import copy
from datetime import UTC, datetime

from app import content
from app.services import CommerceError
from app.site_blocks import default_locale, merge_localized, normalize_document
from app.site_blog import ensure_category, metadata
from app.site_widgets import sanitise_html


def article_document(db, site, page, data):
    document = normalize_document(page.draft_json)
    locale = default_locale(site)
    title = str(data.get("title", "")).strip()
    if not title or len(title) > 240:
        raise CommerceError("Enter a title of up to 240 characters.")
    for key, value in {
        "title": title,
        "description": str(data.get("description", ""))[:320],
        "image": str(data.get("image", "")),
        "category": str(data.get("category", "General")),
    }.items():
        document[key] = merge_localized(document.get(key, ""), value, locale)
    category = ensure_category(db, site, str(data.get("category", "General")))
    publish_at = str(data.get("publish_at", "")).strip()
    if publish_at:
        try:
            moment = datetime.fromisoformat(publish_at)
            publish_at = (
                moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
            ).isoformat()
        except ValueError:
            raise CommerceError("Choose a valid publication date and time in UTC.") from None
    document["blog"] = metadata(document) | {
        "state": "published" if data.get("action") == "publish" else "draft",
        "category_slug": category.slug,
        "author_name": str(data.get("author", ""))[:160],
        "tags": [tag.strip() for tag in str(data.get("tags", "")).split(",") if tag.strip()],
        "publish_at": publish_at,
        "excerpt": str(data.get("excerpt", ""))[:1000],
    }
    # Preserve all visual blocks; this form owns a single dedicated rich-text block.
    body = sanitise_html(str(data.get("body", "")))
    blocks = copy.deepcopy(document["blocks"])
    existing = next((b for b in blocks if b.get("id") == "article-body"), None)
    if existing:
        existing["body"] = merge_localized(existing.get("body", ""), body, locale)
    else:
        blocks.append({"id": "article-body", "type": "richtext", "body": body})
    document["blocks"] = blocks
    return content.validate_document(document)


def bulk_pages(db, site, user_id, ids, action):
    if action not in {"publish", "unpublish"}:
        raise CommerceError("Choose publish or unpublish.")
    content.owned_site(db, site.id, user_id, publish=True)
    if not ids or len(ids) > 100:
        raise CommerceError("Select between 1 and 100 pages.")
    pages = [content.site_page(db, site, page_id) for page_id in dict.fromkeys(ids)]
    for page in pages:
        content.save_page(db, site, page.id, user_id, page.draft_json, page.version, action)
    return len(pages)

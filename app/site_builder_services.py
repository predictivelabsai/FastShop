"""Site-scoped, reversible commands shared by the two builder interfaces."""

import copy
import json
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update

from app import content
from app.models import Site, SiteBuilderTurn, SiteChangeSet, SiteRevision
from app.services import CommerceError
from app.site_theme import validate_theme

BRAND_FIELDS = {"name", "tagline", "announcement", "footer"}
SECTION_FIELDS = {"heading", "eyebrow", "body", "image", "button", "link", "hidden"}


def snapshot(db, site):
    return {"version": site.version, "settings": copy.deepcopy(site.settings_json), "pages": {
        p.id: {"version": p.version, "path": p.path, "kind": p.kind, "document": copy.deepcopy(p.draft_json)}
        for p in content.site_pages(db, site)}}


def lock_site(db, site_id, user_id, version):
    site = content.owned_site(db, site_id, user_id)
    # Conditional UPDATE serializes writers on SQLite as well as PostgreSQL.
    result = db.execute(update(Site).where(Site.id == site.id, Site.tenant_id == site.tenant_id,
        Site.version == version).values(version=Site.version).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise CommerceError("Draft changed. Reload and try again.")
    db.refresh(site)
    return site


def record_change(db, site, user_id, before, source, summary):
    change = SiteChangeSet(tenant_id=site.tenant_id, site_id=site.id, user_id=user_id,
        source=source, summary=summary[:400], before_json=before, after_json=snapshot(db, site), created_at=datetime.now(UTC))
    db.add(change)
    db.flush()
    return change


def validate_response(response):
    if not isinstance(response, dict) or set(response) - {"answer", "question", "operations", "proposals"}:
        raise CommerceError("Builder returned an unsupported response.")
    for key in ("answer", "question"):
        if not isinstance(response.get(key, ""), str) or len(response.get(key, "")) > 3000:
            raise CommerceError("Builder response is too long.")
    operations = response.get("operations", [])
    if not isinstance(operations, list) or len(operations) > 12 or len(json.dumps(response)) > 40000:
        raise CommerceError("Keep builder changes small and focused.")
    from app.site_builder_reviews import validate_proposals
    validate_proposals(response.get("proposals", []))
    return response


def apply_operations(db, site_id, user_id, expected, operations, source="chat"):
    validate_response({"operations": operations})
    site = lock_site(db, site_id, user_id, expected["version"])
    if snapshot(db, site) != expected:
        raise CommerceError("Draft changed while the builder was working. Send your request again.")
    before = snapshot(db, site)
    config = copy.deepcopy(site.settings_json)
    summaries = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise CommerceError("Invalid builder operation.")
        kind = operation.get("op")
        if kind == "brief" and set(operation) == {"op", "values"}:
            values = operation["values"]
            if not isinstance(values, dict) or set(values) - {"business", "audience", "design_language", "pages", "tone"} or any(not isinstance(v, str) or len(v) > 1000 for v in values.values()):
                raise CommerceError("Use supported onboarding answers under 1,000 characters.")
            config["builder_brief"] = config.get("builder_brief", {}) | values
            summaries.append("Site brief")
        elif kind == "navigation" and set(operation) == {"op", "items"}:
            items = operation["items"]
            if not isinstance(items, list) or not 1 <= len(items) <= 12:
                raise CommerceError("Navigation needs one to twelve links.")
            validated = []
            for item in items:
                if not isinstance(item, dict) or set(item) != {"label", "path"} or not isinstance(item["label"], str) or not 1 <= len(item["label"].strip()) <= 80 or not isinstance(item["path"], str):
                    raise CommerceError("Each navigation link needs a label and safe URL.")
                validated.append({"label": item["label"].strip(), "path": content.safe_url(item["path"])})
            config["navigation"] = validated
            summaries.append("Navigation")
        elif kind == "theme" and set(operation) == {"op", "values"}:
            config["design"] = validate_theme(config.get("design", {}) | operation["values"])
            summaries.append("Design settings")
        elif kind == "brand" and set(operation) == {"op", "values"}:
            values = operation["values"]
            if not isinstance(values, dict) or set(values) - BRAND_FIELDS or any(not isinstance(v, str) or len(v) > 2000 for v in values.values()):
                raise CommerceError("Choose supported brand text fields.")
            config.update(values)
            summaries.append("Brand text")
        elif kind in {"section", "add_section", "reorder"}:
            allowed = {"section": {"op", "page_id", "section_id", "values"},
                       "add_section": {"op", "page_id", "section"}, "reorder": {"op", "page_id", "ids"}}[kind]
            if set(operation) != allowed:
                raise CommerceError("Invalid section command.")
            page = content.site_page(db, site, operation["page_id"])
            doc = copy.deepcopy(page.draft_json)
            if kind == "section":
                section = next((s for s in doc["sections"] if s["id"] == operation["section_id"]), None)
                values = operation["values"]
                if section is None or not isinstance(values, dict) or set(values) - SECTION_FIELDS:
                    raise CommerceError("Select an existing section and supported fields.")
                for key, value in values.items():
                    if key == "hidden":
                        if not isinstance(value, bool):
                            raise CommerceError("Visibility must be true or false.")
                    elif not isinstance(value, str) or len(value) > 8000:
                        raise CommerceError("Section text is invalid or too long.")
                section.update(values)
            elif kind == "add_section":
                section = operation["section"]
                if not isinstance(section, dict) or set(section) - (SECTION_FIELDS | {"type"}):
                    raise CommerceError("Choose a supported section.")
                if section.get("type") not in {"hero", "text", "split", "products", "articles"}:
                    raise CommerceError("This section needs manual configuration.")
                if any(not isinstance(v, str) or len(v) > 8000 for k, v in section.items() if k != "hidden"):
                    raise CommerceError("Invalid section text.")
                doc["sections"].append(section)
            else:
                ids = operation["ids"]
                if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or sorted(ids) != sorted(s["id"] for s in doc["sections"]):
                    raise CommerceError("Reorder must retain each section exactly once.")
                by_id = {s["id"]: s for s in doc["sections"]}
                doc["sections"] = [by_id[i] for i in ids]
            content.save_page(db, site, page.id, user_id, doc, page.version)
            summaries.append("Page: " + page.title)
        elif kind == "create_page" and set(operation) == {"op", "title", "path"}:
            if not all(isinstance(operation[k], str) for k in ("title", "path")):
                raise CommerceError("Page title and path must be text.")
            page = content.create_page(db, site, operation["title"], operation["path"])
            summaries.append("Created page: " + page.title)
        else:
            raise CommerceError("This action requires the normal merchant controls.")
    if not operations:
        return None
    site.settings_json = config
    site.version += 1
    db.flush()
    return record_change(db, site, user_id, before, source, "; ".join(dict.fromkeys(summaries)))


def undo_change(db, site_id, user_id, change_id, version):
    site = lock_site(db, site_id, user_id, version)
    change = db.scalar(select(SiteChangeSet).where(SiteChangeSet.id == change_id,
        SiteChangeSet.site_id == site.id, SiteChangeSet.tenant_id == site.tenant_id))
    if not change or snapshot(db, site) != change.after_json:
        raise CommerceError("Only the current unchanged draft revision can be undone.")
    before = snapshot(db, site)
    target = change.before_json
    for page in content.site_pages(db, site):
        if page.id not in target["pages"]:
            if page.published_json:
                raise CommerceError("Unpublish this page in the classical editor before removing it.")
            db.execute(delete(SiteRevision).where(SiteRevision.page_id == page.id,
                SiteRevision.site_id == site.id, SiteRevision.tenant_id == site.tenant_id))
            db.delete(page)
        else:
            doc = target["pages"][page.id]["document"]
            if doc != page.draft_json:
                content.save_page(db, site, page.id, user_id, doc, page.version, "restore")
    site.settings_json = copy.deepcopy(target["settings"])
    site.version += 1
    db.flush()
    return record_change(db, site, user_id, before, "undo", "Undo: " + change.summary)


def begin_turn(db, site_id, user_id, command_id, prompt, page_id, version, section_id=""):
    if not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", command_id) or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise CommerceError("Enter a message of up to 4,000 characters.")
    site = content.owned_site(db, site_id, user_id)
    prior = db.scalar(select(SiteBuilderTurn).where(SiteBuilderTurn.site_id == site.id,
        SiteBuilderTurn.tenant_id == site.tenant_id, SiteBuilderTurn.command_id == command_id))
    if prior:
        if prior.user_id != user_id or prior.prompt != prompt or prior.context_json["page_id"] != page_id or prior.context_json.get("section_id", "") != section_id:
            raise CommerceError("This command identifier is already in use.")
        return prior, False
    site = lock_site(db, site.id, user_id, version)
    from app.site_builder_reviews import review_context
    page = content.site_page(db, site, page_id)
    if section_id and section_id not in {s["id"] for s in page.draft_json.get("sections", [])}:
        raise CommerceError("Choose a section belonging to the selected page.")
    recent = list(db.scalars(select(SiteBuilderTurn).where(SiteBuilderTurn.site_id == site.id,
        SiteBuilderTurn.tenant_id == site.tenant_id,
        SiteBuilderTurn.created_at > datetime.now(UTC) - timedelta(minutes=1))))
    if len(recent) >= 8:
        raise CommerceError("Please wait a minute before sending more builder requests.")
    turn = SiteBuilderTurn(tenant_id=site.tenant_id, site_id=site.id, user_id=user_id,
        command_id=command_id, prompt=prompt, context_json={"snapshot": snapshot(db, site), "page_id": page.id, "section_id": section_id,
            "commerce": review_context(db, site)}, created_at=datetime.now(UTC))
    db.add(turn)
    db.flush()
    return turn, True


def finish_turn(db, site_id, user_id, turn_id, response, provider):
    site = content.owned_site(db, site_id, user_id)
    site = lock_site(db, site.id, user_id, site.version)
    turn = db.scalar(select(SiteBuilderTurn).where(SiteBuilderTurn.id == turn_id,
        SiteBuilderTurn.site_id == site.id, SiteBuilderTurn.tenant_id == site.tenant_id,
        SiteBuilderTurn.user_id == user_id).with_for_update())
    if not turn or turn.status != "pending":
        raise CommerceError("This builder request is no longer pending.")
    claimed = db.execute(update(SiteBuilderTurn).where(SiteBuilderTurn.id == turn.id,
        SiteBuilderTurn.site_id == site.id, SiteBuilderTurn.tenant_id == site.tenant_id,
        SiteBuilderTurn.status == "pending").values(status="applying").execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        raise CommerceError("This builder request is no longer pending.")
    validate_response(response)
    change = apply_operations(db, site.id, user_id, turn.context_json["snapshot"], response.get("operations", []))
    turn.response_json = response | {"change_id": change.id if change else "", "summary": change.summary if change else "No draft changes"}
    if response.get("proposals"):
        from app.site_builder_reviews import review_context
        current = review_context(db, site)
        if current != turn.context_json.get("commerce"):
            raise CommerceError("Merchant data changed while this proposal was prepared. Request another proposal.")
        known = {item["variant_id"] for item in current["catalog"]}
        if any(proposal["kind"] == "price" and proposal["variant_id"] not in known for proposal in response["proposals"]):
            raise CommerceError("Choose a product option in this site's supplied catalog.")
        turn.response_json = turn.response_json | {"review_status": "pending", "review_context": current, "review_site_version": site.version}
    turn.provider, turn.status = provider, "complete"
    db.flush()
    return turn


def end_pending_turn(db, site_id, user_id, command_id, *, failed=False):
    site = content.owned_site(db, site_id, user_id)
    state = "failed" if failed else "cancelled"
    result = db.execute(update(SiteBuilderTurn).where(SiteBuilderTurn.command_id == command_id,
        SiteBuilderTurn.site_id == site.id, SiteBuilderTurn.tenant_id == site.tenant_id,
        SiteBuilderTurn.user_id == user_id, SiteBuilderTurn.status == "pending").values(status=state,
            response_json={"answer": "Request not applied. Your previous draft is safe." if failed else
                "Cancelled. A late model response cannot change the draft. Provider processing may still incur usage.",
                "summary": state}).execution_options(synchronize_session=False))
    if result.rowcount != 1 and not failed:
        raise CommerceError("This request is no longer pending or is not yours. Reload to check its result.")
    return result.rowcount == 1

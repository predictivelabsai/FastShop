"""Public, read-only fragments for progressively enhanced site widgets."""

from __future__ import annotations

import re

from fasthtml.common import to_xml
from sqlalchemy import select
from starlette.responses import HTMLResponse

from app.db import SessionLocal
from app.models import Site


def register_site_widget_routes(rt, actor, csrf, check_csrf, shell, error):
    @rt("/sites/{slug}/widgets/recently-viewed", methods=["GET"])
    def recently_viewed(request, slug: str, ids: str = ""):
        headers = {"Cache-Control": "private, no-store", "Vary": "Host"}
        # Bound parsing work as well as the resulting catalogue selection.
        if len(ids) > 8 * 37:
            return HTMLResponse("Choose up to eight valid product identifiers.", status_code=400, headers=headers)
        selected = ids.split(",") if ids else []
        identifier = r"(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})"
        if len(selected) > 8 or any(not re.fullmatch(identifier, value) for value in selected):
            return HTMLResponse("Choose up to eight valid product identifiers.", status_code=400, headers=headers)
        from app.site_widgets_pack4 import product_cards

        with SessionLocal() as db:
            # Slug is the public site lookup; all subsequent owned queries use its tenant.
            site = db.scalar(select(Site).where(Site.slug == slug, Site.status.in_(["preview", "published"])))
            if site is None:
                return HTMLResponse("", status_code=404, headers=headers)
            base = request.scope.get("site_base", f"/sites/{site.slug}")
            cards = product_cards(db, site, list(dict.fromkeys(selected)), base)
            return HTMLResponse("".join(to_xml(card) for card in cards), headers=headers)

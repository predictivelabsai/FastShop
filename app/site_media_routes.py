"""Merchant library and the existing blob upload/serving boundary."""
import io
from urllib.parse import urlencode

from fasthtml.common import H2, A, Button, Div, Form, Input, Label, P, Small
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from starlette.responses import RedirectResponse, Response

from app import content, site_ui
from app.db import SessionLocal
from app.models import Site, SiteMedia, new_id
from app.services import CommerceError
from app.site_blocks import default_locale, merge_localized, resolve_text
from app.site_media import delete_media, media_for, media_url, owned_media, references


def register_media_routes(rt, actor, csrf, check_csrf, shell, error):
    def redirect(site_id, notice=""):
        return RedirectResponse(f"/admin/sites/{site_id}/media?" + urlencode({"notice": notice}), status_code=303)

    @rt("/admin/sites/{site_id}/media", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                media = media_for(db, site)
                return shell("Media library", A("← Site", href=f"/admin/sites/{site.id}"),
                    P("Upload photographs with descriptive alt text. Images are optimized to WebP and stored with this site."),
                    Form(csrf(session), Label("Title", Input(name="title", maxlength=200)),
                        Label("Alt text", Input(name="alt", required=True, maxlength=400)),
                        Label("Image (JPEG, PNG or WebP, up to 8 MB)", Input(type="file", name="image", accept="image/jpeg,image/png,image/webp", required=True)),
                        Label(Input(type="checkbox", name="placeholder"), " Placeholder image"),
                        Button("Upload image", cls="e-button"), method="post", enctype="multipart/form-data", cls="e-form"),
                    P(notice, role="status") if notice else None,
                    Div(*[card(db, site, m, session) for m in media], cls="e-grid") if media else P("No media yet. Upload your first image to reuse it in the editor."))
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/media", methods=["POST"])
    async def post(session, request, site_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                upload = form.get("image")
                if not upload or not hasattr(upload, "read"):
                    raise CommerceError("Select an image.")
                data = await upload.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024:
                    raise CommerceError("Images must be smaller than 8 MB.")
                with Image.open(io.BytesIO(data)) as original:
                    if original.format not in {"JPEG", "PNG", "WEBP"} or original.width * original.height > 25_000_000:
                        raise CommerceError("Choose a JPEG, PNG or WebP image smaller than 25 megapixels.")
                    original.thumbnail((1800, 1800))
                    output = io.BytesIO()
                    original.convert("RGB").save(output, format="WEBP", quality=85)
                title, alt = str(form.get("title", "")).strip(), str(form.get("alt", "")).strip()
                if not alt:
                    raise CommerceError("Provide useful alt text.")
                data = output.getvalue()
                db.add(SiteMedia(tenant_id=site.tenant_id, site_id=site.id, title=title[:200], alt=alt[:400],
                    storage_key=new_id() + ".webp", content_type="image/webp", size=len(data), data=data,
                    is_placeholder=form.get("placeholder") == "on"))
                db.commit()
            return RedirectResponse(f"/admin/sites/{site_id}/media", status_code=303)
        except (CommerceError, UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
            return redirect(site_id, str(exc) if isinstance(exc, CommerceError) else "Invalid image upload.")

    @rt("/site-media/{site_id}/{media_id}", methods=["GET"])
    def get(session, site_id: str, media_id: str):
        with SessionLocal() as db:
            site = db.get(Site, site_id)
            if not site:
                return Response("Not found", status_code=404)
            if not content.media_is_public(db, site, media_id):
                try:
                    content.owned_site(db, site.id, actor(session))
                except CommerceError:
                    return Response("Not found", status_code=404)
            media = db.scalar(select(SiteMedia).where(SiteMedia.id == media_id, SiteMedia.site_id == site.id, SiteMedia.tenant_id == site.tenant_id))
            if not media or not media.data:
                return Response("Not found", status_code=404)
            return Response(media.data, media_type=media.content_type,
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    def card(db, site, media, session):
        url = media_url(media)
        alt = resolve_text(media.localized_alt if media.localized_alt is not None else media.alt, default_locale(site))
        used = references(db, site, media)
        return Div(H2(media.title or "Untitled image"),
            site_ui.image(url, alt) if media.content_type.startswith("image/") else P(media.content_type),
            P(A("Preview asset", href=url, target="_blank", rel="noopener")),
            Label("Image URL — use in a section", Input(value=url, readonly=True)),
            Button("Copy URL", type="button", onclick="navigator.clipboard.writeText(this.previousElementSibling.querySelector('input').value).then(()=>this.textContent='Copied').catch(()=>this.textContent='Select and copy the URL above')"),
            P(Small(f"{media.size:,} bytes · " + ("Referenced" if used else "Unreferenced"))),
            P("; ".join(used[:3])) if used else None,
            Form(csrf(session), Label("Title", Input(name="title", value=media.title, maxlength=200)),
                Label("Alt text", Input(name="alt", value=alt, maxlength=400)),
                Div(Button("Save details", name="action", value="save", cls="e-button"),
                    Button("Delete media", name="action", value="delete"), cls="e-actions"),
                method="post", action=f"/admin/sites/{site.id}/media/{media.id}", cls="e-form"), cls="e-card e-form")

    @rt("/admin/sites/{site_id}/media/{media_id}", methods=["POST"])
    async def post(session, request, site_id: str, media_id: str):
        form = await request.form()
        try:
            check_csrf(session, form)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                from app.site_builder_services import lock_site
                site = lock_site(db, site.id, actor(session), site.version)
                media = owned_media(db, site, media_id)
                if form.get("action") == "delete":
                    delete_media(db, site, media_id)
                elif form.get("action") == "save":
                    media.title = str(form.get("title", "")).strip()[:200]
                    alt = str(form.get("alt", "")).strip()[:400]
                    media.localized_alt = merge_localized(media.localized_alt if media.localized_alt is not None else media.alt, alt, default_locale(site))
                    media.alt = alt
                else:
                    raise CommerceError("Unknown media action.")
                site.version += 1
                db.commit()
            return redirect(site_id, "Media updated.")
        except CommerceError as exc:
            return redirect(site_id, str(exc))

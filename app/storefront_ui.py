"""Shared public commerce error responses."""
from starlette.responses import Response


def public_message(request, slug, message, *, status_code=400, plain=True):
    return Response(str(message), status_code=status_code, media_type="text/plain" if plain else None)


def commerce_page(site, request, title, *children):
    """Let a registered pack wrap cart and account pages; None uses the default shell."""
    from app.site_theme import pack_renderer
    renderer = pack_renderer(site.published_settings_json or {}, "commerce")
    return renderer(site, request, title, *children) if renderer else None

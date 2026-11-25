"""Tenant-scoped content indexes, editorial forms, navigation and files."""

import json
import re
from urllib.parse import urlencode

from fasthtml.common import (
    H2,
    A,
    Button,
    Dialog,
    Div,
    Form,
    Img,
    Input,
    Label,
    Li,
    Link,
    Ol,
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
    Strong,
    Table,
    Tbody,
    Td,
    Template,
    Textarea,
    Th,
    Thead,
    Tr,
    Ul,
    Video,
)
from starlette.responses import RedirectResponse

from app import content, growth_content, site_menus
from app import site_builder_services as builder
from app.admin_icons import lucide
from app.admin_ui import badge, card, empty_state, form_modal, page_header
from app.db import SessionLocal
from app.models import SiteMedia, new_id
from app.services import CommerceError
from app.site_blocks import default_locale, resolve_document, resolve_text
from app.site_blog import is_listed, metadata
from app.site_media import (
    MAX_IMAGE_BYTES,
    delete_media,
    media_for,
    media_url,
    owned_media,
    prepare_image_bytes,
    reference_map,
    usage_summary,
)

PAGE_TYPE_LABELS = {
    "home": "Home page", "content": "Standard page", "collection": "Collection", "product": "Product",
    "science": "Science page", "blog": "Blog", "article": "Blog post", "contact": "Contact page",
    "legal": "Policy page",
}


def register_growth_content_routes(rt, actor, csrf, check_csrf, shell, error):
    def base(site_id):
        return f"/admin/sites/{site_id}/content"

    def redirect(site_id, area, notice=""):
        return RedirectResponse(
            base(site_id) + "/" + area + "?" + urlencode({"notice": notice}), 303
        )

    def assets():
        return (
            Link(rel="stylesheet", href="/static/admin/growth-content.css"),
            Script(src="/static/admin/growth-content.js", defer=True),
        )

    def page_index(session, site_id, q, kind, status, notice, articles=False, sort=""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                resources = [
                    page
                    for page in content.site_pages(db, site)
                    if (page.kind == "article") == articles
                ]
                pages = [
                    page
                    for page in resources
                    if (not q or q.casefold() in (page.title + " " + page.path).casefold())
                    and (not kind or page.kind == kind)
                    and (
                        not status
                        or bool(page.published_json) == (status == "published")
                    )
                ]
                if sort in {"title", "title_desc"}:
                    pages.sort(key=lambda page: page.title.casefold(), reverse=sort == "title_desc")
                elif sort == "updated":
                    pages.sort(key=lambda page: page.updated_at, reverse=True)

                def resource_row(page):
                    return Tr(
                        Td(
                            Input(
                                type="checkbox",
                                name="page_id",
                                value=page.id,
                                aria_label="Select " + page.title,
                            ),
                            data_label="",
                        ),
                        Td(
                            A(
                                page.title,
                                href=(
                                    base(site.id) + "/blog-posts/" + page.id
                                    if articles
                                    else f"/admin/sites/{site.id}/editor?page={page.id}"
                                ),
                                cls="gc-resource-link",
                            ),
                            Small(page.path),
                            data_label="Post" if articles else "Page",
                        ),
                        Td(
                            badge(
                                ("Scheduled" if articles and not is_listed(page) else "Published")
                                if page.published_json
                                else "Draft",
                                "success" if page.published_json else "neutral",
                            ),
                            data_label="Status",
                        ),
                        Td(PAGE_TYPE_LABELS.get(page.kind, page.kind.title()), data_label="Type"),
                        Td(
                            Div(
                                A(
                                    "Visual editor",
                                    href=f"/admin/sites/{site.id}/editor?page={page.id}",
                                    cls="a-text-link",
                                ),
                                A(
                                    "Classic editor",
                                    href=f"/admin/sites/{site.id}/pages/{page.id}",
                                    cls="a-text-link",
                                ),
                                cls="gc-row-actions",
                            ),
                            data_label="Edit",
                        ),
                    )

                create = (
                    A("Create blog post", href=base(site.id) + "/blog-posts/new", cls="a-button")
                    if articles
                    else Button("Add page", type="button", data_modal_open="create-page", cls="a-button")
                )
                resource_name = "blog posts" if articles else "pages"
                singular_name = "blog post" if articles else "page"
                filters_active = bool(q or kind or status or sort)
                no_results = bool(resources) and filters_active
                index_path = base(site.id) + ("/blog-posts" if articles else "/pages")
                empty_action = (
                    A("Clear filters", href=index_path, cls="a-button")
                    if no_results
                    else (
                        A(
                            "Create blog post",
                            href=base(site.id) + "/blog-posts/new",
                            cls="a-button",
                        )
                        if articles
                        else Button("Add page", type="button", data_modal_open="create-page", cls="a-button")
                    )
                )
                filter_form = Form(
                    Label("Search", Input(name="q", type="search", value=q)),
                    Label(
                        "Status",
                        Select(
                            *[
                                Option(label, value=value, selected=value == status)
                                for value, label in [
                                    ("", "All statuses"),
                                    ("published", "Published"),
                                    ("draft", "Draft"),
                                ]
                            ],
                            name="status",
                        ),
                    ),
                    Label(
                        "Type",
                        Select(
                            Option("All types", value=""),
                            *[
                                Option(PAGE_TYPE_LABELS.get(value, value.title()), value=value,
                                       selected=value == kind)
                                for value in sorted(content.PAGE_KINDS - {"article"})
                            ],
                            name="kind",
                        ),
                    )
                    if not articles
                    else None,
                    Label(
                        "Sort",
                        Select(
                            *[
                                Option(label, value=value, selected=sort == value)
                                for value, label in [
                                    ("", "Manual order"),
                                    ("title", "Title A to Z"),
                                    ("title_desc", "Title Z to A"),
                                    ("updated", "Recently updated"),
                                ]
                            ],
                            name="sort",
                        ),
                    ),
                    Button("Filter", cls="a-button"),
                    method="get",
                    cls="gc-filters",
                )
                filters = Div(
                    filter_form,
                    A("Clear filters", href=index_path, cls="a-button gc-clear-filters")
                    if filters_active
                    else None,
                    cls="gc-filter-toolbar",
                )
                empty = empty_state(
                    f"No {resource_name} match these filters"
                    if no_results
                    else f"Create your first {singular_name}",
                    "Clear a filter or try a different search."
                    if no_results
                    else (
                        "Start a post when you are ready to share an update."
                        if articles
                        else "Add a page, then open it in the visual or classic editor."
                    ),
                    empty_action,
                )
                index = (
                    Form(
                        csrf(session),
                        Div(
                            Table(
                                Thead(
                                    Tr(
                                        Th("Select", scope="col"),
                                        Th("Title", scope="col"),
                                        Th("Status", scope="col"),
                                        Th("Type", scope="col"),
                                        Th("Edit", scope="col"),
                                    )
                                ),
                                Tbody(*[resource_row(page) for page in pages]),
                                cls="a-responsive-table gc-resource-table",
                                aria_label="Blog posts" if articles else "Pages",
                            ),
                            cls="a-table-wrap",
                        ),
                        Div(
                            Button(
                                "Publish selected",
                                name="action",
                                value="publish",
                                cls="a-button",
                            ),
                            Button(
                                "Unpublish selected",
                                name="action",
                                value="unpublish",
                                cls="a-button",
                            ),
                            cls="a-actions gc-bulk",
                        ),
                        method="post",
                        action=base(site.id) + "/pages/bulk",
                    )
                    if pages
                    else empty
                )
                return shell(
                    "Blog posts" if articles else "Pages",
                    *assets(),
                    Div(
                        page_header("Blog posts" if articles else "Pages", primary_action=create),
                        P(notice, role="status", cls="gc-notice") if notice else None,
                        Div(filters, index, cls="a-card gc-index o-operations-index"),
                        form_modal(
                            "Add page",
                            Label("Title", Input(name="title", required=True, maxlength=240,
                                                 placeholder="About us", data_slug_source="create-page-path")),
                            Label("URL path", Input(name="path", required=True, placeholder="/about-us",
                                                    id="create-page-path"),
                                  Small("Where shoppers find the page on your store.", cls="p-help")),
                            Label(
                                "Type",
                                Select(
                                    *[
                                        Option(PAGE_TYPE_LABELS[value], value=value)
                                        for value in ["content", "legal", "contact", "blog"]
                                    ],
                                    name="kind",
                                ),
                                Small("Use Policy page for terms, privacy and refunds, and Blog for a page that lists your posts.",
                                      cls="p-help"),
                            ),
                            submit="Create page",
                            action=base(site.id) + "/pages",
                            id="create-page",
                            csrf=csrf(session),
                            description="The page opens in the visual editor so you can add content.",
                        )
                        if not articles
                        else None,
                        cls="gc-content o-operations",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/pages", methods=["GET"])
    def get(session, site_id: str, q: str = "", kind: str = "", status: str = "", notice: str = "",
            sort: str = ""):
        return page_index(session, site_id, q, kind, status, notice, sort=sort)

    @rt("/admin/sites/{site_id}/content/blog-posts", methods=["GET"])
    def get(session, site_id: str, q: str = "", status: str = "", notice: str = "", sort: str = ""):
        return page_index(session, site_id, q, "", status, notice, True, sort)

    @rt("/admin/sites/{site_id}/content/pages", methods=["POST"])
    async def post(session, request, site_id: str):
        data = await request.form()
        try:
            check_csrf(session, data)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                page = content.create_page(
                    db,
                    site,
                    str(data.get("title", "")),
                    str(data.get("path", "")),
                    str(data.get("kind", "content")),
                )
                db.commit()
                return RedirectResponse(f"/admin/sites/{site.id}/editor?page={page.id}", 303)
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/pages/bulk", methods=["POST"])
    async def post(session, request, site_id: str):
        data = await request.form()
        try:
            check_csrf(session, data)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                growth_content.bulk_pages(
                    db, site, actor(session), data.getlist("page_id"), str(data.get("action", ""))
                )
                db.commit()
            return redirect(site_id, "pages", "Pages updated.")
        except CommerceError as exc:
            return error(exc)

    def article_form(session, site_id, page_id=""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                page = content.site_page(db, site, page_id) if page_id else None
                if page and page.kind != "article":
                    raise CommerceError("Choose a blog post.")
                doc = resolve_document(page.draft_json, default_locale(site)) if page else {}
                blog = metadata(doc)
                body = next(
                    (b.get("body", "") for b in doc.get("blocks", []) if b["id"] == "article-body"),
                    "",
                )
                return shell(
                    "Edit blog post" if page else "Create blog post",
                    *assets(),
                    page_header(
                        "Edit blog post" if page else "Create blog post",
                        back_href=base(site.id) + "/blog-posts",
                    ),
                    Form(
                        csrf(session),
                        Input(type="hidden", name="version", value=page.version if page else 0),
                        Div(
                            card(
                                Label(
                                    "Title",
                                    Input(
                                        name="title",
                                        value=doc.get("title", ""),
                                        required=True,
                                        maxlength=240,
                                    ),
                                ),
                                Label(
                                    "Path",
                                    Input(
                                        name="path",
                                        value=page.path if page else "/blog/",
                                        required=True,
                                    ),
                                ),
                                Label(Input(type="hidden", name="create_redirect", value="off"), Input(type="checkbox", name="create_redirect", value="on", checked=True), " Create redirect when the path changes"),
                                Div(
                                    *[
                                        Button(
                                            label,
                                            type="button",
                                            data_rich_command=command,
                                            cls="a-button",
                                        )
                                        for command, label in [
                                            ("bold", "Bold"),
                                            ("italic", "Italic"),
                                            ("insertUnorderedList", "List"),
                                        ]
                                    ],
                                    cls="a-actions",
                                ),
                                Label(
                                    "Content",
                                    Textarea(body, name="body", rows=14, data_rich_source=""),
                                ),
                                Label(
                                    "Excerpt",
                                    Textarea(
                                        blog.get("excerpt", ""),
                                        name="excerpt",
                                        rows=3,
                                        maxlength=1000,
                                    ),
                                ),
                                Label(
                                    "SEO description",
                                    Textarea(
                                        doc.get("description", ""),
                                        name="description",
                                        rows=3,
                                        maxlength=320,
                                    ),
                                ),
                            ),
                            card(
                                Label(
                                    "Featured image",
                                    Select(
                                        Option("No image", value=""),
                                        *[
                                            Option(
                                                m.title or "Untitled image",
                                                value=media_url(m),
                                                selected=doc.get("image") == media_url(m),
                                            )
                                            for m in media_for(db, site)
                                            if m.content_type.startswith("image/")
                                        ],
                                        name="image",
                                    ),
                                ),
                                A("Manage files", href=base(site.id) + "/files", cls="a-button"),
                                Label(
                                    "Author",
                                    Input(name="author", value=blog["author_name"], maxlength=160),
                                ),
                                Label(
                                    "Category",
                                    Input(
                                        name="category",
                                        value=doc.get("category", "General"),
                                        maxlength=100,
                                    ),
                                ),
                                Label(
                                    "Tags",
                                    Input(
                                        name="tags",
                                        value=", ".join(blog["tags"]),
                                        placeholder="news, guides",
                                    ),
                                ),
                                Label(
                                    "Schedule publication (UTC)",
                                    Input(
                                        name="publish_at",
                                        type="datetime-local",
                                        value=blog.get("publish_at", "")[:16],
                                    ),
                                ),
                                Small(
                                    "Leave empty to publish immediately. Scheduled posts become visible at the chosen time."
                                ),
                                title="Organisation and visibility",
                            ),
                            cls="gc-columns",
                        ),
                        Div(
                            Button("Save draft", name="action", value="draft", cls="a-button"),
                            Button(
                                "Publish", name="action", value="publish", cls="a-button a-button-primary"
                            ),
                            cls="a-actions",
                        ),
                        method="post",
                        action=base(site.id) + "/blog-posts/" + (page.id if page else "new"),
                        cls="gc-form",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/blog-posts/new", methods=["GET"])
    def get(session, site_id: str):
        return article_form(session, site_id)

    @rt("/admin/sites/{site_id}/content/blog-posts/{page_id}", methods=["GET"])
    def get(session, site_id: str, page_id: str):
        return article_form(session, site_id, page_id)

    @rt("/admin/sites/{site_id}/content/blog-posts/{page_id}", methods=["POST"])
    async def post(session, request, site_id: str, page_id: str):
        data = await request.form()
        try:
            check_csrf(session, data)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(db, site_id, user_id)
                site = builder.lock_site(db, site.id, user_id, site.version)
                page = (
                    content.create_page(
                        db, site, str(data.get("title", "")), str(data.get("path", "")), "article"
                    )
                    if page_id == "new"
                    else content.site_page(db, site, page_id)
                )
                if page.kind != "article":
                    raise CommerceError("Choose a blog post.")
                if page_id != "new" and "path" in data:
                    from app.url_redirects import change_page_path
                    change_page_path(db, site, page, str(data["path"]), create_redirect=data.get("create_redirect", "on") == "on")
                version = page.version if page_id == "new" else int(data.get("version", -1))
                doc = growth_content.article_document(db, site, page, data)
                action = str(data.get("action", "draft"))
                if action not in {"draft", "publish"}:
                    raise CommerceError("Choose save draft or publish.")
                content.save_page(db, site, page.id, user_id, doc, version, action)
                site.version += 1
                db.commit()
            return redirect(site_id, "blog-posts", "Blog post saved.")
        except (CommerceError, ValueError) as exc:
            return error(CommerceError(str(exc)))

    @rt("/admin/sites/{site_id}/content/files", methods=["GET"])
    def get(
        session, site_id: str, q: str = "", type: str = "", view: str = "grid", notice: str = ""
    ):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                if view not in {"grid", "list"}:
                    view = "grid"
                all_media = media_for(db, site)
                library = [
                    media
                    for media in all_media
                    if (not q or q.casefold() in (media.title + " " + media.alt).casefold())
                    and (not type or media.content_type.startswith(type))
                ]
                usage = reference_map(db, site, library)
                files_path = base(site.id) + "/files"

                def thumb_url(media, width):
                    url = media_url(media)
                    return url + f"?w={width}" if url.startswith("/site-media/") else url

                def download_filename(media):
                    stem = re.sub(
                        r"[^a-z0-9]+", "-", str(media.title or media.alt or "image").casefold()
                    ).strip("-")
                    identifier = re.sub(r"[^a-zA-Z0-9_-]+", "-", media.id).strip("-")
                    extension = {
                        "image/avif": ".avif",
                        "image/gif": ".gif",
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/svg+xml": ".svg",
                        "image/webp": ".webp",
                        "video/mp4": ".mp4",
                        "video/quicktime": ".mov",
                        "video/webm": ".webm",
                    }.get(media.content_type.casefold(), ".file")
                    return f"{(stem or 'file')[:64]}-{(identifier or 'asset')[:32]}{extension}"

                def kind_label(media):
                    return media.content_type.split("/")[-1].replace("svg+xml", "svg").upper()

                def size_label(media):
                    size = media.size or 0
                    return f"{size / 1_048_576:.1f} MB" if size >= 1_048_576 else f"{max(1, round(size / 1024))} KB"

                def preview(media, width):
                    if media.content_type.startswith("image/"):
                        return Img(src=thumb_url(media, width), alt=media.alt, loading="lazy", decoding="async")
                    if media.content_type.startswith("video/"):
                        return Video(src=media_url(media) + "#t=0.5", muted=True, preload="metadata",
                                     playsinline=True, aria_hidden="true", tabindex="-1")
                    return Span(lucide("file", 24), cls="gc-file-icon")

                def file_data(media):
                    summary, pages = usage_summary(usage[media.id])
                    filename = download_filename(media)
                    return json.dumps({
                        "id": media.id, "title": media.title, "alt": media.alt, "url": media_url(media),
                        "preview": thumb_url(media, 640), "image": media.content_type.startswith("image/"),
                        "filename": filename, "kind": kind_label(media), "size": size_label(media),
                        "usage": summary, "pages": pages[:8], "locked": bool(usage[media.id]),
                        "action": files_path + "/" + media.id,
                    })

                def file_download(media):
                    filename = download_filename(media)
                    label = media.title or media.alt or "file"
                    return A(
                        lucide("download", 16),
                        href=media_url(media),
                        download=filename,
                        aria_label=f"Download {label} as {filename}",
                        title="Download",
                        cls="gc-icon-button",
                    )

                def open_button(media, *content, cls):
                    return Button(*content, type="button", data_file=file_data(media),
                                  aria_label="Edit details for " + (media.title or media.alt or "file"), cls=cls)

                def file_card(media):
                    summary, _ = usage_summary(usage[media.id])
                    return Div(
                        open_button(media, Span(preview(media, 320), cls="gc-file-preview"),
                                    Span(media.title or media.alt or "Untitled file", cls="gc-file-name"),
                                    Span(f"{kind_label(media)} · {size_label(media)}", cls="gc-file-meta"),
                                    cls="gc-file-open"),
                        Div(Span(summary, cls="gc-file-usage"), file_download(media), cls="gc-file-foot"),
                        cls="gc-file-card",
                    )

                def file_row(media):
                    summary, _ = usage_summary(usage[media.id])
                    return Tr(
                        Td(open_button(media, Span(preview(media, 160), cls="gc-file-thumb"),
                                       Span(Span(media.title or media.alt or "Untitled file", cls="gc-file-name"),
                                            Small(kind_label(media), cls="gc-file-type")),
                                       cls="gc-file-open gc-file-open-row"), data_label="File"),
                        Td(media.alt or Span("Missing", cls="gc-missing"), cls="gc-file-alt", data_label="Alt text"),
                        Td(f"{media.created_at.day} {media.created_at:%b %Y}" if media.created_at else "", data_label="Date added"),
                        Td(size_label(media), data_label="Size"),
                        Td(summary, cls="gc-file-usage", data_label="References"),
                        Td(file_download(media), data_label="Download", cls="gc-file-actions"),
                    )

                filters_active = bool(q or type)
                no_results = bool(all_media) and filters_active
                upload_button = Button("Upload files", type="button", data_modal_open="upload-image", cls="a-button")
                empty = empty_state(
                    "No files match these filters" if no_results else "Upload your first image",
                    "Clear a filter or try a different search."
                    if no_results
                    else "Upload an image once, then add accessible details and use it across the site.",
                    A("Clear filters", href=files_path, cls="a-button") if no_results
                    else Button("Upload files", type="button", data_modal_open="upload-image", cls="a-button"),
                )

                def view_link(value, icon_name, label):
                    params = {k: v for k, v in {"q": q, "type": type, "view": value}.items() if v}
                    return A(lucide(icon_name, 16), href=files_path + "?" + urlencode(params), aria_label=label,
                             title=label, aria_current="true" if view == value else None,
                             cls="gc-view" + (" is-active" if view == value else ""))

                filter_form = Form(
                    Label("Search files", Input(name="q", value=q, type="search", placeholder="Search files")),
                    Label(
                        "Type",
                        Select(
                            Option("All files", value=""),
                            Option("Images", value="image/", selected=type == "image/"),
                            Option("Videos", value="video/", selected=type == "video/"),
                            name="type",
                        ),
                    ),
                    Input(type="hidden", name="view", value=view),
                    Button("Filter", cls="a-button"),
                    A("Clear", href=files_path + ("?view=list" if view == "list" else "")) if filters_active else None,
                    method="get",
                    cls="gc-filters",
                )
                library_view = (
                    Div(
                        Table(
                            Thead(Tr(*[Th(name, scope="col") for name in
                                       ("File", "Alt text", "Date added", "Size", "References", "")])),
                            Tbody(*[file_row(media) for media in library]),
                            cls="a-responsive-table gc-resource-table gc-file-table",
                            aria_label="Files",
                        ),
                        cls="a-table-wrap",
                    )
                    if library and view == "list"
                    else (Div(*[file_card(media) for media in library], cls="gc-files") if library else empty)
                )
                details = Dialog(
                    Form(
                        Div(H2("File details", id="file-details-title"),
                            Button(lucide("x", 18), type="button", data_modal_close="", aria_label="Close",
                                   cls="p-modal-close"), cls="p-modal-head"),
                        Div(
                            Div(Img(alt="", data_file_preview=""), cls="gc-detail-preview"),
                            Div(
                                csrf(session),
                                Label("Title", Input(name="title", maxlength=200, data_file_field="title")),
                                Label("Alt text", Textarea(name="alt", maxlength=400, rows=3, data_file_field="alt"),
                                      Small("Describe the image for shoppers using screen readers.", cls="p-help")),
                                Div(Span("Details", cls="gc-detail-label"), P(data_file_meta=""), cls="gc-detail-group"),
                                Div(Span("Used in", cls="gc-detail-label"), P(data_file_usage=""),
                                    Ul(data_file_pages="", cls="gc-detail-pages"), cls="gc-detail-group"),
                                Div(Span("Link", cls="gc-detail-label"),
                                    Div(Input(readonly=True, aria_label="File link", data_file_url=""),
                                        Button(lucide("copy", 16), type="button", data_copy_url="", aria_label="Copy link",
                                               title="Copy link", cls="a-button gc-copy"), cls="gc-copy-row"),
                                    cls="gc-detail-group"),
                                cls="gc-detail-fields",
                            ),
                            cls="p-modal-body gc-detail-body",
                        ),
                        Div(
                            Button("Delete file", name="action", value="delete", data_file_delete="",
                                   cls="a-button gc-delete",
                                   formnovalidate=True),
                            A(lucide("download", 16), Span("Download"), data_file_download="", cls="a-button"),
                            Span(cls="gc-spacer"),
                            Button("Cancel", type="button", data_modal_close="", cls="a-button"),
                            Button("Save", name="action", value="save", cls="a-button a-button-primary"),
                            cls="p-modal-foot",
                        ),
                        method="post",
                    ),
                    id="file-details", aria_labelledby="file-details-title", cls="p-modal gc-file-dialog",
                )
                return shell(
                    "Files",
                    *assets(),
                    Div(
                        page_header("Files", primary_action=upload_button),
                        P(notice, role="status", cls="gc-notice") if notice else None,
                        Div(
                            Div(filter_form, Div(view_link("grid", "layout-grid", "Grid view"),
                                                 view_link("list", "list", "List view"), cls="gc-views",
                                                 role="group", aria_label="View"),
                                cls="gc-filter-toolbar"),
                            library_view,
                            cls="a-card gc-index gc-files-index o-operations-index",
                        ),
                        details,
                        form_modal(
                            "Upload files",
                            Label(lucide("upload", 24), Span("Drop an image here or ", Span("choose a file", cls="gc-link")),
                                  Small("JPEG, PNG or WebP up to 8 MB", cls="p-help"),
                                  Input(type="file", name="file", accept="image/*", required=True),
                                  cls="gc-dropzone", data_dropzone=""),
                            Label("Title", Input(name="title", maxlength=200)),
                            Label("Alt text", Input(name="alt", required=True, maxlength=400),
                                  Small("Describe the image for shoppers using screen readers.", cls="p-help")),
                            submit="Upload",
                            action=files_path,
                            id="upload-image",
                            csrf=csrf(session),
                        ),
                        cls="gc-content o-operations",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/files", methods=["POST"])
    async def post(session, request, site_id: str):
        data = await request.form()
        try:
            check_csrf(session, data)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                upload = data.get("file")
                if not upload or not hasattr(upload, "read"):
                    raise CommerceError("Choose an image.")
                alt = str(data.get("alt", "")).strip()
                if not alt:
                    raise CommerceError("Add useful alt text.")
                image = prepare_image_bytes(await upload.read(MAX_IMAGE_BYTES + 1))
                db.add(
                    SiteMedia(
                        tenant_id=site.tenant_id,
                        site_id=site.id,
                        title=str(data.get("title", ""))[:200],
                        alt=alt[:400],
                        storage_key=new_id() + ".webp",
                        content_type="image/webp",
                        size=len(image),
                        data=image,
                    )
                )
                db.commit()
            return redirect(site_id, "files", "Image uploaded.")
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/files/{media_id}", methods=["POST"])
    async def post(session, request, site_id: str, media_id: str):
        data = await request.form()
        try:
            check_csrf(session, data)
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                site = builder.lock_site(db, site.id, actor(session), site.version)
                media = owned_media(db, site, media_id)
                if data.get("action") == "delete":
                    delete_media(db, site, media_id)
                elif data.get("action") == "save":
                    from app.site_blocks import merge_localized

                    media.title = str(data.get("title", ""))[:200]
                    media.alt = str(data.get("alt", ""))[:400]
                    media.localized_alt = merge_localized(
                        media.localized_alt or media.alt, media.alt, default_locale(site)
                    )
                else:
                    raise CommerceError("Choose save or delete.")
                site.version += 1
                db.commit()
            return redirect(site_id, "files", "File updated.")
        except CommerceError as exc:
            return error(exc)

    def menu_title(name):
        return name.replace("-", " ").capitalize()

    def menu_status(menu):
        if menu.published_items_json is None:
            return badge("Draft", "neutral")
        if menu.published_items_json != menu.items_json:
            return badge("Unpublished changes", "warning")
        return badge("Published", "success")

    def menu_labels(items, locale):
        return [resolve_text(item["label"], locale) for item in site_menus.walk_items(items)]

    @rt("/admin/sites/{site_id}/content/menus", methods=["GET"])
    def get(session, site_id: str, q: str = "", notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                locale = default_locale(site)
                all_menus = site_menus.menus_for(db, site)
                menus = [menu for menu in all_menus if not q or q.casefold() in menu.name.casefold()]
                menus_path = base(site.id) + "/menus"
                rows = [
                    Tr(
                        Td(A(menu_title(menu.name), href=f"{menus_path}/{menu.name}", cls="gc-resource-link"),
                           Small(menu.name), data_label="Menu"),
                        Td(", ".join(menu_labels(menu.items_json, locale)[:6]) or "No items yet",
                           cls="gc-menu-preview", data_label="Menu items"),
                        Td(menu_status(menu), data_label="Status"),
                    )
                    for menu in menus
                ]
                no_results = bool(all_menus) and bool(q)
                add = Button("Add menu", type="button", data_modal_open="create-menu", cls="a-button")
                empty = empty_state(
                    "No menus match this search" if no_results else "Create your first menu",
                    "Clear the search or try another menu name."
                    if no_results
                    else "Menus hold the links in your store header and footer.",
                    A("Clear search", href=menus_path, cls="a-button") if no_results
                    else Button("Add menu", type="button", data_modal_open="create-menu", cls="a-button"),
                )
                return shell(
                    "Menus",
                    *assets(),
                    Div(
                        page_header("Menus", primary_action=add),
                        P(notice, role="status", cls="gc-notice") if notice else None,
                        Div(
                            Form(
                                Label("Search menus", Input(name="q", value=q, type="search")),
                                Button("Filter", cls="a-button"),
                                A("Clear", href=menus_path) if q else None,
                                method="get", cls="gc-filters",
                            ),
                            Div(
                                Table(
                                    Thead(Tr(Th("Menu", scope="col"), Th("Menu items", scope="col"),
                                             Th("Status", scope="col"))),
                                    Tbody(*rows),
                                    cls="a-responsive-table gc-resource-table",
                                    aria_label="Menus",
                                ),
                                cls="a-table-wrap",
                            ) if menus else empty,
                            cls="a-card gc-index o-operations-index",
                        ),
                        form_modal(
                            "Add menu",
                            Label("Title", Input(name="title", required=True, maxlength=40, placeholder="Footer",
                                                 data_slug_source="create-menu-handle", data_slug_plain="")),
                            Label("Handle", Input(name="name", id="create-menu-handle", required=True, maxlength=40,
                                                  pattern=r"[a-z][a-z0-9\-]*", placeholder="footer"),
                                  Small("Themes use the handle to place the menu, for example main-menu or footer. "
                                        "Lowercase letters, numbers and hyphens.", cls="p-help")),
                            Input(type="hidden", name="version", value=site.version),
                            Input(type="hidden", name="items", value="[]"),
                            Input(type="hidden", name="action", value="create"),
                            submit="Add menu",
                            action=menus_path,
                            id="create-menu",
                            csrf=csrf(session),
                        ),
                        cls="gc-content o-operations",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/menus/{name}", methods=["GET"])
    def get(session, site_id: str, name: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                menu = site_menus.get_menu(db, site, name)
                if menu is None:
                    raise CommerceError("Menu not found.")
                locale = default_locale(site)
                pages = content.site_pages(db, site)
                titles = {page.path: page.title for page in pages}

                def target_text(item):
                    if item["kind"] == "external":
                        return item["url"]
                    title = titles.get(item["path"], item["path"])
                    return title + (" · section" if item["kind"] == "anchor" else "")

                def destination_options():
                    return [Option("Web address", value="")] + [
                        Option(f"{p.title} ({PAGE_TYPE_LABELS.get(p.kind, p.kind.title())})", value=p.path)
                        for p in pages
                    ]

                def icon_button(icon_name, label, **attrs):
                    return Button(lucide(icon_name, 16), type="button", aria_label=label, title=label,
                                  cls="gc-icon-button", **attrs)

                def item_row(item, depth=0):
                    label = resolve_text(item["label"], locale) if item else ""
                    data = {k: v for k, v in (item or {}).items() if k != "children"}
                    current = data.get("url") or data.get("path") or ""
                    return Li(
                        Div(
                            Span(lucide("grip-vertical", 16), cls="gc-drag", aria_hidden="true"),
                            Div(Strong(label, data_menu_label_text=""),
                                Small(target_text(item) if item else "", data_menu_target_text=""),
                                cls="gc-item-text"),
                            Div(
                                icon_button("arrow-up", "Move up", data_tree_action="up"),
                                icon_button("arrow-down", "Move down", data_tree_action="down"),
                                icon_button("indent-increase", "Nest under the item above", data_tree_action="indent"),
                                icon_button("indent-decrease", "Move out one level", data_tree_action="outdent"),
                                icon_button("pencil", "Edit " + label if label else "Edit", data_menu_edit="",
                                            aria_expanded="false"),
                                icon_button("trash-2", "Remove " + label if label else "Remove",
                                            data_tree_action="remove"),
                                cls="gc-item-actions",
                            ),
                            cls="gc-tree-row",
                        ),
                        Div(
                            Label("Label", Input(value=label, data_menu_label="", maxlength=200)),
                            Label("Link", Select(*[
                                Option(o.children[0], value=o.attrs["value"], selected=o.attrs["value"] == current)
                                for o in destination_options()], data_menu_link="")),
                            Label("Web address", Input(value=data.get("url", ""), data_menu_url="",
                                                       placeholder="https://", type="url"),
                                  hidden=data.get("kind") != "external", data_menu_url_field=""),
                            Div(Button("Done", type="button", data_menu_done="", cls="a-button"), cls="gc-edit-foot"),
                            cls="gc-item-edit", hidden=True,
                        ),
                        Ol(*[item_row(child, depth + 1) for child in (item or {}).get("children", [])], cls="gc-tree"),
                        draggable="true",
                        data_menu_item=json.dumps(data),
                    )

                form_id = "menu-form"
                actions = [
                    Button("Save draft", name="action", value="draft", form=form_id, cls="a-button"),
                    Button("Publish", name="action", value="publish", form=form_id, cls="a-button"),
                ]
                return shell(
                    menu_title(menu.name),
                    *assets(),
                    Div(
                        page_header(menu_title(menu.name), back_href=base(site.id) + "/menus",
                                    primary_action=actions[1], secondary_actions=[actions[0]],
                                    badges=[menu_status(menu)]),
                        P(notice, role="status", cls="gc-notice") if notice else None,
                        Form(
                            csrf(session),
                            Input(type="hidden", name="name", value=menu.name),
                            Input(type="hidden", name="version", value=site.version),
                            Textarea(json.dumps(menu.items_json), name="items", hidden=True, data_menu_json=""),
                            Div(
                                Div(H2("Menu items"),
                                    P("Drag items to reorder them, or drop one onto the right half of another to nest it.",
                                      cls="gc-card-help"), cls="gc-card-head"),
                                Div(
                                Ol(*[item_row(item) for item in menu.items_json], cls="gc-tree gc-tree-root",
                                   aria_label="Menu items"),
                                Div(
                                    Button(lucide("plus", 16), Span("Add menu item"), type="button",
                                           data_menu_add_open="", cls="gc-add-item"),
                                    Div(
                                        Label("Label", Input(data_new_label="", placeholder="Shop all", maxlength=200)),
                                        Label("Link", Select(*destination_options(), data_link_picker="")),
                                        Label("Web address", Input(data_new_url="", placeholder="https://", type="url"),
                                              data_new_url_field=""),
                                        Div(Button("Cancel", type="button", data_menu_add_cancel="", cls="a-button"),
                                            Button("Add", type="button", data_add_link="", cls="a-button a-button-primary"),
                                            cls="gc-edit-foot"),
                                        cls="gc-menu-add", hidden=True,
                                    ),
                                    cls="gc-add-wrap",
                                ),
                                cls="gc-tree-box",
                                ),
                                cls="a-card gc-menu-card",
                            ),
                            Template(item_row(None), data_menu_template=""),
                            # The save bar submits the form's first button, which keeps a draft.
                            Button("Save draft", name="action", value="draft", hidden=True, tabindex="-1"),
                            method="post",
                            data_dirty_guard="",
                            action=base(site.id) + "/menus",
                            id=form_id,
                            data_menu_form="",
                            data_menu_locale=locale,
                        ),
                        cls="gc-content gc-menu-detail o-operations",
                    ),
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/menus", methods=["POST"])
    async def post(session, request, site_id: str):
        data = await request.form()
        try:
            check_csrf(session, data)
            with SessionLocal() as db:
                user_id = actor(session)
                site = content.owned_site(
                    db, site_id, user_id, publish=data.get("action") == "publish"
                )
                site = builder.lock_site(db, site.id, user_id, int(data.get("version", -1)))
                before = builder.snapshot(db, site)
                if data.get("action") == "create" and site_menus.get_menu(db, site, str(data.get("name", ""))):
                    raise CommerceError("This menu already exists. Choose another name.")
                site_menus.put_menu(
                    db,
                    site,
                    str(data.get("name", "")),
                    json.loads(str(data.get("items", "[]"))),
                    publish=data.get("action") == "publish",
                )
                site.version += 1
                builder.record_change(db, site, user_id, before, "classical", "Menu tree updated")
                db.commit()
            saved = {"create": "Menu created.", "publish": "Menu published."}.get(str(data.get("action")), "Draft saved.")
            return RedirectResponse(base(site_id) + "/menus/" + site_menus.menu_name(str(data.get("name", "")))
                                    + "?" + urlencode({"notice": saved}), 303)
        except (CommerceError, ValueError) as exc:
            return error(CommerceError(str(exc)))

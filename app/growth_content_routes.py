"""Tenant-scoped content indexes, editorial forms, navigation and files."""

import json
from urllib.parse import urlencode

from fasthtml.common import (
    A,
    Button,
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
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Tr,
)
from starlette.responses import RedirectResponse

from app import content, growth_content, site_menus
from app import site_builder_services as builder
from app.admin_ui import badge, card, empty_state, page_header
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
    references,
)


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

    def navigation(site_id):
        return Div(
            *[
                A(label, href=base(site_id) + "/" + path, cls="a-button")
                for path, label in [
                    ("pages", "Pages"),
                    ("blog-posts", "Blog posts"),
                    ("menus", "Menus"),
                    ("files", "Files"),
                ]
            ],
            cls="a-actions",
        )

    def page_index(session, site_id, q, kind, status, notice, articles=False):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                pages = [
                    p
                    for p in content.site_pages(db, site)
                    if (p.kind == "article") == articles
                    and (not q or q.casefold() in (p.title + " " + p.path).casefold())
                    and (not kind or p.kind == kind)
                    and (not status or bool(p.published_json) == (status == "published"))
                ]
                rows = [
                    Tr(
                        Td(
                            Input(
                                type="checkbox",
                                name="page_id",
                                value=p.id,
                                aria_label="Select " + p.title,
                            )
                        ),
                        Td(
                            A(
                                p.title,
                                href=base(site.id) + "/blog-posts/" + p.id
                                if articles
                                else f"/admin/sites/{site.id}/editor?page={p.id}",
                            ),
                            Small(p.path),
                        ),
                        Td(
                            badge(
                                ("Scheduled" if articles and not is_listed(p) else "Published")
                                if p.published_json
                                else "Draft",
                                "success" if p.published_json else "neutral",
                            )
                        ),
                        Td(p.kind.title()),
                        Td(
                            A("Visual editor", href=f"/admin/sites/{site.id}/editor?page={p.id}"),
                            " · ",
                            A("Classic editor", href=f"/admin/sites/{site.id}/pages/{p.id}"),
                        ),
                    )
                    for p in pages
                ]
                create = (
                    A(
                        "Create blog post",
                        href=base(site.id) + "/blog-posts/new",
                        cls="a-button a-button-primary",
                    )
                    if articles
                    else None
                )
                return shell(
                    "Blog posts" if articles else "Pages",
                    *assets(),
                    page_header("Blog posts" if articles else "Pages", primary_action=create),
                    navigation(site.id),
                    P(notice, role="status") if notice else None,
                    Form(
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
                            "Kind",
                            Select(
                                Option("All kinds", value=""),
                                *[
                                    Option(k.title(), value=k, selected=k == kind)
                                    for k in sorted(content.PAGE_KINDS)
                                ],
                                name="kind",
                            ),
                        )
                        if not articles
                        else None,
                        Button("Filter", cls="a-button"),
                        method="get",
                        cls="gc-filters",
                    ),
                    Form(
                        csrf(session),
                        Div(
                            Table(
                                Thead(
                                    Tr(
                                        Th("Select"),
                                        Th("Title"),
                                        Th("Status"),
                                        Th("Kind"),
                                        Th("Edit"),
                                    )
                                ),
                                Tbody(*rows),
                            ),
                            cls="a-table-wrap a-card",
                        ),
                        Div(
                            Button(
                                "Publish selected", name="action", value="publish", cls="a-button"
                            ),
                            Button(
                                "Unpublish selected",
                                name="action",
                                value="unpublish",
                                cls="a-button",
                            ),
                            cls="a-actions",
                        ),
                        method="post",
                        action=base(site.id) + "/pages/bulk",
                    )
                    if pages
                    else empty_state("Start creating", "Your pages and posts appear here."),
                    card(
                        Form(
                            csrf(session),
                            Label("Page title", Input(name="title", required=True, maxlength=240)),
                            Label("Path", Input(name="path", required=True, placeholder="/about")),
                            Label(
                                "Kind",
                                Select(
                                    *[
                                        Option(k.title(), value=k)
                                        for k in ["content", "legal", "contact", "blog"]
                                    ],
                                    name="kind",
                                ),
                            ),
                            Button("Create page", cls="a-button a-button-primary"),
                            method="post",
                            action=base(site.id) + "/pages",
                            cls="gc-form",
                        ),
                        title="Create page",
                    )
                    if not articles
                    else None,
                )
        except CommerceError as exc:
            return error(exc)

    @rt("/admin/sites/{site_id}/content/pages", methods=["GET"])
    def get(session, site_id: str, q: str = "", kind: str = "", status: str = "", notice: str = ""):
        return page_index(session, site_id, q, kind, status, notice)

    @rt("/admin/sites/{site_id}/content/blog-posts", methods=["GET"])
    def get(session, site_id: str, q: str = "", status: str = "", notice: str = ""):
        return page_index(session, site_id, q, "", status, notice, True)

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
                                        readonly=bool(page),
                                    ),
                                ),
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
                library = [
                    m
                    for m in media_for(db, site)
                    if (not q or q.casefold() in (m.title + " " + m.alt).casefold())
                    and (not type or m.content_type.startswith(type))
                ]
                tiles = []
                for media in library:
                    used = references(db, site, media)
                    tiles.append(
                        card(
                            Img(src=media_url(media), alt=media.alt, loading="lazy")
                            if media.content_type.startswith("image/")
                            else P(media.content_type),
                            Form(
                                csrf(session),
                                Label(
                                    "Title", Input(name="title", value=media.title, maxlength=200)
                                ),
                                Label(
                                    "Alt text", Input(name="alt", value=media.alt, maxlength=400)
                                ),
                                Small(
                                    f"{media.size:,} bytes · "
                                    + ("Used: " + "; ".join(used[:3]) if used else "Unused")
                                ),
                                Div(
                                    Button(
                                        "Save details", name="action", value="save", cls="a-button"
                                    ),
                                    Button(
                                        "Delete",
                                        name="action",
                                        value="delete",
                                        disabled=bool(used),
                                        cls="a-button",
                                    ),
                                    cls="a-actions",
                                ),
                                method="post",
                                action=base(site.id) + "/files/" + media.id,
                                cls="gc-form",
                            ),
                        )
                    )
                return shell(
                    "Files",
                    *assets(),
                    page_header("Files"),
                    navigation(site.id),
                    P(notice, role="status") if notice else None,
                    Form(
                        Label("Search files", Input(name="q", value=q, type="search")),
                        Label(
                            "Type",
                            Select(
                                Option("All files", value=""),
                                Option("Images", value="image/", selected=type == "image/"),
                                Option("Videos", value="video/", selected=type == "video/"),
                                name="type",
                            ),
                        ),
                        Label(
                            "View",
                            Select(
                                Option("Grid", value="grid", selected=view == "grid"),
                                Option("List", value="list", selected=view == "list"),
                                name="view",
                            ),
                        ),
                        Button("Filter", cls="a-button"),
                        method="get",
                        cls="gc-filters",
                    ),
                    card(
                        Form(
                            csrf(session),
                            Label(
                                "Drop an image here or choose a file",
                                Input(type="file", name="file", accept="image/*", required=True),
                                cls="gc-dropzone",
                                data_dropzone="",
                            ),
                            Label("Title", Input(name="title", maxlength=200)),
                            Label("Alt text", Input(name="alt", required=True, maxlength=400)),
                            Button("Upload image", cls="a-button a-button-primary"),
                            method="post",
                            enctype="multipart/form-data",
                            action=base(site.id) + "/files",
                            cls="gc-form",
                        )
                    ),
                    Div(*tiles, cls="gc-files " + ("gc-list" if view == "list" else "")),
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

    @rt("/admin/sites/{site_id}/content/menus", methods=["GET"])
    def get(session, site_id: str, notice: str = ""):
        try:
            with SessionLocal() as db:
                site = content.owned_site(db, site_id, actor(session))
                pages = content.site_pages(db, site)

                def tree(items):
                    return Ol(
                        *[
                            Li(
                                Div(
                                    Button(
                                        "↕",
                                        type="button",
                                        aria_label="Drag "
                                        + resolve_text(item["label"], default_locale(site)),
                                        cls="a-button gc-drag",
                                    ),
                                    Input(
                                        value=resolve_text(item["label"], default_locale(site)),
                                        data_menu_label="",
                                        aria_label="Link label",
                                    ),
                                    *[
                                        Button(
                                            label,
                                            type="button",
                                            data_tree_action=action,
                                            cls="a-button",
                                        )
                                        for action, label in [
                                            ("up", "↑"),
                                            ("down", "↓"),
                                            ("indent", "Nest"),
                                            ("outdent", "Unnest"),
                                            ("remove", "Remove"),
                                        ]
                                    ],
                                    cls="gc-tree-row",
                                ),
                                tree(item.get("children", [])),
                                draggable="true",
                                data_menu_item=json.dumps(
                                    {k: v for k, v in item.items() if k != "children"}
                                ),
                            )
                            for item in items
                        ],
                        cls="gc-tree",
                    )

                cards = [
                    card(
                        Form(
                            csrf(session),
                            Input(type="hidden", name="name", value=menu.name),
                            Input(type="hidden", name="version", value=site.version),
                            Textarea(
                                json.dumps(menu.items_json),
                                name="items",
                                hidden=True,
                                data_menu_json="",
                            ),
                            tree(menu.items_json),
                            Div(
                                Label("Link label", Input(data_new_label="")),
                                Label(
                                    "Destination",
                                    Select(
                                        Option("Custom URL", value=""),
                                        *[
                                            Option(f"{p.kind.title()}: {p.title}", value=p.path)
                                            for p in pages
                                        ],
                                        data_link_picker="",
                                    ),
                                ),
                                Label(
                                    "URL", Input(data_new_url="", placeholder="https://example.com")
                                ),
                                Button("Add link", type="button", data_add_link="", cls="a-button"),
                                cls="gc-filters",
                            ),
                            Div(
                                Button("Save menu", name="action", value="draft", cls="a-button"),
                                Button(
                                    "Publish menu",
                                    name="action",
                                    value="publish",
                                    cls="a-button a-button-primary",
                                ),
                                cls="a-actions",
                            ),
                            method="post",
                            action=base(site.id) + "/menus",
                            data_menu_form="",
                            data_menu_locale=default_locale(site),
                        ),
                        title=menu.name.title(),
                    )
                    for menu in site_menus.menus_for(db, site)
                ]
                return shell(
                    "Menus",
                    *assets(),
                    page_header("Menus"),
                    navigation(site.id),
                    P(notice, role="status") if notice else None,
                    P(
                        "Drag links to reorder. Drop on the right side of a link to nest it. Use the arrow and Nest buttons with a keyboard."
                    ),
                    *cards,
                    card(
                        Form(
                            csrf(session),
                            Input(type="hidden", name="version", value=site.version),
                            Label(
                                "Menu name",
                                Input(
                                    name="name",
                                    required=True,
                                    maxlength=40,
                                    pattern=r"[a-z][a-z0-9\-]*",
                                ),
                            ),
                            Input(type="hidden", name="items", value="[]"),
                            Button("Create menu", name="action", value="create", cls="a-button"),
                            method="post",
                            action=base(site.id) + "/menus",
                        ),
                        title="Create menu",
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
            return redirect(site_id, "menus", "Menu saved.")
        except (CommerceError, ValueError) as exc:
            return error(CommerceError(str(exc)))

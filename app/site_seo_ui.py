"""Reusable merchant SEO fields and Google-style search previews."""

from fasthtml.common import (
    Div,
    Input,
    Label,
    NotStr,
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
    Strong,
    Textarea,
)

PREVIEW_SCRIPT = """(()=>{
const update=root=>{
 const title=root.querySelector('[data-seo-title-input]');
 const description=root.querySelector('[data-seo-description-input]');
 const preview=root.querySelector('[data-seo-preview]');
 if(!title||!description||!preview)return;
 const base=title.value.trim()||preview.dataset.baseTitle||'';
 const format=preview.dataset.titleFormat||'{page} | {store}';
 const output=preview.dataset.home==='true'?base:format.replaceAll('{page}',base).replaceAll('{store}',preview.dataset.store||'');
 root.querySelector('[data-seo-preview-title]').textContent=output;
 root.querySelector('[data-seo-preview-description]').textContent=description.value.trim()||preview.dataset.baseDescription||'';
 const titleCount=root.querySelector('[data-seo-title-count]');
 const descriptionCount=root.querySelector('[data-seo-description-count]');
 titleCount.textContent=String(title.value.length);
 descriptionCount.textContent=String(description.value.length);
 const titleWarning=root.querySelector('[data-seo-title-warning]');
 const descriptionWarning=root.querySelector('[data-seo-description-warning]');
 titleWarning.hidden=title.value.length<=60;
 descriptionWarning.hidden=description.value.length<=160;
};
document.addEventListener('input',event=>{const root=event.target.closest('[data-seo-editor]');if(root)update(root)});
document.addEventListener('change',event=>{const root=event.target.closest('[data-seo-editor]');if(root)update(root)});
document.querySelectorAll('[data-seo-editor]').forEach(update);
})();"""


def seo_editor_fields(
    site,
    *,
    title: str,
    path: str,
    seo_title: str = "",
    seo_description: str = "",
    social_title: str = "",
    social_image: str = "",
    noindex: bool = False,
    images=(),
    default_description: str = "",
    home: bool = False,
):
    """Render shared entity fields inside the caller's existing form and SEO card."""
    title = str(title or "")
    seo_title = str(seo_title or "")
    seo_description = str(seo_description or "")
    social_title = str(social_title or "")
    social_image = str(social_image or "")
    format_value = (getattr(site, "settings_json", None) or {}).get(
        "seo_title_format", "{page} | {store}",
    )
    if path == "/":
        preview_title = seo_title or title
    else:
        preview_title = format_value.replace("{page}", seo_title or title).replace("{store}", site.name)
    host = site.hostname or "your-store.example"
    preview_url = host + (path if path.startswith("/") else "/" + path)
    image_rows = [image for image in images if str(getattr(image, "content_type", "image/")).startswith("image/")]
    image_options = [
        Option(
            image.title or image.alt or "Store image",
            value=image.public_url or f"/site-media/{site.id}/{image.id}",
            selected=(image.public_url or f"/site-media/{site.id}/{image.id}") == social_image,
        )
        for image in image_rows
    ]
    return Div(
        Div(
            Small("Google search preview", cls="c-muted"),
            Div(
                Strong(preview_title, data_seo_preview_title=""),
                P(preview_url, data_seo_preview_url=""),
                P(seo_description or default_description, data_seo_preview_description=""),
                cls="c-seo-preview",
            ),
            data_seo_preview="",
            data_base_title=title,
            data_title_format=format_value,
            data_store=site.name,
            data_home="true" if home else "false",
            data_base_description=default_description,
        ),
        Label("SEO title", Input(
            name="seo_title", value=seo_title, maxlength=240,
            data_seo_title_input="",
        )),
        Small(
            Span(str(len(seo_title)), data_seo_title_count=""),
            " / 60 characters recommended. ",
            Span("Long titles may be shortened in search results.", hidden=not (len(seo_title) > 60),
                 data_seo_title_warning="", role="status"),
            cls="c-muted",
        ),
        Label("Meta description", Textarea(
            seo_description, name="seo_description", rows=3, maxlength=320,
            data_seo_description_input="",
        )),
        Small(
            Span(str(len(seo_description)), data_seo_description_count=""),
            " / 160 characters recommended. ",
            Span("Long descriptions may be shortened in search results.",
                 hidden=not (len(seo_description) > 160),
                 data_seo_description_warning="", role="status"),
            cls="c-muted",
        ),
        Label("Social title", Input(name="seo_social_title", value=social_title, maxlength=240)),
        Label(
            "Social image",
            Select(
                Option("Use store default", value="", selected=not social_image),
                *image_options,
                name="seo_social_image",
            ),
        ),
        Label(
            Input(type="checkbox", name="seo_noindex", checked=bool(noindex)),
            " Hide from search engines",
        ),
        Script(NotStr(PREVIEW_SCRIPT)),
        data_seo_editor="",
    )


def seo_home_preview(site, title: str, description: str, default_description: str = ""):
    """Render the home page search preview and advisory counters for store SEO settings."""
    title = str(title or "")
    description = str(description or "")
    host = site.hostname or "your-store.example"
    return Div(
        Small("Google search preview", cls="c-muted"),
        Div(
            Strong(title or site.name, data_seo_preview_title=""),
            P(host + "/", data_seo_preview_url=""),
            P(description or default_description, data_seo_preview_description=""),
            cls="c-seo-preview",
            data_seo_preview="",
            data_base_title=title or site.name,
            data_title_format="{page} | {store}",
            data_store=site.name,
            data_home="true",
            data_base_description=default_description,
        ),
        Label("Home page title", Input(
            name="home_title", value=title, maxlength=240, data_seo_title_input="",
        )),
        Small(
            Span(str(len(title)), data_seo_title_count=""),
            " / 60 characters recommended. ",
            Span("Long titles may be shortened in search results.", hidden=not (len(title) > 60),
                 data_seo_title_warning="", role="status"),
            cls="c-muted",
        ),
        Label("Home page meta description", Textarea(
            description, name="home_description", rows=3, maxlength=320,
            data_seo_description_input="",
        )),
        Small(
            Span(str(len(description)), data_seo_description_count=""),
            " / 160 characters recommended. ",
            Span("Long descriptions may be shortened in search results.",
                 hidden=not (len(description) > 160),
                 data_seo_description_warning="", role="status"),
            cls="c-muted",
        ),
        Script(NotStr(PREVIEW_SCRIPT)),
        data_seo_editor="",
    )

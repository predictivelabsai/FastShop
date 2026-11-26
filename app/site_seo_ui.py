"""Reusable merchant SEO fields and search result previews."""

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

from app.site_seo import DEFAULT_TITLE_FORMAT

PREVIEW_SCRIPT = """(()=>{
if(window.fsSeoPreview)return;window.fsSeoPreview=true;
const update=root=>{
 const title=root.querySelector('[data-seo-title-input]');
 const description=root.querySelector('[data-seo-description-input]');
 const preview=root.querySelector('[data-seo-preview]');
 if(!title||!description||!preview)return;
 const own=title.value.trim();
 const base=own||preview.dataset.baseTitle||'';
 const formatInput=document.querySelector('[data-seo-format-input]');const format=formatInput?(formatInput.value.trim()||formatInput.dataset.defaultFormat):preview.dataset.titleFormat;
 const store=preview.dataset.store||'';
 const named=store&&base.toLowerCase().endsWith(store.toLowerCase());
 const output=(preview.dataset.home==='true'&&own)||named?base:format.replaceAll('{page}',base).replaceAll('{store}',store);
 root.querySelector('[data-seo-preview-title]').textContent=output;
 root.querySelector('[data-seo-preview-description]').textContent=description.value.trim()||preview.dataset.baseDescription||'';
 root.querySelector('[data-seo-title-count]').textContent=String(title.value.length);
 root.querySelector('[data-seo-description-count]').textContent=String(description.value.length);
 root.querySelector('[data-seo-title-warning]').hidden=title.value.length<=60;
 root.querySelector('[data-seo-description-warning]').hidden=description.value.length<=160;
};
const refresh=event=>{if(event.target.matches('[data-seo-format-input]')){const sample=document.querySelector('[data-seo-format-preview]');if(sample)sample.textContent=(event.target.value.trim()||sample.dataset.defaultFormat).replaceAll('{page}','Example page').replaceAll('{store}',sample.dataset.store);document.querySelectorAll('[data-seo-editor]').forEach(update);return}const root=event.target.closest('[data-seo-editor]');if(root)update(root)};
document.addEventListener('input',refresh);
document.addEventListener('change',refresh);
document.querySelectorAll('[data-seo-editor]').forEach(update);
})();"""


def title_format(site) -> str:
    return (getattr(site, "settings_json", None) or {}).get("seo_title_format") or DEFAULT_TITLE_FORMAT


def _preview_title(site, title, seo_title, *, home):
    own = seo_title or title
    if (home and seo_title) or (site.name and own.rstrip().lower().endswith(site.name.lower())):
        return own
    return title_format(site).replace("{page}", own).replace("{store}", site.name)


def _counters(name, value, limit, noun):
    return Small(
        Span(str(len(value)), **{f"data_seo_{name}_count": ""}),
        f" / {limit} characters recommended. ",
        Span(f"Long {noun} may be shortened in search results.", hidden=not (len(value) > limit),
             role="status", **{f"data_seo_{name}_warning": ""}),
        cls="c-muted",
    )


def _image_value(site, image):
    from app.site_media import media_url

    return media_url(image) or f"/site-media/{site.id}/{image.id}"


def image_options(site, images, selected, empty_label):
    rows = [image for image in images if str(getattr(image, "content_type", "image/")).startswith("image/")]
    return [Option(empty_label, value="", selected=not selected)] + [
        Option(image.title or image.alt or "Store image", value=_image_value(site, image),
               selected=_image_value(site, image) == selected)
        for image in rows
    ]


def search_preview(site, *, title, path, seo_title="", description="", default_description="", home=False):
    from app.site_seo import site_origin

    host = site_origin(site).split("://", 1)[-1]
    preview_url = host + (path if path.startswith("/") else "/" + path)
    return Div(
        Small("Google search preview", cls="c-muted"),
        Div(
            Strong(_preview_title(site, title, seo_title, home=home), data_seo_preview_title=""),
            P(preview_url, data_seo_preview_url=""),
            P(description or default_description, data_seo_preview_description=""),
            cls="c-seo-preview",
        ),
        data_seo_preview="",
        data_base_title=title,
        data_title_format=title_format(site),
        data_store=site.name,
        data_home="true" if home else "false",
        data_base_description=default_description,
    )


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
    title, seo_title = str(title or ""), str(seo_title or "")
    seo_description, social_title = str(seo_description or ""), str(social_title or "")
    social_image = str(social_image or "")
    return Div(
        search_preview(site, title=title, path=path, seo_title=seo_title, description=seo_description,
                       default_description=default_description, home=home),
        Label("SEO title", Input(name="seo_title", value=seo_title, maxlength=240,
                                 data_seo_title_input="", placeholder=title)),
        _counters("title", seo_title, 60, "titles"),
        Label("Meta description", Textarea(seo_description, name="seo_description", rows=3, maxlength=320,
                                           data_seo_description_input="")),
        _counters("description", seo_description, 160, "descriptions"),
        Label("Social sharing title", Input(name="seo_social_title", value=social_title, maxlength=240)),
        Label("Social sharing image", Select(*image_options(site, images, social_image, "Use store default"),
                                             name="seo_social_image")),
        Label(Input(type="checkbox", name="seo_noindex", checked=bool(noindex)), " Hide from search engines"),
        Script(NotStr(PREVIEW_SCRIPT)),
        data_seo_editor="",
    )


def seo_home_preview(site, title: str, description: str, *, page_title: str = "", default_description: str = ""):
    """The home page search preview with its title and description fields."""
    title, description = str(title or ""), str(description or "")
    return Div(
        search_preview(site, title=page_title or site.name, path="/", seo_title=title,
                       description=description, default_description=default_description, home=True),
        Label("Home page title", Input(name="home_title", value=title, maxlength=240,
                                       data_seo_title_input="", placeholder=page_title or site.name)),
        _counters("title", title, 60, "titles"),
        Label("Home page meta description", Textarea(description, name="home_description", rows=3,
                                                     maxlength=320, data_seo_description_input="")),
        _counters("description", description, 160, "descriptions"),
        Script(NotStr(PREVIEW_SCRIPT)),
        data_seo_editor="",
    )

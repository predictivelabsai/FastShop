"""Full-screen merchant visual editor, using the shared admin design tokens."""
import json
import re
from html import escape

from fastcore.xml import NotStr
from fasthtml.common import Link, Meta, Script, Title

from app.admin_icons import svg as icon_svg
from app.rich_text_ui import rich_text_template
from app.ui import static_asset


def editor_features(site, page):
    """Optional editor surfaces this installation supports.

    The front end hides any control whose backend is absent, so a store without an
    assistant or a dedicated SEO page never sees a control that errors.
    """
    return {"assistant": False, "seo_href": ""}


def editor_page(site, page, csrf_token):
    """Render the editor chrome; all owned data is loaded through its scoped API."""
    features = editor_features(site, page)
    config = json.dumps({"site_id": str(site.id), "page_id": str(page.id),
                         "theme_id": getattr(site, "theme_context_id", ""),
                         "csrf_token": csrf_token, "features": features},
                        ensure_ascii=True).replace("<", "\\u003c")
    title = escape(str(getattr(site, "name", "Store")))
    seo_link = ('<a id="seo-link" class="icon-link" href="SEO_HREF" aria-label="Search engine optimisation" '
                'title="Search engine optimisation">{search}</a>' if features["seo_href"] else "")
    assistant_button = ('<button data-mode="assistant" aria-pressed="false" title="Assistant (Ctrl+I)" '
                        'aria-label="Assistant">{sparkles}</button>' if features["assistant"] else "")
    markup = '''
<div class="a-shell editor-shell" id="editor" data-editor-mode="sections" aria-label="Visual page editor">
<header class="editor-topbar">
 <a class="exit-link" href="/admin/sites/SITE" aria-label="Exit to admin" title="Exit to admin">{arrow-left}</a>
 <div class="editor-brand"><strong>STORE</strong><span>Theme editor <span class="live-badge">Draft</span></span></div>
 <details class="page-switcher"><summary aria-label="Switch the page you are editing"><span class="page-switcher-label">Editing</span><span id="current-page">Choose page</span>{chevron-down}</summary><div class="page-menu"><div class="page-menu-heading"><strong>Switch page</strong><a id="manage-pages" href="/admin/sites/SITE/pages">Manage pages</a></div><input id="page-search" type="search" placeholder="Search pages" aria-label="Search pages"><div id="page-options"></div></div></details>
 <div class="device-controls" role="group" aria-label="Preview device"><button data-device="desktop" aria-pressed="true" title="Desktop preview" aria-label="Desktop preview">{monitor}</button><button data-device="tablet" aria-pressed="false" title="Tablet preview" aria-label="Tablet preview">{tablet}</button><button data-device="mobile" aria-pressed="false" title="Mobile preview" aria-label="Mobile preview">{smartphone}</button></div>
 <div class="top-actions"><button id="undo" class="icon-button" aria-label="Undo" title="Undo (Ctrl Z)">{undo-2}</button><button id="redo" class="icon-button" aria-label="Redo" title="Redo (Ctrl Shift Z)">{redo-2}</button><span class="top-divider" aria-hidden="true"></span>SEO_LINK<a id="preview-link" class="icon-link" target="_blank" rel="noopener" aria-label="Preview in new tab" title="Preview in new tab">{external-link}</a><button id="revisions" class="icon-button revisions-button" title="Saved revisions" aria-label="Saved revisions">{file-clock}<span>Revisions</span></button><span id="save-status" role="status" aria-live="polite">Loading…</span><button id="save">Save</button><button id="publish" class="publish-button">Publish</button></div>
</header>
<div id="editor-error" role="alert" hidden></div>
<div class="editor-layout">
<nav class="editor-rail" aria-label="Editor tools"><button id="mobile-preview" class="mobile-preview" title="Preview page" aria-label="Preview page">{eye}</button><button data-mode="sections" class="active" aria-pressed="true" title="Sections" aria-label="Sections">{layers}</button><button data-mode="widgets" aria-pressed="false" title="Widgets" aria-label="Widgets">{blocks}</button><button data-mode="theme" aria-pressed="false" title="Theme settings" aria-label="Theme settings">{palette}</button><button data-mode="history" aria-pressed="false" title="Change history" aria-label="History">{history}</button>ASSISTANT_BUTTON<a href="/admin/sites/SITE/pages/PAGE" title="Classic editor" aria-label="Classic editor">{square-pen}</a></nav>
<aside class="editor-left" id="editor-left" aria-label="Sections and widgets"><div class="panel-heading"><div class="panel-heading-text"><h1 id="left-title">Sections</h1><span class="subtle" id="theme-name">Store theme</span></div><div class="panel-heading-actions" id="left-actions"></div></div><div id="left-content"></div></aside>
<main class="editor-stage" aria-label="Page preview"><div class="canvas-caption"><span id="canvas-page-title">Live preview</span><span id="canvas-device">Desktop</span></div><div class="canvas-well"><iframe id="editor-canvas" title="Storefront page canvas" sandbox="allow-scripts allow-same-origin"></iframe></div><div class="canvas-footer"><span>Click to select, double-click text to edit</span><span id="node-count"></span></div></main>
<aside class="editor-right" id="editor-right" aria-label="Block settings"><div id="selection-header" class="panel-heading"><h2>Page settings</h2><span class="subtle">Select a section to start</span></div><div id="settings-content"></div></aside>
<div class="panel-resizer" id="resize-left" data-resize="left" role="separator" tabindex="0" aria-orientation="vertical" aria-controls="editor-left" aria-label="Resize the left panel" title="Drag to resize. Double-click to reset."></div>
<div class="panel-resizer" id="resize-right" data-resize="right" role="separator" tabindex="0" aria-orientation="vertical" aria-controls="editor-right" aria-label="Resize the settings panel" title="Drag to resize. Double-click to reset."></div>
</div>
<dialog id="picker-dialog"><div class="dialog-heading"><h2 id="picker-title">Add section</h2><button data-close-dialog class="icon-button" aria-label="Close">{x}</button></div><input id="picker-search" type="search" placeholder="Search sections and widgets" aria-label="Search sections and widgets"><div id="picker-results"></div></dialog>
<dialog id="media-dialog"><div class="dialog-heading"><h2>Media library</h2><button data-close-dialog class="icon-button" aria-label="Close">{x}</button></div><label class="upload-label">{upload}<strong>Add image</strong><span>Drop an image here, or click to browse</span><small>JPEG, PNG or WebP</small><input id="media-upload" type="file" aria-label="Upload image" accept="image/jpeg,image/png,image/webp"></label><div id="media-grid"></div></dialog>
<dialog id="icon-dialog"><div class="dialog-heading"><h2>Choose an icon</h2><button data-close-dialog class="icon-button" aria-label="Close">{x}</button></div><p class="icon-picker-intro">Search the built-in library, choose an icon from this site’s media, or upload an SVG, PNG or WebP icon.</p><input id="icon-search" type="search" placeholder="Search icons" aria-label="Search icons" aria-controls="icon-grid"><label id="icon-upload-label" class="upload-label icon-upload-label">{upload}<strong>Upload custom icon</strong><span>SVG is sanitised before it is stored</span><small>SVG, PNG or WebP</small><input id="icon-upload" type="file" aria-label="Upload custom icon" accept="image/svg+xml,image/png,image/webp,.svg"></label><p id="icon-results-summary" class="icon-results-summary" role="status" aria-live="polite"></p><div id="icon-grid" class="icon-grid" role="group" aria-label="Built-in icons"></div><section id="custom-icon-section" aria-labelledby="custom-icon-heading"><h3 id="custom-icon-heading">Your site media</h3><div id="custom-icon-grid" class="icon-grid icon-grid-custom" role="group" aria-label="Custom icons"></div></section></dialog>
<dialog id="link-dialog"><div class="dialog-heading"><h2>Choose a link</h2><button data-close-dialog class="icon-button" aria-label="Close">{x}</button></div><input id="link-search" type="search" placeholder="Search pages or enter a URL" aria-label="Link search"><div id="link-results"></div><button id="link-apply">Use URL</button></dialog>
<dialog id="revision-dialog"><div class="dialog-heading"><h2>Saved revisions</h2><button data-close-dialog class="icon-button" aria-label="Close">{x}</button></div><p class="subtle">Restore a previous version as a draft. Your published page stays live.</p><div id="revision-list"></div></dialog>
<dialog id="publish-dialog"><div class="dialog-heading"><h2>Publish this page?</h2><button data-close-dialog class="icon-button" aria-label="Close">{x}</button></div><p>Your latest saved changes will become visible on your storefront.</p><div class="dialog-actions"><button id="confirm-publish" class="publish-button">Publish page</button></div></dialog>
<dialog id="conflict-dialog"><h2>A newer draft is available</h2><p>Someone has saved this page since you opened it. Reload their version, or replace it with your current changes.</p><div class="dialog-actions"><button id="conflict-reload">Reload latest</button><button id="conflict-overwrite">Overwrite with my changes</button></div></dialog>
</div>'''
    markup = markup.replace("SEO_LINK", seo_link).replace("ASSISTANT_BUTTON", assistant_button)
    # {name} placeholders become inline Lucide icons that follow currentColor.
    markup = re.sub(r"\{([a-z0-9-]+)\}", lambda match: icon_svg(match.group(1), 18, 1.75), markup)
    markup = markup.replace("SITE", escape(str(site.id))).replace("PAGE", escape(str(page.id))).replace(
        "STORE", title,
    ).replace("SEO_HREF", escape(features["seo_href"]))
    return (Link(rel="icon", href=static_asset("favicon.svg")), Title(f"Theme editor · {title}"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Link(rel="stylesheet", href=static_asset("admin/admin.css")),
            Link(rel="stylesheet", href=static_asset("editor/editor.css")),
            Link(rel="stylesheet", href=static_asset("editor/dynamic-sources.css")), NotStr(markup),
            rich_text_template(),
            Script(NotStr(f"window.FASTSHOP_EDITOR={config};")),
            Script(src=static_asset("admin/icons.js")),
            Script(src="/static/admin/rich-text.js", type="module"),
            Script(src=static_asset("editor/editor.js"), type="module"))

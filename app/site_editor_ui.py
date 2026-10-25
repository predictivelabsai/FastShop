"""Full-screen merchant visual editor, using the shared admin design tokens."""
import json
from html import escape

from fastcore.xml import NotStr
from fasthtml.common import Link, Meta, Script, Title


def editor_page(site, page, csrf_token):
    """Render the editor chrome; all owned data is loaded through its scoped API."""
    config = json.dumps({"site_id": str(site.id), "page_id": str(page.id),
                         "csrf_token": csrf_token}, ensure_ascii=True).replace("<", "\\u003c")
    title = escape(str(getattr(site, "name", "Store")))
    markup = '''
<div class="a-shell editor-shell" id="editor" aria-label="Visual page editor">
<header class="editor-topbar">
 <a class="exit-link" href="/admin/sites/SITE" aria-label="Exit to admin">←</a>
 <div class="editor-brand"><strong>STORE</strong><span>Theme editor <span class="live-badge">Draft</span></span></div>
 <details class="page-switcher"><summary id="current-page">Choose page</summary><div class="page-menu"><input id="page-search" type="search" placeholder="Search pages" aria-label="Search pages"><div id="page-options"></div></div></details>
 <div class="device-controls" role="group" aria-label="Preview device"><button data-device="desktop" aria-pressed="true" title="Desktop preview">▱<span>Desktop</span></button><button data-device="tablet" aria-pressed="false" title="Tablet preview">▯<span>Tablet</span></button><button data-device="mobile" aria-pressed="false" title="Mobile preview">▯<span>Mobile</span></button></div>
 <div class="top-actions"><button id="undo" aria-label="Undo" title="Undo (Ctrl Z)">↶</button><button id="redo" aria-label="Redo" title="Redo (Ctrl Shift Z)">↷</button><a id="preview-link" class="icon-link" target="_blank" rel="noopener" aria-label="Preview in new tab" title="Preview in new tab">↗</a><button id="revisions" title="Revisions">History</button><span id="save-status" role="status" aria-live="polite">Loading…</span><button id="save">Save</button><button id="publish" class="publish-button">Publish</button></div>
</header>
<div id="editor-error" role="alert" hidden></div>
<div class="editor-layout">
<nav class="editor-rail" aria-label="Editor tools"><button id="mobile-preview" class="mobile-preview" title="Preview page" aria-label="Preview page">▱</button><button data-mode="sections" class="active" title="Sections" aria-label="Sections">☷</button><button data-mode="widgets" title="Widgets" aria-label="Widgets">⊞</button><button data-mode="theme" title="Theme settings" aria-label="Theme settings">◐</button><button data-mode="history" title="History" aria-label="History">↶</button><a href="/admin/sites/SITE/pages/PAGE" title="Classic editor" aria-label="Classic editor">⌘</a></nav>
<aside class="editor-left" aria-label="Sections and widgets"><div class="panel-heading"><h1 id="left-title">Sections</h1><span class="subtle" id="theme-name">Store theme</span></div><div id="left-content"></div></aside>
<main class="editor-stage" aria-label="Page preview"><div class="canvas-caption"><span id="canvas-page-title">Live preview</span><span id="canvas-device">Desktop</span></div><div class="canvas-well"><iframe id="editor-canvas" title="Storefront page canvas" sandbox="allow-scripts allow-same-origin"></iframe></div><div class="canvas-footer"><span>Click to select · Double-click text to edit</span><span id="node-count"></span></div></main>
<aside class="editor-right" aria-label="Block settings"><div id="selection-header" class="panel-heading"><h2>Page settings</h2><span class="subtle">Select a section to start</span></div><div class="settings-tabs" role="tablist"><button data-tab="Content" role="tab" aria-selected="true">Content</button><button data-tab="Style" role="tab" aria-selected="false">Style</button><button data-tab="Advanced" role="tab" aria-selected="false">Advanced</button></div><div id="settings-content"></div></aside>
</div>
<dialog id="picker-dialog"><div class="dialog-heading"><h2 id="picker-title">Add section</h2><button data-close-dialog aria-label="Close">×</button></div><input id="picker-search" type="search" placeholder="Search sections and widgets" aria-label="Search sections and widgets"><div id="picker-results"></div></dialog>
<dialog id="media-dialog"><div class="dialog-heading"><h2>Media library</h2><button data-close-dialog aria-label="Close">×</button></div><label class="upload-label">Upload image<input id="media-upload" type="file" accept="image/jpeg,image/png,image/webp"></label><div id="media-grid"></div></dialog>
<dialog id="link-dialog"><div class="dialog-heading"><h2>Choose a link</h2><button data-close-dialog aria-label="Close">×</button></div><input id="link-search" type="search" placeholder="Search pages or enter a URL" aria-label="Link search"><div id="link-results"></div><button id="link-apply">Use URL</button></dialog>
<dialog id="revision-dialog"><div class="dialog-heading"><h2>Saved revisions</h2><button data-close-dialog aria-label="Close">×</button></div><p class="subtle">Restore a previous version as a draft. Your published page stays live.</p><div id="revision-list"></div></dialog>
<dialog id="publish-dialog"><div class="dialog-heading"><h2>Publish this page?</h2><button data-close-dialog aria-label="Close">×</button></div><p>Your latest saved changes will become visible on your storefront.</p><button id="confirm-publish" class="publish-button">Publish page</button></dialog>
<dialog id="conflict-dialog"><h2>A newer draft is available</h2><p>Someone has saved this page since you opened it. Reload their version, or replace it with your current changes.</p><div class="dialog-actions"><button id="conflict-reload">Reload latest</button><button id="conflict-overwrite">Overwrite with my changes</button></div></dialog>
</div>'''.replace("SITE", escape(str(site.id))).replace("PAGE", escape(str(page.id))).replace("STORE", title)
    return (Title(f"Theme editor · {title}"), Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Link(rel="stylesheet", href="/static/admin/admin.css"),
            Link(rel="stylesheet", href="/static/editor/editor.css"), NotStr(markup),
            Script(NotStr(f"window.FASTSHOP_EDITOR={config};")),
            Script(src="/static/editor/editor.js", type="module"))

"""Server-rendered rich-text editor controls shared across merchant forms."""

from __future__ import annotations

import re

from fasthtml.common import Button, Div, Input, Label, NotStr, Option, Select, Textarea

from app.rich_text import sanitise_rich_text

_ICONS = {
    "bold": '<path d="M7 5h6a4 4 0 0 1 0 8H7zM7 13h7a4 4 0 0 1 0 8H7z"/>',
    "italic": '<path d="M14 4h6M4 20h6M15 4 9 20"/>',
    "underline": '<path d="M7 4v7a5 5 0 0 0 10 0V4M5 21h14"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.1 0l3-3a5 5 0 0 0-7.1-7.1l-1.7 1.7M14 11a5 5 0 0 0-7.1 0l-3 3a5 5 0 0 0 7.1 7.1l1.7-1.7"/>',
    "unlink": '<path d="M10 13a5 5 0 0 0 7.1 0l2-2M14 11a5 5 0 0 0-7.1 0l-2 2M3 3l18 18M13 4l1-1a5 5 0 0 1 7 7l-1 1M11 20l-1 1a5 5 0 0 1-7-7l1-1"/>',
    "bullets": '<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
    "numbers": '<path d="M10 6h11M10 12h11M10 18h11M3 5h2v4M3 9h3M3 13c0-1 3-1 3 0 0 1-3 2-3 3h3M3 17h2a1 1 0 1 1 0 2H3m2 0a1 1 0 1 1 0 2H3"/>',
    "quote": '<path d="M4 5h7v7H4zM13 5h7v7h-7zM4 12c0 4-1 6-3 7M13 12c0 4-1 6-3 7"/>',
    "rule": '<path d="M3 12h18M6 8v8M18 8v8"/>',
    "clear": '<path d="m4 7 5-4 12 12-5 5zM3 21h18M12 7l-5 5"/>',
    "undo": '<path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3"/>',
    "redo": '<path d="m15 14 5-5-5-5m5 5H10a6 6 0 0 0 0 12h3"/>',
    "html": '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>',
}


def _icon(name: str):
    return NotStr(
        '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + _ICONS[name]
        + "</svg>"
    )


def _tool(command: str, label: str, icon: str):
    return Button(
        _icon(icon),
        SpanLabel(label),
        type="button",
        data_rich_command=command,
        aria_label=label,
        title=label,
        aria_pressed="false",
    )


def SpanLabel(label: str):
    return NotStr(f'<span class="rt-visually-hidden">{label}</span>')


def _toolbar():
    return Div(
        Div(
            Select(
                Option("Paragraph", value="p"),
                Option("Heading 2", value="h2"),
                Option("Heading 3", value="h3"),
                Option("Heading 4", value="h4"),
                data_rich_block="",
                aria_label="Paragraph or heading style",
                title="Paragraph or heading style",
            ),
            cls="rt-tool-group rt-block-style",
        ),
        Div(
            _tool("bold", "Bold", "bold"),
            _tool("italic", "Italic", "italic"),
            _tool("underline", "Underline", "underline"),
            cls="rt-tool-group",
        ),
        Div(
            Button(
                _icon("link"), SpanLabel("Link"), type="button", data_rich_link_open="",
                aria_label="Insert or edit link", title="Insert or edit link", aria_expanded="false",
            ),
            _tool("unlink", "Remove link", "unlink"),
            Div(
                Label("Link address", Input(type="text", data_rich_url="", autocomplete="url",
                                              placeholder="https:// or /page")),
                Label(Input(type="checkbox", data_rich_target=""), " Open in new tab"),
                Div(
                    Button("Apply link", type="button", data_rich_link_apply=""),
                    Button("Cancel", type="button", data_rich_link_cancel=""),
                    cls="rt-link-actions",
                ),
                PError(),
                cls="rt-link-popover",
                data_rich_link_popover="",
                hidden=True,
            ),
            cls="rt-tool-group rt-link-group",
        ),
        Div(
            _tool("insertUnorderedList", "Bulleted list", "bullets"),
            _tool("insertOrderedList", "Numbered list", "numbers"),
            _tool("formatBlock:blockquote", "Quote", "quote"),
            _tool("insertHorizontalRule", "Horizontal rule", "rule"),
            cls="rt-tool-group",
        ),
        Div(
            _tool("clear", "Clear formatting", "clear"),
            _tool("undo", "Undo", "undo"),
            _tool("redo", "Redo", "redo"),
            Button(
                _icon("html"), "HTML", type="button", data_rich_source_toggle="",
                aria_label="Toggle HTML source", title="Toggle HTML source", aria_pressed="false",
            ),
            cls="rt-tool-group",
        ),
        role="toolbar",
        aria_label="Text formatting",
        cls="rt-toolbar",
    )


def PError():
    return NotStr('<p class="rt-link-error" role="alert" hidden></p>')


def rich_text_editor(
    value: str = "",
    name: str = "",
    label: str = "",
    *,
    editor_id: str = "",
    setting_path: str = "",
    template: bool = False,
    placeholder: str = "",
):
    """Render the common toolbar, editable surface, HTML source and form value."""
    value = sanitise_rich_text(str(value or ""))
    editor_id = editor_id or ("rich-" + re.sub(r"[^a-zA-Z0-9_-]", "-", name) if name else "")
    content_attrs = {
        "contenteditable": "true",
        "role": "textbox",
        "aria_multiline": "true",
        "aria_label": label or "Rich text editor",
        "data_rich_content": "",
        "cls": "rt-content",
        "id": editor_id + "-content" if editor_id else None,
        "data_placeholder": placeholder or None,
    }
    value_attrs = {
        "type": "hidden",
        "name": name,
        "value": value,
        "data_rich_value": "",
        "data_type": "richtext" if setting_path else None,
        "data_setting": setting_path or None,
    }
    return Div(
        Label(label, cls="rt-label") if label else None,
        _toolbar(),
        Div(
            Div(NotStr(value), **content_attrs),
            Textarea(value, data_rich_source="", aria_label=f"{label or 'Rich text'} HTML source", hidden=True),
            Input(**value_attrs),
            cls="rt-editing-surface",
        ),
        cls="admin-rich-text",
        data_rich_text="",
        data_rich_text_template="true" if template else None,
    )


def rich_text_template():
    """Provide the same editor markup as a clone source for dynamic inspector fields."""
    return Div(
        rich_text_editor(template=True),
        id="rich-text-template-holder",
        hidden=True,
        aria_hidden="true",
    )

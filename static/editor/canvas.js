import "/static/admin/rich-text.js";

/* Same-origin editor bridge. Storefront links and purchasing are disabled here. */
(() => {
  "use strict";
  const configNode = document.getElementById("editor-canvas-config");
  if (!configNode || window.parent === window) return;
  let config;
  try {
    config = JSON.parse(configNode.textContent);
  } catch {
    return;
  }
  const siteId = String(config.siteId),
    pageId = String(config.pageId),
    editorOrigin = parent.location.origin;
  const send = (data) =>
    parent.postMessage(
      { source: "fastshop-editor", site_id: siteId, page_id: pageId, ...data },
      editorOrigin,
    );
  let selected = null,
    dragPayload = null,
    editing = null,
    dropPosition = "after",
    dropTarget = null;
  const style = document.createElement("style");
  style.textContent = `
 .fs-editor-tools[hidden],.fs-rich-tools[hidden]{display:none!important}[data-block-id]{position:relative;min-height:24px;cursor:pointer}[data-block-id]:hover{outline:1px solid #78a9f8;outline-offset:-1px}[data-block-id].fs-selected{outline:2px solid #005bd3!important;outline-offset:-2px}.fs-editor-tools{position:fixed;z-index:2147483647;display:flex;align-items:center;gap:2px;max-width:calc(100vw - 8px);box-sizing:border-box;background:#005bd3;color:white;border-radius:6px;padding:3px;box-shadow:0 2px 8px #0003;font:12px/1.4 system-ui,sans-serif}.fs-editor-tools button{flex:0 0 auto;display:grid;place-items:center;width:26px;height:26px;padding:0;background:none;color:white;border:0;border-radius:4px;cursor:pointer}.fs-editor-tools button svg{width:15px;height:15px}.fs-editor-tools button:hover{background:#ffffff35}.fs-editor-tools button:focus-visible{outline:2px solid #fff;outline-offset:-2px}.fs-editor-tools span{min-width:0;padding:0 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}.fs-drop-before{box-shadow:inset 0 4px #005bd3!important}.fs-drop-after{box-shadow:inset 0 -4px #005bd3!important}.fs-drop-inside{outline:3px dashed #005bd3!important;outline-offset:-5px;background-color:#e8f1ff88!important}[contenteditable=true]{outline:2px solid #005bd3!important;outline-offset:3px;cursor:text!important;min-width:10px}.fs-editor-hint{padding:60px 24px;border:2px dashed #b7c9e4;margin:24px;text-align:center;color:#617085;font:14px/1.6 system-ui,sans-serif}.fs-rich-tools{position:fixed;z-index:2147483647;background:#242426;border-radius:5px;padding:4px;display:flex;gap:3px}.fs-rich-tools button{border:0;border-radius:3px;color:white;background:transparent;padding:5px 8px;font:13px system-ui,sans-serif}.fs-rich-tools button:hover{background:#4b4b50}`;
  document.head.append(style);
  style.textContent += `.fs-rich-tools{position:fixed;z-index:2147483647;left:4px;right:4px;max-width:calc(100vw - 8px);max-height:48vh;overflow:auto;background:#f8fafb;border:1px solid #cfd6dc;border-radius:8px;padding:5px;box-shadow:0 8px 24px #0003;color:#222;font:13px system-ui,sans-serif}.fs-rich-tools .rt-toolbar{display:flex;flex-wrap:wrap;gap:4px}.fs-rich-tools .rt-tool-group{display:flex;flex-wrap:wrap;gap:3px;border-right:1px solid #dce1e6;padding:0 4px}.fs-rich-tools button,.fs-rich-tools select{min-width:42px;min-height:42px;border:1px solid #cfd6dc;border-radius:5px;background:#fff;color:#222;padding:6px}.fs-rich-tools button[aria-pressed=true]{background:#e6f3ed;border-color:#2c7a54;color:#1a553a}.fs-rich-tools [data-rich-link-popover]{position:fixed;right:8px;top:58px;left:8px;max-width:420px;background:white;padding:12px;border:1px solid #cfd6dc;border-radius:6px;box-shadow:0 4px 16px #0003}.fs-rich-tools .rt-visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}`;
  const tools = document.createElement("div");
  tools.className = "fs-editor-tools";
  tools.hidden = true;
  document.body.append(tools);
  const richTools = document.createElement("div");
  richTools.className = "fs-rich-tools";
  richTools.hidden = true;
  document.body.append(richTools);
  const hoverLabel = document.createElement("span");
  hoverLabel.hidden = true;
  hoverLabel.style.cssText =
    "position:fixed;z-index:2147483646;pointer-events:none;background:#005bd3;color:white;padding:3px 7px;border-radius:4px;font:600 11px/1.4 system-ui,sans-serif;max-width:calc(100vw - 8px);box-sizing:border-box;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
  document.body.append(hoverLabel);
  document.addEventListener("mousemove", (e) => {
    const node = e.target.closest("[data-block-id]");
    hoverLabel.hidden =
      !node || Boolean(editing) || node?.dataset.blockId === selected;
    if (!hoverLabel.hidden) {
      const r = node.getBoundingClientRect();
      hoverLabel.textContent = blockName(node);
      hoverLabel.style.left = `${Math.max(4, Math.min(r.left, innerWidth - hoverLabel.offsetWidth - 4))}px`;
      hoverLabel.style.top = `${Math.max(2, r.top)}px`;
    }
  });
  // Friendly section names come from the editor; the raw type is only a fallback.
  let labels = {};
  const blockName = (node) =>
    labels[node.dataset.blockType] || node.dataset.blockType?.replaceAll("_", " ") || "Section";
  // Lucide icons are shared with the same-origin editor page when it has loaded them.
  const toolIcon = (name) => {
    try {
      return parent.fsIcon?.(name, 15) || "";
    } catch {
      return "";
    }
  };
  const blocks = () => [...document.querySelectorAll("[data-block-id]")];
  const find = (id) => blocks().find((n) => n.dataset.blockId === id);
  function positionTools() {
    const node = find(selected);
    if (!node) {
      tools.hidden = true;
      return;
    }
    const r = node.getBoundingClientRect();
    tools.style.top = `${Math.max(4, r.top - 34)}px`;
    tools.style.left = `${Math.max(4, Math.min(r.left, innerWidth - tools.offsetWidth - 6))}px`;
  }
  function select(id) {
    blocks().forEach((n) =>
      n.classList.toggle("fs-selected", n.dataset.blockId === id),
    );
    selected = id;
    const node = find(id);
    tools.hidden = !node;
    if (!node) return;
    tools.replaceChildren();
    const name = document.createElement("span");
    name.textContent = blockName(node);
    name.title = name.textContent;
    tools.append(name);
    for (const [action, label, glyph] of [
      ["up", "↑", "arrow-up"],
      ["down", "↓", "arrow-down"],
      ["duplicate", "⧉", "copy"],
      ["hide", "◉", "eye-off"],
      ["delete", "×", "trash-2"],
      ["parent", "↰", "corner-left-up"],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      const svg = toolIcon(glyph);
      if (svg) button.innerHTML = svg;
      else button.textContent = label;
      button.title = {
        up: "Move up",
        down: "Move down",
        duplicate: "Duplicate",
        hide: "Hide or show",
        delete: "Delete",
        parent: "Select parent",
      }[action];
      button.setAttribute("aria-label", button.title);
      button.onclick = (e) => {
        e.stopPropagation();
        send({ type: "action", action, block_id: selected });
      };
      tools.append(button);
    }
    positionTools();
  }
  function finishEdit() {
    if (!editing) return;
    const field = editing,
      node = field.closest("[data-block-id]");
    field.contentEditable = "false";
    const value = field.dataset.fieldType === "richtext"
      ? (window.FastShopRichText?.sanitize(field.innerHTML) || field.innerHTML)
      : field.textContent;
    if (field.dataset.fieldType === "richtext") field.innerHTML = value;
    send({
      type: "inline",
      block_id: node?.dataset.blockId || null,
      shell: !node,
      field: field.dataset.field,
      value,
    });
    if (field.dataset.inlineOverlay === "true") field.remove();
    window.FastShopRichText?.unmountInline(field);
    editing = null;
    richTools.hidden = true;
  }
  document.addEventListener(
    "click",
    (e) => {
      if (e.target.closest(".fs-editor-tools,.fs-rich-tools")) return;
      const link = e.target.closest("a[href]");
      const target = link && new URL(link.href, location.href);
      const targetPath = target?.pathname.replace(/\/+$/, "");
      const targetPage = target && target.origin === editorOrigin && Array.isArray(config.pages)
        ? config.pages.find((page) => targetPath === `${config.siteRoot}${page.path}`.replace(/\/+$/, ""))
        : null;
      if (targetPage && !editing) {
        e.preventDefault();
        e.stopImmediatePropagation();
        send({ type: "navigate", target_page_id: targetPage.id });
        return;
      }
      // Application-owned preview controls remain usable while links stay inert.
      if (e.target.closest("[data-widget-control]")) {
        if (e.target.closest("a")) e.preventDefault();
        return;
      }
      if (e.target.closest("a,button,input,select,textarea,form")) {
        if (!e.target.closest("[contenteditable=true]")) e.preventDefault();
      }
      if (editing && editing.contains(e.target)) return;
      finishEdit();
      // Theme header, footer and cookie banner open their settings panel in the editor.
      const shell = e.target.closest("[data-builder-shell]");
      if (shell && !e.target.closest("[data-block-id]")) {
        e.preventDefault();
        e.stopImmediatePropagation();
        select(null);
        send({ type: "shell", group: shell.dataset.builderShell });
        return;
      }
      const node = e.target.closest("[data-block-id]");
      if (node) {
        e.preventDefault();
        e.stopImmediatePropagation();
        select(node.dataset.blockId);
        send({ type: "select", block_id: node.dataset.blockId });
      } else {
        if (e.target.closest("a,button,input,select,textarea,form"))
          e.stopImmediatePropagation();
        select(null);
        send({ type: "select", block_id: null });
      }
    },
    true,
  );
  document.addEventListener(
    "submit",
    (e) => {
      e.preventDefault();
      e.stopImmediatePropagation();
    },
    true,
  );
  document.addEventListener(
    "dblclick",
    (e) => {
      const node = e.target.closest("[data-block-id]");
      if (!node) return;
      const image = e.target.closest('img,[data-field-type="image"]');
      if (image) {
        e.preventDefault();
        send({
          type: "media",
          block_id: node.dataset.blockId,
          field: image.dataset.field || "image",
        });
        return;
      }
      const field = e.target.closest("[data-field]");
      if (
        !field ||
        ["image", "video", "url", "link"].includes(field.dataset.field) ||
        field.tagName === "IMG"
      )
        return;
      e.preventDefault();
      finishEdit();
      if (field.tagName === "INPUT") {
        const rect = field.getBoundingClientRect(),
          overlay = document.createElement("span");
        overlay.dataset.field = field.dataset.field;
        overlay.dataset.fieldType = field.dataset.fieldType || "text";
        overlay.dataset.inlineOverlay = "true";
        overlay.textContent = field.placeholder;
        overlay.contentEditable = "true";
        overlay.setAttribute("role", "textbox");
        overlay.setAttribute("aria-label", `Edit ${field.dataset.field}`);
        overlay.style.cssText = `position:fixed;z-index:2147483647;top:${rect.top}px;left:${rect.left}px;width:${rect.width}px;min-height:${rect.height}px;background:white;color:#222;padding:8px;box-sizing:border-box;overflow:auto`;
        document.body.append(overlay);
        editing = overlay;
        overlay.focus();
        const range = document.createRange();
        range.selectNodeContents(overlay);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        return;
      }
      editing = field;
      field.contentEditable = "true";
      field.setAttribute("role", "textbox");
      field.setAttribute("aria-label", `Edit ${field.dataset.field}`);
      field.focus();
      const range = document.createRange();
      range.selectNodeContents(field);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      tools.hidden = true;
      if (
        field.dataset.fieldType === "richtext" ||
        (field.dataset.field === "body" &&
          field.querySelector("p,strong,em,ul,ol"))
      ) {
        field.dataset.fieldType = "richtext";
        window.FastShopRichText?.mountInline(field, richTools);
        const r = field.getBoundingClientRect();
        richTools.style.top = `${Math.max(4, r.top - 35)}px`;
        richTools.style.left = `${Math.max(4, r.left)}px`;
        richTools.hidden = false;
      }
    },
    true,
  );
  document.addEventListener("paste", (e) => {
    if (!editing || editing.dataset.fieldType === "richtext") return;
    e.preventDefault();
    document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
  });
  document.addEventListener("focusout", (e) => {
    if (
      editing &&
      e.target === editing &&
      !e.relatedTarget?.closest(".fs-rich-tools")
    )
      finishEdit();
  });
  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (!mod && e.target.closest("[data-widget-control]")) return;
    if (editing) {
      if (e.key === "Escape") {
        e.preventDefault();
        finishEdit();
        select(selected);
      }
      if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        finishEdit();
        send({ type: "shortcut", key: "save" });
      }
      return;
    }
    if (mod && ["z", "y", "s", "d"].includes(e.key.toLowerCase())) {
      e.preventDefault();
      send({ type: "shortcut", key: e.key.toLowerCase(), shift: e.shiftKey });
    } else if (e.key === "Delete" || e.key === "Escape") {
      e.preventDefault();
      send({
        type: "action",
        action: e.key === "Delete" ? "delete" : "parent",
        block_id: selected,
      });
    }
  });
  function clearDrop() {
    blocks().forEach((n) =>
      n.classList.remove("fs-drop-before", "fs-drop-after", "fs-drop-inside"),
    );
  }
  document.addEventListener("dragstart", (e) => {
    const n = e.target.closest("[data-block-id]");
    if (!n || e.target.closest("[contenteditable=true],[data-widget-control]")) {
      e.preventDefault();
      return;
    }
    dragPayload = { block_id: n.dataset.blockId };
    e.dataTransfer.setData(
      "application/x-fastshop-block",
      JSON.stringify(dragPayload),
    );
    e.dataTransfer.setData("text/plain", JSON.stringify(dragPayload));
  });
  blocks().forEach((n) => {
    n.draggable = true;
  });
  document.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = dragPayload?.widget ? "copy" : "move";
    clearDrop();
    const n = e.target.closest("[data-block-id]");
    dropTarget = n?.dataset.blockId || null;
    if (n) {
      const r = n.getBoundingClientRect(),
        ratio = (e.clientY - r.top) / r.height,
        isContainer =
          n.dataset.blockType === "container" ||
          n.classList.contains("widget-container") ||
          n.querySelector(":scope > .widget-children");
      dropPosition =
        ratio < 0.2
          ? "before"
          : ratio > 0.8
            ? "after"
            : isContainer
              ? "inside"
              : "after";
      n.classList.add(`fs-drop-${dropPosition}`);
    } else dropPosition = "after";
  });
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    let payload = dragPayload;
    try {
      payload = JSON.parse(
        e.dataTransfer.getData("application/x-fastshop-block") ||
          e.dataTransfer.getData("text/plain"),
      );
    } catch {
      /* Parent supplies current drag payload. */
    }
    send({
      type: "drop",
      block_id: dropTarget,
      position: dropPosition,
      payload,
    });
    clearDrop();
    dragPayload = null;
  });
  document.addEventListener("dragend", clearDrop);
  window.addEventListener("message", (e) => {
    if (e.source !== parent || e.origin !== editorOrigin) return;
    const d = e.data;
    if (
      d?.source !== "fastshop-editor" ||
      d.site_id !== siteId ||
      d.page_id !== pageId
    )
      return;
    if (d.type === "refresh") {
      blocks().forEach((n) => (n.draggable = true));
      select(d.block_id);
    }
    if (d.type === "select") {
      select(d.block_id);
      // A section picked in the sidebar comes into view if it is off-screen.
      const node = d.block_id && find(d.block_id);
      if (node) {
        const box = node.getBoundingClientRect();
        if (box.top < 0 || box.top > innerHeight * 0.6)
          node.scrollIntoView({
            block: "start",
            behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
          });
      }
    }
    if (d.type === "context") {
      if (d.labels && typeof d.labels === "object") labels = d.labels;
      select(d.block_id);
      if (Number.isFinite(d.scroll_y)) scrollTo(0, d.scroll_y);
    }
    if (d.type === "drag") dragPayload = d.payload;
  });
  window.addEventListener("scroll", positionTools, { passive: true });
  window.addEventListener("resize", positionTools);
  if (!blocks().length) {
    const hint = document.createElement("div");
    hint.className = "fs-editor-hint";
    hint.textContent = "Start with a section or drag a widget here";
    const main = document.querySelector("main") || document.body;
    hint.style.minHeight = "200px";
    main.prepend(hint);
  }
  send({ type: "ready" });
})();

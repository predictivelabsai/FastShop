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
 .fs-editor-tools[hidden],.fs-rich-tools[hidden]{display:none!important}[data-block-id]{position:relative;min-height:24px;cursor:pointer}[data-block-id]:hover{outline:1px solid #78a9f8;outline-offset:-1px}[data-block-id].fs-selected{outline:2px solid #005bd3!important;outline-offset:-2px}.fs-editor-tools{position:fixed;z-index:2147483647;display:flex;align-items:center;gap:3px;background:#005bd3;color:white;border-radius:5px;padding:4px;box-shadow:0 2px 8px #0003;font:11px/1.4 system-ui,sans-serif}.fs-editor-tools button{font:12px/1.4 system-ui,sans-serif;padding:4px 6px;background:none;color:white;border:0;border-radius:3px;cursor:pointer}.fs-editor-tools button:hover{background:#ffffff35}.fs-editor-tools span{padding:0 5px}.fs-drop-before{box-shadow:inset 0 4px #005bd3!important}.fs-drop-after{box-shadow:inset 0 -4px #005bd3!important}.fs-drop-inside{outline:3px dashed #005bd3!important;outline-offset:-5px;background-color:#e8f1ff88!important}[contenteditable=true]{outline:2px solid #005bd3!important;outline-offset:3px;cursor:text!important;min-width:10px}.fs-editor-hint{padding:60px 24px;border:2px dashed #b7c9e4;margin:24px;text-align:center;color:#617085;font:14px/1.6 system-ui,sans-serif}.fs-rich-tools{position:fixed;z-index:2147483647;background:#242426;border-radius:5px;padding:4px;display:flex;gap:3px}.fs-rich-tools button{border:0;border-radius:3px;color:white;background:transparent;padding:5px 8px;font:13px system-ui,sans-serif}.fs-rich-tools button:hover{background:#4b4b50}`;
  document.head.append(style);
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
    "position:fixed;z-index:2147483646;pointer-events:none;background:#005bd3;color:white;padding:3px 7px;border-radius:3px;font:11px system-ui,sans-serif";
  document.body.append(hoverLabel);
  document.addEventListener("mousemove", (e) => {
    const node = e.target.closest("[data-block-id]");
    hoverLabel.hidden =
      !node || Boolean(editing) || node?.dataset.blockId === selected;
    if (!hoverLabel.hidden) {
      const r = node.getBoundingClientRect();
      hoverLabel.textContent =
        node.dataset.blockType?.replaceAll("_", " ") || "Section";
      hoverLabel.style.left = `${Math.max(2, r.left)}px`;
      hoverLabel.style.top = `${Math.max(2, r.top)}px`;
    }
  });
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
    name.textContent =
      node.dataset.blockType?.replaceAll("_", " ") || "Section";
    tools.append(name);
    for (const [action, label] of [
      ["up", "↑"],
      ["down", "↓"],
      ["duplicate", "⧉"],
      ["hide", "◉"],
      ["delete", "×"],
      ["parent", "↰"],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
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
    send({
      type: "inline",
      block_id: node.dataset.blockId,
      field: field.dataset.field,
      value:
        field.dataset.fieldType === "richtext"
          ? field.innerHTML
          : field.textContent,
    });
    editing = null;
    richTools.hidden = true;
  }
  document.addEventListener(
    "click",
    (e) => {
      if (e.target.closest(".fs-editor-tools,.fs-rich-tools")) return;
      if (e.target.closest("a,button,input,select,textarea,form")) {
        if (!e.target.closest("[contenteditable=true]")) e.preventDefault();
      }
      if (editing && editing.contains(e.target)) return;
      finishEdit();
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
      const image = e.target.closest("img");
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
        richTools.replaceChildren();
        for (const [command, label] of [
          ["bold", "B"],
          ["italic", "I"],
          ["insertUnorderedList", "• List"],
          ["removeFormat", "Clear"],
        ]) {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.setAttribute("aria-label", command);
          button.onmousedown = (ev) => {
            ev.preventDefault();
            document.execCommand(command, false);
          };
          richTools.append(button);
        }
        const r = field.getBoundingClientRect();
        richTools.style.top = `${Math.max(4, r.top - 35)}px`;
        richTools.style.left = `${Math.max(4, r.left)}px`;
        richTools.hidden = false;
      }
    },
    true,
  );
  document.addEventListener("paste", (e) => {
    if (!editing) return;
    e.preventDefault();
    document.execCommand(
      "insertText",
      false,
      e.clipboardData.getData("text/plain"),
    );
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
    if (!n || e.target.closest("[contenteditable=true]")) {
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
    if (d.type === "select") select(d.block_id);
    if (d.type === "context") {
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

/* Dependency-free merchant editor. All saved content passes server validation. */
import { hotspotPreview, hotspotRowsChanged, wireHotspotPreview } from "./hotspots.js";
const boot = window.FASTSHOP_EDITOR;
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const copy = (v) => structuredClone(v);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const base = `/admin/sites/${encodeURIComponent(boot.site_id)}/editor/api`,
  frame = $("#editor-canvas");
let previewBlock = null,
  needsFullPreview = true;
let state,
  selected = null,
  mode = "sections",
  dirty = false,
  themeDirty = false,
  designDirty = false,
  shellDirty = false,
  shellGroup = null,
  saving = false,
  history = [],
  cursor = -1,
  lastSaved = "",
  timer,
  serial = 0,
  dragPayload,
  mediaCallback,
  linkCallback,
  iconCallback,
  iconLibrary,
  conflictVersion;
const collapsed = new Set();
// Open repeater entries stay open when the panel redraws, for example after choosing media.
const openItems = new Set();
let toastTimer;
function openDialog(selector) {
  const dialog = $(selector);
  dialog.editorOpener = document.activeElement;
  dialog.showModal();
}
function toast(message, error = false) {
  let notice = $("#editor-toast");
  if (!notice) {
    notice = document.createElement("div");
    notice.id = "editor-toast";
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
  }
  (error && $("dialog[open]") ? $("dialog[open]") : $("#editor")).append(notice);
  clearTimeout(toastTimer);
  notice.textContent = message;
  notice.classList.toggle("is-error", error);
  notice.hidden = false;
  toastTimer = setTimeout(() => (notice.hidden = true), 4500);
}
async function api(path, method = "GET", data) {
  const headers = { "X-CSRF-Token": boot.csrf_token };
  let body;
  if (data instanceof FormData) body = data;
  else if (data !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(data);
  }
  const res = await fetch(
    `${base}${path}?page=${encodeURIComponent(boot.page_id)}&theme=${encodeURIComponent(boot.theme_id || '')}`,
    { method, headers, body, credentials: "same-origin" },
  );
  let result;
  try {
    result = await res.json();
  } catch {
    throw Error("The server could not complete this request.");
  }
  if (!res.ok) {
    const e = Error(result.error || "The request failed.");
    Object.assign(e, result);
    throw e;
  }
  return result;
}
function showError(e) {
  toast(e.message || String(e), true);
  if (e.site_version !== undefined) state.site_version = e.site_version;
  $("#editor-error").hidden = false;
  $("#editor-error").innerHTML =
    `<button type="button" class="icon-button" aria-label="Dismiss error">${icon("x", 15)}</button><strong>${esc(e.message || e)}</strong>${e.errors?.length ? `<ul>${e.errors.map((x) => `<li>${esc(x.path)}: ${esc(x.message)}</li>`).join("")}</ul>` : ""}`;
  $("#editor-error button").onclick = () => ($("#editor-error").hidden = true);
  if (e.conflict) {
    conflictVersion = e.version;
    openDialog("#conflict-dialog");
  }
}
function status(s) {
  $("#save-status").textContent = s;
}
function walk(blocks = state.document.blocks, parent = null, result = []) {
  for (const block of blocks) {
    result.push({ block, parent });
    walk(block.children || [], block, result);
  }
  return result;
}
const locate = (id) => walk().find((n) => n.block.id === id),
  siblings = (n) => (n.parent ? n.parent.children : state.document.blocks);
const spec = (t) => (t === "__shell" ? state.shell_schema : state.schema[t]) || {},
  schemaLabel = (type) => (spec(type).label || type.replaceAll("_", " ")).replace(/^./, (c) => c.toUpperCase()),
  label = (b) => schemaLabel(b.type);
// Lucide icons from /static/admin/icons.js, so the editor matches the merchant admin.
const icon = (name, size = 16) =>
  (window.fsIcon?.(name, size) || "").replace('stroke-width="2"', 'stroke-width="1.75"');
// Each schema icon key maps to the closest Lucide glyph.
const blockIconNames = {
  "marquee": "scroll-text", "collage": "layout-dashboard", "flip-card": "flip-horizontal-2",
  "cta-box": "mouse-pointer-click", "video-lightbox": "circle-play", "team-members": "users",
  "pull-quote": "quote", "button-group": "rectangle-ellipsis", "shop-the-look": "shopping-bag",
  "recently-viewed": "history", "tabs": "panels-top-left", "countdown": "timer", "stats": "chart-column",
  "progress-bars": "chart-bar", "alert": "triangle-alert", "table": "table", "map": "map",
  "social-links": "share-2", "columns": "columns-3", "heading": "heading", "paragraph": "pilcrow",
  "image": "image", "button": "square-mouse-pointer", "spacer": "unfold-vertical",
  "divider": "separator-horizontal", "video": "video", "checklist": "list-checks", "gallery": "images",
  "quote": "quote", "tag": "tag", "products": "layout-grid", "collections": "folder-open", "mail": "mail",
  "accordion": "list-collapse", "code": "code", "slideshow": "gallery-horizontal",
  "banner": "rectangle-horizontal", "compare": "square-split-horizontal", "logos": "shapes",
  "features": "sparkles", "hotspots": "crosshair", "section-title": "heading-1",
  "embed-video": "monitor-play", "hero": "app-window", "text": "text", "split": "layout-panel-left",
  "catalogue": "book-open", "chart": "chart-column", "shield": "shield-check",
  "reviews": "message-square-quote", "articles": "newspaper", "library": "library",
  "question": "circle-help", "people": "users", "contact": "contact", "product-detail": "package",
  "references": "file-text", "featured-collection": "star", "pricing-table": "tags",
  "comparison-table": "table-2", "trust-badges": "badge-check", "announcement-bar": "megaphone",
  "promo-banner": "badge-percent", "steps": "list-ordered", "star-rating": "star",
  "contact-card": "contact-round", "embed": "code-xml",
};
function blockIcon(type, size = 16) {
  const key = spec(type).icon;
  const name = blockIconNames[key] || "layout-template";
  return icon(name, size).replace('class="fs-icon"', 'class="fs-icon block-icon"');
}
const get = (o, p) => p.split(".").reduce((v, k) => v?.[k], o);
function set(o, p, v) {
  const keys = p.split("."),
    last = keys.pop();
  for (const k of keys) {
    o[k] ??= {};
    o = o[k];
  }
  o[last] = v;
}
const isLocalizedText = (value) =>
  value &&
  typeof value === "object" &&
  !Array.isArray(value) &&
  Object.keys(value).length > 0 &&
  Object.prototype.hasOwnProperty.call(value, state?.default_locale || "en") &&
  Object.entries(value).every(
    ([locale, text]) =>
      /^[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*$/.test(locale) &&
      typeof text === "string",
  );
function text(v) {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v[state?.default_locale || "en"] ?? "")
    : (v ?? "");
}
function setField(o, p, v) {
  const old = get(o, p);
  if (isLocalizedText(old)) {
    const locale = state.default_locale || "en";
    v = { ...old, [locale]: v };
  }
  set(o, p, v);
}
function sanitizeBadItemLocaleKeys(document, schema) {
  const localeKey = /^[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*$/;
  let removed = 0;
  const visit = (blocks = []) => {
    for (const block of blocks) {
      const itemsField = (schema[block.type]?.fields || []).find(
        (field) => field.id === "items" && field.type === "repeater",
      );
      const allowed = new Set(
        (itemsField?.item_fields || itemsField?.fields || []).map(
          (field) => field.id,
        ),
      );
      if (Array.isArray(block.items) && allowed.size) {
        for (const item of block.items) {
          if (!item || typeof item !== "object" || Array.isArray(item)) continue;
          for (const [key, value] of Object.entries(item)) {
            if (
              !allowed.has(key) &&
              localeKey.test(key) &&
              value &&
              typeof value === "object" &&
              !Array.isArray(value)
            ) {
              const copiedFields = Object.keys(value);
              if (
                copiedFields.length > 1 &&
                copiedFields.every((field) => allowed.has(field)) &&
                copiedFields.some((field) =>
                  Object.prototype.hasOwnProperty.call(item, field),
                )
              ) {
                delete item[key];
                removed += 1;
              }
            }
          }
        }
      }
      visit(block.children || []);
    }
  };
  visit(document.blocks || []);
  return removed;
}
function historyButtons() {
  $("#undo").disabled = cursor <= 0;
  $("#redo").disabled = cursor >= history.length - 1;
}
function snapshot(name) {
  // Typing into one field makes one history step, not one per keystroke.
  const last = history[cursor], now = Date.now();
  if (last && cursor === history.length - 1 && last.name === name && name.startsWith("Edit ") && now - last.at < 1500) {
    last.document = copy(state.document);
    last.at = now;
    historyButtons();
    return;
  }
  history = history.slice(0, cursor + 1);
  history.push({ name, document: copy(state.document), at: now });
  if (history.length > 100) history.shift();
  cursor = history.length - 1;
  historyButtons();
}
function changed(name, panel = false) {
  dirty = JSON.stringify(state.document) !== lastSaved;
  status(dirty || themeDirty ? "Unsaved changes" : "Saved");
  snapshot(name);
  renderTree();
  if (panel) renderSettings();
  clearTimeout(timer);
  previewBlock = selected;
  timer = setTimeout(() => preview(previewBlock), 350);
  if (mode === "history") renderLeft();
}
function mutate(name, fn) {
  needsFullPreview = true;
  fn();
  changed(name, true);
  clearTimeout(timer);
  timer = setTimeout(() => preview(), 250);
}
function send(payload) {
  frame.contentWindow?.postMessage(
    {
      source: "fastshop-editor",
      site_id: boot.site_id,
      page_id: boot.page_id,
      ...payload,
    },
    location.origin,
  );
}
function select(id) {
  selected = id;
  if (id) shellGroup = null;
  if (id && window.matchMedia("(max-width: 760px)").matches)
    $("#editor").classList.remove("is-panel-open");
  $("#editor").classList.toggle("has-selection", Boolean(id));
  renderTree();
  renderSettings();
  send({ type: "select", block_id: id });
}
async function preview(blockId = null) {
  if (needsFullPreview) blockId = null;
  const request = ++serial;
  try {
    const result = await api("/render", "POST", {
      document: state.document,
      ...(blockId ? { block_id: blockId } : {}),
    });
    if (request !== serial) return;
    if (blockId) {
      const parsed = new DOMParser().parseFromString(result.html, "text/html");
      const replacement = [...parsed.querySelectorAll("[data-block-id]")].find(
        (n) => n.dataset.blockId === blockId,
      );
      const original = [
        ...frame.contentDocument.querySelectorAll("[data-block-id]"),
      ].find((n) => n.dataset.blockId === blockId);
      if (original && replacement) {
        original.replaceWith(
          frame.contentDocument.importNode(replacement, true),
        );
        send({ type: "refresh", block_id: selected });
        return;
      }
      return preview();
    }
    const y = frame.contentWindow?.scrollY || 0;
    frame.onload = () => {
      if (request === serial) needsFullPreview = false;
      send({ type: "context", block_id: selected, scroll_y: y, labels: canvasLabels() });
    };
    frame.srcdoc = result.html;
    $("#node-count").textContent = `${walk().length} blocks`;
  } catch (e) {
    if (request === serial) showError(e);
  }
}
function canvasLabels() {
  return Object.fromEntries(Object.keys(state?.schema || {}).map((type) => [type, schemaLabel(type)]));
}
function restoreHistory() {
  needsFullPreview = true;
  state.document = copy(history[cursor].document);
  dirty = JSON.stringify(state.document) !== lastSaved;
  status(dirty || themeDirty ? "Unsaved changes" : "Saved");
  if (selected && !locate(selected)) selected = null;
  renderLeft();
  renderSettings();
  historyButtons();
  preview();
}
function undo() {
  if (cursor > 0) {
    cursor--;
    restoreHistory();
  }
}
function redo() {
  if (cursor < history.length - 1) {
    cursor++;
    restoreHistory();
  }
}
function renew(b) {
  b.id = `block-${crypto.randomUUID()}`;
  for (const c of b.children || []) renew(c);
  return b;
}
function insert(type, targetId = null, position = "after") {
  const s = spec(type);
  if (s.insertable === false) return;
  const parent = locate(targetId);
  let depth = 0;
  for (
    let p = parent && (position === "inside" ? parent.block : parent.parent);
    p;
    p = locate(p.id)?.parent
  )
    depth++;
  if (depth > 2) {
    showError("Blocks can be nested up to three levels.");
    return;
  }
  if (
    position === "inside" &&
    parent &&
    !spec(parent.block.type).allowed_children.includes(type)
  ) {
    showError("This container accepts generic widgets only.");
    return;
  }
  if (walk().length >= 200) {
    showError("This page has reached its block limit.");
    return;
  }
  const block = renew(copy(s.defaults));
  mutate(`Add ${s.label}`, () => {
    const target = locate(targetId);
    if (
      target &&
      position === "inside" &&
      spec(target.block.type).accepts_children
    ) {
      target.block.children ??= [];
      target.block.children.push(block);
    } else if (target) {
      const list = siblings(target);
      list.splice(
        list.indexOf(target.block) + (position === "before" ? 0 : 1),
        0,
        block,
      );
    } else state.document.blocks.push(block);
    selected = block.id;
  });
  select(block.id);
}
function move(id, targetId, position = "after") {
  const a = locate(id),
    b = locate(targetId);
  if (
    !a ||
    !b ||
    id === targetId ||
    walk([a.block]).some((n) => n.block.id === targetId)
  )
    return;
  if (
    position === "inside" &&
    (!spec(b.block.type).accepts_children ||
      !spec(b.block.type).allowed_children.includes(a.block.type))
  ) {
    showError("This container accepts generic widgets only.");
    return;
  }
  let depth = 0;
  for (
    let p = position === "inside" ? b.block : b.parent;
    p;
    p = locate(p.id)?.parent
  )
    depth++;
  const height = (n) =>
    n.children?.length ? 1 + Math.max(...n.children.map(height)) : 0;
  if (depth + height(a.block) > 2) {
    showError("Blocks can be nested up to three levels.");
    return;
  }
  mutate("Move block", () => {
    siblings(a).splice(siblings(a).indexOf(a.block), 1);
    if (position === "inside") {
      b.block.children ??= [];
      b.block.children.push(a.block);
    } else {
      const list = siblings(b);
      list.splice(
        list.indexOf(b.block) + (position === "before" ? 0 : 1),
        0,
        a.block,
      );
    }
  });
}
function action(name, id = selected) {
  const n = locate(id);
  if (!n) return;
  const list = siblings(n),
    i = list.indexOf(n.block);
  if (name === "parent") {
    select(n.parent?.id || null);
    return;
  }
  if (name === "up" || name === "down") {
    const j = i + (name === "up" ? -1 : 1);
    if (j < 0 || j >= list.length) return;
    mutate("Reorder block", () => {
      [list[i], list[j]] = [list[j], list[i]];
    });
  }
  if (name === "duplicate")
    mutate("Duplicate block", () => {
      const b = renew(copy(n.block));
      list.splice(i + 1, 0, b);
      selected = b.id;
    });
  if (name === "hide")
    mutate(
      n.block.hidden ? "Show block" : "Hide block",
      () => (n.block.hidden = !n.block.hidden),
    );
  if (name === "delete")
    mutate("Delete block", () => {
      list.splice(i, 1);
      selected = n.parent?.id || null;
    });
}
function startDrag(e, payload) {
  dragPayload = payload;
  e.dataTransfer.effectAllowed = "copyMove";
  e.dataTransfer.setData(
    "application/x-fastshop-block",
    JSON.stringify(payload),
  );
  e.dataTransfer.setData("text/plain", JSON.stringify(payload));
  send({ type: "drag", payload });
}
function readDrag(e) {
  try {
    return JSON.parse(
      e.dataTransfer.getData("application/x-fastshop-block") ||
        e.dataTransfer.getData("text/plain"),
    );
  } catch {
    return dragPayload;
  }
}
function drop(id, position, payload) {
  if (payload?.widget) insert(payload.widget, id, position);
  else if (payload?.block_id) move(payload.block_id, id, position);
  dragPayload = null;
}
function row(b, depth = 0) {
  const children = b.children || [], open = !collapsed.has(b.id), name = label(b);
  const subtitle = String(text(b.heading || b.title) || "").replace(/<[^>]*>/g, "").trim();
  const toggle = children.length
    ? `<button type="button" class="tree-toggle" data-collapse="${esc(b.id)}" tabindex="-1" aria-label="${open ? "Collapse" : "Expand"} ${esc(name)}">${icon("chevron-right", 14)}</button>`
    : `<span class="tree-toggle" aria-hidden="true"></span>`;
  const actions = `<div class="tree-actions"><button type="button" data-action="hide" class="${b.hidden ? "is-on" : ""}" aria-label="${b.hidden ? "Show" : "Hide"} ${esc(name)}" aria-pressed="${Boolean(b.hidden)}" title="${b.hidden ? "Show" : "Hide"} block">${icon(b.hidden ? "eye-off" : "eye", 15)}</button><button type="button" data-action="duplicate" aria-label="Duplicate block" title="Duplicate block">${icon("copy", 15)}</button><button type="button" data-action="delete" aria-label="Delete block" title="Delete block">${icon("trash-2", 15)}</button></div>`;
  const add = spec(b.type).accepts_children && open
    ? `<button type="button" class="tree-add tree-add-child" data-add-child="${esc(b.id)}" style="--depth:${depth + 1}">${icon("circle-plus", 15)}<span>Add block</span></button>`
    : "";
  return `<div class="tree-node" role="none"><div class="tree-row ${selected === b.id ? "selected" : ""} ${b.hidden ? "is-hidden" : ""}" data-block-id="${esc(b.id)}" draggable="true" tabindex="0" role="treeitem" aria-level="${depth + 1}" aria-selected="${selected === b.id}" ${children.length ? `aria-expanded="${open}"` : ""} aria-label="${esc(name)}${subtitle ? `: ${esc(subtitle)}` : ""}${b.hidden ? " (hidden)" : ""}" style="--depth:${depth}"><span class="drag-handle" aria-hidden="true" title="Drag to reorder">${icon("grip-vertical", 14)}</span>${toggle}${blockIcon(b.type)}<span class="tree-title"><span>${esc(name)}</span>${subtitle ? `<small>${esc(subtitle)}</small>` : ""}</span>${actions}</div>${open && children.length ? `<div role="group">${children.map((c) => row(c, depth + 1)).join("")}</div>` : ""}${add}</div>`;
}
function renderTree() {
  if (mode !== "sections") return;
  const blocks = state.document.blocks;
  $("#left-content").innerHTML =
    `<div class="tree-group"><div class="tree-group-label">Header</div>${shellRow("header", "Store header")}</div><div class="tree-group"><div class="tree-group-label" id="template-label">Template</div><div role="tree" aria-labelledby="template-label" class="tree">${blocks.map((b) => row(b)).join("")}</div>${blocks.length ? "" : '<p class="tree-empty">This page has no sections yet.</p>'}<button type="button" class="tree-add" id="add-section">${icon("circle-plus", 15)}<span>Add section</span></button></div><div class="tree-group"><div class="tree-group-label">Store pages</div>${shellRow("pages", "Store pages")}</div><div class="tree-group"><div class="tree-group-label">Footer</div>${shellRow("footer", "Store footer")}${shellRow("consent", "Cookie banner")}</div>`;
  $$("[data-shell]").forEach((b) => (b.onclick = () => openShell(b.dataset.shell)));
  $("[data-open-pages]")?.addEventListener("click", (e) => {
    e.stopPropagation();
    pageSwitcher.open = true;
    $("#page-search").focus();
  });
  $("#add-section").onclick = () => openPicker();
  $$("[data-add-child]").forEach(
    (b) => (b.onclick = () => openPicker(b.dataset.addChild)),
  );
  $$("[data-collapse]").forEach(
    (b) =>
      (b.onclick = (e) => {
        e.stopPropagation();
        const id = b.dataset.collapse;
        collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id);
        renderTree();
        $$(".tree-row").find((node) => node.dataset.blockId === id)?.focus();
      }),
  );
  $$(".tree-row").forEach((r) => {
    r.onclick = (e) => {
      const button = e.target.closest("[data-action]");
      if (button) action(button.dataset.action, r.dataset.blockId);
      else select(r.dataset.blockId);
    };
    r.onkeydown = (e) => {
      if (e.target !== r) return;
      if (["Enter", " "].includes(e.key)) {
        e.preventDefault();
        select(r.dataset.blockId);
        $$(".tree-row").find((node) => node.dataset.blockId === r.dataset.blockId)?.focus();
      }
      if (!e.altKey && ["ArrowLeft", "ArrowRight"].includes(e.key) && r.hasAttribute("aria-expanded")) {
        e.preventDefault();
        const id = r.dataset.blockId, expanded = !collapsed.has(id);
        if (expanded === (e.key === "ArrowRight")) return;
        expanded ? collapsed.add(id) : collapsed.delete(id);
        renderTree();
        $$(".tree-row").find((node) => node.dataset.blockId === id)?.focus();
        return;
      }
      if (!e.altKey && ["ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        const rows = $$(".tree-row"), index = rows.indexOf(r);
        const next = e.key === "Home" ? 0 : e.key === "End" ? rows.length - 1
          : index + (e.key === "ArrowDown" ? 1 : -1);
        rows[Math.max(0, Math.min(next, rows.length - 1))]?.focus();
      }
      if (e.altKey && ["ArrowUp", "ArrowDown"].includes(e.key)) {
        e.preventDefault();
        action(e.key === "ArrowUp" ? "up" : "down", r.dataset.blockId);
        $$(".tree-row").find((node) => node.dataset.blockId === r.dataset.blockId)?.focus();
      }
    };
    r.ondragstart = (e) => startDrag(e, { block_id: r.dataset.blockId });
    r.ondragover = (e) => {
      e.preventDefault();
      $$(".tree-row").forEach((x) =>
        x.classList.remove("drop-before", "drop-after", "drop-inside"),
      );
      const rect = r.getBoundingClientRect(),
        v = (e.clientY - rect.top) / rect.height;
      r.dataset.position =
        v < 0.25
          ? "before"
          : v > 0.75
            ? "after"
            : spec(locate(r.dataset.blockId).block.type).accepts_children
              ? "inside"
              : "after";
      r.classList.add(`drop-${r.dataset.position}`);
    };
    r.ondrop = (e) => {
      e.preventDefault();
      drop(r.dataset.blockId, r.dataset.position, readDrag(e));
    };
  });
}
function widgetHTML(query = "") {
  const groups = new Map();
  // The container schema already declares every registered generic widget.
  const widgets = new Set(spec("container").allowed_children || []);
  for (const [type, s] of Object.entries(state.schema)) {
    if (
      !widgets.has(type) ||
      s.insertable === false ||
      !`${s.label} ${s.category} ${s.description}`
        .toLowerCase()
        .includes(query.toLowerCase())
    )
      continue;
    groups.set(s.category, [...(groups.get(s.category) || []), [type, s]]);
  }
  if (!groups.size) return '<p class="panel-empty">No widgets match that search.</p>';
  return [...groups]
    .map(
      ([category, items]) =>
        `<div class="widget-category">${esc(category)}</div><div class="widget-grid">${items.map(([type, s]) => `<button type="button" class="widget-tile" draggable="true" data-widget-type="${esc(type)}" title="${esc(s.description)}"><span class="tile-preview" aria-hidden="true">${blockIcon(type, 18)}</span><span class="tile-text"><span>${esc(schemaLabel(type))}</span><small>${esc(s.description || "")}</small></span></button>`).join("")}</div>`,
    )
    .join("");
}
function wireWidgets() {
  $$("[data-widget-type]").forEach((b) => {
    b.ondragstart = (e) => startDrag(e, { widget: b.dataset.widgetType });
    b.onclick = () =>
      insert(
        b.dataset.widgetType,
        selected,
        spec(locate(selected)?.block.type).accepts_children
          ? "inside"
          : "after",
      );
  });
}
function renderLeft() {
  const names = {
    sections: "Sections",
    widgets: "Widgets",
    theme: "Theme settings",
    history: "History",
  };
  const notes = {
    sections: state?.site?.name || "",
    widgets: "Click to add, or drag onto the page",
    theme: "Shared across your storefront",
    history: "Changes made in this session",
  };
  $("#left-title").textContent = names[mode];
  $("#theme-name").textContent = notes[mode];
  $("#left-actions").innerHTML = "";
  $("#editor").dataset.editorMode = mode;
  $$("[data-mode]").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
    b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
  });
  applyPanelWidths();
  if (mode === "sections") renderTree();
  if (mode === "widgets") {
    $("#left-content").innerHTML =
      `<div class="widget-search">${icon("search", 15)}<input id="widget-search" type="search" placeholder="Search widgets" aria-label="Search widgets"></div><div id="widget-results">${widgetHTML()}</div>`;
    $("#widget-search").oninput = (e) => {
      $("#widget-results").innerHTML = widgetHTML(e.target.value);
      wireWidgets();
    };
    wireWidgets();
  }
  if (mode === "theme") renderTheme();
  if (mode === "history") {
    $("#left-content").innerHTML =
      `<p class="panel-note">Saved revisions from earlier sessions are under Revisions in the top bar.</p><ol class="history-list" reversed>${history
        .map(
          (h, i) =>
            `<li class="history-item ${i === cursor ? "current" : ""}"><span class="history-dot" aria-hidden="true"></span><span class="history-name">${esc(h.name)}${i === cursor ? '<small>Current state</small>' : i > cursor ? '<small>Undone</small>' : ""}</span>${i === cursor ? "" : `<button type="button" data-history="${i}" aria-label="Restore: ${esc(h.name)}">${icon("rotate-ccw", 14)}<span>Restore</span></button>`}</li>`,
        )
        .reverse()
        .join("")}</ol>`;
    $$("[data-history]").forEach(
      (b) =>
        (b.onclick = () => {
          cursor = Number(b.dataset.history);
          restoreHistory();
        }),
    );
  }
}
function openPicker(parent = null) {
  $("#picker-search").value = "";
  $("#picker-title").textContent = parent ? "Add block" : "Add section";
  const draw = () => {
    let category = "";
    $("#picker-results").innerHTML = Object.entries(state.schema)
      .sort(([, a], [, b]) => (a.category || "").localeCompare(b.category || ""))
      .filter(
        ([, s]) =>
          s.insertable !== false &&
          `${s.label} ${s.category}`
            .toLowerCase()
            .includes($("#picker-search").value.toLowerCase()),
      )
      .map(([type, s]) => {
        const header =
          category !== s.category
            ? `<div class="picker-category">${esc(s.category)}</div>`
            : "";
        category = s.category;
        return `${header}<button type="button" class="picker-item" data-insert="${esc(type)}"><span class="picker-thumbnail" aria-hidden="true">${blockIcon(type, 20)}</span><span>${esc(schemaLabel(type))}<small>${esc(s.description)}</small></span></button>`;
      })
      .join("");
    $$("[data-insert]").forEach(
      (b) =>
        (b.onclick = () => {
          insert(b.dataset.insert, parent, parent ? "inside" : "after");
          $("#picker-dialog").close();
        }),
    );
  };
  $("#picker-search").oninput = draw;
  draw();
  openDialog("#picker-dialog");
  $("#picker-search").focus();
}
function options(opts) {
  return Array.isArray(opts)
    ? opts.map((o) =>
        typeof o === "object"
          ? { value: o.value ?? o.id, label: o.label ?? o.value }
          : { value: o, label: o },
      )
    : Object.entries(opts || {}).map(([value, label]) => ({ value, label }));
}
function fieldHTML(f, obj, prefix = "") {
  const path = prefix + f.id,
    raw = get(obj, f.id) ?? f.default ?? "",
    v = text(raw),
    id = `field-${path.replace(/[^a-z0-9]/gi, "-")}`,
    attrs = `id="${esc(id)}" data-setting="${esc(path)}" data-type="${esc(f.type)}"${f.max_length ? ` maxlength="${f.max_length}"` : ""}`;
  let html = "";
  if (f.type === "toggle")
    html = `<input ${attrs} type="checkbox" role="switch" ${raw ? "checked" : ""}>`;
  else if (f.type === "select")
    html = `<select ${attrs}>${options(f.options)
      .map(
        (o) =>
          `<option value="${esc(o.value)}" ${String(v) === String(o.value) ? "selected" : ""}>${esc(o.label)}</option>`,
      )
      .join("")}</select>`;
  else if (f.type === "richtext")
    html = `<div data-rich-inspector data-rich-setting="${esc(path)}" data-rich-value="${esc(v)}" data-rich-label="${esc(f.label || f.id)}"></div>`;
  else if (f.type === "textarea")
    html = `<textarea ${attrs} rows="4">${esc(v)}</textarea>`;
  else if (f.type === "range")
    html = `<div class="field-inline"><input ${attrs} type="range" min="${f.min ?? 0}" max="${f.max ?? 100}" step="${f.step ?? 1}" value="${esc(v)}"><input type="number" data-companion="${esc(path)}" aria-label="${esc(f.label)} value" value="${esc(v)}" min="${f.min ?? 0}" max="${f.max ?? 100}" step="${f.step ?? 1}"><span class="subtle">${esc(f.unit || "")}</span></div>`;
  else if (["colour", "color"].includes(f.type))
    html = `<div class="field-inline"><input ${attrs} type="color" value="${/^#[0-9a-f]{6}$/i.test(v) ? esc(v) : "#ffffff"}"><input data-companion="${esc(path)}" value="${esc(v)}" aria-label="${esc(f.label)} hex colour" placeholder="#ffffff"></div><div class="swatches">${[...new Set(["#ffffff", "#f5f5f5", "#242426", "#005bd3", ...Object.values(state.theme_settings).filter((v) => typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v))])].map((c) => `<button data-swatch="${esc(path)}" data-colour="${esc(c)}" style="background:${esc(c)}" aria-label="Use ${esc(c)}"></button>`).join("")}</div>`;
  else if (f.type === "repeater") {
    const rows = Array.isArray(raw) ? raw : [];
    html =
      rows
        .map(
          (item, i) => {
            const editorFields = f.fields || f.item_fields || [];
            return `<details class="repeater-item" draggable="true" data-repeater="${esc(path)}" data-index="${i}" ${openItems.has(`${selected}|${path}.${i}`) ? "open" : ""}><summary><span class="repeater-grip" aria-hidden="true" title="Drag to reorder">${icon("grip-vertical", 14)}</span><span class="repeater-title">${esc(text(item.heading || item.label) || `Item ${i + 1}`)}</span></summary>${f.value_list ? `<div class="field-inline"><input data-setting="${esc(path)}.${i}" data-type="${esc(f.fields[0].type)}" value="${esc(item)}" aria-label="${esc(f.fields[0].label)} ID"><button data-link="${esc(path)}.${i}" data-resource-type="${esc(f.fields[0].type)}" aria-label="Choose ${esc(f.fields[0].label)}" title="Choose ${esc(f.fields[0].label)}" class="icon-button">${icon("link", 15)}</button></div>` : editorFields.map((sub) => fieldHTML(sub, item, `${path}.${i}.`)).join("")}<div class="repeater-actions"><button type="button" class="icon-button" data-repeat-action="up" data-path="${esc(path)}" data-index="${i}" aria-label="Move item up" title="Move item up" ${i === 0 ? "disabled" : ""}>${icon("arrow-up", 15)}</button><button type="button" class="icon-button" data-repeat-action="down" data-path="${esc(path)}" data-index="${i}" aria-label="Move item down" title="Move item down" ${i === rows.length - 1 ? "disabled" : ""}>${icon("arrow-down", 15)}</button>${f.fixed ? "" : `<button type="button" class="repeater-remove" data-repeat-action="remove" data-path="${esc(path)}" data-index="${i}" ${rows.length <= (f.min ?? 0) ? "disabled" : ""}>${icon("trash-2", 14)}<span>Remove</span></button>`}</div></details>`;
          },
        )
        .join("") +
      (f.fixed
        ? ""
        : `<button type="button" class="add-item" data-repeat-add="${esc(path)}" ${rows.length >= (f.max ?? 50) ? "disabled" : ""}>${icon("circle-plus", 15)}<span>Add item</span></button>`);
  } else if (f.type === "icon") {
    const selected = String(v || ""),
      label = selected.startsWith("lucide:")
        ? selected.slice("lucide:".length).replaceAll("-", " ")
        : selected.startsWith("/site-media/")
          ? "Custom icon"
          : selected
            ? `${selected.replaceAll("_", " ")} (original icon)`
            : "No icon selected";
    html = `<div class="icon-field-actions"><button id="${esc(id)}" type="button" class="icon-field-trigger" data-icon="${esc(path)}" aria-haspopup="dialog"><span>${selected ? "Change icon" : "Choose icon"}</span><small>${esc(label)}</small></button>${selected ? `<button type="button" data-icon-clear="${esc(path)}" aria-label="Clear ${esc(f.label || f.id)}">Clear</button>` : ""}</div>`;
  } else if (["image", "video"].includes(f.type))
    html = `${f.type === "image" && obj.type !== "image_hotspots" && v && !v.startsWith("metafield:") ? `<img class="field-media-preview" src="${esc(v)}" alt="Selected image">` : ""}<input ${attrs} value="${esc(v)}" placeholder="Media URL"><button type="button" class="media-button" data-media="${esc(path)}">${icon("image", 15)}<span>Choose media</span></button>`;
  else
    html = `<div class="field-inline"><input ${attrs} type="${f.type === "number" ? "number" : f.type === "datetime" ? "datetime-local" : "text"}" value="${esc(Array.isArray(v) ? v.join(", ") : v)}" ${f.min !== undefined ? `min="${f.min}"` : ""} ${f.max !== undefined ? `max="${f.max}"` : ""}>${["url", "link", "product", "collection"].includes(f.type) ? `<button type="button" class="icon-button" data-link="${esc(path)}" aria-label="Choose ${esc(f.label)}" title="Choose ${esc(f.label)}">${icon("link", 15)}</button>` : ""}</div>`;
  const dynamicKind = f.dynamic_source || (!f.id.startsWith("style.") && ["text", "textarea", "richtext"].includes(f.type) ? "text" : f.type === "image" ? "image" : "");
  const sources = (state?.page?.kind === "product" || state?.page?.product_id) && dynamicKind ? (state.dynamic_sources?.[dynamicKind] || []) : [];
  if (sources.length) html += `<label class="field-help">Connect dynamic source<select data-dynamic-for="${esc(id)}"><option value="">Choose a custom data definition</option>${sources.map(source => `<option value="${esc(source.source)}" ${v === source.source ? "selected" : ""}>${esc(source.name)}</option>`).join("")}</select></label>`;
  return `<div class="field field-${esc(f.type)}"><label for="${esc(id)}">${esc(f.label || f.id)}</label>${html}${f.help ? `<span class="field-help">${esc(f.help)}</span>` : ""}</div>`;
}
function selectedFields(group) {
  const n = locate(selected);
  if (!n) return [];
  const s = spec(n.block.type),
    ids = s.groups?.[group] || s.groups?.[group.toLowerCase()];
  return (s.fields || []).filter((f) =>
    // Some labels only apply to particular layouts, such as a bottle or a tablet purchase box.
    (!f.layouts || f.layouts.includes(n.block.layout)) &&
    (ids
      ? ids.includes(f.id)
      : group === "Content"
        ? !f.id.startsWith("style.")
        : group === "Advanced"
          ? ["style.anchor_id", "style.css_class"].includes(f.id)
          : f.id.startsWith("style.")),
  );
}
// Settings groups replace the old Content, Style and Advanced tabs. Every field still renders
// exactly once; the open or closed state of each group is remembered between selections.
let openGroups;
try {
  openGroups = new Set(JSON.parse(localStorage.getItem("fs-editor-open-groups") || "null") || ["Content", "theme:Colours"]);
} catch {
  openGroups = new Set(["Content", "theme:Colours"]);
}
function wireGroups(root) {
  root.querySelectorAll("details[data-group]").forEach((details) => {
    details.ontoggle = () => {
      details.open ? openGroups.add(details.dataset.group) : openGroups.delete(details.dataset.group);
      try { localStorage.setItem("fs-editor-open-groups", JSON.stringify([...openGroups])); } catch { /* Private mode. */ }
    };
  });
}
function groupHTML(name, hint, body, key = name) {
  hint = hint.toLowerCase().replace(/^./, (c) => c.toUpperCase());
  return `<details class="settings-group" data-group="${esc(key)}" ${openGroups.has(key) ? "open" : ""}><summary><span class="settings-group-title">${esc(name)}</span>${hint ? `<small>${esc(hint)}</small>` : ""}</summary><div class="settings-group-body">${body}</div></details>`;
}
function fieldSection(f) {
  if (!f.id.startsWith("style.")) return "";
  const id = f.id.slice(6);
  if (/^(padding|margin)_/.test(id)) return "Spacing";
  if (/colou?r|background/.test(id)) return "Colours and background";
  if (id.startsWith("hide_")) return "Visibility";
  if (["anchor_id", "css_class"].includes(id)) return "Developer";
  return "Layout";
}
const isInterfaceLabel = (f) => f.type === "text" && /_label$/.test(f.id);
function groupFieldsHTML(block, fields) {
  const render = (f) => (f.id === "items" ? hotspotPreview(block) : "") + fieldHTML(f, block);
  const labels = fields.filter(isInterfaceLabel),
    tuck = labels.length >= 2,
    main = tuck ? fields.filter((f) => !isInterfaceLabel(f)) : fields,
    sections = new Map();
  for (const f of main) sections.set(fieldSection(f), [...(sections.get(fieldSection(f)) || []), f]);
  let html = [...sections]
    .map(([section, list]) =>
      (sections.size > 1 && section ? `<h3 class="settings-subheading">${esc(section)}</h3>` : "") + list.map(render).join(""))
    .join("");
  if (tuck)
    html += `<details class="settings-subgroup" data-group="Interface labels" ${openGroups.has("Interface labels") ? "open" : ""}><summary>Interface labels<small>${labels.length}</small></summary><p class="field-help">Button text and screen reader labels used inside this section.</p>${labels.map(render).join("")}</details>`;
  return { html, hint: [...sections.keys()].filter(Boolean).join(", ") };
}
const shellIcons = { header: "panel-top", footer: "panel-bottom", pages: "files", consent: "cookie" };
function shellRow(group, name) {
  const editable = (state.shell_schema?.fields || []).some((f) => f.group === group);
  // Store pages opens the page switcher, so every page is one click away.
  if (group === "pages")
    return `<button type="button" class="tree-shell" data-open-pages aria-haspopup="true">${icon(shellIcons[group])}<span class="tree-title"><span>${esc(name)}</span><small>${state.pages?.length || 0} page${state.pages?.length === 1 ? "" : "s"}</small></span><span class="tree-shell-go" aria-hidden="true">${icon("chevron-right", 14)}</span></button>`;
  if (!editable)
    return group === "consent" ? "" : `<div class="tree-static">${icon(shellIcons[group])}<span class="tree-title"><span>${esc(name)}</span><small>${boot.theme_id ? "Edit on the live theme" : "Set by the theme"}</small></span></div>`;
  return `<button type="button" class="tree-shell ${shellGroup === group ? "selected" : ""}" data-shell="${group}" aria-pressed="${shellGroup === group}">${icon(shellIcons[group])}<span class="tree-title"><span>${esc(name)}</span></span><span class="tree-shell-go" aria-hidden="true">${icon("chevron-right", 14)}</span></button>`;
}
function openShell(group) {
  selected = null;
  shellGroup = group;
  renderTree();
  renderSettings();
  send({ type: "select", block_id: null });
}
function renderShell() {
  const names = { header: "Store header", footer: "Store footer", consent: "Cookie banner", pages: "Store pages" };
  const obj = state.shell_settings;
  // Lets the shared field wiring find the shell schema; never serialised.
  Object.defineProperty(obj, "type", { value: "__shell", enumerable: false, configurable: true });
  $("#editor").classList.add("has-selection");
  $("#selection-header").innerHTML =
    `<div class="selection-title"><span class="selection-icon" aria-hidden="true">${icon(shellIcons[shellGroup], 18)}</span><div class="selection-name"><h2>${esc(names[shellGroup])}</h2><span class="subtle">Shared by every page</span></div></div>`;
  // Shared settings go live on their own, never as a side effect of publishing a page.
  $("#settings-content").innerHTML =
    `<div class="settings-fields">${state.shell_schema.fields
      .filter((f) => f.group === shellGroup)
      .map((f) => fieldHTML(f, obj))
      .join("")}<p class="field-help">Save keeps these changes in your draft. Publish makes the header and footer live on every page.</p></div><div class="settings-footer is-sticky settings-footer-split"><button type="button" id="save-shell">Save</button><button type="button" id="publish-shell" class="primary-button" ${state.can_publish ? "" : "disabled"}>Publish header and footer</button></div>`;
  window.FastShopRichText?.mountAll($("#settings-content"));
  wireFields(obj, (path, v) => {
    set(state.shell_settings, path, v); // The saved copy may have replaced obj.
    shellDirty = themeDirty = true;
    status("Unsaved changes");
  });
  const saveShellNow = async (publish) => {
    const buttons = [$("#save-shell"), $("#publish-shell")];
    buttons.forEach((b) => b && (b.disabled = true));
    try {
      if (publish) await saveShell(true);
      else await saveTheme();
      status(dirty || themeDirty ? "Unsaved changes" : "Saved");
      toast(publish ? "Header and footer published" : "Header and footer saved");
      needsFullPreview = true;
      preview();
    } catch (e) {
      showError(e);
    } finally {
      if ($("#save-shell")) $("#save-shell").disabled = false;
      if ($("#publish-shell")) $("#publish-shell").disabled = !state.can_publish;
    }
  };
  $("#save-shell").onclick = () => saveShellNow(false);
  $("#publish-shell").onclick = () => saveShellNow(true);
}
function renderSettings() {
  const n = locate(selected);
  if (!n && shellGroup && state.shell_schema) return renderShell();
  $("#editor").classList.toggle("has-selection", Boolean(n));
  if (!n) {
    $("#selection-header").innerHTML =
      `<div class="selection-title"><span class="selection-icon" aria-hidden="true">${icon("file-text", 18)}</span><div class="selection-name"><h2>Page settings</h2><span class="subtle">Build your storefront</span></div></div>`;
    $("#settings-content").innerHTML =
      `<div class="settings-empty"><div class="empty-icon" aria-hidden="true">${icon("mouse-pointer-click", 28)}</div><strong>Make this page your own</strong><p>Select a section in the preview or in the list on the left to change its settings, or drag a widget onto the page.</p><button type="button" id="empty-add" class="primary-button">${icon("plus", 15)}<span>Add section</span></button></div>`;
    $("#empty-add").onclick = () => openPicker();
    return;
  }
  const block = n.block,
    list = siblings(n),
    index = list.indexOf(block),
    kind = n.parent ? "block" : "section",
    parentName = n.parent ? label(n.parent) : "";
  $("#selection-header").innerHTML =
    `<button type="button" class="selection-back" data-selected-action="parent" title="Select parent" aria-label="${n.parent ? `Back to ${esc(parentName)}` : "Back to page settings"}">${icon("arrow-left", 15)}<span>${n.parent ? esc(parentName) : "Page"}</span></button><div class="selection-title"><span class="selection-icon" aria-hidden="true">${blockIcon(block.type, 18)}</span><div class="selection-name"><h2>${esc(label(block))}</h2><span class="subtle">${esc(spec(block.type).category || "")}${block.hidden ? " · Hidden" : ""}</span></div></div><div class="selection-actions" role="toolbar" aria-label="${kind === "block" ? "Block" : "Section"} actions">${[
      ["up", "arrow-up", "Move block up", index === 0],
      ["down", "arrow-down", "Move block down", index === list.length - 1],
      ["hide", block.hidden ? "eye-off" : "eye", block.hidden ? "Show" : "Hide", false],
      ["duplicate", "copy", "Duplicate", false],
      ["delete", "trash-2", "Delete", false],
    ]
      .map(
        ([a, glyph, l, disabled]) =>
          `<button type="button" class="icon-button ${a === "hide" && block.hidden ? "is-on" : ""} ${a === "delete" ? "is-danger" : ""}" data-selected-action="${a}" aria-label="${l}" title="${l}" ${disabled ? "disabled" : ""}>${icon(glyph, 16)}</button>`,
      )
      .join("")}</div>`;
  const containers = walk().filter(
    (x) =>
      spec(x.block.type).accepts_children &&
      !walk([n.block]).some((c) => c.block.id === x.block.id),
  );
  const advanced = selectedFields("Advanced"),
    taken = new Set(advanced.map((f) => f.id)),
    content = selectedFields("Content").filter((f) => !taken.has(f.id));
  content.forEach((f) => taken.add(f.id));
  const style = selectedFields("Style").filter((f) => !taken.has(f.id));
  const contentPart = groupFieldsHTML(block, content),
    stylePart = groupFieldsHTML(block, style),
    advancedPart = groupFieldsHTML(block, advanced);
  const moveInto = `<h3 class="settings-subheading">Placement</h3><div class="field"><label for="move-into">Move into container</label><select id="move-into"><option value="">Choose container</option><option value="root">Page (top level)</option>${containers.map((x) => `<option value="${esc(x.block.id)}">${esc(label(x.block))} · ${esc(x.block.id.slice(-6))}</option>`).join("")}</select></div>`;
  $("#settings-content").innerHTML =
    (content.length ? groupHTML("Content", "", contentPart.html) : "") +
    (style.length ? groupHTML("Style", stylePart.hint, stylePart.html) : "") +
    groupHTML("Advanced", [advancedPart.hint, "placement"].filter(Boolean).join(", "), advancedPart.html + moveInto) +
    `<div class="settings-footer"><button type="button" class="settings-remove" data-selected-action="delete">${icon("trash-2", 15)}<span>Remove ${kind}</span></button></div>`;
  $$("[data-selected-action]").forEach(
    (b) => (b.onclick = () => action(b.dataset.selectedAction)),
  );
  wireGroups($("#settings-content"));
  window.FastShopRichText?.mountAll($("#settings-content"));
  wireFields(n.block, (path, v) => {
    setField(n.block, path, v);
    changed(`Edit ${fieldName(n.block, path)}`);
  });
  // Replacing the panel also replaces its delegated preview input handler.
  $("#settings-content").oninput = null;
  wireHotspotPreview($("#settings-content"), n.block);
  if ($("#move-into"))
    $("#move-into").onchange = (e) => {
      if (e.target.value === "root")
        mutate("Move to page", () => {
          siblings(n).splice(siblings(n).indexOf(n.block), 1);
          state.document.blocks.push(n.block);
        });
      else if (e.target.value) move(selected, e.target.value, "inside");
    };
}
// "items.0.heading" reads "Heading (item 1)" in the history list.
function fieldName(block, path) {
  const f = findField(path, spec(block.type).fields || []),
    item = path.match(/\.(\d+)\./);
  return `${f.label || path.replaceAll("_", " ")}${item ? ` (item ${Number(item[1]) + 1})` : ""}`;
}
function findField(path, fields) {
  for (const f of fields) {
    if (f.id === path) return f;
    if (path.startsWith(f.id + "."))
      return findField(
        path
          .slice(f.id.length + 1)
          .split(".")
          .slice(1)
          .join("."),
        f.fields || f.item_fields || [],
      );
  }
  return {};
}
function wireFields(obj, update, root = "#settings-content") {
  document.querySelectorAll(`${root} [data-dynamic-for]`).forEach(select => {
    select.onchange = () => {
      if (!select.value) return;
      const input = document.getElementById(select.dataset.dynamicFor);
      if (!input) return;
      input.value = select.value;
      input.dispatchEvent(new Event("input", {bubbles:true}));
    };
  });

  const host = $(root);
  host.querySelectorAll("[data-setting]").forEach(
    (el) =>
      (el.oninput = () => {
        let v = el.type === "checkbox" ? el.checked : el.value;
        if (["number", "range"].includes(el.dataset.type)) v = Number(v);
        if (el.dataset.setting.endsWith(".theme")) {
          const itemPath = el.dataset.setting.slice(0, -6),
            repeaterPath = itemPath.split(".").slice(0, -1).join("."),
            field = findField(repeaterPath, spec(obj.type).fields || []),
            defaults = field.theme_defaults?.[v];
          if (defaults) {
            update(itemPath, { ...get(obj, itemPath), ...copy(defaults), theme: v });
            renderSettings();
            return;
          }
        }
        // Rich text keeps only the formatting the storefront allow-list renders.
        if (el.dataset.type === "richtext" && window.FastShopRichText)
          v = window.FastShopRichText.sanitize(v);
        update(el.dataset.setting, v);
        const companion = host.querySelector(
          `[data-companion="${CSS.escape(el.dataset.setting)}"]`,
        );
        if (companion) companion.value = v;
      }),
  );
  host.querySelectorAll("[data-companion]").forEach(
    (el) =>
      (el.onchange = () => {
        const input = host.querySelector(
          `[data-setting="${CSS.escape(el.dataset.companion)}"]`,
        );
        if (["colour", "color"].includes(input?.dataset.type)) {
          update(el.dataset.companion, el.value);
          return;
        }
        input.value = el.value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }),
  );
  host.querySelectorAll("[data-swatch]").forEach(
    (el) =>
      (el.onclick = () => {
        const input = host.querySelector(
          `[data-setting="${CSS.escape(el.dataset.swatch)}"]`,
        );
        input.value = el.dataset.colour;
        input.dispatchEvent(new Event("input"));
      }),
  );
  host.querySelectorAll("[data-media]").forEach(
    (b) =>
      (b.onclick = () =>
        openMedia((url) => {
          update(b.dataset.media, url);
          renderSettings();
        })),
  );
  host.querySelectorAll("[data-icon]").forEach(
    (b) =>
      (b.onclick = () =>
        openIcon(
          (value) => {
            update(b.dataset.icon, value);
            renderSettings();
          },
          text(get(obj, b.dataset.icon)),
        )),
  );
  host.querySelectorAll("[data-icon-clear]").forEach(
    (b) =>
      (b.onclick = () => {
        update(b.dataset.iconClear, "");
        renderSettings();
      }),
  );
  host.querySelectorAll("[data-link]").forEach(
    (b) =>
      (b.onclick = () =>
        openLink(
          (url) => {
            update(b.dataset.link, url);
            renderSettings();
          },
          b.dataset.resourceType ||
            findField(b.dataset.link, spec(obj.type).fields || []).type,
        )),
  );
  host.querySelectorAll("[data-repeat-add]").forEach(
    (b) =>
      (b.onclick = () => {
        const path = b.dataset.repeatAdd,
          f = findField(path, spec(obj.type).fields || []),
          rows = copy(get(obj, path) || []);
        if (rows.length >= (f.max ?? 50)) return;
        const item = f.item_default ? copy(f.item_default) : {};
        for (const field of f.fields || f.item_fields || [])
          if (item[field.id] === undefined)
            item[field.id] = copy(field.default ?? "");
        rows.push(f.value_list ? "" : item);
        openItems.add(`${selected}|${path}.${rows.length - 1}`);
        update(path, rows);
        renderSettings();
      }),
  );
  host.querySelectorAll("[data-repeat-action]").forEach(
    (b) =>
      (b.onclick = () => {
        const { path, index, repeatAction: a } = b.dataset,
          rows = copy(get(obj, path)),
          i = Number(index),
          f = findField(path, spec(obj.type).fields || []);
        if (a === "remove") {
          if (rows.length <= (f.min ?? 0)) return;
          if (path === "items") hotspotRowsChanged(obj, i, null);
          rows.splice(i, 1);
        } else {
          const j = i + (a === "up" ? -1 : 1);
          if (j < 0 || j >= rows.length) return;
          if (path === "items") hotspotRowsChanged(obj, i, j);
          [rows[i], rows[j]] = [rows[j], rows[i]];
          const from = `${selected}|${path}.${i}`, to = `${selected}|${path}.${j}`,
            fromOpen = openItems.has(from), toOpen = openItems.has(to);
          fromOpen ? openItems.add(to) : openItems.delete(to);
          toOpen ? openItems.add(from) : openItems.delete(from);
        }
        update(path, rows);
        renderSettings();
      }),
  );
  host.querySelectorAll("details[data-repeater]").forEach((el) => {
    el.addEventListener("toggle", () => {
      const key = `${selected}|${el.dataset.repeater}.${el.dataset.index}`;
      el.open ? openItems.add(key) : openItems.delete(key);
    });
  });
  host.querySelectorAll("[data-repeater]").forEach((el) => {
    el.ondragstart = (e) => {
      e.stopPropagation();
      e.dataTransfer.setData(
        "application/x-fastshop-repeater",
        JSON.stringify({
          path: el.dataset.repeater,
          index: Number(el.dataset.index),
        }),
      );
    };
    el.ondragover = (e) => e.preventDefault();
    el.ondrop = (e) => {
      e.preventDefault();
      e.stopPropagation();
      try {
        const from = JSON.parse(
          e.dataTransfer.getData("application/x-fastshop-repeater"),
        );
        if (from.path !== el.dataset.repeater) return;
        if (from.path === "items") hotspotRowsChanged(obj, from.index, Number(el.dataset.index));
        const rows = copy(get(obj, from.path)),
          [item] = rows.splice(from.index, 1);
        rows.splice(Number(el.dataset.index), 0, item);
        update(from.path, rows);
        renderSettings();
      } catch {
        /* Unrelated drag type. */
      }
    };
  });
}
// Theme settings are grouped like the block settings panel; each group remembers whether it
// was open. Any token the server adds later lands in "More settings", so nothing is hidden.
const themeGroups = [
  ["Colours", ["accent", "background", "surface", "text"]],
  ["Typography", ["font", "headings"]],
  ["Layout", ["width", "spacing", "radius", "hero"]],
];
const themeLabels = {
  accent: "Accent colour", background: "Page background", surface: "Surface colour",
  text: "Text colour", font: "Body font", headings: "Heading font", width: "Page width",
  spacing: "Section spacing", radius: "Corner radius", hero: "Hero alignment",
};
function themeField(id, v) {
  return {
    id,
    label: themeLabels[id] || id.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()),
    type: state.theme_choices?.[id]
      ? "select"
      : typeof v === "number"
        ? "number"
        : typeof v === "string" && v.startsWith("#")
          ? "colour"
          : "text",
    // "modern" with "Arial, sans-serif" reads "Modern (Arial)"; "compact" reads "Compact (55px)".
    options: options(state.theme_choices?.[id]).map((o) => {
      const name = String(o.value).replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()),
        detail = String(o.label).split(",")[0].trim();
      return { ...o, label: detail && detail.toLowerCase() !== String(o.value).toLowerCase() ? `${name} (${detail})` : name };
    }),
    default: v,
  };
}
function renderTheme() {
  const ids = Object.keys(state.theme_settings),
    grouped = new Set(themeGroups.flatMap(([, list]) => list)),
    groups = [...themeGroups, ["More settings", ids.filter((id) => !grouped.has(id))]]
      .map(([name, list]) => [name, list.filter((id) => ids.includes(id))])
      .filter(([, list]) => list.length);
  $("#left-content").innerHTML =
    `<div class="theme-fields">${groups
      .map(([name, list]) =>
        groupHTML(name, "", list.map((id) => fieldHTML(themeField(id, state.theme_settings[id]), state.theme_settings)).join(""), `theme:${name}`),
      )
      .join("")}</div><div class="settings-footer is-sticky"><button type="button" id="save-theme" class="primary-button">Save theme settings</button></div>`;
  wireGroups($("#left-content"));
  wireFields(
    state.theme_settings,
    (p, v) => {
      set(state.theme_settings, p, v);
      themeDirty = designDirty = true;
      status("Unsaved changes");
    },
    "#left-content",
  );
  $("#save-theme").onclick = async () => {
    try {
      await saveTheme();
      status(dirty || themeDirty ? "Unsaved changes" : "Saved");
      toast("Theme settings saved");
      preview();
    } catch (e) {
      showError(e);
    }
  };
}
async function saveShell(publish = false) {
  const submitted = JSON.stringify(state.shell_settings);
  try {
    const r = await api("/shell-settings", "PUT", {
      shell_settings: state.shell_settings,
      version: state.site_version,
      ...(publish ? { publish: true } : {}),
    });
    state.site_version = r.version;
    if (JSON.stringify(state.shell_settings) === submitted) {
      state.shell_settings = r.shell_settings;
      shellDirty = false;
      themeDirty = designDirty;
    }
  } catch (e) {
    if (e.conflict) {
      e.conflict = false;
      e.message = "Store settings changed. Reload the editor before saving the header and footer.";
    }
    throw e;
  }
}
async function saveTheme() {
  if (!themeDirty) return;
  if (shellDirty) await saveShell();
  if (!designDirty) {
    themeDirty = shellDirty;
    return;
  }
  const submitted = JSON.stringify(state.theme_settings);
  try {
    const r = await api("/theme-settings", "PUT", {
      theme_settings: state.theme_settings,
      version: state.site_version,
    });
    state.site_version = r.version;
    if (boot.theme_id) state.version = r.version;
    if (JSON.stringify(state.theme_settings) === submitted) {
      state.theme_settings = r.theme_settings;
      designDirty = false;
    } else designDirty = JSON.stringify(state.theme_settings) !== JSON.stringify(r.theme_settings);
    themeDirty = designDirty || shellDirty;
  } catch (e) {
    // Theme and page versions are distinct. Reload before retrying a theme conflict.
    if (e.conflict) {
      e.conflict = false;
      e.message =
        "Theme settings changed. Reload the editor before saving them.";
    }
    throw e;
  }
}
async function openMedia(callback) {
  mediaCallback = callback;
  openDialog("#media-dialog");
  $("#media-grid").innerHTML = `<div class="editor-skeleton" role="status" aria-label="Loading media"></div>`.repeat(3);
  try {
    const r = await api("/media");
    $("#media-grid").innerHTML =
      r.media
        .map(
          (m) =>
            `<button data-media-url="${esc(m.url)}"><img src="${esc(m.url)}" alt="${esc(m.alt || m.title)}"><small>${esc(m.title || m.alt || "Image")}</small></button>`,
        )
        .join("") ||
      '<p class="subtle">Upload an image to start your media library.</p>';
    $$("[data-media-url]").forEach(
      (b) =>
        (b.onclick = () => {
          callback(b.dataset.mediaUrl);
          $("#media-dialog").close();
        }),
    );
  } catch (e) {
    showError(e);
  }
}
function iconChoiceHTML(icon, current) {
  return `<button type="button" class="icon-choice" data-icon-value="${esc(icon.id)}" aria-pressed="${String(icon.id === current)}" title="${esc(icon.label)}"><span class="icon-choice-glyph" aria-hidden="true">${icon.svg}</span><span>${esc(icon.label)}</span></button>`;
}
function customIconChoiceHTML(media, current) {
  const label = media.title || media.alt || "Custom icon";
  const glyph = media.svg || `<img src="${esc(media.url)}" alt="">`;
  return `<button type="button" class="icon-choice" data-icon-value="${esc(media.url)}" aria-pressed="${String(media.url === current)}" title="${esc(label)}"><span class="icon-choice-glyph" aria-hidden="true">${glyph}</span><span>${esc(label)}</span></button>`;
}
function wireIconChoices(callback) {
  const choices = $$("#icon-dialog [data-icon-value]");
  choices.forEach((button) => {
    button.onclick = () => {
      callback(button.dataset.iconValue);
      $("#icon-dialog").close();
    };
    button.onkeydown = (event) => {
      const keys = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"];
      if (!keys.includes(event.key)) return;
      const grid = button.closest(".icon-grid");
      const gridChoices = [...grid.querySelectorAll("[data-icon-value]")];
      const position = gridChoices.indexOf(button);
      const columns = Math.max(1, getComputedStyle(grid).gridTemplateColumns.split(" ").length);
      let next = position;
      if (event.key === "ArrowRight") next += 1;
      if (event.key === "ArrowLeft") next -= 1;
      if (event.key === "ArrowDown") next += columns;
      if (event.key === "ArrowUp") next -= columns;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = gridChoices.length - 1;
      next = Math.min(gridChoices.length - 1, Math.max(0, next));
      event.preventDefault();
      gridChoices[next]?.focus();
    };
  });
}
async function openIcon(callback, current = "") {
  iconCallback = callback;
  $("#icon-search").value = "";
  $("#icon-grid").innerHTML =
    `<div class="editor-skeleton" role="status" aria-label="Loading icons"></div>`.repeat(8);
  $("#custom-icon-grid").innerHTML = "";
  openDialog("#icon-dialog");
  try {
    if (!iconLibrary) iconLibrary = await api("/icons");
    const draw = () => {
      const query = $("#icon-search").value.trim().toLowerCase();
      const matches = (entry) => `${entry.label} ${entry.id}`.toLowerCase().includes(query);
      const builtins = (iconLibrary.icons || []).filter(matches);
      const custom = (iconLibrary.custom || []).filter((entry) =>
        `${entry.title || ""} ${entry.alt || ""}`.toLowerCase().includes(query),
      );
      $("#icon-grid").innerHTML = builtins.map((icon) => iconChoiceHTML(icon, current)).join("")
        || '<p class="subtle">No built-in icons match that search.</p>';
      $("#custom-icon-grid").innerHTML = custom.map((media) => customIconChoiceHTML(media, current)).join("")
        || '<p class="subtle">No matching icons in this site’s media library.</p>';
      $("#custom-icon-section").hidden = !custom.length && Boolean(query);
      $("#icon-results-summary").textContent = `${builtins.length} built-in icon${builtins.length === 1 ? "" : "s"}${custom.length ? ` and ${custom.length} site media item${custom.length === 1 ? "" : "s"}` : ""}`;
      wireIconChoices(callback);
    };
    $("#icon-search").oninput = draw;
    draw();
    $("#icon-search").focus();
  } catch (e) {
    showError(e);
    $("#icon-dialog").close();
  }
}
async function openLink(callback, type = "url") {
  linkCallback = callback;
  $("#link-search").value = "";
  $("#picker-title").textContent = "Choose link";
  let resources = { pages: state.pages, products: [], collections: [] };
  try {
    resources = await api("/resources");
  } catch (e) {
    showError(e);
  }
  const draw = () => {
    const q = $("#link-search").value.toLowerCase();
    const rows =
      type === "product"
        ? resources.products
        : type === "collection"
          ? resources.collections
          : [
              ...resources.pages,
              ...resources.products,
              ...resources.collections,
            ].filter((r) => r.path);
    $("#link-results").innerHTML =
      rows
        .filter((p) =>
          `${p.title || p.name} ${p.path || ""}`.toLowerCase().includes(q),
        )
        .map(
          (p) =>
            `<button class="link-result" data-link-url="${esc(["product", "collection"].includes(type) ? p.id : p.path)}">${esc(p.title || p.name)} <small>${esc(p.path || "Collection")}</small></button>`,
        )
        .join("") || '<p class="subtle">No matching resources.</p>';
    $$("[data-link-url]").forEach(
      (b) =>
        (b.onclick = () => {
          callback(b.dataset.linkUrl);
          $("#link-dialog").close();
        }),
    );
  };
  $("#link-search").placeholder = ["product", "collection"].includes(type)
    ? `Search ${type}s`
    : "Search pages or enter a URL";
  $("#link-apply").hidden = ["product", "collection"].includes(type);
  $("#link-search").oninput = draw;
  draw();
  openDialog("#link-dialog");
  $("#link-search").focus();
}
async function save() {
  if (saving || !state) return false;
  sanitizeBadItemLocaleKeys(state.document, state.schema);
  saving = true;
  status("Saving…");
  $("#save").disabled = true;
  const submitted = JSON.stringify(state.document);
  try {
    await saveTheme();
    const r = await api("/draft", "PUT", {
      document: state.document,
      version: state.version,
    });
    state.version = r.version;
    if (boot.theme_id) state.site_version = r.version;
    lastSaved = submitted;
    dirty = JSON.stringify(state.document) !== lastSaved;
    status(dirty || themeDirty ? "Unsaved changes" : "Saved");
    $("#editor-error").hidden = true;
    toast("Changes saved");
    return true;
  } catch (e) {
    status("Error");
    showError(e);
    return false;
  } finally {
    saving = false;
    $("#save").disabled = false;
  }
}
function renderPages(query = "") {
  const groups = new Map();
  state.pages
    .filter((p) =>
      `${p.title} ${p.kind}`.toLowerCase().includes(query.toLowerCase()),
    )
    .forEach((p) => groups.set(p.kind, [...(groups.get(p.kind) || []), p]));
  $("#page-options").innerHTML = [...groups]
    .map(
      ([kind, pages]) =>
        `<h3>${esc(kind.replaceAll("_", " "))}</h3>${pages.map((p) => `<a href="/admin/sites/${encodeURIComponent(boot.site_id)}/editor?page=${encodeURIComponent(p.id)}&theme=${encodeURIComponent(boot.theme_id || '')}"${p.id === boot.page_id ? ' aria-current="page"' : ""}>${esc(p.title)}</a>`).join("")}`,
    )
    .join("");
}
async function load() {
  try {
    state = await api("/document");
    needsFullPreview = true;
    if (state.schema.blocks) state.schema = state.schema.blocks;
    if (Array.isArray(state.schema))
      state.schema = Object.fromEntries(state.schema.map((s) => [s.type, s]));
    const loadedDocument = copy(state.document);
    state.document = copy(state.document);
    dirty = sanitizeBadItemLocaleKeys(state.document, state.schema) > 0;
    selected = null;
    shellGroup = null;
    themeDirty = designDirty = shellDirty = false;
    lastSaved = JSON.stringify(loadedDocument);
    history = [];
    snapshot("Opened page");
    $("#current-page").textContent = state.page.title;
    $("#canvas-page-title").textContent = state.page.title;
    $("#theme-name").textContent = state.site.name;
    $("#preview-link").href =
      boot.theme_id ? `/admin/sites/${encodeURIComponent(boot.site_id)}/themes/${encodeURIComponent(boot.theme_id)}/preview?page=${encodeURIComponent(boot.page_id)}` :
      `/admin/sites/${encodeURIComponent(boot.site_id)}/preview/${encodeURIComponent(boot.page_id)}`;
    $("#publish").disabled = !state.can_publish;
    renderPages();
    renderLeft();
    renderSettings();
    await preview();
    status(dirty ? "Unsaved changes" : "Saved");
  } catch (e) {
    showError(e);
    status("Error");
  }
}
frame.addEventListener("dragover", (e) => {
  e.preventDefault();
});
frame.addEventListener("drop", (e) => {
  e.preventDefault();
  const payload = readDrag(e);
  const r = frame.getBoundingClientRect();
  const target = frame.contentDocument
    .elementFromPoint(e.clientX - r.left, e.clientY - r.top)
    ?.closest("[data-block-id]");
  let position = "after";
  if (target) {
    const rect = target.getBoundingClientRect();
    const ratio = (e.clientY - r.top - rect.top) / rect.height;
    position =
      ratio < 0.2
        ? "before"
        : ratio > 0.8
          ? "after"
          : spec(locate(target.dataset.blockId)?.block.type).accepts_children
            ? "inside"
            : "after";
  }
  drop(target?.dataset.blockId || null, position, payload);
});
window.addEventListener("message", async (e) => {
  if (e.source !== frame.contentWindow) return;
  const d = e.data;
  if (
    d?.source !== "fastshop-editor" ||
    d.site_id !== boot.site_id ||
    d.page_id !== boot.page_id
  )
    return;
  if (d.type === "shortcut") {
    if (d.key === "s" || d.key === "save") save();
    if (d.key === "z") d.shift ? redo() : undo();
    if (d.key === "y") redo();
    if (d.key === "d") action("duplicate");
  }
  if (d.type === "ready") send({ type: "context", block_id: selected, labels: canvasLabels() });
  if (d.type === "navigate") {
    const target = state?.pages?.find((page) => page.id === d.target_page_id);
    if (!target || target.id === boot.page_id) return;
    if ((dirty || themeDirty) && !(await save())) return;
    const url = new URL(location.href);
    url.searchParams.set("page", target.id);
    location.assign(url);
    return;
  }
  if (d.type === "select") select(d.block_id);
  if (d.type === "shell") openShell(d.group);
  if (d.type === "inline") {
    if (d.shell) {
      const field = (state.shell_schema?.fields || []).find((item) => item.id === d.field);
      if (!field || !["text", "textarea"].includes(field.type)) return;
      setField(state.shell_settings, d.field, d.value);
      shellDirty = themeDirty = true;
      status("Unsaved changes");
      renderShell();
      return;
    }
    const n = locate(d.block_id),
      f = n && findField(d.field, spec(n.block.type).fields || []);
    if (!f || !["text", "textarea", "richtext"].includes(f.type)) return;
    setField(n.block, d.field, d.value);
    dirty = true;
    status("Unsaved changes");
    snapshot(`Edit ${f.label}`);
    renderTree();
    renderSettings();
  }
  if (d.type === "drop") drop(d.block_id, d.position, d.payload || dragPayload);
  if (d.type === "action") action(d.action, d.block_id);
  if (d.type === "media") {
    const n = locate(d.block_id);
    if (n && findField(d.field || "image", spec(n.block.type).fields || []).type === "image")
      openMedia((url) => {
        setField(n.block, d.field || "image", url);
        changed("Change image", true);
      });
  }
});
$$("[data-mode]").forEach(
  (b) =>
    (b.onclick = () => {
      const shell = $("#editor");
      shell.classList.toggle(
        "is-panel-open",
        mode !== b.dataset.mode || !shell.classList.contains("is-panel-open"),
      );
      if (window.matchMedia("(max-width: 760px)").matches) select(null);
      mode = b.dataset.mode;
      if (state) renderLeft();
    }),
);
$("#mobile-preview").onclick = () => {
  $("#editor").classList.remove("is-panel-open");
  select(null);
};
$$("[data-device]").forEach(
  (b) =>
    (b.onclick = () => {
      frame.dataset.device = b.dataset.device;
      $("#canvas-device").textContent = b.dataset.device.replace(/^./, (c) =>
        c.toUpperCase(),
      );
      $$("[data-device]").forEach((t) =>
        t.setAttribute("aria-pressed", String(t === b)),
      );
    }),
);
$$("[data-close-dialog]").forEach(
  (b) => (b.onclick = () => b.closest("dialog").close()),
);
// Native modal dialogs provide Escape handling and make the background inert.
// Keep labels and focus restoration explicit, including when a tree rerendered.
$$("dialog").forEach((dialog) => {
  const heading = dialog.querySelector("h2");
  if (heading) {
    heading.id ||= `${dialog.id}-title`;
    dialog.setAttribute("aria-labelledby", heading.id);
  }
  new MutationObserver(() => {
    if (dialog.open) {
      requestAnimationFrame(() => {
        if (!dialog.contains(document.activeElement))
          dialog.querySelector("input, button, [tabindex]")?.focus();
      });
    }
  }).observe(dialog, { attributes: true, attributeFilter: ["open"] });
  dialog.addEventListener("close", () => {
    if (dialog.editorOpener?.isConnected) dialog.editorOpener.focus();
    else if (!$("dialog[open]")) $("#left-title").focus();
  });
  dialog.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll("button, input, select, textarea, a[href], [tabindex]")]
      .filter((el) => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  });
});
$("#left-title").tabIndex = -1;
const pageSwitcher = $(".page-switcher");
pageSwitcher.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    pageSwitcher.open = false;
    pageSwitcher.querySelector("summary").focus();
  }
});
document.addEventListener("click", (e) => {
  if (!pageSwitcher.contains(e.target)) pageSwitcher.open = false;
});
const uploadZone = $(".upload-label");
uploadZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  uploadZone.classList.add("drag-over");
});
uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("drag-over"));
uploadZone.addEventListener("drop", (e) => {
  e.preventDefault();
  uploadZone.classList.remove("drag-over");
  const files = e.dataTransfer.files;
  if (!files.length || $("#media-upload").disabled) return;
  $("#media-upload").files = files;
  $("#media-upload").dispatchEvent(new Event("change", { bubbles: true }));
});
// Resizable side panels. Widths are stored per browser. The preview never drops below
// STAGE_MIN, and narrow layouts (overlay panels) ignore stored widths entirely.
const PANEL_LIMITS = { left: [220, 560], right: [260, 560] },
  STAGE_MIN = 360,
  panelStack = window.matchMedia("(max-width: 760px)");
const panelKey = (side) => `fs-editor-width-${side}`;
function storedWidth(side) {
  try {
    const value = Number(localStorage.getItem(panelKey(side)));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}
function panelElement(side) {
  return $(side === "left" ? "#editor-left" : "#editor-right");
}
function panelBounds(side) {
  const [min, max] = PANEL_LIMITS[side],
    other = panelElement(side === "left" ? "right" : "left").getBoundingClientRect().width,
    rail = $(".editor-rail").getBoundingClientRect().width,
    room = window.innerWidth - rail - other - STAGE_MIN;
  return [min, Math.max(min, Math.min(max, Math.floor(room)))];
}
function setPanelWidth(side, width, persist = false) {
  const [min, max] = panelBounds(side),
    value = Math.round(Math.min(max, Math.max(min, width)));
  $("#editor").style.setProperty(`--editor-${side}-w`, `${value}px`);
  if (persist)
    try { localStorage.setItem(panelKey(side), String(value)); } catch { /* Private mode. */ }
  updateResizer(side);
  return value;
}
function updateResizer(side) {
  const handle = $(`#resize-${side}`);
  if (!handle) return;
  const [min, max] = panelBounds(side);
  handle.setAttribute("aria-valuemin", String(min));
  handle.setAttribute("aria-valuemax", String(max));
  handle.setAttribute("aria-valuenow", String(Math.round(panelElement(side).getBoundingClientRect().width)));
  handle.setAttribute("aria-valuetext", `${Math.round(panelElement(side).getBoundingClientRect().width)} pixels wide`);
}
function applyPanelWidths() {
  const shell = $("#editor");
  for (const side of ["left", "right"]) {
    shell.style.removeProperty(`--editor-${side}-w`);
    if (panelStack.matches) continue;
    const stored = storedWidth(side);
    if (stored) setPanelWidth(side, stored);
  }
  // A window too narrow for the defaults gives the room back to the preview.
  for (const side of ["right", "left"]) {
    if (panelStack.matches) break;
    const [, max] = panelBounds(side);
    if (panelElement(side).getBoundingClientRect().width > max) setPanelWidth(side, max);
    updateResizer(side);
  }
}
function resetPanelWidth(side) {
  try { localStorage.removeItem(panelKey(side)); } catch { /* Private mode. */ }
  applyPanelWidths();
}
$$("[data-resize]").forEach((handle) => {
  const side = handle.dataset.resize;
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || panelStack.matches) return;
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    const startX = e.clientX,
      startWidth = panelElement(side).getBoundingClientRect().width,
      shell = $("#editor");
    shell.classList.add("is-resizing");
    handle.classList.add("is-active");
    const moveHandler = (event) =>
      setPanelWidth(side, startWidth + (side === "left" ? event.clientX - startX : startX - event.clientX));
    const stop = () => {
      handle.removeEventListener("pointermove", moveHandler);
      handle.removeEventListener("pointerup", stop);
      handle.removeEventListener("pointercancel", stop);
      shell.classList.remove("is-resizing");
      handle.classList.remove("is-active");
      setPanelWidth(side, panelElement(side).getBoundingClientRect().width, true);
    };
    handle.addEventListener("pointermove", moveHandler);
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
  });
  handle.addEventListener("dblclick", () => resetPanelWidth(side));
  handle.addEventListener("keydown", (e) => {
    const width = panelElement(side).getBoundingClientRect().width,
      step = e.shiftKey ? 64 : 16,
      grow = side === "left" ? 1 : -1,
      [min, max] = panelBounds(side);
    let next = null;
    if (e.key === "ArrowRight") next = width + step * grow;
    if (e.key === "ArrowLeft") next = width - step * grow;
    if (e.key === "Home") next = min;
    if (e.key === "End") next = max;
    if (e.key === "Enter") {
      e.preventDefault();
      resetPanelWidth(side);
      return;
    }
    if (next === null) return;
    e.preventDefault();
    setPanelWidth(side, next, true);
  });
});
let resizeFrame;
window.addEventListener("resize", () => {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(applyPanelWidths);
});
applyPanelWidths();
$("#undo").onclick = undo;
$("#redo").onclick = redo;
$("#save").onclick = save;
$("#page-search").oninput = (e) => renderPages(e.target.value);
$("#publish").onclick = () => openDialog("#publish-dialog");
$("#confirm-publish").onclick = async () => {
  if (!state) return;
  $("#publish-dialog").close();
  if ((dirty || themeDirty) && !(await save())) return;
  $("#publish").disabled = true;
  $("#publish").setAttribute("aria-busy", "true");
  status("Publishing…");
  try {
    const r = await api("/publish", "POST", {
      version: state.version,
      site_version: state.site_version,
    });
    state.version = r.version;
    state.site_version = r.site_version;
    status("Published");
    toast("Page published");
    $(".live-badge").textContent = "Live";
    $("#editor-error").hidden = true;
  } catch (e) {
    showError(e);
    status("Error");
  } finally {
    $("#publish").disabled = !state.can_publish;
    $("#publish").removeAttribute("aria-busy");
  }
};
$("#revisions").onclick = async () => {
  if (!state) return;
  openDialog("#revision-dialog");
  $("#revision-list").innerHTML = `<div class="editor-skeleton" role="status" aria-label="Loading revisions"></div>`;
  try {
    const r = await api("/revisions");
    $("#revision-list").innerHTML =
      r.revisions
        .map(
          (r) =>
            `<div class="revision-item"><span>${esc(r.action || "Draft")}<small>${esc(new Date(r.created_at).toLocaleString("en-GB"))}</small></span><button data-restore="${esc(r.id)}">Restore draft</button></div>`,
        )
        .join("") || "<p>No saved revisions yet.</p>";
    $$("[data-restore]").forEach(
      (b) =>
        (b.onclick = async () => {
          b.disabled = true;
          b.setAttribute("aria-busy", "true");
          try {
            const r = await api("/restore", "POST", {
              revision_id: b.dataset.restore,
              version: state.version,
            });
            state.version = r.version;
            state.document = r.document;
            needsFullPreview = true;
            lastSaved = JSON.stringify(state.document);
            dirty = false;
            selected = null;
            snapshot("Restore revision");
            renderLeft();
            renderSettings();
            await preview();
            status("Saved");
            toast("Draft restored");
            $("#revision-dialog").close();
          } catch (e) {
            showError(e);
          } finally {
            b.disabled = false;
            b.removeAttribute("aria-busy");
          }
        }),
    );
  } catch (e) {
    showError(e);
  }
};
$("#media-upload").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const data = new FormData();
  data.append("file", f);
  data.append("alt", f.name);
  e.target.disabled = true;
  const dropzone = e.target.closest(".upload-label");
  dropzone.setAttribute("aria-busy", "true");
  dropzone.querySelector("strong").textContent = "Uploading image…";
  try {
    const r = await api("/media", "POST", data);
    mediaCallback(r.media.url);
    toast("Image uploaded");
    $("#media-dialog").close();
  } catch (e) {
    showError(e);
  }
  e.target.value = "";
  e.target.disabled = false;
  dropzone.removeAttribute("aria-busy");
  dropzone.querySelector("strong").textContent = "Add image";
};
const iconUpload = $("#icon-upload"), iconUploadZone = $("#icon-upload-label");
iconUploadZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  iconUploadZone.classList.add("drag-over");
});
iconUploadZone.addEventListener("dragleave", () => iconUploadZone.classList.remove("drag-over"));
iconUploadZone.addEventListener("drop", (e) => {
  e.preventDefault();
  iconUploadZone.classList.remove("drag-over");
  if (!e.dataTransfer.files.length || iconUpload.disabled) return;
  iconUpload.files = e.dataTransfer.files;
  iconUpload.dispatchEvent(new Event("change", { bubbles: true }));
});
iconUpload.onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const data = new FormData();
  data.append("file", file);
  data.append("title", file.name.replace(/\.[^.]+$/, ""));
  e.target.disabled = true;
  iconUploadZone.setAttribute("aria-busy", "true");
  iconUploadZone.querySelector("strong").textContent = "Uploading icon…";
  try {
    const result = await api("/icons", "POST", data);
    iconLibrary ??= { icons: [], custom: [] };
    iconLibrary.custom = [result.media, ...(iconLibrary.custom || [])];
    iconCallback?.(result.media.url);
    toast("Custom icon uploaded");
    $("#icon-dialog").close();
  } catch (error) {
    showError(error);
  } finally {
    e.target.value = "";
    e.target.disabled = false;
    iconUploadZone.removeAttribute("aria-busy");
    iconUploadZone.querySelector("strong").textContent = "Upload custom icon";
  }
};
$("#link-apply").onclick = () => {
  linkCallback($("#link-search").value);
  $("#link-dialog").close();
};
$("#conflict-reload").onclick = async () => {
  $("#conflict-dialog").close();
  await load();
};
$("#conflict-overwrite").onclick = async () => {
  state.version = conflictVersion;
  $("#conflict-dialog").close();
  await save();
};
window.addEventListener("keydown", (e) => {
  const editing = e.target.closest(
      "input,textarea,select,[contenteditable=true]",
    ),
    mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "s") {
    e.preventDefault();
    save();
    return;
  }
  // Ctrl+I opens the assistant only when this installation provides one.
  if (mod && e.key.toLowerCase() === "i" && boot.features?.assistant && $("[data-mode=assistant]")) {
    e.preventDefault();
    $("[data-mode=assistant]").click();
    return;
  }
  if (editing || $("dialog[open]")) return;
  if (mod && e.key.toLowerCase() === "z") {
    e.preventDefault();
    e.shiftKey ? redo() : undo();
  } else if (mod && e.key.toLowerCase() === "y") {
    e.preventDefault();
    redo();
  } else if (mod && e.key.toLowerCase() === "d") {
    e.preventDefault();
    action("duplicate");
  } else if (e.key === "Delete" && selected) {
    e.preventDefault();
    action("delete");
  } else if (e.key === "Escape" && !$("dialog[open]")) action("parent");
});
window.addEventListener("beforeunload", (e) => {
  if (dirty || themeDirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
window.addEventListener("blur", () => {
  if ((dirty || themeDirty) && !saving) save();
});
setInterval(() => {
  if ((dirty || themeDirty) && !saving) save();
}, 30000);
load();


/* Dependency-free merchant editor. All saved content passes server validation. */
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
  tab = "Content",
  dirty = false,
  themeDirty = false,
  saving = false,
  history = [],
  cursor = -1,
  lastSaved = "",
  timer,
  serial = 0,
  dragPayload,
  mediaCallback,
  linkCallback,
  conflictVersion;
const collapsed = new Set();
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
    `<button aria-label="Dismiss error">×</button><strong>${esc(e.message || e)}</strong>${e.errors?.length ? `<ul>${e.errors.map((x) => `<li>${esc(x.path)}: ${esc(x.message)}</li>`).join("")}</ul>` : ""}`;
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
const spec = (t) => state.schema[t] || {},
  schemaLabel = (type) => (spec(type).label || type.replaceAll("_", " ")).replace(/^./, (c) => c.toUpperCase()),
  label = (b) => schemaLabel(b.type);
const widgetIcons = {
  "tabs": '<path d="M3 8V4h6v4 M10 8V4h5v4 M16 8V4h5v4 M3 8h18v12H3Z M7 12h10 M7 16h7"/>',
  "countdown": '<circle cx="12" cy="13" r="8"/><path d="M9 2h6 M12 5V2 M12 8v5l3 2 M18 6l2-2"/>',
  "stats": '<path d="M3 20h18 M5 16V9h3v7 M11 16V4h3v12 M17 16v-5h3v5"/>',
  "progress-bars": '<path d="M3 5h18v4H3Z M3 15h18v4H3Z M7 5v4 M15 15v4"/>',
  "alert": '<path d="m12 3 10 18H2Z M12 9v5 M12 17h.01"/>',
  "table": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9h18 M3 14h18 M9 4v16 M15 4v16"/>',
  "map": '<path d="m2 6 6-3 8 3 6-3v15l-6 3-8-3-6 3Z M8 3v15 M16 6v15"/>',
  "social-links": '<circle cx="5" cy="12" r="3"/><circle cx="18" cy="5" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8 11 7-5 M8 13l7 5"/>',
  "columns": "<rect x=\"3\" y=\"4\" width=\"7\" height=\"16\" rx=\"1\"/><rect x=\"14\" y=\"4\" width=\"7\" height=\"16\" rx=\"1\"/>",
  "heading": "<path d=\"M5 4v16 M19 4v16 M5 12h14\"/>",
  "paragraph": "<path d=\"M4 5h16 M4 10h16 M4 15h16 M4 20h10\"/>",
  "image": "<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"2\"/><circle cx=\"8\" cy=\"8\" r=\"2\"/><path d=\"m3 18 6-6 4 4 3-3 5 5\"/>",
  "button": "<rect x=\"2\" y=\"6\" width=\"20\" height=\"12\" rx=\"4\"/><path d=\"M7 12h10 m-3-3 3 3-3 3\"/>",
  "spacer": "<path d=\"M3 3h18 M3 21h18 M12 6v12 m-3-9 3-3 3 3 m-6 6 3 3 3-3\"/>",
  "divider": "<path d=\"M3 12h18 M6 4v2 M12 4v2 M18 4v2 M6 18v2 M12 18v2 M18 18v2\"/>",
  "video": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"16\" rx=\"2\"/><path d=\"m10 8 6 4-6 4Z\"/>",
  "checklist": "<path d=\"m3 6 2 2 3-4 M11 6h10 m-18 8 2 2 3-4 M11 14h10 M11 20h10\"/>",
  "gallery": "<rect x=\"7\" y=\"3\" width=\"14\" height=\"14\" rx=\"2\"/><path d=\"m7 14 5-5 3 3 2-2 4 4 M3 7v14h14\"/>",
  "quote": "<path d=\"M4 6h6v7H6c0 3 1 4 3 5 M14 6h6v7h-4c0 3 1 4 3 5\"/>",
  "tag": "<path d=\"M3 3h9l9 9-9 9-9-9Z\"/><circle cx=\"8\" cy=\"8\" r=\"1.5\"/>",
  "products": "<rect x=\"3\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"3\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/>",
  "collections": "<path d=\"M3 7h7l2 2h9v11H3Z M3 7V4h7l2 2h9v3 M7 13h10 M7 16h6\"/>",
  "mail": "<rect x=\"3\" y=\"5\" width=\"18\" height=\"14\" rx=\"2\"/><path d=\"m3 6 9 7 9-7\"/>",
  "accordion": "<path d=\"M3 4h18v6H3Z M3 14h18v6H3Z m12-7 2 2 2-2 m-4 10 2 2 2-2\"/>",
  "code": "<path d=\"m7 6-5 6 5 6 m10-12 5 6-5 6 M14 3l-4 18\"/>",
  "slideshow": "<rect x=\"5\" y=\"4\" width=\"14\" height=\"14\" rx=\"2\"/><path d=\"M2 7v8 M22 7v8 m-12-8 5 4-5 4\"/><circle cx=\"9\" cy=\"21\" r=\".5\"/><circle cx=\"15\" cy=\"21\" r=\".5\"/>",
  "banner": "<rect x=\"2\" y=\"4\" width=\"20\" height=\"16\" rx=\"1\"/><path d=\"M7 9h10 M9 12h6 M10 16h4\"/>",
  "compare": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"16\" rx=\"2\"/><path d=\"M12 2v20 m-5-13-3 3 3 3 m10-6 3 3-3 3\"/>",
  "logos": "<circle cx=\"6\" cy=\"6\" r=\"3\"/><path d=\"m17 3 4 6h-8Z\"/><rect x=\"3\" y=\"14\" width=\"6\" height=\"6\"/><path d=\"m17 13 4 4-4 4-4-4Z\"/>",
  "features": "<path d=\"m6 3 1 3 3 1-3 1-1 3-1-3-3-1 3-1Z M13 5h8 M13 9h5 M3 15h7 M3 19h7 M14 14h7v7h-7Z\"/>",
  "hotspots": "<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"2\"/><circle cx=\"9\" cy=\"9\" r=\"3\"/><path d=\"M9 7v4 M7 9h4\"/><circle cx=\"17\" cy=\"16\" r=\"2\"/>",
  "section-title": "<path d=\"M8 3h8 M4 7h16 M12 7v8 M8 15h8 M5 20h14\"/>",
  "embed-video": "<path d=\"m3 7-2 5 2 5 m18-10 2 5-2 5\"/><rect x=\"6\" y=\"5\" width=\"12\" height=\"14\" rx=\"2\"/><path d=\"m10 9 5 3-5 3Z\"/>",
  "hero": "<rect x=\"2\" y=\"3\" width=\"20\" height=\"18\" rx=\"1\"/><path d=\"m2 16 5-5 5 5 4-4 6 6 M6 7h12 M8 9h8\"/>",
  "text": "<path d=\"M3 4h11 M3 8h11 M3 12h18 M3 16h18 M3 20h14 M18 4h3v4h-3Z\"/>",
  "split": "<rect x=\"2\" y=\"4\" width=\"9\" height=\"16\" rx=\"1\"/><path d=\"m2 17 4-5 5 6 M15 6h7 M15 11h7 M15 16h5\"/>",
  "catalogue": "<path d=\"M4 3h16v18H4Z M7 7h4v4H7Z M14 7h3 M14 10h3 M7 15h10 M7 18h7\"/>",
  "chart": "<path d=\"M3 3v18h18 M7 17v-5h3v5 M13 17V8h3v9 M19 17V4h3v13\"/>",
  "shield": "<path d=\"m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Z m-5 10 3 3 7-7\"/>",
  "reviews": "<path d=\"M3 3h18v13H9l-6 5Z m9-10 1-3 1 3h3l-2 2 1 3-3-2-3 2 1-3-2-2Z\"/>",
  "articles": "<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"1\"/><path d=\"M6 6h5v5H6Z M14 6h4 M14 10h4 M6 15h12 M6 18h12\"/>",
  "library": "<path d=\"M3 4h4v17H3Z M10 4h4v17h-4Z m6 1 4-1 3 16-4 1Z\"/>",
  "question": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M9 8a3 3 0 0 1 6 0c0 3-3 2-3 5 M12 17h.01\"/>",
  "people": "<circle cx=\"9\" cy=\"7\" r=\"3\"/><path d=\"M3 21v-3a6 6 0 0 1 12 0v3 M17 4a3 3 0 0 1 0 6 M18 14a5 5 0 0 1 3 5v2\"/>",
  "contact": "<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"2\"/><path d=\"M7 7h10 M7 11h10 M7 15h5 m3 2 2 2 3-4\"/>",
  "product-detail": "<path d=\"m3 7 5-3 5 3v6l-5 3-5-3Z M3 7l5 3 5-3 M8 10v6 M17 5h4 M17 9h4 M17 13h4 M3 20h18\"/>",
  "references": "<path d=\"M5 2h10l4 4v16H5Z M15 2v5h4 M8 11h8 M8 15h8 M8 19h5\"/>",
  "embed": "<rect x=\"2\" y=\"4\" width=\"20\" height=\"16\" rx=\"2\"/><path d=\"M2 8h20 m-13 3-3 3 3 3 m6-6 3 3-3 3\"/>"
};
function blockIcon(type) {
  const path = widgetIcons[spec(type).icon] || widgetIcons.columns;
  return `<svg class="block-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
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
function text(v) {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v[state?.default_locale || "en"] ?? "")
    : (v ?? "");
}
function setField(o, p, v) {
  const old = get(o, p);
  if (old && typeof old === "object" && !Array.isArray(old)) {
    const locale = state.default_locale || "en";
    v = { ...old, [locale]: v };
  }
  set(o, p, v);
}
function historyButtons() {
  $("#undo").disabled = cursor <= 0;
  $("#redo").disabled = cursor >= history.length - 1;
}
function snapshot(name) {
  history = history.slice(0, cursor + 1);
  history.push({ name, document: copy(state.document) });
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
      send({ type: "context", block_id: selected, scroll_y: y });
    };
    frame.srcdoc = result.html;
    $("#node-count").textContent = `${walk().length} blocks`;
  } catch (e) {
    if (request === serial) showError(e);
  }
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
  const children = b.children || [];
  const subtitle = String(text(b.heading || b.title) || "").replace(/<[^>]*>/g, "").trim();
  return `<div class="tree-node"><div class="tree-row ${selected === b.id ? "selected" : ""} ${b.hidden ? "is-hidden" : ""}" data-block-id="${esc(b.id)}" draggable="true" tabindex="0" role="treeitem" aria-selected="${selected === b.id}" ${children.length ? `aria-expanded="${!collapsed.has(b.id)}"` : ""} aria-label="${esc(label(b))}${subtitle ? `: ${esc(subtitle)}` : ""}" style="--depth:${depth}"><span class="drag-handle" aria-hidden="true">⠿</span><button class="tree-toggle" ${children.length ? "" : "disabled tabindex=\"-1\""} data-collapse="${esc(b.id)}" aria-label="Expand or collapse ${esc(label(b))}">${children.length ? (collapsed.has(b.id) ? "▸" : "▾") : "·"}</button>${blockIcon(b.type)}<span class="tree-title"><span>${esc(label(b))}</span>${subtitle ? `<small>${esc(subtitle)}</small>` : ""}</span><div class="tree-actions"><button data-action="hide" aria-label="${b.hidden ? "Show" : "Hide"} ${esc(label(b))}" aria-pressed="${Boolean(b.hidden)}" title="${b.hidden ? "Show" : "Hide"} block">◉</button><button data-action="duplicate" aria-label="Duplicate block" title="Duplicate block">⧉</button><button data-action="delete" aria-label="Delete block" title="Delete block">×</button></div></div>${collapsed.has(b.id) ? "" : children.map((c) => row(c, depth + 1)).join("")}${spec(b.type).accepts_children && !collapsed.has(b.id) ? `<button class="add-section" data-add-child="${esc(b.id)}">+ Add block</button>` : ""}</div>`;
}
function renderTree() {
  if (mode !== "sections") return;
  $("#left-content").innerHTML =
    `<div class="tree-group-label">Header</div><div class="tree-static">▤ &nbsp; Store header <span class="subtle">· Theme</span></div><div class="tree-group-label">Template</div><div role="tree" aria-label="Page sections">${state.document.blocks.map((b) => row(b)).join("")}</div><button class="add-section" id="add-section">+ Add section</button><div class="tree-group-label">Footer</div><div class="tree-static">▤ &nbsp; Store footer <span class="subtle">· Theme</span></div>`;
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
        $$("[data-collapse]").find((node) => node.dataset.collapse === id)?.focus();
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
  return [...groups]
    .map(
      ([category, items]) =>
        `<div class="widget-category">${esc(category)}</div><div class="widget-grid">${items.map(([type, s]) => `<button class="widget-tile" draggable="true" data-widget-type="${esc(type)}" title="${esc(s.description)}"><span class="tile-preview" aria-hidden="true">${blockIcon(type)}</span><span>${esc(schemaLabel(type))}<small>${esc(s.description || "")}</small></span></button>`).join("")}</div>`,
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
  $("#left-title").textContent = names[mode];
  $$("[data-mode]").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === mode),
  );
  if (mode === "sections") renderTree();
  if (mode === "widgets") {
    $("#left-content").innerHTML =
      `<div class="widget-search"><input id="widget-search" type="search" placeholder="Search widgets" aria-label="Search widgets"></div><div id="widget-results">${widgetHTML()}</div>`;
    $("#widget-search").oninput = (e) => {
      $("#widget-results").innerHTML = widgetHTML(e.target.value);
      wireWidgets();
    };
    wireWidgets();
  }
  if (mode === "theme") renderTheme();
  if (mode === "history") {
    $("#left-content").innerHTML =
      `<p class="field-help" style="padding:12px 16px">Changes in this session. Saved revisions include earlier drafts.</p>${history
        .map(
          (h, i) =>
            `<div class="history-item ${i === cursor ? "current" : ""}"><span>${esc(h.name)}</span><button data-history="${i}" ${i === cursor ? "disabled" : ""}>Restore</button></div>`,
        )
        .reverse()
        .join("")}`;
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
        return `${header}<button class="picker-item" data-insert="${esc(type)}"><span class="picker-thumbnail" aria-hidden="true">${blockIcon(type)}</span><span>${esc(schemaLabel(type))}<small>${esc(s.description)}</small></span></button>`;
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
  else if (["textarea", "richtext"].includes(f.type))
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
          (item, i) =>
            `<details class="repeater-item" draggable="true" data-repeater="${esc(path)}" data-index="${i}"><summary>${esc(text(item.heading || item.label) || `Item ${i + 1}`)}</summary>${f.value_list ? `<div class="field-inline"><input data-setting="${esc(path)}.${i}" data-type="${esc(f.fields[0].type)}" value="${esc(item)}" aria-label="${esc(f.fields[0].label)} ID"><button data-link="${esc(path)}.${i}" data-resource-type="${esc(f.fields[0].type)}" aria-label="Choose ${esc(f.fields[0].label)}">↗</button></div>` : (f.fields || f.item_fields || []).map((sub) => fieldHTML(sub, item, `${path}.${i}.`)).join("")}<div class="repeater-actions"><button data-repeat-action="up" data-path="${esc(path)}" data-index="${i}" aria-label="Move item up" ${i === 0 ? "disabled" : ""}>↑</button><button data-repeat-action="down" data-path="${esc(path)}" data-index="${i}" aria-label="Move item down" ${i === rows.length - 1 ? "disabled" : ""}>↓</button>${f.fixed ? "" : `<button data-repeat-action="remove" data-path="${esc(path)}" data-index="${i}" ${rows.length <= (f.min ?? 0) ? "disabled" : ""}>Remove</button>`}</div></details>`,
        )
        .join("") +
      (f.fixed
        ? ""
        : `<button data-repeat-add="${esc(path)}" ${rows.length >= (f.max ?? 50) ? "disabled" : ""}>+ Add item</button>`);
  } else if (["image", "video"].includes(f.type))
    html = `${f.type === "image" && v && !v.startsWith("metafield:") ? `<img class="field-media-preview" src="${esc(v)}" alt="Selected image">` : ""}<input ${attrs} value="${esc(v)}" placeholder="Media URL"><button data-media="${esc(path)}">Choose media</button>`;
  else
    html = `<div class="field-inline"><input ${attrs} type="${f.type === "number" ? "number" : f.type === "datetime" ? "datetime-local" : "text"}" value="${esc(Array.isArray(v) ? v.join(", ") : v)}" ${f.min !== undefined ? `min="${f.min}"` : ""} ${f.max !== undefined ? `max="${f.max}"` : ""}>${["url", "link", "product", "collection"].includes(f.type) ? `<button data-link="${esc(path)}" aria-label="Choose ${esc(f.label)}">↗</button>` : ""}</div>`;
  const dynamicKind = f.dynamic_source || (!f.id.startsWith("style.") && ["text", "textarea", "richtext"].includes(f.type) ? "text" : f.type === "image" ? "image" : "");
  const sources = (state?.page?.kind === "product" || state?.page?.product_id) && dynamicKind ? (state.dynamic_sources?.[dynamicKind] || []) : [];
  if (sources.length) html += `<label class="field-help">Connect dynamic source<select data-dynamic-for="${esc(id)}"><option value="">Choose a custom data definition</option>${sources.map(source => `<option value="${esc(source.source)}" ${v === source.source ? "selected" : ""}>${esc(source.name)}</option>`).join("")}</select></label>`;
  return `<div class="field"><label for="${esc(id)}">${esc(f.label || f.id)}</label>${html}${f.help ? `<span class="field-help">${esc(f.help)}</span>` : ""}</div>`;
}
function selectedFields() {
  const n = locate(selected);
  if (!n) return [];
  const s = spec(n.block.type),
    ids = s.groups?.[tab] || s.groups?.[tab.toLowerCase()];
  return (s.fields || []).filter((f) =>
    ids
      ? ids.includes(f.id)
      : tab === "Content"
        ? !f.id.startsWith("style.")
        : tab === "Advanced"
          ? ["style.anchor_id", "style.css_class"].includes(f.id)
          : f.id.startsWith("style."),
  );
}
function renderSettings() {
  const n = locate(selected);
  $("#editor").classList.toggle("has-selection", Boolean(n));
  if (!n) {
    $("#selection-header").innerHTML =
      '<h2>Page settings</h2><span class="subtle">Build your storefront</span>';
    $("#settings-content").innerHTML =
      `<div class="settings-empty"><div class="empty-icon">${blockIcon("container")}</div><strong>Make this page your own</strong><p>Select a section in the preview or drag a widget onto the page.</p><button id="empty-add">Add section</button></div>`;
    $("#empty-add").onclick = () => openPicker();
    return;
  }
  $("#selection-header").innerHTML =
    `<h2>${esc(label(n.block))}</h2><span class="subtle">${esc(spec(n.block.type).category)}</span><div class="selection-actions">${[
      ["up", "↑"],
      ["down", "↓"],
      ["duplicate", "Duplicate"],
      ["hide", n.block.hidden ? "Show" : "Hide"],
      ["delete", "Delete"],
      ["parent", "Parent"],
    ]
      .map(
        ([a, l]) =>
          `<button data-selected-action="${a}" aria-label="${a === "up" ? "Move block up" : a === "down" ? "Move block down" : l}">${l}</button>`,
      )
      .join("")}</div>`;
  $$("[data-selected-action]").forEach(
    (b) => (b.onclick = () => action(b.dataset.selectedAction)),
  );
  const containers = walk().filter(
    (x) =>
      spec(x.block.type).accepts_children &&
      !walk([n.block]).some((c) => c.block.id === x.block.id),
  );
  $("#settings-content").innerHTML =
    `<div class="settings-fields">${selectedFields()
      .map((f) => fieldHTML(f, n.block))
      .join(
        "",
      )}${tab === "Advanced" ? `<div class="field"><label for="move-into">Move into container</label><select id="move-into"><option value="">Choose container</option><option value="root">Page (top level)</option>${containers.map((x) => `<option value="${esc(x.block.id)}">${esc(label(x.block))} · ${esc(x.block.id.slice(-6))}</option>`).join("")}</select></div>` : ""}</div>`;
  wireFields(n.block, (path, v) => {
    setField(n.block, path, v);
    changed(`Change ${path}`);
  });
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
            const item = { ...get(obj, itemPath), ...copy(defaults), theme: v };
            update(itemPath, item);
            renderSettings();
            return;
          }
        }
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
        input.value = el.value;
        input.dispatchEvent(new Event("input"));
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
          rows.splice(i, 1);
        } else {
          const j = i + (a === "up" ? -1 : 1);
          if (j < 0 || j >= rows.length) return;
          [rows[i], rows[j]] = [rows[j], rows[i]];
        }
        update(path, rows);
        renderSettings();
      }),
  );
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
function renderTheme() {
  const fields = Object.entries(state.theme_settings).map(([id, v]) => ({
    id,
    label: id.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()),
    type: state.theme_choices?.[id]
      ? "select"
      : typeof v === "number"
        ? "number"
        : typeof v === "string" && v.startsWith("#")
          ? "colour"
          : "text",
    options: state.theme_choices?.[id],
    default: v,
  }));
  $("#left-content").innerHTML =
    `<div class="theme-fields"><p class="field-help">Shared across your storefront.</p>${fields.map((f) => fieldHTML(f, state.theme_settings)).join("")}<button id="save-theme">Save theme settings</button></div>`;
  wireFields(
    state.theme_settings,
    (p, v) => {
      set(state.theme_settings, p, v);
      themeDirty = true;
      status("Unsaved changes");
    },
    "#left-content",
  );
  $("#save-theme").onclick = async () => {
    try {
      await saveTheme();
      status(dirty || themeDirty ? "Unsaved changes" : "Saved");
      preview();
    } catch (e) {
      showError(e);
    }
  };
}
async function saveTheme() {
  if (!themeDirty) return;
  const submitted = JSON.stringify(state.theme_settings);
  try {
    const r = await api("/theme-settings", "PUT", {
      theme_settings: state.theme_settings,
      version: state.site_version,
    });
    state.site_version = r.version;
    if (boot.theme_id) state.version = r.version;
    themeDirty = JSON.stringify(state.theme_settings) !== submitted;
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
        `<h3>${esc(kind.replaceAll("_", " "))}</h3>${pages.map((p) => `<a href="/admin/sites/${encodeURIComponent(boot.site_id)}/editor?page=${encodeURIComponent(p.id)}&theme=${encodeURIComponent(boot.theme_id || '')}">${esc(p.title)}</a>`).join("")}`,
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
    selected = null;
    dirty = false;
    themeDirty = false;
    lastSaved = JSON.stringify(state.document);
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
    status("Saved");
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
window.addEventListener("message", (e) => {
  if (e.source !== frame.contentWindow || e.origin !== location.origin) return;
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
  if (d.type === "ready") send({ type: "context", block_id: selected });
  if (d.type === "select") select(d.block_id);
  if (d.type === "inline") {
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
$$("[data-tab]").forEach(
  (b) =>
    (b.onclick = () => {
      tab = b.dataset.tab;
      $$("[data-tab]").forEach((t) =>
        t.setAttribute("aria-selected", String(t === b)),
      );
      if (state) renderSettings();
    }),
);
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

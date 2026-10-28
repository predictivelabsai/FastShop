/* The existing settings inputs remain the only path to saved coordinates. */
const selections = new Map();
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);

function markersHTML(block, selected) {
  return block.items.map((item, index) => `<button type="button" data-hotspot-index="${index}" aria-label="Select hotspot ${index + 1}" aria-pressed="${index === selected}" style="left:${Math.max(0, Math.min(100, Number(item.x)))}%;top:${Math.max(0, Math.min(100, Number(item.y)))}%">${index + 1}</button>`).join("");
}

export function hotspotRowsChanged(block, from, to) {
  if (block.type !== "image_hotspots") return;
  let selected = selections.get(block.id) ?? 0;
  if (to === null) {
    if (selected > from) selected--;
    else if (selected === from) selected = Math.min(from, block.items.length - 2);
  } else if (selected === from) selected = to;
  else if (from < selected && to >= selected) selected--;
  else if (from > selected && to <= selected) selected++;
  selections.set(block.id, selected);
}

export function hotspotPreview(block) {
  if (block.type !== "image_hotspots") return "";
  const selected = block.items.length ? Math.max(0, Math.min(selections.get(block.id) ?? 0, block.items.length - 1)) : -1;
  selections.set(block.id, selected);
  return `<div class="field hotspot-editor" data-hotspot-editor>
    <strong>Place hotspots</strong>
    <p class="field-help" id="hotspot-instructions">Click the image to place the selected hotspot. You can also type the position below. Select a numbered marker or an entry to choose a hotspot.</p>
    <div class="hotspot-stage" data-hotspot-stage role="group" aria-label="Hotspot placement preview" aria-describedby="hotspot-instructions" hidden>${block.image && !block.image.startsWith("metafield:") ? `<img src="${escapeHTML(block.image)}" alt="${escapeHTML(block.alt)}">${markersHTML(block, selected)}` : ""}</div>
    <p class="field-help" data-hotspot-empty>Add an image to place hotspots.</p>
    <span class="field-help" data-hotspot-status role="status" aria-live="polite"></span>
  </div>`;
}

export function wireHotspotPreview(host, block) {
  const preview = host.querySelector("[data-hotspot-editor]");
  if (!preview) return;
  const stage = preview.querySelector("[data-hotspot-stage]"),
    empty = preview.querySelector("[data-hotspot-empty]"),
    status = preview.querySelector("[data-hotspot-status]");
  const input = (index, axis) => host.querySelector(`[data-setting="items.${index}.${axis}"]`);
  const select = (index, open = false) => {
    selections.set(block.id, index);
    stage.querySelectorAll("[data-hotspot-index]").forEach((marker) => {
      marker.setAttribute("aria-pressed", String(Number(marker.dataset.hotspotIndex) === index));
    });
    status.textContent = index < 0 ? "Click the image to add your first hotspot." : `Hotspot ${index + 1} selected.`;
    if (open) host.querySelector(`[data-repeater="items"][data-index="${index}"]`).open = true;
  };
  const syncMarkers = () => {
    stage.querySelectorAll("[data-hotspot-index]").forEach((marker) => {
      const index = Number(marker.dataset.hotspotIndex);
      for (const axis of ["x", "y"]) {
        const control = input(index, axis);
        const value = Math.max(Number(control.min), Math.min(Number(control.max), Number(control.value)));
        marker.style[axis === "x" ? "left" : "top"] = `${value}%`;
      }
    });
  };
  const renderImage = () => {
    const source = host.querySelector('[data-setting="image"]').value.trim();
    stage.replaceChildren();
    stage.hidden = true;
    empty.hidden = false;
    empty.textContent = source.startsWith("metafield:")
      ? "Choose an image URL to place hotspots here. You can also type the position below."
      : "Add an image to place hotspots.";
    if (!source || source.startsWith("metafield:")) return;
    const image = document.createElement("img");
    image.alt = block.alt || "Hotspot image";
    image.draggable = false;
    image.onload = () => {
      if (image.parentElement !== stage) return;
      stage.hidden = false;
      empty.hidden = true;
    };
    image.onerror = () => {
      if (image.parentElement !== stage) return;
      stage.hidden = true;
      empty.hidden = false;
      empty.textContent = "The image could not be loaded. Choose another image or type the position below.";
    };
    image.src = source;
    stage.append(image);
    stage.insertAdjacentHTML("beforeend", markersHTML(block, selections.get(block.id)));
    stage.querySelectorAll("[data-hotspot-index]").forEach((marker) => {
      const index = Number(marker.dataset.hotspotIndex);
      marker.onclick = (event) => { event.stopPropagation(); select(index, true); };
    });
    syncMarkers();
    select(selections.get(block.id));
  };
  host.oninput = (event) => {
    const path = event.target.dataset.setting;
    if (path === "image") renderImage();
    if (path === "alt" && stage.querySelector("img")) stage.querySelector("img").alt = event.target.value;
    const match = path?.match(/^items\.(\d+)\.[xy]$/);
    if (match) { select(Number(match[1])); syncMarkers(); }
  };
  host.querySelectorAll('[data-repeater="items"]').forEach((row) => {
    row.addEventListener("focusin", () => select(Number(row.dataset.index)));
    row.querySelector("summary").addEventListener("click", () => select(Number(row.dataset.index)));
  });
  stage.onclick = (event) => {
    if (event.target !== stage.querySelector("img")) return;
    const bounds = event.target.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    let index = selections.get(block.id);
    if (index < 0) {
      host.querySelector('[data-repeat-add="items"]').click();
      // Adding a row rebuilds the panel through the normal repeater handler.
      host = document.querySelector("#settings-content");
      index = 0;
    }
    for (const [axis, position, size] of [["x", event.clientX - bounds.left, bounds.width], ["y", event.clientY - bounds.top, bounds.height]]) {
      const control = input(index, axis), min = Number(control.min), max = Number(control.max), step = Number(control.step) || 1;
      const value = Math.min(max, Math.max(min, min + Math.round((position / size * 100 - min) / step) * step));
      control.value = value;
      control.dispatchEvent(new Event("input", {bubbles: true}));
    }
    host.querySelector(`[data-repeater="items"][data-index="${index}"]`).open = true;
  };
  renderImage();
}

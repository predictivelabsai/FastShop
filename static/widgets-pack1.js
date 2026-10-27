/* Progressive enhancement. WeakSet + observer also cover editor iframe block replacements. */
(() => {
  "use strict";
  const ready = new WeakSet();
  const disposers = new WeakMap();
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  function slideshow(root) {
    const slides = [...root.querySelectorAll(".fs-pack-slide")];
    if (!slides.length) return;
    const controls = root.querySelector(".fs-pack-slide-controls");
    const dots = [...root.querySelectorAll("[data-slide-to]")];
    const pause = root.querySelector("[data-slide-pause]");
    root.dataset.enhanced = "true";
    const controller = new AbortController();
    const on = (target, event, handler) => target?.addEventListener(event, handler, { signal: controller.signal });
    let index = 0, timer = null, paused = false, hovered = false, focused = false;
    function show(next, announce = false) {
      index = (next + slides.length) % slides.length;
      slides.forEach((slide, i) => { slide.hidden = i !== index; });
      dots.forEach((dot, i) => dot.setAttribute("aria-current", String(i === index)));
      if (announce) root.querySelector("[data-slide-status]").textContent = `Slide ${index + 1} of ${slides.length}`;
    }
    function schedule() {
      clearInterval(timer);
      timer = null;
      if (pause) {
        pause.hidden = reduced.matches;
        pause.textContent = paused ? "Play slideshow" : "Pause slideshow";
      }
      if (root.dataset.autoplay === "true" && !reduced.matches && !paused && !hovered && !focused && slides.length > 1 && !document.hidden && root.isConnected) {
        timer = setInterval(() => {
          if (!root.isConnected) { clearInterval(timer); return; }
          show(index + 1);
        }, Math.max(3, Math.min(15, Number(root.dataset.interval) || 5)) * 1000);
      }
    }
    on(root.querySelector("[data-slide-prev]"), "click", () => { show(index - 1, true); schedule(); });
    on(root.querySelector("[data-slide-next]"), "click", () => { show(index + 1, true); schedule(); });
    dots.forEach((dot, i) => on(dot, "click", () => { show(i, true); schedule(); }));
    on(pause, "click", () => {
      paused = !paused;
      pause.textContent = paused ? "Play slideshow" : "Pause slideshow";
      pause.setAttribute("aria-pressed", String(paused));
      schedule();
    });
    on(root, "mouseenter", () => { hovered = true; schedule(); });
    on(root, "mouseleave", () => { hovered = false; schedule(); });
    on(root, "focusin", () => { focused = true; schedule(); });
    on(root, "focusout", () => { focused = false; schedule(); });
    reduced.addEventListener("change", schedule, { signal: controller.signal });
    document.addEventListener("visibilitychange", schedule, { signal: controller.signal });
    disposers.set(root, () => { controller.abort(); clearInterval(timer); });
    controls.hidden = slides.length < 2;
    show(0);
    schedule();
  }
  function comparison(root) {
    const range = root.querySelector("[data-compare-range]");
    const stage = root.querySelector(".fs-pack-compare-stage");
    if (!range || !stage) return;
    root.dataset.enhanced = "true";
    range.closest("label").hidden = false;
    const vertical = root.dataset.orientation === "vertical";
    range.setAttribute("aria-orientation", vertical ? "vertical" : "horizontal");
    function update(value) {
      range.value = String(Math.max(10, Math.min(90, value)));
      root.style.setProperty("--fs-split", `${range.value}%`);
      range.setAttribute("aria-valuetext", `${range.value}% before image`);
    }
    range.addEventListener("input", () => update(Number(range.value)));
    range.addEventListener("keydown", (event) => {
      if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) {
        event.preventDefault();
        update(Number(range.value) + (["ArrowUp", "ArrowRight"].includes(event.key) ? 1 : -1));
      }
    });
    function move(event) {
      const rect = stage.getBoundingClientRect();
      update(vertical ? (event.clientY - rect.top) / rect.height * 100 : (event.clientX - rect.left) / rect.width * 100);
    }
    stage.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      stage.setPointerCapture(event.pointerId);
      move(event);
      range.focus({ preventScroll: true });
    });
    stage.addEventListener("pointermove", (event) => { if (stage.hasPointerCapture(event.pointerId)) move(event); });
    stage.addEventListener("pointerup", (event) => { if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId); });
    stage.addEventListener("dragstart", (event) => event.preventDefault());
    update(Number(range.value));
  }
  function hotspots(root) {
    const markers = [...root.querySelectorAll("[data-hotspot-target]")];
    const entries = [...root.querySelectorAll(".fs-pack-hotspot-detail")];
    let active = null;
    function close(restore = false) {
      entries.forEach((entry) => {
        if (entry.hasAttribute("popover") && entry.matches(":popover-open")) entry.hidePopover();
        entry.hidden = true;
      });
      markers.forEach((marker) => marker.setAttribute("aria-expanded", "false"));
      if (restore) active?.focus();
      active = null;
    }
    entries.forEach((entry) => {
      entry.hidden = true;
      entry.setAttribute("role", "region");
      entry.setAttribute("aria-label", entry.querySelector("h3")?.textContent || "Image detail");
      if (typeof entry.showPopover === "function") entry.setAttribute("popover", "manual");
      const button = entry.querySelector("[data-hotspot-close]");
      button.hidden = false;
      button.addEventListener("click", () => close(true));
    });
    markers.forEach((marker) => {
      marker.hidden = false;
      marker.addEventListener("click", () => {
        const wasActive = active === marker;
        close();
        if (wasActive) return;
        const entry = entries.find((item) => item.id === marker.dataset.hotspotTarget);
        if (!entry) return;
        active = marker;
        marker.setAttribute("aria-expanded", "true");
        entry.hidden = false;
        if (entry.hasAttribute("popover")) entry.showPopover();
        entry.focus({ preventScroll: true });
      });
    });
    root.addEventListener("keydown", (event) => { if (event.key === "Escape" && active) { event.preventDefault(); close(true); } });
  }
  function initialise() {
    document.querySelectorAll("[data-slideshow],[data-before-after],[data-hotspots]").forEach((root) => {
      if (ready.has(root)) return;
      ready.add(root);
      if (root.hasAttribute("data-slideshow")) slideshow(root);
      if (root.hasAttribute("data-before-after")) comparison(root);
      if (root.hasAttribute("data-hotspots")) hotspots(root);
    });
  }
  initialise();
  new MutationObserver((records) => {
    for (const record of records) for (const node of record.removedNodes) {
      if (node.nodeType !== 1 || node.isConnected) continue;
      const removed = [node, ...node.querySelectorAll("[data-slideshow],[data-before-after],[data-hotspots]")];
      removed.forEach((root) => {
        const dispose = disposers.get(root);
        if (dispose) {
          dispose();
          disposers.delete(root);
          ready.delete(root);
        }
      });
    }
    if (records.some((record) => [...record.addedNodes].some((node) => node.nodeType === 1))) initialise();
  }).observe(document.documentElement, { childList: true, subtree: true });
})();

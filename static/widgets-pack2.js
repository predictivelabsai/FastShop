/* Progressive enhancement also supports replaced editor canvas blocks. */
(() => {
  "use strict";
  const ready = new WeakSet(), disposers = new WeakMap();
  const selector = "[data-tabs],[data-countdown],[data-alert],[data-map],[data-stats]";
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  function tabs(root, on) {
    const list = root.querySelector("[data-tab-list]");
    const buttons = [...root.querySelectorAll("[data-tab]")];
    const panels = [...root.querySelectorAll("[data-tab-panel]")];
    if (!list || !buttons.length) return;
    list.hidden = false;
    list.setAttribute("role", "tablist");
    list.setAttribute("aria-label", "Content tabs");
    root.dataset.enhanced = "true";
    const mobile = window.matchMedia("(max-width: 640px)");
    const vertical = () => root.dataset.layout === "vertical" && !mobile.matches;
    function orientation() { list.setAttribute("aria-orientation", vertical() ? "vertical" : "horizontal"); }
    orientation();
    on(mobile, "change", orientation);
    function select(index, focus = false) {
      buttons.forEach((button, i) => {
        button.setAttribute("role", "tab");
        button.setAttribute("aria-controls", panels[i].id);
        button.setAttribute("aria-selected", String(i === index));
        button.tabIndex = i === index ? 0 : -1;
        panels[i].hidden = i !== index;
        panels[i].setAttribute("role", "tabpanel");
        panels[i].setAttribute("aria-labelledby", button.id);
        panels[i].tabIndex = 0;
      });
      if (focus) buttons[index].focus();
    }
    buttons.forEach((button, i) => {
      on(button, "click", () => select(i));
      on(button, "keydown", (event) => {
        let next;
        if (event.key === "Home") next = 0;
        else if (event.key === "End") next = buttons.length - 1;
        else if (event.key === (vertical() ? "ArrowDown" : "ArrowRight")) next = (i + 1) % buttons.length;
        else if (event.key === (vertical() ? "ArrowUp" : "ArrowLeft")) next = (i - 1 + buttons.length) % buttons.length;
        if (next !== undefined) { event.preventDefault(); select(next, true); }
      });
    });
    select(0);
  }
  function countdown(root, on) {
    const end = Date.parse(root.dataset.endAt);
    if (!Number.isFinite(end)) return () => {};
    let timer = null;
    const digits = root.querySelector("[data-countdown-digits]");
    const summary = root.querySelector("[data-countdown-summary]");
    const expired = root.querySelector("[data-countdown-expired]");
    function tick() {
      const remaining = Math.max(0, Math.ceil((end - Date.now()) / 1000));
      if (!remaining) {
        clearInterval(timer); timer = null;
        if (digits) digits.hidden = true;
        if (summary) summary.hidden = true;
        if (expired) expired.hidden = false;
        root.hidden = root.dataset.expiredBehaviour === "hide";
        return;
      }
      const units = { days: Math.floor(remaining / 86400), hours: Math.floor(remaining / 3600) % 24, minutes: Math.floor(remaining / 60) % 60, seconds: remaining % 60 };
      let remainder = remaining;
      const divisors = { days: 86400, hours: 3600, minutes: 60, seconds: 1 };
      root.querySelectorAll("[data-countdown-unit]").forEach((digit) => {
        const divisor = divisors[digit.dataset.countdownUnit];
        digit.textContent = String(Math.floor(remainder / divisor)).padStart(2, "0");
        remainder %= divisor;
      });
      if (summary) summary.textContent = `Ends ${root.dataset.endAt}. ${Object.entries(units).map(([unit, value]) => `${value} ${unit}`).join(", ")} remaining.`;
    }
    function schedule() {
      clearInterval(timer); timer = null;
      if (document.hidden || !root.isConnected) return;
      tick();
      if (Date.now() < end) timer = setInterval(tick, 1000);
    }
    on(document, "visibilitychange", schedule);
    schedule();
    return () => clearInterval(timer);
  }
  function alert(root, on) {
    const button = root.querySelector("[data-alert-dismiss]");
    if (!button) return;
    button.hidden = false;
    const preview = root.dataset.preview === "true";
    try { if (!preview && localStorage.getItem(root.dataset.storageKey) === "dismissed") root.hidden = true; } catch { /* Storage is optional. */ }
    on(button, "click", () => {
      root.hidden = true;
      try { if (!preview) localStorage.setItem(root.dataset.storageKey, "dismissed"); } catch { /* Keep dismissal usable when storage is unavailable. */ }
    });
  }
  function map(root, on) {
    const button = root.querySelector("[data-map-load]");
    if (!button || !root.dataset.embedUrl) return;
    button.hidden = false;
    on(button, "click", () => {
      if (root.querySelector("iframe")) return;
      const frame = document.createElement("iframe");
      frame.title = root.dataset.mapTitle || "Map";
      frame.loading = "lazy";
      frame.referrerPolicy = "no-referrer";
      frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-popups");
      frame.src = root.dataset.embedUrl;
      root.querySelector("[data-map-frame]").append(frame);
      root.querySelector("[data-map-placeholder]").hidden = true;
    });
  }
  function stats(root, on) {
    if (root.dataset.animate !== "true" || reduced.matches) return () => {};
    const animations = [];
    let observer;
    function animate() {
      if (reduced.matches || !root.isConnected) return;
      root.querySelectorAll("[data-stat-value]").forEach((value) => {
        const text = value.textContent;
        const match = text.match(/^([\d,]+)(\+|%)?$/);
        if (!match) return;
        const end = Number(match[1].replaceAll(",", ""));
        if (!Number.isSafeInteger(end) || end === 0) return;
        // The final merchant value remains in the DOM and accessibility tree.
        const overlay = document.createElement("span");
        overlay.setAttribute("aria-hidden", "true");
        overlay.className = "fs-pack2-stat-animation";
        value.classList.add("fs-pack2-stat-animating");
        value.append(overlay);
        const start = performance.now();
        let id;
        function stop() { cancelAnimationFrame(id); overlay.remove(); value.classList.remove("fs-pack2-stat-animating"); }
        function step(time) {
          const progress = Math.min(1, (time - start) / 850);
          if (progress === 1 || reduced.matches || !root.isConnected) { stop(); return; }
          overlay.textContent = Math.round(end * (1 - (1 - progress) ** 3)).toLocaleString("en-GB") + (match[2] || "");
          id = requestAnimationFrame(step);
        }
        id = requestAnimationFrame(step);
        animations.push(stop);
      });
    }
    if ("IntersectionObserver" in window) {
      observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) { observer.disconnect(); animate(); } });
      observer.observe(root);
    } else animate();
    on(reduced, "change", () => { if (reduced.matches) { observer?.disconnect(); animations.forEach((stop) => stop()); } });
    return () => { observer?.disconnect(); animations.forEach((stop) => stop()); };
  }
  function init(scope = document) {
    const roots = [...(scope.matches?.(selector) ? [scope] : []), ...scope.querySelectorAll(selector)];
    roots.forEach((root) => {
      if (ready.has(root)) return;
      ready.add(root);
      const controller = new AbortController();
      const on = (target, event, handler) => target?.addEventListener(event, handler, { signal: controller.signal });
      let dispose;
      if (root.hasAttribute("data-tabs")) tabs(root, on);
      if (root.hasAttribute("data-countdown")) dispose = countdown(root, on);
      if (root.hasAttribute("data-alert")) alert(root, on);
      if (root.hasAttribute("data-map")) map(root, on);
      if (root.hasAttribute("data-stats")) dispose = stats(root, on);
      disposers.set(root, () => { controller.abort(); dispose?.(); });
    });
  }
  window.FastShopWidgetsPack2 = { init };
  init();
  new MutationObserver((records) => {
    records.forEach((record) => record.removedNodes.forEach((node) => {
      if (node.nodeType !== 1 || node.isConnected) return;
      [node, ...node.querySelectorAll(selector)].forEach((root) => {
        disposers.get(root)?.(); disposers.delete(root); ready.delete(root);
      });
    }));
    if (records.some((record) => [...record.addedNodes].some((node) => node.nodeType === 1))) init();
  }).observe(document.documentElement, { childList: true, subtree: true });
})();

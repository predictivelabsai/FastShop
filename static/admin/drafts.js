(() => {
  "use strict";
  const editor = document.querySelector("[data-draft-editor]");
  if (!editor) return;
  const root = editor.dataset.draftEditor.replace(/\/[^/]+$/, "");
  function picker(inputSelector, selectSelector, endpoint) {
    const input = editor.querySelector(inputSelector);
    const select = editor.querySelector(selectSelector);
    if (!input || !select) return;
    let timer, controller;
    async function load() {
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(root + endpoint + "?q=" + encodeURIComponent(input.value), {signal: controller.signal});
        if (!response.ok) return;
        const result = await response.json();
        const selected = select.value;
        const existing = Array.from(select.options).find(option => option.value === selected);
        select.replaceChildren(new Option("Choose an option", ""));
        if (existing && selected) select.add(new Option(existing.text, selected));
        for (const item of result.items) if (item.id !== selected) select.add(new Option(item.label, item.id));
        select.value = selected;
      } catch (error) { if (error.name !== "AbortError") input.setAttribute("aria-description", "Search is unavailable. Try again."); }
    }
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 180); });
    load();
  }
  picker("[data-draft-variant-search]", "[data-draft-variant-results]", "/variants");
  picker("[data-draft-customer-search]", "[data-draft-customers]", "/customers");
})();

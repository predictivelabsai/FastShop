(() => {
  'use strict';
  document.querySelectorAll('[data-dropzone]').forEach(zone => {
    ['dragenter', 'dragover'].forEach(name => zone.addEventListener(name, event => {event.preventDefault(); zone.classList.add('gc-over');}));
    zone.addEventListener('dragleave', () => zone.classList.remove('gc-over'));
    zone.addEventListener('drop', event => {event.preventDefault(); zone.classList.remove('gc-over'); const files = event.dataTransfer.files; if (files.length) {zone.querySelector('input').files = files;}});
  });
  const fileDialog = document.getElementById('file-details');
  if (fileDialog) {
    const form = fileDialog.querySelector('form');
    const set = (selector, apply) => { const el = fileDialog.querySelector(selector); if (el) apply(el); };
    document.addEventListener('click', event => {
      const opener = event.target.closest('[data-file]'); if (!opener) return;
      const file = JSON.parse(opener.dataset.file);
      form.action = file.action;
      set('[data-file-field="title"]', el => { el.value = file.title; });
      set('[data-file-field="alt"]', el => { el.value = file.alt; });
      set('[data-file-preview]', el => { el.hidden = !file.image; el.src = file.image ? file.preview : ''; el.alt = file.alt; });
      set('[data-file-meta]', el => { el.textContent = `${file.kind} · ${file.size}`; });
      set('[data-file-usage]', el => { el.textContent = file.usage; });
      set('[data-file-pages]', el => { el.replaceChildren(...file.pages.map(path => Object.assign(document.createElement('li'), {textContent: path}))); });
      set('[data-file-url]', el => { el.value = new URL(file.url, location.href).href; });
      set('[data-file-download]', el => { el.href = file.url; el.download = file.filename; });
      set('[data-file-delete]', el => {
        el.disabled = file.locked;
        el.title = file.locked ? 'Remove the file from your content before deleting it.' : '';
      });
      fileDialog.showModal();
    });
    fileDialog.querySelector('[data-copy-url]')?.addEventListener('click', event => {
      const input = fileDialog.querySelector('[data-file-url]'); const button = event.currentTarget;
      navigator.clipboard.writeText(input.value).then(() => { button.setAttribute('aria-label', 'Copied'); button.title = 'Copied'; },
        () => { input.select(); });
    });
    form.addEventListener('submit', event => {
      if (event.submitter?.value === 'delete' && !confirm('Delete this file? This cannot be undone.')) event.preventDefault();
    });
  }
  document.querySelectorAll('[data-menu-form]').forEach(form => {
    const root = form.querySelector('.gc-tree-root'); let dragging = null;
    const template = form.querySelector('template[data-menu-template]');
    const children = node => Array.from(node.children).filter(el => el.matches('li[data-menu-item]'));
    const pathLabel = select => select.selectedOptions[0]?.textContent || '';
    // Apply the edit fields of a row to its stored item and its visible summary.
    const apply = li => {
      const item = JSON.parse(li.dataset.menuItem);
      const edit = li.querySelector(':scope > .gc-item-edit');
      const label = edit.querySelector('[data-menu-label]').value.trim();
      const link = edit.querySelector('[data-menu-link]'); const url = edit.querySelector('[data-menu-url]').value.trim();
      const current = item.kind === 'external' ? '' : item.path;
      if (link.value !== current || (item.kind === 'external' && url !== item.url)) {
        delete item.path; delete item.url; delete item.block_id;
        if (link.value) { item.kind = 'page'; item.path = link.value; } else { item.kind = 'external'; item.url = url; }
      }
      li.dataset.menuItem = JSON.stringify(item);
      li.querySelector(':scope > .gc-tree-row [data-menu-label-text]').textContent = label;
      li.querySelector(':scope > .gc-tree-row [data-menu-target-text]').textContent =
        item.kind === 'external' ? item.url : (link.value === current && item.kind === 'anchor'
          ? li.querySelector(':scope > .gc-tree-row [data-menu-target-text]').textContent : pathLabel(link).replace(/ \([^)]*\)$/, ''));
      return label;
    };
    const serialize = tree => children(tree).map(li => {
      const item = JSON.parse(li.dataset.menuItem); const label = li.querySelector(':scope > .gc-item-edit [data-menu-label]').value;
      item.label = typeof item.label === 'object' ? {...item.label, [form.dataset.menuLocale]: label} : label;
      const nested = serialize(li.querySelector(':scope > ol')); if (nested.length) item.children = nested; else delete item.children; return item;
    });
    const changed = () => { form.querySelector('[data-menu-json]').value = JSON.stringify(serialize(root)); form.dispatchEvent(new Event('input', {bubbles: true})); };
    const linkToggle = (select, field) => { field.hidden = !!select.value; };
    form.addEventListener('submit', changed);
    form.addEventListener('change', event => {
      const select = event.target.closest('[data-menu-link]');
      if (select) linkToggle(select, select.closest('.gc-item-edit').querySelector('[data-menu-url-field]'));
    });
    form.addEventListener('click', event => {
      const edit = event.target.closest('[data-menu-edit]');
      if (edit) {
        const panel = edit.closest('li').querySelector(':scope > .gc-item-edit'); panel.hidden = !panel.hidden;
        edit.setAttribute('aria-expanded', String(!panel.hidden)); if (!panel.hidden) panel.querySelector('input').focus();
        return;
      }
      const done = event.target.closest('[data-menu-done]');
      if (done) {
        const li = done.closest('li'); const panel = li.querySelector(':scope > .gc-item-edit');
        if (!panel.querySelector('[data-menu-label]').value.trim()) { panel.querySelector('[data-menu-label]').focus(); return; }
        apply(li); panel.hidden = true; const button = li.querySelector(':scope > .gc-tree-row [data-menu-edit]');
        button.setAttribute('aria-expanded', 'false'); button.focus(); changed(); return;
      }
      const button = event.target.closest('[data-tree-action]'); if (!button) return;
      const li = button.closest('li'); const siblings = children(li.parentElement); const index = siblings.indexOf(li);
      switch (button.dataset.treeAction) {
        case 'up': if (index > 0) li.parentElement.insertBefore(li, siblings[index-1]); break;
        case 'down': if (siblings[index+1]) li.parentElement.insertBefore(siblings[index+1], li); break;
        case 'indent': if (index > 0 && li.closest('ol').closest('li')?.closest('ol').closest('li') == null) siblings[index-1].querySelector(':scope > ol').append(li); break;
        case 'outdent': {const parent = li.parentElement.closest('li'); if (parent) parent.after(li); break;}
        case 'remove': li.remove(); break;
      }
      if (button.dataset.treeAction !== 'remove' && li.isConnected) li.querySelector(`[data-tree-action="${button.dataset.treeAction}"]`)?.focus();
      changed();
    });
    root.addEventListener('dragstart', event => {dragging = event.target.closest('li'); if (dragging) {dragging.classList.add('gc-dragging'); event.dataTransfer.setData('text/plain', dragging.dataset.menuItem); event.stopPropagation();}});
    root.addEventListener('dragover', event => {event.preventDefault();});
    root.addEventListener('drop', event => {
      event.preventDefault(); event.stopPropagation(); const target = event.target.closest('li');
      if (!dragging || !target || target === dragging || dragging.contains(target)) return;
      const bounds = target.getBoundingClientRect();
      if (event.clientX > bounds.left + bounds.width * .55) target.querySelector(':scope > ol').append(dragging);
      else if (event.clientY < bounds.top + bounds.height / 2) target.before(dragging); else target.after(dragging);
      changed();
    });
    root.addEventListener('dragend', () => {if (dragging) dragging.classList.remove('gc-dragging'); dragging = null;});
    // Adding an item: a Shopify-style row that opens a small inline form.
    const addPanel = form.querySelector('.gc-menu-add'), addOpen = form.querySelector('[data-menu-add-open]');
    const picker = form.querySelector('[data-link-picker]'), newUrlField = form.querySelector('[data-new-url-field]');
    const closeAdd = () => { addPanel.hidden = true; addOpen.hidden = false; addOpen.focus(); };
    addOpen.addEventListener('click', () => { addPanel.hidden = false; addOpen.hidden = true; addPanel.querySelector('input').focus(); });
    form.querySelector('[data-menu-add-cancel]').addEventListener('click', closeAdd);
    picker.addEventListener('change', () => linkToggle(picker, newUrlField));
    linkToggle(picker, newUrlField);
    form.querySelector('[data-add-link]').addEventListener('click', () => {
      const labelInput = form.querySelector('[data-new-label]'); const label = labelInput.value.trim();
      const path = picker.value || form.querySelector('[data-new-url]').value.trim();
      if (!label) { labelInput.focus(); return; }
      if (!path) { form.querySelector('[data-new-url]').focus(); return; }
      const item = {id: crypto.randomUUID().replaceAll('-', ''), label, kind: picker.value ? 'page' : 'external'}; item[item.kind === 'page' ? 'path' : 'url'] = path;
      const li = template.content.firstElementChild.cloneNode(true); li.dataset.menuItem = JSON.stringify(item);
      li.querySelector('[data-menu-label]').value = label;
      const link = li.querySelector('[data-menu-link]'); link.value = picker.value; li.querySelector('[data-menu-url]').value = picker.value ? '' : path;
      linkToggle(link, li.querySelector('[data-menu-url-field]'));
      li.querySelector('[data-menu-label-text]').textContent = label;
      li.querySelector('[data-menu-target-text]').textContent = picker.value ? pathLabel(picker).replace(/ \([^)]*\)$/, '') : path;
      li.querySelector('[data-menu-edit]').setAttribute('aria-label', 'Edit ' + label);
      li.querySelector('[data-tree-action="remove"]').setAttribute('aria-label', 'Remove ' + label);
      root.append(li); changed();
      labelInput.value = ''; form.querySelector('[data-new-url]').value = ''; picker.value = ''; linkToggle(picker, newUrlField);
      closeAdd();
    });
    // Discard in the save bar resets the hidden JSON; reload so the visible tree matches it again.
    form.addEventListener('reset', () => setTimeout(() => location.reload(), 0));
  });
})();

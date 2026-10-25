(() => {
  'use strict';
  document.querySelectorAll('[data-dropzone]').forEach(zone => {
    ['dragenter', 'dragover'].forEach(name => zone.addEventListener(name, event => {event.preventDefault(); zone.classList.add('gc-over');}));
    zone.addEventListener('dragleave', () => zone.classList.remove('gc-over'));
    zone.addEventListener('drop', event => {event.preventDefault(); zone.classList.remove('gc-over'); const files = event.dataTransfer.files; if (files.length) {zone.querySelector('input').files = files;}});
  });
  document.querySelectorAll('[data-rich-source]').forEach(source => {
    const rich = document.createElement('div'); rich.className = 'gc-rich'; rich.contentEditable = 'true'; rich.setAttribute('role', 'textbox'); rich.setAttribute('aria-label', 'Rich content'); rich.setAttribute('aria-multiline', 'true');
    // Draft HTML has passed the shared server-side sanitiser.
    rich.innerHTML = source.value; source.after(rich); source.hidden = true;
    source.form.addEventListener('submit', () => {source.value = rich.innerHTML;});
    source.form.querySelectorAll('[data-rich-command]').forEach(button => button.addEventListener('click', () => {rich.focus(); document.execCommand(button.dataset.richCommand, false); rich.dispatchEvent(new Event('input', {bubbles:true}));}));
    rich.addEventListener('input', () => {source.value = rich.innerHTML; source.dispatchEvent(new Event('change', {bubbles:true}));});
  });
  document.querySelectorAll('[data-menu-form]').forEach(form => {
    const root = form.querySelector(':scope > .gc-tree'); let dragging = null;
    const children = node => Array.from(node.children).filter(el => el.matches('li[data-menu-item]'));
    const serialize = tree => children(tree).map(li => {const item = JSON.parse(li.dataset.menuItem); const label = li.querySelector('[data-menu-label]').value; item.label = typeof item.label === 'object' ? {...item.label, [form.dataset.menuLocale]: label} : label; const nested = serialize(li.querySelector(':scope > ol')); if (nested.length) item.children = nested; else delete item.children; return item;});
    const changed = () => {form.querySelector('[data-menu-json]').value = JSON.stringify(serialize(root)); form.dispatchEvent(new Event('change', {bubbles:true}));};
    form.addEventListener('submit', changed);
    form.addEventListener('click', event => {
      const button = event.target.closest('[data-tree-action]'); if (!button) return;
      const li = button.closest('li'); const siblings = children(li.parentElement); const index = siblings.indexOf(li);
      switch (button.dataset.treeAction) {
        case 'up': if (index > 0) li.parentElement.insertBefore(li, siblings[index-1]); break;
        case 'down': if (siblings[index+1]) li.parentElement.insertBefore(siblings[index+1], li); break;
        case 'indent': if (index > 0 && li.closest('ol').closest('li')?.closest('ol').closest('li') == null) siblings[index-1].querySelector(':scope > ol').append(li); break;
        case 'outdent': {const parent = li.parentElement.closest('li'); if (parent) parent.after(li); break;}
        case 'remove': li.remove(); break;
      }
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
    form.querySelector('[data-link-picker]').addEventListener('change', event => {form.querySelector('[data-new-url]').value = event.target.value;});
    form.querySelector('[data-add-link]').addEventListener('click', () => {
      const label = form.querySelector('[data-new-label]').value.trim(); const path = form.querySelector('[data-new-url]').value.trim();
      if (!label || !path) return;
      const item = {id: crypto.randomUUID().replaceAll('-', ''), label, kind: path.startsWith('/') ? 'page' : 'external'}; item[item.kind === 'page' ? 'path' : 'url'] = path;
      const li = document.createElement('li'); li.draggable = true; li.dataset.menuItem = JSON.stringify(item);
      const row = document.createElement('div'); row.className = 'gc-tree-row';
      const input = document.createElement('input'); input.value = label; input.dataset.menuLabel = ''; input.setAttribute('aria-label', 'Link label'); row.append(input);
      [['up','↑'],['down','↓'],['indent','Nest'],['outdent','Unnest'],['remove','Remove']].forEach(([action,text]) => {const button = document.createElement('button'); button.type = 'button'; button.className = 'a-button'; button.dataset.treeAction = action; button.textContent = text; row.append(button);});
      const nested = document.createElement('ol'); nested.className = 'gc-tree'; li.append(row,nested); root.append(li); changed();
      form.querySelector('[data-new-label]').value = ''; form.querySelector('[data-new-url]').value = '';
    });
  });
})();

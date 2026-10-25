(() => {
  'use strict';
  const form = document.querySelector('[data-discount-form]');
  if (!form) return;
  const value = name => form.elements.namedItem(name)?.value || '';
  const show = (name, visible) => {
    const group = form.querySelector(`[data-discount-field="${name}"]`);
    const field = form.elements.namedItem(name);
    const target = group || (field instanceof Element ? field.closest('label') : null);
    if (target) target.hidden = !visible;
  };
  const update = () => {
    show('codes', value('method') === 'code');
    form.querySelector('[data-generate-discount]').hidden = value('method') !== 'code';
    show('value_type', value('type') !== 'free_shipping');
    show('value', value('type') !== 'free_shipping');
    show('buy_quantity', value('type') === 'buy_x_get_y');
    show('get_quantity', value('type') === 'buy_x_get_y');
    form.querySelectorAll('[data-discount-resource-group]').forEach(group => {
      group.hidden = group.dataset.discountResourceGroup !== value('applies_to');
    });
    show('emails', value('eligibility') === 'emails');
    show('minimum_value', value('minimum_type') !== 'none');
    form.querySelectorAll('p').forEach(p => {
      if (p.textContent.startsWith('For Buy X get Y')) p.hidden = value('type') !== 'buy_x_get_y';
    });
    const method = value('method') === 'automatic' ? 'Applied automatically' : `Code: ${value('codes') || 'Enter a code'}`;
    const amount = value('value_type') === 'percentage' ? `${value('value')}%` : `${value('value')} minor units`;
    const minimum = value('minimum_type') === 'none' ? 'No minimum purchase' : `Minimum ${value('minimum_value')} ${value('minimum_type') === 'quantity' ? 'items' : 'minor units'}`;
    form.querySelector('[data-discount-summary]').textContent = `${value('title') || 'Untitled discount'}. ${form.dataset.discountKindLabel}${value('type') === 'free_shipping' ? '' : ': ' + amount}. ${method}. ${minimum}. Starts: ${value('starts_at') || 'Immediately'}. Ends: ${value('ends_at') || 'No end date'}.`;
  };
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  form.querySelector('[data-generate-discount]').addEventListener('click', () => {
    const bytes = crypto.getRandomValues(new Uint8Array(6));
    form.elements.namedItem('codes').value = Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('').toUpperCase();
    form.dispatchEvent(new Event('input', {bubbles: true}));
  });

  const dialog = document.getElementById('discount-resources');
  const search = dialog?.querySelector('[data-discount-resource-search]');
  const results = dialog?.querySelector('[data-discount-resource-results]');
  const status = dialog?.querySelector('[data-discount-resource-status]');
  let activeGroup;
  let opener;
  let timer;
  let request;
  let loading = false;
  let queued = false;
  const changed = () => form.dispatchEvent(new Event('change', {bubbles: true}));
  const chips = group => {
    const select = group.querySelector('select');
    const container = group.querySelector('[data-resource-chips]');
    container.replaceChildren();
    Array.from(select.selectedOptions).forEach(option => {
      const chip = document.createElement('span');
      chip.className = 'a-resource-chip';
      chip.append(document.createTextNode(option.textContent));
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `Remove ${option.textContent}`);
      remove.addEventListener('click', () => {
        option.selected = false;
        chips(group);
        changed();
        group.querySelector('button[data-discount-resources]').focus();
      });
      chip.append(remove);
      container.append(chip);
    });
  };
  const loadResources = async () => {
    if (!dialog.open || !activeGroup) return;
    if (loading) { queued = true; return; }
    loading = true;
    request = {};
    const pending = request;
    const group = activeGroup;
    const url = new URL(form.dataset.discountResourcesUrl, location.origin);
    url.searchParams.set('kind', group.dataset.discountResourceGroup);
    url.searchParams.set('q', search.value);
    status.textContent = 'Searching…';
    results.setAttribute('aria-busy', 'true');
    results.replaceChildren();
    try {
      const response = await fetch(url, {credentials: 'same-origin'});
      if (!response.ok) throw new Error('Search unavailable');
      const payload = await response.json();
      if (pending !== request || group !== activeGroup) return;
      const select = group.querySelector('select');
      payload.results.forEach(item => {
        const row = document.createElement('label');
        row.className = 'a-resource-option';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        let option = Array.from(select.options).find(option => option.value === item.id);
        checkbox.checked = Boolean(option?.selected);
        checkbox.addEventListener('change', () => {
          if (!option) { option = new Option(item.title, item.id); select.add(option); }
          option.selected = checkbox.checked;
          chips(group);
          changed();
        });
        row.append(checkbox, document.createTextNode(item.title));
        results.append(row);
      });
      status.textContent = payload.results.length ? `${payload.results.length} ${payload.results.length === 1 ? 'result' : 'results'}` : 'No matches. Try another search.';
    } catch (error) {
      if (error.name !== 'AbortError') status.textContent = 'Unable to search. Please try again.';
    } finally {
      if (pending === request) results.removeAttribute('aria-busy');
      loading = false;
      if (queued) { queued = false; loadResources(); }
    }
  };
  if (dialog instanceof HTMLDialogElement && search && results && status) {
    form.querySelectorAll('[data-discount-resource-group]').forEach(group => {
      group.querySelector('[data-resource-fallback]').closest('label').hidden = true;
      const button = group.querySelector('[data-discount-resources]');
      button.hidden = false;
      chips(group);
      button.addEventListener('click', () => {
        activeGroup = group;
        opener = button;
        search.value = '';
        dialog.showModal();
        search.focus();
        loadResources();
      });
    });
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(loadResources, 180); });
    dialog.addEventListener('close', () => { request = null; queued = false; clearTimeout(timer); results.removeAttribute('aria-busy'); opener?.focus(); });
    dialog.querySelectorAll('[data-modal-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
  }
  update();
})();

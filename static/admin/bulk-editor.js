/* Accessible product spreadsheet with one scoped, optimistic save. */
(() => {
  const root = document.querySelector('#bulk-grid');
  if (!root) return;
  const config = JSON.parse(document.querySelector('#bulk-grid-data').textContent);
  const original = new Map(config.rows.map(r => [r.id, structuredClone(r)]));
  let rows = structuredClone(config.rows);
  const dirty = new Set();
  const columns = [
    ['price_minor', 'Price (minor units)', 'number'], ['compare_at_minor', 'Compare-at', 'number'],
    ['cost_minor', 'Cost', 'number'], ['sku', 'SKU', 'text'], ['barcode', 'Barcode', 'text'],
    ['status', 'Status', 'status'], ['track_inventory', 'Track inventory', 'checkbox'],
    ...config.locations.map(w => ['stock:' + w.id, w.name + ' stock', 'number'])
  ];
  const esc = value => String(value ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  root.innerHTML = '<div class="e-actions"><details><summary>Columns</summary>' + columns.map(([key, label]) =>
    `<label><input type="checkbox" data-column="${esc(key)}" checked> ${esc(label)}</label>`).join('') +
    '</details><button type="button" class="a-button" id="bulk-copy">Copy focused cell down</button>' +
    '<button type="button" class="a-button" id="bulk-save">Save changes</button>' +
    '<span role="status" aria-live="polite" id="bulk-status"></span></div>' +
    '<div class="a-table-wrap"><table role="grid" aria-label="Product bulk editor"><thead><tr><th>Product / variant</th>' +
    columns.map(([key,label]) => `<th data-col="${esc(key)}">${esc(label)}</th>`).join('') +
    '<th>Result</th></tr></thead><tbody></tbody></table></div>';
  function value(row, key) {return key.startsWith('stock:') ? row.quantities[key.slice(6)] : row[key];}
  function render() {
    root.querySelector('tbody').innerHTML = rows.map((row, index) => `<tr data-row="${index}"><th scope="row">${esc(row.product)}<br><small>${esc(row.variant)}</small></th>` +
      columns.map(([key,label,type]) => {
        const v = value(row,key), attrs = `data-cell="${esc(key)}" aria-label="${esc(row.product + ' ' + row.variant + ' ' + label)}"`;
        const changed = value(original.get(row.id),key) !== v;
        return `<td data-col="${esc(key)}"${changed ? ' style="background:#fff3cd" data-dirty="true"' : ''}>` + (type === 'status' ? `<select ${attrs}>${['active','draft','archived'].map(s => `<option${s === v ? ' selected' : ''}>${s}</option>`).join('')}</select>` :
          `<input ${attrs} type="${type}"${type === 'checkbox' ? (v ? ' checked' : '') : ` value="${esc(v)}"`}${type === 'number' ? ' min="0" step="1"' : ''}>`) + '</td>';
      }).join('') + '<td data-result role="status"></td></tr>').join('');
    root.querySelectorAll('[data-column]').forEach(toggle => {
      root.querySelectorAll('[data-col]').forEach(cell => {if (cell.dataset.col === toggle.dataset.column) cell.hidden = !toggle.checked;});
    });
  }
  let focused;
  root.addEventListener('focusin', event => {if (event.target.matches('[data-cell]')) focused = event.target;});
  function change(input) {
    const row = rows[Number(input.closest('tr').dataset.row)], key = input.dataset.cell;
    let v = input.type === 'checkbox' ? input.checked : input.type === 'number' ?
      (input.value === '' && key === 'compare_at_minor' ? null : Number(input.value)) : input.value;
    if (key.startsWith('stock:')) row.quantities[key.slice(6)] = v;
    else row[key] = v;
    const changed = value(original.get(row.id), key) !== v;
    input.closest('td').style.background = changed ? '#fff3cd' : '';
    input.closest('td').dataset.dirty = String(changed);
    dirty.add(row.id);
    root.querySelector('#bulk-status').textContent = 'Unsaved changes';
  }
  root.addEventListener('input', event => {if (event.target.matches('[data-cell]')) change(event.target);});
  root.addEventListener('change', event => {
    if (event.target.matches('[data-column]')) render();
  });
  root.addEventListener('keydown', event => {
    if (!event.target.matches('[data-cell]') || !['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Enter'].includes(event.key)) return;
    const input = event.target, row = input.closest('tr'), cells = [...row.querySelectorAll('td:not([hidden]) [data-cell]')];
    const index = cells.indexOf(input);
    let target;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') target = cells[index + (event.key === 'ArrowRight' ? 1 : -1)];
    else {
      const next = Number(row.dataset.row) + ((event.key === 'ArrowUp' || event.shiftKey) ? -1 : 1);
      target = root.querySelector(`tr[data-row="${next}"] [data-cell="${input.dataset.cell}"]`);
    }
    if (target) {event.preventDefault(); target.focus();}
  });
  root.querySelector('#bulk-copy').onclick = () => {
    if (!focused?.isConnected) return;
    const index = Number(focused.closest('tr').dataset.row), key = focused.dataset.cell;
    root.querySelectorAll('[data-cell]').forEach(input => {
      if (input.dataset.cell === key && Number(input.closest('tr').dataset.row) > index) {
        input.value = focused.value; input.checked = focused.checked; change(input);
      }
    });
  };
  root.querySelector('#bulk-save').onclick = async event => {
    if (!dirty.size) return;
    event.target.disabled = true;
    try {
      const response = await fetch(`/admin/sites/${encodeURIComponent(config.site)}/products/bulk-edit`, {
        method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':config.csrf},
        body:JSON.stringify({rows:rows.filter(row => dirty.has(row.id))})});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || 'Could not save changes.');
      for (const updated of result.rows) {
        const index = rows.findIndex(r => r.id === updated.id);
        if (!result.errors[updated.id] && index !== -1) {
          rows[index] = updated; original.set(updated.id, structuredClone(updated)); dirty.delete(updated.id);
        }
      }
      render();
      rows.forEach((row,index) => {
        const tr = root.querySelector(`tr[data-row="${index}"]`);
        tr.querySelector('[data-result]').textContent = result.errors[row.id] || (result.saved.includes(row.id) ? 'Saved' : '');
        if (dirty.has(row.id)) tr.querySelectorAll('td[data-col]').forEach(cell => {
          if (value(original.get(row.id),cell.dataset.col) !== value(row,cell.dataset.col)) cell.style.background = '#fff3cd';
        });
      });
      root.querySelector('#bulk-status').textContent = `${result.saved.length} saved; ${Object.keys(result.errors).length} need attention`;
    } catch (error) {root.querySelector('#bulk-status').textContent = error.message;}
    finally {event.target.disabled = false;}
  };
  render();
})();

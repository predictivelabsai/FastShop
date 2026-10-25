/* Catalogue tools are progressively enhanced without external dependencies. */
(() => {
  'use strict';
  const initialise = () => {
    document.querySelectorAll('.c-catalog, [data-collection-editor], [data-catalog-bulk]').forEach(root => {
      if (root.dataset.catalogReady) return;
      root.dataset.catalogReady = 'true';
      const change = element => element.dispatchEvent(new Event('input', { bubbles: true }));
      const read = name => {
        const input = root.querySelector(`[data-catalog-json="${name}"]`);
        return input ? JSON.parse(input.value || '[]') : [];
      };
      const write = (name, value) => {
        const input = root.querySelector(`[data-catalog-json="${name}"]`);
        if (input) { input.value = JSON.stringify(value); change(input); }
      };
      const el = (tag, text, attrs = {}) => {
        const node = document.createElement(tag);
        if (text != null) node.textContent = text;
        for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
        return node;
      };
      const button = (text, fn) => {
        const node = el('button', text, { type: 'button', class: 'a-button' });
        node.addEventListener('click', fn); return node;
      };
      const input = (label, value, handler, type = 'text') => {
        const node = el('input', null, { type, 'aria-label': label });
        node.value = value ?? '';
        if (type === 'number') { node.step = '0.01'; node.min = '0'; }
        node.addEventListener('input', () => handler(node.value));
        return node;
      };
      const rich = root.querySelector('[data-rich-editor]');
      const source = root.querySelector('[data-rich-text]');
      if (rich && source) {
        rich.addEventListener('input', () => { source.value = rich.innerHTML; change(source); });
        source.addEventListener('input', () => { if (!rich.hidden) rich.innerHTML = source.value; });
        root.querySelectorAll('[data-rich-command]').forEach(node => node.addEventListener('mousedown', event => event.preventDefault()));
        root.querySelectorAll('[data-rich-command]').forEach(node => node.addEventListener('click', () => {
          const command = node.dataset.richCommand;
          let value = null;
          if (command === 'createLink') {
            value = window.prompt('Link URL (https://...)');
            if (!value || !/^(https:\/\/|\/|mailto:)/i.test(value)) return;
          }
          rich.focus(); document.execCommand(command, false, value);
          source.value = rich.innerHTML; change(source);
        }));
        root.querySelector('[data-rich-block]')?.addEventListener('change', event => {
          rich.focus(); document.execCommand('formatBlock', false, event.target.value);
          source.value = rich.innerHTML; change(source);
        });
        root.querySelector('[data-rich-source]')?.addEventListener('click', event => {
          const showSource = !rich.hidden;
          if (!showSource) rich.innerHTML = source.value;
          rich.hidden = showSource; source.hidden = !showSource;
          source.closest('label').hidden = !showSource;
          event.target.setAttribute('aria-pressed', String(showSource));
        });
        source.closest('label').hidden = true;
      }
      const tags = root.querySelector('[data-tags-input]');
      const renderTags = () => {
        const host = root.querySelector('[data-tags-chips]');
        if (!host || !tags) return;
        host.replaceChildren(...tags.value.split(',').map(t => t.trim()).filter(Boolean).map(t => el('span', t, { class: 'c-chip' })));
      };
      tags?.addEventListener('input', renderTags); renderTags();
      const profit = () => {
        const price = Number(root.querySelector('[data-profit-price]')?.value || 0);
        const cost = Number(root.querySelector('[data-profit-cost]')?.value || 0);
        const output = root.querySelector('[data-profit-output]');
        if (output) output.textContent = `Profit: ${(price - cost).toFixed(2)} · Margin: ${price ? ((price - cost) / price * 100).toFixed(1) : '0'}%`;
      };
      root.querySelectorAll('[data-profit-price], [data-profit-cost]').forEach(node => node.addEventListener('input', profit)); profit();
      [['seo_title', 'title'], ['seo_description', 'description'], ['slug', 'handle']].forEach(([name, key]) => {
        const node = root.querySelector(`[name="${name}"]`);
        node?.addEventListener('input', () => { const target = root.querySelector(`[data-seo-preview-${key}]`); if (target) target.textContent = node.value; });
      });
      const optionsHost = root.querySelector('[data-options-list]');
      let options = read('options_json');
      let variants = read('variants_json');
      const existingByValues = new Map(variants.map(v => [JSON.stringify(Object.values(v.attributes_json || {})), v]));
      const selectedVariants = new Set();
      const variantHost = root.querySelector('[data-variants-table]');
      const optionValues = () => options.map(option => option.values.filter(Boolean));
      const generateVariants = () => {
        if (!options.length) { write('options_json', options); renderVariants(); return; }
        const lists = optionValues();
        if (lists.some(list => !list.length)) { write('options_json', options); return; }
        const combinations = lists.reduce((all, values) => all.flatMap(row => values.map(value => [...row, value])), [[]]);
        if (combinations.length > 100) { window.alert('A product can have at most 100 variants. Remove some option values.'); return; }
        const currentByValues = new Map(variants.map(v => [JSON.stringify(Object.values(v.attributes_json || {})), v]));
        const defaultPrice = Math.round(Number(root.querySelector('[name="price"]')?.value || 0) * 100);
        variants = combinations.map(values => {
          const prior = currentByValues.get(JSON.stringify(values)) || existingByValues.get(JSON.stringify(values));
          return { ...(prior || { price_minor: defaultPrice, compare_at_minor: Math.round(Number(root.querySelector('[name="compare_at_price"]')?.value || 0) * 100) || null, cost_minor: Math.round(Number(root.querySelector('[name="cost"]')?.value || 0) * 100), sku: '', barcode: '', quantities: {}, track_inventory: true }),
            name: values.join(' / '), attributes_json: Object.fromEntries(options.map((option, i) => [option.name, values[i]])) };
        });
        write('options_json', options); write('variants_json', variants); renderVariants();
      };
      const renderOptions = () => {
        if (!optionsHost) return;
        optionsHost.replaceChildren();
        options.forEach((option, i) => {
          const row = el('div', null, { class: 'c-option' });
          const nameLabel = el('label', 'Option name');
          nameLabel.append(input('Option name', option.name, value => { option.name = value; generateVariants(); }));
          const valuesLabel = el('label', 'Option values');
          valuesLabel.append(input('Option values', option.values.join(', '), value => {
            option.values = [...new Set(value.split(',').map(v => v.trim()).filter(Boolean))];
            generateVariants();
          }));
          const chips = el('div', null, { class: 'c-chips' });
          option.values.forEach(value => chips.append(el('span', value, { class: 'c-chip' })));
          row.append(nameLabel, valuesLabel, chips, button('Remove option', () => { options.splice(i, 1); renderOptions(); generateVariants(); }));
          optionsHost.append(row);
        });
      };
      const renderVariants = () => {
        if (!variantHost) return;
        variantHost.replaceChildren();
        const table = el('table'); const head = el('thead'); const heading = el('tr');
        ['', 'Variant', 'Price', 'Inventory', 'SKU', 'Details'].forEach(label => heading.append(el('th', label))); head.append(heading);
        const body = el('tbody');
        variants.forEach((variant, i) => {
          const name = variant.name || 'Default';
          const row = el('tr'); const selectedCell = el('td'); const selected = el('input', null, { type: 'checkbox', 'aria-label': `Select variant ${name}` });
          selected.checked = selectedVariants.has(i);
          selected.addEventListener('change', () => selected.checked ? selectedVariants.add(i) : selectedVariants.delete(i)); selectedCell.append(selected);
          row.append(selectedCell, el('td', name));
          const priceCell = el('td'); priceCell.append(input(`Price ${name}`, ((variant.price_minor || 0) / 100).toFixed(2), value => { variant.price_minor = Math.round(Number(value) * 100); write('variants_json', variants); }, 'number'));
          const quantityCell = el('td');
          const warehouses = Array.from(root.querySelectorAll('[name^="quantity_"]')).map(node => node.name.replace('quantity_', ''));
          if (!warehouses.length) quantityCell.append(el('span', 'No location'));
          warehouses.forEach((id, index) => {
            const quantity = input(`Quantity ${name}${index ? ' location ' + (index + 1) : ''}`, variant.quantities?.[id] || 0, value => { variant.quantities = { ...variant.quantities, [id]: Number(value) }; write('variants_json', variants); }, 'number');
            quantity.step = '1'; quantityCell.append(quantity);
          });
          const skuCell = el('td'); skuCell.append(input(`SKU ${name}`, variant.sku, value => { variant.sku = value; write('variants_json', variants); }));
          const detailsCell = el('td'); const details = el('details'); details.append(el('summary', 'Edit variant', { class: 'a-button' }));
          [['Barcode', 'barcode'], ['Compare-at price', 'compare_at_minor'], ['Cost', 'cost_minor']].forEach(([label, key]) => {
            const wrapper = el('label', label); const money = key.endsWith('_minor');
            wrapper.append(input(`${label} ${name}`, money ? ((variant[key] || 0) / 100).toFixed(2) : variant[key], value => { variant[key] = money ? Math.round(Number(value) * 100) : value; write('variants_json', variants); }, money ? 'number' : 'text')); details.append(wrapper);
          });
          [['Track quantity', 'track_inventory'], ['Continue selling when out of stock', 'continue_selling_when_out_of_stock']].forEach(([label, key]) => {
            const wrapper = el('label', null, { class: 'c-check' });
            const toggle = el('input', null, { type: 'checkbox', 'aria-label': `${label} ${name}` });
            toggle.checked = Boolean(variant[key]);
            toggle.addEventListener('change', () => { variant[key] = toggle.checked; write('variants_json', variants); });
            wrapper.append(toggle, document.createTextNode(label)); details.append(wrapper);
          });
          const imageLabel = el('label', 'Variant image'); const imageSelect = el('select', null, { 'aria-label': `Image ${name}` });
          imageSelect.append(el('option', 'No image', { value: '' }));
          read('images_json').filter(image => image.id).forEach(image => imageSelect.append(el('option', image.alt || image.id, { value: image.id })));
          imageSelect.value = variant.image_id || ''; imageSelect.addEventListener('change', () => { variant.image_id = imageSelect.value || null; write('variants_json', variants); });
          imageLabel.append(imageSelect); details.append(imageLabel); detailsCell.append(details);
          row.append(priceCell, quantityCell, skuCell, detailsCell); body.append(row);
        });
        table.append(head, body); variantHost.append(table);
      };
      root.querySelector('[data-option-add]')?.addEventListener('click', () => {
        if (options.length >= 3) { window.alert('A product can have at most 3 options.'); return; }
        options.push({ name: options.length ? '' : 'Colour', values: [] }); renderOptions();
        optionsHost.lastElementChild.querySelector('input')?.focus();
      });
      root.querySelector('[data-variant-bulk-apply]')?.addEventListener('click', () => {
        const value = Math.round(Number(root.querySelector('[data-variant-bulk-price]').value) * 100);
        variants.forEach((variant, i) => { if (selectedVariants.has(i)) variant.price_minor = value; });
        write('variants_json', variants); renderVariants();
      });
      ['track_inventory', 'continue_selling_when_out_of_stock'].forEach(key => {
        root.querySelector(`[name="${key}"]`)?.addEventListener('change', event => {
          variants.forEach(variant => { variant[key] = event.target.checked; });
          write('variants_json', variants); renderVariants();
        });
      });
      renderOptions(); renderVariants();
      root.querySelector('form[data-catalog-editor]')?.addEventListener('reset', () => setTimeout(() => window.location.reload(), 0));
      root.querySelector('form[data-catalog-editor]')?.addEventListener('submit', () => {
        if (rich && !rich.hidden) source.value = rich.innerHTML;
        write('variants_json', variants); write('options_json', options);
      });
      const imagesHost = root.querySelector('[data-media-list]');
      let images = read('images_json');
      let draggedImage = null;
      const renderImages = () => {
        if (!imagesHost) return;
        imagesHost.replaceChildren();
        images.forEach((image, i) => {
          const tile = el('div', null, { class: 'c-media-tile', draggable: 'true' });
          tile.append(el('img', null, { src: image.image_url, alt: image.alt || '' }));
          const label = el('label', 'Alt text'); label.append(input(`Image ${i + 1} alt text`, image.alt, value => { image.alt = value; write('images_json', images); }));
          tile.append(label, button('Move earlier', () => { if (i > 0) { images.splice(i - 1, 0, images.splice(i, 1)[0]); write('images_json', images); renderImages(); } }), button('Remove image', () => { images.splice(i, 1); write('images_json', images); renderImages(); }));
          tile.addEventListener('dragstart', () => { draggedImage = i; });
          tile.addEventListener('dragover', event => event.preventDefault());
          tile.addEventListener('drop', event => { event.preventDefault(); if (draggedImage == null) return; images.splice(i, 0, images.splice(draggedImage, 1)[0]); draggedImage = null; write('images_json', images); renderImages(); });
          imagesHost.append(tile);
        });
      };
      root.querySelector('[data-media-add]')?.addEventListener('click', () => {
        const id = root.querySelector('[name="media_picker"]').value;
        const image = read('media_library_json').find(item => item.media_id === id);
        if (image && !images.some(item => item.image_url === image.image_url)) { images.push({ ...image }); write('images_json', images); renderImages(); }
      });
      const upload = root.querySelector('[name="images_upload"]');
      upload?.addEventListener('change', () => {
        root.querySelectorAll('[data-upload-preview]').forEach(node => node.remove());
        Array.from(upload.files).forEach(file => {
          const node = el('img', null, { src: URL.createObjectURL(file), alt: file.name, 'data-upload-preview': '' });
          node.addEventListener('load', () => URL.revokeObjectURL(node.src), { once: true }); imagesHost.append(node);
        });
      });
      const drop = root.querySelector('[data-media-drop]');
      drop?.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('is-dragging'); });
      drop?.addEventListener('dragleave', () => drop.classList.remove('is-dragging'));
      drop?.addEventListener('drop', event => { event.preventDefault(); drop.classList.remove('is-dragging'); upload.files = event.dataTransfer.files; upload.dispatchEvent(new Event('change', { bubbles: true })); });
      renderImages();
      const selectAll = root.querySelector('[data-c-select-all]');
      const checks = Array.from(root.querySelectorAll('[name="product_ids"]'));
      const updateSelection = () => {
        const count = checks.filter(node => node.checked).length;
        const label = root.querySelector('[data-c-selection-count]'); if (label) label.textContent = `${count} selected`;
        root.classList.toggle('has-selection', count > 0);
      };
      selectAll?.addEventListener('change', () => { checks.forEach(node => { node.checked = selectAll.checked; }); updateSelection(); });
      checks.forEach(node => node.addEventListener('change', updateSelection)); updateSelection();
      let productIds = read('product_ids_json'); const picker = read('product_picker_json');
      const collectionList = root.querySelector('[data-collection-list]');
      let draggedProduct = null;
      const renderProducts = () => {
        if (!collectionList) return;
        collectionList.replaceChildren();
        productIds.forEach((id, i) => {
          const p = picker.find(item => item.id === id); if (!p) return;
          const row = el('div', null, { class: 'c-picked-product', draggable: 'true' });
          row.append(el('span', `⠿ ${p.title}`), button('Remove', () => { productIds.splice(i, 1); write('product_ids_json', productIds); renderProducts(); }));
          row.addEventListener('dragstart', () => { draggedProduct = i; }); row.addEventListener('dragover', event => event.preventDefault());
          row.addEventListener('drop', event => { event.preventDefault(); if (draggedProduct == null) return; productIds.splice(i, 0, productIds.splice(draggedProduct, 1)[0]); draggedProduct = null; write('product_ids_json', productIds); renderProducts(); });
          collectionList.append(row);
        });
      };
      root.querySelector('[data-collection-add]')?.addEventListener('click', () => {
        const id = root.querySelector('[data-collection-picker]').value;
        if (id && !productIds.includes(id)) { productIds.push(id); write('product_ids_json', productIds); renderProducts(); }
      });
      root.querySelector('[data-collection-search]')?.addEventListener('input', event => {
        root.querySelectorAll('[data-collection-picker] option').forEach(option => { option.hidden = !option.textContent.toLowerCase().includes(event.target.value.toLowerCase()); });
      });
      renderProducts();
      let rules = read('rules_json'); if (Array.isArray(rules)) rules = { match: 'all', rules: [] };
      const rulesHost = root.querySelector('[data-rules-list]');
      const renderRules = () => {
        if (!rulesHost) return;
        rulesHost.replaceChildren();
        (rules.rules || []).forEach((rule, i) => {
          const row = el('div', null, { class: 'c-rule' });
          const field = el('select', null, { 'aria-label': 'Rule field' });
          [['tag', 'Tag'], ['vendor', 'Vendor'], ['type', 'Product type'], ['title', 'Title'], ['price', 'Price (minor units)'], ['inventory', 'Inventory']].forEach(([v, t]) => field.append(el('option', t, { value: v })));
          field.value = rule.field; field.addEventListener('change', () => { rule.field = field.value; write('rules_json', rules); });
          const op = el('select', null, { 'aria-label': 'Rule operator' });
          [['equals', 'is equal to'], ['not_equals', 'is not equal to'], ['contains', 'contains'], ['gt', 'is greater than'], ['lt', 'is less than'], ['gte', 'is at least'], ['lte', 'is at most']].forEach(([v, t]) => op.append(el('option', t, { value: v })));
          op.value = rule.operator; op.addEventListener('change', () => { rule.operator = op.value; write('rules_json', rules); });
          row.append(field, op, input('Rule value', rule.value, value => { rule.value = value; write('rules_json', rules); }), button('Remove condition', () => { rules.rules.splice(i, 1); write('rules_json', rules); renderRules(); })); rulesHost.append(row);
        });
      };
      root.querySelector('[data-rule-add]')?.addEventListener('click', () => { rules.rules ||= []; rules.rules.push({ field: 'tag', operator: 'equals', value: '' }); write('rules_json', rules); renderRules(); });
      root.querySelector('[name="match"]')?.addEventListener('change', event => { rules.match = event.target.value; write('rules_json', rules); });
      const kind = root.querySelector('[name="kind"]');
      const showKind = () => { const manual = root.querySelector('[data-collection-manual]'); const automatic = root.querySelector('[data-collection-automated]'); if (manual) manual.hidden = kind.value !== 'manual'; if (automatic) automatic.hidden = kind.value !== 'automated'; };
      kind?.addEventListener('change', showKind); if (kind) showKind(); renderRules();
      root.querySelector('[data-rule-preview-button]')?.addEventListener('click', async () => {
        const output = root.querySelector('[data-rule-preview]'); output.textContent = 'Checking products…';
        try {
          const response = await fetch(root.dataset.previewUrl, { method: 'POST', body: new FormData(root), credentials: 'same-origin' });
          if (!response.ok) throw new Error('Could not evaluate conditions');
          const result = await response.json(); output.textContent = `${result.count} matching ${result.count === 1 ? 'product' : 'products'}: ${result.products.map(p => p.title).join(', ')}`;
        } catch (error) { output.textContent = error.message; }
      });
    });
  };
  document.addEventListener('DOMContentLoaded', initialise);
  document.addEventListener('htmx:afterSettle', initialise);
  initialise();
})();

/* Shared progressive enhancements. Server forms remain the source of truth. */
(() => {
  'use strict';
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const toast = (text, error = false) => {
    const node = make('div', text, 'a-toast');
    node.setAttribute('role', error ? 'alert' : 'status');
    const close = make('button', '×');
    close.type = 'button'; close.setAttribute('aria-label', 'Dismiss notification');
    close.addEventListener('click', () => node.remove()); node.append(close);
    document.querySelector('.a-shell')?.append(node);
  };
  const enhance = () => {
    const root = document.querySelector('.a-content');
    if (!root) return;
    const primaryActions = Array.from(root.querySelectorAll('.a-button-primary, .p3-primary, .a-primary')).filter(node => !node.closest('dialog'));
    const primary = primaryActions.find(node => node.closest('.a-header')) || primaryActions[0];
    primaryActions.forEach(node => {
      if (node !== primary) { node.classList.remove('a-button-primary', 'p3-primary', 'a-primary'); node.classList.add('a-button'); }
    });
    if (!primary) {
      const submit = Array.from(root.querySelectorAll('form[method=post] button:not([type=button])')).find(node =>
        node.getClientRects().length && !node.closest('[class*=bulk], dialog') && node.value !== 'delete');
      submit?.classList.add('a-button-primary');
    }
    root.querySelectorAll('[data-modal-open], [data-resource-picker]').forEach(button => {
      if (button.dataset.polishModal) return;
      button.dataset.polishModal = 'true';
      button.addEventListener('click', () => {
        const dialog = document.getElementById(button.dataset.modalOpen || button.dataset.resourcePicker);
        if (dialog instanceof HTMLDialogElement && !dialog.open) dialog.showModal();
      });
    });
    root.querySelectorAll('dialog [data-modal-close]').forEach(button => {
      if (button.dataset.polishModal) return;
      button.dataset.polishModal = 'true'; button.addEventListener('click', () => button.closest('dialog').close());
    });
    root.querySelectorAll('input[type=file]').forEach(input => {
      if (input.closest('.a-dropzone, .c-drop, .gc-dropzone, .u-dropzone') || input.dataset.dropReady) return;
      input.dataset.dropReady = 'true';
      const fileLabel = input.labels?.[0]?.textContent.trim();
      if (fileLabel && !input.hasAttribute('aria-label')) input.setAttribute('aria-label', fileLabel);
      const zone = make('div', '', 'a-dropzone');
      const title = make('strong', 'Add files or drag and drop');
      const hint = make('span', input.accept ? `Accepted files: ${input.accept}` : 'Choose files to upload');
      hint.setAttribute('role', 'status');
      input.before(zone); zone.append(title, hint, input);
      input.addEventListener('change', () => { hint.textContent = Array.from(input.files).map(file => file.name).join(', ') || 'Choose files to upload'; });
      ['dragenter', 'dragover'].forEach(name => zone.addEventListener(name, event => { event.preventDefault(); zone.classList.add('is-dragging'); }));
      zone.addEventListener('dragleave', () => zone.classList.remove('is-dragging'));
      zone.addEventListener('drop', event => {
        event.preventDefault(); zone.classList.remove('is-dragging');
        if (!event.dataTransfer.files.length) return;
        const transfer = new DataTransfer();
        Array.from(event.dataTransfer.files).slice(0, input.multiple ? undefined : 1).forEach(file => transfer.items.add(file));
        input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
    root.querySelectorAll('input[name="tags"]').forEach(source => {
      if (source.dataset.chipsReady || source.closest('[class*=bulk], [data-catalog-bulk]')) return;
      source.dataset.chipsReady = 'true';
      const host = make('div', '', 'a-tags');
      const chips = make('div', '', 'a-tag-list');
      const input = make('input'); input.type = 'text'; input.placeholder = 'Add a tag';
      input.setAttribute('aria-label', 'Add tag');
      const add = make('button', 'Add'); add.type = 'button';
      source.after(host); host.append(chips, input, add); source.hidden = true;
      let tagLabel = false;
      source.closest('label')?.childNodes.forEach(node => { if (node.nodeType === Node.TEXT_NODE) { node.textContent = tagLabel ? '' : 'Tags'; tagLabel = true; } });
      const values = () => [...new Set(source.value.split(',').map(value => value.trim()).filter(Boolean))];
      const render = () => {
        chips.replaceChildren();
        values().forEach(value => {
          const chip = make('span', value, 'a-tag'); const remove = make('button', '×');
          remove.type = 'button'; remove.setAttribute('aria-label', `Remove tag ${value}`);
          remove.addEventListener('click', () => { source.value = values().filter(item => item !== value).join(', '); source.dispatchEvent(new Event('input', { bubbles: true })); render(); input.focus(); });
          chip.append(remove); chips.append(chip);
        });
      };
      const commit = () => {
        source.value = [...new Set([...values(), ...input.value.split(',').map(value => value.trim()).filter(Boolean)])].join(', ');
        input.value = ''; source.dispatchEvent(new Event('input', { bubbles: true })); render();
      };
      add.addEventListener('click', commit);
      input.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); commit(); } });
      source.form?.addEventListener('submit', commit); source.form?.addEventListener('reset', () => setTimeout(render, 0)); render();
      const old = source.parentElement.querySelector('[data-tags-chips]'); if (old) old.hidden = true;
    });
    root.querySelectorAll('input[type=checkbox]').forEach(input => {
      if (/^(track_inventory|requires_shipping|charge_tax|continue_selling_when_out_of_stock|is_published|published|enabled|active|notify_customer|restock|notify|offer_paypal)$/.test(input.name)) {
        input.classList.add('a-switch'); input.setAttribute('role', 'switch');
      }
    });
    root.querySelectorAll('table').forEach(table => {
      const firstHeader = table.querySelector('thead th');
      if (firstHeader?.textContent.trim() === 'Select' && table.querySelector('tbody input[type=checkbox]') && !firstHeader.querySelector('input')) {
        const all = make('input'); all.type = 'checkbox'; all.dataset.selectAll = '';
        all.setAttribute('aria-label', 'Select all rows'); firstHeader.replaceChildren(all);
      }
      const headers = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
      table.classList.add('a-responsive-table');
      table.querySelectorAll('tbody tr').forEach(row => {
        Array.from(row.cells).forEach((cell, index) => { cell.dataset.label = headers[index] || ''; });
        const link = row.querySelector('a[href]');
        if (link && !row.dataset.rowReady) {
          row.dataset.rowReady = 'true'; row.classList.add('a-clickable-row');
          row.addEventListener('click', event => { if (!event.target.closest('a, button, input, select, textarea, summary, label') && !window.getSelection()?.toString()) link.click(); });
        }
      });
    });
    root.querySelectorAll('.c-inventory-row').forEach(row => {
      const link = row.querySelector('a[href]');
      if (!link || row.dataset.rowReady) return;
      row.dataset.rowReady = 'true'; row.classList.add('a-clickable-row');
      row.addEventListener('click', event => {
        if (!event.target.closest('a, button, input, select, textarea, summary, label') && !window.getSelection()?.toString()) link.click();
      });
    });
    root.querySelectorAll('form').forEach(form => {
      const bar = form.querySelector('.u-bulk, .p3-bulk, .c-bulk, [data-bulk-actions], [data-bulk-bar]') ||
        ((form.getAttribute('action') || '').endsWith('/pages/bulk') ? form.querySelector('.a-actions') : null);
      if (!bar || form.dataset.polishBulk) return;
      form.dataset.polishBulk = 'true';
      const update = () => {
        const checks = Array.from(form.querySelectorAll('tbody input[type=checkbox]'));
        const count = checks.filter(input => input.checked).length;
        bar.hidden = !count;
        form.classList.toggle('has-selection', count > 0);
        const all = form.querySelector('[data-select-all]');
        if (all) { all.checked = !!checks.length && count === checks.length; all.indeterminate = count > 0 && count < checks.length; }
        const label = bar.querySelector('[data-selected-count]'); if (label) label.textContent = `${count} selected`;
      };
      form.querySelector('[data-select-all]')?.addEventListener('change', event => {
        form.querySelectorAll('tbody input[type=checkbox]').forEach(input => { input.checked = event.target.checked; }); update();
      });
      form.addEventListener('change', () => setTimeout(update, 0)); update();
    });
    const listPage = /\/(products|orders|customers|discounts|collections|inventory|drafts|subscriptions|pages|blog-posts|files|redirects|gift-cards)$/.test(location.pathname);
    if (listPage) root.querySelectorAll('form[method=get]').forEach(form => {
      if (form.closest('dialog') || !form.querySelector('[name=q]') || form.dataset.filterReady) return;
      form.dataset.filterReady = 'true'; form.classList.add('a-filter-bar');
      const search = form.querySelector('[name=q]'); search.type = 'search'; search.placeholder ||= 'Search';
      const searchLabel = search.closest('label') || search; searchLabel.classList.add('a-filter-search');
      const popover = make('details', '', 'a-filter-popover');
      const summary = make('summary', 'Add filter', 'a-button');
      const panel = make('div', '', 'a-filter-panel'); popover.append(summary, panel);
      const labels = Array.from(form.querySelectorAll('label')).filter(label => label !== searchLabel && label.querySelector('[name]'));
      labels.forEach(label => {
        const control = label.querySelector('[name]');
        if (control.name === 'sort') {
          const sort = make('details', '', 'a-sort-popover');
          const trigger = make('summary', '↕ Sort', 'a-button'); trigger.setAttribute('aria-label', 'Sort');
          const options = make('div', '', 'a-filter-panel'); label.classList.add('a-sort-control');
          options.append(label); sort.append(trigger, options); form.append(sort);
        }
        else panel.append(label);
      });
      form.querySelectorAll('details').forEach(details => { if (details !== popover && !details.classList.contains('a-sort-popover')) details.remove(); });
      if (panel.children.length) searchLabel.after(popover);
      const chips = make('div', '', 'a-active-filters'); chips.setAttribute('aria-label', 'Active filters'); form.after(chips);
      const paintChips = () => {
        chips.replaceChildren();
        Array.from(form.elements).forEach(control => {
          if (!control.name || control.type === 'hidden' || control.name === 'sort' || control.name === 'q' || !control.value || control.value.toLowerCase() === 'all' || (control.type === 'checkbox' && !control.checked)) return;
          const label = control.closest('label');
          const title = label?.childNodes[0]?.textContent.trim() || control.name.replaceAll('_', ' ');
          const value = control.selectedOptions ? control.selectedOptions[0]?.textContent : control.value;
          const chip = make('button', `${title}: ${value} ×`, 'a-filter-chip'); chip.type = 'button';
          chip.setAttribute('aria-label', `Remove ${title.toLowerCase()} filter`);
          chip.addEventListener('click', () => { if (control.type === 'checkbox') control.checked = false; else control.value = control.options ? control.options[0].value : ''; update(); }); chips.append(chip);
        });
      };
      const status = make('span', '', 'a-filter-status'); status.setAttribute('role', 'status'); form.append(status);
      let timer; let updating = false; let pending = false;
      const update = async () => {
        clearTimeout(timer);
        if (updating) { pending = true; return; }
        updating = true;
        const url = new URL(form.getAttribute('action') || location.href, location.origin);
        url.search = new URLSearchParams(new FormData(form)).toString(); url.searchParams.delete('page');
        root.setAttribute('aria-busy', 'true'); status.textContent = 'Updating results…'; paintChips();
        try {
          const response = await fetch(url, { credentials: 'same-origin' });
          if (!response.ok) throw new Error('Unable to load results');
          const documentNext = new DOMParser().parseFromString(await response.text(), 'text/html');
          if (pending) return;
          const next = documentNext.querySelector('.a-content'); if (!next) throw new Error('Unable to load results');
          const freshForm = Array.from(next.querySelectorAll('form[method=get]')).find(item => item.querySelector('[name=q]'));
          if (!freshForm) throw new Error('Unable to load filters');
          const focused = form.contains(document.activeElement) ? document.activeElement : null;
          const selection = focused && 'selectionStart' in focused ? [focused.selectionStart, focused.selectionEnd] : null;
          freshForm.replaceWith(form); chips.remove(); form.after(chips);
          root.replaceChildren(...next.childNodes);
          focused?.focus({ preventScroll: true });
          if (selection && selection[0] !== null) focused.setSelectionRange(...selection);
          history.replaceState(null, '', url); root.removeAttribute('aria-busy');
          status.textContent = 'Results updated'; enhance();
          document.dispatchEvent(new Event('htmx:afterSwap'));
          document.dispatchEvent(new Event('htmx:afterSettle'));
        } catch (error) { if (error.name !== 'AbortError') { status.textContent = 'Could not update results. Press Enter to try again.'; toast(status.textContent, true); } }
        finally {
          updating = false;
          if (pending) { pending = false; update(); }
          else root.removeAttribute('aria-busy');
        }
      };
      form.addEventListener('input', event => { if (event.target.name) { clearTimeout(timer); timer = setTimeout(update, 350); } });
      form.addEventListener('change', event => { if (event.target.matches('select, [type=checkbox]')) update(); });
      form.addEventListener('submit', event => { event.preventDefault(); update(); }); paintChips();
      // Keep submit controls available for keyboard and script-free use.
      form.querySelectorAll('button:not([type=button])').forEach(button => button.classList.add('a-filter-apply'));
    });
  };
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.a-filter-popover[open], .a-sort-popover[open], .a-view-popover[open], .c-more[open], .p3-more-actions[open], .a-store-switcher[open], .a-user-menu[open]').forEach(details => { details.open = false; details.querySelector('summary')?.focus(); });
  });
  document.addEventListener('click', event => document.querySelectorAll('.a-filter-popover[open], .a-sort-popover[open], .a-view-popover[open], .c-more[open], .p3-more-actions[open]').forEach(details => { if (!details.contains(event.target)) details.open = false; }));
  // Window receives bubbling submits after page-specific document handlers.
  window.addEventListener('submit', async event => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement) || (form.getAttribute('method') || '').toLowerCase() !== 'post' || !form.closest('.a-shell') || form.target) return;
    const data = new FormData(form, event.submitter || undefined);
    // Attachment actions retain the browser's native download handling.
    if (data.get('action') === 'export') return;
    event.preventDefault();
    if (form.dataset.submitting) return;
    form.dataset.submitting = 'true';
    form.setAttribute('aria-busy', 'true');
    const submitters = Array.from(form.querySelectorAll('button:not([type=button]), input[type=submit]')).filter(button => !button.disabled);
    submitters.forEach(button => { button.disabled = true; });
    let navigating = false;
    try {
      const response = await fetch(new URL(form.getAttribute('action') || location.href, location.origin), {
        method: 'POST', body: data, credentials: 'same-origin',
      });
      if (!response.ok) {
        const text = await response.text();
        const message = response.headers.get('content-type')?.includes('text/html')
          ? new DOMParser().parseFromString(text, 'text/html').querySelector('[role=alert]')?.textContent || 'Could not save changes. Review the form and try again.'
          : text.trim();
        throw new Error(message.slice(0, 300) || 'Could not save changes. Please try again.');
      }
      try { sessionStorage.setItem('admin-mutation', JSON.stringify({ time: Date.now(), path: location.pathname })); } catch (_) { /* Storage may be disabled. */ }
      if (response.redirected) { navigating = true; location.assign(response.url); }
      else if (response.headers.get('content-type')?.includes('text/html')) {
        const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
        const next = parsed.querySelector('.a-content');
        const content = document.querySelector('.a-content');
        if (!next || !content) { navigating = true; location.assign(response.url); return; }
        history.replaceState(null, '', response.url);
        document.title = parsed.title;
        content.replaceChildren(...next.childNodes);
        // Native POST result pages (including CSV previews) keep their returned HTML.
        content.querySelectorAll('script[src]').forEach(script => {
          const url = new URL(script.getAttribute('src'), location.origin);
          if (url.origin === location.origin && url.pathname.startsWith('/static/')) {
            const replacement = document.createElement('script'); replacement.src = url.href;
            script.replaceWith(replacement);
          }
        });
        enhance();
        document.dispatchEvent(new Event('htmx:afterSwap'));
        document.dispatchEvent(new Event('htmx:afterSettle'));
        try { sessionStorage.removeItem('admin-mutation'); } catch (_) { /* Storage may be disabled. */ }
        const heading = content.querySelector('h1'); heading?.setAttribute('tabindex', '-1'); heading?.focus({ preventScroll: true });
        toast(heading?.textContent.includes('preview') ? 'Preview ready' : 'Action completed');
      } else { navigating = true; location.assign(response.url); }
    } catch (error) {
      toast(error.message || 'Could not save changes. Please try again.', true);
      form.querySelector('input:not([type=hidden]), select, textarea, button')?.focus({ preventScroll: true });
      document.dispatchEvent(new Event('admin:mutation-error'));
    } finally {
      if (!navigating) {
        delete form.dataset.submitting; form.removeAttribute('aria-busy');
        submitters.forEach(button => { button.disabled = false; });
      }
    }
  });
  const start = () => {
    enhance();
    try {
      const pending = JSON.parse(sessionStorage.getItem('admin-mutation') || 'null'); sessionStorage.removeItem('admin-mutation');
      if (pending && Date.now() - pending.time < 60000) {
        const error = document.querySelector('.a-content [role=alert], .a-content .e-error');
        if (!document.querySelector('.a-toast')) toast(error ? error.textContent.trim() : 'Changes saved', !!error);
        const heading = document.querySelector('.a-content h1'); heading?.setAttribute('tabindex', '-1'); heading?.focus({ preventScroll: true });
      }
    } catch (_) { /* Storage may be disabled. */ }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
  document.addEventListener('htmx:afterSwap', enhance);
})();

/* Progressive enhancement for Merchant Admin v2. No dependencies. */
(() => {
  'use strict';
  const initialise = () => {
    const shell = document.querySelector('.a-shell');
    if (!shell || shell.dataset.adminReady) return;
    shell.dataset.adminReady = 'true';
    const sidebar = document.getElementById('admin-sidebar');
    const toggles = shell.querySelectorAll('[data-sidebar-toggle]');
    const setSidebar = (open) => {
      shell.classList.toggle('is-sidebar-open', open);
      toggles.forEach((button) => button.setAttribute('aria-expanded', String(open)));
      if (open) sidebar?.querySelector('summary, a, input, button')?.focus();
    };
    toggles.forEach((button) => button.addEventListener('click', () => setSidebar(!shell.classList.contains('is-sidebar-open'))));
    let backdrop = shell.querySelector('.a-sidebar-backdrop');
    if (!backdrop) {
      backdrop = document.createElement('button');
      backdrop.className = 'a-sidebar-backdrop';
      backdrop.type = 'button';
      backdrop.setAttribute('aria-label', 'Close navigation');
      backdrop.tabIndex = -1;
      shell.append(backdrop);
    }
    backdrop.addEventListener('click', () => setSidebar(false));
    sidebar?.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab' || !shell.classList.contains('is-sidebar-open')) return;
      const focusable = Array.from(sidebar.querySelectorAll('a, input, button, summary, [tabindex="0"]')).filter((item) => item.getClientRects().length && !item.disabled);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    });
    const search = document.getElementById('admin-search');
    const results = document.getElementById('admin-search-results');
    let searchTimer;
    let request;
    let selected = -1;
    const closeSearch = () => {
      if (results) results.hidden = true;
      search?.setAttribute('aria-expanded', 'false');
      search?.removeAttribute('aria-activedescendant');
      selected = -1;
    };
    const showMessage = (message) => {
      if (!results) return;
      results.replaceChildren();
      const text = document.createElement('p');
      text.className = 'a-search-message';
      text.setAttribute('role', 'status');
      text.textContent = message;
      results.append(text);
      results.hidden = false;
      search.setAttribute('aria-expanded', 'true');
    };
    const runSearch = async () => {
      const query = search.value.trim();
      request?.abort();
      if (!query) { closeSearch(); return; }
      request = new AbortController();
      const activeRequest = request;
      const url = new URL(search.dataset.searchUrl, window.location.origin);
      url.searchParams.set('q', query);
      url.searchParams.set('format', 'json');
      showMessage('Searching…');
      try {
        const response = await fetch(url, { headers: { Accept: 'application/json' }, signal: activeRequest.signal, credentials: 'same-origin' });
        if (!response.ok) throw new Error('Search failed');
        const payload = await response.json();
        if (activeRequest !== request) return;
        const matches = Array.isArray(payload.results) ? payload.results : [];
        if (!matches.length) { showMessage('No results found. Try another search.'); return; }
        results.replaceChildren();
        selected = -1;
        matches.forEach((match, index) => {
          const target = new URL(match.href, window.location.origin);
          if (target.origin !== window.location.origin) return;
          const link = document.createElement('a');
          link.id = `admin-search-result-${index}`;
          link.className = 'a-search-result';
          link.href = target.pathname + target.search;
          link.setAttribute('role', 'option');
          link.setAttribute('aria-selected', 'false');
          link.textContent = match.title || 'Untitled';
          const detail = document.createElement('span');
          detail.textContent = [match.type, match.subtitle].filter(Boolean).join(' · ');
          link.append(detail);
          results.append(link);
        });
        results.hidden = false;
        search.setAttribute('aria-expanded', 'true');
      } catch (error) {
        if (error.name !== 'AbortError' && activeRequest === request) showMessage('Search is unavailable. Please try again.');
      }
    };
    if (search && results && search.dataset.searchUrl) {
      search.setAttribute('role', 'combobox');
      search.setAttribute('aria-autocomplete', 'list');
      search.setAttribute('aria-controls', results.id);
      search.setAttribute('aria-expanded', 'false');
      results.hidden = true;
      search.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 180); });
      search.addEventListener('keydown', (event) => {
        const links = Array.from(results.querySelectorAll('[role="option"]'));
        if (event.key === 'Escape') { closeSearch(); return; }
        if (['ArrowDown', 'ArrowUp'].includes(event.key) && links.length && !results.hidden) {
          event.preventDefault();
          selected = selected < 0
            ? (event.key === 'ArrowDown' ? 0 : links.length - 1)
            : (selected + (event.key === 'ArrowDown' ? 1 : -1) + links.length) % links.length;
          links.forEach((link, index) => link.setAttribute('aria-selected', String(index === selected)));
          search.setAttribute('aria-activedescendant', links[selected].id);
          links[selected].scrollIntoView({ block: 'nearest' });
        }
        if (event.key === 'Enter' && selected >= 0 && !results.hidden) { event.preventDefault(); links[selected]?.click(); }
      });
      document.addEventListener('click', (event) => { if (!event.target.closest('.a-search')) closeSearch(); });
    }
    document.addEventListener('keydown', (event) => {
      const editing = event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable="true"]');
      if (((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') || (event.key === '/' && !editing)) {
        if (!search) return;
        event.preventDefault();
        if (window.matchMedia('(max-width: 800px)').matches) setSidebar(true);
        search.focus();
        search.select();
      }
      if (event.key === 'Escape' && shell.classList.contains('is-sidebar-open')) { setSidebar(false); toggles[0]?.focus(); }
    });
    const saveBar = document.getElementById('admin-save-bar');
    const guardedForms = Array.from(shell.querySelectorAll('form[data-dirty-guard]'));
    const snapshots = new Map();
    const snapshot = (form) => JSON.stringify(Array.from(new FormData(form).entries(), ([key, value]) => [key, value instanceof File ? (value.name ? `${value.name}:${value.size}:${value.lastModified}` : '') : value]));
    let dirtyForms = [];
    let submitting = false;
    const refreshDirty = () => {
      dirtyForms = guardedForms.filter((form) => snapshot(form) !== snapshots.get(form));
      if (saveBar) saveBar.hidden = !dirtyForms.length;
      guardedForms.forEach((form) => form.dataset.dirty = String(dirtyForms.includes(form)));
    };
    guardedForms.forEach((form) => {
      snapshots.set(form, snapshot(form));
      form.addEventListener('input', refreshDirty);
      form.addEventListener('change', refreshDirty);
      form.addEventListener('submit', () => { submitting = true; });
      form.addEventListener('reset', () => setTimeout(refreshDirty, 0));
    });
    saveBar?.querySelector('[data-discard]')?.addEventListener('click', () => { dirtyForms.forEach((form) => form.reset()); setTimeout(refreshDirty, 0); });
    saveBar?.querySelector('[data-save]')?.addEventListener('click', () => {
      const form = dirtyForms[0];
      if (!form) return;
      const submitter = form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
      if (submitter && !submitter.disabled) form.requestSubmit(submitter);
      else form.requestSubmit();
    });
    window.addEventListener('beforeunload', (event) => {
      if (dirtyForms.length && !submitting) { event.preventDefault(); event.returnValue = ''; }
    });
    shell.querySelectorAll('[data-settings-search]').forEach((input) => input.addEventListener('input', () => {
      const query = input.value.toLowerCase().trim();
      const root = input.closest('.a-settings-nav, .admin-settings-layout, .a-settings') || input.closest('.a-card');
      root?.querySelectorAll('[data-settings-item], .a-settings-nav a, .admin-settings-nav a').forEach((link) => { link.hidden = !link.textContent.toLowerCase().includes(query); });
    }));
    shell.querySelectorAll('[data-table-search]').forEach((input) => input.addEventListener('input', () => {
      const query = input.value.toLowerCase().trim();
      const root = input.closest('[data-index-table]') || input.closest('.a-card');
      root?.querySelectorAll('tbody tr').forEach((row) => { row.hidden = !row.textContent.toLowerCase().includes(query); });
    }));
    shell.querySelectorAll('[data-index-table], .a-index-table').forEach((table) => {
      const checks = Array.from(table.querySelectorAll('tbody input[type="checkbox"]'));
      const all = table.querySelector('[data-select-all]');
      const bar = table.querySelector('[data-bulk-actions], [data-bulk-bar]');
      const update = () => {
        const count = checks.filter((check) => check.checked).length;
        if (bar) { bar.hidden = !count; const label = bar.querySelector('[data-selected-count]'); if (label) label.textContent = `${count} selected`; }
        if (all) { all.checked = !!checks.length && count === checks.length; all.indeterminate = count > 0 && count < checks.length; }
      };
      checks.forEach((check) => check.addEventListener('change', update));
      all?.addEventListener('change', () => { checks.filter((check) => !check.closest('tr').hidden).forEach((check) => { check.checked = all.checked; }); update(); });
      update();
    });
    shell.querySelectorAll('[data-modal-open], [data-resource-picker]').forEach((button) => button.addEventListener('click', () => {
      const id = button.dataset.modalOpen || button.dataset.resourcePicker;
      const dialog = document.getElementById(id);
      if (dialog instanceof HTMLDialogElement) dialog.showModal();
    }));
    shell.querySelectorAll('dialog').forEach((dialog) => {
      dialog.querySelectorAll('[data-modal-close]').forEach((button) => button.addEventListener('click', () => dialog.close()));
      dialog.addEventListener('click', (event) => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
    });
    shell.querySelectorAll('.a-toast').forEach((toast) => {
      const dismiss = toast.querySelector('[data-toast-dismiss]');
      dismiss?.addEventListener('click', () => toast.remove());
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialise);
  else initialise();
  document.addEventListener('htmx:afterSwap', initialise);
})();

/* Column visibility is persisted by the CSRF-protected form, scoped to this user. */
(() => {
  const enhance = () => {
    const control = document.querySelector('[data-visible-columns]');
    if (!control) return;
    const filter = document.querySelector('.a-content form.a-filter-bar');
    if (filter && !control.closest('.a-filter-toolbar')) {
      const focused = filter.contains(document.activeElement) ? document.activeElement : null;
      const selection = focused && 'selectionStart' in focused ? [focused.selectionStart, focused.selectionEnd] : null;
      const toolbar = document.createElement('div');
      toolbar.className = 'a-filter-toolbar';
      filter.before(toolbar); toolbar.append(filter, control);
      focused?.focus({ preventScroll: true });
      if (selection && selection[0] !== null) focused.setSelectionRange(...selection);
    }
    const view = new URL(location.href).searchParams.get('saved_view');
    if (view) {
      document.querySelectorAll('main a[href]').forEach(link => {
        const target = new URL(link.href, location.href);
        if (!link.hasAttribute('data-saved-view-link') && target.pathname === location.pathname && target.search && !target.searchParams.has('saved_view')) {
          target.searchParams.set('saved_view', view);
          link.href = target.href;
        }
      });
      document.querySelectorAll('main form[method="get"]').forEach(form => {
        if (new URL(form.action, location.href).pathname !== location.pathname) return;
        if (form.querySelector('[name=saved_view]')) return;
        const input = document.createElement('input');
        input.type = 'hidden'; input.name = 'saved_view'; input.value = view;
        form.append(input);
      });
    }
    const visible = JSON.parse(control.dataset.visibleColumns);
    const available = JSON.parse(control.dataset.availableColumns);
    const table = [...document.querySelectorAll('main table')].find(candidate =>
      [...candidate.querySelectorAll('thead th')].some(th => available.includes(th.textContent.trim())));
    if (!table) return;
    const hidden = [...table.querySelectorAll('thead th')].map((th, index) =>
      available.includes(th.textContent.trim()) && !visible.includes(th.textContent.trim()) ? index : -1);
    table.querySelectorAll('tr').forEach(row => hidden.forEach(index => {
      if (index >= 0 && row.children[index]) row.children[index].hidden = true;
    }));
  };
  enhance();
  document.addEventListener('htmx:afterSettle', enhance);
})();

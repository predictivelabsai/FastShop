/* Shopify-style index cards: views and saved views share one tab row, filters sit on one
   compact row underneath, and "Clear" only appears while a filter is active. Runs after the
   shared filter and saved-view enhancers, and again after every live filter update. */
(() => {
  const ICON_SORT = window.fsIcon('arrow-up-down', 16);
  const ICON_FILTER = window.fsIcon('list-filter', 16);
  const ICON_COLUMNS = window.fsIcon('columns-3', 16);
  const IGNORED = new Set(['page', 'saved_view', 'view', 'sort', 'status', 'tab']);

  const setLabel = (summary, icon, text) => {
    if (!summary || summary.dataset.polarisLabel) return;
    summary.dataset.polarisLabel = 'true';
    summary.innerHTML = icon + '<span>' + text + '</span>';
  };

  const filtersActive = () => {
    for (const [key, value] of new URL(location.href).searchParams) {
      if (!IGNORED.has(key) && value && value.toLowerCase() !== 'all') return true;
    }
    return false;
  };

  const enhance = () => {
    const content = document.querySelector('.a-shell.p-shell .a-content');
    if (!content) return;
    content.querySelectorAll('form.a-filter-bar').forEach(form => {
      let card = form.closest('.a-card');
      if (!card) {
        // A filter row that sits loose on the page joins the list it filters in one card.
        card = document.createElement('div');
        card.className = 'a-card p-index';
        const anchor = form.closest('.a-filter-toolbar') || form;
        anchor.before(card);
        card.append(anchor);
        let next = card.nextElementSibling;
        const joins = '.a-empty, .a-table-wrap, table, .a-active-filters, .c-filter-chips, .c-pagination, ' +
          '.p3-pagination, .u-pagination, .p-pager';
        while (next && (next.matches(joins) || (next.matches('form') && next.querySelector('table')))) {
          const following = next.nextElementSibling;
          card.append(next);
          next = following;
        }
      }
      card.classList.add('p-index');
      const tabs = card.querySelector(':scope > nav');
      const controls = content.querySelector('.a-view-controls');
      let head = card.querySelector(':scope > .p-index-head');
      if (!head && (tabs || controls)) {
        head = document.createElement('div');
        head.className = 'p-index-head';
        (tabs || form.closest('.a-filter-toolbar') || form).before(head);
        if (tabs) { tabs.classList.add('p-index-tabs'); head.append(tabs); }
      }
      if (head && controls && !head.contains(controls)) {
        const saved = controls.querySelector(':scope > nav');
        if (saved && tabs) {
          const activeSaved = saved.querySelector('a.is-active:not(:first-child)');
          saved.querySelectorAll('a').forEach(link => {
            if (link.textContent.trim() === 'All') return;
            link.classList.add('p-saved-view');
            tabs.append(link);
          });
          if (activeSaved) {
            tabs.querySelectorAll('a').forEach(link => {
              if (link === activeSaved) return;
              link.classList.remove('is-active', 'active');
              link.removeAttribute('aria-current');
            });
          }
          saved.hidden = true;
        }
        controls.querySelectorAll('.a-view-popover > summary').forEach(summary => {
          const text = summary.textContent.trim();
          if (text === 'Columns') setLabel(summary, ICON_COLUMNS, 'Columns');
        });
        head.append(controls);
      }
      setLabel(form.querySelector('.a-sort-popover > summary'), ICON_SORT, 'Sort');
      setLabel(form.querySelector('.a-filter-popover > summary'), ICON_FILTER, 'Add filter');
      // Pages that render their own removable filter chips keep those; hide the duplicate set.
      const serverChips = card.querySelector('.c-filter-chips, .p3-chips, .u-chips');
      const liveChips = card.querySelector('.a-active-filters');
      if (liveChips) liveChips.hidden = !!serverChips;
      const active = filtersActive();
      form.querySelectorAll(':scope > a').forEach(link => {
        if (link.textContent.trim() === 'Clear') { link.hidden = !active; link.classList.add('p-clear'); }
      });
    });
  };

  // Forms whose Apply button is only needed without JavaScript apply as soon as a choice changes.
  const autoApply = () => document.querySelectorAll('[data-auto-apply]').forEach(button => {
    const form = button.form;
    if (!form || form.dataset.autoApplyReady) return;
    form.dataset.autoApplyReady = 'true';
    button.classList.add('p-auto-apply');
    form.addEventListener('change', () => form.requestSubmit());
  });
  // A title field fills its URL path until the merchant edits the path themselves.
  const slugs = () => document.querySelectorAll('[data-slug-source]').forEach(source => {
    const target = document.getElementById(source.dataset.slugSource);
    if (!target || source.dataset.slugReady) return;
    source.dataset.slugReady = 'true';
    target.addEventListener('input', () => { target.dataset.edited = target.value ? 'true' : ''; });
    source.addEventListener('input', () => {
      if (target.dataset.edited) return;
      const slug = source.value.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
      target.value = slug ? ('slugPlain' in source.dataset ? slug : '/' + slug) : '';
    });
  });
  // Clicking the dimmed backdrop closes a modal, as in Shopify.
  document.addEventListener('click', event => {
    const dialog = event.target;
    if (dialog instanceof HTMLDialogElement && dialog.classList.contains('p-modal') && dialog.open) dialog.close();
    // Popovers close when the merchant clicks anywhere else.
    document.querySelectorAll('details.p-popover[open]').forEach(popover => {
      if (!popover.contains(event.target)) popover.open = false;
    });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('details.p-popover[open]').forEach(popover => {
      popover.open = false; popover.querySelector('summary')?.focus();
    });
  });
  // A search box that filters the rows of a list page, hiding sections that end up empty.
  const filters = () => document.querySelectorAll('[data-filter-input]').forEach(input => {
    if (input.dataset.filterReady) return;
    input.dataset.filterReady = 'true';
    input.addEventListener('input', () => {
      const term = input.value.trim().toLowerCase();
      let shown = 0;
      document.querySelectorAll('[data-filter-section]').forEach(section => {
        let visible = 0;
        section.querySelectorAll('[data-filter-item]').forEach(item => {
          const match = !term || item.dataset.filterItem.includes(term) ||
            section.querySelector('h2')?.textContent.toLowerCase().includes(term);
          item.hidden = !match; visible += match ? 1 : 0;
        });
        section.hidden = !visible; shown += visible;
      });
      const none = document.querySelector('[data-filter-none]'); if (none) none.hidden = shown > 0;
    });
  });
  const run = () => setTimeout(() => { enhance(); autoApply(); slugs(); filters(); }, 0);
  document.addEventListener('DOMContentLoaded', run);
  document.addEventListener('htmx:afterSettle', run);
})();

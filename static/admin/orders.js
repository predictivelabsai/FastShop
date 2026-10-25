(() => {
  const rows = Array.from(document.querySelectorAll('[data-order-select]'));
  const all = document.querySelector('[data-order-select-all]');
  const bulk = document.querySelector("#order-bulk");
  const count = document.querySelector('[data-order-count]');
  const update = () => {
    const total = rows.filter(row => row.checked).length;
    if (bulk) bulk.hidden = total === 0;
    if (count) count.textContent = `${total} selected`;
    if (all) { all.checked = rows.length > 0 && total === rows.length; all.indeterminate = total > 0 && total < rows.length; }
  };
  all?.addEventListener('change', () => { rows.forEach(row => { row.checked = all.checked; }); update(); });
  rows.forEach(row => row.addEventListener('change', update));
  document.querySelector('[data-print-order]')?.addEventListener('click', () => window.print());
  update();
})();

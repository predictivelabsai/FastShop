(() => {
  'use strict';
  const form = document.querySelector('[data-discount-form]');
  if (!form) return;
  const value = name => form.elements.namedItem(name)?.value || '';
  const update = () => {
    const kind = form.elements.namedItem('type');
    const show = (name, visible) => {
      const field = form.elements.namedItem(name);
      if (field) field.closest('label').hidden = !visible;
    };
    show('codes', value('method') === 'code');
    form.querySelector('[data-generate-discount]').hidden = value('method') !== 'code';
    show('value_type', value('type') !== 'free_shipping');
    show('value', value('type') !== 'free_shipping');
    show('buy_quantity', value('type') === 'buy_x_get_y');
    show('get_quantity', value('type') === 'buy_x_get_y');
    show('product_ids', value('applies_to') === 'products');
    show('collection_ids', value('applies_to') === 'collections');
    show('emails', value('eligibility') === 'emails');
    show('minimum_value', value('minimum_type') !== 'none');
    form.querySelectorAll('p').forEach(p => {
      if (p.textContent.startsWith('For Buy X get Y')) p.hidden = value('type') !== 'buy_x_get_y';
    });
    const method = value('method') === 'automatic' ? 'Applied automatically' : `Code: ${value('codes') || 'Enter a code'}`;
    const amount = value('value_type') === 'percentage' ? `${value('value')}%` : `${value('value')} minor units`;
    const minimum = value('minimum_type') === 'none' ? 'No minimum purchase' : `Minimum ${value('minimum_value')} ${value('minimum_type') === 'quantity' ? 'items' : 'minor units'}`;
    form.querySelector('[data-discount-summary]').textContent = `${value('title') || 'Untitled discount'}. ${kind.selectedOptions[0].text}${value('type') === 'free_shipping' ? '' : ': ' + amount}. ${method}. ${minimum}. Starts: ${value('starts_at') || 'Immediately'}. Ends: ${value('ends_at') || 'No end date'}.`;
  };
  form.addEventListener('input', update);
  form.querySelector('[data-generate-discount]').addEventListener('click', () => {
    const bytes = crypto.getRandomValues(new Uint8Array(6));
    form.elements.namedItem('codes').value = Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('').toUpperCase();
    form.dispatchEvent(new Event('input', {bubbles: true}));
  });
  update();
})();

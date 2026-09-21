/* Selection bridge exists only on authenticated draft previews. */
(() => {
  const root = document.querySelector('[data-builder-page]');
  if (!root || !root.dataset.builderPage || window.parent === window) return;
  let selecting = false;
  const sections = Array.from(document.querySelectorAll('[data-builder-section]'));
  const selected = element => {
    if (!element) return;
    sections.forEach(section => section.classList.toggle('b-selected-section', section === element));
    window.parent.postMessage({type:'fastshop:section-selected', site:root.dataset.site,
      page:root.dataset.builderPage, section:element.dataset.builderSection}, location.origin);
  };
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.source !== window.parent || !event.data ||
        event.data.type !== 'fastshop:select-mode' || event.data.site !== root.dataset.site ||
        event.data.page !== root.dataset.builderPage) return;
    selecting = event.data.enabled === true;
    document.body.classList.toggle('b-select-mode', selecting);
    sections.forEach(section => {
      if (selecting) { section.setAttribute('tabindex', '0'); section.setAttribute('aria-label', 'Select section: ' + section.dataset.builderLabel); }
      else { section.removeAttribute('tabindex'); section.removeAttribute('aria-label'); }
    });
  });
  document.addEventListener('click', event => {
    if (!selecting) return;
    const section = event.target.closest('[data-builder-section]');
    if (!section) return;
    event.preventDefault(); event.stopImmediatePropagation(); selected(section);
  }, true);
  document.addEventListener('keydown', event => {
    if (selecting && ['Enter', ' '].includes(event.key) && event.target.matches('[data-builder-section]')) {
      event.preventDefault(); selected(event.target);
    }
  });
})();

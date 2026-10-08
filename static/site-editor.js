document.addEventListener('click', event => {
  const button = event.target.closest('[data-move], [data-remove]');
  if (!button) return;
  const section = button.closest('[data-section]');
  if (button.hasAttribute('data-remove')) {
    if (window.confirm('Remove this section from the draft? Save to keep the change.')) section.remove();
  } else if (button.dataset.move === 'up' && section.previousElementSibling) {
    section.parentElement.insertBefore(section, section.previousElementSibling);
  } else if (button.dataset.move === 'down' && section.nextElementSibling) {
    section.parentElement.insertBefore(section.nextElementSibling, section);
  }
});

document.addEventListener('submit', event => {
  const form = event.target.closest('[data-generation-form]');
  if (!form) return;
  const button = form.querySelector('[data-generation-submit]');
  const status = form.querySelector('[data-generation-status]');
  if (button) {
    button.disabled = true;
    button.textContent = 'Building your private draft…';
  }
  if (status) {
    status.hidden = false;
    status.textContent = 'Creating pages, navigation, design and starter content. Keep this tab open.';
  }
});

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

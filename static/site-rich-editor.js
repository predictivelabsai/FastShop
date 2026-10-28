(() => {
  document.querySelectorAll('[data-rich-editor]').forEach(editor => {
    const form = editor.closest('form');
    const input = form?.querySelector(`[name="policy_${editor.dataset.richEditor}"]`);
    if (!input) return;
    editor.innerHTML = input.value;
    editor.closest('.e-rich-field')?.querySelectorAll('[data-rich-command]').forEach(button => {
      button.addEventListener('click', () => {
        const [command, value] = button.dataset.richCommand.split(':');
        if (command === 'createLink') {
          const href = window.prompt('Link address');
          if (href) document.execCommand(command, false, href);
        } else document.execCommand(command, false, value || null);
        editor.focus();
        input.value = editor.innerHTML;
      });
    });
    editor.addEventListener('input', () => { input.value = editor.innerHTML; });
    form.addEventListener('submit', () => { input.value = editor.innerHTML; });
  });
})();

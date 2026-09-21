(() => {
  const scrollHistory = () => {
    const history = document.querySelector('.b-history');
    if (history) history.scrollTop = history.scrollHeight;
  };
  scrollHistory();
  document.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.prompt) {
      const form = document.querySelector('[data-builder-form]');
      if (!form || form.dataset.busy) return;
      form.querySelector('[name=prompt]').value = button.dataset.prompt;
      form.requestSubmit();
    }
    if (button.dataset.previewSize) document.querySelector('.b-preview').dataset.size = button.dataset.previewSize;
    if (button.hasAttribute('data-select-section')) {
      const form = document.querySelector('[data-builder-form]');
      const enabled = button.getAttribute('aria-pressed') !== 'true';
      button.setAttribute('aria-pressed', String(enabled));
      document.querySelector('.b-preview').contentWindow.postMessage({type:'fastshop:select-mode',
        site:form.dataset.site, page:form.querySelector('[name=page_id]').value, enabled}, location.origin);
    }
    if (button.hasAttribute('data-builder-cancel')) {
      const form = button.closest('form');
      button.disabled = true;
      try {
        const response = await fetch(button.dataset.builderCancel, {method:'POST', body:new FormData(form), credentials:'same-origin'});
        if (!response.ok) throw new Error('Could not cancel: the edit may already have completed. Reload to check.');
        location.assign(response.url);
      } catch (error) { form.querySelector('[data-builder-status]').textContent = error.message; button.disabled = false; }
    }
    if (button.dataset.workspaceTab) document.body.classList.toggle('b-show-preview', button.dataset.workspaceTab === 'preview');
  });
  window.addEventListener('message', event => {
    const frame = document.querySelector('.b-preview');
    const form = document.querySelector('[data-builder-form]');
    if (!frame || !form || event.origin !== location.origin || event.source !== frame.contentWindow ||
        !event.data || event.data.type !== 'fastshop:section-selected' || event.data.site !== form.dataset.site ||
        event.data.page !== form.querySelector('[name=page_id]').value) return;
    const select = form.querySelector('[name=section_id]');
    if (!Array.from(select.options).some(option => option.value === event.data.section)) return;
    select.value = event.data.section;
    form.querySelector('[data-builder-status]').textContent = 'Section selected. Describe the change in chat.';
    document.body.classList.remove('b-show-preview');
  });
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.matches('[data-builder-form]')) return;
    event.preventDefault();
    if (form.dataset.busy) return;
    form.dataset.busy = 'true';
    form.setAttribute('aria-busy', 'true');
    const submit = form.querySelector('button.e-button');
    submit.disabled = true;
    form.querySelector('[data-builder-cancel]').hidden = false;
    const status = form.querySelector('[data-builder-status]');
    status.textContent = 'Preparing your draft… This can take up to 40 seconds.';
    try {
      const response = await fetch(form.action, {method:'POST', body:new FormData(form), credentials:'same-origin'});
      if (!response.ok) throw new Error('Request was not applied. Reload to check the latest draft, then try again.');
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      const workspace = parsed.querySelector('.b-workspace');
      if (!workspace) throw new Error('Your session may have expired. Reload to sign in.');
      const size = document.querySelector('.b-preview').dataset.size;
      const section = form.querySelector('[name=section_id]').value;
      const scroll = {x:window.scrollX, y:window.scrollY, editor:document.querySelector('.b-editor').scrollTop};
      document.querySelector('.b-workspace').replaceWith(workspace);
      if (size) workspace.querySelector('.b-preview').dataset.size = size;
      const target = workspace.querySelector('[name=section_id]');
      if (Array.from(target.options).some(option => option.value === section)) target.value = section;
      scrollHistory();
      workspace.querySelector('[name=prompt]').focus({preventScroll:true});
      workspace.querySelector('.b-editor').scrollTop = scroll.editor;
      window.scrollTo(scroll.x, scroll.y);
      workspace.querySelector('[data-builder-status]').textContent = 'Draft updated. Preview refreshed.';
    } catch (error) {
      status.textContent = error.message;
      // An unknown network outcome may have committed. Reuse this command ID
      // until the merchant reloads; never silently submit a fresh duplicate.
      submit.disabled = false;
      delete form.dataset.busy;
      form.removeAttribute('aria-busy');
    }
  });
})();

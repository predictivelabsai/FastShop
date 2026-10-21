/* Progressive enhancements: native details and server forms remain usable without JS. */
(() => {
  const root = document.querySelector('.h-site[data-pack="liquid"]');
  if (!root) return;
  const search = root.querySelector('[data-faq-search]');
  if (search) {
    const categories = [...root.querySelectorAll('[data-faq-category]')];
    const empty = root.querySelector('[data-faq-empty]');
    const filter = () => {
      const query = search.value.trim().toLocaleLowerCase('en-US');
      let count = 0;
      categories.forEach(category => {
        let matches = 0;
        category.querySelectorAll('[data-faq-question]').forEach(row => {
          row.hidden = !!query && !row.dataset.faqText.toLocaleLowerCase('en-US').includes(query);
          if (!row.hidden) matches++;
        });
        category.hidden = !!query && !matches;
        if (!category.hidden) count++;
      });
      if (empty) empty.hidden = count > 0;
    };
    search.addEventListener('input', filter);
    root.querySelectorAll('.faq-chip').forEach(chip => chip.addEventListener('click', () => {
      search.value = ''; filter();
    }));
    categories.forEach(category => category.addEventListener('toggle', event => {
      const opened = event.target;
      if (!opened.matches('details[open]')) return;
      category.querySelectorAll('details[open]').forEach(row => { if (row !== opened) row.open = false; });
    }, true));
    window.addEventListener('pageshow', filter);
  }
  root.querySelectorAll('[data-liquid-contact]').forEach(form => {
    form.noValidate = true;
    let tried = false;
    const validate = () => {
      const value = name => form.elements.namedItem(name).value;
      const errors = {};
      if (!value('name').trim()) errors.name = 'Please add your name.';
      else if (value('name').trim().length > 160) errors.name = 'Please keep your name to 160 characters.';
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value('email').trim()) || value('email').length > 255)
        errors.email = 'Please add a valid email address.';
      if (!value('topic')) errors.topic = 'Please choose a topic.';
      if (!value('message').trim()) errors.message = 'Please write a short message.';
      else if (value('message').trim().length < 10 || value('message').trim().length > 5000)
        errors.message = 'Please write a message of 10–5000 characters.';
      ['name', 'email', 'topic', 'message'].forEach(name => {
        const field = form.elements.namedItem(name);
        const error = form.querySelector('#contact-' + name + '-error');
        field.setAttribute('aria-invalid', String(!!errors[name]));
        if (errors[name]) field.setAttribute('aria-describedby', error.id);
        else field.removeAttribute('aria-describedby');
        error.textContent = errors[name] || '';
        if (errors[name]) error.setAttribute('role', 'alert');
        else error.removeAttribute('role');
      });
      return Object.keys(errors)[0];
    };
    form.addEventListener('submit', event => {
      tried = true;
      const first = validate();
      if (first) {
        event.preventDefault();
        form.elements.namedItem(first).focus();
      }
    });
    form.addEventListener('input', () => { if (tried) validate(); });
    form.addEventListener('change', () => { if (tried) validate(); });
  });
  root.querySelectorAll('[data-liquid-contact] select').forEach(select => {
    select.addEventListener('change', () => { select.dataset.empty = String(!select.value); });
  });
})();

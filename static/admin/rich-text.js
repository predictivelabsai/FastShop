(() => {
  'use strict';

  const ALLOWED = new Set(['p', 'br', 'h2', 'h3', 'h4', 'strong', 'b', 'em', 'i', 'u', 'a', 'ul', 'ol', 'li', 'blockquote', 'hr']);
  const DROPPED = new Set(['script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'template']);
  const SAFE_REL = new Set(['nofollow', 'noopener', 'noreferrer', 'ugc', 'sponsored']);
  const editors = new WeakMap();
  const inlineRoots = new WeakMap();
  const mounted = new WeakSet();
  const forms = new WeakSet();
  let active = null;

  function safeHref(value) {
    const href = String(value || '').trim();
    if (!href || /[\u0000-\u001f\u007f\\]/.test(href)) return '';
    const probe = href.replace(/[\u0000-\u0020\u007f]+/g, '');
    const scheme = probe.match(/^([a-z][a-z0-9+.-]*):/i)?.[1]?.toLowerCase();
    if (scheme) {
      if (scheme === 'http' || scheme === 'https') {
        if (!/^https?:\/\//i.test(href)) return '';
        try {
          const url = new URL(href);
          return url.hostname ? href : '';
        } catch { return ''; }
      }
      if (scheme === 'mailto' || scheme === 'tel') return href.split(':', 2)[1]?.trim() ? href : '';
      return '';
    }
    return href.startsWith('//') ? '' : href;
  }

  function cleanHtml(value) {
    const documentValue = new DOMParser().parseFromString(String(value || ''), 'text/html');
    const cleanChildren = parent => {
      [...parent.children].forEach(element => {
        const tag = element.tagName.toLowerCase();
        if (DROPPED.has(tag)) {
          element.remove();
          return;
        }
        cleanChildren(element);
        if (!ALLOWED.has(tag)) {
          element.replaceWith(...element.childNodes);
          return;
        }
        const linkAttributes = tag === 'a' ? {
          href: element.getAttribute('href'),
          title: element.getAttribute('title'),
          target: element.getAttribute('target'),
          rel: element.getAttribute('rel'),
        } : null;
        [...element.attributes].forEach(attribute => element.removeAttribute(attribute.name));
        if (tag === 'a') {
          const href = safeHref(linkAttributes.href);
          const title = linkAttributes.title;
          const target = linkAttributes.target;
          const rel = linkAttributes.rel;
          if (href) element.setAttribute('href', href);
          if (title) element.setAttribute('title', title.slice(0, 200));
          if (target === '_blank' || target === '_self') element.setAttribute('target', target);
          const tokens = new Set(String(rel || '').toLowerCase().split(/\s+/).filter(token => SAFE_REL.has(token)));
          if (target === '_blank') { tokens.add('noopener'); tokens.add('noreferrer'); }
          if (tokens.size) element.setAttribute('rel', [...tokens].sort().join(' '));
        }
      });
    };
    cleanChildren(documentValue.body);
    return documentValue.body.innerHTML;
  }

  function selectionInside(content) {
    const selection = window.getSelection();
    return selection?.rangeCount && content.contains(selection.getRangeAt(0).commonAncestorContainer)
      ? selection : null;
  }

  function saveRange(state) {
    const selection = selectionInside(state.content);
    if (selection) state.range = selection.getRangeAt(0).cloneRange();
  }

  function restoreRange(state) {
    if (!state.range) return;
    state.content.focus();
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(state.range);
  }

  function currentBlock(state) {
    const selection = selectionInside(state.content);
    let node = selection?.anchorNode;
    if (node?.nodeType === Node.TEXT_NODE) node = node.parentElement;
    while (node && node !== state.content) {
      if (/^(P|H2|H3|H4|BLOCKQUOTE)$/.test(node.tagName)) return node;
      node = node.parentElement;
    }
    return null;
  }

  function currentAnchor(state) {
    const selection = selectionInside(state.content);
    let node = selection?.anchorNode;
    if (node?.nodeType === Node.TEXT_NODE) node = node.parentElement;
    while (node && node !== state.content) {
      if (node.tagName === 'A') return node;
      node = node.parentElement;
    }
    return null;
  }

  function refreshToolbar(state) {
    const block = currentBlock(state);
    const style = state.toolbar.querySelector('[data-rich-block]');
    if (style) style.value = block && /^H[234]$/.test(block.tagName) ? block.tagName.toLowerCase() : 'p';
    state.toolbar.querySelectorAll('[data-rich-command]').forEach(button => {
      const [command, value] = button.dataset.richCommand.split(':');
      let pressed = false;
      if (['bold', 'italic', 'underline', 'insertUnorderedList', 'insertOrderedList'].includes(command)) {
        try { pressed = document.queryCommandState(command); } catch { pressed = false; }
      } else if (command === 'formatBlock' && value === 'blockquote') {
        pressed = Boolean(block?.tagName === 'BLOCKQUOTE');
      }
      button.setAttribute('aria-pressed', String(pressed));
      if (command === 'undo' || command === 'redo') {
        try { button.disabled = !document.queryCommandEnabled(command); } catch { button.disabled = false; }
      }
    });
    state.toolbar.querySelector('[data-rich-link-open]')?.setAttribute('aria-pressed', String(Boolean(currentAnchor(state))));
  }

  function setHiddenValue(state, value, notify = true) {
    state.value.value = value;
    if (notify) state.value.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function sync(state, { notify = true, clean = false } = {}) {
    const value = clean
      ? cleanHtml(state.source.hidden ? state.content.innerHTML : state.source.value)
      : state.source.hidden ? state.content.innerHTML : state.source.value;
    if (clean) {
      state.content.innerHTML = value;
      state.source.value = value;
    }
    setHiddenValue(state, value, notify);
  }

  function showSource(state, show) {
    if (show) {
      saveRange(state);
      state.source.value = state.content.innerHTML;
      state.content.hidden = true;
      state.source.hidden = false;
      state.sourceToggle.setAttribute('aria-pressed', 'true');
      state.source.focus();
    } else {
      const value = cleanHtml(state.source.value);
      state.content.innerHTML = value;
      state.source.value = value;
      state.content.hidden = false;
      state.source.hidden = true;
      state.sourceToggle.setAttribute('aria-pressed', 'false');
      state.content.focus();
    }
    sync(state);
    refreshToolbar(state);
  }

  function execute(state, command, value) {
    if (!state.source.hidden) showSource(state, false);
    restoreRange(state);
    if (command === 'clear') {
      document.execCommand('removeFormat', false);
      document.execCommand('unlink', false);
      document.execCommand('formatBlock', false, 'p');
    } else if (command === 'undo' || command === 'redo') {
      document.execCommand(command, false);
    } else {
      try { document.execCommand('styleWithCSS', false, false); } catch { /* Use semantic tags where supported. */ }
      document.execCommand(command, false, value || null);
    }
    saveRange(state);
    sync(state);
    refreshToolbar(state);
  }

  function openLink(state) {
    if (!state.source.hidden) showSource(state, false);
    saveRange(state);
    const anchor = currentAnchor(state);
    state.url.value = anchor?.getAttribute('href') || '';
    state.target.checked = anchor?.getAttribute('target') === '_blank';
    state.linkError.hidden = true;
    state.linkPopover.hidden = false;
    state.linkButton.setAttribute('aria-expanded', 'true');
    state.url.focus();
    state.url.select();
  }

  function closeLink(state, returnFocus = true) {
    state.linkPopover.hidden = true;
    state.linkButton.setAttribute('aria-expanded', 'false');
    state.linkError.hidden = true;
    if (returnFocus) state.linkButton.focus();
  }

  function applyLink(state) {
    const href = safeHref(state.url.value);
    if (!href) {
      state.linkError.textContent = 'Enter a valid http(s), mailto, tel or relative link.';
      state.linkError.hidden = false;
      state.url.setAttribute('aria-invalid', 'true');
      state.url.focus();
      return;
    }
    state.url.removeAttribute('aria-invalid');
    restoreRange(state);
    let anchor = currentAnchor(state);
    if (anchor) anchor.setAttribute('href', href);
    else {
      document.execCommand('createLink', false, href);
      anchor = currentAnchor(state);
    }
    if (anchor) {
      if (state.target.checked) {
        anchor.setAttribute('target', '_blank');
        anchor.setAttribute('rel', 'noopener noreferrer');
      } else {
        anchor.removeAttribute('target');
        anchor.removeAttribute('rel');
      }
    }
    saveRange(state);
    sync(state);
    refreshToolbar(state);
    closeLink(state, false);
    state.content.focus();
  }

  function initEditor(root, contentOverride = null) {
    if (mounted.has(root) || root.closest('#rich-text-template-holder')) return;
    const content = contentOverride || root.querySelector('[data-rich-content]');
    const source = root.querySelector('[data-rich-source]');
    const value = root.querySelector('[data-rich-value]');
    const toolbar = root.querySelector('[data-rich-text-toolbar], .rt-toolbar');
    if (!content || !source || !value || !toolbar) return;
    const state = {
      root, content, source, value, toolbar,
      sourceToggle: toolbar.querySelector('[data-rich-source-toggle]'),
      linkButton: toolbar.querySelector('[data-rich-link-open]'),
      linkPopover: toolbar.querySelector('[data-rich-link-popover]'),
      url: toolbar.querySelector('[data-rich-url]'),
      target: toolbar.querySelector('[data-rich-target]'),
      linkError: toolbar.querySelector('.rt-link-error'),
      range: null,
    };
    if (!state.sourceToggle || !state.linkButton || !state.linkPopover || !state.url || !state.target || !state.linkError) return;
    mounted.add(root);
    root.dataset.richTextReady = 'true';
    editors.set(root, state);
    content.innerHTML = cleanHtml(content.innerHTML || value.value);
    value.value = content.innerHTML;

    content.addEventListener('focus', () => { active = state; saveRange(state); refreshToolbar(state); });
    content.addEventListener('input', () => { active = state; saveRange(state); sync(state); refreshToolbar(state); });
    content.addEventListener('keyup', () => { saveRange(state); refreshToolbar(state); });
    content.addEventListener('mouseup', () => { saveRange(state); refreshToolbar(state); });
    source.addEventListener('input', () => sync(state));
    source.addEventListener('focus', () => { active = state; });
    content.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !state.linkPopover.hidden) { event.preventDefault(); closeLink(state); return; }
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      const commands = { b: 'bold', i: 'italic', u: 'underline' };
      if (commands[key]) { event.preventDefault(); execute(state, commands[key]); }
      if (key === 'k') { event.preventDefault(); openLink(state); }
    });

    toolbar.querySelectorAll('[data-rich-command]').forEach(button => {
      button.addEventListener('mousedown', event => { event.preventDefault(); saveRange(state); });
      button.addEventListener('click', () => {
        const [command, argument] = button.dataset.richCommand.split(':');
        execute(state, command, argument);
      });
    });
    toolbar.querySelector('[data-rich-block]').addEventListener('mousedown', event => saveRange(state));
    toolbar.querySelector('[data-rich-block]').addEventListener('change', event => execute(state, 'formatBlock', event.target.value));
    state.sourceToggle.addEventListener('click', () => showSource(state, state.source.hidden));
    state.linkButton.addEventListener('mousedown', event => event.preventDefault());
    state.linkButton.addEventListener('click', () => state.linkPopover.hidden ? openLink(state) : closeLink(state));
    toolbar.querySelector('[data-rich-link-apply]').addEventListener('click', () => applyLink(state));
    toolbar.querySelector('[data-rich-link-cancel]').addEventListener('click', () => closeLink(state));
    state.url.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); applyLink(state); } });
    state.linkPopover.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closeLink(state); } });

    content.addEventListener('paste', event => {
      event.preventDefault();
      const clipboard = event.clipboardData;
      const markup = clipboard.getData('text/html');
      if (markup) document.execCommand('insertHTML', false, cleanHtml(markup));
      else document.execCommand('insertText', false, clipboard.getData('text/plain'));
      saveRange(state);
      sync(state);
      refreshToolbar(state);
    });

    const form = root.closest('form');
    if (form && !forms.has(form)) {
      forms.add(form);
      form.addEventListener('submit', () => {
        form.querySelectorAll('.admin-rich-text[data-rich-text-ready="true"]').forEach(editor => {
          const editorState = editors.get(editor);
          if (editorState) sync(editorState, { clean: true });
        });
      }, true);
    }
    refreshToolbar(state);
  }

  function mountInspector(host) {
    const holder = document.getElementById('rich-text-template-holder');
    const template = holder?.querySelector('[data-rich-text]');
    if (!template || !host) return;
    const root = template.cloneNode(true);
    root.removeAttribute('data-rich-text-template');
    root.removeAttribute('id');
    root.querySelector('.rt-label')?.remove();
    const content = root.querySelector('[data-rich-content]');
    const source = root.querySelector('[data-rich-source]');
    const value = root.querySelector('[data-rich-value]');
    const html = cleanHtml(host.dataset.richValue || '');
    content.innerHTML = html;
    content.setAttribute('aria-label', host.dataset.richLabel || 'Rich text');
    source.value = html;
    value.value = html;
    value.dataset.setting = host.dataset.richSetting || '';
    value.dataset.type = 'richtext';
    value.removeAttribute('name');
    host.replaceChildren(root);
    initEditor(root);
  }

  function initAll(root = document) {
    root.querySelectorAll('[data-rich-text]').forEach(editor => initEditor(editor));
  }

  function mountAll(root = document) {
    root.querySelectorAll('[data-rich-inspector]').forEach(mountInspector);
    initAll(root);
  }

  function mountInline(field, host) {
    let root = inlineRoots.get(field);
    if (!root) {
      const template = document.querySelector('#rich-text-template-holder [data-rich-text]');
      const toolbar = template?.querySelector('.rt-toolbar');
      if (!toolbar) return null;
      root = document.createElement('div');
      root.className = 'admin-rich-text';
      root.append(toolbar.cloneNode(true));
      const source = document.createElement('textarea');
      source.dataset.richSource = '';
      source.hidden = true;
      const value = document.createElement('input');
      value.type = 'hidden';
      value.dataset.richValue = '';
      root.append(source, value);
      inlineRoots.set(field, root);
      initEditor(root, field);
    }
    host.replaceChildren(root);
    return root;
  }

  function unmountInline(field) {
    inlineRoots.get(field)?.remove();
  }

  document.addEventListener('selectionchange', () => {
    if (active && selectionInside(active.content)) {
      saveRange(active);
      refreshToolbar(active);
    }
  });
  document.addEventListener('focusin', event => {
    const root = event.target.closest?.('.admin-rich-text[data-rich-text-ready="true"]');
    if (root) active = editors.get(root) || null;
  });
  initAll();
  window.FastShopRichText = { initAll, mountAll, mountInline, unmountInline, sanitize: cleanHtml, safeHref };
})();

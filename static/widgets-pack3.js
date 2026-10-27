/* Readable server output gains optional rotation, dismissal and code copying. */
(() => {
  'use strict';
  const ready = new WeakSet();
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  function announcement(root) {
    const messages = [...root.querySelectorAll('[data-announcement-message]')];
    const pause = root.querySelector('[data-announcement-pause]');
    const dismiss = root.querySelector('[data-announcement-dismiss]');
    let index = 0, paused = false, timer;
    function show() {
      messages.forEach((message, i) => { message.hidden = i !== index; });
      root.dataset.activeMessage = String(index);
    }
    function stop() { window.clearInterval(timer); timer = undefined; }
    function start() {
      stop();
      if (messages.length > 1 && !reduced.matches && !paused) {
        timer = window.setInterval(() => {
          if (!root.isConnected || root.hidden) { stop(); return; }
          index = (index + 1) % messages.length; show();
        }, Number(root.dataset.interval || 5) * 1000);
      }
    }
    function motion() {
      if (reduced.matches) index = 0;
      show();
      if (pause) { pause.hidden = messages.length < 2 || reduced.matches; }
      start();
    }
    if (pause) {
      pause.setAttribute('aria-pressed', 'false');
      pause.addEventListener('click', () => {
        paused = !paused;
        pause.textContent = paused ? 'Resume announcements' : 'Pause announcements';
        pause.setAttribute('aria-pressed', String(paused)); start();
      });
    }
    root.addEventListener('focusin', stop);
    root.addEventListener('focusout', start);
    root.addEventListener('mouseenter', stop);
    root.addEventListener('mouseleave', start);
    reduced.addEventListener('change', motion);
    if (dismiss) {
      dismiss.hidden = false;
      if (root.dataset.preview !== 'true') {
        try { root.hidden = localStorage.getItem(root.dataset.storageKey) === 'dismissed'; } catch (_) { /* Storage is optional. */ }
      }
      dismiss.addEventListener('click', () => {
        root.hidden = true; stop();
        if (root.dataset.preview !== 'true') {
          try { localStorage.setItem(root.dataset.storageKey, 'dismissed'); } catch (_) { /* Storage is optional. */ }
        }
      });
    }
    motion();
    const removal = new MutationObserver(() => {
      if (!root.isConnected) { stop(); reduced.removeEventListener('change', motion); removal.disconnect(); }
    });
    removal.observe(document.body, {childList: true, subtree: true});
  }
  function promo(root) {
    const button = root.querySelector('[data-copy-code]');
    const code = root.querySelector('[data-promo-code]');
    const status = root.querySelector('[data-copy-status]');
    if (!button || !code || !status) return;
    button.hidden = false;
    button.addEventListener('click', async () => {
      try {
        if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(code.textContent.trim());
        status.textContent = 'Code copied.';
      } catch (_) {
        const range = document.createRange(); range.selectNodeContents(code);
        const selection = window.getSelection();
        if (selection) { selection.removeAllRanges(); selection.addRange(range); }
        code.focus();
        status.textContent = 'Code selected. Copy the selected text to use it.';
      }
    });
  }
  function scan(scope) {
    const roots = [...scope.querySelectorAll('[data-announcement],.fs-widget-promo_banner')];
    if (scope.matches && scope.matches('[data-announcement],.fs-widget-promo_banner')) roots.unshift(scope);
    roots.forEach(root => {
      if (ready.has(root)) return;
      ready.add(root);
      if (root.hasAttribute('data-announcement')) announcement(root);
      else promo(root);
    });
  }
  function init() {
    scan(document);
    new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
      if (node.nodeType === 1) scan(node);
    }))).observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

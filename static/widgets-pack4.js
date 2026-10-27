/* Optional showcase behaviour. Server-rendered copy and links remain usable without JavaScript. */
(() => {
  'use strict';
  const ready = new WeakSet();
  const disposers = new WeakMap();
  const selector = '.widget-marquee,.flip-card,.widget-video-lightbox,[data-recently-viewed],[data-recent-product]';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const pointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  const validId = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(value);
  const storageKey = site => `fastshop:recently-viewed:${site}`;
  function readViewed(site) {
    try {
      const values = JSON.parse(localStorage.getItem(storageKey(site)) || '[]');
      return Array.isArray(values) ? [...new Set(values.filter(validId))].slice(0, 8) : [];
    } catch (_) { return []; }
  }
  function marquee(root) {
    const button = root.querySelector('[data-marquee-toggle]');
    root.dataset.enhanced = 'true';
    const viewport = root.querySelector('.marquee-viewport');
    const strips = [...root.querySelectorAll('.marquee-items')];
    const original = strips[0];
    const source = original ? [...original.children] : [];
    function fill() {
      if (!original || !viewport || strips.length < 2) return;
      original.querySelectorAll('[data-marquee-fill]').forEach(item => item.remove());
      // Short messages still cover wide screens. Every visual repetition is silent.
      const width = original.getBoundingClientRect().width;
      const repetitions = reduced.matches || !width ? 1 : Math.min(80, Math.ceil(viewport.clientWidth / width) + 1);
      for (let i = 1; i < repetitions; i += 1) source.forEach(item => {
        const clone = item.cloneNode(true);
        clone.setAttribute('aria-hidden', 'true');
        clone.setAttribute('data-marquee-fill', '');
        [clone, ...clone.querySelectorAll('[data-field]')].forEach(node => {
          node.removeAttribute('data-field'); node.removeAttribute('data-field-type');
        });
        original.append(clone);
      });
      strips[1].replaceChildren(...[...original.children].map(item => item.cloneNode(true)));
      strips[1].querySelectorAll('[data-field]').forEach(node => {
        node.removeAttribute('data-field'); node.removeAttribute('data-field-type');
      });
    }
    const resize = new ResizeObserver(fill);
    if (viewport) resize.observe(viewport);
    reduced.addEventListener('change', fill);
    fill();
    disposers.set(root, () => { resize.disconnect(); reduced.removeEventListener('change', fill); });
    if (!button) return;
    button.hidden = false;
    root.dataset.paused = 'false';
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      const paused = root.dataset.paused !== 'true';
      root.dataset.paused = String(paused);
      button.setAttribute('aria-pressed', String(paused));
      button.textContent = paused ? 'Resume scrolling' : 'Pause scrolling';
    });
  }
  function flip(root) {
    const button = root.querySelector('[data-flip-toggle]');
    if (!button) return;
    root.dataset.enhanced = 'true';
    button.hidden = false;
    let pinned = false, hovered = false;
    const links = [...root.querySelectorAll('.flip-card-back a')];
    function update() {
      const expanded = pinned || hovered;
      root.dataset.expanded = String(expanded);
      button.setAttribute('aria-expanded', String(expanded));
      button.textContent = expanded ? 'Show front' : 'Show details';
      links.forEach(link => { link.tabIndex = expanded ? 0 : -1; });
    }
    button.addEventListener('click', () => { pinned = !(pinned || hovered); hovered = false; update(); });
    root.addEventListener('mouseenter', () => { if (pointer.matches) { hovered = true; update(); } });
    root.addEventListener('mouseleave', () => { hovered = false; if (!root.contains(document.activeElement)) update(); });
    root.addEventListener('focusout', event => { if (!root.contains(event.relatedTarget)) { hovered = false; update(); } });
    root.addEventListener('keydown', event => {
      if (event.key === 'Escape' && (pinned || hovered)) {
        event.preventDefault(); pinned = false; hovered = false; update(); button.focus();
      }
    });
    update();
  }
  function video(root) {
    const opener = root.querySelector('[data-video-open]');
    const dialog = root.querySelector('[data-video-dialog]');
    const mount = root.querySelector('[data-video-mount]');
    const close = root.querySelector('[data-video-close]');
    if (!opener || !dialog || !mount || typeof dialog.showModal !== 'function') return;
    let restore;
    function clean() {
      mount.replaceChildren();
      if (restore?.isConnected) restore.focus({preventScroll: true});
      restore = null;
    }
    opener.addEventListener('click', event => {
      const source = root.dataset.videoSrc;
      if (!source) return;
      event.preventDefault();
      restore = document.activeElement;
      const media = document.createElement(root.dataset.videoKind === 'mp4' ? 'video' : 'iframe');
      if (media.tagName === 'VIDEO') {
        media.controls = true;
        media.playsInline = true;
        media.autoplay = !reduced.matches;
      } else {
        media.title = root.dataset.videoTitle || 'Video';
        media.allow = 'autoplay; fullscreen; picture-in-picture';
        media.allowFullscreen = true;
        media.referrerPolicy = 'strict-origin-when-cross-origin';
      }
      media.src = source;
      mount.replaceChildren(media);
      dialog.showModal();
      close?.focus();
    });
    close?.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', clean);
    dialog.addEventListener('click', event => { if (event.target === dialog) {
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
    } });
    // Native modal dialogs make the page inert; this also cycles keyboard focus inside the dialog.
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const elements = [...dialog.querySelectorAll('button,a[href],iframe,video,[tabindex="0"]')].filter(el => !el.hidden && !el.disabled);
      const first = elements[0], last = elements[elements.length - 1];
      if (!first) { event.preventDefault(); dialog.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    disposers.set(root, () => { if (dialog.open) dialog.close(); mount.replaceChildren(); });
  }
  function recordProduct(root) {
    const site = root.dataset.siteId;
    const product = root.dataset.recentProduct;
    if (!site || !validId(product)) return;
    try {
      localStorage.setItem(storageKey(site), JSON.stringify([product, ...readViewed(site).filter(id => id !== product)].slice(0, 8)));
    } catch (_) { /* Viewing products also works when browser storage is unavailable. */ }
  }
  async function recentlyViewed(root) {
    if (root.dataset.preview === 'true') return;
    const site = root.dataset.siteId, endpoint = root.dataset.recentlyViewedUrl;
    const target = root.querySelector('[data-recently-viewed-content]');
    root.hidden = true;
    const ids = site ? readViewed(site) : [];
    if (!ids.length || !endpoint || !target) return;
    const controller = new AbortController();
    disposers.set(root, () => controller.abort());
    try {
      const url = new URL(endpoint, location.href);
      if (url.origin !== location.origin) return;
      url.searchParams.set('ids', ids.slice(0, Math.max(1, Math.min(8, Number(root.dataset.limit) || 8))).join(','));
      const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
      if (!response.ok || !root.isConnected) return;
      target.innerHTML = await response.text();
      root.hidden = !target.querySelector('a[href]');
    } catch (_) { /* An unavailable fragment leaves the optional section hidden. */ }
  }
  function scan(scope) {
    const roots = [...scope.querySelectorAll(selector)];
    if (scope.matches?.(selector)) roots.unshift(scope);
    roots.forEach(root => {
      if (ready.has(root)) return;
      ready.add(root);
      if (root.hasAttribute('data-recent-product')) recordProduct(root);
      else if (root.hasAttribute('data-recently-viewed')) recentlyViewed(root);
      else if (root.matches('.widget-marquee')) marquee(root);
      else if (root.matches('.flip-card')) flip(root);
      else if (root.matches('.widget-video-lightbox')) video(root);
    });
  }
  function init() {
    scan(document);
    new MutationObserver(records => records.forEach(record => {
      record.removedNodes.forEach(node => {
        if (node.nodeType !== 1 || node.isConnected) return;
        [node, ...node.querySelectorAll(selector)].forEach(root => {
          disposers.get(root)?.(); disposers.delete(root); ready.delete(root);
        });
      });
      record.addedNodes.forEach(node => { if (node.nodeType === 1) scan(node); });
    })).observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

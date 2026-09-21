(() => {
  const root = document.querySelector('.h-site');
  if (!root) return;
  const consentKey = `fastshop-consent:${root.dataset.site}`;
  document.querySelectorAll('[data-gallery-src]').forEach(button => button.addEventListener('click', () => {
    const image = document.querySelector('[data-gallery-main] img');
    if (image) { image.src = button.dataset.gallerySrc; image.alt = button.querySelector('img').alt; }
  }));
  const offerKey = `fastshop-offer:${root.dataset.site}`;
  const cookie = document.querySelector('#h-cookie');
  const offer = document.querySelector('#h-offer-panel');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  if (!reduced.matches && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      observer.unobserve(entry.target);
      const text = entry.target.textContent;
      const target = Number(text.replace(/[^0-9]/g, ''));
      if (!target) return;
      const started = performance.now();
      const frame = now => {
        const progress = Math.min((now - started) / 900, 1);
        entry.target.textContent = progress === 1 ? text : Math.round(target * progress).toLocaleString('en-US') + (text.includes('+') ? '+' : '');
        if (progress < 1) requestAnimationFrame(frame);
      };
      requestAnimationFrame(frame);
    }), {threshold: 0.5});
    document.querySelectorAll('.h-facts strong').forEach(counter => observer.observe(counter));
  }
  const read = (key) => { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Consent still applies in memory. */ } };
  let consent = read(consentKey) || {analytics: false, marketing: false};
  cookie.hidden = Boolean(read(consentKey));
  document.querySelector('#consent-analytics').checked = Boolean(consent.analytics);
  document.querySelector('#consent-marketing').checked = Boolean(consent.marketing);
  window.fastshopConsent = consent;
  // Phase 1 has no analytics or marketing adapters. Future adapters must observe
  // this event and stop collection on withdrawal, not merely hide their UI.
  document.querySelectorAll('[data-consent]').forEach(button => button.addEventListener('click', () => {
    const mode = button.dataset.consent;
    consent = {essential: true, analytics: mode === 'all' || (mode === 'custom' && document.querySelector('#consent-analytics').checked),
      marketing: mode === 'all' || (mode === 'custom' && document.querySelector('#consent-marketing').checked), version: 1, at: new Date().toISOString()};
    write(consentKey, consent);
    window.fastshopConsent = consent;
    window.dispatchEvent(new CustomEvent('fastshop:consent', {detail: consent}));
    cookie.hidden = true;
  }));
  document.querySelectorAll('[data-cookie-open]').forEach(button => button.addEventListener('click', () => {
    cookie.hidden = false;
    document.querySelector('#consent-analytics').checked = consent.analytics;
    document.querySelector('#consent-marketing').checked = consent.marketing;
    cookie.querySelector('button').focus();
  }));
  const menuButton = document.querySelector('[data-menu-toggle]');
  const nav = document.querySelector('.h-nav');
  document.querySelectorAll('.h-shop-menu').forEach(menu => {
    menu.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse') menu.open = true; });
    menu.addEventListener('pointerleave', () => { if (!menu.contains(document.activeElement)) menu.open = false; });
  });
  menuButton.addEventListener('click', () => {
    const open = menuButton.getAttribute('aria-expanded') !== 'true';
    menuButton.setAttribute('aria-expanded', String(open));
    nav.classList.toggle('is-open', open);
  });
  const toast = document.querySelector('#h-toast');
  document.querySelectorAll('[data-commerce-notice]').forEach(button => button.addEventListener('click', () => {
    toast.textContent = 'Accounts and shopping open in Phase 2, after this design review.';
    toast.hidden = false;
    window.setTimeout(() => { toast.hidden = true; }, 5000);
  }));
  const showOffer = () => { offer.hidden = false; };
  document.querySelectorAll('[data-offer-open]').forEach(button => button.addEventListener('click', showOffer));
  document.querySelector('[data-offer-close]').addEventListener('click', () => { offer.hidden = true; write(offerKey, true); });
  if (!read(offerKey)) window.setTimeout(() => { if (cookie.hidden) showOffer(); }, 18000);
  const video = document.querySelector('.h-hero video');
  const toggle = document.querySelector('[data-video-toggle]');
  if (video && toggle) {
    const update = () => { toggle.textContent = video.paused ? 'Play motion' : 'Pause motion'; };
    if (reduced.matches) video.pause();
    video.addEventListener('play', update);
    video.addEventListener('pause', update);
    toggle.addEventListener('click', () => { if (video.paused) video.play().catch(() => {}); else video.pause(); });
    update();
  }
  const scroll = () => root.classList.toggle('h-scrolled', window.scrollY > 80);
  window.addEventListener('scroll', scroll, {passive: true}); scroll();
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      nav.classList.remove('is-open'); menuButton.setAttribute('aria-expanded', 'false');
      offer.hidden = true;
      document.querySelectorAll('.h-shop-menu[open]').forEach(el => { el.open = false; });
    }
  });
  document.querySelectorAll('[data-research-filter]').forEach(button => button.addEventListener('click', () => {
    const filter = button.dataset.researchFilter;
    document.querySelectorAll('[data-research-filter]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    document.querySelectorAll('[data-research-theme]').forEach(card => { card.hidden = filter !== 'All' && card.dataset.researchTheme !== filter; });
  }));
})();

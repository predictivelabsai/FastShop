(() => {
  'use strict';
  const root = document.querySelector('.h-site');
  const panel = document.querySelector('#h-cookie');
  if (!root || !panel) return;
  const site = root.dataset.site;
  const key = `fastshop-consent:${site}`;
  const valid = value => value && value.version === 1
    && typeof value.analytics === 'boolean' && typeof value.marketing === 'boolean'
    && typeof value.preferences === 'boolean';
  const read = () => {
    try {
      const value = JSON.parse(localStorage.getItem(key));
      return valid(value) ? value : null;
    } catch (_) { return null; }
  };
  let consent = read();
  const sync = () => {
    panel.hidden = Boolean(consent);
    panel.querySelector('#consent-preferences').checked = consent?.preferences === true;
    panel.querySelector('#consent-analytics').checked = consent?.analytics === true;
    panel.querySelector('#consent-marketing').checked = consent?.marketing === true;
    window.fastshopConsent = consent || {version: 1, essential: true, preferences: false, analytics: false, marketing: false};
  };
  const announce = () => window.dispatchEvent(new CustomEvent('fastshop:consent', {detail: window.fastshopConsent}));
  sync();
  announce();
  document.querySelectorAll('[data-cookie-open]').forEach(button => button.addEventListener('click', () => {
    sync();
    panel.hidden = false;
    panel.querySelector('button').focus();
  }));
  panel.querySelectorAll('[data-consent]').forEach(button => button.addEventListener('click', () => {
    const mode = button.dataset.consent;
    consent = {
      version: 1,
      essential: true,
      preferences: mode === 'all' || (mode === 'custom' && panel.querySelector('#consent-preferences').checked),
      analytics: mode === 'all' || (mode === 'custom' && panel.querySelector('#consent-analytics').checked),
      marketing: mode === 'all' || (mode === 'custom' && panel.querySelector('#consent-marketing').checked),
      at: new Date().toISOString(),
    };
    try { localStorage.setItem(key, JSON.stringify(consent)); } catch (_) { /* Consent remains active in memory. */ }
    if (!consent.preferences) {
      try { localStorage.removeItem(`fastshop:recently-viewed:${site}`); } catch (_) { /* Storage may be unavailable. */ }
    }
    sync();
    announce();
  }));
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    consent = read();
    sync();
    announce();
  });
})();

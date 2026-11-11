/* Legacy fallback consent panel for embedded widgets without the storefront shell. */
(() => {
  const script = document.currentScript;
  const site = script?.dataset.site;
  if (!site || document.querySelector('#h-cookie')) return;
  const key = `fastshop-consent:${site}`;
  const read = () => {
    try {
      const value = JSON.parse(localStorage.getItem(key));
      return value && value.version === 1 && typeof value.analytics === 'boolean'
        && typeof value.marketing === 'boolean' ? value : null;
    } catch (_) { return null; }
  };
  let consent = read();
  const panel = document.createElement('aside');
  panel.setAttribute('aria-label', 'Cookie preferences');
  panel.style.cssText = 'position:fixed;bottom:16px;left:16px;right:16px;max-width:440px;background:#fff;color:#182a21;padding:20px;border:1px solid #cbd4cf;border-radius:12px;z-index:1000;box-shadow:0 4px 30px #0002;font:14px system-ui';
  panel.innerHTML = '<strong>Cookie preferences</strong><p>Choose whether to allow preferences, analytics and marketing storage.</p><label><input type="checkbox" id="consent-preferences" data-pref="preferences"> Preferences</label> <label><input type="checkbox" data-pref="analytics"> Analytics</label> <label><input type="checkbox" data-pref="marketing"> Marketing</label><div style="margin-top:12px"><button data-choice="all">Accept all</button> <button data-choice="none">Decline</button> <button data-choice="custom">Save preferences</button></div>';
  const open = document.createElement('button');
  open.type = 'button';
  open.textContent = 'Cookie preferences';
  open.style.cssText = 'position:fixed;bottom:12px;right:12px;z-index:999;font:12px system-ui;padding:6px';
  const sync = () => {
    panel.hidden = Boolean(consent);
    panel.querySelectorAll('[data-pref]').forEach(input => {
      input.checked = consent?.[input.dataset.pref] === true;
    });
    window.fastshopConsent = consent || {
      version: 1, essential: true, preferences: false, analytics: false, marketing: false,
    };
  };
  sync();
  open.addEventListener('click', () => { sync(); panel.hidden = false; });
  panel.querySelectorAll('[data-choice]').forEach(button => button.addEventListener('click', () => {
    const choice = button.dataset.choice;
    consent = {
      version: 1,
      essential: true,
      preferences: choice === 'all' || (choice === 'custom' && panel.querySelector('#consent-preferences').checked),
      analytics: choice === 'all' || (choice === 'custom' && panel.querySelector('[data-pref="analytics"]').checked),
      marketing: choice === 'all' || (choice === 'custom' && panel.querySelector('[data-pref="marketing"]').checked),
    };
    try { localStorage.setItem(key, JSON.stringify(consent)); } catch (_) { /* Consent remains in memory. */ }
    sync();
    window.dispatchEvent(new CustomEvent('fastshop:consent', {detail: consent}));
  }));
  document.body.append(panel, open);
})();

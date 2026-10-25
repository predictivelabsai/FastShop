/* Integrations follow the storefront's existing versioned consent and privacy signal. */
(() => {
  const config = document.querySelector('script[src="/static/store-preferences.js"]');
  if (!config) return;
  const key = `fastshop-consent:${config.dataset.site}`;
  let consent = window.fastshopConsent;
  if (!consent) { try { consent = JSON.parse(localStorage.getItem(key)); } catch (_) { /* denied */ } }
  const loaded = new Set();
  const permitted = category => consent && consent.version === 1 &&
    typeof consent.analytics === 'boolean' && typeof consent.marketing === 'boolean' &&
    consent[category] === true && navigator.globalPrivacyControl !== true;
  const apply = () => {
    if ((loaded.has('analytics') && !permitted('analytics')) ||
        (loaded.has('marketing') && !permitted('marketing'))) { location.reload(); return; }
    const id = config.dataset.analytics;
    if (id && permitted('analytics') && !loaded.has('analytics')) {
      loaded.add('analytics');
      window.dataLayer = window.dataLayer || [];
      window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
      window.gtag('consent', 'default', {analytics_storage:'denied', ad_storage:'denied', ad_user_data:'denied', ad_personalization:'denied'});
      window.gtag('consent', 'update', {analytics_storage:'granted'});
      window.gtag('js', new Date());
      window.gtag('config', id, {allow_google_signals:false, allow_ad_personalization_signals:false,
        cookie_prefix:`fs_${config.dataset.site}`, page_location:location.origin + location.pathname});
      const script = document.createElement('script');
      script.async = true; script.referrerPolicy = 'no-referrer';
      script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
      document.head.append(script);
    }
    const pixel = config.dataset.pixel;
    if (pixel && permitted('marketing') && !loaded.has('marketing')) {
      loaded.add('marketing');
      window.fbq = window.fbq || function () { window.fbq.queue.push(arguments); };
      window.fbq.queue = window.fbq.queue || [];
      window.fbq('consent', 'grant'); window.fbq('init', pixel); window.fbq('track', 'PageView');
      const script = document.createElement('script');
      script.async = true; script.referrerPolicy = 'no-referrer';
      script.src = 'https://connect.facebook.net/en_US/fbevents.js'; document.head.append(script);
    }
  };
  window.addEventListener('fastshop:consent', event => { consent = event.detail; apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    try { consent = JSON.parse(event.newValue); } catch (_) { consent = null; }
    apply();
  });
  if (!document.querySelector('#h-cookie')) {
    const panel = document.createElement('aside');
    panel.setAttribute('aria-label', 'Cookie preferences');
    panel.style.cssText = 'position:fixed;bottom:16px;left:16px;right:16px;max-width:440px;background:#fff;color:#182a21;padding:20px;border:1px solid #cbd4cf;border-radius:12px;z-index:1000;box-shadow:0 4px 30px #0002;font:14px system-ui';
    panel.innerHTML = '<strong>Cookie preferences</strong><p>Choose whether to allow analytics and marketing cookies.</p><label><input type="checkbox" data-pref="analytics"> Analytics</label> <label><input type="checkbox" data-pref="marketing"> Marketing</label><div style="margin-top:12px"><button data-choice="all">Accept all</button> <button data-choice="none">Decline</button> <button data-choice="custom">Save preferences</button></div>';
    const open = document.createElement('button'); open.textContent = 'Cookie preferences';
    open.style.cssText = 'position:fixed;bottom:12px;right:12px;z-index:999;font:12px system-ui;padding:6px';
    document.body.append(panel, open);
    panel.hidden = Boolean(consent && consent.version === 1);
    open.onclick = () => { panel.hidden = false; };
    panel.querySelectorAll('[data-choice]').forEach(button => { button.onclick = () => {
      const choice = button.dataset.choice;
      consent = {version:1, essential:true, analytics:choice === 'all' || (choice === 'custom' && panel.querySelector('[data-pref="analytics"]').checked),
        marketing:choice === 'all' || (choice === 'custom' && panel.querySelector('[data-pref="marketing"]').checked)};
      try { localStorage.setItem(key, JSON.stringify(consent)); } catch (_) { /* memory only */ }
      window.fastshopConsent = consent;
      panel.hidden = true;
      window.dispatchEvent(new CustomEvent('fastshop:consent', {detail:consent}));
    }; });
  }
  apply();
})();

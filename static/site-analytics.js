/* Basic consent mode: no Google resource is requested before explicit consent. */
(() => {
  const root = document.querySelector('.h-site');
  const id = root && root.dataset.ga4;
  if (!id || !/^G-[A-Z0-9]{6,20}$/.test(id)) return;
  const site = root.dataset.site;
  if (!/^[a-f0-9]{32}$/.test(site)) return;
  const key = `fastshop-consent:${site}`;
  const prefix = `fs_${site}`;
  const path = root.dataset.analyticsPath || '/';
  const disabled = `ga-disable-${id}`;
  const optOut = `fastshop_analytics_denied_${site}`;
  let started = false;
  let consent = window.fastshopConsent;
  const allowed = () => consent && consent.version === 1 && consent.analytics === true && typeof consent.marketing === 'boolean'
    && navigator.globalPrivacyControl !== true && !document.cookie.split(';').some(item => item.trim() === `${optOut}=1`);
  const rememberWithdrawal = choice => {
    if (!choice || choice.analytics !== true) {
      // Essential preference fallback if localStorage cannot persist the withdrawal.
      document.cookie = `${optOut}=1; Max-Age=31536000; Path=${path}; SameSite=Lax`;
    } else if (choice.version === 1 && typeof choice.marketing === 'boolean') {
      document.cookie = `${optOut}=; Max-Age=0; Path=${path}; SameSite=Lax`;
    }
  };
  const cleanURL = value => {
    try { const url = new URL(value); return url.origin + url.pathname; } catch (_) { return ''; }
  };
  const clearCookies = () => {
    document.cookie.split(';').map(part => part.trim().split('=')[0]).filter(name => name.startsWith(prefix + '_')).forEach(name => {
      document.cookie = `${name}=; Max-Age=0; Path=${path}; SameSite=Lax`;
      document.cookie = `${name}=; Max-Age=0; Path=${path}; Domain=${location.hostname}; SameSite=Lax`;
    });
  };
  const apply = () => {
    window[disabled] = !allowed();
    if (!allowed()) {
      clearCookies();
      if (started) {
        // Removing a script cannot unload its listeners. Disable sends first,
        // then discard the runtime; the saved denial prevents the next load.
        window.dataLayer = [];
        location.reload();
      }
      return;
    }
    if (started) return;
    started = true;
    window.dataLayer = window.dataLayer || [];
    const tag = function () { window.dataLayer.push(arguments); };
    tag('consent', 'default', {analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied'});
    tag('consent', 'update', {analytics_storage: 'granted', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied'});
    tag('js', new Date());
    tag('config', id, {send_page_view: false, allow_google_signals: false, allow_ad_personalization_signals: false,
      cookie_prefix: prefix, cookie_path: path, cookie_domain: location.hostname,
      page_location: cleanURL(location.href), page_referrer: cleanURL(document.referrer)});
    tag('event', 'page_view', {send_to: id, page_location: cleanURL(location.href), page_referrer: cleanURL(document.referrer)});
    const script = document.createElement('script');
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
    script.async = true;
    script.referrerPolicy = 'no-referrer';
    document.head.append(script);
    flush();
    const item = readItem();
    if (item) fastshopTrack('view_item', {currency: item.currency, value: item.price, items: [item]});
  };
  const queue = [];
  const readItem = () => {
    try {
      const el = document.getElementById('h-ga4-item');
      return el ? JSON.parse(el.textContent) : null;
    } catch (_) { return null; }
  };
  const fastshopTrack = (name, params) => {
    if (!name) return;
    if (started && allowed()) window.dataLayer.push(['event', name, Object.assign({send_to: id}, params || {})]);
    else queue.push([name, params]);
  };
  const flush = () => { while (started && allowed() && queue.length) { const [n, p] = queue.shift(); fastshopTrack(n, p); } };
  window.fastshopTrack = fastshopTrack;
  // Ecommerce events fire only on storefront pages (consent-gated). Checkout and
  // account pages deliberately load no analytics, so no cart/purchase events there.
  document.addEventListener('submit', event => {
    const form = event.target;
    if (form && form.matches && form.matches('form[action$="/cart/add"]')) {
      const item = readItem();
      if (item) fastshopTrack('add_to_cart', {currency: item.currency, value: item.price, items: [item]});
    }
  }, true);
  window.addEventListener('fastshop:consent', event => { consent = event.detail; rememberWithdrawal(consent); apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    try { consent = JSON.parse(event.newValue); } catch (_) { consent = null; }
    rememberWithdrawal(consent);
    apply();
  });
  apply();
})();

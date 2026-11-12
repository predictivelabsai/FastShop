(() => {
  'use strict';
  const configNode = document.querySelector('#fastshop-tracking-config');
  if (!configNode) return;
  let config;
  try { config = JSON.parse(configNode.textContent); } catch (_) { return; }
  const site = config.site_id;
  const ids = config.providers || {};
  const event = config.event || {};
  const key = `fastshop-consent:${site}`;
  const loaded = new Set();
  const sent = new Set();
  let googleStarted = false;
  let consent = window.fastshopConsent;
  const valid = value => value && value.version === 1
    && typeof value.analytics === 'boolean' && typeof value.marketing === 'boolean';
  const readConsent = () => {
    try {
      const value = JSON.parse(localStorage.getItem(key));
      return valid(value) ? value : null;
    } catch (_) { return null; }
  };
  if (!valid(consent)) consent = readConsent();
  const deniedByPrivacySignal = () => navigator.globalPrivacyControl === true;
  const permitted = category => valid(consent) && consent[category] === true && !deniedByPrivacySignal();
  const copyNonce = script => {
    const source = document.currentScript || document.querySelector('script[nonce]');
    if (source && source.nonce) script.nonce = source.nonce;
  };
  const loadScript = (src, id) => {
    if (id && document.getElementById(id)) return;
    const script = document.createElement('script');
    if (id) script.id = id;
    script.async = true;
    script.referrerPolicy = 'no-referrer';
    script.src = src;
    copyNonce(script);
    document.head.append(script);
  };
  const sentKey = (provider, name, transaction) =>
    `${provider}:${name}:${transaction || location.pathname}`;
  const alreadyPurchased = provider => {
    const transaction = String(event.transaction_id || '');
    if (!transaction) return false;
    const storageKey = `fastshop-purchase:${site}:${provider}:${encodeURIComponent(transaction)}`;
    try {
      if (localStorage.getItem(storageKey)) return true;
      localStorage.setItem(storageKey, '1');
    } catch (_) {
      if (sent.has(storageKey)) return true;
      sent.add(storageKey);
    }
    return false;
  };
  const payload = name => ({
    value: Number(event.value) || 0,
    currency: String(event.currency || 'USD').toUpperCase(),
    items: Array.isArray(event.items) ? event.items : [],
    ...(event.transaction_id ? {transaction_id: String(event.transaction_id)} : {}),
  });
  const metaPayload = name => {
    const data = payload(name);
    return {
      value: data.value,
      currency: data.currency,
      content_type: 'product',
      content_ids: data.items.map(item => String(item.item_id || '')).filter(Boolean),
      contents: data.items.map(item => ({
        id: String(item.item_id || ''),
        quantity: Number(item.quantity) || 1,
        item_price: Number(item.price) || 0,
      })),
      ...(data.transaction_id ? {order_id: data.transaction_id} : {}),
    };
  };
  const tiktokPayload = name => {
    const data = payload(name);
    return {
      value: data.value,
      currency: data.currency,
      contents: data.items.map(item => ({
        content_id: String(item.item_id || ''),
        content_type: 'product',
        content_name: String(item.item_name || ''),
        quantity: Number(item.quantity) || 1,
        price: Number(item.price) || 0,
      })),
      ...(data.transaction_id ? {order_id: data.transaction_id} : {}),
    };
  };
  const send = (provider, name, params, options = {}) => {
    const transaction = name === 'purchase' ? event.transaction_id : '';
    const marker = sentKey(provider, name, transaction);
    if (options.initial && sent.has(marker)) return;
    if (name === 'purchase' && alreadyPurchased(provider)) return;
    if (options.initial) sent.add(marker);
    if (provider === 'ga4') {
      window.gtag('event', name, {...params, send_to: ids.ga4_measurement_id});
    } else if (provider === 'ads') {
      if (name === 'purchase' && ids.google_ads_purchase_label) {
        window.gtag('event', 'conversion', {
          send_to: `${ids.google_ads_conversion_id}/${ids.google_ads_purchase_label}`,
          value: params.value,
          currency: params.currency,
          transaction_id: String(event.transaction_id || ''),
        });
      }
    } else if (provider === 'meta') {
      const names = {view_item: 'ViewContent', add_to_cart: 'AddToCart',
        begin_checkout: 'InitiateCheckout', purchase: 'Purchase'};
      window.fbq('track', names[name], metaPayload(name));
    } else if (provider === 'tiktok') {
      const names = {view_item: 'ViewContent', add_to_cart: 'AddToCart',
        begin_checkout: 'InitiateCheckout', purchase: 'CompletePayment'};
      window.ttq.track(names[name], tiktokPayload(name));
    }
  };
  const sendInitialEvent = provider => {
    if (!event.name || !['view_item', 'begin_checkout', 'purchase'].includes(event.name)) return;
    const name = event.name;
    const params = payload(name);
    if (provider === 'ga4' || provider === 'ads') send(provider, name, params, {initial: true});
    else send(provider, name, params, {initial: true});
  };
  const startGoogle = () => {
    const analytics = Boolean(ids.ga4_measurement_id && permitted('analytics'));
    const marketing = Boolean(ids.google_ads_conversion_id && permitted('marketing'));
    if (!analytics && !marketing) return;
    window.dataLayer = window.dataLayer || [];
    window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
    if (!googleStarted) {
      googleStarted = true;
      window.gtag('consent', 'default', {
        analytics_storage: 'denied', ad_storage: 'denied',
        ad_user_data: 'denied', ad_personalization: 'denied',
      });
      window.gtag('js', new Date());
      const id = analytics ? ids.ga4_measurement_id : ids.google_ads_conversion_id;
      loadScript(`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`, 'fastshop-google-tag');
    }
    window.gtag('consent', 'update', {
      analytics_storage: analytics ? 'granted' : 'denied',
      ad_storage: marketing ? 'granted' : 'denied',
      ad_user_data: marketing ? 'granted' : 'denied',
      ad_personalization: marketing ? 'granted' : 'denied',
    });
    if (analytics && !loaded.has('ga4')) {
      loaded.add('ga4');
      window.gtag('config', ids.ga4_measurement_id, {
        send_page_view: false, allow_google_signals: false,
        allow_ad_personalization_signals: false,
      });
      send('ga4', 'page_view', {
        page_location: location.origin + location.pathname,
      }, {initial: true});
      if (event.name && event.name !== 'page_view') sendInitialEvent('ga4');
    }
    if (marketing && !loaded.has('ads')) {
      loaded.add('ads');
      window.gtag('config', ids.google_ads_conversion_id, {send_page_view: false});
      sendInitialEvent('ads');
    }
  };
  const startMeta = () => {
    if (!ids.meta_pixel_id || !permitted('marketing') || loaded.has('meta')) return;
    loaded.add('meta');
    window.fbq = window.fbq || function () {
      window.fbq.callMethod ? window.fbq.callMethod.apply(window.fbq, arguments)
        : window.fbq.queue.push(arguments);
    };
    window.fbq.queue = window.fbq.queue || [];
    window._fbq = window.fbq;
    window.fbq.push = window.fbq;
    window.fbq.loaded = true;
    window.fbq.version = '2.0';
    window.fbq('consent', 'grant');
    window.fbq('init', ids.meta_pixel_id);
    const names = {view_item: 'ViewContent', add_to_cart: 'AddToCart',
      begin_checkout: 'InitiateCheckout', purchase: 'Purchase'};
    window.fbq('track', 'PageView');
    if (names[event.name]) sendInitialEvent('meta');
    loadScript('https://connect.facebook.net/en_US/fbevents.js', 'fastshop-meta-pixel');
  };
  const startTikTok = () => {
    if (!ids.tiktok_pixel_id || !permitted('marketing') || loaded.has('tiktok')) return;
    loaded.add('tiktok');
    window.TiktokAnalyticsObject = 'ttq';
    window.ttq = window.ttq || [];
    window.ttq.methods = ['page', 'track', 'identify', 'instances', 'debug', 'on', 'off', 'once',
      'ready', 'alias', 'group', 'enableCookie', 'disableCookie', 'holdConsent', 'revokeConsent', 'grantConsent'];
    window.ttq.setAndDefer = function (target, method) {
      target[method] = function () { target.push([method, ...arguments]); };
    };
    window.ttq.methods.forEach(method => window.ttq.setAndDefer(window.ttq, method));
    window.ttq.load = pixelId => loadScript(
      `https://analytics.tiktok.com/i18n/pixel/events.js?sdkid=${encodeURIComponent(pixelId)}&lib=ttq`,
      'fastshop-tiktok-pixel',
    );
    window.ttq.grantConsent();
    window.ttq.load(ids.tiktok_pixel_id);
    window.ttq.page();
    if (['view_item', 'begin_checkout', 'purchase'].includes(event.name)) sendInitialEvent('tiktok');
  };
  const createCheckoutConsent = () => {
    if (document.querySelector('#h-cookie, #fastshop-tracking-consent')) return;
    const panel = document.createElement('aside');
    panel.id = 'fastshop-tracking-consent';
    panel.setAttribute('aria-label', 'Cookie preferences');
    panel.style.cssText = 'position:fixed;bottom:16px;left:16px;right:16px;max-width:440px;background:#fff;color:#182a21;padding:20px;border:1px solid #cbd4cf;border-radius:12px;z-index:1000;box-shadow:0 4px 30px #0002;font:14px system-ui';
    const heading = document.createElement('strong');
    heading.textContent = 'Your privacy, your choice.';
    const body = document.createElement('p');
    body.textContent = 'Preferences, analytics and marketing stay off unless you choose them.';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Cookie categories';
    details.append(summary);
    for (const [category, label] of [
      ['preferences', 'Preferences'], ['analytics', 'Analytics'], ['marketing', 'Marketing'],
    ]) {
      const labelNode = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.id = `tracking-consent-${category}`;
      input.dataset.category = category;
      labelNode.append(input, document.createTextNode(` ${label}`));
      details.append(labelNode, document.createElement('br'));
    }
    const actions = document.createElement('div');
    actions.style.marginTop = '12px';
    for (const [choice, label] of [['all', 'Accept all'], ['none', 'Decline'], ['custom', 'Save preferences']]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.choice = choice;
      button.textContent = label;
      actions.append(button, document.createTextNode(' '));
    }
    panel.append(heading, body, details, actions);
    const open = document.createElement('button');
    open.type = 'button';
    open.textContent = 'Cookie preferences';
    open.setAttribute('aria-label', 'Cookie preferences');
    open.style.cssText = 'position:fixed;bottom:12px;right:12px;z-index:999;font:12px system-ui;padding:6px';
    const sync = () => {
      panel.hidden = valid(consent);
      panel.querySelectorAll('[data-category]').forEach(input => {
        input.checked = consent?.[input.dataset.category] === true;
      });
    };
    sync();
    open.addEventListener('click', () => { sync(); panel.hidden = false; });
    panel.querySelectorAll('[data-choice]').forEach(button => button.addEventListener('click', () => {
      const choice = button.dataset.choice;
      consent = {
        version: 1, essential: true,
        preferences: choice === 'all' || (choice === 'custom' && panel.querySelector('#tracking-consent-preferences').checked),
        analytics: choice === 'all' || (choice === 'custom' && panel.querySelector('#tracking-consent-analytics').checked),
        marketing: choice === 'all' || (choice === 'custom' && panel.querySelector('#tracking-consent-marketing').checked),
      };
      try { localStorage.setItem(key, JSON.stringify(consent)); } catch (_) { /* Consent remains in memory. */ }
      window.fastshopConsent = consent;
      sync();
      window.dispatchEvent(new CustomEvent('fastshop:consent', {detail: consent}));
    }));
    document.body.append(panel, open);
  };
  const apply = () => {
    if ((loaded.has('ga4') && !permitted('analytics'))
        || (loaded.has('ads') && !permitted('marketing'))
        || ((loaded.has('meta') || loaded.has('tiktok')) && !permitted('marketing'))) {
      if (googleStarted) window.gtag('consent', 'update', {
        analytics_storage: permitted('analytics') ? 'granted' : 'denied',
        ad_storage: permitted('marketing') ? 'granted' : 'denied',
        ad_user_data: permitted('marketing') ? 'granted' : 'denied',
        ad_personalization: permitted('marketing') ? 'granted' : 'denied',
      });
      if (loaded.has('meta')) window.fbq('consent', 'revoke');
      if (loaded.has('tiktok')) window.ttq.revokeConsent();
      location.reload();
      return;
    }
    startGoogle();
    startMeta();
    startTikTok();
  };
  const initialConsent = readConsent();
  if (initialConsent) consent = initialConsent;
  window.addEventListener('fastshop:consent', event => {
    consent = event.detail;
    apply();
  });
  window.addEventListener('storage', storageEvent => {
    if (storageEvent.key !== key && storageEvent.key !== null) return;
    consent = readConsent();
    apply();
  });
  document.addEventListener('submit', submitEvent => {
    const form = submitEvent.target;
    if (!form?.matches?.('form[action$="/cart/add"]') || !event.items?.length) return;
    const quantity = Math.max(1, Number(form.elements.quantity?.value) || 1);
    const selectedVariant = String(form.elements.variant_id?.value || '');
    const selectedItem = event.variants?.[selectedVariant] || event.items[0];
    const items = [{...selectedItem, quantity}];
    const value = items.reduce((total, item) => total + (Number(item.price) || 0) * quantity, 0);
    const params = {currency: String(items[0].currency || event.currency || 'USD').toUpperCase(), value, items};
    if (permitted('analytics') && loaded.has('ga4')) send('ga4', 'add_to_cart', params);
    if (permitted('marketing') && loaded.has('ads')) send('ads', 'add_to_cart', params);
    if (permitted('marketing') && loaded.has('meta')) send('meta', 'add_to_cart', params);
    if (permitted('marketing') && loaded.has('tiktok')) send('tiktok', 'add_to_cart', params);
  }, true);
  if (!document.querySelector('#h-cookie')) createCheckoutConsent();
  apply();
})();

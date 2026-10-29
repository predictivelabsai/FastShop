(() => {
  const page = document.querySelector('[data-checkout-root]');
  const form = document.querySelector('[data-checkout-form]');
  if (!page || !form) return;

  const errors = document.querySelector('#checkout-errors');
  const rateBox = document.querySelector('#checkout-shipping-rates');
  const payButton = document.querySelector('[data-checkout-pay]');
  const csrf = page.dataset.csrf;
  let quote = null;
  let quoteKey = '';
  let stripeCheckout = null;
  let paymentAttemptId = '';
  let paymentSession = null;
  let stripePromise = null;
  let timer;
  let busy = false;

  const value = (name) => form.elements[name]?.value.trim() || '';
  const checked = (name) => Boolean(form.elements[name]?.checked);
  const dollars = (minor, currency = 'USD') => new Intl.NumberFormat('en-US', {
    style: 'currency', currency: currency.toUpperCase()
  }).format(minor / 100);
  const showError = (message, field) => {
    errors.hidden = !message;
    errors.textContent = message || '';
    form.querySelectorAll('[aria-invalid="true"]').forEach((node) => node.removeAttribute('aria-invalid'));
    if (field) {
      const control = form.elements[field];
      if (control) {
        control.setAttribute('aria-invalid', 'true');
        control.focus();
      }
    }
  };
  const payload = () => {
    const first = value('first_name');
    const last = value('last_name');
    const fullName = `${first} ${last}`.trim();
    form.elements.full_name.value = fullName;
    return {
      csrf_token: csrf, email: value('email'), full_name: fullName,
      line1: value('line1'), line2: value('line2'), city: value('city'),
      state: value('state'), postal_code: value('postal_code'), country: 'US',
      code: value('promo_type') === 'discount' ? value('checkout_code') : '',
      gift_card_code: value('promo_type') === 'gift_card' ? value('checkout_code') : '',
      shipping_rate_id: value('shipping_rate_id'),
      subscription_consent: checked('subscription_consent')
    };
  };
  const ready = (data) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email) && data.full_name &&
    data.line1 && data.city && data.state && /^\d{5}(?:-\d{4})?$/.test(data.postal_code);

  function renderLines(target, lines, currency) {
    target.replaceChildren();
    lines.forEach((line) => {
      const row = document.createElement('div');
      row.className = 'checkout-line';
      const thumbWrap = document.createElement('div');
      thumbWrap.className = 'checkout-thumb-wrap';
      const thumb = document.createElement(line.image_url ? 'img' : 'div');
      thumb.className = `checkout-thumb${line.image_url ? '' : ' checkout-thumb-empty'}`;
      if (line.image_url) { thumb.src = line.image_url; thumb.alt = ''; }
      const quantity = document.createElement('span');
      quantity.className = 'checkout-quantity';
      quantity.textContent = line.quantity;
      thumbWrap.append(thumb, quantity);
      const copy = document.createElement('div');
      copy.className = 'checkout-line-copy';
      const name = document.createElement('h3');
      name.textContent = line.variant ? line.name.replace(new RegExp(`(?: · | — )${line.variant.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&')}$`), '') : line.name;
      const detail = document.createElement('p');
      detail.textContent = line.variant || (line.subscription ? page.dataset.subscriptionLine : '');
      copy.append(name, detail);
      const price = document.createElement('strong');
      price.textContent = dollars(line.amount, currency);
      row.append(thumbWrap, copy, price);
      target.append(row);
    });
  }

  async function loadStripe(key) {
    if (!stripePromise) {
      stripePromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://js.stripe.com/basil/stripe.js';
        script.onload = () => resolve();
        script.onerror = () => reject(new Error(page.dataset.paymentLoadError));
        document.head.append(script);
      });
    }
    await stripePromise;
    if (!window.Stripe) throw new Error(page.dataset.paymentLoadError);
    return window.Stripe(key);
  }

  async function createPayment(attemptId) {
    if (attemptId === paymentAttemptId && paymentSession) return paymentSession;
    const response = await fetch(page.dataset.sessionUrlTemplate.replace('{attempt_id}', encodeURIComponent(attemptId)), {
      method: 'POST', headers: { 'X-CSRF-Token': csrf }
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || page.dataset.paymentPrepareError);
    if (data.fallback_url) {
      paymentAttemptId = attemptId;
      paymentSession = data;
      return data;
    }
    const stripe = await loadStripe(data.publishable_key);
    // The basil Stripe.js release takes fetchClientSecret and resolves to the checkout object.
    const initialized = await stripe.initCheckout({ fetchClientSecret: async () => data.client_secret });
    if (stripeCheckout?.destroy) stripeCheckout.destroy();
    stripeCheckout = initialized;
    const element = initialized.createPaymentElement();
    document.querySelector('#payment-element').replaceChildren();
    element.mount('#payment-element');
    paymentAttemptId = attemptId;
    paymentSession = data;
    return data;
  }

  async function requestQuote(force = false) {
    const data = payload();
    if (!ready(data) || !checked('subscription_consent') && form.elements.subscription_consent?.required) return null;
    const key = JSON.stringify(data);
    if (!force && key === quoteKey && quote) return quote;
    payButton.disabled = true;
    try {
      const response = await fetch(page.dataset.quoteUrl, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify(data)
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || page.dataset.quoteError);
      quote = result;
      quoteKey = key;
      renderLines(document.querySelector('#checkout-lines'), result.lines, result.currency);
      renderLines(document.querySelector('#checkout-mobile-lines'), result.lines, result.currency);
      const subtotal = document.querySelector('[data-summary-subtotal] strong');
      subtotal.textContent = dollars(result.subtotal, result.currency);
      document.querySelector('[data-summary-shipping]').textContent = dollars(result.shipping, result.currency);
      document.querySelector('[data-summary-tax]').textContent = dollars(result.tax, result.currency);
      document.querySelector('[data-summary-tax-row]').hidden = false;
      document.querySelector('[data-summary-total]').textContent = `USD ${dollars(result.total, result.currency)}`;
      document.querySelector('.checkout-summary-toggle strong').textContent = `USD ${dollars(result.total, result.currency)}`;
      const rates = result.shipping_rates || [];
      rateBox.replaceChildren();
      rateBox.classList.toggle('checkout-shipping-prompt', !rates.length);
      rateBox.classList.toggle('checkout-rates', rates.length > 0);
      if (!rates.length) {
        rateBox.textContent = rateBox.dataset.noRates;
        payButton.disabled = false;
        await createPayment(result.attempt_id);
      } else {
        rates.forEach((rate, index) => {
          const label = document.createElement('label');
          label.className = 'checkout-rate';
          const radio = document.createElement('input');
          radio.type = 'radio'; radio.name = 'shipping_rate_id'; radio.value = rate.id;
          radio.checked = rate.id === data.shipping_rate_id || (!data.shipping_rate_id && index === 0);
          const text = document.createElement('span'); text.textContent = rate.name;
          const amount = document.createElement('strong'); amount.textContent = dollars(rate.amount, result.currency);
          label.append(radio, text, amount); rateBox.append(label);
        });
        const selected = rateBox.querySelector('input:checked');
        if (selected && selected.value !== data.shipping_rate_id) {
          selected.checked = true;
          quote = null;
          quoteKey = '';
          return await requestQuote(true);
        } else {
          payButton.disabled = false;
          await createPayment(result.attempt_id);
        }
      }
      return result;
    } catch (error) {
      showError(error.message);
      payButton.disabled = false;
      return null;
    }
  }

  function scheduleQuote() {
    quote = null;
    quoteKey = '';
    clearTimeout(timer);
    timer = setTimeout(requestQuote, 450);
  }

  form.addEventListener('input', (event) => {
    if (['email', 'first_name', 'last_name', 'line1', 'line2', 'city', 'state', 'postal_code', 'code', 'gift_card_code', 'checkout_code'].includes(event.target.name)) scheduleQuote();
  });
  form.addEventListener('change', (event) => {
    if (event.target.name === 'shipping_rate_id' || event.target.name === 'promo_type') scheduleQuote();
    if (event.target.name === 'subscription_consent') scheduleQuote();
  });
  document.querySelector('[data-checkout-apply]').addEventListener('click', scheduleQuote);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    showError('');
    const data = payload();
    if (!ready(data)) {
      const missing = !data.email ? 'email' : !form.elements.first_name.value ? 'first_name' : !form.elements.last_name.value ? 'last_name' : !data.line1 ? 'line1' : !data.city ? 'city' : !data.state ? 'state' : 'postal_code';
      showError(page.dataset.detailError, missing);
      return;
    }
    if (form.elements.subscription_consent?.required && !checked('subscription_consent')) {
      showError(page.dataset.subscriptionError, 'subscription_consent');
      return;
    }
    busy = true; payButton.disabled = true;
    try {
      const fresh = await requestQuote(true);
      if (!fresh) return;
      const payment = await createPayment(fresh.attempt_id);
      if (payment.fallback_url) {
        const fallback = document.createElement('form');
        fallback.method = 'post'; fallback.action = payment.fallback_url;
        const token = document.createElement('input'); token.type = 'hidden'; token.name = 'csrf_token'; token.value = csrf;
        fallback.append(token); document.body.append(fallback); fallback.submit(); return;
      }
      const result = await stripeCheckout.confirm();
      if (result?.type === 'error' || result?.error) throw new Error(result.error?.message || page.dataset.paymentConfirmError);
    } catch (error) {
      showError(error.message);
    } finally {
      busy = false; payButton.disabled = false;
    }
  });
})();

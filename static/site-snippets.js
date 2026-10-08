/* Merchant scripts remain inert until the matching stored consent is valid. */
(() => {
  const root = document.querySelector('.h-site');
  const templates = Array.from(document.querySelectorAll('template[data-fastshop-snippet]'));
  if (!root || !templates.length) return;
  const key = `fastshop-consent:${root.dataset.site}`;
  let consent = window.fastshopConsent;
  const allowed = category => consent && consent.version === 1
    && typeof consent.analytics === 'boolean' && typeof consent.marketing === 'boolean'
    && consent[category] === true && navigator.globalPrivacyControl !== true;
  const decode = value => {
    const bytes = Uint8Array.from(atob(value), character => character.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  };
  const activate = template => {
    if (template.dataset.loaded === 'true') return;
    const content = document.createElement('template');
    content.innerHTML = decode(template.dataset.content || '');
    content.content.querySelectorAll('script').forEach(oldScript => {
      const script = document.createElement('script');
      Array.from(oldScript.attributes).forEach(attribute => script.setAttribute(attribute.name, attribute.value));
      script.textContent = oldScript.textContent;
      oldScript.replaceWith(script);
    });
    template.parentNode.insertBefore(content.content, template);
    template.dataset.loaded = 'true';
  };
  const apply = () => {
    let withdrewLoadedSnippet = false;
    templates.forEach(template => {
      const permitted = allowed(template.dataset.consent);
      if (permitted) activate(template);
      else if (template.dataset.loaded === 'true') withdrewLoadedSnippet = true;
    });
    if (withdrewLoadedSnippet) location.reload();
  };
  window.addEventListener('fastshop:consent', event => { consent = event.detail; apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    try { consent = JSON.parse(event.newValue); } catch (_) { consent = null; }
    apply();
  });
  apply();
})();

/* Verification secrets stay in the fragment, never the HTTP URL or access log. */
(() => {
  const field = document.querySelector('[data-email-token]');
  if (!field) return;
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  if (token && /^[a-f0-9]{64}$/.test(token)) field.value = token;
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
})();

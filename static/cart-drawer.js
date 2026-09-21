/* Progressive enhancement: all prices, mutations and discounts remain server-owned. */
(() => {
  const link = document.querySelector('[data-cart-open]');
  if (!link || !window.HTMLDialogElement) return;
  const cart = new URL(link.href, location.href);
  if (cart.origin !== location.origin) return;
  const dialog = document.createElement('dialog');
  dialog.className = 'fs-cart-dialog';
  dialog.setAttribute('aria-label', 'Your FastShop bag');
  const header = document.createElement('header');
  const title = document.createElement('span');
  title.textContent = 'Your bag · FastShop';
  const close = document.createElement('button');
  close.type = 'button'; close.textContent = 'Close bag';
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  const frame = document.createElement('iframe');
  frame.title = 'Your bag, quantities and discount code';
  header.append(title, close); dialog.append(header, status, frame);
  document.body.append(dialog);
  let opener = link;
  let busy = false;
  let notice = '';
  let countTask = Promise.resolve();
  const updateCount = async () => {
    try {
      const result = await fetch(cart.pathname + '/summary', {credentials: 'same-origin', cache: 'no-store'});
      if (!result.ok) return;
      const data = await result.json();
      if (Number.isInteger(data.count) && data.count >= 0) {
        link.textContent = `Bag (${data.count})`;
        link.setAttribute('aria-label', `Open bag, ${data.count} items`);
      }
    } catch (_) { /* Keep the working normal cart link. */ }
  };
  const refreshCount = () => { countTask = updateCount(); return countTask; };
  const open = (message = '') => {
    notice = message;
    opener = document.activeElement;
    status.textContent = 'Loading your bag…';
    frame.src = cart.href;
    if (!dialog.open) dialog.showModal();
    close.focus();
  };
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { if (opener && opener.isConnected) opener.focus(); });
  frame.addEventListener('load', () => {
    if (!dialog.open) return;
    try {
      const doc = frame.contentDocument;
      if (!doc) return;
      doc.querySelectorAll('a').forEach(a => { a.target = '_top'; });
      // Escape from inside the embedded document must close the outer dialog too.
      doc.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); dialog.close(); } });
      status.textContent = notice;
      refreshCount();
    } catch (_) { status.textContent = 'Open the full bag page if this view is unavailable.'; }
  });
  link.addEventListener('click', async event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); await countTask; open();
  });
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || new URL(form.action).href !== cart.href + '/add') return;
    event.preventDefault();
    if (busy) return;
    busy = true;
    const button = event.submitter;
    if (button) button.disabled = true;
    try {
      await countTask;
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin'});
      if (!response.ok) throw new Error('rejected');
      open();
      opener = button || form;
    } catch (_) {
      open('We could not confirm that update. Check your bag before adding again.');
      opener = button || form;
    } finally {
      busy = false;
      if (button) button.disabled = false;
    }
  });
  window.addEventListener('pageshow', refreshCount);
})();

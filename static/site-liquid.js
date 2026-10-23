/* Liquid Metal progressive enhancement. Prices and mutations remain server-owned. */
(() => {
  'use strict';
  const root = document.querySelector('.h-site[data-pack="liquid"]');
  if (!root) return;
  // Native forms remain usable without JavaScript, including inside the drawer.
  let purchaseBusy = false;
  root.addEventListener('change', event => {
    const select = event.target.closest('.liquid-cart-purchase select');
    if (select) select.form.requestSubmit();
  });
  root.addEventListener('submit', async event => {
    const form = event.target.closest('.liquid-cart-purchase');
    if (!form) return;
    event.preventDefault();
    if (purchaseBusy) return;
    purchaseBusy = true;
    const cart = form.closest('.liquid-cart-page');
    const data = new FormData(form);
    const controls = [...cart.querySelectorAll('button,input,select')].filter(el => !el.disabled);
    cart.setAttribute('aria-busy', 'true');
    controls.forEach(el => { el.disabled = true; });
    try {
      const response = await fetch(form.action, {method: 'POST', body: data, credentials: 'same-origin'});
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (!response.ok) throw new Error(doc.querySelector('.liquid-commerce-content p')?.textContent || 'We could not update your bag. Reload before trying again.');
      const next = doc.querySelector('.liquid-cart-page');
      if (!next) throw new Error('We could not confirm that update. Reload your bag before trying again.');
      // Keep the shipping region in the document while replacing the other contents.
      const shipping = cart.querySelector('.cart-shipping');
      const nextShipping = next.querySelector('.cart-shipping');
      if (shipping && nextShipping) {
        const live = shipping.querySelector('[aria-live]');
        const message = nextShipping.querySelector('[aria-live]').textContent;
        shipping.querySelector('[role="progressbar"]').replaceWith(nextShipping.querySelector('[role="progressbar"]'));
        [...cart.children].forEach(el => { if (el !== shipping) el.remove(); });
        [...next.children].forEach(el => { if (el !== nextShipping) cart.append(el); });
        live.textContent = message;
      } else {
        cart.replaceChildren(...next.childNodes);
      }
      const updated = [...cart.querySelectorAll('.liquid-cart-purchase')].find(el =>
        el.elements.variant_id.value === data.get('variant_id') &&
        el.elements.subscription.value === data.get('target_subscription'));
      updated?.querySelector('select').focus({preventScroll: true});
    } catch (error) {
      form.elements.target_subscription.value = data.get('subscription');
      let status = cart.querySelector('[data-purchase-status]');
      if (!status) {
        status = document.createElement('p'); status.dataset.purchaseStatus = '';
        status.setAttribute('role', 'alert'); cart.prepend(status);
      }
      status.textContent = error.message;
    } finally {
      cart.removeAttribute('aria-busy');
      controls.forEach(el => { el.disabled = false; });
      purchaseBusy = false;
    }
  });
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const bars = root.querySelector('.metal-top-bars');
  const offer = root.querySelector('[data-liquid-offer]');
  const offerToggle = root.querySelector('[data-liquid-offer-toggle]');
  const offerForm = root.querySelector('#liquid-offer-form');
  const storageKey = 'h24you-liquid-offer-dismissed:' + root.dataset.site;
  const measure = () => {
    root.style.setProperty('--metal-top-height', bars.offsetHeight + 'px');
    const categories = root.querySelector('.faq-category-nav');
    const clearance = bars.offsetHeight + (categories ? categories.offsetHeight : 0) + 20;
    root.style.setProperty('--metal-anchor-offset', clearance + 'px');
    document.documentElement.style.scrollPaddingTop = clearance + 'px';
  };
  try { offer.hidden = localStorage.getItem(storageKey) === 'true'; } catch (_) { /* Storage is optional. */ }
  root.querySelector('[data-liquid-dismiss]').addEventListener('click', () => {
    offer.hidden = true;
    try { localStorage.setItem(storageKey, 'true'); } catch (_) { /* This visit still works. */ }
    measure();
  });
  offerToggle.addEventListener('click', () => {
    offerForm.hidden = !offerForm.hidden;
    offerToggle.setAttribute('aria-expanded', String(!offerForm.hidden));
    offerToggle.textContent = offerForm.hidden ? 'Request your code' : 'Close';
    if (!offerForm.hidden) offerForm.querySelector('input[type="email"]').focus();
  });
  const onScroll = () => bars.classList.toggle('is-scrolled', scrollY > 24);
  window.addEventListener('scroll', onScroll, {passive: true});
  new ResizeObserver(measure).observe(bars);
  const categoryStrip = root.querySelector('.faq-category-nav');
  if (categoryStrip) new ResizeObserver(measure).observe(categoryStrip);
  onScroll(); measure();

  const menu = root.querySelector('#liquid-mobile-menu');
  const menuButton = root.querySelector('[data-liquid-menu]');
  const main = root.querySelector('main');
  const footer = root.querySelector('footer');
  const closeMenu = () => {
    menu.hidden = true;
    menuButton.setAttribute('aria-expanded', 'false');
    main.inert = footer.inert = bars.inert = false;
    document.body.style.overflow = '';
    menuButton.focus();
  };
  menuButton.addEventListener('click', () => {
    menu.hidden = false;
    menuButton.setAttribute('aria-expanded', 'true');
    main.inert = footer.inert = bars.inert = true;
    document.body.style.overflow = 'hidden';
    menu.querySelector('[data-liquid-menu-close]').focus();
  });
  menu.querySelector('[data-liquid-menu-close]').addEventListener('click', closeMenu);
  menu.addEventListener('click', event => { if (event.target.closest('a')) closeMenu(); });
  menu.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const targets = [...menu.querySelectorAll('a,button,summary')].filter(el => el.getClientRects().length);
    const first = targets[0], last = targets.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (!menu.hidden) closeMenu();
    root.querySelectorAll('.liquid-dropdown[open]').forEach(el => { el.open = false; el.querySelector('summary').focus(); });
  });
  const mobileQuery = matchMedia('(max-width: 767px)');
  mobileQuery.addEventListener('change', () => { if (!mobileQuery.matches && !menu.hidden) closeMenu(); });
  root.querySelectorAll('.liquid-dropdown').forEach(dropdown => {
    dropdown.addEventListener('mouseenter', () => { if (matchMedia('(hover:hover)').matches) dropdown.open = true; });
    dropdown.addEventListener('mouseleave', () => { if (matchMedia('(hover:hover)').matches) dropdown.open = false; });
    document.addEventListener('click', event => { if (!dropdown.contains(event.target)) dropdown.open = false; });
  });

  const motion = root.querySelector('[data-liquid-motion]');
  if (motion) {
    const hero = motion.closest('.metal-hero');
    let paused = reduced.matches;
    const syncMotion = () => {
      const stopped = paused || reduced.matches;
      hero.classList.toggle('motion-paused', stopped);
      motion.setAttribute('aria-pressed', String(stopped));
      motion.setAttribute('aria-label', stopped ? 'Play background motion' : 'Pause background motion');
      motion.textContent = stopped ? 'Play' : 'Pause';
    };
    motion.addEventListener('click', () => { paused = !paused; syncMotion(); });
    reduced.addEventListener('change', syncMotion);
    syncMotion();
  }

  if (!reduced.matches && 'IntersectionObserver' in window) {
    const counters = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        counters.unobserve(entry.target);
        const element = entry.target, final = element.dataset.liquidCount;
        const target = Number(final.replace(/[^0-9]/g, ''));
        const start = performance.now();
        element.setAttribute('aria-label', final);
        const tick = now => {
          const progress = reduced.matches ? 1 : Math.min(1, (now - start) / 1800);
          element.textContent = Math.round(target * (1 - (1 - progress) ** 3)).toLocaleString('en-US') + '+';
          if (progress < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
    }, {threshold: 0.4});
    root.querySelectorAll('[data-liquid-count]').forEach(element => counters.observe(element));
  }

  root.querySelectorAll('[data-liquid-carousel]').forEach(viewport => {
    const track = viewport.firstElementChild;
    const section = viewport.closest('section');
    const originals = [...track.children];
    const count = originals.length;
    if (count < 2) return;
    // Extra turns keep native touch/scroll-snap continuous in both directions.
    const clone = node => {
      const copy = node.cloneNode(true);
      copy.setAttribute('aria-hidden', 'true'); copy.inert = true;
      copy.removeAttribute('aria-label'); copy.dataset.carouselClone = '';
      return copy;
    };
    const before = document.createDocumentFragment(), after = document.createDocumentFragment();
    originals.forEach(node => { before.append(clone(node)); after.append(clone(node)); });
    track.prepend(before); track.append(after);
    const slides = [...track.children];
    const dots = [...section.querySelectorAll('[data-liquid-dot]')];
    let position = 0, timer, resizing = false;
    const offset = i => slides[i].offsetLeft;
    const jump = i => { viewport.scrollLeft = offset(i); };
    const sync = () => {
      const nearest = slides.reduce((best, el, i) => Math.abs(el.offsetLeft - viewport.scrollLeft) < Math.abs(slides[best].offsetLeft - viewport.scrollLeft) ? i : best, 0);
      position = ((nearest - count) % count + count) % count;
      dots.forEach((dot, i) => dot.setAttribute('aria-pressed', String(i === position)));
      section.querySelector('[data-liquid-position]').textContent = `Reviews starting at ${position + 1} of ${count}`;
      return nearest;
    };
    const settle = () => {
      if (resizing) return;
      const index = sync();
      if (index < count || index >= count * 2) jump(count + position);
    };
    viewport.addEventListener('scroll', () => { sync(); clearTimeout(timer); timer = setTimeout(settle, 140); }, {passive: true});
    viewport.addEventListener('scrollend', settle);
    const go = i => viewport.scrollTo({left: offset(i), behavior: reduced.matches ? 'instant' : 'smooth'});
    const move = direction => go(count + position + direction);
    section.querySelector('[data-liquid-next]').addEventListener('click', () => move(1));
    section.querySelector('[data-liquid-previous]').addEventListener('click', () => move(-1));
    dots.forEach((dot, i) => dot.addEventListener('click', () => go(count+i)));
    viewport.addEventListener('keydown', event => {
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault(); move(event.key === 'ArrowRight' ? 1 : -1);
      }
      if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); go(count + (event.key === 'Home' ? 0 : count-1)); }
    });
    const resize = () => { resizing = true; jump(count+position); resizing = false; };
    new ResizeObserver(resize).observe(viewport);
    jump(count); sync();
  });
})();

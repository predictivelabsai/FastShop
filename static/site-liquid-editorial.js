/* Progressive enhancement for Helen's editorial interactions. */
(() => {
  const site = document.querySelector('.h-site[data-pack="liquid"]');
  if (!site) return;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const nav = site.querySelector('.why-index');
  const chapters = [...site.querySelectorAll('.why-chapter')];
  const story = site.querySelector('.why-story');
  const visual = story?.querySelector('.why-story-visual');
  const steps = [...(story?.querySelectorAll('.why-steps li') || [])];
  const setStage = stage => {
    if (!story) return;
    const angles = [0,-7,-7,-2,-15,0];
    const angle = angles[stage];
    const dy = 110 * Math.sin(angle * Math.PI / 180);
    story.querySelector('.why-beam').style.transform = `rotate(${angle}deg)`;
    story.querySelectorAll('.why-pan').forEach(pan => {
      pan.style.transform = `translateY(${pan.dataset.side === 'l' ? -dy : dy}px)`;
    });
    story.querySelector('.why-everyday').dataset.on = String(stage >= 1);
    story.querySelector('.why-defenses').dataset.glow = String(stage >= 3);
    story.querySelector('.why-extra-dots').dataset.on = String(stage >= 4);
    story.querySelector('.why-h2drop').dataset.on = String(stage >= 5);
    story.querySelector('.why-balance').dataset.stage = String(stage);
    steps.forEach((step,i) => { step.dataset.active = String(stage === i); });
  };
  let frame = 0;
  function update() {
    frame = 0;
    const top = parseFloat(getComputedStyle(site).getPropertyValue('--metal-top-height')) || 116;
    if (nav && chapters.length) {
      const bounds = nav.getBoundingClientRect();
      const sample = innerWidth < 1024 ? bounds.bottom + 24 : bounds.top + bounds.height / 2;
      const active = chapters.find(ch => { const b = ch.getBoundingClientRect(); return b.top <= sample && b.bottom > sample; });
      const closing = site.querySelector('.why-final');
      const visible = chapters[0].getBoundingClientRect().top <= bounds.bottom + 24 && (!closing || closing.getBoundingClientRect().top > innerHeight*.6);
      nav.dataset.visible = String(visible);
      nav.inert = !visible;
      if (active) {
        nav.dataset.light = String(active.classList.contains('metal-light'));
        nav.querySelectorAll('a').forEach(a => { if (a.hash === '#'+active.id) a.setAttribute('aria-current','true'); else a.removeAttribute('aria-current'); });
      }
    }
    const disclaimer = site.querySelector('[data-editorial-disclaimer]');
    if (disclaimer) {
      const claimInView = [...site.querySelectorAll('main p, main h2')].some(node => {
        if (node === disclaimer || !node.textContent.includes('*') || node.closest('[inert],[aria-hidden="true"]')) return false;
        const bounds = node.getBoundingClientRect();
        return bounds.bottom > top && bounds.top < innerHeight - disclaimer.offsetHeight;
      });
      disclaimer.hidden = !claimInView;
    }
    if (story) {
      const reading = innerWidth < 1024 ? Math.max(top + 80, visual.getBoundingClientRect().bottom + 40) : top + (innerHeight - top)*.45;
      let stage = 0;
      steps.forEach((step,i) => { if (step.getBoundingClientRect().top <= reading) stage = i; });
      setStage(reduce.matches ? 5 : stage);
    }
  }
  const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
  addEventListener('scroll',schedule,{passive:true});
  addEventListener('resize',schedule);
  site.addEventListener('click',schedule);
  reduce.addEventListener('change',schedule);
  new ResizeObserver(schedule).observe(site);
  update();
  site.querySelectorAll('[data-editorial-video]').forEach(video => {
    const motion = () => { if (reduce.matches) { video.pause(); video.hidden = true; } else { video.hidden = false; video.play().catch(() => { video.hidden = true; }); } };
    video.addEventListener('error',() => { video.hidden = true; });
    reduce.addEventListener('change',motion); motion();
  });
  site.querySelectorAll('.why-study').forEach(card => {
    card.dataset.enhanced = 'true';
    const flip = value => {
      card.dataset.flipped = String(value);
      card.querySelector('.why-study-front').inert = value;
      card.querySelector('.why-study-back').inert = !value;
    };
    flip(false);
    card.addEventListener('click',event => { if (event.target.closest('[data-study-back]')) flip(false); else if (!event.target.closest('a,button')) flip(card.dataset.flipped !== 'true'); });
    card.addEventListener('keydown',event => { if (event.target === card && ['Enter',' '].includes(event.key)) { event.preventDefault(); flip(card.dataset.flipped !== 'true'); } });
  });
  site.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click',() => {
    site.querySelectorAll('[data-filter]').forEach(b => { const active = b === button; b.setAttribute('aria-pressed',String(active)); b.classList.toggle('bg-metal-sheen',active); });
    site.querySelectorAll('[data-tags]').forEach(card => { card.hidden = button.dataset.filter !== 'all' && !card.dataset.tags.split(' ').includes(button.dataset.filter); });
  }));
  const deck = site.querySelector('.why-deck');
  if (deck) {
    deck.dataset.enhanced = 'true';
    const cards = [...deck.querySelectorAll('.why-myth-card')];
    let index = 0, flipped = false, start = null, swiped = false;
    function show(next = index, flip = false) {
      index = (next + cards.length) % cards.length; flipped = flip;
      cards.forEach((card,i) => {
        const current = i === index;
        card.dataset.current = String(current); card.dataset.flipped = String(current && flipped);
        card.setAttribute('aria-hidden',String(!current)); card.inert = !current;
        card.querySelector('.why-myth-front').inert = !current || flipped;
        card.querySelector('.why-myth-back').inert = !current || !flipped;
      });
      deck.querySelector('.why-deck-count').textContent = `${index+1} / ${cards.length}`;
      deck.querySelectorAll('.why-deck-dots span').forEach((dot,i) => { dot.dataset.active = String(i === index); });
    }
    deck.addEventListener('click',event => {
      if (swiped) { swiped = false; return; }
      if (event.target.closest('[data-next]')) show(index+1);
      else if (event.target.closest('[data-previous]')) show(index-1);
      else if (event.target.closest('[data-reveal]')) show(index,true);
      else if (event.target.closest('.why-myth-card') && !event.target.closest('a,button')) show(index,!flipped);
    });
    deck.addEventListener('keydown',event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); show(index+(event.key === 'ArrowRight' ? 1 : -1)); } });
    const stack = deck.querySelector('.why-deck-stack');
    stack.addEventListener('pointerdown',event => { start = {x:event.clientX,y:event.clientY}; swiped = false; });
    stack.addEventListener('pointerup',event => { if (!start) return; const dx = event.clientX-start.x, dy = event.clientY-start.y; start = null; if (Math.abs(dx)>40 && Math.abs(dx)>Math.abs(dy)) { swiped = true; show(index+(dx<0?1:-1)); } });
    stack.addEventListener('pointercancel',() => { start = null; });
    show();
  }
})();

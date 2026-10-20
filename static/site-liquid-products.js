/* Product controls enhance native CSRF-protected forms. Prices originate in catalogue listings. */
(() => {
  const root = document.querySelector('.h-site[data-pack="liquid"] .bottle-page');
  if (!root) return;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const money = (minor, compact = false) => '$' + (minor / 100).toFixed(compact && minor % 100 === 0 ? 0 : 2);
  root.querySelectorAll('[data-product-gallery]').forEach(gallery => {
    const track = gallery.querySelector('.bottle-gallery-track');
    const slides = [...track.children];
    const buttons = [...gallery.querySelectorAll('[data-gallery-index]')];
    let active = 0;
    const go = index => {
      active = (index + slides.length) % slides.length;
      track.style.transform = `translateX(-${active * 100}%)`;
      slides.forEach((slide,i) => { slide.setAttribute('aria-hidden', String(i !== active)); });
      buttons.forEach((button,i) => button.setAttribute('aria-pressed',String(i === active)));
      gallery.querySelector('[data-gallery-status]').textContent = `Photo ${active + 1} of ${slides.length}`;
    };
    gallery.querySelector('[data-gallery-previous]').addEventListener('click',()=>go(active-1));
    gallery.querySelector('[data-gallery-next]').addEventListener('click',()=>go(active+1));
    buttons.forEach((button,i)=>button.addEventListener('click',()=>go(i)));
    const viewport = gallery.querySelector('.bottle-gallery-viewport');
    viewport.addEventListener('keydown',event=>{
      if (['ArrowLeft','ArrowRight'].includes(event.key)) { event.preventDefault(); go(active+(event.key === 'ArrowRight' ? 1 : -1)); }
    });
    let start = null;
    viewport.addEventListener('pointerdown',event=>{ start={x:event.clientX,y:event.clientY}; });
    viewport.addEventListener('pointerup',event=>{
      if (!start) return;
      const dx=event.clientX-start.x,dy=event.clientY-start.y;
      if (Math.abs(dx)>35 && Math.abs(dx)>Math.abs(dy)) go(active+(dx<0?1:-1));
      start=null;
    });
    viewport.addEventListener('pointercancel',()=>{start=null;});
    gallery.addEventListener('liquid:gallery-reset',()=>go(0));
    go(0);
  });
  root.querySelectorAll('[data-product-purchase]').forEach(form=>{
    const mode=form.dataset.productPurchase;
    const panel=form.closest('.bottle-purchase');
    const quantity=form.querySelector('[data-product-quantity]');
    const submit=form.querySelector('[data-product-submit]');
    const sticky=panel.querySelector('[data-product-sticky]');
    const base=Number(form.dataset.basePrice);
    const update=()=>{
      let count=Math.max(Number(quantity?.min || 1),Math.min(25,Math.floor(Number(quantity?.value)||1)));
      if (quantity) quantity.value=String(count);
      const selected=form.querySelector('input[name="variant_id"]:checked');
      let unit=Number(selected?.dataset.unitPrice || base);
      if (mode==='bottle') {
        const tier=Number(selected?.dataset.bundle || 0);
        count=tier===3?Math.max(4,count):Number(selected?.dataset.bundleQuantity||1);
        form.querySelector('[data-custom-quantity]').hidden=tier!==3;
        form.querySelector('[data-bottle-quantity]').value=String(count);
        form.querySelectorAll('[data-bundle-option]').forEach(label=>label.setAttribute('aria-pressed',String(label.contains(selected))));
        const savings=form.querySelector('[data-product-savings]');
        savings.hidden=count===1;
        savings.textContent='You save '+money(count*(base-unit),true);
      }
      const subscribed=form.querySelector('input[name="subscription"]:checked')?.value==='on';
      if (subscribed) unit=Number(selected?.dataset.subscriptionPrice || Math.floor((unit*90+50)/100));
      const total=money(unit*count,mode==='bottle');
      form.querySelector('[data-product-total]').textContent=total;
      form.querySelectorAll('.tablet-option').forEach(option=>option.dataset.selected=String(option.querySelector('input')?.checked));
      const frequency=form.querySelector('[data-product-frequency]');
      if (frequency) frequency.hidden=!subscribed;
      const decrement=form.querySelector('[data-quantity-step="-1"]');
      if (decrement) decrement.disabled=Number(quantity?.value)<=Number(quantity?.min||1);
      const increment=form.querySelector('[data-quantity-step="1"]');
      if (increment) increment.disabled=Number(quantity?.value)>=25;
      if (sticky) sticky.querySelector('[data-sticky-summary]').textContent=mode==='bottle'?`${count} ${count===1?'bottle':'bottles'} · ${total}`:`${subscribed?'Subscribe & save · every month':'One-time purchase'} · ${total}`;
      if (mode==='tablet' && selected) {
        const flavor=selected.dataset.flavor;
        form.querySelectorAll('[data-ingredient]').forEach(p=>p.hidden=p.dataset.ingredient!==flavor);
        const g=panel.closest('section').querySelector('[data-product-gallery]');
        const photos=[...g.querySelectorAll('.bottle-gallery-slide img')];
        const thumbs=[...g.querySelectorAll('[data-gallery-index] img')];
        const options=[...form.querySelectorAll('[data-flavor]')];
        const other=options.filter(input=>input!==selected);
        const configured=[
          [selected.dataset.boxImage,selected.dataset.boxAlt],
          [selected.dataset.flavorImage,selected.dataset.flavorAlt],
          [photos[2].getAttribute('src'),photos[2].alt],
          ...other.map(input=>[input.dataset.boxImage,input.dataset.boxAlt]),
        ];
        photos.forEach((img,i)=>{
          const [src,alt]=configured[i] || [];
          if(src) {img.src=src;if(thumbs[i])thumbs[i].src=src;}
          if(alt) img.alt=alt;
        });
        if (g.dataset.selectedFlavor!==flavor) {g.dataset.selectedFlavor=flavor;g.dispatchEvent(new Event('liquid:gallery-reset'));}
      }
    };
    form.addEventListener('change',update);
    form.addEventListener('input',event=>{if(event.target===quantity)update();});
    form.querySelectorAll('[data-quantity-step]').forEach(button=>button.addEventListener('click',()=>{
      quantity.value=String(Number(quantity.value)+Number(button.dataset.quantityStep));update();
    }));
    root.querySelectorAll('[data-select-flavor]').forEach(button=>button.addEventListener('click',()=>{
      const input=form.querySelector(`[data-flavor="${button.dataset.selectFlavor}"]`);
      if (!input) return;
      input.checked=true;update();
      document.getElementById('tablet-product').scrollIntoView({behavior:reduced?'auto':'smooth',block:'start'});
    }));
    if(sticky && 'IntersectionObserver' in window) {
      const io=new IntersectionObserver(([entry])=>{sticky.hidden=entry.isIntersecting;});
      io.observe(submit);
      sticky.querySelector('[data-sticky-purchase]').addEventListener('click',()=>form.requestSubmit(submit));
    }
    update();
  });
  const reveal=root.querySelectorAll('[data-liquid-reveal]');
  if ('IntersectionObserver' in window && !reduced) {
    const io=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('is-visible');io.unobserve(entry.target);}}),{threshold:.1});
    reveal.forEach(el=>io.observe(el));
  } else reveal.forEach(el=>el.classList.add('is-visible'));
  root.querySelectorAll('.tablet-benefit-map').forEach(map=>{
    const glass=map.querySelector('.tablet-benefit-glass'),svg=map.querySelector('svg'),cards=[...map.querySelectorAll('.tablet-benefit')];
    const lines=cards.map(()=>{const line=document.createElementNS('http://www.w3.org/2000/svg','line');svg.append(line);return line;});
    const measure=()=>{
      const bounds=map.getBoundingClientRect(),circle=glass.getBoundingClientRect(),cx=circle.left+circle.width/2,cy=circle.top+circle.height/2,r=circle.width/2;
      cards.forEach((card,i)=>{
        const b=card.getBoundingClientRect(),x=i===4?b.left+b.width/2:i%2===0?b.right:b.left,y=i===4?b.top:b.top+b.height/2,d=Math.hypot(x-cx,y-cy)||1;
        Object.entries({x1:cx+(x-cx)*r/d-bounds.left,y1:cy+(y-cy)*r/d-bounds.top,x2:x-bounds.left,y2:y-bounds.top}).forEach(([key,value])=>lines[i].setAttribute(key,String(value)));
      });
    };
    if ('ResizeObserver' in window) {const observer=new ResizeObserver(measure);[map,glass,...cards].forEach(el=>observer.observe(el));}
    cards.forEach((card,i)=>{
      const active=value=>{cards.forEach((c,j)=>c.dataset.active=String(value&&i===j));lines.forEach((line,j)=>line.dataset.active=String(value&&i===j));};
      card.addEventListener('mouseenter',()=>active(true));card.addEventListener('mouseleave',()=>active(false));card.addEventListener('focusin',()=>active(true));card.addEventListener('focusout',()=>active(false));card.addEventListener('click',()=>active(true));
    });
    measure();
  });
})();

/* Halo Motion: geometry and decoration only; persistence/data stay in the app. */
(() => {
  'use strict';
  const root = document.documentElement;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const desktop = window.matchMedia('(min-width: 761px) and (pointer: fine)');
  const enabled = () => !document.hidden && !reduced.matches;
  const curve = (name) => getComputedStyle(root).getPropertyValue('--spring-' + name).trim() || 'cubic-bezier(.16,1,.3,1)';
  const animate = (el, frames, duration = 240) => {
    if (!el || typeof el.animate !== 'function' || document.hidden) return;
    el.getAnimations().forEach(a => a.cancel());
    return el.animate(reduced.matches ? [{ opacity: .4 }, { opacity: 1 }] : frames,
      { duration: reduced.matches ? 120 : duration, easing: reduced.matches ? 'ease-out' : curve('smooth') });
  };
  const tabs = document.querySelector('.tabs');
  const pill = document.createElement('span');
  pill.className = 'cine-nav-pill'; pill.setAttribute('aria-hidden', 'true');
  tabs?.append(pill);
  let previous = document.querySelector('.tab.active')?.dataset.view;
  let transition = null;
  const placePill = () => {
    const active = tabs?.querySelector('.tab.active');
    if (!active || active.hidden) { if (!pill.hidden) pill.hidden = true; return; }
    const a = active.getBoundingClientRect(), b = tabs.getBoundingClientRect();
    if (pill.hidden) pill.hidden = false;
    Object.assign(pill.style, { width: a.width + 'px', height: a.height + 'px', transform: `translate(${a.left - b.left}px,${a.top - b.top}px)` });
  };
  const nav = (tab, view) => {
    const order = [...document.querySelectorAll('.tab')].map(el => el.dataset.view);
    const direction = order.indexOf(tab.dataset.view) < order.indexOf(previous) ? -1 : 1;
    root.style.setProperty('--cine-direction', direction);
    placePill();
    if (previous !== tab.dataset.view && !transition) animate(view, [{ opacity: 0, transform: `translateX(${direction * 8}px) scale(.985)` }, { opacity: 1, transform: 'none' }]);
    previous = tab.dataset.view;
  };
  const change = (update, name) => {
    if (!enabled() || typeof document.startViewTransition !== 'function' || name === previous) { update(); return; }
    const order = [...document.querySelectorAll('.tab')].map(el => el.dataset.view);
    root.style.setProperty('--cine-direction', order.indexOf(name) < order.indexOf(previous) ? -1 : 1);
    transition?.skipTransition();
    root.classList.add('cine-nav-running');
    const current = document.startViewTransition(update); transition = current;
    current.finished.catch(() => {}).finally(() => { if (transition === current) { transition = null; root.classList.remove('cine-nav-running'); } });
  };
  window.addEventListener('resize', placePill, { passive: true });
  const navObserver = new MutationObserver(placePill);
  if (tabs) navObserver.observe(tabs, { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'class'] });
  placePill();

  // Search camera: keep the focused input readable; only surrounding layers recede.
  const search = document.getElementById('homeSearchBox');
  const focus = () => root.classList.toggle('cine-search', Boolean(search?.contains(document.activeElement)));
  search?.addEventListener('focusin', focus);
  search?.addEventListener('focusout', () => queueMicrotask(focus));
  let frame = 0, mx = 0, my = 0;
  const resetParallax = () => { if (frame) cancelAnimationFrame(frame); frame = 0; mx = my = 0; root.style.setProperty('--wall-px', '0px'); root.style.setProperty('--wall-py', '0px'); };
  document.addEventListener('pointermove', e => {
    if (!enabled() || !desktop.matches || !root.classList.contains('wall-on') || e.pointerType !== 'mouse') return;
    mx = (e.clientX / innerWidth - .5) * 12; my = (e.clientY / innerHeight - .5) * 12;
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; if (!enabled()) return; root.style.setProperty('--wall-px', mx + 'px'); root.style.setProperty('--wall-py', my + 'px'); });
  }, { passive: true });
  reduced.addEventListener('change', resetParallax); desktop.addEventListener('change', resetParallax);
  document.addEventListener('visibilitychange', () => {
    root.classList.toggle('motion-paused', document.hidden);
    if (document.hidden) { resetParallax(); transition?.skipTransition(); document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).forEach(a => a.cancel()); }
  });

  // Bounded FLIP: snapshot the previous layout, then map new cards by stable public IDs.
  const keyOf = el => el.dataset.linkId || el.dataset.arrangeId || ('bm:' + el.dataset.bmIndex);
  const visibleCards = list => [...list.querySelectorAll('[data-link-id],[data-arrange-id],[data-bm-index]')]
    .filter(el => { const r = el.getBoundingClientRect(); return r.width && r.height && r.bottom > 0 && r.top < innerHeight; });
  ['linkList', 'bmList', 'homeList'].forEach(id => {
    const list = document.getElementById(id); if (!list) return;
    let snapshot = new Map();
    const remember = () => { snapshot = new Map(visibleCards(list).map(el => [keyOf(el), el.getBoundingClientRect()])); };
    const observer = new MutationObserver(() => {
      const cards = visibleCards(list);
      if (!enabled() || cards.length > 80) { if (cards.length > 80) animate(list, [{ opacity: .65 }, { opacity: 1 }], 160); remember(); return; }
      const next = new Map();
      cards.forEach(el => {
        el.getAnimations().forEach(a => a.cancel());
        const to = el.getBoundingClientRect(), from = snapshot.get(keyOf(el)); next.set(keyOf(el), to);
        if (from && (Math.abs(from.left - to.left) > 1 || Math.abs(from.top - to.top) > 1)) {
          animate(el, [{ transform: `translate(${from.left - to.left}px,${from.top - to.top}px)` }, { transform: 'none' }]);
        } else if (!from) animate(el, [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], 180);
      });
      snapshot = next;
    });
    observer.observe(list, { childList: true, attributes: true, attributeFilter: ['class'] });
    window.addEventListener('resize', remember, { passive: true }); remember();
  });
  let homePlayed = /(?:^|; )bh_cine_seen=1(?:;|$)/.test(document.cookie);
  const homeEnter = () => {
    const hero = document.querySelector('.home-hero');
    if (homePlayed || !hero || !hero.getBoundingClientRect().width) return;
    homePlayed = true; document.cookie = 'bh_cine_seen=1; Path=/; SameSite=Strict';
    ['.home-hero', '.home-search', '.home-body'].forEach((sel, i) => {
      const el = document.querySelector(sel);
      if (enabled() && el?.animate) el.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: 220, delay: i * 36, easing: curve('smooth'), fill: 'backwards' });
    });
  };

  // An editor grows from its originating card. Close uses an empty surface, never a clone of form data.
  let cardOrigin = null;
  const cardToModal = (idx) => {
    const card = document.querySelector(`#bmList [data-bm-index="${idx}"]`);
    const modal = document.querySelector('#bmModal .modal');
    if (!card || !modal || !enabled()) { cardOrigin = null; return; }
    modal.getAnimations().forEach(a => a.cancel());
    const from = card.getBoundingClientRect(), to = modal.getBoundingClientRect();
    if (!from.width || !to.width) return;
    cardOrigin = { idx, from, to };
    animate(modal, [{ opacity: .2, transformOrigin: 'top left', transform: `translate(${from.left - to.left}px,${from.top - to.top}px) scale(${from.width / to.width},${from.height / to.height})` }, { opacity: 1, transformOrigin: 'top left', transform: 'none' }]);
  };
  const bm = document.getElementById('bmModal');
  if (bm) new MutationObserver(() => {
    if (bm.classList.contains('show') || !cardOrigin) return;
    const origin = cardOrigin; cardOrigin = null;
    const card = document.querySelector(`#bmList [data-bm-index="${origin.idx}"]`);
    if (!card || !enabled()) return;
    const from = origin.to, to = card.getBoundingClientRect();
    const ghost = document.createElement('div'); ghost.className = 'cine-card-return'; ghost.setAttribute('aria-hidden', 'true');
    Object.assign(ghost.style, { left: from.left + 'px', top: from.top + 'px', width: from.width + 'px', height: from.height + 'px' });
    document.body.append(ghost);
    const a = ghost.animate([{ transformOrigin: 'top left', transform: 'none', opacity: .7 }, { transformOrigin: 'top left', transform: `translate(${to.left - from.left}px,${to.top - from.top}px) scale(${to.width / from.width},${to.height / from.height})`, opacity: 0 }], { duration: 180, easing: curve('snappy'), fill: 'forwards' });
    a.finished.catch(() => {}).finally(() => ghost.remove());
    setTimeout(() => ghost.remove(), 400);   // 动画被拖住（后台、软件渲染）也不留残影
  }).observe(bm, { attributes: true, attributeFilter: ['class'] });

  // ===== 科幻特效（html.hub-scifi，外观 → 科幻特效）=====
  // 深空星场：三层深度的星星缓缓朝你飘来，偶尔闪一下；切页 / 开场结束时「跃迁」一下（星星拉成线）。
  // 只是背景：aria-hidden、不接收指针；后台标签页不画，减弱动态效果只画一帧静止画面。
  const scifiOn = () => root.classList.contains('hub-scifi');
  const contrast = window.matchMedia('(prefers-contrast: more), (forced-colors: active)');
  const portraitScene = window.matchMedia('(max-width: 760px) and (orientation: portrait)');
  const connection = navigator.connection;
  const stillScene = () => reduced.matches || Boolean(connection?.saveData);
  const sceneAllowed = () => scifiOn() && !contrast.matches;
  const wall = document.getElementById('homeWall');
  // 舱窗：两侧一道刻度导轨 + 每 11 秒一遍扫描线。只是边框，不写字。
  const hud = document.createElement('div');
  hud.className = 'hub-hud'; hud.setAttribute('aria-hidden', 'true');
  hud.innerHTML = '<i class="hub-hud__rail"></i><i class="hub-hud__rail"></i><i class="hub-hud__scan"></i>';
  wall?.after(hud);

  // ===== 星核反应堆 =====
  // 以时钟为圆心：DOM 里是 .hub-reactor（刻度环 / 虚线环 / 渐变光弧 / 雷达扫描 / 轨道光点 / 三组读数，SVG + CSS，
  // 跟着首页一起滚动）；星核壁纸下 canvas 再在同一圆心实时画星核本体（drawReactor）。读数只写真实的东西：
  // 秒级时钟、首页网址数、看板站点数、预警数（index.html 渲染首页时经 HaloMotion.telemetry 报过来）。
  const hero = document.querySelector('.home-hero');
  const hudSlots = {};
  if (hero) {
    const reactor = document.createElement('div');
    reactor.className = 'hub-reactor'; reactor.setAttribute('aria-hidden', 'true');
    const ticks = Array.from({ length: 120 }, (_, i) => `<line y1="${i % 10 ? -180 : -187}" y2="-172" transform="rotate(${i * 3})"${i % 10 ? '' : ' class="major"'}/>`).join('');
    reactor.innerHTML = '<div class="hub-reactor__sweep"></div>'
      + '<svg class="hub-reactor__svg" viewBox="-200 -200 400 400"><defs><linearGradient id="hubReactorArc" x1="0" y1="0" x2="1" y2="1">'
      + '<stop offset="0" stop-color="#5ee7ff"/><stop offset=".6" stop-color="#a78bfa"/><stop offset="1" stop-color="#f472d0"/></linearGradient></defs>'
      + `<g class="hub-reactor__ticks">${ticks}</g><circle class="hub-reactor__dash" r="152"/>`
      + '<g class="hub-reactor__arcs"><circle r="128" pathLength="100" stroke-dasharray="18 82"/>'
      + '<circle r="128" pathLength="100" stroke-dasharray="9 91" stroke-dashoffset="-40"/><circle r="128" pathLength="100" stroke-dasharray="14 86" stroke-dashoffset="-68"/></g>'
      + '<g class="hub-reactor__orbit"><circle cy="-140" r="2.6"/><circle cy="140" r="1.6"/></g>'
      + '<g class="hub-reactor__orbit is-slow"><circle cx="-164" r="2"/></g></svg>'
      + '<div class="hub-reactor__hud is-tl"><b>SYS</b> ONLINE<br><i data-hud="clock">T+ --:--:--</i></div>'
      + '<div class="hub-reactor__hud is-tr"><b>LINKS</b> <span data-hud="links">--</span><br><i>MONITOR <span data-hud="monitors">--</span></i></div>'
      + '<div class="hub-reactor__hud is-br"><b data-hud="state">ALL NOMINAL</b><br><i data-hud="alerts">ALERT 0</i></div>';
    hero.prepend(reactor);
    reactor.querySelectorAll('[data-hud]').forEach(el => { hudSlots[el.dataset.hud] = el; });
  }
  const tele = {};
  const paintHud = () => {
    if (!hudSlots.clock) return;
    const locked = Boolean(tele.locked), alerts = tele.alerts || 0;
    hudSlots.links.textContent = locked || tele.links == null ? '--' : String(tele.links);
    hudSlots.monitors.textContent = locked || tele.monitors == null ? '--' : String(tele.monitors);
    hudSlots.state.textContent = locked ? 'VAULT LOCKED' : alerts ? 'ATTENTION' : 'ALL NOMINAL';
    hudSlots.alerts.textContent = locked ? 'AUTH REQUIRED' : 'ALERT ' + alerts;
    hudSlots.state.parentElement.classList.toggle('is-warn', !locked && alerts > 0);
  };
  const telemetry = (data) => { Object.assign(tele, data || {}); paintHud(); };
  const tickHud = () => {
    if (!hudSlots.clock || document.hidden || !scifiOn()) return;
    const d = new Date();
    hudSlots.clock.textContent = 'T+ ' + [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
  };
  tickHud(); setInterval(tickHud, 1000);

  const stars = document.createElement('canvas');
  stars.className = 'hub-stars'; stars.setAttribute('aria-hidden', 'true');
  (document.getElementById('homeWall') || document.body.firstChild)?.after(stars);
  const sctx = stars.getContext && stars.getContext('2d');
  const TINTS = ['#ffffff', '#bfe9ff', '#9fd8ff', '#d7c8ff', '#ffe6c4'];
  let field = [], sw = 0, sh = 0, sframe = 0, slast = 0, warpStart = 0, warpUntil = 0, spx = 0, spy = 0, stx = 0, sty = 0;
  let meteor = null, nextMeteor = performance.now() + 5000;
  const phone = () => !desktop.matches;
  const spawn = (far) => ({ x: (Math.random() - .5) * sw / (Math.max(sw, sh) * .55), y: (Math.random() - .5) * sh / (Math.max(sw, sh) * .55), z: far ? 1 : .15 + Math.random() * .85, tint: TINTS[(Math.random() * TINTS.length) | 0], ph: Math.random() * 6.28 });
  const sizeStars = () => {
    if (!sctx) return;
    sw = innerWidth; sh = innerHeight;
    const ratio = Math.min(devicePixelRatio || 1, phone() ? 1.25 : 1.5);
    stars.width = sw * ratio; stars.height = sh * ratio;
    sctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const n = Math.round(Math.min(phone() ? 90 : 240, Math.max(50, sw * sh / 5500)));
    while (field.length < n) field.push(spawn(false));
    field.length = n;
  };
  // 星核本体（星核壁纸下）：外圈辉光、横向镜头光束、吸积盘粒子流（内圈转得快、朝我们转来的左侧更亮）、
  // 日冕射线、被引力透镜弯到核上方的盘影、暗核 + 光子环、冲击波、偶发电弧。暗核正好托在时钟数字后面，
  // 字永远读得清；盘的近半边从数字下方掠过，正对文字那一段淡掉。跃迁（切页 / 开场）时整体增亮、加速。
  const clockEl = document.getElementById('heroClock');
  const DISK_TINTS = ['#e6fbff', '#7fe3ff', '#a78bfa', '#f472d0'];
  const flow = Array.from({ length: 220 }, () => {
    const e = 1.22 + Math.random() ** 1.4 * 2.3;
    return { a: Math.random() * 6.2832, e, lift: (Math.random() - .5) * .05, len: .1 + Math.random() * .16, tint: DISK_TINTS[Math.min(3, ((e - 1.22) / .6) | 0)] };
  });
  const RAYS = Array.from({ length: 72 }, (_, i) => ({ a: i / 72 * 6.2832 + Math.random() * .05, len: .2 + Math.random() ** 2 * 1.2, ph: Math.random() * 6.28 }));
  const DISK_TILT = -.08, TC = Math.cos(DISK_TILT), TS = Math.sin(DISK_TILT);
  let DISK_FLAT = .3;
  let reactorLast = 0, arc = null, nextArc = 0;
  const onDisk = (r, e, a, lift = 0) => {
    const x = Math.cos(a) * e * r, y = (Math.sin(a) * e * DISK_FLAT + lift) * r;
    return [x * TC - y * TS, x * TS + y * TC];
  };
  const reactorAnchor = () => {
    if (root.dataset.wallpaper !== 'starcore' || !clockEl) return null;
    const b = clockEl.getBoundingClientRect();
    if (!b.width || b.bottom < -b.height * 3 || b.top > sh) return null;
    // 桌面：暗核要装得下整排数字和下面那行问候；手机：时钟只有 32px，画扁平版（没有暗核和光环，见 drawReactor）。
    return { x: b.left + b.width / 2, y: b.top + b.height / 2, r: phone() ? Math.max(30, b.height) : Math.max(b.height * 1.3, b.width * .64) };
  };
  const drawReactor = (now, energy) => {
    const at = reactorAnchor();
    if (!at) return;
    const r = at.r, t = now / 1000, dt = reactorLast && t > reactorLast ? Math.min(.1, t - reactorLast) : 0;
    const still = stillScene(), small = phone();
    DISK_FLAT = small ? .16 : .3;
    const boost = 1 + energy * 1.6 + (root.classList.contains('cine-search') ? .35 : 0), lift = Math.min(1.5, boost);
    const breathe = .5 + .5 * Math.sin(t * .9);
    reactorLast = t;
    sctx.save(); sctx.translate(at.x, at.y); sctx.lineCap = 'round';
    sctx.globalCompositeOperation = 'lighter';
    let g = sctx.createRadialGradient(0, 0, r * .8, 0, 0, r * 4.4);
    g.addColorStop(0, 'rgba(167,139,250,.34)'); g.addColorStop(.35, 'rgba(94,231,255,.11)'); g.addColorStop(1, 'rgba(94,231,255,0)');
    sctx.globalAlpha = (.6 + .4 * breathe) * lift; sctx.fillStyle = g; sctx.fillRect(-r * 4.4, -r * 4.4, r * 8.8, r * 8.8);
    const beam = Math.min(sw * .5, r * 4.2);   // 只在星核两侧亮一段，不横贯整屏
    g = sctx.createLinearGradient(-beam, 0, beam, 0);
    g.addColorStop(0, 'rgba(94,231,255,0)'); g.addColorStop(.5, 'rgba(214,248,255,.95)'); g.addColorStop(1, 'rgba(94,231,255,0)');
    sctx.fillStyle = g; sctx.globalAlpha = (.4 + .25 * breathe) * lift; sctx.fillRect(-beam, -.8, beam * 2, 1.6);
    sctx.globalAlpha *= .3; sctx.fillRect(-beam * .75, -6, beam * 1.5, 12);
    const disk = (front) => {
      sctx.lineWidth = small ? 1.1 : 1.6;
      for (let i = 0, n = small ? 100 : flow.length; i < n; i++) {
        const p = flow[i];
        if (!front) p.a += dt * (.5 + energy * 2.4) / p.e ** 1.5;
        if ((Math.sin(p.a) > 0) !== front) continue;
        const [x0, y0] = onDisk(r, p.e, p.a - p.len / p.e, p.lift), [x1, y1] = onDisk(r, p.e, p.a, p.lift);
        let alpha = (.16 + .66 * (1 - .7 * Math.cos(p.a)) / 1.7) * (1 - .5 * (p.e - 1.22) / 2.3);
        if (front) alpha *= Math.min(1, Math.max(.1, (Math.abs(x1) - r * .5) / r));
        sctx.globalAlpha = alpha * lift; sctx.strokeStyle = p.tint;
        sctx.beginPath(); sctx.moveTo(x0, y0); sctx.lineTo(x1, y1); sctx.stroke();
      }
    };
    disk(false);
    if (!small) {   // 手机上到此为止只剩辉光、光束与扁平盘：暗核、光环、透镜弧都不画
      sctx.lineWidth = 1; sctx.strokeStyle = '#9fe9ff';
      for (const ray of RAYS) {
        const a = ray.a + t * .025, len = ray.len * (.65 + .35 * Math.sin(t * 1.3 + ray.ph));
        sctx.globalAlpha = .08 * lift;
        sctx.beginPath(); sctx.moveTo(Math.cos(a) * r * 1.06, Math.sin(a) * r * 1.06);
        sctx.lineTo(Math.cos(a) * r * (1.06 + len), Math.sin(a) * r * (1.06 + len)); sctx.stroke();
      }
    }
    if (!small) {
      g = sctx.createLinearGradient(-r * 1.2, 0, r * 1.2, 0);
      g.addColorStop(0, '#5ee7ff'); g.addColorStop(.45, '#f2fbff'); g.addColorStop(1, '#f472d0');
      sctx.strokeStyle = g; sctx.shadowColor = '#a78bfa'; sctx.shadowBlur = small ? 6 : 16;
      sctx.lineWidth = small ? 1.6 : 2.4; sctx.globalAlpha = (.55 + .3 * breathe) * Math.min(1.4, boost);
      sctx.beginPath(); sctx.ellipse(0, -r * .04, r * 1.17, r * 1.1, 0, Math.PI * 1.04, Math.PI * 1.96); sctx.stroke();
      sctx.lineWidth = 1.2; sctx.globalAlpha *= .5;
      sctx.beginPath(); sctx.ellipse(0, r * .02, r * 1.08, r * 1.04, 0, Math.PI * .1, Math.PI * .9); sctx.stroke();
      sctx.shadowBlur = 0;
      sctx.globalCompositeOperation = 'source-over';
      g = sctx.createRadialGradient(0, 0, 0, 0, 0, r * 1.03);
      g.addColorStop(0, 'rgba(2,3,10,.95)'); g.addColorStop(.85, 'rgba(3,4,14,.92)'); g.addColorStop(1, 'rgba(3,4,14,0)');
      sctx.globalAlpha = 1; sctx.fillStyle = g; sctx.beginPath(); sctx.arc(0, 0, r * 1.03, 0, 6.2832); sctx.fill();
      sctx.globalCompositeOperation = 'lighter';
      sctx.lineWidth = 1.4; sctx.strokeStyle = '#dff8ff'; sctx.shadowColor = '#5ee7ff'; sctx.shadowBlur = small ? 4 : 12;
      sctx.globalAlpha = (.5 + .35 * breathe) * Math.min(1.4, boost);
      sctx.beginPath(); sctx.arc(0, 0, r, 0, 6.2832); sctx.stroke(); sctx.shadowBlur = 0;
    }
    disk(true);
    const wave = (t % 7) / 1.8;
    if (!still && wave < 1) {
      const k = 1.05 + wave * 3;
      sctx.globalAlpha = (1 - wave) ** 2 * .6; sctx.strokeStyle = '#9fe9ff'; sctx.lineWidth = .8 + 2 * (1 - wave);
      sctx.beginPath(); sctx.ellipse(0, 0, r * k, r * k * DISK_FLAT, DISK_TILT, 0, 6.2832); sctx.stroke();
      sctx.globalAlpha *= .5; sctx.strokeStyle = '#c4b0ff';
      sctx.beginPath(); sctx.arc(0, 0, r * (1.02 + wave * 1.4), 0, 6.2832); sctx.stroke();
    }
    if (energy > .05) {   // 跃迁：一圈冷光从暗核边缘炸开
      sctx.globalAlpha = energy * .7; sctx.strokeStyle = '#e0fbff'; sctx.lineWidth = 2 + energy * 3;
      sctx.shadowColor = '#5ee7ff'; sctx.shadowBlur = 20;
      sctx.beginPath(); sctx.arc(0, 0, r * (1 + (1 - energy) * 2.4), 0, 6.2832); sctx.stroke(); sctx.shadowBlur = 0;
    }
    if (!still && !small) {
      if (now > nextArc) {
        // 从暗核边缘沿径向打到盘上：起点取终点方向上的光子环，整条电弧都在暗核外面，不会划过时钟数字。
        const a = Math.random() * 6.2832, e = 1.6 + Math.random() * 1.2, pts = [];
        const [ex, ey] = onDisk(1, e, a), b = Math.atan2(ey, ex) + (Math.random() - .5) * .5, sx = Math.cos(b) * 1.03, sy = Math.sin(b) * 1.03;
        for (let i = 0; i <= 10; i++) {
          const k = i / 10, jit = i && i < 10 ? (Math.random() - .5) * .18 : 0;
          const x = sx * (1 - k) + ex * k, y = sy * (1 - k) + ey * k;
          pts.push([x - (ey - sy) * jit, y + (ex - sx) * jit]);
        }
        arc = { start: now, pts }; nextArc = now + 2600 + Math.random() * 4000;
      }
      if (arc && now - arc.start < 260) {
        sctx.globalAlpha = .55 + .45 * Math.random(); sctx.strokeStyle = '#e6f9ff'; sctx.lineWidth = 1.3;
        sctx.shadowColor = '#5ee7ff'; sctx.shadowBlur = 10;
        sctx.beginPath(); arc.pts.forEach(([x, y], i) => (i ? sctx.lineTo(x * r, y * r) : sctx.moveTo(x * r, y * r))); sctx.stroke();
        sctx.shadowBlur = 0;
      }
    }
    sctx.restore();
  };
  const drawStars = (now, dt) => {
    const warping = !stillScene() && now < warpUntil;
    const progress = warping ? Math.max(0, (now - warpStart) / (warpUntil - warpStart)) : 0;
    const energy = warping ? Math.sin(progress * Math.PI) : 0, speed = .025 + energy * .95;
    const cx = sw / 2 + spx, cy = sh / 2 + spy, scale = Math.max(sw, sh) * .55;
    const light = root.getAttribute('data-theme') === 'light' && !root.classList.contains('wall-on');
    sctx.clearRect(0, 0, sw, sh); sctx.lineCap = 'round';
    for (const s of field) {
      s.z -= speed * dt;
      if (s.z <= .04) { Object.assign(s, spawn(true)); continue; }
      const x = cx + s.x / s.z * scale, y = cy + s.y / s.z * scale;
      if (x < -20 || x > sw + 20 || y < -20 || y > sh + 20) { Object.assign(s, spawn(true)); continue; }
      const near = 1 - s.z, tw = warping ? 1 : .7 + .3 * Math.sin(now / 620 + s.ph);
      const alpha = Math.min(1, (.15 + near * .95) * tw), size = .35 + near * near * (phone() ? 1.6 : 2.1);
      sctx.globalAlpha = light ? alpha * .32 : alpha;
      const color = light ? '#3b4a7a' : s.tint;
      if (energy > .04) {
        sctx.strokeStyle = color; sctx.lineWidth = size; sctx.beginPath();
        const tail = s.z + .18 * energy;
        sctx.moveTo(cx + s.x / tail * scale, cy + s.y / tail * scale); sctx.lineTo(x, y); sctx.stroke();
        if (!light && energy > .3 && near > .45) {   // 色差拖尾：近处的星在跃迁峰值拖出一道品红 / 青色的余光
          const far = s.z + .34 * energy;
          sctx.globalAlpha = alpha * .4 * energy; sctx.strokeStyle = s.tint === '#ffffff' ? '#f472d0' : '#5ee7ff'; sctx.lineWidth = size * 2.2;
          sctx.beginPath(); sctx.moveTo(cx + s.x / far * scale, cy + s.y / far * scale); sctx.lineTo(cx + s.x / tail * scale, cy + s.y / tail * scale); sctx.stroke();
        }
      } else { sctx.fillStyle = color; sctx.beginPath(); sctx.arc(x, y, size, 0, 6.2832); sctx.fill(); }
    }
    sctx.globalAlpha = 1;
    drawReactor(now, energy);
    sctx.globalAlpha = 1;
    if (!light && energy > .05) {   // 跃迁峰值：中心一团冷光，封顶 .2，不做全屏闪白
      const q = Math.min(sw, sh) * .45, g = sctx.createRadialGradient(cx, cy, 0, cx, cy, q);
      g.addColorStop(0, 'rgba(160,240,255,1)'); g.addColorStop(.4, 'rgba(150,110,255,.35)'); g.addColorStop(1, 'rgba(94,231,255,0)');
      sctx.globalAlpha = energy * .2; sctx.fillStyle = g; sctx.fillRect(cx - q, cy - q, q * 2, q * 2); sctx.globalAlpha = 1;
    }
    if (!phone() && !stillScene() && !warping) {
      if (now > nextMeteor) { meteor = { start: now, x: sw * (.4 + Math.random() * .5), y: sh * Math.random() * .3 }; nextMeteor = now + 6500 + Math.random() * 5500; }
      if (meteor) {
        const p = (now - meteor.start) / 1200;
        if (p >= 1) meteor = null;
        else {
          const x = meteor.x - p * 260, y = meteor.y + p * 120;
          const trail = sctx.createLinearGradient(x + 110, y - 50, x, y);
          trail.addColorStop(0, 'rgba(94,231,255,0)'); trail.addColorStop(1, light ? '#536b97' : '#b9f4ff');
          sctx.globalAlpha = Math.sin(p * Math.PI) * .7; sctx.strokeStyle = trail; sctx.lineWidth = 1.2;
          sctx.beginPath(); sctx.moveTo(x + 110, y - 50); sctx.lineTo(x, y); sctx.stroke(); sctx.globalAlpha = 1;
        }
      }
    }
  };
  const starLoop = (now) => {
    sframe = 0;
    if (document.hidden || stillScene() || !sceneAllowed()) return;
    // Wall-clock cap also holds on 120/144Hz screens: 30fps desktop, 20fps phone.
    if (!slast || now - slast >= (phone() ? 50 : 1000 / 30)) {
      const dt = slast ? Math.min(.1, (now - slast) / 1000) : 0;
      spx += (stx - spx) * .12; spy += (sty - spy) * .12; drawStars(now, dt); slast = now;
    }
    sframe = requestAnimationFrame(starLoop);
  };
  const startStars = () => {
    if (!sctx || sframe || document.hidden || !sceneAllowed()) return;
    if (stillScene()) { drawStars(0, 0); return; }
    slast = 0; sframe = requestAnimationFrame(starLoop);
  };
  const stopStars = () => { if (sframe) cancelAnimationFrame(sframe); sframe = 0; };
  const hubWarp = (ms = 900) => { if (stillScene() || !sceneAllowed() || document.hidden) return; warpStart = performance.now(); warpUntil = warpStart + ms; startStars(); };
  if (sctx) {
    sizeStars();
    window.addEventListener('resize', () => { sizeStars(); startStars(); }, { passive: true });
    document.addEventListener('visibilitychange', () => (document.hidden ? stopStars() : startStars()));
    document.addEventListener('pointermove', e => {
      if (!desktop.matches || e.pointerType !== 'mouse') return;
      stx = (e.clientX / innerWidth - .5) * -24; sty = (e.clientY / innerHeight - .5) * -24;
    }, { passive: true });
    const syncScene = () => { stopStars(); sctx.clearRect(0, 0, sw, sh); root.classList.toggle('scene-still', stillScene()); startStars(); };
    reduced.addEventListener('change', syncScene); contrast.addEventListener('change', syncScene);
    connection?.addEventListener?.('change', syncScene);
    // 外观里开关一拨，<html> 的 class 变了：星场跟着起停。
    new MutationObserver(() => {
      if (!sceneAllowed()) { stopStars(); sctx.clearRect(0, 0, sw, sh); }
      else startStars();
    }).observe(root, { attributes: true, attributeFilter: ['class', 'data-wallpaper', 'data-theme'] });
    syncScene();
  }

  // 首屏入场：页面打开后 3 秒内画出来的分组 / 组件 / 预警条逐个从模糊里扫描显形；之后重画首页不再播。
  if (sceneAllowed() && !stillScene()) {
    root.classList.add('sf-entering');
    setTimeout(() => root.classList.remove('sf-entering'), 3000);
  }

  // 开场：最多每 6 小时一次（Cookie 自带 6 小时寿命），新开标签页不会每次都放；点一下或按任意键跳过。
  const bootCookie = /(?:^|; )bh_scifi_boot=1(?:;|$)/;
  if (sceneAllowed() && !stillScene() && !bootCookie.test(document.cookie) && window.top === window) {
    document.cookie = 'bh_scifi_boot=1; max-age=21600; path=/; SameSite=Lax';
    const boot = document.createElement('div');
    boot.className = 'hub-boot'; boot.setAttribute('aria-hidden', 'true');
    boot.innerHTML = '<div class="hub-boot__grid"></div><div class="hub-boot__scan"></div>'
      + '<svg class="hub-boot__ring" viewBox="-100 -100 200 200"><circle class="a" r="86" pathLength="100"/><circle class="b" r="70" pathLength="100"/><circle class="c" r="54" pathLength="100"/><circle class="dot" r="7"/></svg>'
      + '<div class="hub-boot__title" data-text="BOOKMARK HUB">BOOKMARK HUB</div>'
      + '<div class="hub-boot__lines"><p style="--d:.3s">&gt; SYNCING BOOKMARKS ......... <b>OK</b></p><p style="--d:.65s">&gt; MONITORS ONLINE ........... <b>OK</b></p><p style="--d:1s">&gt; ALL SYSTEMS NOMINAL</p></div>'
      + '<div class="hub-boot__skip">CLICK TO SKIP</div>';
    document.body.append(boot);
    let gone = false;
    const leave = () => {
      if (gone) return; gone = true;
      window.removeEventListener('keydown', leave);
      boot.classList.add('is-leaving'); hubWarp(1100);
      setTimeout(() => boot.remove(), 600);
    };
    boot.addEventListener('click', leave);
    window.addEventListener('keydown', leave, { once: true });
    const dismissBoot = new MutationObserver(() => { if (!sceneAllowed() || stillScene()) leave(); });
    dismissBoot.observe(root, { attributes: true, attributeFilter: ['class'] });
    setTimeout(() => dismissBoot.disconnect(), 2400);
    setTimeout(leave, 1800);
  }

  // 切页时星场跃迁一下（和 View Transition 同时发生）。
  const plainChange = change;
  const scifiChange = (update, name) => { if (name !== previous) hubWarp(650); plainChange(update, name); };
  window.HaloMotion = { nav, change: scifiChange, homeEnter, cardToModal, warp: hubWarp, telemetry };
})();

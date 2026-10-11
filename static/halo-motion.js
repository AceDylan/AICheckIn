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
  let transition = null, updating = false;
  // 转场的 update 回调里置 updating：期间（例如切大页面时顺带打开首页）再要转场就直接更新，不嵌套。
  const inUpdate = (update) => () => { updating = true; try { update(); } finally { updating = false; } };
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
    const current = document.startViewTransition(inUpdate(update)); transition = current;
    current.finished.catch(() => {}).finally(() => { if (transition === current) { transition = null; root.classList.remove('cine-nav-running'); } });
  };
  // 收藏库里切分组页（同一个大页面里换内容）：和切大页面同一套 View Transition（内容一跳），科幻特效开着时星场跃迁一下；
  // direction 按侧栏里的上下顺序。在另一个转场的 update 里（切大页面时顺带打开首页）就直接更新，不嵌套；连点则跳过上一个。
  const page = (update, changed, direction = 1) => {
    if (!changed || updating || !enabled()) { update(); return; }
    root.style.setProperty('--cine-direction', direction);
    if (root.classList.contains('hub-scifi')) window.HaloMotion?.warp?.(520);
    if (typeof document.startViewTransition !== 'function') {
      update();
      animate(document.querySelector('.view.active'), [{ opacity: 0, transform: `translateY(10px) scale(.985)`, filter: 'blur(4px)' }, { opacity: 1, transform: 'none', filter: 'none' }], 320);
      return;
    }
    transition?.skipTransition();   // 连点几个分组：上一个转场直接收尾，新的接着放
    root.classList.add('cine-nav-running');
    const current = document.startViewTransition(inUpdate(update)); transition = current;
    current.ready.catch(() => {});   // 浏览器跳过转场（页面隐藏、又起了一个）时 ready 会 reject：不算错误
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

  // ===== 星核反应堆 =====
  // 以时钟为圆心：DOM 里是 .hub-reactor（刻度环 / 科技分段环 / 虚线环 / 渐变光弧 / 内层约束环 / 雷达扫描 / 轨道光点 / 四角读数，
  // SVG + CSS，跟着首页一起滚动）；深色无壁纸或星核/星河/流光壁纸下 canvas 再在同一圆心实时画星核本体（drawReactor）。
  // 读数只写真实的东西：秒级时钟、首页网址数、看板站点数、分组数、预警数（index.html 渲染首页时经 HaloMotion.telemetry 报过来）。
  const hero = document.querySelector('.home-hero');
  const hudSlots = {};
  if (hero) {
    const reactor = document.createElement('div');
    reactor.className = 'hub-reactor'; reactor.setAttribute('aria-hidden', 'true');
    const ticks = Array.from({ length: 120 }, (_, i) => {
      const card = i % 30 === 0, maj = i % 10 === 0;
      return `<line y1="${card ? -191 : maj ? -187 : -180}" y2="${card ? -169 : -172}" transform="rotate(${i * 3})"${card ? ' class="cardinal"' : maj ? ' class="major"' : ''}/>`;
    }).join('');
    const reticles = [0, 90, 180, 270].map(deg => `<path d="M-5,-195 L0,-189 L5,-195" transform="rotate(${deg})"/>`).join('');
    reactor.innerHTML = '<div class="hub-reactor__sweep"></div>'
      + '<svg class="hub-reactor__svg" viewBox="-200 -200 400 400"><defs><linearGradient id="hubReactorArc" x1="0" y1="0" x2="1" y2="1">'
      + '<stop offset="0" stop-color="#5ee7ff"/><stop offset=".55" stop-color="#a78bfa"/><stop offset="1" stop-color="#f472d0"/></linearGradient></defs>'
      + `<g class="hub-reactor__ticks">${ticks}</g><g class="hub-reactor__reticle">${reticles}</g>`
      + '<circle class="hub-reactor__tech" r="166" pathLength="120" stroke-dasharray="9 5 2 5 19 8"/>'
      + '<circle class="hub-reactor__dash" r="152"/>'
      + '<g class="hub-reactor__arcs"><circle r="132" pathLength="100" stroke-dasharray="20 80"/>'
      + '<circle r="132" pathLength="100" stroke-dasharray="10 90" stroke-dashoffset="-38"/><circle r="132" pathLength="100" stroke-dasharray="15 85" stroke-dashoffset="-68"/></g>'
      + '<circle class="hub-reactor__inner" r="110" pathLength="100" stroke-dasharray="16 9"/>'
      + '<g class="hub-reactor__orbit"><circle cy="-142" r="2.8"/><circle cy="142" r="1.8"/></g>'
      + '<g class="hub-reactor__orbit is-slow"><circle cx="-166" r="2.2"/><circle cx="166" r="1.4"/></g></svg>'
      + '<div class="hub-reactor__hud is-tl"><b>SYS</b> ONLINE<br><i data-hud="clock">T+ --:--:--</i></div>'
      + '<div class="hub-reactor__hud is-tr"><b>LINKS</b> <span data-hud="links">--</span><br><i>MONITOR <span data-hud="monitors">--</span></i></div>'
      + '<div class="hub-reactor__hud is-bl"><b>SECTORS</b> <span data-hud="groups">--</span><br><i data-hud="scene">REACTOR SYNC</i></div>'
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
    if (hudSlots.groups) hudSlots.groups.textContent = locked || tele.groups == null ? '--' : String(tele.groups);
    if (hudSlots.scene) hudSlots.scene.textContent = locked ? 'STANDBY' : 'REACTOR SYNC';
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
  // 透视网格地面（见 app-v3.css「科幻层 II / III」）：纯 CSS 动画，这里只放个空节点。
  const floor = document.createElement('div');
  floor.className = 'hub-floor'; floor.setAttribute('aria-hidden', 'true');
  stars.after(floor);
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
  // 星核本体（深色科幻舞台 & 星核/星河/流光壁纸）：外圈等离子星云辉光、变形宽银幕镜头光束 + 光圈鬼影、
  // 连续吸积盘流光带 + 多普勒粒子弧、柔和日冕弧、引力透镜上下光环、暗核 + 双层光子环、脉冲冲击波、偶发电弧。
  // 暗核正好托在时钟数字后面，字永远读得清；跃迁或聚焦搜索时整体增能加速。
  const clockEl = document.getElementById('heroClock');
  const DISK_TINTS = ['#e6fbff', '#5ee7ff', '#a78bfa', '#f472d0'];
  const RINGS = [
    { e: 1.15, w: 1.8, a: .52 },
    { e: 1.36, w: 1.3, a: .38 },
    { e: 1.64, w: 1.1, a: .28 },
    { e: 1.98, w: .9, a: .18 },
  ];
  const flow = Array.from({ length: 44 }, (_, i) => {
    const e = 1.14 + (i / 44) ** 1.35 * 1.18;
    return { a: (i * 2.39996) % 6.2832, e, len: .38 + (i % 5) * .11, tint: DISK_TINTS[Math.min(3, ((e - 1.14) / .3) | 0)] };
  });
  const CORONA = Array.from({ length: 18 }, (_, i) => ({ a: i / 18 * 6.2832, span: .16 + (i % 3) * .08, reach: 1.05 + (i % 4) * .035, ph: i * 1.1 }));
  const DISK_TILT = -.085, TC = Math.cos(DISK_TILT), TS = Math.sin(DISK_TILT);
  let DISK_FLAT = .24;
  let reactorLast = 0, arc = null, nextArc = 0;
  const onDisk = (r, e, a) => {
    const x = Math.cos(a) * e * r, y = Math.sin(a) * e * DISK_FLAT * r;
    return [x * TC - y * TS, x * TS + y * TC];
  };
  const reactorAnchor = () => {
    const wp = root.dataset.wallpaper;
    const darkScene = wp === 'starcore' || wp === 'galaxy' || wp === 'flow' || (!root.classList.contains('wall-on') && root.getAttribute('data-theme') !== 'light');
    if (!darkScene || !clockEl) return null;
    const b = clockEl.getBoundingClientRect();
    if (!b.width || b.bottom < -b.height * 3 || b.top > sh) return null;
    return { x: b.left + b.width / 2, y: b.top + b.height / 2, r: phone() ? Math.max(30, b.height) : Math.max(b.height * 1.3, b.width * .64) };
  };
  const drawReactor = (now, energy) => {
    const at = reactorAnchor();
    if (!at) return;
    const r = at.r, t = now / 1000, dt = reactorLast && t > reactorLast ? Math.min(.1, t - reactorLast) : 0;
    const still = stillScene(), small = phone();
    DISK_FLAT = small ? .16 : .24;
    const boost = 1 + energy * 1.6 + (root.classList.contains('cine-search') ? .4 : 0), lift = Math.min(1.55, boost);
    const breathe = .5 + .5 * Math.sin(t * .9);
    reactorLast = t;
    sctx.save(); sctx.translate(at.x, at.y); sctx.lineCap = 'round';
    sctx.globalCompositeOperation = 'lighter';
    let g = sctx.createRadialGradient(0, 0, r * .65, 0, 0, r * 4.8);
    g.addColorStop(0, 'rgba(94,231,255,.28)'); g.addColorStop(.28, 'rgba(167,139,250,.22)'); g.addColorStop(.62, 'rgba(244,114,208,.07)'); g.addColorStop(1, 'rgba(94,231,255,0)');
    sctx.globalAlpha = (.65 + .35 * breathe) * lift; sctx.fillStyle = g; sctx.fillRect(-r * 4.8, -r * 4.8, r * 9.6, r * 9.6);
    // 变形宽银幕镜头光束（Anamorphic Lens Flare）+ 对称光圈鬼影
    const beam = Math.min(sw * .54, r * 4.8);
    g = sctx.createLinearGradient(-beam, 0, beam, 0);
    g.addColorStop(0, 'rgba(94,231,255,0)'); g.addColorStop(.28, 'rgba(94,231,255,.38)'); g.addColorStop(.5, 'rgba(235,252,255,.98)'); g.addColorStop(.72, 'rgba(167,139,250,.38)'); g.addColorStop(1, 'rgba(167,139,250,0)');
    sctx.fillStyle = g; sctx.globalAlpha = (.48 + .28 * breathe) * lift; sctx.fillRect(-beam, -.9, beam * 2, 1.8);
    sctx.globalAlpha *= .34; sctx.fillRect(-beam * .8, -7, beam * 1.6, 14);
    if (!small) {
      for (const [ox, rx, ry, col] of [[-r * 2.25, r * .26, r * .12, 'rgba(94,231,255,.16)'], [r * 2.25, r * .26, r * .12, 'rgba(167,139,250,.16)'], [-r * 3.05, r * .14, r * .07, 'rgba(244,114,208,.12)'], [r * 3.05, r * .14, r * .07, 'rgba(94,231,255,.12)']]) {
        sctx.strokeStyle = col; sctx.lineWidth = 1.1; sctx.globalAlpha = (.35 + .25 * breathe) * lift;
        sctx.beginPath(); sctx.ellipse(ox, 0, rx, ry, 0, 0, 6.2832); sctx.stroke();
      }
    }
    const disk = (front) => {
      sctx.save(); sctx.rotate(DISK_TILT); sctx.scale(1, DISK_FLAT);
      const rg = sctx.createRadialGradient(-r * .35, 0, r * 1.02, 0, 0, r * 2.55);
      rg.addColorStop(0, 'rgba(224,251,255,0)'); rg.addColorStop(.14, 'rgba(94,231,255,.32)'); rg.addColorStop(.45, 'rgba(167,139,250,.20)'); rg.addColorStop(.76, 'rgba(244,114,208,.08)'); rg.addColorStop(1, 'rgba(94,231,255,0)');
      sctx.fillStyle = rg; sctx.globalAlpha = (front ? .44 : .72) * lift;
      sctx.beginPath(); sctx.arc(0, 0, r * 2.55, front ? 0 : Math.PI, front ? Math.PI : 6.2832); sctx.arc(0, 0, r * 1.05, front ? Math.PI : 6.2832, front ? 0 : Math.PI, true); sctx.closePath(); sctx.fill();
      sctx.restore();
      const lg = sctx.createLinearGradient(-r * 2.3, 0, r * 2.3, 0);
      lg.addColorStop(0, 'rgba(224,251,255,.95)'); lg.addColorStop(.28, 'rgba(94,231,255,.85)'); lg.addColorStop(.62, 'rgba(167,139,250,.55)'); lg.addColorStop(1, 'rgba(244,114,208,.32)');
      sctx.strokeStyle = lg;
      for (const ring of RINGS) {
        sctx.lineWidth = small ? ring.w * .75 : ring.w;
        sctx.globalAlpha = ring.a * (front ? .55 : .9) * lift;
        sctx.beginPath(); sctx.ellipse(0, 0, r * ring.e, r * ring.e * DISK_FLAT, DISK_TILT, front ? 0 : Math.PI, front ? Math.PI : 6.2832); sctx.stroke();
      }
      sctx.lineWidth = small ? 1.15 : 1.5;
      for (let i = 0, n = small ? 24 : flow.length; i < n; i++) {
        const p = flow[i];
        if (!front) p.a = (p.a + dt * (.48 + energy * 2.2) / p.e ** 1.4) % 6.2832;
        if ((Math.sin(p.a) > 0) !== front) continue;
        const a0 = p.a - p.len / p.e;
        let alpha = (.22 + .65 * (1 - .65 * Math.cos(p.a)) / 1.65) * (1 - .45 * (p.e - 1.14) / 1.2);
        if (front) alpha *= Math.min(1, Math.max(.14, (Math.abs(Math.cos(p.a) * p.e) - .45)));
        sctx.globalAlpha = alpha * lift; sctx.strokeStyle = p.tint;
        sctx.beginPath(); sctx.ellipse(0, 0, r * p.e, r * p.e * DISK_FLAT, DISK_TILT, a0, p.a); sctx.stroke();
      }
    };
    disk(false);
    if (!small) {
      // 柔和日冕光弧
      sctx.lineWidth = 1.3;
      for (const c of CORONA) {
        const a = c.a + t * .035, pulse = .55 + .45 * Math.sin(t * 1.4 + c.ph);
        sctx.strokeStyle = c.ph > 3.14 ? '#a78bfa' : '#7fe3ff';
        sctx.globalAlpha = .16 * pulse * lift;
        sctx.beginPath(); sctx.arc(0, 0, r * (1.03 + (c.reach - 1.03) * pulse), a, a + c.span); sctx.stroke();
      }
      g = sctx.createLinearGradient(-r * 1.22, 0, r * 1.22, 0);
      g.addColorStop(0, '#5ee7ff'); g.addColorStop(.45, '#f2fbff'); g.addColorStop(1, '#f472d0');
      sctx.strokeStyle = g; sctx.shadowColor = '#a78bfa'; sctx.shadowBlur = 18;
      sctx.lineWidth = 2.5; sctx.globalAlpha = (.62 + .3 * breathe) * Math.min(1.45, boost);
      sctx.beginPath(); sctx.ellipse(0, -r * .04, r * 1.18, r * 1.11, 0, Math.PI * 1.04, Math.PI * 1.96); sctx.stroke();
      sctx.lineWidth = 1.4; sctx.globalAlpha *= .58;
      sctx.beginPath(); sctx.ellipse(0, r * .02, r * 1.09, r * 1.05, 0, Math.PI * .08, Math.PI * .92); sctx.stroke();
      sctx.shadowBlur = 0;
      sctx.globalCompositeOperation = 'source-over';
      g = sctx.createRadialGradient(0, 0, 0, 0, 0, r * 1.04);
      g.addColorStop(0, 'rgba(2,3,10,.96)'); g.addColorStop(.84, 'rgba(3,5,15,.92)'); g.addColorStop(1, 'rgba(3,5,15,0)');
      sctx.globalAlpha = 1; sctx.fillStyle = g; sctx.beginPath(); sctx.arc(0, 0, r * 1.04, 0, 6.2832); sctx.fill();
      sctx.globalCompositeOperation = 'lighter';
      sctx.lineWidth = 1.6; sctx.strokeStyle = '#e6fbff'; sctx.shadowColor = '#5ee7ff'; sctx.shadowBlur = 14;
      sctx.globalAlpha = (.58 + .35 * breathe) * Math.min(1.45, boost);
      sctx.beginPath(); sctx.arc(0, 0, r, 0, 6.2832); sctx.stroke();
      sctx.lineWidth = .9; sctx.strokeStyle = '#a78bfa'; sctx.globalAlpha *= .55;
      sctx.beginPath(); sctx.arc(0, 0, r * 1.05, 0, 6.2832); sctx.stroke();
      sctx.shadowBlur = 0;
    } else {
      // 手机端：在时钟四周点亮一圈轻量光子弧，不遮挡数字
      sctx.lineWidth = 1.1; sctx.strokeStyle = 'rgba(94,231,255,.45)';
      sctx.globalAlpha = (.45 + .25 * breathe) * lift;
      sctx.beginPath(); sctx.arc(0, 0, r * 1.18, -.55, .55); sctx.stroke();
      sctx.beginPath(); sctx.arc(0, 0, r * 1.18, 2.59, 3.69); sctx.stroke();
    }
    disk(true);
    const wave = (t % 6.4) / 1.8;
    if (!still && wave < 1) {
      const k = 1.04 + wave * 2.9;
      sctx.globalAlpha = (1 - wave) ** 2 * .68; sctx.strokeStyle = '#9fe9ff'; sctx.lineWidth = .9 + 2.2 * (1 - wave);
      sctx.beginPath(); sctx.ellipse(0, 0, r * k, r * k * DISK_FLAT, DISK_TILT, 0, 6.2832); sctx.stroke();
      sctx.globalAlpha *= .55; sctx.strokeStyle = '#c4b0ff';
      sctx.beginPath(); sctx.arc(0, 0, r * (1.02 + wave * 1.45), 0, 6.2832); sctx.stroke();
    }
    if (energy > .05) {   // 跃迁：一圈冷光从暗核边缘炸开
      sctx.globalAlpha = energy * .75; sctx.strokeStyle = '#e0fbff'; sctx.lineWidth = 2 + energy * 3.2;
      sctx.shadowColor = '#5ee7ff'; sctx.shadowBlur = 22;
      sctx.beginPath(); sctx.arc(0, 0, r * (1 + (1 - energy) * 2.4), 0, 6.2832); sctx.stroke(); sctx.shadowBlur = 0;
    }
    if (!still && !small) {
      if (now > nextArc) {
        // 从暗核边缘沿径向打到盘上：起点取终点方向上的光子环，整条电弧都在暗核外面，不会划过时钟数字。
        const a = Math.random() * 6.2832, e = 1.55 + Math.random() * 1.15, pts = [];
        const [ex, ey] = onDisk(1, e, a), b = Math.atan2(ey, ex) + (Math.random() - .5) * .45, sx = Math.cos(b) * 1.03, sy = Math.sin(b) * 1.03;
        for (let i = 0; i <= 10; i++) {
          const k = i / 10, jit = i && i < 10 ? (Math.random() - .5) * .16 : 0;
          const x = sx * (1 - k) + ex * k, y = sy * (1 - k) + ey * k;
          pts.push([x - (ey - sy) * jit, y + (ex - sx) * jit]);
        }
        arc = { start: now, pts }; nextArc = now + 2400 + Math.random() * 3600;
      }
      if (arc && now - arc.start < 260) {
        sctx.globalAlpha = .58 + .42 * Math.random(); sctx.strokeStyle = '#e6f9ff'; sctx.lineWidth = 1.35;
        sctx.shadowColor = '#5ee7ff'; sctx.shadowBlur = 12;
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
    if (!light && !root.classList.contains('wall-on')) {
      // 深色无壁纸场景：在星场底层铺两团缓慢随视差呼吸的深空星云，让星核与网格融入电影感深空
      const nx = sw * .5 + spx * .4, ny = sh * .24 + spy * .4, nr = Math.max(sw, sh) * .48;
      const ng = sctx.createRadialGradient(nx, ny, 0, nx, ny, nr);
      ng.addColorStop(0, 'rgba(94,231,255,.09)'); ng.addColorStop(.45, 'rgba(167,139,250,.07)'); ng.addColorStop(1, 'rgba(6,8,18,0)');
      sctx.fillStyle = ng; sctx.fillRect(0, 0, sw, sh);
    }
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
      } else {
        sctx.fillStyle = color; sctx.beginPath(); sctx.arc(x, y, size, 0, 6.2832); sctx.fill();
        if (!light && !phone() && near > .82) {   // 近景亮星带微弱十字星芒
          sctx.strokeStyle = color; sctx.lineWidth = .65; sctx.globalAlpha = alpha * .38;
          const arm = size * 2.6;
          sctx.beginPath(); sctx.moveTo(x - arm, y); sctx.lineTo(x + arm, y); sctx.moveTo(x, y - arm); sctx.lineTo(x, y + arm); sctx.stroke();
        }
      }
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

  // 卡片聚光：鼠标在卡片 / 面板上移动时，把指针在卡片里的位置写进 --mx / --my，CSS 在那里画一团冷光。
  // 只桌面鼠标、科幻特效开着、没要求减弱动态时；每帧最多写一次，只写指针下面那一张。
  const SPOT = '.panel, .bookmark-card, .deck-card, .home-widget, .link-card, .ov-item';
  let spotEl = null, spotFrame = 0, spotX = 0, spotY = 0;
  document.addEventListener('pointermove', e => {
    if (e.pointerType !== 'mouse' || !desktop.matches || reduced.matches || !scifiOn()) return;
    const el = e.target instanceof Element ? e.target.closest(SPOT) : null;
    if (!el) return;
    spotEl = el; spotX = e.clientX; spotY = e.clientY;
    if (!spotFrame) spotFrame = requestAnimationFrame(() => {
      spotFrame = 0;
      const r = spotEl.getBoundingClientRect();
      spotEl.style.setProperty('--mx', Math.round(spotX - r.left) + 'px'); spotEl.style.setProperty('--my', Math.round(spotY - r.top) + 'px');
    });
  }, { passive: true });

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
  window.HaloMotion = { nav, change: scifiChange, page, homeEnter, cardToModal, warp: hubWarp, telemetry };
})();

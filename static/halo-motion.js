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
  // 透视网格地面（只在宽屏收藏首页显示，见 app-v3.css「科幻层 II」）：纯 CSS 动画，这里只放个空节点。
  const floor = document.createElement('div');
  floor.className = 'hub-floor'; floor.setAttribute('aria-hidden', 'true');
  stars.after(floor);
  // 电影镜头（见 app-v3.css「科幻层 III」）：胶片层（暗角 + 颗粒）纯 CSS；切镜层的遮幅黑边与镜头光束由 playCut 用 WAAPI 播。
  const film = document.createElement('div');
  film.className = 'hub-film'; film.setAttribute('aria-hidden', 'true');
  const cut = document.createElement('div');
  cut.className = 'hub-cut'; cut.setAttribute('aria-hidden', 'true');
  cut.innerHTML = '<i class="hub-cut__bar is-top"></i><i class="hub-cut__bar is-bottom"></i><i class="hub-cut__flare"></i>';
  document.body.append(film, cut);
  // 切镜：上下遮幅一合一开（中间那段停一下），同时一道横向光束从中间拉开再散掉。只是一层不接收指针的装饰，不挡操作。
  const playCut = (ms) => {
    if (typeof cut.animate !== 'function') return;
    const duration = Math.max(560, Math.min(ms, 900)), easing = 'cubic-bezier(.65, 0, .35, 1)';
    const [top, bottom, flare] = cut.children;
    // 只在播的时候挂出来：常驻的话即便黑边在屏幕外，也是一整屏的合成层（手机显存吃紧时会挤掉内容层）。
    cut.classList.add('is-on');
    [[top, '-101%'], [bottom, '101%']].forEach(([el, away]) => el.animate([
      { transform: `translateY(${away})` }, { transform: 'none', offset: .3 }, { transform: 'none', offset: .55 }, { transform: `translateY(${away})` },
    ], { duration, easing }));
    flare.animate([{ opacity: 0, transform: 'scaleX(0)' }, { opacity: 1, transform: 'scaleX(1)', offset: .4 }, { opacity: 0, transform: 'scaleX(1.15)' }],
      { duration, easing: 'ease-out' }).finished.then(() => { if (!cut.getAnimations({ subtree: true }).length) cut.classList.remove('is-on'); }, () => {});
  };
  // 星门用：遮幅合上后停住（等框里的应用说「好了」），开门时再拉开，拉开那一下带镜头光束。
  let cutHeld = false;
  const cutHold = (on) => {
    if (typeof cut.animate !== 'function' || on === cutHeld) return;
    cutHeld = on;
    const [top, bottom, flare] = cut.children, easing = 'cubic-bezier(.65, 0, .35, 1)';
    cut.getAnimations({ subtree: true }).forEach(a => a.cancel());
    cut.classList.add('is-on');
    cut.classList.toggle('is-hold', on);
    const bars = [[top, '-101%'], [bottom, '101%']].map(([el, away]) => el.animate(
      on ? [{ transform: `translateY(${away})` }, { transform: 'none' }] : [{ transform: 'none' }, { transform: `translateY(${away})` }],
      { duration: on ? 300 : 460, easing, fill: on ? 'forwards' : 'none' }));
    if (on) return;
    flare.animate([{ opacity: 0, transform: 'scaleX(0)' }, { opacity: 1, transform: 'scaleX(1)', offset: .35 }, { opacity: 0, transform: 'scaleX(1.15)' }], { duration: 520, easing: 'ease-out' });
    Promise.all(bars.map(a => a.finished)).then(() => { if (!cutHeld && !cut.getAnimations({ subtree: true }).length) cut.classList.remove('is-on'); }, () => {});
  };
  const sctx = stars.getContext && stars.getContext('2d');
  const TINTS = ['#ffffff', '#bfe9ff', '#9fd8ff', '#d7c8ff', '#ffe6c4'];
  let field = [], sw = 0, sh = 0, sframe = 0, slast = 0, warpStart = 0, warpUntil = 0, spx = 0, spy = 0, stx = 0, sty = 0;
  let meteor = null, nextMeteor = performance.now() + 5000;
  const phone = () => !desktop.matches;
  const spawn = (far) => ({ x: (Math.random() - .5) * sw / (Math.max(sw, sh) * .55), y: (Math.random() - .5) * sh / (Math.max(sw, sh) * .55), z: far ? 1 : .15 + Math.random() * .85, tint: TINTS[(Math.random() * TINTS.length) | 0], ph: Math.random() * 6.28 });
  // 画布高度按 CSS 的 100lvh（地址栏收起时的最大视口）：手机上滚动时地址栏伸缩、弹出键盘都会触发 resize，
  // 尺寸没变就不重建画布——重建会清空画面、星场中心跟着跳，滚动时看着一闪一抖。
  const sizeStars = () => {
    if (!sctx) return;
    const w = innerWidth, h = stars.clientHeight || innerHeight;
    if (w === sw && h === sh) return;
    sw = w; sh = h;
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
  // 只在电脑上画：星核跟着时钟走、却画在固定的画布上，触屏的原生滚动在合成线程里跑，画布每帧都慢半拍，
  // 手机上滚动时整团光拖在时钟后面一顿一顿地抖。
  const clockEl = document.getElementById('heroClock');
  const DISK_TINTS = ['#e6fbff', '#7fe3ff', '#a78bfa', '#f472d0'];
  const flow = Array.from({ length: 220 }, () => {
    const e = 1.22 + Math.random() ** 1.4 * 2.3;
    return { a: Math.random() * 6.2832, e, lift: (Math.random() - .5) * .05, len: .1 + Math.random() * .16, tint: DISK_TINTS[Math.min(3, ((e - 1.22) / .6) | 0)] };
  });
  const RAYS = Array.from({ length: 72 }, (_, i) => ({ a: i / 72 * 6.2832 + Math.random() * .05, len: .2 + Math.random() ** 2 * 1.2, ph: Math.random() * 6.28 }));
  const DISK_TILT = -.08, TC = Math.cos(DISK_TILT), TS = Math.sin(DISK_TILT);
  const DISK_FLAT = .3;
  let reactorLast = 0, arc = null, nextArc = 0, reactorSeen = null;
  const onDisk = (r, e, a, lift = 0) => {
    const x = Math.cos(a) * e * r, y = (Math.sin(a) * e * DISK_FLAT + lift) * r;
    return [x * TC - y * TS, x * TS + y * TC];
  };
  const reactorAnchor = () => {
    if (root.dataset.wallpaper !== 'starcore' || !clockEl || phone()) return null;
    const b = clockEl.getBoundingClientRect();
    if (!b.width || b.bottom < -b.height * 3 || b.top > sh) return null;
    // 暗核要装得下整排数字和下面那行问候。
    return { x: b.left + b.width / 2, y: b.top + b.height / 2, r: Math.max(b.height * 1.3, b.width * .64) };
  };
  const drawReactor = (now, energy) => {
    const at = reactorAnchor();
    if (!at) return;
    reactorSeen = { ...at, t: performance.now() };   // 星门从这里起飞：切页那一刻时钟已经藏起来了，用最后画过的那一帧
    const r = at.r, t = now / 1000, dt = reactorLast && t > reactorLast ? Math.min(.1, t - reactorLast) : 0;
    const still = stillScene();
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
      sctx.lineWidth = 1.6;
      for (const p of flow) {
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
    sctx.lineWidth = 1; sctx.strokeStyle = '#9fe9ff';
    for (const ray of RAYS) {
      const a = ray.a + t * .025, len = ray.len * (.65 + .35 * Math.sin(t * 1.3 + ray.ph));
      sctx.globalAlpha = .08 * lift;
      sctx.beginPath(); sctx.moveTo(Math.cos(a) * r * 1.06, Math.sin(a) * r * 1.06);
      sctx.lineTo(Math.cos(a) * r * (1.06 + len), Math.sin(a) * r * (1.06 + len)); sctx.stroke();
    }
    g = sctx.createLinearGradient(-r * 1.2, 0, r * 1.2, 0);
    g.addColorStop(0, '#5ee7ff'); g.addColorStop(.45, '#f2fbff'); g.addColorStop(1, '#f472d0');
    sctx.strokeStyle = g; sctx.shadowColor = '#a78bfa'; sctx.shadowBlur = 16;
    sctx.lineWidth = 2.4; sctx.globalAlpha = (.55 + .3 * breathe) * Math.min(1.4, boost);
    sctx.beginPath(); sctx.ellipse(0, -r * .04, r * 1.17, r * 1.1, 0, Math.PI * 1.04, Math.PI * 1.96); sctx.stroke();
    sctx.lineWidth = 1.2; sctx.globalAlpha *= .5;
    sctx.beginPath(); sctx.ellipse(0, r * .02, r * 1.08, r * 1.04, 0, Math.PI * .1, Math.PI * .9); sctx.stroke();
    sctx.shadowBlur = 0;
    sctx.globalCompositeOperation = 'source-over';
    g = sctx.createRadialGradient(0, 0, 0, 0, 0, r * 1.03);
    g.addColorStop(0, 'rgba(2,3,10,.95)'); g.addColorStop(.85, 'rgba(3,4,14,.92)'); g.addColorStop(1, 'rgba(3,4,14,0)');
    sctx.globalAlpha = 1; sctx.fillStyle = g; sctx.beginPath(); sctx.arc(0, 0, r * 1.03, 0, 6.2832); sctx.fill();
    sctx.globalCompositeOperation = 'lighter';
    sctx.lineWidth = 1.4; sctx.strokeStyle = '#dff8ff'; sctx.shadowColor = '#5ee7ff'; sctx.shadowBlur = 12;
    sctx.globalAlpha = (.5 + .35 * breathe) * Math.min(1.4, boost);
    sctx.beginPath(); sctx.arc(0, 0, r, 0, 6.2832); sctx.stroke(); sctx.shadowBlur = 0;
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
    if (!still) {
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
  // 只拉星场（星星拉成线、星核增亮），不切镜：星门自己管遮幅。
  const warpStars = (ms) => {
    if (stillScene() || !sceneAllowed() || document.hidden) return false;
    warpStart = performance.now(); warpUntil = warpStart + ms; startStars();
    return true;
  };
  const hubWarp = (ms = 900) => { if (warpStars(ms)) playCut(ms); };
  if (sctx) {
    sizeStars();
    window.addEventListener('resize', () => { sizeStars(); startStars(); }, { passive: true });
    // 从别的标签页（多半是刚点开的网址）回来：离开超过 1.5 秒就补一个回场镜头。
    let hiddenAt = 0;
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { hiddenAt = performance.now(); stopStars(); return; }
      startStars();
      if (hiddenAt && performance.now() - hiddenAt > 1500) hubWarp(700);
    });
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
      else { sizeStars(); startStars(); }   // 刚打开科幻特效时画布才有 CSS 尺寸
    }).observe(root, { attributes: true, attributeFilter: ['class', 'data-wallpaper', 'data-theme'] });
    syncScene();
  }

  // 卡片聚光：鼠标在卡片 / 面板上移动时，把指针在卡片里的位置写进 --mx / --my，CSS 在那里画一团冷光。
  // 只桌面鼠标、科幻特效开着、没要求减弱动态时；每帧最多写一次，只写指针下面那一张。
  const SPOT = '.panel, .bookmark-card, .deck-card, .home-widget';
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

  // ===== 星门：进「AI 聊天」/「笔记」=====
  // 照着「启动动画」的分镜：① 离港——星核（电脑、星核壁纸、从首页出发时）先涨一下再坍缩成一个光点，
  // 不在首页 / 触屏时由侧栏图标亮起发射，光点拖着尾迹飞到内容区中央，遮幅合上停住，星场跃迁；
  // ② 成形——光点处画出星核同款刻度环，再一笔笔画出目的地的签名线稿：HaloWebUI 是它登录页上那组
  // 黄金角星座（HaloConstellation.svelte 同一套几何），笔记是一颗神经晶体；读数只写真实发生的事
  // （签票、框加载、框里的应用说「好了」，带实测耗时）；没好就一直在这里转着等；
  // ③ 点火——就绪后线稿上色、中心光核点亮、冲击波；④ 开门——刻度环放大成光圈冲出去，从中心露出下面的框，
  // 开到一小半时才告诉框「有人进来了」，框里接着播自己的入场。
  // 每次都播：框还没开过（冷）时等真实就绪，开过的（热）压到一秒以内。点一下 / Esc 直接开门；
  // 折叠屏开合（宽度档位变了）、切去别的页面：立即收尾。只盖在框所在的那块舞台上，侧栏照常能点。
  // 科幻特效关、高对比时不播（加载层照样等就绪再撤，见 index.html）；减弱动态效果 / 省流量时只显示静止的一帧。
  const GATE_T = {   // [离港, 成形（至少）, 点火, 开门]，毫秒
    desk: { cold: [320, 900, 340, 560], warm: [170, 300, 170, 400] },
    touch: { cold: [280, 800, 300, 420], warm: [140, 240, 140, 320] },
    still: { cold: [0, 300, 0, 200], warm: [0, 350, 0, 200] },
  };
  const GATE_CAP = 10000;   // 十秒还没就绪：先开门，框上的加载层自己接着等
  const GATE_GLYPHS = '01<>/\\|=+*#%ΣΔΛΞΨΩ░▒▓';   // WebObsidian 片头解码标题用的同一组字符
  const f1 = n => Math.round(n * 10) / 10;
  const haloGlyph = (n) => {
    const pts = Array.from({ length: n }, (_, i) => {
      const a = i * 2.39996, r = (.1 + Math.sqrt((i + 1) / n) * .32) * 330;
      return [f1(Math.cos(a) * r), f1(Math.sin(a) * r)];
    });
    const edges = [];
    for (let i = 1; i < n; i++) {
      const [ax, ay] = pts[i], [bx, by] = pts[(i - 1) >> 1], my = f1((ay + by) / 2);
      edges.push(`M${ax} ${ay}C${ax} ${my} ${bx} ${my} ${bx} ${by}`);
    }
    return { edges, nodes: pts.map(([x, y], i) => [x, y, i % 5 ? 2.8 : 4.6, i % 5 ? 'a' : 'b']), core: [0, 0], pulses: [n - 2, n - 4, n - 7, n - 10] };
  };
  const CRYSTAL = { T: [0, -138], L: [-92, -22], ML: [-36, -44], MR: [38, -40], R: [94, -18], LL: [-58, 52], LR: [60, 56], C: [2, 4], B: [0, 140] };
  const CRYSTAL_EDGES = ['T-ML', 'T-MR', 'T-L', 'T-R', 'L-ML', 'ML-MR', 'MR-R', 'ML-C', 'MR-C', 'L-LL', 'R-LR', 'C-LL', 'C-LR', 'LL-B', 'LR-B', 'C-B'];
  const CRYSTAL_SYNAPSES = [[-158, -84, 'L'], [156, -74, 'R'], [-146, 98, 'LL'], [150, 102, 'LR'], [-34, -176, 'T'], [44, 178, 'B']];
  const vaultGlyph = (narrow) => {
    const line = ([ax, ay], [bx, by]) => `M${ax} ${ay}L${bx} ${by}`;
    const edges = CRYSTAL_EDGES.map(k => line(...k.split('-').map(v => CRYSTAL[v])));
    const nodes = Object.entries(CRYSTAL).filter(([k]) => k !== 'C').map(([k, [x, y]]) => [x, y, k === 'T' || k === 'B' ? 4.6 : 3.4, k === 'T' || k === 'B' ? 'b' : 'a']);
    if (!narrow) CRYSTAL_SYNAPSES.forEach(([x, y, to]) => { edges.push(line([x, y], CRYSTAL[to])); nodes.push([x, y, 2.4, 'a']); });
    return { edges, nodes, core: CRYSTAL.C, pulses: [0, 3, 9, 15] };
  };
  const gateSvg = (glyph) => {
    const [cx, cy] = glyph.core;
    return '<svg class="hub-gate__svg" viewBox="-200 -200 400 400">'
      + '<circle class="hub-gate__ticks" r="184" pathLength="360"/><circle class="hub-gate__ring" r="170" pathLength="100"/>'
      + '<circle class="hub-gate__ring is-inner" r="156" pathLength="100"/><g class="hub-gate__glyph">'
      + glyph.edges.map((d, i) => `<path class="e" d="${d}" pathLength="1" style="--i:${i}"/>`).join('')
      + glyph.pulses.map((k, i) => `<path class="p" d="${glyph.edges[k]}" pathLength="1" style="--i:${i}"/>`).join('')
      + glyph.nodes.map(([x, y, r, c], i) => `<circle class="n ${c}" cx="${x}" cy="${y}" r="${r}" style="--i:${i}"/>`).join('')
      + `</g><circle class="hub-gate__shock" cx="${cx}" cy="${cy}" r="24"/><circle class="hub-gate__core" cx="${cx}" cy="${cy}" r="10"/></svg>`;
  };
  const gateTier = () => desktop.matches ? 'desk' : innerWidth <= 760 ? 'narrow' : 'touch';
  const elapsed = (ms) => ms < 1000 ? Math.round(ms) + 'ms' : (ms / 1000).toFixed(2) + 's';
  let gateCur = null;
  const gateLine = (g, text, state = 'OK', timed = false) => {
    const p = document.createElement('p');
    if (state === 'WARN' || state === 'WAIT') p.className = 'is-warn';
    const parts = [['span', '> ' + text], ['i', ''], ['b', state], ['em', timed ? elapsed(performance.now() - g.start) : '']];
    parts.forEach(([tag, value]) => { const el = document.createElement(tag); el.textContent = value; p.append(el); });
    g.lines.append(p);
  };
  // 笔记：标题和 WebObsidian 片头一样，从乱码里一位位解出来。
  const gateDecode = (g, text, ms) => {
    const span = g.el.querySelector('.hub-gate__title span'), t0 = performance.now();
    const tick = () => {
      if (gateCur !== g || !span) return;
      const p = Math.min(1, (performance.now() - t0) / ms), done = Math.floor(p * text.length);
      span.textContent = text.slice(0, done) + [...text.slice(done)].map(c => c === ' ' ? ' ' : GATE_GLYPHS[(Math.random() * GATE_GLYPHS.length) | 0]).join('');
      if (p < 1) g.timers.push(setTimeout(tick, 45));
    };
    tick();
  };
  // 离港：起点是星核（刚画过的那一帧）或侧栏图标，光点飞到舞台中央。一层固定的装饰，播完即删。
  const gateLaunch = (g, tab) => {
    const to = g.stage.getBoundingClientRect();
    const tx = to.left + to.width / 2, ty = to.top + to.height / 2;
    const seen = reactorSeen && performance.now() - reactorSeen.t < 400 ? reactorSeen : null;
    const ib = tab && tab.getBoundingClientRect ? tab.getBoundingClientRect() : null;
    const from = seen ? { x: seen.x, y: seen.y, r: seen.r * 1.1, core: true }
      : ib && ib.width ? { x: ib.left + ib.width / 2, y: ib.top + ib.height / 2, r: Math.max(ib.width, ib.height) * .5 } : null;
    if (!from || !to.width) return;
    gateFlight(from, { x: tx, y: ty }, g.t.launch, from.core);
  };
  const gateFlight = (from, to, d, core, landing) => {
    const fly = document.createElement('div');
    fly.className = 'hub-gate-flight' + (core ? ' is-core' : ''); fly.setAttribute('aria-hidden', 'true');
    fly.innerHTML = '<i class="hub-gate-flight__core"></i><i class="hub-gate-flight__spark"></i>';
    document.body.append(fly);
    const [orb, spark] = fly.children, node = landing ? to : from;
    Object.assign(orb.style, { left: node.x - node.r + 'px', top: node.y - node.r + 'px', width: node.r * 2 + 'px', height: node.r * 2 + 'px' });
    orb.animate(landing
      ? [{ transform: 'scale(0)', opacity: 0 }, { transform: 'scale(0)', opacity: 0, offset: .7 }, { transform: 'scale(1.25)', opacity: 1, offset: .85 }, { transform: 'scale(1)', opacity: 0 }]
      : [{ transform: 'scale(1)', opacity: .9 }, { transform: 'scale(1.22)', opacity: 1, offset: .3 }, { transform: 'scale(.04)', opacity: 1, offset: .8 }, { transform: 'scale(0)', opacity: 0 }],
    { duration: d * (landing ? 1.4 : 1.1), easing: 'cubic-bezier(.5, 0, .2, 1)', fill: 'forwards' });
    const ang = Math.atan2(to.y - from.y, to.x - from.x);
    const at = (x, y, s) => `translate(${x}px,${y}px) rotate(${ang}rad) scaleX(${s})`;
    spark.animate([
      { transform: at(from.x, from.y, .05), opacity: 0 }, { transform: at(from.x, from.y, .2), opacity: 1, offset: landing ? .08 : .45 },
      { transform: at(to.x, to.y, 1.4), opacity: 1, offset: landing ? .72 : .92 }, { transform: at(to.x, to.y, .1), opacity: 0 },
    ], { duration: d * 1.35, easing: 'cubic-bezier(.6, 0, .3, 1)', fill: 'forwards' });
    setTimeout(() => fly.remove(), d * 1.45 + 120);
  };
  const gateDrop = (g = gateCur) => {
    if (!g) return;
    g.timers.forEach(clearTimeout);
    window.removeEventListener('keydown', g.key);
    g.el.remove();
    if (gateCur === g) { gateCur = null; cutHold(false); }
  };
  const gateOpen = (g) => {
    if (gateCur !== g || g.phase === 'open') return;
    g.phase = 'open';
    const svg = g.el.querySelector('.hub-gate__svg').getBoundingClientRect(), box = g.el.getBoundingClientRect();
    g.el.style.setProperty('--gate-cx', Math.round(svg.left + svg.width / 2 - box.left) + 'px');
    g.el.style.setProperty('--gate-cy', Math.round(svg.top + svg.height / 2 - box.top) + 'px');
    g.el.classList.add('is-open');
    cutHold(false);
    window.removeEventListener('keydown', g.key);
    g.timers.push(setTimeout(() => { const fn = g.onOpen; g.onOpen = null; if (fn) fn(); }, g.t.open * .35));
    g.timers.push(setTimeout(() => gateDrop(g), g.t.open + 80));
  };
  const gateIgnite = (g) => {
    if (gateCur !== g || g.phase !== 'draw') return;
    g.phase = 'ignite';
    g.el.classList.add('is-ignite');
    g.el.classList.toggle('is-fail', Boolean(g.fail));
    if (!g.still) warpStars(g.t.ignite + 420);
    g.timers.push(setTimeout(() => gateOpen(g), g.t.ignite));
  };
  const gateReady = (g, fail = '') => {
    if (g.ready) return;
    g.ready = true; g.fail = fail;
    g.timers.push(setTimeout(() => gateIgnite(g), Math.max(0, g.start + g.t.launch + g.t.draw - performance.now())));
  };
  const gateEnter = (name, opts = {}) => {
    const stage = document.getElementById(name === 'chat' ? 'chatStage' : 'vaultStage');
    if (!stage || !sceneAllowed() || document.hidden) return false;
    gateDrop();
    const still = stillScene(), tier = gateTier(), warm = Boolean(opts.ready);
    const [launch, draw, ignite, open] = GATE_T[still ? 'still' : tier === 'desk' ? 'desk' : 'touch'][warm ? 'warm' : 'cold'];
    const glyph = name === 'chat' ? haloGlyph(tier === 'narrow' ? 14 : 22) : vaultGlyph(tier === 'narrow');
    const title = name === 'chat' ? 'HALO · WEBUI' : 'NEURAL · VAULT';
    const el = document.createElement('div');
    el.className = `hub-gate is-${name} is-${tier}` + (warm ? ' is-warm' : '') + (still ? ' is-still' : '');
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '<div class="hub-gate__rays"></div><div class="hub-gate__grid"></div><div class="hub-gate__scan"></div><div class="hub-gate__flash"></div>'
      + gateSvg(glyph) + `<div class="hub-gate__title" data-text="${title}"><span>${title}</span></div><div class="hub-gate__lines"></div>`
      + `<div class="hub-gate__skip">${tier === 'desk' ? 'CLICK TO SKIP' : 'TAP TO SKIP'}</div>`;
    [['--gate-launch', launch], ['--gate-draw', draw], ['--gate-ignite', ignite], ['--gate-open', open], ['--gate-step', Math.round(draw * .55 / glyph.edges.length)]]
      .forEach(([k, v]) => el.style.setProperty(k, v + 'ms'));
    stage.append(el);
    const g = { name, el, stage, tier, still, start: performance.now(), t: { launch, draw, ignite, open }, ready: false, fail: '', phase: 'draw', timers: [], onOpen: opts.onOpen, lines: el.querySelector('.hub-gate__lines') };
    gateCur = g;
    (opts.lines || []).forEach(([text, state]) => gateLine(g, text, state));
    g.key = (e) => { if (e.key === 'Escape') gateOpen(g); };
    el.addEventListener('click', () => gateOpen(g));
    window.addEventListener('keydown', g.key);
    if (!still) {
      gateLaunch(g, opts.tab);
      cutHold(true);
      warpStars(launch + 560);
      if (name === 'vault') gateDecode(g, title, launch + draw * .6);
    }
    g.timers.push(setTimeout(() => { if (!g.ready) { gateLine(g, '链路较慢，先打开', 'WAIT', true); gateReady(g); } }, GATE_CAP));
    if (warm) gateReady(g);
    return true;
  };
  // 回程：光点从内容区中央飞回时钟（星核）或侧栏图标，落点处星核重新点火。
  // 等这一轮页面更新做完（切页的 update 里接着会打开收藏首页）再量位置；不用 requestAnimationFrame——
  // View Transition 进行中它会被拖到转场结束以后，光点就晚了半秒。
  const gateBack = (tab) => {
    gateDrop();
    if (!sceneAllowed() || stillScene() || document.hidden) return;
    queueMicrotask(() => {
      const main = document.querySelector('.main'), m = main ? main.getBoundingClientRect() : null;
      const c = clockEl ? clockEl.getBoundingClientRect() : null, ib = tab && tab.getBoundingClientRect ? tab.getBoundingClientRect() : null;
      const clock = c && c.width && c.bottom > 0 && c.top < innerHeight;
      const to = clock ? { x: c.left + c.width / 2, y: c.top + c.height / 2, r: Math.max(c.height * 1.3, c.width * .64) }
        : ib && ib.width ? { x: ib.left + ib.width / 2, y: ib.top + ib.height / 2, r: Math.max(ib.width, ib.height) * .5 } : null;
      if (!m || !m.width || !to) return;
      const d = desktop.matches ? 380 : 320;
      gateFlight({ x: m.left + m.width / 2, y: m.top + m.height / 2 }, to, d, clock && root.dataset.wallpaper === 'starcore', true);
      setTimeout(() => hubWarp(700), d * .95);
    });
  };
  window.addEventListener('resize', () => { if (gateCur && gateCur.tier !== gateTier()) gateOpen(gateCur); }, { passive: true });
  document.addEventListener('visibilitychange', () => { if (document.hidden && gateCur) gateOpen(gateCur); });
  const gate = {
    enter: gateEnter,
    // 框里的应用说「好了」（fail：没能免登录，开门露出登录页）。返回 false = 没有门在等它，调用方自己告诉框。
    ready: (name, fail = '') => {
      const g = gateCur;
      if (!g || g.name !== name) return false;
      if (fail) gateLine(g, fail, 'WARN', true);
      gateReady(g, fail);
      return true;
    },
    step: (name, text, state = 'OK') => { if (gateCur && gateCur.name === name && gateCur.phase === 'draw') gateLine(gateCur, text, state, true); },
    leave: () => gateDrop(),
    back: gateBack,
  };

  // 切页时星场跃迁一下（和 View Transition 同时发生）。进聊天 / 笔记由星门接手（不再叠 View Transition 和切镜）；
  // 从这两页回收藏库：旧画面收成一个光圈（gate-back，见 app-v3.css），回程镜头由 switchView 调 gate.back。
  const plainChange = change;
  const FRAME_VIEWS = ['chat', 'vault'];
  const scifiChange = (update, name) => {
    if (name === previous || !sceneAllowed()) { if (name !== previous) hubWarp(650); plainChange(update, name); return; }
    if (FRAME_VIEWS.includes(name)) { transition?.skipTransition(); update(); return; }
    if (name === 'bookmarks' && FRAME_VIEWS.includes(previous)) {
      root.classList.add('gate-back');
      setTimeout(() => root.classList.remove('gate-back'), 900);
      plainChange(update, name);
      return;
    }
    hubWarp(650); plainChange(update, name);
  };
  window.HaloMotion = { nav, change: scifiChange, page, homeEnter, cardToModal, warp: hubWarp, telemetry, gate };
})();

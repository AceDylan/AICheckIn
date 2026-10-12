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
    // 光圈接力在播时不放：暗幕底下看不见，还会让光圈的落点（框里光圈的位置）在量的那一刻偏开
    if (previous !== tab.dataset.view && !transition && !iris) animate(view, [{ opacity: 0, transform: `translateX(${direction * 8}px) scale(.985)` }, { opacity: 1, transform: 'none' }]);
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
  // 刻度环 / 虚线环 / 渐变光弧 / 轨道光点：首页的反应堆和光圈接力里飞的那一圈是同一套（外圈刻度半径 ≈ 184）。
  // 渐变各用各的 id：窄屏上反应堆是 display: none，引用藏起来的那份渐变会画不出来。
  const reactorSvg = (arcId) => `<svg class="hub-reactor__svg" viewBox="-200 -200 400 400"><defs><linearGradient id="${arcId}" x1="0" y1="0" x2="1" y2="1">`
    + '<stop offset="0" stop-color="#5ee7ff"/><stop offset=".6" stop-color="#a78bfa"/><stop offset="1" stop-color="#f472d0"/></linearGradient></defs>'
    + `<g class="hub-reactor__ticks">${Array.from({ length: 120 }, (_, i) => `<line y1="${i % 10 ? -180 : -187}" y2="-172" transform="rotate(${i * 3})"${i % 10 ? '' : ' class="major"'}/>`).join('')}</g>`
    + '<circle class="hub-reactor__dash" r="152"/>'
    + `<g class="hub-reactor__arcs" stroke="url(#${arcId})"><circle r="128" pathLength="100" stroke-dasharray="18 82"/>`
    + '<circle r="128" pathLength="100" stroke-dasharray="9 91" stroke-dashoffset="-40"/><circle r="128" pathLength="100" stroke-dasharray="14 86" stroke-dashoffset="-68"/></g>'
    + '<g class="hub-reactor__orbit"><circle cy="-140" r="2.6"/><circle cy="140" r="1.6"/></g>'
    + '<g class="hub-reactor__orbit is-slow"><circle cx="-164" r="2"/></g></svg>';
  const hero = document.querySelector('.home-hero');
  const hudSlots = {};
  if (hero) {
    const reactor = document.createElement('div');
    reactor.className = 'hub-reactor'; reactor.setAttribute('aria-hidden', 'true');
    reactor.innerHTML = '<div class="hub-reactor__sweep"></div>' + reactorSvg('hubReactorArc')
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
  let reactorLast = 0, arc = null, nextArc = 0;
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
  // 只拉星场（星星拉成线、星核增亮），不切镜：光圈接力回首页时用它让星核重新点火。
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

  // ===== 光圈接力：收藏首页 ⇄ AI 聊天 / 笔记（外壳 v81，用户 2026-10-12 定的分镜）=====
  // ① 变暗：整屏暗下去，只剩出发处的光圈——收藏首页是时钟外那圈星核刻度环（电脑上就是 .hub-reactor 本身，
  //    从暗幕的孔里露出来，后半段换成一模一样的一圈接手；触屏没有它，就在时钟外亮起同样一圈）；
  //    聊天是 HaloWebUI 新对话页中间的光圈（位置由框里报来，见 index.html 的 frameBeacon）；笔记是舞台中央的星光。
  // ② 接力：页面在全暗时才换，光圈拖着尾迹沿弧线飞到目的地的光圈上——进聊天落在新对话光圈上，进笔记落在中央，
  //    回首页落回星核；框还没好（冷进入）就先停在舞台中央转着等，下面的读数只写真实发生的事。
  // ③ 点亮：落点的光圈亮起——暗幕在那里开孔露出真身，一团辉光 + 冲击波（笔记是一颗十字星芒），这时才告诉框「有人进来了」；
  // ④ 照亮：孔带着一圈光边扩到整屏，暗幕散去。
  // 点一下 / Esc 直接收尾（该换的页先换上，点在侧栏标签上照样切过去）；折叠屏开合、切去后台：立即收尾。
  // 科幻特效关、高对比时不播；减弱动态效果 / 省流量：暗幕一淡一出，不飞。
  const IRIS_T = {   // [变暗, 接力, 点亮, 照亮]，毫秒
    desk: [640, 760, 440, 800],
    touch: [540, 640, 400, 700],
  };
  const IRIS_CAP = 10000;    // 十秒还没就绪：先照亮，框上的加载层自己接着等
  const RING_R = 184;        // 刻度环在 400×400 的光圈元素里的半径
  const FRAME_VIEWS = ['chat', 'vault'];
  let iris = null, irisSkipping = false;
  const irisTier = () => desktop.matches ? 'desk' : innerWidth <= 760 ? 'narrow' : 'touch';
  const ease = t => t < .5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
  const rectOf = el => { const b = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null; return b && b.width && b.bottom > 0 && b.top < innerHeight ? b : null; };
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  // 各处的光圈：{ x, y, r（刻度环半径，px）}，kind = reactor（首页星核）/ orb（HaloWebUI 光圈）/ star（舞台中央的星光）/ icon。
  const homeAperture = () => {
    const re = document.querySelector('.home-hero .hub-reactor'), b = rectOf(re);
    // 回到首页时反应堆正放入场动画（缩放 + 旋转）：圆心照取，尺寸用没变形的 offsetWidth。
    if (b && re.offsetWidth) return { x: b.left + b.width / 2, y: b.top + b.height / 2, r: re.offsetWidth / 400 * RING_R, kind: 'reactor', el: re };
    const c = rectOf(clockEl);
    if (c) return { x: c.left + c.width / 2, y: c.top + c.height / 2, r: Math.min(Math.max(c.height * 1.9, c.width * .66), Math.min(innerWidth, innerHeight) * .42), kind: 'reactor' };
    return null;
  };
  const stageAperture = (name) => {
    const b = rectOf(document.getElementById(name === 'chat' ? 'chatStage' : 'vaultStage')) || { left: 0, top: 0, width: innerWidth, height: innerHeight };
    return { x: b.left + b.width / 2, y: b.top + b.height / 2, r: Math.max(56, Math.min(120, Math.min(b.width, b.height) * .15)), kind: 'star' };
  };
  const iconAperture = (tab) => {
    const b = rectOf(tab);
    return b ? { x: b.left + b.width / 2, y: b.top + b.height / 2, r: Math.max(b.width, b.height) * .8, kind: 'icon' } : null;
  };
  // HaloWebUI 报来的是它那颗光圈（直径 ≈ 88px）本身：刻度环套在它外面一圈。
  const orbAperture = (b) => b && b.r > 4 ? { x: b.x, y: b.y, r: b.r * 1.6, kind: 'orb' } : null;
  const askBeacon = (ir, name, ms) => (name === 'chat' && ir.beacon ? Promise.resolve(ir.beacon(name, ms)).catch(() => null) : Promise.resolve(null)).then(orbAperture);
  const apertureOf = async (ir, view) => {
    if (view === 'bookmarks') return homeAperture() || iconAperture(ir.tab);
    if (FRAME_VIEWS.includes(view)) return (await askBeacon(ir, view, 0)) || stageAperture(view);
    return iconAperture(ir.tab);
  };
  const place = (ap, k = 1) => `translate(${f1(ap.x)}px,${f1(ap.y)}px) scale(${(ap.r * k / RING_R).toFixed(4)})`;
  const f1 = n => Math.round(n * 10) / 10;
  // 暗幕：一层径向渐变，孔内透明、孔边一圈光、孔外是暗幕。孔的位置 / 大小 / 光边每帧写进变量。
  const hole = (ir, x, y, r, feather, rim = 0) => {
    const s = ir.veil.style;
    s.setProperty('--ix', f1(x) + 'px'); s.setProperty('--iy', f1(y) + 'px');
    s.setProperty('--ir', f1(Math.max(0, r)) + 'px'); s.setProperty('--if', f1(feather) + 'px'); s.setProperty('--irim', rim.toFixed(3));
  };
  // 按帧推进；这一轮被收掉（或切到后台再回来）就返回 false，调用方就此停手。
  const tween = (ir, ms, fn) => new Promise(done => {
    const t0 = performance.now();
    const step = (now) => {
      if (iris !== ir) { done(false); return; }
      const p = Math.min(1, (now - t0) / ms);
      fn(p);
      if (p < 1) ir.raf = requestAnimationFrame(step); else done(true);
    };
    ir.raf = requestAnimationFrame(step);
  });
  // 换页后新页面还在入场（舞台淡入缩放、首页展开）：等它站稳了（连续两帧不动，最多 600ms）再量落点。
  const settle = (ir, el) => new Promise(done => {
    let last = '', same = 0;
    const t0 = performance.now();
    const step = () => {
      const b = el && el.getBoundingClientRect(), key = b ? [b.left, b.top, b.width, b.height].map(Math.round).join() : '';
      same = key === last ? same + 1 : 0; last = key;
      if (iris !== ir || same >= 2 || performance.now() - t0 > 600) done(); else ir.raf = requestAnimationFrame(step);
    };
    ir.raf = requestAnimationFrame(step);
  });
  const irisLine = (ir, text, state = 'OK', timed = false) => {
    const p = document.createElement('p');
    if (state === 'WARN' || state === 'WAIT') p.className = 'is-warn';
    [['span', '> ' + text], ['b', state], ['em', timed ? elapsed(performance.now() - ir.start) : '']]
      .forEach(([tag, value]) => { const el = document.createElement(tag); el.textContent = value; p.append(el); });
    ir.lines.append(p);
    while (ir.lines.children.length > (ir.tier === 'narrow' ? 2 : 3)) ir.lines.firstChild.remove();
  };
  const showHud = (ir, ap) => {
    ir.hud.style.left = f1(ap.x) + 'px'; ir.hud.style.top = f1(ap.y + ap.r * 1.2 + 14) + 'px';
    ir.el.classList.add('is-waiting');
  };
  const elapsed = (ms) => ms < 1000 ? Math.round(ms) + 'ms' : (ms / 1000).toFixed(2) + 's';
  // 光圈飞一段：沿二次贝塞尔弧线（偏离直线 14%），边飞边转、尺寸从出发处过渡到落点；尾迹跟着同一条弧线。
  const fly = (ir, a, b, ms) => {
    const dx = b.x - a.x, dy = b.y - a.y, bend = .14;
    const c = { x: (a.x + b.x) / 2 - dy * bend, y: (a.y + b.y) / 2 + dx * bend };
    const at = (t) => ({ x: (1 - t) ** 2 * a.x + 2 * (1 - t) * t * c.x + t * t * b.x, y: (1 - t) ** 2 * a.y + 2 * (1 - t) * t * c.y + t * t * b.y });
    const steps = [0, .15, .3, .45, .6, .75, .9, 1];
    const timing = { duration: ms, easing: 'cubic-bezier(.6, 0, .25, 1)', fill: 'forwards' };
    const flight = ir.ring.animate(steps.map(t => {
      const p = at(t), r = a.r + (b.r - a.r) * t, swell = 1 - .22 * Math.sin(t * Math.PI);   // 飞到半路收紧一点，像在加速
      return { transform: place({ ...p, r: r * swell }) + ` rotate(${Math.round(t * 140)}deg)` };
    }), timing);
    ir.trail.animate(steps.map(t => {
      const p = at(t), q = at(Math.min(1, t + .02)), ang = Math.atan2(q.y - p.y, q.x - p.x) || Math.atan2(dy, dx);
      return { transform: `translate(${f1(p.x)}px,${f1(p.y)}px) rotate(${ang.toFixed(3)}rad) scaleX(${(.1 + 1.3 * Math.sin(t * Math.PI)).toFixed(3)})`, opacity: t === 0 || t === 1 ? 0 : 1 };
    }), timing);
    ir.el.classList.add('is-flying');
    // 等动画真的播完（页面忙时它会晚几帧起步，按钟点算会在半路被下一段接走）；兜底多给 400ms
    return Promise.race([flight.finished, wait(ms + 400)]).catch(() => {}).then(() => { ir.el.classList.remove('is-flying'); return iris === ir; });
  };
  const irisReady = (ir, fail = '') => {
    if (ir.ready) return;
    ir.ready = true; ir.fail = fail;
    ir.el.classList.toggle('is-fail', Boolean(fail));
    ir.wake();
  };
  // 收尾：没换的页先换上；框已就绪就告诉它「有人进来了」（没就绪的话等它说好了，index.html 自己补）。
  const irisFinish = (ir = iris) => {
    if (!ir) return;
    if (iris === ir) iris = null;
    cancelAnimationFrame(ir.raf); ir.timers.forEach(clearTimeout);
    window.removeEventListener('keydown', ir.key);
    const update = ir.update; ir.update = null;
    if (update) { irisSkipping = true; try { update(); } finally { irisSkipping = false; } }
    const open = ir.ready && ir.onOpen; ir.onOpen = null;
    if (open) open();
    ir.el.remove();
  };
  const irisStart = (to, opts) => {
    irisFinish();
    const tier = irisTier(), still = stillScene();
    const el = document.createElement('div');
    el.className = `hub-iris is-${tier} to-${to}` + (still ? ' is-still' : '');
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '<div class="hub-iris__veil"></div><i class="hub-iris__trail"></i>'
      + `<div class="hub-iris__ring">${reactorSvg('hubIrisArc')}<i class="hub-iris__core"></i><i class="hub-iris__star"></i></div>`
      + '<i class="hub-iris__wave"></i><i class="hub-iris__bloom"></i>'
      + `<div class="hub-iris__hud"><b>${to === 'chat' ? 'HALO · WEBUI' : to === 'vault' ? 'NEURAL · VAULT' : 'BOOKMARK HUB'}</b><div></div></div>`;
    document.body.append(el);
    const [veil, trail, ring, wave, bloom, hud] = el.children;
    const ir = { to, el, veil, trail, ring, wave, bloom, hud, lines: hud.lastChild, tier, still, t: IRIS_T[tier === 'desk' ? 'desk' : 'touch'],
      update: opts.update || null, beacon: opts.beacon || null, tab: opts.tab, from: opts.from, start: performance.now(),
      ready: to === 'bookmarks', fail: '', onOpen: null, attached: !opts.update, raf: 0, timers: [], wake: () => {} };
    iris = ir;
    ir.key = (e) => { if (e.key === 'Escape') irisFinish(ir); };
    window.addEventListener('keydown', ir.key);
    el.addEventListener('click', (e) => {
      // 点在侧栏标签上：收尾后照样切过去。先认出底下是哪个标签——收尾会换页，侧栏跟着变样（首页是窄图标栏）。
      el.style.pointerEvents = 'none';
      const tab = document.elementFromPoint(e.clientX, e.clientY)?.closest?.('.tab');
      irisFinish(ir);
      if (tab && !tab.classList.contains('active')) tab.click();
    });
    ir.timers.push(setTimeout(() => { if (!ir.ready) { irisLine(ir, '链路较慢，先打开', 'WAIT', true); irisReady(ir); } }, IRIS_CAP));
    irisRun(ir);
    return ir;
  };
  const irisRun = async (ir) => {
    const alive = () => iris === ir;
    const [tDim, tFly, tFlare, tLight] = ir.t;
    // ① 变暗（已经换好页的——浏览器后退、框重开——只快速暗下来，从目的地附近起步）
    const switched = !ir.update;
    let at = switched ? (ir.to === 'bookmarks' ? homeAperture() : stageAperture(ir.to)) : await apertureOf(ir, ir.from);
    if (!alive()) return;
    at = at || stageAperture(ir.to);
    ir.el.classList.toggle('is-star', at.kind === 'star');
    ir.ring.style.transform = place(at);
    if (at.el) {   // 首页星核：这一圈和真反应堆转到同一个角度，换手时看不出接缝
      const mine = ir.ring.querySelectorAll('svg *'), real = at.el.querySelectorAll('svg *');
      mine.forEach((m, i) => { const a = real[i]?.getAnimations?.()[0], b = m.getAnimations?.()[0]; if (a && b && a.currentTime != null) b.currentTime = a.currentTime; });
    }
    const dim = switched ? 300 : ir.still ? 220 : tDim;
    hole(ir, at.x, at.y, at.r * 1.15, at.r * .7);
    ir.veil.animate([{ opacity: 0 }, { opacity: 1 }], { duration: dim, easing: 'cubic-bezier(.4, 0, .2, 1)', fill: 'forwards' });
    if (!ir.still) {
      ir.ring.animate(at.el ? [{ opacity: 0 }, { opacity: 0, offset: .5 }, { opacity: 1 }]
        : [{ opacity: 0, transform: place(at, .7) }, { opacity: 1, transform: place(at) }], { duration: dim, easing: 'ease-out', fill: 'forwards' });
    }
    // 孔在后半段合上：真反应堆隐去，只剩接手的这一圈
    if (!await tween(ir, dim, p => { const k = 1 - ease(Math.max(0, (p - .5) / .5)); hole(ir, at.x, at.y, at.r * 1.15 * k, at.r * .7 * k); })) return;
    // 全暗：这时才换页（换页里 index.html 会调 gate.enter，把就绪状态、开门回调挂到这一轮上）
    const update = ir.update; ir.update = null;
    if (update) update();
    if (!alive()) return;
    await settle(ir, ir.to === 'bookmarks' ? document.querySelector('.home-hero') : document.getElementById(ir.to === 'chat' ? 'chatStage' : 'vaultStage'));
    if (!alive()) return;
    if (ir.still) {   // 减弱动态效果：等就绪（或十秒兜底），暗幕淡出
      if (!ir.ready) { showHud(ir, stageAperture(ir.to)); await new Promise(r => { ir.wake = r; }); }
      if (!alive()) return;
      const open = ir.onOpen; ir.onOpen = null; if (open) open();
      ir.veil.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 320, easing: 'ease-out', fill: 'forwards' });
      ir.timers.push(setTimeout(() => irisFinish(ir), 340));
      return;
    }
    // ② 接力
    const moved = (a, b) => Math.hypot(b.x - a.x, b.y - a.y) > 3 || Math.abs(b.r - a.r) > 3;
    let waited = false;
    if (!ir.ready) {   // 冷进入：先飞到舞台中央停住，转着等框里的应用说好了
      const mid = stageAperture(ir.to);
      ir.el.classList.add('is-star');
      if (moved(at, mid) && !await fly(ir, at, mid, tFly)) return;
      at = mid; waited = true;
      showHud(ir, mid);
      if (!ir.ready) await new Promise(r => { ir.wake = r; });
      if (!alive()) return;
      ir.el.classList.remove('is-waiting');
    }
    const dest = ir.to === 'bookmarks' ? (homeAperture() || stageAperture('chat'))
      : ir.to === 'chat' && !ir.fail ? (await askBeacon(ir, 'chat', 900)) || stageAperture('chat') : stageAperture(ir.to);
    if (!alive()) return;
    ir.el.classList.toggle('is-star', dest.kind === 'star');
    if (moved(at, dest) && !await fly(ir, at, dest, waited ? tFly * .7 : tFly)) return;
    // ③ 点亮：落点的光圈亮起，告诉框「有人进来了」（框里的入场在辉光底下播）
    const open = ir.onOpen; ir.onOpen = null; if (open) open();
    ir.el.classList.add('is-flare');
    [ir.bloom, ir.wave].forEach(el => { el.style.left = f1(dest.x) + 'px'; el.style.top = f1(dest.y) + 'px'; el.style.setProperty('--r', f1(dest.r) + 'px'); });
    ir.ring.animate([{ transform: place(dest) + ' rotate(140deg)', opacity: 1 }, { transform: place(dest, .86) + ' rotate(170deg)', opacity: 1, offset: .35 },
      { transform: place(dest, 1.25) + ' rotate(200deg)', opacity: 0 }], { duration: tFlare + tLight * .4, easing: 'cubic-bezier(.3, 0, .2, 1)', fill: 'forwards' });
    ir.bloom.animate([{ transform: 'scale(.2)', opacity: 0 }, { transform: 'scale(1)', opacity: 1, offset: .3 }, { transform: 'scale(1.6)', opacity: 0 }],
      { duration: tFlare + tLight * .5, easing: 'ease-out', fill: 'forwards' });
    ir.wave.animate([{ transform: 'scale(.9)', opacity: .9 }, { transform: 'scale(4.2)', opacity: 0 }], { duration: tFlare * 1.6, easing: 'cubic-bezier(.2, .6, .3, 1)', fill: 'forwards' });
    if (ir.to === 'bookmarks') warpStars(tFlare + tLight);   // 星核重新点火（电脑 + 星核壁纸时画布上那颗跟着增亮）
    if (!await tween(ir, tFlare, p => hole(ir, dest.x, dest.y, dest.r * 1.3 * ease(p), dest.r * .8 * ease(p), .9 * p))) return;
    // ④ 照亮：孔带着光边扩到整屏
    const far = Math.max(...[[0, 0], [innerWidth, 0], [0, innerHeight], [innerWidth, innerHeight]].map(([x, y]) => Math.hypot(x - dest.x, y - dest.y)));
    if (!await tween(ir, tLight, p => {
      const e = ease(p), feather = dest.r * .8 + 160 * e;
      hole(ir, dest.x, dest.y, dest.r * 1.3 + (far + feather - dest.r * 1.3) * e, feather, .9 * (1 - p * p));
      ir.veil.style.opacity = p < .7 ? 1 : 1 - (p - .7) / .3;
    })) return;
    irisFinish(ir);
  };
  window.addEventListener('resize', () => { if (iris && iris.tier !== irisTier()) irisFinish(); }, { passive: true });
  document.addEventListener('visibilitychange', () => { if (document.hidden) irisFinish(); });
  const gate = {
    // 换页时 index.html 调：进聊天 / 笔记。正在为它播的这一轮就挂上就绪状态与开门回调；
    // 没有（浏览器后退、框重开）就从这里起一轮。返回 false = 不播，调用方自己告诉框。
    enter: (name, opts = {}) => {
      if (irisSkipping || !sceneAllowed() || document.hidden) return false;
      const ir = iris && iris.to === name && !iris.attached ? iris : irisStart(name, { tab: opts.tab, beacon: opts.beacon });
      ir.attached = true;
      ir.onOpen = opts.onOpen || null;
      if (opts.beacon) ir.beacon = opts.beacon;
      (opts.lines || []).forEach(([text, state]) => irisLine(ir, text, state));
      if (opts.ready) irisReady(ir);
      return true;
    },
    // 框里的应用说「好了」（fail：没能免登录，照亮后露出登录页）。返回 false = 没有这一轮在等它，调用方自己告诉框。
    ready: (name, fail = '') => {
      const ir = iris;
      if (!ir || ir.to !== name) return false;
      if (fail) irisLine(ir, fail, 'WARN', true);
      irisReady(ir, fail);
      return true;
    },
    step: (name, text, state = 'OK') => { if (iris && iris.to === name && !iris.ready) irisLine(iris, text, state, true); },
    // 换到别的页：正在播的收掉；从聊天 / 笔记回首页却没经过侧栏（浏览器后退）：在首页上补一段暗下来再点亮。
    leave: (name, from) => {
      if (iris && iris.to === name) return;
      irisFinish();
      if (name === 'bookmarks' && FRAME_VIEWS.includes(from) && sceneAllowed() && !document.hidden && !irisSkipping) irisStart(name, {});
    },
  };

  // 切页时星场跃迁一下（和 View Transition 同时发生）。首页 ⇄ 聊天 / 笔记、聊天 ⇄ 笔记由光圈接力接手（不叠 View Transition 和切镜）。
  const plainChange = change;
  const scifiChange = (update, name, opts = {}) => {
    if (iris && iris.to === name && iris.update) return;   // 同一个目的地连点：接着播
    irisFinish();   // 上一段还没播完：直接收尾（该换的页先换上）
    if (name === previous || !sceneAllowed()) { if (name !== previous) hubWarp(650); plainChange(update, name); return; }
    if (!document.hidden && (FRAME_VIEWS.includes(name) || (name === 'bookmarks' && FRAME_VIEWS.includes(previous)))) {
      transition?.skipTransition();
      irisStart(name, { update, from: previous, tab: opts.tab, beacon: opts.beacon });
      return;
    }
    hubWarp(650); plainChange(update, name);
  };
  window.HaloMotion = { nav, change: scifiChange, page, homeEnter, cardToModal, warp: hubWarp, telemetry, gate };
})();

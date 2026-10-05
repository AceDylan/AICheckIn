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
  const hud = document.createElement('div');
  hud.className = 'hub-hud'; hud.setAttribute('aria-hidden', 'true');
  hud.innerHTML = '<i class="hub-hud__rail"></i><i class="hub-hud__rail"></i><i class="hub-hud__scan"></i>'
    + '<span class="hub-hud__link">CORE LINK // STABLE</span><span>DEEP SPACE / 01</span><span>ORBITAL ARRAY</span>';
  wall?.after(hud);
  const hero = document.querySelector('.home-hero');
  if (hero) {
    const orbit = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    orbit.setAttribute('viewBox', '-300 -90 600 180'); orbit.setAttribute('aria-hidden', 'true');
    orbit.setAttribute('class', 'hub-clock-orbit');
    orbit.innerHTML = '<g class="hub-clock-orbit__outer"><ellipse rx="245" ry="69"/><ellipse rx="230" ry="59" stroke-dasharray="2 14"/>'
      + '<ellipse class="hub-clock-orbit__comet" rx="245" ry="69" pathLength="100"/></g>'
      + '<g class="hub-clock-orbit__inner"><ellipse rx="204" ry="48" stroke-dasharray="80 22 4 18"/>'
      + '<ellipse class="hub-clock-orbit__comet" rx="204" ry="48" pathLength="100"/></g>'
      + '<path d="M-282 0h42m480 0h42M0-84v14M0 70v14"/><circle cx="-245" r="3"/><circle cx="245" r="3"/>';
    hero.prepend(orbit);
  }
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
  // Match the wallpaper's cover crop and camera transform, including phone landscape.
  // 盘面倾角、喷流方向与 tools/make_wallpapers.py 的 starcore() 一致：粒子流正好压在画好的吸积盘上。
  const DISK_TINTS = ['#cdf6ff', '#5ebeff', '#9664ff', '#ff5cc4'];
  const flow = Array.from({ length: 160 }, () => {
    const e = .55 + Math.random() ** .7 * 1.6;
    return { a: Math.random() * 6.2832, e, lift: (Math.random() - .5) * .07, tint: DISK_TINTS[Math.min(3, ((e - .55) / .4) | 0)] };
  });
  const TILT_COS = Math.cos(-.38), TILT_SIN = Math.sin(-.38);
  let reactorLast = 0, arc = null, nextArc = 0;
  const onDisk = (r, e, a, lift = 0) => {
    const x = Math.cos(a) * e * r, y = (Math.sin(a) * e * .3 + lift) * r;
    return [x * TILT_COS - y * TILT_SIN, x * TILT_SIN + y * TILT_COS];
  };
  const glowDot = (x, y, q, color, alpha) => {
    const g = sctx.createRadialGradient(x, y, 0, x, y, q);
    g.addColorStop(0, color); g.addColorStop(1, 'rgba(94,231,255,0)');
    sctx.globalAlpha = alpha; sctx.fillStyle = g; sctx.fillRect(x - q, y - q, q * 2, q * 2);
  };
  const drawReactor = (now) => {
    if (root.dataset.wallpaper !== 'starcore' || !wall?.classList.contains('is-ready')) return;
    const portrait = portraitScene.matches, iw = portrait ? 1080 : 2560, ih = portrait ? 1920 : 1440;
    const moving = desktop.matches && !reduced.matches;
    const zoom = moving ? (root.classList.contains('cine-search') ? 1.04 : 1.015) : 1;
    const cover = Math.max(sw / iw, sh / ih) * zoom;
    const style = getComputedStyle(root);
    const cx = sw / 2 + ((portrait ? .68 : .72) - .5) * iw * cover + (moving ? parseFloat(style.getPropertyValue('--wall-px')) || 0 : 0);
    const cy = sh / 2 + ((portrait ? .23 : .46) - .5) * ih * cover + (moving ? parseFloat(style.getPropertyValue('--wall-py')) || 0 : 0);
    const r = Math.min(iw, ih) * (portrait ? .28 : .23) * cover;
    const t = now / 1000, dt = reactorLast && t > reactorLast ? Math.min(.1, t - reactorLast) : 0;
    const still = stillScene(), small = phone();
    reactorLast = t;
    sctx.save(); sctx.translate(cx, cy); sctx.lineCap = 'round';
    // 锁定准星：虚线环缓慢转动，外加三段反向的轨道光弧。
    sctx.globalAlpha = .4; sctx.lineWidth = 1; sctx.strokeStyle = '#5ee7ff';
    sctx.setLineDash([r * .2, r * .07]); sctx.lineDashOffset = -t * r * .06;
    sctx.beginPath(); sctx.arc(0, 0, r * 1.62, 0, 6.2832); sctx.stroke(); sctx.setLineDash([]);
    sctx.globalAlpha = .55;
    for (let j = 0; j < 3; j++) {
      const a = t * (j % 2 ? -.12 : .08) + j * 2.1;
      sctx.strokeStyle = j % 2 ? '#af91ff' : '#77eaff';
      sctx.beginPath(); sctx.ellipse(0, 0, r * (1.15 + j * .15), r * (.36 + j * .09), -.38, a, a + 1.8); sctx.stroke();
    }
    sctx.globalCompositeOperation = 'lighter';
    // 吸积盘粒子流：内圈转得快（开普勒），朝我们转来的左侧更亮。
    sctx.lineWidth = small ? 1.1 : 1.6;
    for (let i = 0, n = small ? 60 : flow.length; i < n; i++) {
      const p = flow[i];
      p.a += dt * .85 / p.e ** 1.5;
      const [x0, y0] = onDisk(r, p.e, p.a - .16 / p.e, p.lift), [x1, y1] = onDisk(r, p.e, p.a, p.lift);
      sctx.globalAlpha = .12 + .7 * (1 - .72 * Math.cos(p.a)) / 1.72;
      sctx.strokeStyle = p.tint; sctx.beginPath(); sctx.moveTo(x0, y0); sctx.lineTo(x1, y1); sctx.stroke();
    }
    // 日冕呼吸 + 每 7 秒一圈沿盘面扩散的冲击波。
    const pulse = .5 + .5 * Math.sin(t * .9);
    glowDot(0, 0, r * .45, 'rgba(190,246,255,.55)', .25 + pulse * .25);
    const wave = (t % 7) / 1.8;
    if (!still && wave < 1) {
      sctx.globalAlpha = (1 - wave) ** 2 * .7; sctx.strokeStyle = '#9fe9ff'; sctx.lineWidth = .8 + 2.2 * (1 - wave);
      sctx.beginPath(); sctx.ellipse(0, 0, r * (.3 + wave * 2.1), r * (.3 + wave * 2.1) * .3, -.38, 0, 6.2832); sctx.stroke();
      sctx.globalAlpha *= .45; sctx.strokeStyle = '#c4b0ff';
      sctx.beginPath(); sctx.arc(0, 0, r * (.25 + wave * 1.3), 0, 6.2832); sctx.stroke();
    }
    // 双极喷流：亮结沿喷流向外涌，越远越淡。
    for (const sign of [-1, 1]) {
      for (let k = 0; k < 4; k++) {
        const s = (t * .2 + k / 4) % 1, d = r * (.2 + s * 3);
        glowDot(-TILT_SIN * sign * d, TILT_COS * sign * d, r * (.09 * (1 - s) + .02), 'rgba(226,251,255,.9)', (1 - s) * .75);
      }
    }
    // 偶发等离子电弧：从核心表面打到盘上，闪 0.25 秒（桌面）。
    if (!still && !small) {
      if (now > nextArc) {
        const a = Math.random() * 6.2832, e = .8 + Math.random() * .8, b = Math.random() * 6.2832, pts = [];
        const [ex, ey] = onDisk(1, e, a);
        for (let i = 0; i <= 9; i++) {
          const k = i / 9, jit = i && i < 9 ? (Math.random() - .5) * .16 : 0;
          const x = Math.cos(b) * .1 * (1 - k) + ex * k, y = Math.sin(b) * .1 * (1 - k) + ey * k;
          pts.push([x - (ey - Math.sin(b) * .1) * jit, y + (ex - Math.cos(b) * .1) * jit]);
        }
        arc = { start: now, pts }; nextArc = now + 2400 + Math.random() * 4200;
      }
      if (arc && now - arc.start < 250) {
        sctx.globalAlpha = .5 + .5 * Math.random(); sctx.strokeStyle = '#d9f6ff'; sctx.lineWidth = 1.3;
        sctx.shadowColor = '#5ee7ff'; sctx.shadowBlur = 10;
        sctx.beginPath(); arc.pts.forEach(([x, y], i) => (i ? sctx.lineTo(x * r, y * r) : sctx.moveTo(x * r, y * r))); sctx.stroke();
        sctx.shadowBlur = 0;
      }
    }
    sctx.globalCompositeOperation = 'source-over';
    // 遥测读数（宽屏桌面）：一根引线从准星拉到星核右上方——工具条之下、右侧组件栏之上那块空地，数值随时间轻微浮动。
    if (!small && sw >= 1100 && sw - cx >= 200) {   // 竖长桌面屏上星核贴近右缘，右边没地方就不画
      const tx = Math.min(r * 1.25, sw - cx - 176), ty = Math.max(96 - cy, -r * 1.12);
      const ax = r * 1.62 * Math.cos(-.62), ay = r * 1.62 * Math.sin(-.62);
      sctx.globalAlpha = .55; sctx.strokeStyle = '#5ee7ff'; sctx.lineWidth = 1;
      sctx.beginPath(); sctx.moveTo(ax, ay); sctx.lineTo(tx - 10, ty + 48); sctx.lineTo(tx + 150, ty + 48); sctx.stroke();
      sctx.fillStyle = '#9fe9ff'; sctx.font = '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
      [
        'STELLAR CORE // Σ-07',
        'CORE TEMP  ' + (5.8 + .07 * Math.sin(t * 1.7)).toFixed(2) + 'e6 K',
        'PLASMA FLUX ' + (97.2 + 1.9 * Math.sin(t * .9)).toFixed(1) + '%',
        'SPIN PHASE  ' + String(Math.floor(t * 12) % 360).padStart(3, '0') + '°',
      ].forEach((line, i) => sctx.fillText(line, tx, ty + i * 14));
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
    drawReactor(now);
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
    if (wall) new MutationObserver(startStars).observe(wall, { attributes: true, attributeFilter: ['class'] });
    syncScene();
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
  window.HaloMotion = { nav, change: scifiChange, homeEnter, cardToModal, warp: hubWarp };
})();

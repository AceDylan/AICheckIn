/* 极简 DOM 替身：用来在 node 里真正「执行」模板里的内联脚本。
 *
 * 页面是单文件模板、没有构建步骤，语法错误能靠 node --check 挡住，但「脚本跑起来
 * 第一秒就抛异常」这类问题（例如读取声明在下方的 const 触发 TDZ）只有真的执行
 * 一遍才会暴露。这里不追求 DOM 语义正确，只要让脚本能一路跑完并记录它做了什么。
 */
const CALLS = { fetches: [], errors: [], rejections: [] };

function makeEl(id) {
  const el = {
    id: id || '',
    dataset: {},
    style: {},
    hidden: false,
    disabled: false,
    value: '',
    textContent: '',
    innerHTML: '',
    placeholder: '',
    checked: false,
    isConnected: true,
    lastElementChild: null,
    classList: {
      _set: new Set(),
      add(...c) { c.forEach((x) => this._set.add(x)); },
      remove(...c) { c.forEach((x) => this._set.delete(x)); },
      toggle(c, on) { if (on === undefined) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); } else if (on) { this._set.add(c); } else { this._set.delete(c); } },
      contains(c) { return this._set.has(c); },
    },
    addEventListener() {}, removeEventListener() {},
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    closest() { return null; },
    focus() {}, select() {}, click() {}, blur() {}, remove() {}, scrollIntoView() {},
    appendChild() {}, insertBefore() {}, contains() { return false; },
    setPointerCapture() {}, releasePointerCapture() {},
    getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
  };
  return el;
}

const REGISTRY = new Map();
function el(id) {
  if (!REGISTRY.has(id)) REGISTRY.set(id, makeEl(id));
  return REGISTRY.get(id);
}

// 视图与 tab：选择器里出现的那几种形态给出可用的替身。
const VIEWS = ['bookmarks', 'checkin', 'configs', 'history', 'settings'];
let activeView = 'bookmarks';

globalThis.document = {
  documentElement: makeEl('html'),
  cookie: '',
  getElementById: (id) => el(id),
  createElement: () => makeEl(''),
  addEventListener() {}, removeEventListener() {},
  querySelector(sel) {
    let m = /^\.tab\[data-view="([a-z]+)"\]$/.exec(sel);
    if (m) { const t = el('tab-' + m[1]); t.dataset.view = m[1]; return VIEWS.includes(m[1]) ? t : null; }
    if (sel === '.view.active') { const v = el('view-' + activeView); v.id = 'view-' + activeView; return v; }
    if (sel === '.modal-mask.show') return null;
    return null;
  },
  querySelectorAll(sel) {
    if (sel === '.tab') return VIEWS.map((n) => { const t = el('tab-' + n); t.dataset.view = n; return t; });
    if (sel === '.view') return VIEWS.map((n) => { const v = el('view-' + n); v.id = 'view-' + n; return v; });
    return [];
  },
  activeElement: makeEl(''),
};
// switchView 会把 .view.active 切到目标页面，这里跟着记一下，
// 好让 currentViewName() 这类逻辑拿到真实结果。
const origToggle = makeEl('').classList.toggle;
VIEWS.forEach((n) => {
  const v = el('view-' + n);
  v.id = 'view-' + n;
  v.classList.toggle = function (cls, on) {
    if (cls === 'active' && on) activeView = n;
    origToggle.call(this, cls, on);
  };
});

globalThis.window = globalThis;
globalThis.location = { hash: '', search: '', pathname: '/', origin: 'https://example.test', protocol: 'https:' };
globalThis.history = { replaceState() {}, pushState() {} };
globalThis.navigator = { serviceWorker: { register: () => Promise.resolve() }, clipboard: null };
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
globalThis.scrollTo = () => {};
globalThis.setTimeout = setTimeout;
globalThis.alert = () => {};
globalThis.confirm = () => true;
globalThis.open = () => {};

globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};

globalThis.__CALLS = CALLS;
globalThis.fetch = function (url, opts) {
  CALLS.fetches.push(String(url));
  const body = globalThis.__RESPONSES[String(url).split('?')[0]] || { ok: true };
  return Promise.resolve({
    ok: true, status: 200, headers: { get: () => null },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
};

process.on('uncaughtException', (e) => { CALLS.errors.push(String(e && e.message || e)); });
process.on('unhandledRejection', (e) => { CALLS.rejections.push(String(e && e.message || e)); });

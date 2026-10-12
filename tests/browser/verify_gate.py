# -*- coding: utf-8 -*-
"""光圈接力（收藏首页 ⇄ AI 聊天 / 笔记的过渡，halo-motion.js 的 iris，外壳 v81）的真实浏览器回归。

    python tests/browser/verify_gate.py

只有真浏览器说得清的几件事：
  - 先变暗、再换页：点下去以后首页还在，整屏暗下去只剩光圈（起点是首页时钟外的星核刻度环）；
  - 光圈飞到目的地的光圈上：进聊天落在 HaloWebUI 新对话那颗光圈上（框里报来的位置），进笔记落在舞台中央的星光上，
    回首页落回星核；然后那里亮起、照亮整屏，这时才把 enter 发进框（只发一次）；
  - 框里的应用说「好了」之前，框始终被暗幕或加载层盖着（冷进入先停在舞台中央等）；
  - 旧版 HaloWebUI 不回答光圈位置：落在舞台中央，之后不再干等；
  - 点一下 / Esc 直接收尾，点在侧栏标签上照样切过去；折叠开合立即收尾；
  - 减弱动态效果只有暗幕一淡一出；科幻特效关掉时不播，但加载层照样等就绪。

布置：一个隔离的本站实例（配了 HUB_CHAT_URL + 共享密钥、HUB_VAULT_URL + 嵌入密钥），一个扮演 HaloWebUI 的假站
（/auth 按 MODE 说话：ready = 先报「验票中」过一会儿说 ready、再画出新对话的光圈并回答 beacon；form = 报「登录表单」；
silent = 一声不吭，像旧版），一个扮演 WebObsidian 的假站（/auth/hub/sso 303 到首页，首页过一会儿说 ready）。
两个假站都把收到的 enter 记在页面上。
"""
import http.server
import os
import secrets
import threading
import time

from common import *   # noqa: F401,F403
from common import _free_port

RESULTS = []
MODE = {"halo": "ready", "halo_delay": 1500, "vault_delay": 900}
ORB = {"left": "50%", "top": "34%"}   # 假 HaloWebUI 新对话光圈的位置（框内）


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + ((" — " + str(detail)[:200]) if detail and not ok else ""), flush=True)


_FRAME_SCRIPT = """<script>
window.__enters = 0; window.__beacons = 0;
addEventListener('message', e => { if (e.data && e.data.source === 'hub' && e.data.type === 'enter') window.__enters++; });
const post = (m) => parent.postMessage(Object.assign({ source: '%(source)s' }, m), '%(hub)s');
%(body)s
</script>"""

# 新版 HaloWebUI：ready 之后画出光圈（真站上是 data-halo-orb，这里用类名，免得被当成本站页面的选择器），并按 hub-embed.ts 的协议回答「光圈在哪」（这里不等，有就报、没有报 none）。
_HALO_ORB = """
const drawOrb = () => { const o = document.createElement('div'); o.className = 'fake-orb';
  o.style.cssText = 'position:absolute;left:%(left)s;top:%(top)s;width:88px;height:88px;margin-left:-44px;border-radius:50%%;background:radial-gradient(white,cyan 40%%,transparent 70%%)';
  document.body.append(o); };
addEventListener('message', e => {
  const d = e.data; if (!d || d.source !== 'hub' || d.type !== 'beacon') return;
  window.__beacons++;
  const o = document.querySelector('.fake-orb'), b = o && o.getBoundingClientRect();
  post(b ? { type: 'beacon', id: d.id, x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2), r: Math.round(b.width / 2) } : { type: 'beacon', id: d.id, none: true });
});
"""


class _Peer(http.server.BaseHTTPRequestHandler):
    hub_origin = ""

    def _html(self, title, script):
        body = ('<!doctype html><meta charset="utf-8"><title>%s</title><body style="margin:0;background:white;font:14px sans-serif">'
                '<h1>%s</h1>%s' % (title, title, script)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self' " + self.hub_origin)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Halo(_Peer):
    def do_GET(self):
        mode, delay = MODE["halo"], MODE["halo_delay"]
        body = {"ready": (_HALO_ORB % ORB) + "post({ type: 'auth', state: 'signing-in' }); setTimeout(() => { drawOrb(); post({ type: 'ready' }); }, %d);" % delay,
                "form": "post({ type: 'auth', state: 'form' });",
                # 线上 10-12 的样子：/auth 不报 auth（未登录拿不到 Hub 源），验完票先报 activity，应用代码到了才 ready
                "chatty": (_HALO_ORB % ORB) + "setTimeout(() => post({ type: 'activity', state: 'idle' }), 300); setTimeout(() => { drawOrb(); post({ type: 'ready' }); }, %d);" % delay,
                "silent": ""}[mode]
        self._html("halo-auth", _FRAME_SCRIPT % {"source": "halowebui", "hub": self.hub_origin, "body": body})

    def do_POST(self):   # 启动握手（probe 票）：一律回失败，本套不关心
        self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _Vault(_Peer):
    def do_GET(self):
        body = "setTimeout(() => post({ type: 'ready' }), %d);" % MODE["vault_delay"]
        self._html("vault-home", _FRAME_SCRIPT % {"source": "webobsidian", "hub": self.hub_origin, "body": body})

    def do_POST(self):   # /auth/hub/sso：不验票，直接 303 到首页（验票由 WebObsidian 自己的测试负责）
        self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()


def start_peer(handler, hub_origin):
    handler.hub_origin = hub_origin
    server = http.server.ThreadingHTTPServer(("127.0.0.1", _free_port()), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_address[1]


def open_hub(b, w, h, mobile=False, cookies=None, **kw):
    ctx = new_context(b, w, h, mobile=mobile, **kw)
    jar = {"bh_theme": "dark", "bh_wallpaper": "starcore"}
    jar.update(cookies or {})
    ctx.add_cookies([{"name": k, "value": v, "url": BASE} for k, v in jar.items()])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED")
    page.wait_for_timeout(700)
    page.evaluate("""() => { window.__readyAt = 0; addEventListener('message', e => {
        const d = e.data; if (d && (d.type === 'ready' || (d.type === 'auth' && d.state === 'form')) && !window.__readyAt) window.__readyAt = performance.now(); }); }""")
    return ctx, page, errors


# 点下去之前挂上：每 30ms 记一帧——接力层在不在、类名、暗幕不透明度、光圈圆心与大小、当前是哪一页、加载层盖没盖着。
WATCH = """([loadingId]) => {
  const loading = document.getElementById(loadingId);
  window.__w = { t0: performance.now(), out: [], classes: new Set(), lines: new Set(), end: 0 };
  const tick = () => {
    const w = window.__w, ir = document.querySelector('.hub-iris');
    const view = (document.querySelector('.view.active') || { id: '' }).id.replace('view-', '');
    let ring = null, veil = 0;
    if (ir) {
      ir.className.split(' ').forEach(c => w.classes.add(c));
      ir.querySelectorAll('.hub-iris__hud b, .hub-iris__hud p span').forEach(el => w.lines.add(el.textContent));   // 读数只留最近三行：攒下见过的每一行
      const b = ir.querySelector('.hub-iris__ring').getBoundingClientRect();
      ring = [Math.round(b.left + b.width / 2), Math.round(b.top + b.height / 2), Math.round(b.width)];
      veil = parseFloat(getComputedStyle(ir.querySelector('.hub-iris__veil')).opacity);
    }
    w.out.push({ t: performance.now() - w.t0, on: !!ir, cls: ir ? ir.className : '', veil, ring, view, loading: !loading.hidden });
    if (!ir && w.out.length > 10 && w.out[w.out.length - 2].on) { w.end = performance.now() - w.t0; return; }
    if (performance.now() - w.t0 < 12000) setTimeout(tick, 30);
  };
  tick();
}"""


def watch(page, view, click=None, limit=9000):
    loading = "chatLoading" if view == "chat" else "vaultLoading"
    page.evaluate(WATCH, [loading])
    page.click(click or '.tab[data-view="%s"]' % view)
    t0 = time.time()
    while time.time() - t0 < limit / 1000:
        page.wait_for_timeout(120)
        if page.evaluate("() => window.__w.end > 0"):
            break
    w = page.evaluate("() => ({ ...window.__w, classes: [...window.__w.classes], lines: [...window.__w.lines].join(' | '), readyAt: window.__readyAt ? window.__readyAt - window.__w.t0 : 0 })")
    return w


def reactor_center(page):
    return page.evaluate("""() => { const r = document.querySelector('.home-hero .hub-reactor').getBoundingClientRect();
        return [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)]; }""")


def orb_center(page, halo):
    frame = peer_frame(page, halo)
    fb = page.evaluate("() => { const b = document.querySelector('#chatStage iframe').getBoundingClientRect(); return [b.left, b.top]; }")
    ob = frame.evaluate("() => { const b = document.querySelector('.fake-orb').getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }")
    return [round(fb[0] + ob[0]), round(fb[1] + ob[1])]


def stage_center(page, view):
    return page.evaluate("""(id) => { const b = document.getElementById(id).getBoundingClientRect();
        return [Math.round(b.left + b.width / 2), Math.round(b.top + b.height / 2)]; }""", "chatStage" if view == "chat" else "vaultStage")


def near(a, b, tol=6):
    return a is not None and b is not None and abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def first(w, pred):
    return next((s for s in w["out"] if pred(s)), None)


def landing(w):
    """点亮（is-flare）前最后一帧光圈的圆心：光圈落在哪。"""
    before = [s for s in w["out"] if s["on"] and "is-flare" not in s["cls"] and s["ring"]]
    flare = first(w, lambda s: "is-flare" in s["cls"])
    return (before[-1]["ring"] if before else None), flare


def covered_until_ready(w):
    """就绪（ready / 登录表单）之前的每一帧，舞台要么被暗幕（基本不透明）盖着，要么被加载层盖着。"""
    ready = w["readyAt"]
    after_switch = [s for s in w["out"] if s["view"] in ("chat", "vault")]
    bare = [s for s in after_switch if (not ready or s["t"] < ready) and not (s["on"] and s["veil"] > .85) and not s["loading"]]
    return ready > 0 and not bare, {"readyAt": round(ready), "bare": bare[:2]}


def peer_frame(page, origin):
    for frame in page.frames:
        if frame is not page.main_frame and frame.url.startswith(origin):
            return frame
    return None


def gone(page):
    return page.evaluate("() => !document.querySelector('.hub-iris')")


def desktop(b, halo, vault):
    MODE.update(halo="ready", halo_delay=1500)
    ctx, page, errors = open_hub(b, 1440, 900)
    home = reactor_center(page)
    w = watch(page, "chat")
    early = first(w, lambda s: s["on"] and 0 < s["veil"] < .95)
    check("电脑·进聊天：先暗下去、页面还没换（首页还在）", early is not None and early["view"] == "bookmarks", early)
    start = first(w, lambda s: s["on"] and s["veil"] > .05)
    check("电脑·进聊天：光圈从首页的星核刻度环出发", start is not None and near(start["ring"], home), (start and start["ring"], home))
    dark = first(w, lambda s: s["view"] == "chat")
    check("电脑·进聊天：全暗时才换页", dark is not None and dark["on"] and dark["veil"] > .95, dark)
    ok, detail = covered_until_ready(w)
    check("电脑·冷进入：HaloWebUI 说 ready 之前框一直被暗幕或加载层盖着", ok, detail)
    waiting = first(w, lambda s: "is-waiting" in s["cls"])
    check("电脑·冷进入：先停在舞台中央等", waiting is not None and near(waiting["ring"], stage_center(page, "chat"), 8), (waiting and waiting["ring"], stage_center(page, "chat")))
    for text in ("HALO · WEBUI", "签发通行票据", "接入 HaloWebUI", "验证通行票据"):
        check("电脑·冷进入：等的时候读数里有「%s」" % text, text in w["lines"], w["lines"])
    land, flare = landing(w)
    orb = orb_center(page, halo)
    check("电脑·进聊天：光圈落在 HaloWebUI 新对话的光圈上", near(land, orb), (land, orb))
    check("电脑·进聊天：落点亮起（点亮）后照亮整屏，层删干净", flare is not None and gone(page) and w["end"] > 0, w["classes"])
    took = w["end"] - w["readyAt"]
    check("电脑·冷进入：就绪后约两秒半内收尾（不太快也不拖）", 1200 < took < 3200, round(took))
    check("电脑·冷进入：加载层撤掉", page.evaluate("() => document.getElementById('chatLoading').hidden"))
    frame = peer_frame(page, halo)
    check("电脑·冷进入：点亮时把 enter 发进框（只发一次）", frame is not None and frame.evaluate("() => window.__enters") == 1, frame and frame.evaluate("() => window.__enters"))

    # 回收藏库：暗下去只留 HaloWebUI 的光圈 → 飞回星核 → 点亮
    w = watch(page, "bookmarks")
    early = first(w, lambda s: s["on"] and s["veil"] > .05)
    check("回首页：先暗下去、聊天页还在，光圈从 HaloWebUI 的光圈出发", early is not None and early["view"] == "chat" and near(early["ring"], orb), (early, orb))
    land, flare = landing(w)
    home = reactor_center(page)
    check("回首页：光圈落回星核刻度环", near(land, home), (land, home))
    check("回首页：点亮后照亮整屏、层删干净", flare is not None and gone(page) and page.evaluate("() => document.getElementById('view-bookmarks').classList.contains('active')"))
    took = w["end"]
    check("回首页：全程约两到三秒半（无头软件渲染偏慢，放宽到四秒）", 1800 < took < 4000, round(took))

    w = watch(page, "chat")
    check("电脑·热进入：不停在中央等", not any("is-waiting" in s["cls"] for s in w["out"]), w["classes"])
    land, flare = landing(w)
    check("电脑·热进入：直接落在 HaloWebUI 的光圈上", near(land, orb_center(page, halo)), (land, orb_center(page, halo)))
    check("电脑·热进入：两到三秒半收尾（同上，放宽到四秒）", 1800 < w["end"] < 4000, round(w["end"]))
    check("电脑·热进入：框又收到一次 enter", frame.evaluate("() => window.__enters") == 2, frame.evaluate("() => window.__enters"))
    check("电脑·热进入：没有横向溢出", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"))
    check("电脑：页面没有脚本错误", not errors, errors[:3])
    ctx.close()


def skip_and_old(b, halo, vault):
    MODE.update(halo="silent")
    ctx, page, errors = open_hub(b, 1440, 900)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(2600)
    check("旧版 HaloWebUI（不说话）：停在中央等", page.evaluate("() => !!document.querySelector('.hub-iris.is-waiting')"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    check("Esc 直接收尾（层收掉，加载层接着盖着框）", gone(page) and not page.evaluate("() => document.getElementById('chatLoading').hidden"))
    page.wait_for_timeout(3000)
    check("旧版 HaloWebUI：load 后 3 秒按兜底撤掉加载层", page.evaluate("() => document.getElementById('chatLoading').hidden"))
    frame = peer_frame(page, halo)
    check("旧版 HaloWebUI：就绪时把 enter 发进框", frame is not None and frame.evaluate("() => window.__enters") >= 1)
    # 旧版不回答光圈位置：回首页从舞台中央出发；问过一次没回音，这张框之后不再问（不干等）
    mid = stage_center(page, "chat")
    w = watch(page, "bookmarks")
    early = first(w, lambda s: s["on"] and s["veil"] > .05)
    check("旧版：回首页从舞台中央出发", early is not None and near(early["ring"], mid, 8), (early, mid))
    t0 = time.time()
    w = watch(page, "chat")
    check("旧版：之后不再干等光圈位置（热进入照常收尾）", w["end"] > 0 and w["end"] < 4000, round(w["end"]))
    ctx.close()

    MODE.update(halo="chatty", halo_delay=4600)
    ctx, page, errors = open_hub(b, 1440, 900)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(4000)
    check("说过话但没报 auth 的框：过了 3 秒兜底也还盖着（不露出 /auth 登录页）",
          page.evaluate("() => !!document.querySelector('.hub-iris.is-waiting') && !document.getElementById('chatLoading').hidden"))
    page.wait_for_timeout(4000)
    check("说过话的框：ready 到了才撤加载层、接力播完", gone(page) and page.evaluate("() => document.getElementById('chatLoading').hidden"))
    ctx.close()

    MODE.update(halo="ready", halo_delay=800)
    ctx, page, errors = open_hub(b, 1440, 900)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(250)
    page.mouse.click(720, 450)
    page.wait_for_timeout(100)
    check("点一下直接收尾：该换的页先换上", gone(page) and page.evaluate("() => document.getElementById('view-chat').classList.contains('active')"))
    page.wait_for_timeout(1500)
    page.click('.tab[data-view="bookmarks"]')
    page.wait_for_timeout(300)
    box = page.evaluate("() => { const b = document.querySelector('.tab[data-view=\"settings\"]').getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }")
    page.mouse.click(box[0], box[1])
    page.wait_for_timeout(900)   # 切到系统设置走 View Transition，无头软件渲染要几百毫秒
    check("播的时候点在侧栏标签上：收尾并切过去（系统设置）", gone(page) and page.evaluate("() => document.getElementById('view-settings').classList.contains('active')"))
    check("点击收尾：页面没有脚本错误", not errors, errors[:3])
    ctx.close()

    MODE.update(halo="form")
    ctx, page, errors = open_hub(b, 1440, 900)
    w = watch(page, "chat")
    check("免登录没接通（框里是登录表单）：变警示色、读数写明，照亮后露出登录页",
          "is-fail" in w["classes"] and "需要登录" in w["lines"] and page.evaluate("() => document.getElementById('chatLoading').hidden") and gone(page), (w["classes"], w["lines"]))
    ctx.close()


def touch_and_fold(b, halo, vault):
    # 折叠屏内屏：近正方形、触屏、宽度 > 760（侧栏在左边）
    MODE.update(halo="ready", halo_delay=1200)
    ctx, page, errors = open_hub(b, 880, 812, mobile=True)
    w = watch(page, "chat")
    ok, detail = covered_until_ready(w)
    check("内屏（触屏宽）：就绪前一直盖着", ok, detail)
    check("内屏：触屏档（不是电脑档），播完整", {"is-touch", "is-flare"} <= set(w["classes"]) and "is-desk" not in w["classes"] and gone(page), w["classes"])
    land, _ = landing(w)
    check("内屏：落在 HaloWebUI 的光圈上", near(land, orb_center(page, halo)), (land, orb_center(page, halo)))
    filt = page.evaluate("""async () => { document.querySelector('.tab[data-view="bookmarks"]').click(); await new Promise(r => setTimeout(r, 200));
        const a = document.querySelector('.hub-iris .hub-reactor__arcs'); return a ? getComputedStyle(a).filter : null; }""")
    check("内屏：飞的那一圈不带滤镜光晕", filt == "none", filt)
    page.wait_for_timeout(3500)
    check("内屏：没有横向溢出", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"))
    check("内屏：页面没有脚本错误", not errors, errors[:3])
    ctx.close()

    # 外屏：窄竖屏，侧栏变底部标签栏；没有反应堆，光圈从时钟外亮起
    MODE.update(halo="ready", halo_delay=1200)
    ctx, page, errors = open_hub(b, 400, 880, mobile=True)
    clock = page.evaluate("() => { const b = document.getElementById('heroClock').getBoundingClientRect(); return [Math.round(b.left + b.width / 2), Math.round(b.top + b.height / 2)]; }")
    w = watch(page, "chat")
    start = first(w, lambda s: s["on"] and s["veil"] > .05)
    check("外屏：光圈从时钟外亮起", start is not None and near(start["ring"], clock, 8) and "is-narrow" in w["classes"], (start and start["ring"], clock))
    land, flare = landing(w)
    check("外屏：落在 HaloWebUI 的光圈上、播完整", near(land, orb_center(page, halo)) and flare is not None and gone(page), (land, orb_center(page, halo)))
    check("外屏：没有横向溢出", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"))
    ctx.close()

    # 折叠 / 展开：播到一半宽度档位变了 → 立即收尾
    MODE.update(halo="ready", halo_delay=4000)
    ctx, page, errors = open_hub(b, 880, 812, mobile=True)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(1200)
    page.set_viewport_size({"width": 400, "height": 880})
    page.wait_for_timeout(250)
    check("折叠（内屏 → 外屏）：正在播的立即收尾", gone(page) and page.evaluate("() => document.getElementById('view-chat').classList.contains('active')"))
    check("折叠：加载层仍然盖着（框里还没好）", not page.evaluate("() => document.getElementById('chatLoading').hidden"))
    page.wait_for_timeout(3600)
    check("折叠：就绪后加载层撤掉", page.evaluate("() => document.getElementById('chatLoading').hidden"))
    check("折叠：页面没有脚本错误", not errors, errors[:3])
    ctx.close()


def prefs(b, halo, vault):
    MODE.update(halo="ready", halo_delay=1200)
    ctx, page, errors = open_hub(b, 1440, 900, reduced_motion="reduce")
    w = watch(page, "chat")
    check("减弱动态效果：只有暗幕（静止档，不飞、不点亮）", "is-still" in w["classes"] and "is-flare" not in w["classes"]
          and page.evaluate("() => true"), w["classes"])
    ok, detail = covered_until_ready(w)
    check("减弱动态效果：就绪前一直盖着", ok, detail)
    check("减弱动态效果：就绪后暗幕淡出、加载层撤掉", gone(page) and page.evaluate("() => document.getElementById('chatLoading').hidden"))
    ctx.close()

    ctx, page, errors = open_hub(b, 1440, 900, cookies={"bh_scifi": "off"})
    w = watch(page, "chat", limit=4000)
    ok, detail = covered_until_ready(w)
    check("科幻特效关：没有接力", not w["classes"], w["classes"])
    page.wait_for_timeout(1500)
    check("科幻特效关：加载层照样等 HaloWebUI 说 ready 才撤", page.evaluate("() => document.getElementById('chatLoading').hidden"))
    frame = peer_frame(page, halo)
    check("科幻特效关：就绪时直接把 enter 发进框", frame is not None and frame.evaluate("() => window.__enters") == 1)
    ctx.close()


def vault_entry(b, halo, vault):
    MODE.update(vault_delay=900, halo="ready", halo_delay=800)
    ctx, page, errors = open_hub(b, 1440, 900)
    w = watch(page, "vault")
    ok, detail = covered_until_ready(w)
    check("笔记·冷进入：WebObsidian 说 ready 之前一直盖着", ok, detail)
    land, flare = landing(w)
    check("笔记：光圈落在舞台中央、点亮的是星光（十字星芒）", near(land, stage_center(page, "vault"), 8) and "is-star" in (flare or {}).get("cls", ""), (land, stage_center(page, "vault"), flare and flare["cls"]))
    for text in ("NEURAL · VAULT", "签发通行证", "解锁笔记库"):
        check("笔记·冷进入：读数里有「%s」" % text, text in w["lines"], w["lines"])
    frame = peer_frame(page, vault)
    check("笔记：点亮时把 enter 发进框", frame is not None and frame.evaluate("() => window.__enters") == 1, frame and frame.evaluate("() => window.__enters"))
    # 回首页：从中央的星光出发，落回星核
    mid = stage_center(page, "vault")
    w = watch(page, "bookmarks")
    early = first(w, lambda s: s["on"] and s["veil"] > .05)
    check("笔记 → 首页：从中央的星光出发", early is not None and early["view"] == "vault" and near(early["ring"], mid, 8), (early, mid))
    land, _ = landing(w)
    check("笔记 → 首页：落回星核", near(land, reactor_center(page)), (land, reactor_center(page)))
    # 笔记 → 聊天：只有一层
    page.click('.tab[data-view="vault"]')
    page.wait_for_timeout(3600)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(300)
    check("笔记 → 聊天：只有一层接力", page.evaluate("() => document.querySelectorAll('.hub-iris').length === 1"))
    page.wait_for_timeout(4000)
    check("笔记 → 聊天：播完删干净", gone(page))
    check("笔记：框收到第二次 enter（热进入）", frame.evaluate("() => window.__enters") == 2, frame.evaluate("() => window.__enters"))
    check("笔记：页面没有脚本错误", not errors, errors[:3])
    ctx.close()


def main():
    t0 = time.time()
    halo = start_peer(_Halo, BASE)
    vault = start_peer(_Vault, BASE)
    # start_server() 把当前进程的环境变量带给子进程：地址与密钥只活在这里，不落盘。
    os.environ["HUB_CHAT_URL"] = halo
    os.environ["HUB_TRUSTED_EMBED_ADMIN_SECRET"] = secrets.token_hex(32)
    os.environ["HUB_VAULT_URL"] = vault
    os.environ["HUB_VAULT_EMBED_SECRET"] = secrets.token_hex(32)
    for name in ("HUB_PUBLIC_ORIGIN", "HUB_PUBLIC_LIBRARY", "GYQD_PRIVATE", "HUB_VAULT_API_KEY"):
        os.environ.pop(name, None)
    start_server()
    with sync_playwright() as p:
        b = launch(p)
        for fn in (desktop, skip_and_old, touch_and_fold, prefs, vault_entry):
            print("==", fn.__name__, flush=True)
            try:
                fn(b, halo, vault)
            except Exception as exc:
                check(fn.__name__ + " 运行完毕", False, repr(exc))
        b.close()
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS)} checks, {len(failed)} failed, {time.time() - t0:.0f}s")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

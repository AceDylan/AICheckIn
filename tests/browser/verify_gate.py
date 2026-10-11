# -*- coding: utf-8 -*-
"""星门（进「AI 聊天」「笔记」时的过渡，halo-motion.js 的 gate）的真实浏览器回归。

    python tests/browser/verify_gate.py

只有真浏览器说得清的几件事：
  - 框里的应用说「好了」之前，框始终被星门或加载层盖着（以前 load 一到就撤，HaloWebUI 每次都先露出 /auth 登录页）；
  - 冷进入等真实就绪、热进入一秒左右收尾，开门后才把 enter 发进框；
  - 电脑 / 折叠屏内屏（触屏宽）/ 外屏（窄竖屏）三档的样子，折叠开合时立即收尾；
  - 减弱动态效果只给静止的一帧，科幻特效关掉时没有门但加载层照样等就绪；
  - 星门只盖在舞台上：播的时候侧栏照样能点，切走就收掉。

布置：一个隔离的本站实例（配了 HUB_CHAT_URL + 共享密钥、HUB_VAULT_URL + 嵌入密钥），一个扮演 HaloWebUI 的假站
（/auth 按 MODE 说话：ready = 先报「验票中」过一会儿说 ready；form = 报「登录表单」；silent = 一声不吭，像旧版），
一个扮演 WebObsidian 的假站（/auth/hub/sso 303 到首页，首页过一会儿说 ready）。两个假站都把收到的 enter 记在页面上。
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


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + ((" — " + str(detail)[:200]) if detail and not ok else ""), flush=True)


_FRAME_SCRIPT = """<script>
window.__enters = 0;
addEventListener('message', e => { if (e.data && e.data.source === 'hub' && e.data.type === 'enter') window.__enters++; });
const post = (m) => parent.postMessage(Object.assign({ source: '%(source)s' }, m), '%(hub)s');
%(body)s
</script>"""


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
        body = {"ready": "post({ type: 'auth', state: 'signing-in' }); setTimeout(() => post({ type: 'ready' }), %d);" % delay,
                "form": "post({ type: 'auth', state: 'form' });",
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
    page.wait_for_timeout(700)   # 让星场 / 星核先画几帧（离港从星核出发要用到它最后画过的那一帧）
    page.evaluate("""() => { window.__readyAt = 0; addEventListener('message', e => {
        const d = e.data; if (d && (d.type === 'ready' || (d.type === 'auth' && d.state === 'form')) && !window.__readyAt) window.__readyAt = performance.now(); }); }""")
    return ctx, page, errors


# 从点下去开始，每 40ms 记一次：星门在不在、是不是已经在开门、加载层盖没盖着；外加门上见过的类名与读数。
WATCH = """async ([stageId, loadingId, limit]) => {
  const stage = document.getElementById(stageId), loading = document.getElementById(loadingId);
  const t0 = performance.now(), out = [], classes = new Set();
  let lines = '', title = '';
  while (performance.now() - t0 < limit) {
    const gate = stage.querySelector('.hub-gate');
    if (gate) {
      gate.className.split(' ').forEach(c => classes.add(c));
      lines = gate.querySelector('.hub-gate__lines').innerText;
      title = gate.querySelector('.hub-gate__title span').textContent;
    }
    out.push([performance.now(), !!gate && !gate.classList.contains('is-open'), !loading.hidden, !!gate]);
    if (!gate && loading.hidden && performance.now() - t0 > 300) break;
    await new Promise(r => setTimeout(r, 40));
  }
  return { out, classes: [...classes], lines, title, readyAt: window.__readyAt || 0, t0,
           flight: !!document.querySelector('.hub-gate-flight'), end: performance.now() };
}"""


def click_and_watch(page, view, limit=9000):
    stage, loading = ("chatStage", "chatLoading") if view == "chat" else ("vaultStage", "vaultLoading")
    page.evaluate("(v) => { window.__flightSeen = false; window.__holdSeen = false; window.__coreSeen = false;"
                  " new MutationObserver(() => { const f = document.querySelector('.hub-gate-flight'); if (f) { window.__flightSeen = true; if (f.classList.contains('is-core')) window.__coreSeen = true; }"
                  " if (document.querySelector('.hub-cut.is-hold')) window.__holdSeen = true; }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] }); }", view)
    page.click('.tab[data-view="%s"]' % view)
    return page.evaluate(WATCH, [stage, loading, limit])


def covered_until_ready(w):
    """就绪（ready / 登录表单）之前的每一帧，舞台要么被（还没开门的）星门盖着，要么被加载层盖着。"""
    ready = w["readyAt"]
    bare = [s for s in w["out"] if (not ready or s[0] < ready) and not s[1] and not s[2]]
    return ready > 0 and not bare, {"readyAt": ready, "bare": bare[:3]}


def peer_frame(page, origin):
    for frame in page.frames:
        if frame is not page.main_frame and frame.url.startswith(origin):
            return frame
    return None


def desktop(b, halo, vault):
    MODE.update(halo="ready", halo_delay=1500)
    ctx, page, errors = open_hub(b, 1440, 900)
    w = click_and_watch(page, "chat")
    ok, detail = covered_until_ready(w)
    check("电脑·冷进入：HaloWebUI 说 ready 之前框一直被盖着（不再露出登录页）", ok, detail)
    check("电脑·冷进入：星门是电脑档、播了点火和开门", {"hub-gate", "is-chat", "is-desk", "is-ignite", "is-open"} <= set(w["classes"]) and "is-warm" not in w["classes"], w["classes"])
    check("电脑·冷进入：离港光点从星核出发、遮幅合上停住", page.evaluate("() => window.__flightSeen && window.__coreSeen && window.__holdSeen"),
          page.evaluate("() => [window.__flightSeen, window.__coreSeen, window.__holdSeen]"))
    for text in ("签发通行票据", "接入 HaloWebUI", "验证通行票据", "会话就绪"):
        check("电脑·冷进入：读数里有「%s」" % text, text in w["lines"], w["lines"])
    gone = w["end"] - w["readyAt"]
    check("电脑·冷进入：就绪后约一秒内开门收尾", 0 < gone < 1600, round(gone))
    check("电脑·冷进入：开门后加载层撤掉、星门与离港层都删干净",
          page.evaluate("() => document.getElementById('chatLoading').hidden && !document.querySelector('.hub-gate, .hub-gate-flight') && !document.querySelector('.hub-cut.is-hold')"))
    frame = peer_frame(page, halo)
    check("电脑·冷进入：开门时把 enter 发进框（只发一次）", frame is not None and frame.evaluate("() => window.__enters") == 1, frame and frame.evaluate("() => window.__enters"))

    # 回收藏库：回程光点 + 旧画面收成光圈
    # （无头 Chromium 软件渲染，给带框的页面拍转场快照要几百毫秒，切页本身就晚；所以看「一秒多内出现过」）
    page.evaluate("""() => { window.__backSeen = false; window.__backFlight = false;
        new MutationObserver(() => { if (document.documentElement.classList.contains('gate-back')) window.__backSeen = true;
          if (document.querySelector('.hub-gate-flight.is-core')) window.__backFlight = true; })
          .observe(document.documentElement, { attributes: true, childList: true, subtree: true }); }""")
    page.click('.tab[data-view="bookmarks"]')
    page.wait_for_timeout(1200)
    check("回收藏库：回程镜头（旧画面收成光圈、光点飞回星核）", page.evaluate("() => window.__backSeen && window.__backFlight"),
          page.evaluate("() => [window.__backSeen, window.__backFlight]"))
    check("回收藏库：回程层播完即删", page.evaluate("() => !document.querySelector('.hub-gate-flight') && !document.documentElement.classList.contains('gate-back')"))
    t0 = time.time()
    w = click_and_watch(page, "chat", 4000)
    check("电脑·热进入：照样过一道门（热档）", {"is-warm", "is-ignite", "is-open"} <= set(w["classes"]), w["classes"])
    check("电脑·热进入：读数是此刻的状态", "链路保持" in w["lines"] and "待命" in w["lines"], w["lines"])
    took = (w["end"] - w["t0"])
    check("电脑·热进入：一秒左右收尾", took < 1400, round(took))
    check("电脑·热进入：框又收到一次 enter", frame.evaluate("() => window.__enters") == 2, frame.evaluate("() => window.__enters"))
    check("电脑：页面没有脚本错误", not errors, errors[:3])
    ctx.close()


def skip_and_sidebar(b, halo, vault):
    MODE.update(halo="silent")
    ctx, page, errors = open_hub(b, 1440, 900)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(600)
    check("旧版 HaloWebUI（不说话）：门在等", page.locator("#chatStage .hub-gate").count() == 1 and not page.evaluate("() => document.getElementById('chatLoading').hidden"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(700)
    check("Esc 直接开门（门收掉，加载层接着盖着框）", page.locator(".hub-gate").count() == 0 and not page.evaluate("() => document.getElementById('chatLoading').hidden"))
    page.wait_for_timeout(2900)
    check("旧版 HaloWebUI：load 后 3 秒按兜底撤掉加载层", page.evaluate("() => document.getElementById('chatLoading').hidden"))
    frame = peer_frame(page, halo)
    check("Esc 开门时也把 enter 发进框", frame is not None and frame.evaluate("() => window.__enters") >= 1)
    ctx.close()

    ctx, page, errors = open_hub(b, 1440, 900)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(300)
    page.click('.tab[data-view="settings"]')
    page.wait_for_timeout(250)
    check("播的时候侧栏照样能点：切到系统设置", page.evaluate("() => document.getElementById('view-settings').classList.contains('active')"))
    check("切走后星门与遮幅一并收掉", page.evaluate("() => !document.querySelector('.hub-gate') && !document.querySelector('.hub-cut.is-hold')"))
    check("播放中切走：页面没有脚本错误", not errors, errors[:3])
    ctx.close()

    MODE.update(halo="form")
    ctx, page, errors = open_hub(b, 1440, 900)
    w = click_and_watch(page, "chat", 5000)
    check("免登录没接通（框里是登录表单）：门变成警示色、读数写明，然后开门露出登录页",
          "is-fail" in w["classes"] and "需要登录" in w["lines"] and page.evaluate("() => document.getElementById('chatLoading').hidden"), (w["classes"], w["lines"]))
    ctx.close()


def touch_and_fold(b, halo, vault):
    # 折叠屏内屏：近正方形、触屏、宽度 > 760（侧栏在左边）
    MODE.update(halo="ready", halo_delay=1200)
    ctx, page, errors = open_hub(b, 880, 812, mobile=True)
    w = click_and_watch(page, "chat")
    ok, detail = covered_until_ready(w)
    check("内屏（触屏宽）：就绪前一直盖着", ok, detail)
    check("内屏：触屏档（不是电脑档），播完整", {"is-touch", "is-ignite", "is-open"} <= set(w["classes"]) and "is-desk" not in w["classes"], w["classes"])
    check("内屏：离港光点从侧栏图标出发（触屏不画画布星核）", page.evaluate("() => window.__flightSeen && !window.__coreSeen"))
    check("内屏：没有横向溢出", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"))
    page.click('.tab[data-view="bookmarks"]')
    page.wait_for_timeout(900)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(120)
    styles = page.evaluate("""() => { const g = document.querySelector('.hub-gate'); if (!g) return null;
        return { filter: getComputedStyle(g.querySelector('.hub-gate__glyph')).filter, grid: getComputedStyle(g.querySelector('.hub-gate__grid')).display, warm: g.classList.contains('is-warm') }; }""")
    check("内屏·热进入：不用 filter 光晕、不画网格地面", styles and styles["filter"] == "none" and styles["grid"] == "none" and styles["warm"], styles)
    page.wait_for_timeout(1200)
    check("内屏：页面没有脚本错误", not errors, errors[:3])
    ctx.close()

    # 外屏：窄竖屏，侧栏变底部标签栏
    MODE.update(halo="ready", halo_delay=1500)
    ctx, page, errors = open_hub(b, 400, 880, mobile=True)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(1300)
    info = page.evaluate("""() => { const g = document.querySelector('.hub-gate'); if (!g) return null;
        const lines = [...g.querySelectorAll('.hub-gate__lines p')].filter(p => getComputedStyle(p).display !== 'none').length;
        const bar = document.querySelector('.hub-cut.is-hold .hub-cut__bar'), side = document.querySelector('.sidebar').getBoundingClientRect();
        return { narrow: g.classList.contains('is-narrow'), lines, nodes: g.querySelectorAll('.hub-gate__glyph .n').length,
                 bar: bar ? bar.getBoundingClientRect().height / innerHeight : 0, bottom: g.getBoundingClientRect().bottom, barTop: side.top,
                 svg: g.querySelector('.hub-gate__svg').getBoundingClientRect().width }; }""")
    check("外屏：窄屏档，读数最多两行、星座节点减到 14 个", info and info["narrow"] and info["lines"] <= 2 and info["nodes"] == 14, info)
    check("外屏：遮幅变细（约 7vh）", info and 0.05 < info["bar"] < 0.09, info)
    check("外屏：星门不压到底部标签栏、线稿不超过屏宽", info and info["bottom"] <= info["barTop"] + 1 and info["svg"] <= 400 * 0.72 + 1, info)
    check("外屏：没有横向溢出", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"))
    page.wait_for_timeout(1500)
    ctx.close()

    # 折叠 / 展开：播到一半宽度档位变了 → 立即开门收尾
    MODE.update(halo="ready", halo_delay=4000)
    ctx, page, errors = open_hub(b, 880, 812, mobile=True)
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(500)
    page.set_viewport_size({"width": 400, "height": 880})
    page.wait_for_timeout(250)
    check("折叠（内屏 → 外屏）：正在播的门立即开门", page.evaluate("() => { const g = document.querySelector('.hub-gate'); return !g || g.classList.contains('is-open'); }"))
    page.wait_for_timeout(600)
    check("折叠：门删干净、遮幅拉开", page.evaluate("() => !document.querySelector('.hub-gate') && !document.querySelector('.hub-cut.is-hold')"))
    check("折叠：加载层仍然盖着（框里还没好）", not page.evaluate("() => document.getElementById('chatLoading').hidden"))
    page.wait_for_timeout(3600)
    check("折叠：就绪后加载层撤掉", page.evaluate("() => document.getElementById('chatLoading').hidden"))
    check("折叠：页面没有脚本错误", not errors, errors[:3])
    ctx.close()


def prefs(b, halo, vault):
    MODE.update(halo="ready", halo_delay=1200)
    ctx, page, errors = open_hub(b, 1440, 900, reduced_motion="reduce")
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(200)
    still = page.evaluate("""() => { const g = document.querySelector('.hub-gate'); if (!g) return null;
        const e = g.querySelector('.hub-gate__glyph .e');
        return { still: g.classList.contains('is-still'), offset: parseFloat(getComputedStyle(e).strokeDashoffset), anim: getComputedStyle(e).animationName }; }""")
    check("减弱动态效果：静止的一帧——线稿已经画好、没有动画", still and still["still"] and still["offset"] == 0 and still["anim"] == "none", still)
    check("减弱动态效果：没有离港光点、遮幅不动", not page.evaluate("() => !!document.querySelector('.hub-gate-flight, .hub-cut.is-hold')"))
    page.wait_for_timeout(2200)
    check("减弱动态效果：就绪后门收掉、加载层撤掉", page.evaluate("() => !document.querySelector('.hub-gate') && document.getElementById('chatLoading').hidden"))
    ctx.close()

    ctx, page, errors = open_hub(b, 1440, 900, cookies={"bh_scifi": "off"})
    w = click_and_watch(page, "chat")
    ok, detail = covered_until_ready(w)
    check("科幻特效关：没有门", not w["classes"], w["classes"])
    check("科幻特效关：加载层照样等 HaloWebUI 说 ready 才撤", ok, detail)
    frame = peer_frame(page, halo)
    check("科幻特效关：就绪时直接把 enter 发进框", frame is not None and frame.evaluate("() => window.__enters") == 1)
    ctx.close()


def vault_entry(b, halo, vault):
    MODE.update(vault_delay=900, halo="ready", halo_delay=800)
    ctx, page, errors = open_hub(b, 1440, 900)
    w = click_and_watch(page, "vault")
    ok, detail = covered_until_ready(w)
    check("笔记·冷进入：WebObsidian 说 ready 之前一直盖着", ok, detail)
    check("笔记：神经晶体（笔记档的门），播完整", {"is-vault", "is-desk", "is-ignite", "is-open"} <= set(w["classes"]), w["classes"])
    check("笔记：标题从乱码解出「NEURAL · VAULT」", w["title"] == "NEURAL · VAULT", w["title"])
    for text in ("签发通行证", "解锁笔记库", "图谱同步"):
        check("笔记·冷进入：读数里有「%s」" % text, text in w["lines"], w["lines"])
    frame = peer_frame(page, vault)
    check("笔记：开门时把 enter 发进框", frame is not None and frame.evaluate("() => window.__enters") == 1, frame and frame.evaluate("() => window.__enters"))
    # 笔记 → 聊天：换一道门，不叠两道
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(200)
    check("笔记 → 聊天：只剩聊天这一道门", page.evaluate("() => document.querySelectorAll('.hub-gate').length === 1 && !!document.querySelector('#chatStage .hub-gate')"))
    page.wait_for_timeout(2600)
    page.click('.tab[data-view="vault"]')
    page.wait_for_timeout(120)
    check("笔记·热进入：热档的门", page.evaluate("() => !!document.querySelector('#vaultStage .hub-gate.is-warm')"))
    page.wait_for_timeout(1300)
    check("笔记·热进入：框又收到一次 enter", frame.evaluate("() => window.__enters") == 2, frame.evaluate("() => window.__enters"))
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
        for fn in (desktop, skip_and_sidebar, touch_and_fold, prefs, vault_entry):
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

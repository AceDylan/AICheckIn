# -*- coding: utf-8 -*-
"""「AI 聊天」标签页的真实浏览器回归：收藏库登录后才出现、框里是 HaloWebUI、票据走 # 后面、外壳的响应头。

    python tests/browser/verify_chat.py

这几件事只有真浏览器说了算：iframe 的 src / sandbox 由页面脚本拼、CSP 的 frame-src 与对面的
frame-ancestors 由浏览器执行、# 后面的票据到没到框里的页面、手机端的框会不会被底部标签栏盖住。

布置：一个隔离的本站实例（配了 HUB_CHAT_URL + 共享密钥），外加一个扮演 HaloWebUI 的假站——
它的 /auth 页把收到的地址片段写进页面（好让测试读出票据），响应头里放行本站嵌入，
/api/v1/hub/handshake 按文档里的格式独立验一张 probe 票。再起一个没配密钥的实例，看「签不了票」的退路。
共享密钥每次运行现生成，只活在本进程与子进程的环境变量里。
"""
import base64
import hashlib
import hmac
import http.server
import json
import os
import secrets
import threading
import time

from common import *   # noqa: F401,F403
from common import _free_port, DATA, WORK

RESULTS = []
SECRET = secrets.token_hex(32)
HANDSHAKES = []   # 假 HaloWebUI 收到的 probe 票据：(是否验签通过, 签发方, 接收方)


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + ((" — " + str(detail)[:200]) if detail and not ok else ""), flush=True)


def _unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")


def verify_ticket(ticket):
    """按 SECURITY.md 里写的格式独立验票（不 import app）。通过返回各字段，否则 None。"""
    parts = str(ticket or "").split(".")
    if len(parts) != 7 or parts[0] != "v2":
        return None
    key = hashlib.sha256(("hub-chat-admin|" + SECRET).encode("utf-8")).digest()
    expected = hmac.new(key, ".".join(parts[:6]).encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[6]):
        return None
    return {"purpose": parts[1], "exp": int(parts[2]), "nonce": parts[3], "issuer": _unb64(parts[4]), "audience": _unb64(parts[5])}


class _Halo(http.server.BaseHTTPRequestHandler):
    """假 HaloWebUI：/auth 与 / 各一页，把地址片段写进 <pre id="hash">；/api/v1/hub/handshake 验 probe 票。"""
    hub_origin = ""

    def _page(self, title):
        body = ('<!doctype html><meta charset="utf-8"><title>%s</title><body style="margin:0;font:14px sans-serif">'
                '<h1>%s</h1><pre></pre><script>document.querySelector("pre").textContent = location.hash;</script>'
                % (title, title)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self' " + self.hub_origin)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._page("halo-auth" if self.path.split("?")[0] == "/auth" else "halo-home")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        try:
            ticket = json.loads(raw.decode("utf-8")).get("ticket", "")
        except ValueError:
            ticket = ""
        claims = verify_ticket(ticket)
        ok = self.path == "/api/v1/hub/handshake" and claims is not None and claims["purpose"] == "probe"
        HANDSHAKES.append((ok, claims and claims["issuer"], claims and claims["audience"]))
        payload = ({"ok": True, "frame_ancestors": [self.hub_origin], "session_ttl": 43200, "session_user_ready": True}
                   if ok else {"detail": {"error": "ticket_refused", "reason": "signature"}})
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def start_halo(hub_origin):
    _Halo.hub_origin = hub_origin
    server = http.server.ThreadingHTTPServer(("127.0.0.1", _free_port()), _Halo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_address[1]


def ready(page, base=None):
    page.goto((base or BASE) + "/")
    page.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED")
    page.wait_for_timeout(150)
    return page


def tab_hidden(page, view):
    return page.evaluate("(v) => { const t = document.querySelector(`.tab[data-view=\"${v}\"]`); return !t || t.hidden; }", view)


def active_view(page):
    return page.evaluate("() => (document.querySelector('.view.active') || {}).id")


def chat_frame(page, timeout=8000):
    """本站页面里 #chatStage 下的 iframe 元素，等它出现并且 src 已经指向假站。"""
    page.wait_for_function("() => { const f = document.querySelector('#chatStage iframe'); return !!(f && f.src); }", timeout=timeout)
    return page.query_selector("#chatStage iframe")


def framed_page(page, halo, timeout=8000):
    """iframe 里那份假 HaloWebUI 页面（被浏览器拒掉的话这里会超时，返回 None）。"""
    deadline = time.time() + timeout / 1000.0
    while time.time() < deadline:
        for frame in page.frames:
            if frame is not page.main_frame and frame.url.startswith(halo):
                try:
                    frame.wait_for_selector("pre", timeout=timeout)
                    return frame
                except Exception:
                    return None
        page.wait_for_timeout(100)
    return None


def locked_visitor(b, halo):
    ctx = new_context(b, 1280, 800, unlocked=False)
    page = ready(ctx.new_page())
    check("未解锁：落在解锁面板（系统设置）", active_view(page) == "view-settings", active_view(page))
    check("未解锁：侧栏没有「收藏库」", tab_hidden(page, "bookmarks"))
    check("未解锁：侧栏没有「AI 聊天」", tab_hidden(page, "chat"))
    check("未解锁：侧栏没有「签到中心」", tab_hidden(page, "checkin"))
    state = page.evaluate("() => ({ locked: STATE.locked, private: STATE.private, n: STATE.bookmarks.length, g: STATE.link_groups.length, chat: STATE.chat })")
    check("未解锁：页面里一条收藏都没有，也不知道聊天在哪", state == {"locked": True, "private": True, "n": 0, "g": 0, "chat": None}, state)
    check("未解锁：页面文本里没有种子数据的网址", "dash.example" not in page.evaluate("() => document.body.innerText") and halo not in page.content())
    page.goto(BASE + "/#bookmarks")
    page.wait_for_timeout(300)
    check("未解锁：地址栏手敲 /#bookmarks 也只到解锁面板", active_view(page) == "view-settings", active_view(page))
    page.evaluate("() => switchView('chat')")
    page.wait_for_timeout(200)
    check("未解锁：脚本里硬切 chat 也不放行", active_view(page) == "view-settings" and page.evaluate("() => !document.querySelector('#chatStage iframe')"))
    status = ctx.request.post(BASE + "/api/chat/ticket").status
    check("未解锁：签票接口 403", status == 403, status)
    page.close()
    ctx.close()


def admin_desktop(b, halo):
    ctx = new_context(b, 1280, 800, unlocked=True)
    page = ready(ctx.new_page())
    check("已解锁：「收藏库」回来了", not tab_hidden(page, "bookmarks"))
    check("已解锁：「AI 聊天」出现", not tab_hidden(page, "chat"))
    check("已解锁：首页有收藏", page.evaluate("() => STATE.bookmarks.length + STATE.link_groups.length") > 0)

    page.click('.tab[data-view="chat"]')
    frame_el = chat_frame(page)
    check("点「AI 聊天」：切到聊天页、整块铺满", active_view(page) == "view-chat" and page.evaluate("() => document.documentElement.classList.contains('chat-on')"))
    src = frame_el.get_attribute("src")
    check("iframe 指向 HaloWebUI 的 /auth，票据在 # 后面", src.startswith(halo + "/auth#hub_ticket=v2.chat."), src)
    claims = verify_ticket(src.split("/auth#hub_ticket=", 1)[1])
    check("票据验签通过：签发方 = 本站，接收方 = HaloWebUI，60 秒内", bool(claims) and claims["issuer"] == BASE and claims["audience"] == halo and 0 < claims["exp"] - time.time() <= 61, claims)
    sandbox = (frame_el.get_attribute("sandbox") or "").split()
    check("sandbox 不给 allow-top-navigation", sandbox and "allow-top-navigation" not in sandbox and "allow-same-origin" in sandbox, sandbox)
    check("referrerpolicy=no-referrer", frame_el.get_attribute("referrerpolicy") == "no-referrer")
    framed = framed_page(page, halo)
    check("对面的 frame-ancestors 放行了本站：框里渲染出 HaloWebUI 的登录页", framed is not None and framed.title() == "halo-auth", framed and framed.url)
    if framed is not None:
        got = framed.evaluate("() => document.querySelector('pre').textContent")
        check("# 后面的票据完整到达框里的页面", got == "#" + "hub_ticket=" + src.split("/auth#hub_ticket=", 1)[1], got[:60])
    page.wait_for_timeout(300)
    check("免登录正常时没有提示条", page.evaluate("() => document.getElementById('chatNote').hidden"))
    box = frame_el.bounding_box()
    vw, vh = page.evaluate("() => [innerWidth, innerHeight]")
    # 开壁纸时框贴到视口底边；默认不开壁纸时内容区是一块四周留 8px 底色的画布，框到画布底边为止。
    gap = vh - (box["y"] + box["height"]) if box else -1
    check("框铺到视口底部（或画布底边）、不溢出", box and -1 < gap < 11 and box["x"] + box["width"] <= vw + 1 and box["height"] > vh * 0.7, box)
    check("页面没有纵向滚动条", page.evaluate("() => document.documentElement.scrollHeight <= innerHeight + 1"))

    # 握手：本站后台向假 HaloWebUI 送了一张 probe 票，假站按文档格式验过
    for _ in range(30):
        if HANDSHAKES:
            break
        page.wait_for_timeout(100)
    check("启动握手：HaloWebUI 收到 probe 票且验签通过", HANDSHAKES and HANDSHAKES[0][0] is True and HANDSHAKES[0][1] == BASE and HANDSHAKES[0][2] == halo, HANDSHAKES[:1])

    # 切走再切回：框不重建（聊到一半不丢）
    page.evaluate("() => { document.querySelector('#chatStage iframe').__hubKeep = 'kept'; }")
    page.click('.tab[data-view="bookmarks"]')
    page.wait_for_timeout(200)
    check("切到收藏库：聊天页收起、铺满态解除", active_view(page) == "view-bookmarks" and not page.evaluate("() => document.documentElement.classList.contains('chat-on')"))
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(200)
    check("切回来：还是同一个框", page.evaluate("() => (document.querySelector('#chatStage iframe') || {}).__hubKeep") == "kept")

    # 重新载入：新的一张票
    page.click("#chatReload")
    page.wait_for_function("(old) => { const f = document.querySelector('#chatStage iframe'); return !!(f && f.src && f.src !== old); }", arg=src)
    src2 = page.query_selector("#chatStage iframe").get_attribute("src")
    claims2 = verify_ticket(src2.split("/auth#hub_ticket=", 1)[1])
    check("「重新载入」换了一张新票（nonce 不同）", bool(claims2) and claims2["nonce"] != claims["nonce"])
    check("「新标签页打开」指向 HaloWebUI 首页（不带票据）", page.get_attribute("#chatPopout", "href") == halo + "/")

    # 锁定：入口与框一起收起
    page.evaluate("() => fetch('/api/logout', { method: 'POST' })")
    page.wait_for_timeout(200)
    page.evaluate("() => loadConfigs()")
    page.wait_for_function("() => STATE.locked === true")
    page.wait_for_timeout(200)
    check("锁定后：「AI 聊天」「收藏库」都收起，框被卸掉，回到解锁面板",
          tab_hidden(page, "chat") and tab_hidden(page, "bookmarks") and page.evaluate("() => !document.querySelector('#chatStage iframe')") and active_view(page) == "view-settings",
          (tab_hidden(page, "chat"), tab_hidden(page, "bookmarks"), active_view(page)))
    page.close()
    ctx.close()


def admin_mobile(b, halo):
    ctx = new_context(b, 390, 844, mobile=True, unlocked=True)
    page = ready(ctx.new_page())
    check("手机：底栏有「AI 聊天」", not tab_hidden(page, "chat"))
    page.click('.tab[data-view="chat"]')
    frame_el = chat_frame(page)
    page.wait_for_timeout(300)
    box = frame_el.bounding_box()
    bar_top = page.evaluate("() => document.querySelector('.sidebar').getBoundingClientRect().top")
    check("手机：框的下沿不被底部标签栏盖住", box and box["y"] + box["height"] <= bar_top + 1, (box, bar_top))
    check("手机：整页没被撑宽", page.evaluate("() => document.documentElement.scrollWidth") <= 390)
    check("手机：框里照样是 HaloWebUI", framed_page(page, halo) is not None)
    page.close()
    ctx.close()


def headers(halo):
    import urllib.request
    resp = urllib.request.urlopen(BASE + "/", timeout=5)
    csp = resp.headers.get("Content-Security-Policy", "")
    check("外壳 CSP：frame-src 只放行 HaloWebUI", "frame-src 'self' " + halo + ";" in csp, csp)
    check("外壳 CSP：本站自己 frame-ancestors 'none'", csp.endswith("frame-ancestors 'none'"), csp)
    check("外壳 X-Frame-Options: DENY", resp.headers.get("X-Frame-Options") == "DENY", resp.headers.get("X-Frame-Options"))
    check("响应头里没有 Vary: Sec-Fetch-Dest（不再分两种外壳）", "Sec-Fetch-Dest" not in resp.headers.get("Vary", ""))


def no_secret(b, halo):
    """没配密钥的实例：标签页照常，框里是 HaloWebUI 首页，顶部说明「需要自己登录」。"""
    port = _free_port()
    env_backup = os.environ.pop("HUB_TRUSTED_EMBED_ADMIN_SECRET", None)
    try:
        base = start_server(port=port, data_dir=os.path.join(WORK, "data-nosecret")).rstrip("/")
    finally:
        if env_backup is not None:
            os.environ["HUB_TRUSTED_EMBED_ADMIN_SECRET"] = env_backup
    ctx = new_context(b, 1280, 800, unlocked=True, base=base)
    page = ready(ctx.new_page(), base)
    check("没配密钥：「AI 聊天」仍然出现", not tab_hidden(page, "chat"))
    page.click('.tab[data-view="chat"]')
    page.wait_for_function("() => { const f = document.querySelector('#chatStage iframe'); return !!(f && f.src); }")
    src = page.query_selector("#chatStage iframe").get_attribute("src")
    check("没配密钥：框里是 HaloWebUI 首页，不带票据", src == halo + "/", src)
    page.wait_for_timeout(200)
    note = page.evaluate("() => [document.getElementById('chatNote').hidden, document.getElementById('chatNoteText').textContent]")
    check("没配密钥：顶部说明需要自己登录", note[0] is False and "HUB_TRUSTED_EMBED_ADMIN_SECRET" in note[1], note)
    page.click("#chatNoteClose")
    page.reload()
    page.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED")
    page.click('.tab[data-view="chat"]')
    page.wait_for_timeout(400)
    check("提示关掉后刷新不再弹同一条", page.evaluate("() => document.getElementById('chatNote').hidden"))
    page.close()
    ctx.close()


def main():
    t0 = time.time()
    halo = start_halo(BASE)
    # start_server() 把当前进程的环境变量带给子进程：地址与密钥只活在这里，不落盘。
    os.environ["HUB_CHAT_URL"] = halo
    os.environ["HUB_TRUSTED_EMBED_ADMIN_SECRET"] = SECRET
    os.environ.pop("HUB_PUBLIC_ORIGIN", None)
    os.environ.pop("HUB_PUBLIC_LIBRARY", None)
    os.environ.pop("GYQD_PRIVATE", None)
    start_server()
    headers(halo)
    with sync_playwright() as p:
        b = launch(p)
        for fn in (locked_visitor, admin_desktop, admin_mobile, no_secret):
            print("==", fn.__name__, flush=True)
            try:
                fn(b, halo)
            except Exception as exc:
                check(fn.__name__ + " 运行完毕", False, repr(exc))
        b.close()
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS)} checks, {len(failed)} failed, {time.time() - t0:.0f}s")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""被 HaloWebUI 嵌入的真实浏览器回归：iframe 白名单、票据换管理员会话、被嵌入时外链一律新标签页。

    python tests/browser/verify_embed.py

这几件事只有真浏览器说了算：CSP 的 frame-ancestors 由浏览器执行，跨源 iframe 里 SameSite=Lax 的
Cookie 收不收、sandbox 属性放不放行脚本与弹窗，单元测试里都看不出来。

布置：一个隔离的本站实例（带白名单与共享密钥），外加两个只有一张 iframe 的「宿主页」——
一个在白名单里（扮演 HaloWebUI 的 /hub 页面，iframe 属性与它一致），一个不在。三者同主机不同端口，
和线上一样是「跨源但同站」。共享密钥每次运行现生成，只活在本进程与子进程的环境变量里；
票据按 HaloWebUI 那边的算法在这里独立签发。
"""
import hashlib
import hmac
import http.server
import os
import secrets
import threading
import time
import urllib.parse

from common import *   # noqa: F401,F403
from common import _free_port

RESULTS = []
SECRET = secrets.token_hex(32)
# 与 HaloWebUI src/routes/(app)/hub/+page.svelte 里的 iframe 保持一致。
SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads allow-modals"


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + ((" — " + str(detail)[:200]) if detail and not ok else ""), flush=True)


def ticket(purpose="enter", ttl=120):
    exp = str(int(time.time()) + ttl)
    nonce = secrets.token_urlsafe(18)
    key = hashlib.sha256(("hub-embed-admin|" + SECRET).encode("utf-8")).digest()
    message = ".".join(("v1", purpose, exp, nonce))
    return message + "." + hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def enter_url(value=None):
    return BASE + "/embed/enter?ticket=" + urllib.parse.quote(value or ticket(), safe="")


class _Host(http.server.BaseHTTPRequestHandler):
    """宿主页：/?src=<地址> → 一张铺满的 iframe。"""

    def do_GET(self):
        src = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("src", [""])[0]
        body = ('<!doctype html><meta charset="utf-8"><title>host</title><body style="margin:0">'
                '<iframe id="hub-frame" src="%s" sandbox="%s" referrerpolicy="no-referrer" allow="clipboard-write" '
                'style="width:100vw;height:100vh;border:0"></iframe>' % (src.replace('"', "&quot;"), SANDBOX)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_host():
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Host)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % port


def host_page(ctx, host, src):
    page = ctx.new_page()
    page.goto(host + "/?src=" + urllib.parse.quote(src, safe=""))
    return page


def hub_frame(page, timeout=8000):
    """宿主页里的那张 iframe；等它真的渲染出本站的页面（被浏览器拒掉的话这里会超时，返回 None）。"""
    deadline = time.time() + timeout / 1000.0
    while time.time() < deadline:
        for frame in page.frames:
            if frame is not page.main_frame and frame.url.startswith(BASE):
                try:
                    frame.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED", timeout=timeout)
                    return frame
                except Exception:
                    return None
        page.wait_for_timeout(100)
    return None


def unlocked(frame):
    return frame.evaluate("() => fetch('/api/configs').then(r => r.json()).then(d => d.admin_unlocked)")


def embed(b, allowed, stranger):
    ctx = new_context(b, 1280, 800, unlocked=False)
    ctx.add_cookies([{"name": "bh_open", "value": "same", "url": BASE}])   # 这台设备平时选的是「当前页打开」

    # ---- 白名单里的宿主页：嵌得进来，并且已经是管理员 ----
    page = host_page(ctx, allowed, enter_url())
    frame = hub_frame(page)
    check("白名单里的站点：iframe 渲染出本站", frame is not None, [f.url for f in page.frames])
    if frame is None:
        ctx.close()
        return
    check("票据换到了管理员会话（框里没输过密码）", unlocked(frame) is True)
    check("换票后地址里不再带票据", "ticket" not in frame.url, frame.url)
    check("外壳带 data-embedded 标记", frame.evaluate("() => document.documentElement.getAttribute('data-embedded')") == "1")
    cookie = next((c for c in ctx.cookies(BASE) if c["name"] == "gyqd_session"), None)
    check("会话 Cookie 是 HttpOnly + SameSite=Lax", bool(cookie) and cookie["httpOnly"] and cookie["sameSite"] == "Lax", cookie and {k: cookie[k] for k in ("httpOnly", "sameSite")})
    check("会话比密码解锁的 30 天短（默认 12 小时）", bool(cookie) and 0 < cookie["expires"] - time.time() <= 12 * 3600 + 60, cookie and cookie["expires"] - time.time())

    # ---- 被嵌入：外链一律新标签页，哪怕偏好是「当前页打开」 ----
    check("被嵌入时 openMode() 恒为 new", frame.evaluate("() => openMode()") == "new")
    links = frame.evaluate("() => [...document.querySelectorAll('#homeList .home-tile a[href^=\"http\"]')].map(a => a.target)")
    check("首页图标全部 target=_blank", len(links) > 5 and set(links) == {"_blank"}, (len(links), sorted(set(links))))
    with page.expect_popup() as popup:
        frame.evaluate("() => openUrl('https://docs.example.com/')")
    check("openUrl() 开的是新标签页", popup.value is not None)
    popup.value.close()
    check("框本身没有被导航走", frame.url.startswith(BASE), frame.url)
    frame.evaluate("() => { const a = document.createElement('a'); a.href = 'https://stray.example.com/'; a.id = 'strayLink'; a.textContent = 'x'; document.body.appendChild(a); }")
    with page.expect_popup() as popup:
        frame.evaluate("() => document.querySelector('a[href=\"https://stray.example.com/\"]').click()")
    popup.value.close()
    check("没带 target 的外链在点击那一刻被补成新标签页", frame.url.startswith(BASE), frame.url)
    frame.evaluate("() => switchView('settings')")
    same = frame.evaluate("() => { const b = document.querySelector('#openModeSeg [data-open-mode=\"same\"]'); return { disabled: b.disabled, active: b.classList.contains('active') }; }")
    check("设置页里「当前页打开」不可选", same == {"disabled": True, "active": False}, same)

    # ---- Service Worker 激活之后再嵌一次：缓存不会把「禁止嵌入」的旧外壳端出来 ----
    try:
        frame.evaluate("() => navigator.serviceWorker.ready.then(() => true)")
        again = host_page(ctx, allowed, enter_url())
        frame2 = hub_frame(again)
        check("SW 激活后重新嵌入仍然成功且已解锁", frame2 is not None and unlocked(frame2) is True)
        if frame2 is not None:
            frame2.evaluate("() => location.assign('/')")     # 不带查询串 → 走 SW 缓存里的外壳
            again.wait_for_timeout(800)
            frame3 = hub_frame(again)
            check("框里回到不带查询串的首页（SW 缓存的外壳）照样嵌得进来，标记由页面脚本补上",
                  frame3 is not None and frame3.evaluate("() => document.documentElement.getAttribute('data-embedded')") == "1")
        again.close()
    except Exception as exc:
        check("SW 激活后重新嵌入", False, repr(exc))
    page.close()
    ctx.close()

    # ---- 不在白名单里的宿主页：浏览器直接拒绝 ----
    ctx = new_context(b, 1280, 800, unlocked=False)
    page = host_page(ctx, stranger, BASE + "/?embed=1")
    check("白名单之外的站点：iframe 被浏览器拒绝", hub_frame(page, timeout=2500) is None, [f.url for f in page.frames])
    page.close()
    ctx.close()


def tickets(b, allowed, stranger):
    # ---- 一张票据只能用一次 ----
    spent = ticket()
    ctx = new_context(b, 1280, 800, unlocked=False)
    first = hub_frame(host_page(ctx, allowed, enter_url(spent)))
    check("第一次使用：通过", first is not None and unlocked(first) is True)
    ctx.close()
    ctx = new_context(b, 1280, 800, unlocked=False)
    page = host_page(ctx, allowed, enter_url(spent))
    replay = hub_frame(page)
    check("同一张票据第二次使用：照样进得来，但没解锁", replay is not None and unlocked(replay) is False)
    if replay is not None:
        page.wait_for_timeout(300)
        toast = replay.evaluate("() => [...document.querySelectorAll('.toast')].map(t => t.textContent).join('|')")
        check("页面说明了为什么没自动登录", "已被用过" in toast, toast)
    ctx.close()

    # ---- 过期 / 伪造 / probe 票据 ----
    good = ticket()
    for label, value in (("过期", ticket(ttl=-5)), ("用途不符（probe）", ticket("probe")),
                         ("签名被改", good[:-1] + ("0" if good[-1] != "0" else "1"))):
        ctx = new_context(b, 1280, 800, unlocked=False)
        frame = hub_frame(host_page(ctx, allowed, enter_url(value)))
        check(label + "的票据：不解锁", frame is not None and unlocked(frame) is False)
        ctx.close()

    # ---- 「在新标签页打开」的兜底入口：顶层页面同样能换票，落在干净的首页 ----
    ctx = new_context(b, 1280, 800, unlocked=False)
    ctx.add_cookies([{"name": "bh_open", "value": "same", "url": BASE}])
    page = ctx.new_page()
    page.goto(enter_url())
    page.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED")
    check("顶层打开：已解锁，地址是干净的首页", unlocked(page) is True and page.url.rstrip("/") == BASE, page.url)
    check("顶层页面没有 data-embedded，偏好照常生效", page.evaluate("() => [document.documentElement.getAttribute('data-embedded'), openMode()]") == [None, "same"])
    ctx.close()


def main():
    t0 = time.time()
    allowed, stranger = start_host(), start_host()
    # start_server() 把当前进程的环境变量带给子进程：白名单与密钥只活在这里，不落盘。
    os.environ["HUB_FRAME_ANCESTORS"] = allowed
    os.environ["HUB_TRUSTED_EMBED_ADMIN_SECRET"] = SECRET
    start_server()
    with sync_playwright() as p:
        b = launch(p)
        for fn in (embed, tickets):
            print("==", fn.__name__, flush=True)
            try:
                fn(b, allowed, stranger)
            except Exception as exc:
                check(fn.__name__ + " 运行完毕", False, repr(exc))
        b.close()
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS)} checks, {len(failed)} failed, {time.time() - t0:.0f}s")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

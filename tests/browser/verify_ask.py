# -*- coding: utf-8 -*-
"""「发送到 AI 聊天 / 存入笔记」与「在笔记里搜索」的真实浏览器回归。

    python tests/browser/verify_ask.py

这几件事只有真浏览器说得清：编辑框里最后到底是哪几个字、点「发送」之后 iframe 的 src
长什么样（问题编了两次码，解回来必须一字不差）、下拉里那一行「在笔记里搜索」到底在什么
时候才真的发请求——边打边搜的话，每敲一个字就是一次跨机调用。

布置：一个隔离的本站实例，外加两个假站——
  - 假 HaloWebUI：/auth 与 / 各一页，响应头里放行本站嵌入（真站不在这台机器上）；
  - 假 WebObsidian：按 Agent API 的形状应答，并把收到的每一次请求记下来，
    好断言「没选中那一行之前一次都没发过」。
再起一个只配了聊天、没配笔记的实例，看「少一半功能」时页面长什么样。
"""
import http.server
import json
import os
import secrets
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse

from common import *   # noqa: F401,F403
from common import _free_port, DATA, WORK

RESULTS = []
SECRET = secrets.token_hex(32)
VAULT_KEY = "wok_" + secrets.token_hex(16)     # 每次运行现生成，只活在本进程与子进程的环境变量里
INBOX = "收件箱"
VAULT_CALLS = []      # 假 WebObsidian 收到的每一次请求：{method, path, body, key}


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + ((" — " + str(detail)[:200]) if detail and not ok else ""), flush=True)


# ---------------------------------------------------------------------------
# 两个假站
# ---------------------------------------------------------------------------

class _Halo(http.server.BaseHTTPRequestHandler):
    """假 HaloWebUI：够当一个 iframe 的落点，并把收到的完整地址写进页面。"""
    hub_origin = ""

    def do_GET(self):
        body = ('<!doctype html><meta charset="utf-8"><title>halo</title>'
                '<body style="margin:0;font:14px sans-serif"><pre></pre>'
                '<script>document.querySelector("pre").textContent = location.href;</script>').encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self' " + self.hub_origin)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        data = json.dumps({"ok": True, "frame_ancestors": [self.hub_origin],
                           "session_ttl": 43200, "session_user_ready": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class _Vault(http.server.BaseHTTPRequestHandler):
    """假 WebObsidian Agent API：记下每一次请求，搜索回两条命中（其中一条路径是坏的）。"""

    def _handle(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        VAULT_CALLS.append({"method": self.command, "path": self.path, "body": raw,
                            "key": self.headers.get("X-API-Key") or ""})
        if not self.headers.get("X-API-Key"):
            payload, status = {"error": "no key"}, 401
        elif self.path.startswith("/api/v1/search"):
            payload, status = {"hits": [
                {"path": "项目/季度计划.md", "title": "季度计划", "snippet": "本季度   要做的事"},
                {"path": "../../etc/passwd", "title": "不该出现的"},
            ]}, 200
        else:
            payload, status = {"ok": True}, 200
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_PUT = do_PATCH = do_POST = _handle

    def log_message(self, *args):
        pass


def _serve(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", _free_port()), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_address[1]


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def ready(page, base=None):
    page.goto((base or BASE) + "/")
    page.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED")
    page.wait_for_timeout(150)
    return page


def composer_open(page):
    return page.evaluate("() => document.querySelector('.modal-mask.show') === document.getElementById('askModal')")


def composer_text(page):
    return page.evaluate("() => document.getElementById('askText').value")


def close_composer(page):
    page.click("#askCancel")
    page.wait_for_timeout(120)


def type_search(page, text):
    box = page.locator("#homeSearch")
    box.click()
    box.fill("")
    box.type(text, delay=12)
    page.wait_for_timeout(250)


def search_rows(page):
    return page.evaluate("() => HOME_SEARCH.rows.map(r => r.type)")


def pick_row(page, kind):
    index = page.evaluate("(k) => HOME_SEARCH.rows.findIndex(r => r.type === k)", kind)
    if index < 0:
        return False
    page.evaluate("(i) => runHomeSearchRow(HOME_SEARCH.rows[i])", index)
    page.wait_for_timeout(200)
    return True


def prompt_in(url):
    """把 iframe 的 src 按 HaloWebUI 的读法解一遍，取出问题和票据。"""
    parsed = urlparse(url)
    redirect = parse_qs(parsed.query).get("redirect", [""])[0]
    return (parse_qs(urlparse(redirect).query).get("q", [""])[0],
            parse_qs(parsed.fragment).get("hub_ticket", [""])[0],
            redirect)


def open_todo_deck(page):
    """待办组件在窄屏收在标签条里；宽屏直接就在。两种都保证它展开。"""
    tab = page.locator('#deckTabs [data-deck-tab="todo"]')
    if tab.count() and tab.is_visible() and tab.get_attribute("aria-expanded") != "true":
        tab.click()
        page.wait_for_timeout(300)
    page.wait_for_selector("#todoList", timeout=5000)


def open_memo_deck(page):
    tab = page.locator('#deckTabs [data-deck-tab="memo"]')
    if tab.count() and tab.is_visible() and tab.get_attribute("aria-expanded") != "true":
        tab.click()
        page.wait_for_timeout(300)


def put_memo_on_home(page):
    """便签默认不在首页：把它加进本机的组件顺序里再重绘。"""
    page.evaluate("() => { if (!deckOrder().includes('memo')) setDeckOn('memo', true); }")
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# 各段
# ---------------------------------------------------------------------------

def locked_visitor(b, halo, vault):
    ctx = new_context(b, 1280, 900, unlocked=False)
    page = ready(ctx.new_page())
    state = page.evaluate("() => ({ chat: STATE.chat, vault: STATE.vault, ask: askAvailable() })")
    check("未解锁：连有没有 AI 聊天 / 笔记服务都不知道",
          state == {"chat": None, "vault": None, "ask": False}, state)
    hidden = page.evaluate("() => [document.getElementById('memoAsk').hidden, document.getElementById('todoVaultSync').hidden]")
    check("未解锁：便签的「发给 AI」和待办的「同步到笔记」都收起来了", hidden == [True, True], hidden)
    check("未解锁：页面文本里没有笔记服务的地址", vault not in page.content() and halo not in page.content())
    for path in ("/api/vault/capture", "/api/vault/todos/sync"):
        status = ctx.request.post(BASE + path, data=json.dumps({"text": "x"}),
                                  headers={"Content-Type": "application/json"}).status
        check("未解锁：%s 403" % path, status == 403, status)
    status = ctx.request.get(BASE + "/api/vault/search?q=x").status
    check("未解锁：搜笔记 403", status == 403, status)
    check("未解锁：一次出站请求都没发出去", VAULT_CALLS == [], VAULT_CALLS[:2])
    page.close()
    ctx.close()


def composer(b, halo, vault):
    ctx = new_context(b, 1280, 900, unlocked=True)
    page = ready(ctx.new_page())
    check("已解锁：两个去处都在", page.evaluate("() => [chatAvailable(), vaultAvailable(), askAvailable()]") == [True, True, True])

    # --- 首页搜索：用 AI 回答 ---
    type_search(page, "量子计算 是什么")
    rows = search_rows(page)
    check("搜索下拉里有「用 AI 回答」和「在笔记里搜索」两行", "ai" in rows and "notes" in rows, rows)
    check("默认选中的仍是第一条（收藏 / 网页搜索），AI 不抢默认项",
          page.evaluate("() => HOME_SEARCH.rows[HOME_SEARCH.active].type") not in ("ai", "notes"))
    pick_row(page, "ai")
    check("选「用 AI 回答」：弹出编辑框，里面就是刚输入的字", composer_open(page) and composer_text(page) == "量子计算 是什么", composer_text(page))
    check("编辑框里两个去处都在", page.evaluate("() => [document.getElementById('askSend').hidden, document.getElementById('askVault').hidden]") == [False, False])
    check("字数提示跟着内容走", "字" in page.evaluate("() => document.getElementById('askCount').textContent"))
    close_composer(page)
    check("取消之后编辑框关上了", not composer_open(page))

    # --- 待办：让 AI 拆解 ---
    open_todo_deck(page)
    # 有鼠标的设备上操作按钮平时是透明的、不吃点击，悬停整行才浮出来（app-v3.css 的 @media (hover: hover)）。
    page.locator("#todoList .todo-item").first.hover()
    page.wait_for_timeout(200)
    first = page.locator('#todoList [data-todo-act="ask"]').first
    check("每条待办上有「让 AI 拆解」", first.is_visible())
    todo_text = page.evaluate("() => todoItems().filter(t => !t.done)[0].text")
    first.click()
    page.wait_for_timeout(200)
    body = composer_text(page)
    check("「让 AI 拆解」带着这条待办和一句提问", composer_open(page) and todo_text in body and "拆成可执行的几步" in body, body[:120])
    close_composer(page)

    # --- 便签：发给 AI ---
    put_memo_on_home(page)
    open_memo_deck(page)
    page.fill("#memoText", "周末要做的事：\n1. 修水管\n2. 还书")
    page.wait_for_timeout(150)
    check("配了 AI 之后便签的「发给 AI」露出来", not page.evaluate("() => document.getElementById('memoAsk').hidden"))
    page.click("#memoAsk")
    page.wait_for_timeout(200)
    check("「发给 AI」带着便签全文（含换行）", composer_open(page) and composer_text(page) == "周末要做的事：\n1. 修水管\n2. 还书", composer_text(page))
    close_composer(page)

    # --- 收藏卡片的「···」里有「问 AI」 ---
    page.evaluate("() => { openLibPage(libGroups()[0].id); }")
    page.wait_for_timeout(400)
    menu = page.locator(".link-card .menu-trigger").first
    if menu.count():
        menu.click()
        page.wait_for_timeout(200)
        labels = page.evaluate("() => [...document.querySelectorAll('.link-card details.action-menu[open] .menu-popover button')].map(b => b.textContent.trim())")
        check("收藏卡片的「···」里第一项是「问 AI」", labels and labels[0] == "问 AI", labels[:3])
        page.keyboard.press("Escape")
    else:
        check("收藏卡片的「···」里第一项是「问 AI」", False, "没有找到收藏卡片")
    page.close()
    ctx.close()


def send_to_chat(b, halo, vault):
    ctx = new_context(b, 1280, 900, unlocked=True)
    page = ready(ctx.new_page())
    prompt = "帮我读一下 https://a.example/x?y=1&z=2 这一页\n第二行 100% 确定"
    page.evaluate("(t) => openAsk(t, 'search', '用 AI 回答')", prompt)
    page.wait_for_timeout(150)
    check("编辑框里是原文", composer_text(page) == prompt, composer_text(page))
    page.click("#askSend")
    page.wait_for_function("() => { const f = document.querySelector('#chatStage iframe'); return !!(f && f.src); }", timeout=8000)
    check("发送之后切到了「AI 聊天」", page.evaluate("() => (document.querySelector('.view.active') || {}).id") == "view-chat")
    src = page.query_selector("#chatStage iframe").get_attribute("src")
    sent, ticket, redirect = prompt_in(src)
    check("iframe 指向 HaloWebUI 的 /auth，且带着 redirect", src.startswith(halo + "/auth?redirect="), src[:120])
    check("问题一字不差地到了对面（& ? % 和换行都活着）", sent == prompt, repr(sent))
    check("redirect 是站内路径，不是别人家的地址", redirect.startswith("/?q=") and not redirect.startswith("//"), redirect[:60])
    check("票据仍然只在 # 后面", ticket.startswith("v2.chat.") and "hub_ticket" not in src.split("#", 1)[0], src[:120])

    # 框里那页把自己的完整地址写出来了：确认浏览器真的带着这条地址去了对面。
    landed = None
    deadline = time.time() + 8
    while time.time() < deadline and landed is None:
        for frame in page.frames:
            if frame is not page.main_frame and frame.url.startswith(halo):
                try:
                    frame.wait_for_selector("pre", timeout=4000)
                    landed = frame.inner_text("pre")
                except Exception:
                    landed = ""
        page.wait_for_timeout(100)
    check("对面真的收到了这条地址", bool(landed) and "redirect=" in landed, (landed or "")[:120])

    # 超长：后端截断，页面说一声，地址仍然是完整可解的
    page.evaluate("(t) => openAsk(t, 'search', '用 AI 回答')", "很长的中文" * 900)
    page.wait_for_timeout(150)
    page.click("#askSend")
    page.wait_for_timeout(1200)
    src = page.query_selector("#chatStage iframe").get_attribute("src")
    sent, _, _ = prompt_in(src)
    check("超长的问题被截断，地址没被撑爆", 0 < len(src) <= 6000 and sent.endswith("…"), (len(src), sent[-6:]))
    toast = page.evaluate("() => [...document.querySelectorAll('#toasts .toast')].map(t => t.textContent).join('|')")
    check("页面说了一声「已截断」", "截断" in toast, toast[:120])
    page.close()
    ctx.close()


def capture_and_mirror(b, halo, vault):
    ctx = new_context(b, 1280, 900, unlocked=True)
    page = ready(ctx.new_page())
    VAULT_CALLS.clear()

    page.evaluate("() => openAsk('第一行\\n第二行', 'memo', '把便签发给 AI')")
    page.wait_for_timeout(150)
    page.click("#askVault")
    page.wait_for_timeout(1200)
    check("「存入笔记」之后编辑框自己关上了", not composer_open(page))
    patches = [c for c in VAULT_CALLS if c["method"] == "PATCH"]
    check("发出去的是一次 PATCH（追加）", len(patches) == 1, [c["method"] for c in VAULT_CALLS])
    if patches:
        path = unquote(patches[0]["path"])
        today = time.strftime("%Y-%m-%d")
        check("写的是收件箱里今天那篇", path == "/api/v1/notes/%s/%s.md" % (INBOX, today), path)
        check("key 在请求头里，不在地址里", patches[0]["key"] == VAULT_KEY and VAULT_KEY not in patches[0]["path"])
        appended = json.loads(patches[0]["body"])["append"]
        check("正文缩进成一个块，并带上来源", "  第一行" in appended and "  第二行" in appended and "便签" in appended, repr(appended))
    toast = page.evaluate("() => [...document.querySelectorAll('#toasts .toast')].map(t => t.textContent).join('|')")
    check("页面回报写到了哪一篇", INBOX in toast, toast[:120])

    # --- 待办镜像 ---
    open_todo_deck(page)
    VAULT_CALLS.clear()
    check("配了笔记之后「同步到笔记」露出来", not page.evaluate("() => document.getElementById('todoVaultSync').hidden"))
    page.click("#todoVaultSync")
    page.wait_for_timeout(1200)
    puts = [c for c in VAULT_CALLS if c["method"] == "PUT"]
    check("镜像发的是 PUT（整篇覆盖）", len(puts) == 1, [c["method"] for c in VAULT_CALLS])
    if puts:
        check("写的是收件箱里那一篇待办", unquote(puts[0]["path"]) == "/api/v1/notes/%s/待办.md" % INBOX, unquote(puts[0]["path"]))
        content = json.loads(puts[0]["body"])["content"]
        check("内容是一份清单，并写明它不是真相源", "- [ ] " in content and "单向写入" in content, content[:120])
    page.close()
    ctx.close()


def notes_search(b, halo, vault):
    ctx = new_context(b, 1280, 900, unlocked=True)
    page = ready(ctx.new_page())
    VAULT_CALLS.clear()

    type_search(page, "季度计划")
    page.wait_for_timeout(400)
    check("边打边搜的口子是关着的：还没选中那一行，一次请求都没发",
          [c for c in VAULT_CALLS if "/search" in c["path"]] == [], VAULT_CALLS[:2])
    rows = search_rows(page)
    check("下拉里有「在笔记里搜索」这一行", "notes" in rows, rows)

    pick_row(page, "notes")
    page.wait_for_timeout(1200)
    searches = [c for c in VAULT_CALLS if "/search" in c["path"]]
    check("选中之后才发了一次搜索", len(searches) == 1, [c["path"] for c in VAULT_CALLS])
    rows = search_rows(page)
    check("结果换成了笔记行，「在笔记里搜索」那一行退场", "note" in rows and "notes" not in rows, rows)
    hits = page.evaluate("() => HOME_SEARCH.rows.filter(r => r.type === 'note').map(r => r.hit.path)")
    check("穿越路径被后端洗掉了，只剩干净的那一条", hits == ["项目/季度计划.md"], hits)
    url = page.evaluate("() => (HOME_SEARCH.rows.find(r => r.type === 'note') || {}).hit.url")
    check("点开就是 WebObsidian 的深链", url.startswith(vault + "/note/") and "%E9%A1%B9%E7%9B%AE" in url, url)
    snippet = page.evaluate("() => (HOME_SEARCH.rows.find(r => r.type === 'note') || {}).hit.snippet")
    check("摘要被压成一行", snippet == "本季度 要做的事", repr(snippet))
    check("搜索框里的字还在（结果挂在这几个字下面）", page.evaluate("() => document.getElementById('homeSearch').value") == "季度计划")

    before = len(VAULT_CALLS)
    type_search(page, "季度计划表")
    page.wait_for_timeout(400)
    check("再敲一个字：结果作废、换回「在笔记里搜索」，且没有自动重搜",
          "notes" in search_rows(page) and len(VAULT_CALLS) == before, (search_rows(page), len(VAULT_CALLS) - before))
    page.close()
    ctx.close()


def chat_without_notes(b, halo, vault):
    """只配了聊天、没配笔记的实例：笔记那半边的入口一个都不该出现。"""
    port = _free_port()
    saved = os.environ.get("HUB_VAULT_URL")
    try:
        os.environ.pop("HUB_VAULT_URL", None)
        base = start_server(port=port, data_dir=os.path.join(WORK, "data-novault")).rstrip("/")
    finally:
        if saved is not None:
            os.environ["HUB_VAULT_URL"] = saved
    ctx = new_context(b, 1280, 900, unlocked=True, base=base)
    page = ready(ctx.new_page(), base)
    check("没配笔记：STATE 里没有它", page.evaluate("() => STATE.vault") is None)
    check("没配笔记：「同步到笔记」仍然收着", page.evaluate("() => document.getElementById('todoVaultSync').hidden"))
    check("没配笔记：「发给 AI」照常在（聊天还在）", not page.evaluate("() => document.getElementById('memoAsk').hidden"))
    type_search(page, "季度计划")
    rows = search_rows(page)
    check("没配笔记：下拉里没有「在笔记里搜索」，但有「用 AI 回答」", "notes" not in rows and "ai" in rows, rows)
    page.evaluate("() => openAsk('随便问问', 'search', '用 AI 回答')")
    page.wait_for_timeout(150)
    check("没配笔记：编辑框里只剩「发送到 AI 聊天」",
          page.evaluate("() => [document.getElementById('askSend').hidden, document.getElementById('askVault').hidden]") == [False, True])
    status = ctx.request.get(base + "/api/vault/search?q=x").status
    check("没配笔记：三个笔记接口 404", status == 404, status)
    page.close()
    ctx.close()


def main():
    t0 = time.time()
    _Halo.hub_origin = BASE.rstrip("/")
    halo = _serve(_Halo)
    vault = _serve(_Vault)
    # start_server() 把当前进程的环境变量带给子进程：地址与密钥只活在这里，不落盘。
    os.environ["HUB_CHAT_URL"] = halo
    os.environ["HUB_TRUSTED_EMBED_ADMIN_SECRET"] = SECRET
    os.environ["HUB_VAULT_URL"] = vault
    os.environ["HUB_VAULT_API_KEY"] = VAULT_KEY
    os.environ["HUB_VAULT_INBOX"] = INBOX
    os.environ["HUB_VAULT_TIMEOUT"] = "5"
    os.environ.pop("HUB_VAULT_TODO_MIRROR", None)
    os.environ.pop("HUB_PUBLIC_ORIGIN", None)
    os.environ.pop("HUB_PUBLIC_LIBRARY", None)
    os.environ.pop("GYQD_PRIVATE", None)
    start_server()
    with sync_playwright() as p:
        b = launch(p)
        for fn in (locked_visitor, composer, send_to_chat, capture_and_mirror, notes_search, chat_without_notes):
            print("==", fn.__name__, flush=True)
            try:
                fn(b, halo, vault)
            except Exception as exc:
                check(fn.__name__ + " 运行完毕", False, repr(exc))
        b.close()
    failed = [r for r in RESULTS if not r["ok"]]
    print("\n%d checks, %d failed, %.0fs" % (len(RESULTS), len(failed), time.time() - t0))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

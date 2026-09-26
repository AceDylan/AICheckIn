# -*- coding: utf-8 -*-
"""0926 体验评估改进的浏览器验证：上传图标独立缓存、加载 / 断网状态、看板提醒与修复入口、锁定时的首页、
拼音首字母搜索、分组页图标视图、看板列表视图与并发刷新、矮屏首页。"""
import json
import os
import time
from verify import *   # noqa: F401,F403
from common import _free_port, DATA, WORK, PASSWORD, launch  # noqa: F401

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII="


def write_store(mutate):
    path = os.path.join(DATA, "config.json")
    with open(path, encoding="utf-8") as fh:
        store = json.load(fh)
    mutate(store)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False)


def open_at(b, w, h, path, **kw):
    """open_page 只认首页的就绪标志；要落在别的页面时先开首页再跳过去。"""
    ctx, page = open_page(b, w, h, **kw)
    page.goto(BASE + path)
    page.wait_for_timeout(700)
    return ctx, page


def icons(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "dark"})
    status, _ = api(ctx, "PUT", "/api/link_groups/daily/links/l001", {"custom_icon": PNG})
    check("上传图标：保存成功", status == 200, status)
    body = ctx.request.get(BASE + "/api/configs").text()
    link = next(l for g in json.loads(body)["link_groups"] for l in g["links"] if l["id"] == "l001")
    check("首页数据里没有图片本体，只有带版本号的图标地址", "data:image" not in body and link.get("custom_icon_url", "").startswith("/api/link_icon/daily/l001?v="), link)
    resp = ctx.request.get(BASE + link["custom_icon_url"])
    check("图标地址：返回图片、一年内不再询问", resp.status == 200 and resp.headers["content-type"] == "image/png" and "immutable" in resp.headers.get("cache-control", ""), resp.headers.get("cache-control"))
    page.reload(); page.wait_for_timeout(900)
    st = page.evaluate("() => [...document.querySelectorAll('img.avatar-upload')].map(i => [i.getAttribute('src'), i.naturalWidth])")
    check("页面上的上传图标从独立地址加载成功", st and all(s.startswith("/api/link_icon/") and w > 0 for s, w in st), st)
    sent = []
    page.on("request", lambda r: sent.append(r.post_data or "") if r.method == "PUT" and "/links/l001" in r.url else None)
    page.evaluate("() => editLink('daily', 'l001')"); page.wait_for_timeout(300)
    prev = page.evaluate("() => [document.getElementById('lk_icon_preview').hidden, document.getElementById('lk_icon_image').getAttribute('src')]")
    check("编辑网址：弹窗里照样看得到已上传的图标", prev[0] is False and prev[1].startswith("/api/link_icon/"), prev)
    page.fill("#lk_name", "GitHub 改名")
    page.click("#linkSave"); page.wait_for_timeout(700)
    exported = ctx.request.get(BASE + "/api/configs/export").json()
    saved = next(l for g in exported["link_groups"] for l in g["links"] if l["id"] == "l001")
    check("图标没动就不带 custom_icon：改名后原图还在", saved["name"] == "GitHub 改名" and saved["custom_icon"] == PNG and sent and "custom_icon" not in sent[0], (saved["name"], sent[:1]))
    ctx.close()


def boot_states(b):
    reset()
    ctx = new_context(b, 390, 844, mobile=True)
    ctx.add_cookies([{"name": "bh_theme", "value": "dark", "url": BASE}])
    page = ctx.new_page()
    held = []
    page.route("**/api/configs*", lambda r: held.append(r))
    page.goto(BASE + "/#bookmarks", wait_until="domcontentloaded"); page.wait_for_timeout(900)
    st = page.evaluate("() => [!!document.querySelector('#homeList .boot-skeleton'), document.getElementById('homeEmpty').innerText, getComputedStyle(document.getElementById('homeEmpty')).display]")
    shot(page, "0926-boot-skeleton")
    check("数据没到：首页是占位骨架，不是「从第一个网址开始」", st[0] and "从第一个网址开始" not in st[1] and st[2] == "none", st)
    for r in held:
        r.continue_()
    page.unroute("**/api/configs*")
    page.wait_for_selector("#homeList .home-tile", timeout=8000)
    check("数据到了：骨架换成真实图标", page.evaluate("() => !document.querySelector('.boot-skeleton') && document.querySelectorAll('#homeList .home-tile').length > 3"))
    page.close()

    page = ctx.new_page()
    page.route("**/api/configs*", lambda r: r.abort("internetdisconnected"))
    page.goto(BASE + "/#bookmarks", wait_until="domcontentloaded"); page.wait_for_timeout(1500)
    text = page.locator("#homeEmpty").inner_text()
    shot(page, "0926-boot-offline")
    check("断网：说「连不上服务器」并给「立即重试」，不给建库引导", "连不上服务器" in text and "立即重试" in text and "新建第一个分组" not in text, text)
    check("断网：提示条是中文而不是 Failed to fetch", "Failed to fetch" not in page.evaluate("() => document.body.innerText"))
    check("断网：搜索框照常可用", page.locator("#homeSearch").is_visible() and page.locator("#homeSearch").is_enabled())
    page.unroute("**/api/configs*")
    page.get_by_role("button", name="立即重试").click()
    page.wait_for_selector("#homeList .home-tile", timeout=8000)
    check("点「立即重试」：网络恢复后收藏回来", page.evaluate("() => STATE_LOADED && !BOOT_FAILED"))
    ctx.close()


def seed_dashboard(store):
    now = int(time.time())
    a = store["bookmarks"][0]
    a["fields"][0]["value"] = "13.4625268000"
    a["fields"].append({"id": "reset", "label": "刷新时间", "type": "time", "enabled": True, "raw": now + 3 * 3600 + 300,
                        "value": "x", "updated_at": "2026-09-26 10:00:00", "warn_days": 0,
                        "method": "GET", "url": "https://relay-a.example.com/api/reset", "headers": {}, "body": "", "json_path": "data.reset"})
    for f in a["fields"]:
        f.setdefault("method", "GET"); f.setdefault("url", "https://relay-a.example.com/api"); f.setdefault("headers", {}); f.setdefault("body", ""); f.setdefault("json_path", "data.x")
    bf = store["bookmarks"][1]["fields"][0]
    bf.update({"error": "HTTP 401", "method": "GET", "url": "https://relay-b.example.com/api/balance", "headers": {}, "body": "", "json_path": "data.balance"})


def dashboard(b):
    reset()
    write_store(seed_dashboard)
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "dark", "bh_wallpaper": "off"})
    st = page.evaluate("() => [bookmarkAlert(STATE.bookmarks[0]), siteWidgetParts(STATE.bookmarks[0]).sub, document.getElementById('homeNotice').innerText]")
    check("额度重置时间（填 0 = 不提醒）不算预警，首页小组件照常显示倒计时", st[0] == "" and "还有" in st[1], st)
    page.goto(BASE + "/#links/monitor"); page.wait_for_timeout(700)
    card = page.locator('#bmList [data-bm-index="0"]')
    vals = card.locator(".field-val").all_inner_texts()
    check("看板卡片的金额和首页小组件一样（13.46，不再是 13.4625268000）", vals and vals[0].strip().startswith("13.46") and "4625" not in vals[0], vals)
    quiet = page.evaluate("""() => { const it = [...document.querySelectorAll('#bmList [data-bm-index="0"] .field-item')].find(x => x.innerText.includes('刷新时间'));
        return it ? [it.innerText, it.querySelector('.field-val').className, !!it.querySelector('.field-badge.is-soon, .field-badge.is-past')] : null; }""")
    check("重置时间：写「还有 N 小时」、不上色、没有到期徽标", quiet and "还有" in quiet[0] and "到期" not in quiet[0] and "is-soon" not in quiet[1] and not quiet[2], quiet)
    fixer = page.locator('#bmList [data-bm-index="1"] .field-fix')
    check("401：直接说「登录凭据可能过期了」并给「更新 cURL」", "登录凭据可能过期了" in fixer.inner_text() and fixer.get_by_role("button", name="更新 cURL").count() == 1)
    shot(page, "0926-monitor-cards")
    fixer.get_by_role("button", name="更新 cURL").click(); page.wait_for_timeout(700)
    st = page.evaluate("""() => { const open = [...document.querySelectorAll('#bmFieldList details.field-editor-item')].map(d => d.open);
        const a = document.activeElement; return [document.getElementById('bmModal').classList.contains('show'), open, a && a.dataset ? a.dataset.k : null]; }""")
    check("点「更新 cURL」：打开编辑弹窗、只展开出错的字段、光标在 cURL 输入框", st[0] and st[1] == [True] and st[2] == "_curl_text", st)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    page.evaluate("() => document.getElementById('bmModal').classList.remove('show')")

    page.locator('#libView [data-lib-view="list"]').click(); page.wait_for_timeout(400)
    rows = page.locator("#bmList .bm-row")
    check("看板切到「列表」：一行一个站点，选择记进 Cookie", rows.count() == 3 and any(c["name"] == "bh_bm_view" and c["value"] == "list" for c in ctx.cookies()), rows.count())
    check("列表行：金额同格式、401 也有「更新 cURL」", "13.46" in rows.nth(0).inner_text() and rows.nth(1).get_by_role("button", name="更新 cURL").count() == 1)
    shot(page, "0926-monitor-list")

    page.evaluate("""() => { window.__inflight = 0; window.__maxInflight = 0; const f = window.fetch;
        window.fetch = async (...a) => { const hit = String(a[0]).includes('/refresh_balance');
          if (hit) { __inflight++; __maxInflight = Math.max(__maxInflight, __inflight); await new Promise(r => setTimeout(r, 400)); }
          try { return await f(...a); } finally { if (hit) __inflight--; } }; }""")
    before = ctx.request.get(BASE + "/api/configs").json()
    page.click("#refreshAllBm")
    page.wait_for_function("() => document.getElementById('refreshAllBm').textContent.trim() === '刷新所有数据' && !document.getElementById('refreshAllBm').disabled", timeout=20000)
    peak = page.evaluate("() => __maxInflight")
    check("「刷新所有数据」：不同站点同时刷（最多 3 个）", 2 <= peak <= 3, peak)
    after = ctx.request.get(BASE + "/api/configs").json()
    check("并发刷新后配置完整（分组、站点、字段数都没少）",
          len(after["link_groups"]) == len(before["link_groups"]) and [len(x["fields"]) for x in after["bookmarks"]] == [len(x["fields"]) for x in before["bookmarks"]],
          [len(x["fields"]) for x in after["bookmarks"]])
    ctx.close()

    ctx, page = open_at(b, 390, 844, "/#links/monitor", mobile=True, cookies={"bh_theme": "light", "bh_bm_view": "list"})
    over = page.evaluate("() => document.documentElement.scrollWidth")
    check("手机：看板列表不撑宽页面", over <= 390 and page.locator("#bmList .bm-row").count() == 3, over)
    shot(page, "0926-monitor-list-mobile")
    ctx.close()


def quiet_editor(b):
    reset()
    write_store(seed_dashboard)
    ctx, page = open_at(b, 1440, 900, "/#links/monitor")
    page.evaluate("() => openBmModal(0)"); page.wait_for_timeout(600)
    page.evaluate("() => document.querySelectorAll('#bmFieldList details.field-editor-item').forEach(d => d.open = true)")
    val = page.evaluate("() => { const inp = [...document.querySelectorAll('#bmFieldList [data-k=warn_days]')].pop(); return inp ? [inp.value, inp.min, inp.closest('.field').innerText] : null; }")
    check("编辑重置时间字段：提醒天数显示 0，并说明「0 = 不提醒」", val and val[0] == "0" and val[1] == "0" and "0 = 不提醒" in val[2], val)
    page.click("#bmSave"); page.wait_for_timeout(800)
    exported = ctx.request.get(BASE + "/api/configs/export").json()
    f = next(x for x in exported["bookmarks"][0]["fields"] if x["id"] == "reset")
    check("保存后仍是「不提醒」（warn_days = 0 落盘）", f.get("warn_days") == 0, f.get("warn_days"))
    ctx.close()


def locked(b):
    port = _free_port()
    backup = os.environ.pop("HUB_PUBLIC_LIBRARY", None)
    try:
        base = start_server(port=port, data_dir=os.path.join(WORK, "data-private")).rstrip("/")
    finally:
        if backup is not None:
            os.environ["HUB_PUBLIC_LIBRARY"] = backup
    for w, h, mobile in ((1280, 800, False), (390, 844, True)):
        ctx = new_context(b, w, h, mobile=mobile, unlocked=False, base=base)
        page = ctx.new_page()
        page.on("pageerror", lambda e: ERRORS.append("pageerror: " + str(e)))
        page.goto(base + "/"); page.wait_for_selector("#homeEmpty .empty-actions", timeout=8000); page.wait_for_timeout(400)
        label = "手机" if mobile else "电脑"
        st = page.evaluate("() => [document.querySelector('.view.active').id, LIB.page, document.getElementById('homeEmpty').innerText, document.querySelectorAll('#homeList .home-tile').length]")
        check("锁定（%s）：起始页还是首页，中间一张「收藏已锁定」卡片、没有任何收藏" % label, st[0] == "view-bookmarks" and st[1] == "@home" and "收藏已锁定" in st[2] and st[3] == 0, st)
        check("锁定（%s）：分组导航 / 手机顶上的分组标签都收起" % label, page.locator("#libChips").is_hidden() and page.locator("#libSubnav").is_hidden())
        page.fill("#homeSearch", "天气"); page.wait_for_timeout(300)
        rows = page.locator("#homeSearchList").inner_text()
        check("锁定（%s）：搜索框照常能网页搜索" % label, "搜索" in rows and "天气" in rows, rows[:80])
        page.fill("#homeSearch", "")
        check("锁定（%s）：页面不撑宽" % label, page.evaluate("() => document.documentElement.scrollWidth") <= w)
        shot(page, "0926-locked-home-" + ("m" if mobile else "d"))
        if mobile:
            ctx.close()
            continue
        page.goto(base + "/#links/monitor"); page.wait_for_timeout(400)
        check("锁定：手敲看板地址也只到首页的解锁卡片", page.evaluate("() => LIB.page") == "@home")
        page.goto(base + "/#settings"); page.wait_for_timeout(500)
        st = page.evaluate("() => [[...document.querySelectorAll('#settingsGrid > .panel.needs-unlock')].map(p => !!p.offsetParent), !!document.getElementById('adminPanel').offsetParent]")
        check("锁定：设置页收起定时签到 / 自动刷新 / 代理 / 自检 / 恢复（不再显示占位的 08:30 未启用）", st[0] == [False] * 5 and st[1], st)
        page.goto(base + "/"); page.wait_for_selector("#homeEmpty .empty-actions"); page.wait_for_timeout(300)
        page.get_by_role("button", name="输入密码解锁").click(); page.wait_for_timeout(300)
        check("点「输入密码解锁」：到设置页、光标在密码框", page.evaluate("() => [document.querySelector('.view.active').id, document.activeElement && document.activeElement.type]") == ["view-settings", "password"])
        page.keyboard.type(PASSWORD); page.keyboard.press("Enter")
        page.wait_for_selector("#homeList .home-tile", timeout=8000); page.wait_for_timeout(300)
        check("解锁成功：回到首页，收藏回来了", page.evaluate("() => [document.querySelector('.view.active').id, LIB.page, canEdit()]") == ["view-bookmarks", "@home", True])
        ctx.close()


def pinyin(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "dark"})
    page.fill("#homeSearch", "ryf"); page.wait_for_timeout(300)
    first = page.locator("#homeSearchList .home-search-row").first.inner_text()
    check("首页搜索：输「ryf」第一条就是「阮一峰的网络日志」", "阮一峰的网络日志" in first, first[:40])
    page.fill("#homeSearch", "lyq"); page.wait_for_timeout(300)
    check("首页搜索：输「lyq」找到「路由器」（「路」不会算成 m）", "路由器" in page.locator("#homeSearchList").inner_text())
    page.fill("#homeSearch", "")
    page.keyboard.press("Control+k"); page.wait_for_timeout(300); page.keyboard.type("gdd"); page.wait_for_timeout(300)
    check("⌘K：输「gdd」找到「高德地图」", "高德地图" in page.locator(".omni-row").first.inner_text())
    page.keyboard.press("Escape")
    gid = page.evaluate("() => STATE.link_groups.find(g => g.links.some(l => l.name === '路由器')).id")
    page.goto(BASE + "/#links/" + gid); page.wait_for_timeout(500)
    page.fill("#bmSearch", "lyq"); page.wait_for_timeout(300)
    names = page.locator("#linkList .link-name").all_inner_texts()
    check("分组页筛选框也认拼音首字母", [n.strip() for n in names] == ["路由器"], names)
    ctx.close()


def group_tiles(b):
    reset()
    ctx, page = open_at(b, 1440, 900, "/#links/daily", cookies={"bh_theme": "dark", "bh_wallpaper": "off"})
    check("分组页工具条右端有「列表 / 图标」", page.locator("#libView").is_visible() and page.locator('#libView [data-lib-view="rows"]').get_attribute("aria-checked") == "true")
    page.locator('#libView [data-lib-view="tiles"]').click(); page.wait_for_timeout(500)
    n = page.locator("#linkList.link-tiles .home-tile").count()
    check("切到「图标」：整组变成大图标网格，选择记进 Cookie", n == 14 and any(c["name"] == "bh_link_view" and c["value"] == "tiles" for c in ctx.cookies()), n)
    page.locator("#linkList .home-tile").first.hover(); page.wait_for_timeout(200)
    page.locator("#linkList .home-tile").first.locator(".tile-menu > summary").click(); page.wait_for_timeout(200)
    check("图标上的「···」有编辑 / 删除", page.locator("#linkList .home-tile").first.locator(".menu-popover button", has_text="编辑").count() == 1)
    shot(page, "0926-group-tiles")
    page.goto(BASE + "/#links/monitor"); page.wait_for_timeout(400)
    check("看板页的切换是「卡片 / 列表」", page.locator("#libView").inner_text().replace("\n", "") == "卡片列表")
    ctx.close()
    ctx, page = open_at(b, 390, 844, "/#links/daily", mobile=True, cookies={"bh_theme": "dark", "bh_link_view": "tiles"})
    tops = page.evaluate("() => [...document.querySelectorAll('#linkList .home-tile')].slice(0, 5).map(t => Math.round(t.getBoundingClientRect().top))")
    check("手机图标视图：一行 4 个", len(set(tops[:4])) == 1 and tops[4] > tops[0], tops)
    check("手机图标视图：页面不撑宽", page.evaluate("() => document.documentElement.scrollWidth") <= 390)
    shot(page, "0926-group-tiles-mobile")
    ctx.close()
    ctx, page = open_at(b, 390, 844, "/#links/daily", mobile=True, cookies={"bh_theme": "dark"})
    api(ctx, "PUT", "/api/link_groups/daily/links/l001", {"tags": ["工具"]})
    page.reload(); page.wait_for_selector("#linkList .link-tags"); page.wait_for_timeout(300)
    st = page.evaluate("""() => { const c = [...document.querySelectorAll('#linkList .link-card')].find(x => x.querySelector('.link-tags'));
        if (!c) return null; const h = c.querySelector('.link-host').getBoundingClientRect(), t = c.querySelector('.link-tags').getBoundingClientRect();
        return [Math.round(Math.abs((h.top + h.bottom) / 2 - (t.top + t.bottom) / 2)), Math.round(c.getBoundingClientRect().height)]; }""")
    check("手机列表：标签并到域名那一行（行高不再多出一整行标签）", st is not None and st[0] <= 6, st)
    ctx.close()


def short_screen(b):
    reset()
    ctx, page = open_page(b, 1280, 650, cookies={"bh_theme": "dark", "bh_wallpaper": "off"})
    st = page.evaluate("""() => { const c = document.querySelector('.hero-clock').getBoundingClientRect(), m = document.querySelector('.hero-meta').getBoundingClientRect();
        const tile = document.querySelector('#homeList .home-tile');
        return [parseFloat(getComputedStyle(document.querySelector('.hero-clock')).fontSize), Math.abs(c.bottom - m.bottom) < 16, tile ? Math.round(tile.getBoundingClientRect().bottom) : 9999]; }""")
    check("矮屏（1280×650）：时钟和问候并成一行、字号收小", st[0] <= 46 and st[1], st)
    check("矮屏：第一行图标完整落在首屏", st[2] < 650, st)
    shot(page, "0926-short-screen")
    ctx.close()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "dark", "bh_wallpaper": "off"})
    size = page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('.hero-clock')).fontSize)")
    check("普通高度的屏幕：大号时钟不变", size == 80, size)
    ctx.close()


def todo_fade(b):
    reset()
    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_theme": "dark"})
    for i in range(14):
        api(ctx, "POST", "/api/todos", {"text": "待办第 %d 条：把这件事做完" % (i + 1)})
    page.reload(); page.wait_for_timeout(900)
    page.locator(".deck-tab", has_text="待办").first.tap(); page.wait_for_timeout(500)
    st = page.evaluate("() => { const l = document.getElementById('todoList'); return [l.scrollHeight > l.clientHeight, l.className]; }")
    check("待办多于一屏：列表底部淡出，看得出下面还有", st[0] and "fade-bottom" in st[1] and "fade-top" not in st[1], st)
    page.evaluate("() => { const l = document.getElementById('todoList'); l.scrollTop = l.scrollHeight; l.dispatchEvent(new Event('scroll')); }"); page.wait_for_timeout(200)
    cls = page.evaluate("() => document.getElementById('todoList').className")
    check("滚到底：底部淡出去掉、顶部淡出出现", "fade-top" in cls and "fade-bottom" not in cls, cls)
    shot(page, "0926b-todo-fade")
    ctx.close()


def shell_update(b):
    reset()
    ctx, page = open_page(b, 1280, 800, cookies={"bh_theme": "dark"})
    page.evaluate("() => navigator.serviceWorker.ready.then(() => true)")
    page.reload(); page.wait_for_timeout(800)
    controlled = page.evaluate("() => !!navigator.serviceWorker.controller")
    check("外壳由 Service Worker 接管（第二次打开）", controlled)
    toasts = lambda: page.evaluate("() => [...document.querySelectorAll('.toast')].map(t => t.innerText).join(' | ')")
    check("外壳没变：不提示新版本", "有新版本" not in toasts(), toasts())
    ok = page.evaluate("""async () => { const keys = await caches.keys(); const c = await caches.open(keys.find(k => k.startsWith('bh-shell')));
        const r = await c.match('/'); if (!r) return false; const h = new Headers(r.headers); h.set('ETag', '"shell-before-deploy"');
        await c.put('/', new Response(await r.blob(), { status: 200, headers: h })); return true; }""")
    check("模拟部署：把缓存里的外壳标成旧版本", ok)
    page.reload(); page.wait_for_timeout(1500)
    check("部署后第一次打开：提示「有新版本」并给「刷新」", "有新版本" in toasts() and page.locator(".toast .toast-action", has_text="刷新").count() == 1, toasts())
    shot(page, "0926b-shell-update")
    page.locator(".toast .toast-action", has_text="刷新").click(); page.wait_for_timeout(1500)
    check("点「刷新」之后：已是新版本，不再提示", "有新版本" not in toasts(), toasts())
    ctx.close()


def quiet_shortcut(b):
    reset()
    def mutate(store):
        seed_dashboard(store)
        for f in store["bookmarks"][0]["fields"]:
            if f["id"] == "reset":
                f.pop("warn_days", None)
    write_store(mutate)
    ctx, page = open_at(b, 1440, 900, "/#links/monitor", cookies={"bh_theme": "dark", "bh_wallpaper": "off"})
    meta = page.locator("#bmVisibleCount").inner_text()
    check("看板工具条写明自动刷新的节奏（或没开）", "自动刷新" in meta, meta)
    notice_before = page.evaluate("() => bookmarkAlert(STATE.bookmarks[0])")
    btn = page.locator('#bmList [data-bm-index="0"]').get_by_role("button", name="不再提醒…")
    check("重置时间还按到期提醒时：卡片上有「不再提醒…」", notice_before == "soon" and btn.count() == 1, notice_before)
    btn.click(); page.wait_for_timeout(700)
    st = page.evaluate("() => { const a = document.activeElement; return [document.getElementById('bmModal').classList.contains('show'), a && a.dataset ? a.dataset.k : null, a ? a.value : null]; }")
    check("点了：编辑弹窗定位到这个字段，提醒天数已填 0、光标在那一格", st == [True, "warn_days", "0"], st)
    page.click("#bmSave"); page.wait_for_timeout(900)
    exported = ctx.request.get(BASE + "/api/configs/export").json()
    f = next(x for x in exported["bookmarks"][0]["fields"] if x["id"] == "reset")
    after = page.evaluate("() => bookmarkAlert(STATE.bookmarks[0])")
    check("保存后：落盘为 0，这个站点不再算快到期", f.get("warn_days") == 0 and after == "", (f.get("warn_days"), after))
    ctx.close()
    ctx, page = open_at(b, 390, 844, "/#links/monitor", mobile=True, cookies={"bh_theme": "dark"})
    check("手机：工具条多了刷新节奏也不撑宽", page.evaluate("() => document.documentElement.scrollWidth") <= 390)
    shot(page, "0926b-monitor-mobile")
    ctx.close()


if __name__ == "__main__":
    main((icons, boot_states, dashboard, quiet_editor, locked, pinyin, group_tiles, short_screen, todo_fade, shell_update, quiet_shortcut))

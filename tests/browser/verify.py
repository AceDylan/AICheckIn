# -*- coding: utf-8 -*-
"""首页的真实浏览器回归：待办排序（鼠标 / 触摸 / 菜单 / 键盘）、首页版面、编辑首页、自定义首页弹窗、导航、权限 / XSS、降级、性能。

    python tests/browser/verify.py            # 全部
    python tests/browser/verify.py todo_desktop layout   # 只跑点名的几段

服务由 common.start_server() 现起（合成数据、临时目录、随机端口）；任何离开它的请求都会被掐掉并记为失败。
同目录的 verify_polish / verify_railtip / verify_transport / verify_deck 复用这里的工具函数。
"""
import json
import sys
import time

from common import *   # noqa: F401,F403

RESULTS, EXTERNAL, ERRORS = [], [], []


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + ((" — " + str(detail)[:200]) if detail and not ok else ""), flush=True)


def reset():
    seed_data()


def open_page(b, w, h, mobile=False, unlocked=True, cookies=None, path="/", **kw):
    ctx = new_context(b, w, h, mobile=mobile, unlocked=unlocked, **kw)
    if cookies:
        ctx.add_cookies([{"name": k, "value": v, "url": BASE} for k, v in cookies.items()])
    page = ctx.new_page()
    page.on("pageerror", lambda e: ERRORS.append("pageerror: " + str(e)))
    page.on("console", lambda m: ERRORS.append("console: " + m.text) if m.type == "error" and "ERR_FAILED" not in m.text else None)
    page.on("request", lambda r: EXTERNAL.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)
    page.goto(BASE + path)
    page.wait_for_selector("#homeList .home-section, #homeEmpty .empty-title, .empty-state", timeout=8000)
    page.wait_for_timeout(600)
    return ctx, page


def settle(page, selector, block="nearest"):
    """把元素滚进视野并等它停稳。页面开着 scroll-behavior: smooth：滚动要几百毫秒才到位，
    没停稳就量坐标，鼠标会落在别的行上（看上去像「拖错了行」）。这里强制瞬时滚动，再确认连续两帧位置不变。"""
    page.evaluate("([s, b]) => document.querySelector(s).scrollIntoView({ block: b, inline: 'nearest', behavior: 'instant' })", [selector, block])
    page.wait_for_function("""(s) => new Promise(done => { const el = document.querySelector(s), a = el.getBoundingClientRect();
      requestAnimationFrame(() => requestAnimationFrame(() => { const b = el.getBoundingClientRect(); done(a.top === b.top && a.left === b.left); })); })""", arg=selector)


def todo_at(page, x, y):
    """这个坐标上是哪一条待办（data-todo 的 id）；不在任何一行上、或在视口外，回 None。"""
    return page.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y), row = e && e.closest('#todoList [data-todo]'); return row ? row.dataset.todo : null; }", [x, y])


def todo_ids(ctx):
    return [t["id"] for t in api(ctx, "GET", "/api/todos")[1]["todos"] if not t["done"]]


def open_todo_card(page, mobile=False):
    """让待办卡片可操作：右侧一列里它排在日历下面、多半在首屏以下，要先滚进来；标签条布局（手机 / 窄屏）下要先点开「待办」这一枚。"""
    tab = page.locator('#deckTabs [data-deck-tab="todo"]')
    if tab.is_visible() and tab.get_attribute("aria-expanded") != "true":
        (tab.tap() if mobile else tab.click()); page.wait_for_timeout(300)
    settle(page, "#homeTodo", "center" if mobile else "nearest")


def pending(ctx):
    return [t["text"] for t in api(ctx, "GET", "/api/todos")[1]["todos"] if not t["done"]]


def group_links(ctx, gid):
    g = [x for x in api(ctx, "GET", "/api/configs")[1]["link_groups"] if x["id"] == gid][0]
    return [(l["name"], bool(l.get("show_on_home"))) for l in g["links"]]


def touch_fn(ctx, page):
    cdp = ctx.new_cdp_session(page)

    def touch(kind, x=0, y=0):
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": x, "y": y}]})
    return touch


def shot(page, name, full=False):
    page.screenshot(path=f"{SHOTS}/{name}.png", full_page=full)


def mouse_drag(page, x0, y0, x1, y1, hold=False):
    page.mouse.move(x0, y0); page.mouse.down()
    page.mouse.move(x0 + 3, y0 + 4, steps=2)
    page.mouse.move(x1, y1, steps=10); page.wait_for_timeout(120)
    if not hold:
        page.mouse.up(); page.wait_for_timeout(450)


# =====================================================================
def todo_desktop(b):
    reset()
    ctx, page = open_page(b, 1440, 900)
    seed, seed_ids = pending(ctx), todo_ids(ctx)
    rows = page.locator("#todoList .todo-item")
    box0 = page.locator("#homeTodo").bounding_box()
    check("默认版面 1440×900：待办卡片排在日历下面、有一部分在首屏以外（所以下面每一步都先滚进视野）", box0["y"] + box0["height"] > 900, box0)
    open_todo_card(page)
    box1 = page.locator("#homeTodo").bounding_box()
    check("滚进视野后：整张待办卡片都在视口里（±1px）", box1["y"] >= -1 and box1["y"] + box1["height"] <= 901, box1)
    check("待办：每条未完成都有把手，已完成的没有", page.locator("#todoList [data-todo-grip]").count() == len(seed) and page.locator("#todoDoneList [data-todo-grip]").count() == 0)
    g = page.locator("#todoList [data-todo-grip]").first
    check("把手：button + 可访问名称 + haspopup + describedby", g.evaluate("e => e.tagName === 'BUTTON' && e.getAttribute('aria-label').startsWith('排序：') && e.getAttribute('aria-haspopup') === 'menu' && e.getAttribute('aria-describedby') === 'todoSortHint' && !!document.getElementById('todoSortHint').textContent"))
    # 鼠标：按住文字拖整行，第 1 条 → 第 3 条之后
    r0, r2 = rows.nth(0).bounding_box(), rows.nth(2).bounding_box()
    grab = (r0["x"] + 130, r0["y"] + r0["height"] / 2)
    hit = [todo_at(page, *grab), todo_at(page, r0["x"] + 130, r2["y"] + r2["height"] * 0.8)]
    check("按下点落在第 1 条上、松手点落在第 3 条上（按 id 核对，不靠猜）", hit == [seed_ids[0], seed_ids[2]], (hit, seed_ids[:3]))
    mouse_drag(page, grab[0], grab[1], r0["x"] + 130, r2["y"] + r2["height"] * 0.8, hold=True)
    dragging = page.evaluate("() => [...document.querySelectorAll('#todoList .todo-item.is-dragging')].map(r => r.dataset.todo)")
    check("拖动中：正在拖的就是按下的那一条，且只有它", dragging == [seed_ids[0]], dragging)
    check("拖动中：行有 is-dragging、卡片 is-sorting", page.locator("#todoList .todo-item.is-dragging").count() == 1 and page.locator("#homeTodo.is-sorting").count() == 1)
    shot(page, "V1-todo-dragging-1440")
    page.mouse.up(); page.wait_for_timeout(500)
    want = seed[1:3] + [seed[0]] + seed[3:]
    check("鼠标拖整行：顺序已保存到服务端", pending(ctx) == want, pending(ctx)[:4])
    check("鼠标拖整行：按 id 核对——只有第 1 条挪到了第 3 位，其余相对顺序不变", todo_ids(ctx) == seed_ids[1:3] + [seed_ids[0]] + seed_ids[3:], todo_ids(ctx)[:4])
    shown_ids = page.evaluate("() => [...document.querySelectorAll('#todoList [data-todo]')].map(r => r.dataset.todo)")
    check("鼠标拖整行：页面上的顺序 = 服务端的顺序", shown_ids == todo_ids(ctx), shown_ids[:4])
    check("拖完不会顺手把这一条勾掉", page.locator("#todoList .todo-check:checked").count() == 0 and len(pending(ctx)) == len(seed))
    check("拖完焦点落在这一行的把手上", page.evaluate("() => document.activeElement.matches('[data-todo-grip]') && document.activeElement.closest('[data-todo]').querySelector('.todo-text').textContent") == seed[0])
    # Esc 取消
    open_todo_card(page)
    r0, r3 = rows.nth(0).bounding_box(), rows.nth(3).bounding_box()
    mouse_drag(page, r0["x"] + 130, r0["y"] + 14, r0["x"] + 130, r3["y"] + r3["height"] * 0.8, hold=True)
    check("Esc 之前确实已经拖起来了（否则下一条是空过）", page.locator("#todoList .todo-item.is-dragging").count() == 1)
    page.keyboard.press("Escape"); page.wait_for_timeout(200); page.mouse.up(); page.wait_for_timeout(400)
    check("拖到一半按 Esc：顺序不变", pending(ctx) == want)
    # 原地点击仍然是勾选
    rows.nth(5).locator(".todo-text").click(); page.wait_for_timeout(500)
    check("不拖、只点文字：照常勾选", len(pending(ctx)) == len(seed) - 1)
    # 把手菜单
    cur = pending(ctx)
    grip = page.locator("#todoList [data-todo-grip]").nth(4)
    grip.click(); page.wait_for_timeout(200)
    menu = page.locator("#todoSortMenu")
    check("点把手：弹出 role=menu 的排序菜单，焦点进菜单，把手 aria-expanded=true", menu.is_visible() and menu.get_attribute("role") == "menu" and page.evaluate("() => document.activeElement.getAttribute('role')") == "menuitem" and grip.get_attribute("aria-expanded") == "true")
    shot(page, "V2-todo-menu-1440")
    page.keyboard.press("ArrowDown"); page.keyboard.press("ArrowUp")
    page.keyboard.press("Escape"); page.wait_for_timeout(150)
    check("菜单 Esc：收起并把焦点还给把手", (not menu.is_visible()) and page.evaluate("() => document.activeElement.matches('[data-todo-grip]')"))
    grip.click(); page.locator('#todoSortMenu [data-todo-sort="top"]').click(); page.wait_for_timeout(450)
    check("菜单「置顶」", pending(ctx) == [cur[4]] + cur[:4] + cur[5:], pending(ctx)[:3])
    page.locator("#todoList [data-todo-grip]").first.click(); page.wait_for_timeout(150)
    dis = page.evaluate("() => [...document.querySelectorAll('#todoSortMenu [data-todo-sort]')].filter(b => b.disabled).map(b => b.dataset.todoSort)")
    check("第一条的菜单里「置顶 / 上移」禁用", dis == ["top", "up"], dis)
    page.locator('#todoSortMenu [data-todo-sort="bottom"]').click(); page.wait_for_timeout(450)
    check("菜单「移到最后」", pending(ctx)[-1] == cur[4])
    page.locator("#todoList [data-todo-grip]").nth(2).click(); page.locator('#todoSortMenu [data-todo-sort="up"]').click(); page.wait_for_timeout(400)
    page.locator("#todoList [data-todo-grip]").nth(1).click(); page.locator('#todoSortMenu [data-todo-sort="down"]').click(); page.wait_for_timeout(450)
    cur2 = pending(ctx)
    check("菜单「上移 / 下移」互为逆操作", cur2[:3] == cur[:3], cur2[:3])
    page.locator("#todoList [data-todo-grip]").nth(1).click(); page.wait_for_timeout(100)
    page.mouse.click(700, 500); page.wait_for_timeout(150)
    check("点菜单外面：收起", not menu.is_visible())
    open_todo_card(page)
    # 键盘：Tab 到把手（真实按键，才有 :focus-visible）
    page.locator("#todoInput").focus(); page.keyboard.press("Tab"); page.keyboard.press("Tab")
    on_grip = page.evaluate("() => document.activeElement.matches('[data-todo-grip]:focus-visible')")
    check("键盘 Tab 能走到把手，且有可见焦点环", on_grip and page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle") != "none")
    before = pending(ctx)
    for _ in range(3):
        page.keyboard.press("ArrowDown")
    page.wait_for_timeout(700)
    check("把手上按 ↓×3：挪了三位且焦点不丢", pending(ctx) == before[1:4] + [before[0]] + before[4:] and page.evaluate("() => document.activeElement.matches('[data-todo-grip]')"), pending(ctx)[:5])
    check("排序结果进了 aria-live 区域", "移到第 4 位" in (page.locator("#todoLive").text_content() or ""), page.locator("#todoLive").text_content())
    page.keyboard.press("Home"); page.wait_for_timeout(450)
    check("把手上按 Home：回到最前", pending(ctx) == before)
    page.keyboard.press("End"); page.wait_for_timeout(450)
    check("把手上按 End：到最后", pending(ctx) == before[1:] + [before[0]])
    page.locator("#todoList .todo-check").first.focus()
    page.keyboard.press("Alt+ArrowDown"); page.wait_for_timeout(450)
    check("复选框上 Alt+↓ 仍然可用，焦点留在复选框", pending(ctx)[:2] == [before[2], before[1]] and page.evaluate("() => document.activeElement.matches('[data-todo-check]')"))
    # 连点两条
    n = len(pending(ctx))
    page.locator("#todoList .todo-check").nth(0).click(); page.locator("#todoList .todo-check").nth(0).click(); page.wait_for_timeout(700)
    check("回归：连着勾两条，两条都保存（以前第二条会被丢掉）", len(pending(ctx)) == n - 2, len(pending(ctx)))
    # 刷新后顺序还在
    final = pending(ctx)
    page.reload(); page.wait_for_selector("#todoList .todo-item"); page.wait_for_timeout(500)
    shown = page.evaluate("() => [...document.querySelectorAll('#todoList .todo-text')].map(e => e.textContent)")
    check("刷新页面：顺序持久化", shown == final, shown[:3])
    # XSS
    st, data = api(ctx, "POST", "/api/todos", {"text": '<img src=x onerror="window.__xss=1"> "引号\' & <script>window.__xss=2</script>'})
    page.reload(); page.wait_for_selector("#todoList .todo-item"); page.wait_for_timeout(400)
    open_todo_card(page)
    page.locator("#todoList [data-todo-grip]").first.click(); page.wait_for_timeout(150); page.keyboard.press("Escape")
    check("XSS：恶意待办文字只当文字显示（文本 / aria-label / title 都转义）", page.evaluate("() => !window.__xss && !document.querySelector('#todoList img, #todoList script') && document.querySelector('#todoList .todo-text').textContent.includes('<img src=x')"))
    # 自动滚动：把第一条拖到列表底沿并停住
    open_todo_card(page)
    lst = page.locator("#todoList"); lst.evaluate("e => { e.scrollTop = 0; }"); box = lst.bounding_box()
    r0 = rows.nth(0).bounding_box()
    check("自动滚动前：列表底沿在视口里（鼠标够得着）", box["y"] + box["height"] <= 900, box)
    mouse_drag(page, r0["x"] + 130, r0["y"] + 14, r0["x"] + 130, box["y"] + box["height"] - 6, hold=True)
    page.wait_for_timeout(900)
    sc = lst.evaluate("e => [e.scrollTop, e.scrollHeight - e.clientHeight]")
    page.mouse.up(); page.wait_for_timeout(450)
    check("拖到列表底沿：列表自动滚动", sc[1] > 0 and sc[0] > 20, sc)
    ctx.close()


def todo_mobile(b):
    reset()
    ctx, page = open_page(b, 390, 844, mobile=True)
    touch = touch_fn(ctx, page)
    tab = page.locator('#deckTabs [data-deck-tab="todo"]')
    check("手机：组件收在标签条里，「待办」这一枚带未完成数，卡片默认不展开", tab.is_visible() and tab.get_attribute("aria-expanded") == "false" and "项" in tab.inner_text() and not page.locator("#todoList").is_visible(), tab.inner_text())
    open_todo_card(page, mobile=True)
    check("手机：点「待办」标签 → 卡片展开，标签 aria-expanded=true", page.locator("#todoList").is_visible() and tab.get_attribute("aria-expanded") == "true")
    seed, seed_ids = pending(ctx), todo_ids(ctx)
    grip = page.locator("#todoList [data-todo-grip]").first
    gb = grip.bounding_box()
    check("手机：把手触摸目标 ≥ 28×32，且 touch-action:none", gb["width"] >= 28 and gb["height"] >= 32 and grip.evaluate("e => getComputedStyle(e).touchAction") == "none", gb)
    r2 = page.locator("#todoList .todo-item").nth(2).bounding_box()
    x, y = gb["x"] + gb["width"] / 2, gb["y"] + gb["height"] / 2
    ty = r2["y"] + r2["height"] * 0.85
    nav_top = page.evaluate("() => document.querySelector('.sidebar').getBoundingClientRect().top")
    check("手机：起点在第 1 条的把手上、落点在第 3 条上，且都没被底部标签栏盖住", [todo_at(page, x, y), todo_at(page, x, ty)] == [seed_ids[0], seed_ids[2]] and ty < nav_top, ([todo_at(page, x, y), todo_at(page, x, ty)], ty, nav_top))
    touch("touchStart", x, y)
    for i in range(1, 11):
        touch("touchMove", x, y + (ty - y) * i / 10); page.wait_for_timeout(16)
    page.wait_for_timeout(150)
    dragging = page.evaluate("() => [...document.querySelectorAll('#todoList .todo-item.is-dragging')].map(r => r.dataset.todo)")
    check("手机：拖动中正在拖的就是第 1 条", dragging == [seed_ids[0]], dragging)
    shot(page, "V3-todo-touch-dragging-390")
    touch("touchEnd"); page.wait_for_timeout(600)
    check("手机：从把手触摸拖拽，第 1 条 → 第 3 条之后", pending(ctx) == seed[1:3] + [seed[0]] + seed[3:], pending(ctx)[:4])
    check("手机：按 id 核对——只有第 1 条挪到了第 3 位", todo_ids(ctx) == seed_ids[1:3] + [seed_ids[0]] + seed_ids[3:], todo_ids(ctx)[:4])
    open_todo_card(page, mobile=True)
    # 在文字上滑动 = 滚动列表，不是拖拽
    cur = pending(ctx)
    lst = page.locator("#todoList"); r1 = page.locator("#todoList .todo-item").nth(3).bounding_box()
    top0 = lst.evaluate("e => e.scrollTop")
    touch("touchStart", r1["x"] + 150, r1["y"] + 10)
    for i in range(1, 9):
        touch("touchMove", r1["x"] + 150, r1["y"] + 10 - 14 * i); page.wait_for_timeout(16)
    touch("touchEnd"); page.wait_for_timeout(500)
    check("手机：在行上滑动是滚动列表，不触发排序", pending(ctx) == cur and lst.evaluate("e => e.scrollTop") > top0, lst.evaluate("e => e.scrollTop"))
    lst.evaluate("e => { e.scrollTop = 0; }")
    page.locator("#todoList [data-todo-grip]").nth(1).tap(); page.wait_for_timeout(250)
    mb = page.locator('#todoSortMenu [data-todo-sort="top"]').bounding_box()
    nav_top = page.evaluate("() => document.querySelector('.sidebar').getBoundingClientRect().top")
    menu_box = page.locator("#todoSortMenu").bounding_box()
    check("手机：轻点把手出菜单，菜单项 ≥ 44px 高，且不被底部标签栏盖住", page.locator("#todoSortMenu").is_visible() and mb["height"] >= 44 and menu_box["y"] + menu_box["height"] <= nav_top + 1, (mb, nav_top))
    shot(page, "V4-todo-menu-390")
    page.locator('#todoSortMenu [data-todo-sort="top"]').tap(); page.wait_for_timeout(500)
    check("手机：菜单「置顶」", pending(ctx)[0] == cur[1])
    page.reload(); page.wait_for_selector("#todoList .todo-item"); page.wait_for_timeout(500)
    shown = page.evaluate("() => [...document.querySelectorAll('#todoList .todo-text')].map(e => e.textContent)")
    check("手机：刷新后顺序还在，「待办」仍是展开的那一枚（Cookie bh_home_deck_open）", shown == pending(ctx) and "bh_home_deck_open=todo" in page.evaluate("() => document.cookie"))
    page.locator("#todoCollapse").tap(); page.wait_for_timeout(300)
    check("手机：卡片上的收起按钮 = 收回标签条，焦点回到「待办」标签", (not page.locator("#todoList").is_visible()) and page.evaluate("() => document.activeElement.dataset.deckTab") == "todo")
    ctx.close()


def todo_access(b):
    reset()
    ctx, page = open_page(b, 1280, 800, unlocked=False)
    tid = json.load(open(DATA + "/todos.json", encoding="utf-8"))["todos"][0]["id"]
    st, data = api(ctx, "POST", "/api/todos/%s/move" % tid, {"before": "t002"})
    check("权限：未解锁调用 move 接口 → 403，且不回显待办内容", st == 403 and "todos" not in data, (st, data))
    check("权限：未解锁的页面里没有任何待办文字和把手", page.locator("#todoList .todo-item, [data-todo-grip]").count() == 0 and "给域名续费" not in page.content())
    check("权限：未解锁的访客看不到「添加 / 编辑」", (not page.locator("#homeAddLink").is_visible()) and (not page.locator("#homeEditBtn").is_visible()) and page.locator("#homeLookBtn").is_visible())
    st2, _ = api(ctx, "POST", "/api/link_groups/daily/links/reorder", {"order": []})
    check("权限：未解锁调用分组排序接口 → 403", st2 == 403, st2)
    ctx.close()


# =====================================================================
def layout(b):
    reset()
    for w, h, mobile, tag in [(1920, 1080, False, "1920"), (1440, 900, False, "1440"), (1280, 720, False, "1280"), (1024, 768, False, "1024"), (820, 1180, False, "820"), (390, 844, True, "390"), (360, 740, True, "360")]:
        ctx, page = open_page(b, w, h, mobile=mobile)
        info = page.evaluate("""() => {
          const secs = [...document.querySelectorAll('#homeList .home-section')];
          const rows = {};
          secs.forEach(s => { const r = s.getBoundingClientRect(); (rows[Math.round(r.top)] = rows[Math.round(r.top)] || []).push(Math.round(r.left)); });
          const firsts = Object.values(rows).map(a => Math.min(...a));
          const align = secs.map(s => { const ic = s.querySelector('.section-label .ic'), av = s.querySelector('.tile-avatar:not(.widget-avatar)'); return ic && av ? Math.round(ic.getBoundingClientRect().left - av.getBoundingClientRect().left) : null; }).filter(v => v !== null);
          const tools = document.getElementById('homeTools').getBoundingClientRect(), clock = document.getElementById('heroClock').getBoundingClientRect();
          const toggle = document.getElementById('homeToolsToggle');
          return { firsts: [...new Set(firsts)], align, over: document.documentElement.scrollWidth - innerWidth, cols: getComputedStyle(document.getElementById('homeBody')).getPropertyValue('--cols'),
                   toolsVisible: tools.width > 0, toolsRight: Math.round(innerWidth - tools.right), toolsHitClock: tools.width > 0 && !(tools.left > clock.right || tools.bottom < clock.top),
                   toggleVisible: getComputedStyle(toggle).display !== 'none', foot: document.getElementById('homeCount').textContent,
                   firstIconTop: Math.round(document.querySelector('#homeList .tile-avatar').getBoundingClientRect().top) };
        }""")
        check(f"版面 {tag}：每一行分组从同一条左边线起排", len(info["firsts"]) == 1, info["firsts"])
        if not mobile:
            check(f"版面 {tag}：分组标题与第一个图标左对齐（±2px）", all(abs(v) <= 2 for v in info["align"]) and len(info["align"]) > 3, info["align"])
        check(f"版面 {tag}：页面不横向溢出", info["over"] <= 0, info["over"])
        docked = w >= 1024
        check(f"版面 {tag}：工具条" + ("常驻右上角且不压时钟" if docked else "收在「⋯」后面"), (info["toolsVisible"] and not info["toolsHitClock"] and not info["toggleVisible"]) if docked else ((not info["toolsVisible"]) and info["toggleVisible"]), info)
        check(f"版面 {tag}：计数在页脚", "41 个网址" in info["foot"], info["foot"])
        if tag in ("1440", "390"):
            check(f"版面 {tag}：第一排图标在首屏内", info["firstIconTop"] < h - 120, info["firstIconTop"])
        shot(page, f"V5-home-{tag}")
        if tag in ("1440", "390"):
            shot(page, f"V5-home-{tag}-full", full=True)
        if not docked:
            (page.locator("#homeToolsToggle").tap() if mobile else page.locator("#homeToolsToggle").click()); page.wait_for_timeout(250)
            ok = page.locator("#homeTools").is_visible() and page.locator("#homeToolsToggle").get_attribute("aria-expanded") == "true" and page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
            check(f"版面 {tag}：点「⋯」展开工具条，页面仍不溢出", ok)
            if tag == "390":
                shot(page, "V6-home-390-tools-open")
        ctx.close()
    # 右侧那一列组件全部移除 → 图标区变宽、列数变多；窗口缩放 → 列数跟着变
    ctx, page = open_page(b, 1440, 900)
    c1 = page.evaluate("() => HOME_GRID.cols")
    page.locator("#homeDeckBtn").click(); page.wait_for_timeout(300)
    check("「组件」弹窗：默认开着日历和待办两项", page.locator("#deckPicker [data-deck-toggle]:checked").count() == 2 and page.locator("#deckPicker [data-deck-pick]").count() == 6)
    page.locator('[data-deck-pick="calendar"] .switch').click(); page.wait_for_timeout(200)
    c_mid = page.evaluate("() => HOME_GRID.cols")
    page.locator('[data-deck-pick="todo"] .switch').click(); page.wait_for_timeout(300)
    page.locator("#deckModalClose").click(); page.wait_for_timeout(200)
    c2 = page.evaluate("() => HOME_GRID.cols")
    gone = page.evaluate("() => [document.getElementById('homeDeck').hidden, document.getElementById('homeBody').classList.contains('has-deck'), document.cookie.includes('bh_home_deck=none')]")
    check("组件全部移除：右侧一列消失、偏好记成 none（不是回落到默认）", gone == [True, False, True], gone)
    page.set_viewport_size({"width": 1100, "height": 800}); page.wait_for_timeout(400)
    c3 = page.evaluate("() => [HOME_GRID.cols, [...document.querySelectorAll('.home-section')].map(s => getComputedStyle(s).getPropertyValue('--span')).join(',')]")
    check("列数随版面变化：只要还剩一张组件就不变，全部移除后变多；窗口缩到 1100 再重算且各分组跨度同步", c_mid == c1 and c2 > c1 and c3[0] < c2 and all(int(v) <= c3[0] for v in c3[1].split(",")), (c1, c_mid, c2, c3))
    page.set_viewport_size({"width": 1440, "height": 900}); page.wait_for_timeout(300)
    page.locator("#homeDeckBtn").click(); page.locator("#deckReset").click(); page.wait_for_timeout(300)
    page.locator("#deckModalClose").click(); page.wait_for_timeout(200)
    check("「恢复默认」：日历 + 待办回到右侧一列，列数回到原样", page.evaluate("() => [...document.querySelectorAll('#deckCards > .deck-card')].filter(e => !e.hidden).map(e => e.dataset.deck).join('.')") == "calendar.todo" and page.evaluate("() => HOME_GRID.cols") == c1)
    ctx.close()
    # 升级上来的老用户：旧 Cookie 说「不要待办」，新版面就只给日历，不把待办塞回来
    ctx, page = open_page(b, 1440, 900, cookies={"bh_home_todo": "off"})
    check("旧偏好 bh_home_todo=off：默认组件里没有待办", page.evaluate("() => [...document.querySelectorAll('#deckCards > .deck-card')].filter(e => !e.hidden).map(e => e.dataset.deck).join('.')") == "calendar")
    ctx.close()
    # 其他密度 / 主题
    for tag, cookies in [("minimal", {"bh_home_view": "minimal"}), ("cards", {"bh_home_view": "cards"}), ("light-nowall", {"bh_theme": "light", "bh_wallpaper": "off"})]:
        ctx, page = open_page(b, 1440, 900, cookies=cookies)
        check(f"回归：{tag} 正常渲染、不溢出", page.locator("#homeList .home-section").count() >= 7 and page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0)
        shot(page, f"V7-home-{tag}-1440")
        ctx.close()
    # 侧栏：矮窗口下「签到中心 / 系统设置」仍在视野里，分组段自己滚动
    for nav in ("rail", "full"):
        ctx, page = open_page(b, 1280, 720, cookies={"bh_home_nav": nav})
        info = page.evaluate("""() => { const n = document.getElementById('libSubnav'); const foot = document.querySelector('.sidebar-foot').getBoundingClientRect().top;
          return { scrolls: n.scrollHeight > n.clientHeight + 4, fade: n.classList.contains('fade-bottom'), tabs: [...document.querySelectorAll('.tab')].map(t => t.getBoundingClientRect().bottom <= foot + 1) }; }""")
        check(f"导航（{nav}，720px 高）：分组段滚动 + 底端淡出，主入口全部可见", info["scrolls"] and info["fade"] and all(info["tabs"]), info)
        page.locator('#libSubnav [data-lib="wetab"]').evaluate("e => e.click()"); page.wait_for_timeout(500)
        vis = page.evaluate("() => { const n = document.getElementById('libSubnav'), a = n.querySelector('.active'), r = a.getBoundingClientRect(), nr = n.getBoundingClientRect(); return r.top >= nr.top - 1 && r.bottom <= nr.bottom + 1; }")
        check(f"导航（{nav}）：切到靠后的分组，当前项被带进视野", vis)
        if nav == "rail":
            shot(page, "V8-nav-rail-1280x720")
        ctx.close()


# =====================================================================
def arrange(b):
    reset()
    ctx, page = open_page(b, 1440, 900)
    seed = group_links(ctx, "daily")
    page.locator("#homeEditBtn").click(); page.wait_for_timeout(350)
    check("编辑首页：说明条出现、按钮 aria-pressed、图标不再是链接", page.locator("#homeArrangeBar").is_visible() and page.locator("#homeEditBtn").get_attribute("aria-pressed") == "true" and page.locator("#homeList a[href]").count() == 0 and page.locator("#homeList [data-arrange-remove]").count() == 41)
    shot(page, "V9-arrange-1440")
    tiles = page.locator('[data-arrange-group="daily"] [data-arrange-id]')
    a, c = tiles.nth(0).bounding_box(), tiles.nth(3).bounding_box()
    mouse_drag(page, a["x"] + 48, a["y"] + 40, c["x"] + 75, c["y"] + 40, hold=True)
    dim = page.evaluate("() => [getComputedStyle(document.querySelector('[data-arrange-group=\"ai\"] .home-tile')).opacity, getComputedStyle(document.querySelector('[data-arrange-group=\"daily\"] .home-tile:not(.is-dragging)')).opacity]")
    check("拖动中：别的分组变暗、同组不变暗", float(dim[0]) < 0.5 and float(dim[1]) == 1.0, dim)
    shot(page, "V10-arrange-dragging-1440")
    page.mouse.up(); page.wait_for_timeout(600)
    names = [n for n, _ in group_links(ctx, "daily")]
    want = [seed[1][0], seed[2][0], seed[3][0], seed[0][0]] + [n for n, _ in seed[4:]]
    check("编辑首页：鼠标拖动，分组内顺序已保存", names == want, names[:5])
    check("没上首页的网址原地不动", [i for i, (_, h) in enumerate(group_links(ctx, "daily")) if not h] == [i for i, (_, h) in enumerate(seed) if not h])
    check("拖完焦点落在被拖的图标上", page.evaluate("() => document.activeElement.dataset.arrangeId") == "l001")
    # 拖到别的分组上方：不会跨组
    a = tiles.nth(0).bounding_box(); other = page.locator('[data-arrange-group="ai"] [data-arrange-id]').nth(2).bounding_box()
    before_ai = group_links(ctx, "ai")
    mouse_drag(page, a["x"] + 48, a["y"] + 40, other["x"] + 48, other["y"] + 40)
    check("不能跨分组：拖到别的分组上，只会落在本组末尾", group_links(ctx, "ai") == before_ai and len(group_links(ctx, "daily")) == len(seed))
    # 键盘
    page.locator('[data-arrange-group="dev"] [data-arrange-id]').first.focus()
    dev0 = [n for n, _ in group_links(ctx, "dev")]
    page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowRight"); page.wait_for_timeout(600)
    dev1 = [n for n, _ in group_links(ctx, "dev")]
    check("键盘 →×2：挪两位、焦点跟随、结果念给读屏", dev1[:3] == [dev0[1], dev0[2], dev0[0]] and page.evaluate("() => document.activeElement.dataset.arrangeId") == "l027" and "移到第 3 个" in (page.locator("#homeArrangeLive").text_content() or ""), dev1[:3])
    page.keyboard.press("Delete"); page.wait_for_timeout(700)
    check("键盘 Delete：从首页移除（分组里仍保留）", ("MDN", False) in group_links(ctx, "dev") and page.locator('[data-arrange-id="l027"]').count() == 0)
    # 角上的 −
    page.locator('[data-arrange-group="@sites"] [data-arrange-remove]').first.click(); page.wait_for_timeout(700)
    bms = api(ctx, "GET", "/api/configs")[1]["bookmarks"]
    check("点「−」：看板站点从首页移除", [x.get("show_on_home") for x in bms] == [False, True, True], [x.get("show_on_home") for x in bms])
    w = page.locator('[data-arrange-group="@sites"] [data-arrange-id]')
    w0, w1 = w.nth(0).bounding_box(), w.nth(1).bounding_box()
    mouse_drag(page, w0["x"] + 90, w0["y"] + 40, w1["x"] + 170, w1["y"] + 40)
    check("看板小组件也能拖：按下标重排，没上首页的那个原地不动", [x["name"] for x in api(ctx, "GET", "/api/configs")[1]["bookmarks"]] == ["中转站 A", "云主机 C", "中转站 B"], [x["name"] for x in api(ctx, "GET", "/api/configs")[1]["bookmarks"]])
    page.locator('[data-arrange-group="daily"] [data-arrange-id]').first.focus(); page.keyboard.press("Escape"); page.wait_for_timeout(300)
    check("Esc：退出编辑，图标恢复成链接，焦点回到「编辑」按钮", page.locator("#homeArrangeBar").is_hidden() and page.locator("#homeList a[href^='http']").count() > 30 and page.evaluate("() => document.activeElement.id") == "homeEditBtn")
    page.reload(); page.wait_for_selector("#homeList .home-section"); page.wait_for_timeout(500)
    shown = page.evaluate("() => [...document.querySelectorAll('#homeList .home-section')][1].querySelectorAll('.tile-title').length && [...[...document.querySelectorAll('#homeList .home-section')][1].querySelectorAll('.tile-title')].map(e => e.textContent)")
    check("刷新后首页顺序还在", shown[:4] == want[1:4] + [want[4]] or shown[:3] == want[:3], shown[:5])
    page.goto(BASE + "/#links/daily"); page.wait_for_timeout(900)
    cards = page.evaluate("() => [...document.querySelectorAll('#linkList .link-name span:first-child')].map(e => e.textContent)")
    check("分组页同步：卡片顺序 = 首页拖出来的顺序", cards == [n for n, _ in group_links(ctx, "daily")], cards[:4])
    # XSS：名称带标签
    api(ctx, "PUT", "/api/link_groups/ai/links/l015", {"name": '"><img src=x onerror="window.__xss=3">'})
    page.goto(BASE + "/"); page.wait_for_selector("#homeList .home-section"); page.wait_for_timeout(400)
    page.locator("#homeEditBtn").click(); page.wait_for_timeout(300)
    page.locator("#chooseHomeLinks").click(); page.wait_for_timeout(300)
    check("XSS：恶意网址名称在编辑状态和弹窗里都只当文字", page.evaluate("() => !window.__xss && !document.querySelector('#homeList img[src=\"x\"], #homeChoices img')"))
    ctx.close()


def arrange_mobile(b):
    reset()
    ctx, page = open_page(b, 390, 844, mobile=True)
    touch = touch_fn(ctx, page)
    page.locator("#homeToolsToggle").tap(); page.wait_for_timeout(200)
    page.locator("#homeEditBtn").tap(); page.wait_for_timeout(400)
    check("手机：说明条提示「按住再拖」", "按住图标" in page.locator("#homeArrangeTip").text_content())
    page.evaluate("() => document.querySelector('[data-arrange-group=\"daily\"]').scrollIntoView({ block: 'center' })"); page.wait_for_timeout(400)
    shot(page, "V11-arrange-390")
    seed = [n for n, _ in group_links(ctx, "daily")]
    tiles = page.locator('[data-arrange-group="daily"] [data-arrange-id]')
    a, c = tiles.nth(0).bounding_box(), tiles.nth(2).bounding_box()
    x, y = a["x"] + a["width"] / 2, a["y"] + 30
    tx = c["x"] + c["width"] * 0.8
    touch("touchStart", x, y); page.wait_for_timeout(340)
    for i in range(1, 11):
        touch("touchMove", x + (tx - x) * i / 10, y + 2); page.wait_for_timeout(16)
    page.wait_for_timeout(200)
    shot(page, "V12-arrange-touch-dragging-390")
    touch("touchEnd"); page.wait_for_timeout(700)
    now = [n for n, _ in group_links(ctx, "daily")]
    check("手机：长按后拖动图标，顺序已保存", now[:3] == [seed[1], seed[2], seed[0]], now[:4])
    sy = page.evaluate("() => scrollY")
    t5 = tiles.nth(5).bounding_box()
    touch("touchStart", t5["x"] + 40, t5["y"] + 30)
    for i in range(1, 11):
        touch("touchMove", t5["x"] + 40, t5["y"] + 30 - 18 * i); page.wait_for_timeout(16)
    touch("touchEnd"); page.wait_for_timeout(500)
    check("手机：不长按直接滑 = 滚动页面，顺序不变", page.evaluate("() => scrollY") > sy + 60 and [n for n, _ in group_links(ctx, "daily")] == now)
    rm = page.locator('[data-arrange-group="daily"] [data-arrange-remove]').first
    rm.scroll_into_view_if_needed(); page.evaluate("() => scrollBy(0, -200)"); page.wait_for_timeout(300)
    rm.tap(); page.wait_for_timeout(700)
    check("手机：点「−」移除（以前手机上没有任何办法把图标移出首页）", (now[0], False) in group_links(ctx, "daily"))
    page.locator("#homeArrangeDone").tap(); page.wait_for_timeout(300)
    check("手机：点「完成」退出", page.locator("#homeArrangeBar").is_hidden())
    ctx.close()


def picker(b):
    reset()
    for w, h, mobile in [(1440, 900, False), (390, 844, True)]:
        ctx, page = open_page(b, w, h, mobile=mobile)
        if mobile:
            page.locator("#homeToolsToggle").tap()
        page.locator("#homeEditBtn").click(); page.locator("#chooseHomeLinks").click(); page.wait_for_timeout(400)
        save = page.locator("#homeSave").bounding_box()
        check(f"弹窗 {w}：打开即见筛选框与「保存」（头尾固定，清单自己滚动）", page.locator("#homeFilter").is_visible() and save["y"] + save["height"] <= h and page.locator("#homeChoices").evaluate("e => e.scrollHeight > e.clientHeight && e.scrollTop === 0"))
        check(f"弹窗 {w}：计数与每组 n/m", page.locator("#homeSelectionCount").text_content() == "已选择 41 / 103 项" and page.locator(".picker-count").nth(1).text_content() == "10 / 14")
        shot(page, f"V13-picker-{w}")
        page.fill("#homeFilter", "git"); page.wait_for_timeout(150)
        check(f"弹窗 {w}：筛选 git → 3 条，按钮改成「全选筛选结果」", page.locator("#homeChoices .home-check:visible").count() == 3 and page.locator("#homeSelectAll").text_content() == "全选筛选结果")
        page.locator("#homeSelectAll").click()
        check(f"弹窗 {w}：全选只作用于筛选结果（41→42）", page.locator("#homeSelectionCount").text_content() == "已选择 42 / 103 项")
        page.fill("#homeFilter", "影音"); page.wait_for_timeout(150)
        page.locator('[data-home-all="media"]').check()
        check(f"弹窗 {w}：按分组名筛选 + 组头一键全选（+6）", page.locator("#homeSelectionCount").text_content() == "已选择 48 / 103 项")
        page.fill("#homeFilter", ""); page.locator("#homeOnlyChecked").check(); page.wait_for_timeout(150)
        check(f"弹窗 {w}：只看已选 → 48 条", page.locator("#homeChoices .home-check:visible").count() == 48)
        page.fill("#homeFilter", "zzzz"); page.wait_for_timeout(100)
        check(f"弹窗 {w}：无匹配时给出提示", page.locator(".picker-none").is_visible())
        page.locator("#homeSave").click(); page.wait_for_timeout(700)      # 筛选状态下保存：藏着的勾选照样算数
        data = api(ctx, "GET", "/api/configs")[1]
        total = sum(1 for g in data["link_groups"] for l in g["links"] if l["show_on_home"]) + sum(1 for x in data["bookmarks"] if x.get("show_on_home"))
        check(f"弹窗 {w}：带着筛选保存，被藏起来的勾选没有丢（48）", total == 48, total)
        ctx.close(); reset()


def extras(b):
    """第二轮的三样：分组折叠、待办里的网址、「常用」一行。"""
    reset()
    # ---------- 待办里的网址 ----------
    ctx, page = open_page(b, 1440, 900)
    seen = []
    ctx.on("request", lambda r: seen.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)
    api(ctx, "POST", "/api/todos", {"text": '续费见https://pay.example.com/renew?id=7。然后核对 https://bank.example@evil.example/login <img src=x onerror="window.__xss=9">'})
    page.reload(); page.wait_for_selector("#todoList .todo-item"); page.wait_for_timeout(400)
    open_todo_card(page)
    links = page.locator("#todoList .todo-item").first.locator("a.todo-link")
    info = links.evaluate_all("els => els.map(a => [a.textContent, a.getAttribute('href'), a.target, a.rel, a.getAttribute('draggable')])")
    check("待办链接：中文里紧跟句号也能正确切出网址；显示真实主机名（user@host 骗不了人）", info == [["pay.example.com/renew?…", "https://pay.example.com/renew?id=7", "_blank", "noopener noreferrer nofollow", "false"], ["evil.example/login", "https://bank.example@evil.example/login", "_blank", "noopener noreferrer nofollow", "false"]], info)
    check("待办链接 XSS：夹带的标签只当文字，页面里没有多出 img / 脚本", page.evaluate("() => !window.__xss && !document.querySelector('#todoList img') && document.querySelector('#todoList .todo-text').textContent.includes('<img src=x')"))
    n0 = len(pending(ctx))
    with page.expect_popup() as pop:
        links.first.click()
    page.wait_for_timeout(500)
    check("待办链接：点击在新标签页打开目标网址，且不会顺带勾选这一条", any(u.startswith("https://pay.example.com/renew?id=7") for u in seen) and len(pending(ctx)) == n0 and page.locator("#todoList .todo-check:checked").count() == 0, seen[:2])
    pop.value.close()
    page.bring_to_front(); open_todo_card(page)
    lb = links.first.bounding_box(); r2 = page.locator("#todoList .todo-item").nth(2).bounding_box()
    before = pending(ctx)
    check("待办链接：按下点确实在链接上", page.evaluate("([x, y]) => !!document.elementFromPoint(x, y).closest('a.todo-link')", [lb["x"] + 8, lb["y"] + lb["height"] / 2]))
    mouse_drag(page, lb["x"] + 8, lb["y"] + lb["height"] / 2, lb["x"] + 8, r2["y"] + r2["height"] * 0.8)
    for pg in ctx.pages[1:]:
        pg.close()
    check("待办链接：在链接上按下拖动不会发起整行排序", pending(ctx) == before)
    page.locator("#todoList .todo-item").first.hover()
    page.locator('#todoList .todo-item').first.locator('[data-todo-act="edit"]').click(); page.wait_for_timeout(200)
    check("待办链接：修改时看到的是原文", "https://pay.example.com/renew?id=7。然后核对" in page.locator("[data-todo-edit]").input_value())
    page.keyboard.press("Escape")
    shot(page, "W1-todo-links-1440")
    ctx.close()
    ctx, page = open_page(b, 390, 844, mobile=True)
    seen_m = []
    ctx.on("request", lambda r: seen_m.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)
    open_todo_card(page, mobile=True)
    a = page.locator("#todoList a.todo-link").first
    with page.expect_popup() as pop:
        a.tap()
    page.wait_for_timeout(500)
    check("手机：轻点待办里的链接 → 新标签页打开，不勾选", any("pay.example.com" in u for u in seen_m) and page.locator("#todoList .todo-check:checked").count() == 0)
    pop.value.close()
    shot(page, "W2-todo-links-390")
    ctx.close()

    # ---------- 分组折叠 ----------
    reset()
    ctx, page = open_page(b, 1440, 900)
    btn = page.locator('[data-home-fold="ai"]')
    check("折叠按钮：button + aria-expanded=true + aria-controls 指向内容容器", btn.evaluate("e => e.tagName === 'BUTTON' && e.getAttribute('aria-expanded') === 'true' && !!document.getElementById(e.getAttribute('aria-controls'))"))
    h0 = page.evaluate("() => document.documentElement.scrollHeight")
    btn.click(); page.wait_for_timeout(300)
    st = page.evaluate("""() => { const s = document.querySelector('[data-home-fold="ai"]').closest('.home-section'); return [s.classList.contains('is-folded'), s.querySelectorAll('.home-tile').length, s.querySelector('.section-fold').getAttribute('aria-expanded'), document.activeElement.dataset.homeFold, s.querySelector('.count').textContent]; }""")
    check("折叠：内容收起、计数还在、aria-expanded=false、焦点留在按钮上", st == [True, 0, "false", "ai", "8"], st)
    check("折叠：页面变短", page.evaluate("() => document.documentElement.scrollHeight") < h0)
    page.keyboard.press("Enter"); page.wait_for_timeout(250)
    check("键盘 Enter：再按一次展开", page.locator('[data-arrange-group="ai"] .home-tile').count() == 8 and page.evaluate("() => document.activeElement.dataset.homeFold") == "ai")
    page.locator('[data-home-fold="ai"]').click(); page.locator('[data-home-fold="monitor"]').click(); page.wait_for_timeout(250)
    shot(page, "W3-fold-1440")
    page.reload(); page.wait_for_selector("#homeList .home-section"); page.wait_for_timeout(400)
    folded = page.evaluate("() => [...document.querySelectorAll('.home-section.is-folded')].map(s => s.getAttribute('aria-label'))")
    check("折叠：刷新后还在（Cookie bh_home_fold）", folded == ["站点看板", "AI 服务"] and "bh_home_fold=ai.monitor" in page.evaluate("() => document.cookie"), folded)
    info = page.evaluate("""() => { const firsts = {}; [...document.querySelectorAll('#homeList .home-section')].forEach(s => { const r = s.getBoundingClientRect(); const k = Math.round(r.top); firsts[k] = Math.min(firsts[k] ?? 1e9, Math.round(r.left)); });
      return { lefts: [...new Set(Object.values(firsts))], over: document.documentElement.scrollWidth - innerWidth }; }""")
    check("折叠后版面：左边线仍然对齐、不溢出", len(info["lefts"]) == 1 and info["over"] <= 0, info)
    page.locator("#homeEditBtn").click(); page.wait_for_timeout(300)
    check("编辑首页时：折叠着的分组保持折叠，其余照常可拖", page.locator(".home-section.is-folded").count() == 2 and page.locator('[data-arrange-group="daily"] [data-arrange-id]').count() == 10)
    page.keyboard.press("Escape")
    page.locator("#homeArrangeDone").click(); page.wait_for_timeout(200)
    page.locator('[data-home-view="minimal"]').click(); page.wait_for_timeout(300)
    check("极简密度：没有标题行，折叠不生效（全部显示）", page.locator("#homeList .home-tile").count() == 41 and page.locator(".section-fold:visible").count() == 0)
    ctx.close()
    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_home_fold": "<script>alert(1)</script>"})
    check("折叠 Cookie 被改成脚本：整个作废，页面照常", page.locator(".home-section.is-folded").count() == 0 and page.locator("#homeList .home-tile").count() == 41)
    fb = page.locator('[data-home-fold="daily"]')
    hit_area = fb.evaluate("e => { const r = e.getBoundingClientRect(), a = getComputedStyle(e, '::after'); return [r.width, r.height, a.position, a.top]; }")
    check("手机：折叠按钮可见，触摸目标靠 ::after 扩到约 42px", fb.is_visible() and hit_area[2] == "absolute" and hit_area[3] == "-9px" and float(fb.evaluate("e => getComputedStyle(e).opacity")) >= 0.8, hit_area)
    fb.tap(); page.wait_for_timeout(300)
    check("手机：轻点折叠", page.locator('.home-section.is-folded').count() == 1 and page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0)
    shot(page, "W4-fold-390")
    ctx.close()

    # ---------- 常用一行 ----------
    reset()
    ctx, page = open_page(b, 1440, 900)
    check("常用：没有使用记录时不出现", page.locator(".is-frequent").count() == 0)

    def click_tile(name, times, middle=False):
        for _ in range(times):
            tile = page.locator(f'#homeList .home-section:not(.is-frequent) a.tile-link:has(.tile-title:text-is("{name}")), #homeList .home-section:not(.is-frequent) a.widget-link:has(.widget-name:text-is("{name}"))').first
            if middle:      # 无头浏览器里中键不一定真的开出新标签页，但 auxclick 事件照样触发——记数靠的是它
                tile.click(button="middle"); page.wait_for_timeout(250)
            else:
                with page.expect_popup():
                    tile.click()
            for extra in ctx.pages[1:]:
                extra.close()
    click_tile("Claude", 3); click_tile("GitHub", 2); click_tile("中转站 A", 2); click_tile("Grafana", 2, middle=True); click_tile("知乎", 1)
    check("常用：点击只记数、不当场重排（图标不会在手底下换位置）", page.locator(".is-frequent").count() == 0)
    raw = page.evaluate("() => document.cookie.split('; ').find(c => c.startsWith('bh_home_hits='))")
    import re as _re
    check("常用：记录在 Cookie 里，只有短哈希 + 次数，不含网址", bool(_re.match(r"^bh_home_hits=\d{1,6}(\.[a-z0-9]{1,8}-\d{1,4}){5}$", raw or "")) and "claude" not in raw and "github" not in raw, raw)
    page.reload(); page.wait_for_selector("#homeList .home-section"); page.wait_for_timeout(500)
    names = page.evaluate("() => [...document.querySelectorAll('.is-frequent .home-tile:not([hidden]) .tile-title')].map(e => e.textContent)")
    check("常用：下次打开首页出现在最前，按次数排序（中键点击也算，只点 1 次的不算）", names == ["Claude", "中转站 A", "GitHub", "Grafana"] and page.evaluate("() => document.querySelector('#homeList .home-section').classList.contains('is-frequent')"), names)
    al = page.evaluate("""() => { const s = document.querySelector('.is-frequent'); const ic = s.querySelector('.section-label .ic').getBoundingClientRect().left, av = s.querySelector('.tile-avatar').getBoundingClientRect().left, other = document.querySelectorAll('#homeList .home-section')[1].getBoundingClientRect().left; return [Math.round(ic - av), Math.round(s.getBoundingClientRect().left - other)]; }""")
    check("常用：和其他分组同一条左边线，标题与图标对齐", abs(al[0]) <= 2 and al[1] == 0, al)
    shot(page, "W5-frequent-1440")
    # 多到超过一行：始终正好一行
    page.evaluate("() => { ['https://www.youtube.com','https://www.bilibili.com','https://www.zhihu.com','https://weibo.com','https://x.com','https://www.reddit.com','https://chatgpt.com','https://pypi.org'].forEach(u => { recordHit(u); recordHit(u); }); renderHome(); }")
    page.wait_for_timeout(300)
    rows = page.evaluate("() => { const t = [...document.querySelectorAll('.is-frequent .home-tile:not([hidden])')]; return [t.length, new Set(t.map(e => Math.round(e.getBoundingClientRect().top))).size, HOME_GRID.cols]; }")
    check("常用：候选再多也正好一行（= 当前列数）", rows[1] == 1 and rows[0] == rows[2], rows)
    page.set_viewport_size({"width": 1100, "height": 800}); page.wait_for_timeout(400)
    rows2 = page.evaluate("() => { const t = [...document.querySelectorAll('.is-frequent .home-tile:not([hidden])')]; return [t.length, new Set(t.map(e => Math.round(e.getBoundingClientRect().top))).size, HOME_GRID.cols]; }")
    check("常用：窗口变窄，跟着列数收成更少的一行", rows2[1] == 1 and rows2[0] == rows2[2] and rows2[2] < rows[2], rows2)
    page.set_viewport_size({"width": 1440, "height": 900}); page.wait_for_timeout(400)
    first = page.locator(".is-frequent .home-tile").first
    first.hover(); first.locator("summary").click(); page.wait_for_timeout(200)
    gone = first.locator(".tile-title").text_content()
    first.locator("button", has_text="不在「常用」里显示").click(); page.wait_for_timeout(300)
    left = page.evaluate("() => [...document.querySelectorAll('.is-frequent .tile-title')].map(e => e.textContent)")
    check("常用：菜单「不在常用里显示」把它请出这一行", gone and gone not in left and len(left) >= 3, (gone, left))
    page.locator("#homeEditBtn").click(); page.wait_for_timeout(250)
    check("常用：编辑首页时让路", page.locator(".is-frequent").count() == 0)
    page.locator("#homeArrangeDone").click(); page.wait_for_timeout(250)
    # 同一页里「开 / 关」交替各量 4 轮取最小值：单次测量会被共享 VPS 的抖动、图标请求的回调带偏（实测偏差可达 ±60%）。
    bench = """() => { const run = (pref) => { document.cookie = 'bh_home_freq=' + pref + '; path=/'; const t0 = performance.now(); for (let i = 0; i < 15; i++) renderHome(); return (performance.now() - t0) / 15; };
      const on = [], off = []; for (let r = 0; r < 4; r++) { on.push(run('on')); off.push(run('off')); } document.cookie = 'bh_home_freq=on; path=/'; renderHome(); return [Math.min(...on), Math.min(...off)]; }"""
    t_on, t_off = page.evaluate(bench)
    check("性能：「常用」一行 + 折叠判断对 renderHome 的开销（同页交替取最小值：开 ≤ 关 × 1.35 + 2ms，且 < 40ms）", t_on <= t_off * 1.35 + 2 and t_on < 40, (round(t_on, 2), round(t_off, 2)))
    print("   renderHome with frequent row %.2f ms vs without %.2f ms" % (t_on, t_off))
    page.locator("#homeLookBtn").click(); page.wait_for_timeout(300)
    shot(page, "W6-look-modal-frequent-1440")
    page.locator('[data-home-freq="off"]').click(); page.wait_for_timeout(300)
    off = page.evaluate("() => [document.querySelectorAll('.is-frequent').length, document.cookie.includes('bh_home_hits='), document.cookie.includes('bh_home_freq=off'), document.querySelector('[data-home-freq=\"off\"]').getAttribute('aria-checked')]")
    check("外观里关闭：这一行消失、已有记录一并清掉、不再记录", off == [0, False, True, "true"], off)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    click_tile("GitHub", 1)
    check("关闭之后点击不再产生记录", "bh_home_hits=" not in page.evaluate("() => document.cookie"))
    ctx.close()
    # 手机 + 极简 + 被改坏的 Cookie
    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_home_hits": "1.<img/src=x>-9"})
    check("常用 Cookie 被改坏：整个作废，不出现这一行、不报错", page.locator(".is-frequent").count() == 0 and page.evaluate("() => !document.querySelector('#homeList img[src=\"x\"]')"))
    page.evaluate("() => { ['https://github.com','https://claude.ai','https://www.youtube.com','https://www.bilibili.com','https://x.com','https://pypi.org'].forEach((u, i) => { for (let k = 0; k < 8 - i; k++) recordHit(u); }); renderHome(); }")
    page.wait_for_timeout(300)
    m = page.evaluate("() => { const t = [...document.querySelectorAll('.is-frequent .home-tile:not([hidden])')]; return [t.length, new Set(t.map(e => Math.round(e.getBoundingClientRect().top))).size, document.documentElement.scrollWidth - innerWidth]; }")
    check("手机：常用正好一行 4 个，不溢出", m == [4, 1, 0], m)
    shot(page, "W7-frequent-390")
    page.locator("#homeToolsToggle").tap(); page.locator('[data-home-view="minimal"]').tap(); page.wait_for_timeout(300)
    mm = page.evaluate("() => { const s = document.querySelector('.is-frequent'); const t = [...s.querySelectorAll('.home-tile:not([hidden])')]; const first = document.querySelector('#homeList .home-section:not(.is-frequent) .home-tile'); return [t.length, getComputedStyle(s.querySelector('.section-label')).display, Math.round(t[0].getBoundingClientRect().left - first.getBoundingClientRect().left), Math.round(t[0].getBoundingClientRect().width - first.getBoundingClientRect().width)]; }")
    check("极简密度：常用仍自成一行、带标题，和下面的图标同列同宽", mm[0] == 4 and mm[1] == "flex" and abs(mm[2]) <= 1 and abs(mm[3]) <= 1, mm)
    shot(page, "W8-frequent-minimal-390")
    ctx.close()


def degrade_and_perf(b):
    reset()
    ctx, page = open_page(b, 1440, 900, reduced_motion="reduce")
    open_todo_card(page)
    rows = page.locator("#todoList .todo-item")
    r0, r2 = rows.nth(0).bounding_box(), rows.nth(2).bounding_box()
    mouse_drag(page, r0["x"] + 130, r0["y"] + 14, r0["x"] + 130, r2["y"] + r2["height"] * 0.8, hold=True)
    check("减少动态效果：确实拖起来了", page.locator("#todoList .todo-item.is-dragging").count() == 1)
    moved = page.evaluate("() => [...document.querySelectorAll('#todoList .todo-item:not(.is-dragging)')].some(r => r.style.transition.includes('transform'))")
    page.mouse.up(); page.wait_for_timeout(400)
    check("减少动态效果：让路不做位移动画，排序照常生效", (not moved) and pending(ctx)[2] == "给域名续费", pending(ctx)[:3])
    ctx.close()
    ctx, page = open_page(b, 1440, 900, forced_colors="active")
    page.locator("#homeEditBtn").click(); page.wait_for_timeout(300)
    shot(page, "V14-forced-colors-arrange-1440")
    check("强制颜色：壁纸让路，把手 / 移除按钮仍可见", page.evaluate("() => !document.documentElement.classList.contains('wall-on') && getComputedStyle(document.querySelector('.todo-grip')).opacity === '1'") and page.locator("[data-arrange-remove]").first.is_visible())
    ctx.close()
    ctx, page = open_page(b, 1440, 900)
    # 取多批里最快的一批（Python timeit 文档的做法）：慢的那几批多半是同机别的进程抢了 CPU，不是代码变慢。
    # 以前是一次 30 连跑的平均值，共享 VPS 上同一份代码能在 18–40ms 间飘，偶尔越过 35ms 的线而误报。
    t = page.evaluate("() => { let best = Infinity; for (let b = 0; b < 6; b++) { const t0 = performance.now(); for (let i = 0; i < 5; i++) renderHome(); best = Math.min(best, (performance.now() - t0) / 5); } return best; }")
    t2 = page.evaluate("() => { let best = Infinity; for (let b = 0; b < 5; b++) { const t0 = performance.now(); for (let i = 0; i < 40; i++) relayoutHomeGrid(); best = Math.min(best, (performance.now() - t0) / 40); } return best; }")
    check("性能：100 个网址下 renderHome 单次 < 35ms，relayout 单次 < 2ms", t < 35 and t2 < 2, (round(t, 2), round(t2, 3)))   # 最快一批实测约 20ms
    print("   renderHome %.2f ms（6 批取最快）, relayout %.3f ms" % (t, t2))
    blur = page.evaluate("() => [...document.querySelectorAll('.home-tile, .todo-item, .tile-remove, .todo-grip, .home-arrange-bar')].filter(e => getComputedStyle(e).backdropFilter !== 'none').length")
    check("性能：图标 / 待办行 / 把手都不开毛玻璃", blur == 0, blur)
    ctx.close()


def main(sections, results_file=""):
    """起服务、开浏览器、按顺序跑各段；命令行点了名就只跑点名的。任何一段抛异常只记成一条失败，不拖累其他段。"""
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    unknown = [a for a in only if a not in [fn.__name__ for fn in sections]]
    if unknown:
        sys.exit("没有这一段：%s（可选：%s）" % (" ".join(unknown), " ".join(fn.__name__ for fn in sections)))
    t0 = time.time()
    # 这几套（verify / verify_polish / verify_deck / verify_railtip / verify_transport）写于「收藏库默认公开」的年代：
    # 访客段要看到首页、空库段要看到建库引导。8947960 起设了管理密码就默认私密，这里与 tests/__init__.py 一样显式公开；
    # 私密默认由 verify_chat / verify_ask 覆盖（它们起服务前会把这个变量删掉）。
    os.environ["HUB_PUBLIC_LIBRARY"] = "1"
    start_server()
    with sync_playwright() as p:
        b = launch(p)
        for fn in sections:
            if only and fn.__name__ not in only:
                continue
            print("==", fn.__name__, flush=True)
            try:
                fn(b)
            except Exception as exc:   # 一段挂了不影响其他段，但要记成失败
                check(fn.__name__ + " 运行完毕", False, repr(exc))
        b.close()
    check("全程没有页面脚本错误", not ERRORS, ERRORS[:3])
    check("全程没有发往外部站点的请求", not EXTERNAL, EXTERNAL[:3])
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS)} checks, {len(failed)} failed, {time.time() - t0:.0f}s")
    if results_file:
        with open(results_file, "w", encoding="utf-8") as fh:
            json.dump(RESULTS, fh, ensure_ascii=False, indent=1)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main((todo_desktop, todo_mobile, todo_access, layout, arrange, arrange_mobile, picker, extras, degrade_and_perf), os.environ.get("BH_VERIFY_RESULTS", ""))

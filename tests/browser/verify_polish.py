# -*- coding: utf-8 -*-
"""本轮打磨的浏览器验证：空状态引导、小字对比度、键盘下的搜索下拉、时钟行不换行、壁纸场景浮层、清除已完成原地确认。"""
import json
import sys

from verify import *   # noqa: F401,F403  复用 check / reset / open_page / shot / api

EMPTY = {"configs": [], "bookmarks": [], "link_groups": [], "proxy_url": ""}
DIALOGS = []


def write_config(cfg):
    base = json.load(open(SEED + "/config.json", encoding="utf-8"))
    base.update(cfg)
    with open(DATA + "/config.json", "w", encoding="utf-8") as fh:
        json.dump(base, fh, ensure_ascii=False)


def rgb(page, sel, prop="color"):
    return page.evaluate("([s, p]) => getComputedStyle(document.querySelector(s))[p]", [sel, prop])


def empty_state(b):
    write_config(EMPTY)
    ctx, page = open_page(b, 1440, 900)
    txt = page.locator("#homeEmpty").inner_text()
    check("空库：标题是建库引导，不再引去自定义首页", "从第一个网址开始" in txt and "自定义首页" not in txt, txt)
    check("空库：主操作 = 新建第一个分组，次操作 = 导入", "新建第一个分组" in txt and "导入 JSON / 备份" in txt, txt)
    shot(page, "P1-empty-fresh")
    page.locator("#homeEmpty button", has_text="新建第一个分组").click()
    check("空库：点主操作打开「新建分组」弹窗", page.locator("#groupModal.show").count() == 1)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    if page.locator("#groupModal.show").count(): page.locator("#groupModal .btn.ghost").first.click()
    page.locator("#homeEmpty button", has_text="导入").click()
    check("空库：点「导入」打开导入弹窗", page.locator("#importModal.show").count() == 1)
    ctx.close()
    write_config({"bookmarks": [], "link_groups": [{"id": "g1", "name": "常用", "color": "mint", "icon": "folder", "links": []}]})
    ctx, page = open_page(b, 1440, 900)
    txt = page.locator("#homeEmpty").inner_text()
    check("有分组无网址：主操作 = 添加第一个网址", "添加第一个网址" in txt and "新建第一个分组" not in txt, txt)
    page.locator("#homeEmpty button", has_text="添加第一个网址").click()
    check("有分组无网址：打开添加网址弹窗并勾上「展示到首页」", page.locator("#linkModal.show").count() == 1 and page.locator("#lk_home").is_checked())
    ctx.close()
    ctx, page = open_page(b, 390, 844, mobile=True, unlocked=False)
    btns = page.locator("#homeEmpty button").all_inner_texts()
    check("访客 + 空库：只给解锁入口，没有写操作", btns == ["去解锁"], btns)
    shot(page, "P2-empty-visitor-mobile")
    ctx.close()
    reset()
    ctx, page = open_page(b, 1440, 900)
    check("有数据：首页照常渲染，空状态收起", page.locator("#homeList .home-section").count() > 0 and not page.locator("#homeEmpty").is_visible())
    ctx.close()


def contrast_and_overlays(b):
    ctx, page = open_page(b, 1440, 900, cookies={"bh_wallpaper": "off"})
    check("深色主题 --text-3 = #938e87", page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim()") == "#938e87")
    ctx.close()
    # 浅色主题 + 壁纸：弹窗 / 命令面板 / 提示条 / 排序菜单固定深色
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light", "bh_wallpaper": "aurora"})
    check("浅色 + 壁纸：场景已启用", page.evaluate("() => document.documentElement.classList.contains('wall-on') && document.documentElement.dataset.theme === 'light'"))
    page.locator("#homeToolsToggle").click() if page.locator("#homeToolsToggle").is_visible() else None
    page.locator("#homeAddLink").click(); page.wait_for_selector("#linkModal.show")
    bg, fg = rgb(page, "#linkModal .modal", "backgroundColor"), rgb(page, "#linkModal .modal", "color")
    check("浅色 + 壁纸：弹窗是深色实底 + 浅色字", bg == "rgb(25, 23, 20)" and fg == "rgb(245, 243, 240)", (bg, fg))
    check("浅色 + 壁纸：弹窗里的输入框也是深色", rgb(page, "#linkModal input[type=text], #linkModal input", "backgroundColor") == "rgb(17, 15, 13)", rgb(page, "#linkModal input", "backgroundColor"))
    check("浅色 + 壁纸：遮罩用深色那一档", rgb(page, "#linkModal", "backgroundColor") == "rgba(8, 6, 4, 0.7)", rgb(page, "#linkModal", "backgroundColor"))
    page.wait_for_timeout(300); shot(page, "P3-wall-light-modal")
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    page.keyboard.press("Control+k"); page.wait_for_selector("#omniModal.show")
    check("浅色 + 壁纸：命令面板深色", rgb(page, "#omniModal .omni", "backgroundColor") != "rgb(255, 255, 255)", rgb(page, "#omniModal .omni", "backgroundColor"))
    page.keyboard.press("Escape")
    page.evaluate("() => toast('验证提示', 'ok')"); page.wait_for_selector(".toast")
    check("浅色 + 壁纸：提示条深色", rgb(page, ".toast", "backgroundColor") == "rgb(32, 30, 26)", rgb(page, ".toast", "backgroundColor"))
    ctx.close()
    # 对照：浅色、不开壁纸——弹窗仍是白的（只改壁纸场景）
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light", "bh_wallpaper": "off"})
    page.locator("#homeToolsToggle").click() if page.locator("#homeToolsToggle").is_visible() else None
    page.locator("#homeAddLink").click(); page.wait_for_selector("#linkModal.show")
    check("浅色、无壁纸：弹窗保持白底深字（不受影响）", rgb(page, "#linkModal .modal", "backgroundColor") == "rgb(255, 254, 253)" and rgb(page, "#linkModal .modal", "color") == "rgb(26, 23, 18)")
    shot(page, "P4-light-nowall-modal")
    ctx.close()
    # 图标栏宽度令牌没有被浮层那组声明盖掉
    for nav, want in (("rail", "72px"), ("full", "252px")):
        ctx, page = open_page(b, 1440, 900, cookies={"bh_home_nav": nav, "bh_theme": "light"})
        page.keyboard.press("Control+k"); page.wait_for_selector("#omniModal.show")
        # 壁纸场景（默认）里侧栏浮起、左边留 10px：它占的那一列 = 自身宽度 + 左外边距。
        got = page.evaluate("() => { const s = getComputedStyle(document.querySelector('.sidebar')); return [getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w').trim(), (parseFloat(s.width) + parseFloat(s.marginLeft)) + 'px']; }")
        check(f"导航 {nav}：--sidebar-w = {want}，没被浮层那组令牌盖掉", got[0] == want and got[1] == want, got)
        ctx.close()


def dropdown_keyboard(b):
    ctx, page = open_page(b, 390, 844, mobile=True)
    page.locator("#homeSearch").click(); page.keyboard.type("e"); page.wait_for_timeout(300)
    box = page.evaluate("() => { const l = document.getElementById('homeSearchList'), r = l.getBoundingClientRect(); return { h: r.height, bottom: r.bottom, sh: l.scrollHeight, dd: document.getElementById('homeSearchBox').style.getPropertyValue('--dd-max') }; }")
    check("手机：无键盘时下拉 ≤ min(60vh, 440)", box["h"] <= min(0.6 * 844, 440) + 1 and box["dd"] != "", box)
    # 模拟键盘弹起：可视视口只剩 430px（布局视口不变），派发 visualViewport 的 resize
    page.evaluate("() => { Object.defineProperty(window.visualViewport, 'height', { configurable: true, get: () => 430 }); window.visualViewport.dispatchEvent(new Event('resize')); }")
    page.wait_for_timeout(150)
    box2 = page.evaluate("() => { const l = document.getElementById('homeSearchList'), r = l.getBoundingClientRect(); const last = l.lastElementChild; l.scrollTop = l.scrollHeight; const lr = last.getBoundingClientRect(); return { h: r.height, bottom: r.bottom, lastBottom: lr.bottom, lastText: last.textContent.trim().slice(0, 30), dd: document.getElementById('homeSearchBox').style.getPropertyValue('--dd-max') }; }")
    check("手机：键盘弹起后下拉收到键盘上沿以内", box2["bottom"] <= 430 and box2["h"] < box["h"] + 1, box2)
    check("手机：最后一行（搜索引擎回车项）滚到底后露在键盘上方", box2["lastBottom"] <= 430 and "搜索" in box2["lastText"], box2)
    shot(page, "P5-dropdown-keyboard")
    ctx.close()
    ctx, page = open_page(b, 1440, 900)
    page.locator("#homeSearch").click(); page.keyboard.type("e"); page.wait_for_timeout(300)
    h = page.evaluate("() => document.getElementById('homeSearchList').getBoundingClientRect().height")
    check("桌面：下拉上限不变（≤ 440）", 0 < h <= 441, h)
    ctx.close()


def hero_row(b):
    for w, weekday_visible in ((320, False), (360, True), (390, True)):
        ctx, page = open_page(b, w, 700, mobile=True)
        page.evaluate("() => { heroGreeting.textContent = '晚上好'; heroDate.textContent = '12月28日'; heroWeekday.textContent = '星期四'; }")
        m = page.evaluate("() => { const meta = document.querySelector('.hero-meta'), hero = document.querySelector('.home-hero'); return { metaH: meta.getBoundingClientRect().height, lh: parseFloat(getComputedStyle(meta).lineHeight) || 0, heroH: hero.getBoundingClientRect().height, wk: getComputedStyle(heroWeekday).display, over: document.documentElement.scrollWidth > innerWidth, clipped: meta.scrollWidth > meta.clientWidth + 1 }; }")
        check(f"{w}px：时钟行只有一行、页面不横向溢出", m["metaH"] <= 22 and not m["over"], m)
        check(f"{w}px：星期{'显示' if weekday_visible else '收起'}，日期没被省略掉", (m["wk"] != "none") == weekday_visible and not m["clipped"], m)
        if w == 320: shot(page, "P6-hero-320")
        ctx.close()
    ctx, page = open_page(b, 1440, 900)
    t = page.locator(".hero-meta").inner_text().replace("\n", " ")
    check("桌面：问候 · 日期 星期 仍完整", "月" in t and "星期" in t or "周" in t, t)
    ctx.close()


def clear_done(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_home_todo": "open"})
    page.on("dialog", lambda d: (DIALOGS.append(d.message), d.dismiss()))
    done0 = sum(1 for t in api(ctx, "GET", "/api/todos")[1]["todos"] if t["done"])
    if not page.locator("#todoDone").evaluate("d => d.open"): page.locator("#todoDone > summary").click()
    btn = page.locator("#todoClear")
    btn.click(); page.wait_for_timeout(100)
    check("清除已完成：第一下只换文案，不弹原生对话框", btn.inner_text() == f"确认清除 {done0} 条？" and not DIALOGS and "confirming" in btn.get_attribute("class"), (btn.inner_text(), DIALOGS))
    check("清除已完成：点按钮不把「已完成」折叠起来", page.locator("#todoDone").evaluate("d => d.open"))
    check("清除已完成：读屏提示已写入", "再按一次" in page.locator("#todoLive").inner_text())
    shot(page, "P7-clear-confirming")
    page.wait_for_timeout(3300)
    check("清除已完成：3 秒无操作自动还原，数据未动", btn.inner_text() == "清除已完成" and sum(1 for t in api(ctx, "GET", "/api/todos")[1]["todos"] if t["done"]) == done0)
    btn.click(); page.keyboard.press("Escape"); page.wait_for_timeout(100)
    check("清除已完成：Esc 取消", btn.inner_text() == "清除已完成")
    btn.click(); page.wait_for_timeout(80); btn.click(); page.wait_for_timeout(700)
    left = sum(1 for t in api(ctx, "GET", "/api/todos")[1]["todos"] if t["done"])
    check("清除已完成：3 秒内再点一次才真删", left == 0 and not DIALOGS, left)
    ctx.close(); reset()


if __name__ == "__main__":
    main((empty_state, contrast_and_overlays, dropdown_keyboard, hero_row, clear_done))

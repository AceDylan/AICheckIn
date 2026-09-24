# -*- coding: utf-8 -*-
"""图标栏即时提示的浏览器验证。"""
import sys
from verify import *   # noqa: F401,F403

TIP = "() => { const t = document.getElementById('railTip'); const r = t.getBoundingClientRect(); return { hidden: t.hidden, text: t.textContent, left: r.left, top: r.top, h: r.height, right: r.right }; }"


def rail(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_home_nav": "rail"})
    item = page.locator("#libSubnav .subnav-item[data-sortable]").nth(1)
    name = item.locator(".sub-text").text_content()
    title0 = item.get_attribute("title")
    box = item.bounding_box()
    item.hover(); page.wait_for_timeout(60)
    check("悬停 60ms：还没出（有 120ms 防抖，划过不闪）", page.evaluate(TIP)["hidden"])
    page.wait_for_timeout(260)
    tip = page.evaluate(TIP)
    check("悬停 ~300ms 内出提示，文字 = 分组名", not tip["hidden"] and tip["text"] == name, (tip, name))
    check("提示在图标右侧、垂直居中、没被侧栏裁掉", tip["left"] >= box["x"] + box["width"] + 6 and abs((tip["top"] + tip["h"] / 2) - (box["y"] + box["height"] / 2)) <= 2 and tip["left"] > 72, (tip, box))
    check("显示期间原生 title 暂存、aria-label 顶上", item.get_attribute("title") is None and item.get_attribute("aria-label") == title0)
    shot(page, "P8-rail-tip")
    page.mouse.move(700, 450); page.wait_for_timeout(120)
    check("移开：提示收起，title 还原、临时 aria-label 撤掉", page.evaluate(TIP)["hidden"] and item.get_attribute("title") == title0 and item.get_attribute("aria-label") is None)
    # 顶层入口
    page.locator('.tab[data-view="settings"]').hover(); page.wait_for_timeout(300)
    check("顶层入口也有提示", page.evaluate(TIP)["text"] == "系统设置" and not page.evaluate(TIP)["hidden"], page.evaluate(TIP))
    page.mouse.move(700, 450); page.wait_for_timeout(120)
    check("自带 aria-label 的入口还原后不丢 aria-label", page.locator('.tab[data-view="settings"]').get_attribute("aria-label") == "系统设置")
    # 键盘
    page.locator("#homeSearch").focus()
    page.keyboard.press("Shift+Tab"); page.keyboard.press("Shift+Tab"); page.keyboard.press("Shift+Tab")
    for _ in range(12):
        if page.evaluate("() => !!document.activeElement.closest('.sidebar .tab, .sidebar .subnav-item, .sidebar .omni-trigger')"): break
        page.keyboard.press("Shift+Tab")
    page.wait_for_timeout(120)
    tip = page.evaluate(TIP)
    focused = page.evaluate("() => document.activeElement.className")
    check("键盘聚焦到侧栏入口：立即出提示", not tip["hidden"] and tip["text"], (tip, focused))
    page.keyboard.press("Escape"); page.wait_for_timeout(80)
    check("Esc 收起提示", page.evaluate(TIP)["hidden"])
    # 点击：提示让路，导航照常
    item.hover(); page.wait_for_timeout(300); item.click(); page.wait_for_timeout(300)
    check("点击后提示收起，页面切到该分组", page.evaluate(TIP)["hidden"] and page.evaluate("() => LIB.page") == item.get_attribute("data-lib"))
    page.wait_for_timeout(300)
    check("点击后指针没动：重绘出来的同一项不再弹提示", page.evaluate(TIP)["hidden"])
    page.mouse.move(700, 450); page.wait_for_timeout(80); item.hover(); page.wait_for_timeout(300)
    check("移开再回来：提示恢复", not page.evaluate(TIP)["hidden"])
    page.mouse.move(700, 450); page.wait_for_timeout(80)
    ctx.close()
    # 浅色、无壁纸
    ctx, page = open_page(b, 1440, 900, cookies={"bh_home_nav": "rail", "bh_theme": "light", "bh_wallpaper": "off"})
    page.locator("#libSubnav .subnav-item").nth(2).hover(); page.wait_for_timeout(300)
    check("浅色主题：提示仍是深底浅字", page.evaluate("() => getComputedStyle(document.getElementById('railTip')).backgroundColor") == "rgb(32, 30, 26)" and not page.evaluate(TIP)["hidden"])
    shot(page, "P9-rail-tip-light")
    ctx.close()
    # 完整侧栏：文字看得见 → 不出
    ctx, page = open_page(b, 1440, 900, cookies={"bh_home_nav": "full"})
    it = page.locator("#libSubnav .subnav-item").nth(2); t0 = it.get_attribute("title")
    it.hover(); page.wait_for_timeout(320)
    check("完整侧栏：不出提示，title 不动", page.evaluate(TIP)["hidden"] and it.get_attribute("title") == t0)
    ctx.close()
    # 手机：底部标签栏，触摸不出
    ctx, page = open_page(b, 390, 844, mobile=True)
    page.locator('.tab[data-view="settings"]').tap(); page.wait_for_timeout(320)
    check("手机触摸：不出提示", page.evaluate(TIP)["hidden"])
    ctx.close()


if __name__ == "__main__":
    main((rail,))

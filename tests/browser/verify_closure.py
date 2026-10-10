# -*- coding: utf-8 -*-
"""首页管理闭环（docs/hub-ui-strategy.md 批次 A–C）的浏览器验证：编辑弹窗原地删除、首页搜索「查看全部」与 ⌘K 带字、
编辑首页时不抢焦点、编辑态轻点 / Enter 出菜单（触屏与鼠标）、手机弹窗 ✕、手机分组标签滚进视野。"""
from verify import *   # noqa: F401,F403

PICK_QUERY = """() => { for (const q of ['a', 'e', 'o', 'i', 'n', 's']) { const n = omniSearch(q).length; if (n > HOME_SEARCH_LIMIT + 1) return [q, n]; } return null; }"""


def search_more(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light", "bh_wallpaper": "off"})
    q, n = page.evaluate(PICK_QUERY)
    page.locator("#homeSearch").fill(q); page.wait_for_timeout(200)
    more = page.locator('#homeSearchList .home-search-row', has_text="查看全部")
    check("首页搜索命中超过 6 条：下拉里多一行「查看全部 N 条结果」", more.count() == 1 and f"查看全部 {n} 条结果" in more.text_content(), (q, n))
    items = page.locator("#homeSearchList .home-search-row[class*='tone-']").count()
    check("收藏结果仍然只列 6 条", items == 6, items)
    for _ in range(6):
        page.keyboard.press("ArrowDown")
    check("方向键能选中「查看全部」这一行", "查看全部" in page.locator("#homeSearchList .home-search-row.is-active").text_content())
    shot(page, "C1-search-more")
    page.keyboard.press("Enter"); page.wait_for_timeout(400)
    st = page.evaluate("() => [document.getElementById('omniModal').classList.contains('show'), document.getElementById('omniInput').value, document.getElementById('homeSearch').value, document.getElementById('homeSearchList').hidden]")
    check("回车：打开全局搜索、带着原来的字，首页搜索框清空收起", st == [True, q, "", True], st)
    rows = page.locator("#omniList [data-omni]").count()
    check("全局搜索里看得到第 7 条以后的结果", rows > 6, rows)
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    page.locator("#homeSearch").fill("docs"); page.wait_for_timeout(100)
    page.keyboard.press("Control+k"); page.wait_for_timeout(400)
    st = page.evaluate("() => [document.getElementById('omniModal').classList.contains('show'), document.getElementById('omniInput').value, document.getElementById('homeSearch').value]")
    check("首页搜索框里按 Ctrl+K：面板预填已输入的字", st == [True, "docs", ""], st)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    page.locator("#homeSearch").fill("zzzz-nothing"); page.wait_for_timeout(150)
    check("命中不多时没有「查看全部」", page.locator('#homeSearchList .home-search-row', has_text="查看全部").count() == 0)
    ctx.close()
    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_theme": "light"})
    page.locator("#homeSearch").tap(); page.locator("#homeSearch").fill(q); page.wait_for_timeout(300)
    page.locator('#homeSearchList .home-search-row', has_text="查看全部").tap(); page.wait_for_timeout(500)
    box = page.evaluate("() => { const r = document.querySelector('#omniModal .omni').getBoundingClientRect(); return [document.getElementById('omniModal').classList.contains('show'), Math.round(r.width), document.getElementById('omniInput').value]; }")
    check("手机：点「查看全部」打开全屏全局搜索（手机上原本没有 ⌘K 入口）", box[0] and box[1] == 390 and box[2] == q, box)
    shot(page, "C2-search-more-390")
    ctx.close()


def modal_delete(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light", "bh_wallpaper": "off"})
    seed = group_links(ctx, "daily")
    page.locator("#homeAddLink").click(); page.wait_for_timeout(300)
    check("新建网址：没有「删除」", page.locator("#linkDelete").is_hidden())
    page.locator("#linkCancel").click(); page.wait_for_timeout(200)
    first = page.locator("#homeList .home-tile:has(button[onclick^=\"editLink('daily'\"])").first
    first.hover(); first.locator(".tile-menu > summary").click(); page.wait_for_timeout(200)
    first.locator(".menu-popover button", has_text="编辑").click(); page.wait_for_timeout(400)
    check("首页图标「···」→ 编辑：弹窗左下角有「删除网址」", page.locator("#linkDelete").is_visible())
    name = page.locator("#lk_name").input_value()
    shot(page, "C3-link-modal-delete")
    page.locator("#linkDelete").click(); page.wait_for_timeout(500)
    gone = page.evaluate("(n) => !STATE.link_groups.some(g => (g.links || []).some(l => l.name === n))", name)
    check("点「删除网址」：弹窗关闭、网址立刻不见，提示条带「撤销」",
          page.locator("#linkModal.show").count() == 0 and gone and page.locator(".toast .toast-action", has_text="撤销").count() == 1, name)
    page.locator(".toast .toast-action", has_text="撤销").click(); page.wait_for_timeout(600)
    check("撤销：回到原分组的原位置", group_links(ctx, "daily") == seed)
    page.evaluate("() => openLibPage('monitor')"); page.wait_for_timeout(400)
    page.evaluate("() => editBm(0)"); page.wait_for_timeout(600)
    check("编辑看板站点：弹窗里有「删除站点」", page.locator("#bmDelete").is_visible())
    page.locator("#bmCancel").click(); page.wait_for_timeout(200)
    page.locator("#addBm").click(); page.wait_for_timeout(300)
    check("新增站点：没有「删除站点」", page.locator("#bmDelete").is_hidden())
    ctx.close()


def arrange_menu(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light", "bh_wallpaper": "off"})
    page.locator("#homeEditBtn").click(); page.wait_for_timeout(400)
    tiles = page.locator('[data-arrange-group="daily"] [data-arrange-id]')
    tiles.nth(1).focus(); page.keyboard.press("a"); page.wait_for_timeout(150)
    st = page.evaluate("() => [document.activeElement.dataset.arrangeId || document.activeElement.id, document.getElementById('homeSearch').value]")
    check("编辑首页时按字母键：焦点留在图标上、搜索框不进字", st[0] and st[0] != "homeSearch" and st[1] == "", st)
    tiles.nth(1).click(); page.wait_for_timeout(250)
    menu = page.locator("#homeList .arrange-menu")
    check("编辑态单击图标：弹出同一份菜单（编辑 / 复制网址 / 前往 / 从首页移除）",
          menu.count() == 1 and menu.locator(".menu-popover").is_visible() and menu.locator("button").count() == 4, menu.count())
    pop = menu.locator(".menu-popover").bounding_box()
    check("菜单完整在视口里", pop and pop["y"] >= 0 and pop["y"] + pop["height"] <= 900, pop)
    shot(page, "C4-arrange-menu-1440")
    page.mouse.click(5, 450); page.wait_for_timeout(200)
    check("点别处：菜单收起并摘掉", page.locator("#homeList .arrange-menu").count() == 0)
    tiles.nth(2).focus(); page.keyboard.press("Enter"); page.wait_for_timeout(200)
    focused = page.evaluate("() => !!document.activeElement.closest('.arrange-menu')")
    check("焦点在图标上按 Enter：菜单打开、焦点进第一项", page.locator("#homeList .arrange-menu").count() == 1 and focused)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    st = page.evaluate("() => [document.querySelectorAll('#homeList .arrange-menu').length, document.activeElement.dataset.arrangeId || '', ARRANGE.on]")
    check("Esc 先关菜单、焦点回图标，仍在编辑", st[0] == 0 and st[1] != "" and st[2] is True, st)
    seed = [n for n, _ in group_links(ctx, "daily")]
    a, c = tiles.nth(0).bounding_box(), tiles.nth(2).bounding_box()
    mouse_drag(page, a["x"] + a["width"] / 2, a["y"] + 30, c["x"] + c["width"] * 0.8, c["y"] + 30)
    check("鼠标拖动排序：松手后不弹菜单", page.locator("#homeList .arrange-menu").count() == 0 and [n for n, _ in group_links(ctx, "daily")] != seed)
    tiles.nth(0).click(); page.wait_for_timeout(250)
    page.locator("#homeList .arrange-menu button", has_text="编辑").click(); page.wait_for_timeout(500)
    check("菜单里点「编辑」：打开编辑弹窗、菜单收起", page.locator("#linkModal.show").count() == 1 and page.locator("#homeList .arrange-menu").count() == 0)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    ctx.close()

    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_theme": "light"})
    touch = touch_fn(ctx, page)
    page.locator("#homeToolsToggle").tap(); page.wait_for_timeout(200)
    page.locator("#homeEditBtn").tap(); page.wait_for_timeout(400)
    check("手机：说明条提示可以轻点图标", "轻点图标" in page.locator("#homeArrangeTip").text_content())
    page.evaluate("() => document.querySelector('[data-arrange-group=\"daily\"]').scrollIntoView({ block: 'center', behavior: 'instant' })"); page.wait_for_timeout(400)
    tiles = page.locator('[data-arrange-group="daily"] [data-arrange-id]')
    tiles.nth(1).tap(); page.wait_for_timeout(400)
    menu = page.locator("#homeList .arrange-menu .menu-popover")
    check("手机：编辑态轻点图标弹出菜单（触屏上平时没有「···」）", menu.count() == 1 and menu.is_visible())
    if menu.count():
        mb = menu.bounding_box()
        nav_top = page.evaluate("() => document.querySelector('.sidebar').getBoundingClientRect().top")
        check("手机：菜单不被底栏盖住、不出屏", mb["x"] >= 0 and mb["x"] + mb["width"] <= 390 and mb["y"] >= 0 and mb["y"] + mb["height"] <= nav_top + 1, (mb, nav_top))
    shot(page, "C5-arrange-menu-390")
    page.locator("#homeList .arrange-menu button", has_text="编辑").tap(); page.wait_for_timeout(500)
    check("手机：菜单里点「编辑」打开编辑弹窗", page.locator("#linkModal.show").count() == 1)
    shot(page, "C6-link-modal-390")
    x = page.locator("#linkModal .modal-x")
    bb = x.bounding_box() if x.is_visible() else None
    check("手机：弹窗右上角有 ✕，触控区不小于 44px", bb and bb["width"] >= 44 and bb["height"] >= 44, bb)
    check("手机：编辑弹窗里「删除网址」在可见的底栏上", page.locator("#linkDelete").is_visible())
    x.tap(); page.wait_for_timeout(400)
    check("手机：点 ✕ 关闭（等于取消）", page.locator("#linkModal.show").count() == 0)
    seed = [n for n, _ in group_links(ctx, "daily")]
    a, c = tiles.nth(0).bounding_box(), tiles.nth(2).bounding_box()
    x0, y0, tx = a["x"] + a["width"] / 2, a["y"] + 30, c["x"] + c["width"] * 0.8
    touch("touchStart", x0, y0); page.wait_for_timeout(340)
    for i in range(1, 11):
        touch("touchMove", x0 + (tx - x0) * i / 10, y0 + 2); page.wait_for_timeout(16)
    touch("touchEnd"); page.wait_for_timeout(700)
    check("手机：长按拖动照旧排序，松手不弹菜单", page.locator("#homeList .arrange-menu").count() == 0 and [n for n, _ in group_links(ctx, "daily")] != seed)
    ctx.close()


def mobile_modal_and_chips(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light", "bh_wallpaper": "off"})
    page.locator("#homeAddLink").click(); page.wait_for_timeout(300)
    check("桌面：弹窗头部不显示 ✕", page.locator("#linkModal .modal-x").is_hidden())
    ctx.close()
    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_theme": "light"})
    n = page.evaluate("() => document.querySelectorAll('.modal-mask:not(.omni-mask) .modal-head .modal-x').length")
    total = page.evaluate("() => document.querySelectorAll('.modal-mask:not(.omni-mask) .modal-head').length")
    check("每个弹窗（⌘K 面板除外）都有 ✕", n == total and n >= 9, (n, total))
    page.evaluate("() => openKeysModal()"); page.wait_for_timeout(300)
    page.locator("#keysModal .modal-x").tap(); page.wait_for_timeout(300)
    check("手机：快捷键表点 ✕ 关闭", page.locator("#keysModal.show").count() == 0)
    ob = page.evaluate("() => getComputedStyle(document.querySelector('#linkModal .modal')).overscrollBehaviorY")
    check("弹窗滚到头不带着背景滚（overscroll-behavior: contain）", ob == "contain", ob)
    last = page.evaluate("() => libGroups()[libGroups().length - 1].id")
    page.evaluate("(g) => openLibPage(g)", last); page.wait_for_timeout(600)
    r = page.evaluate("""() => { const row = document.getElementById('libChips'), c = row.querySelector('.chip.active');
      const a = row.getBoundingClientRect(), b = c.getBoundingClientRect(); return [Math.round(b.left - a.left), Math.round(a.right - b.right), row.scrollLeft, scrollY]; }""")
    check("手机：切到最右边的分组，标签行里它完整可见", r[0] >= 0 and r[1] >= 0 and r[2] > 0, r)
    shot(page, "C7-chips-last-390")
    page.evaluate("() => openLibPage(libGroups()[0].id)"); page.wait_for_timeout(600)
    r = page.evaluate("""() => { const row = document.getElementById('libChips'), c = row.querySelector('.chip.active');
      const a = row.getBoundingClientRect(), b = c.getBoundingClientRect(); return [Math.round(b.left - a.left), Math.round(a.right - b.right)]; }""")
    check("手机：再切回靠左的分组，也滚回来", r[0] >= 0 and r[1] >= 0, r)
    ctx.close()


if __name__ == "__main__":
    main((search_more, modal_delete, arrange_menu, mobile_modal_and_chips))

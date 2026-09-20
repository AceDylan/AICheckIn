# -*- coding: utf-8 -*-
"""首页组件的真实浏览器验证：桌面右侧一列 / 手机标签条、添加移除排序、日历、倒数日、便签、访客视角。"""
from verify import *   # noqa: F401,F403  复用 check / main / reset / new_context
from common import api as request


def new_page(b, viewport, mobile=False, cookies=None, theme="dark", unlocked=True):
    ctx = new_context(b, viewport["width"], viewport["height"], mobile=mobile, unlocked=unlocked, locale="zh-CN", timezone_id="Asia/Shanghai")
    jar = dict(cookies or {})
    jar.setdefault("bh_theme", theme)
    ctx.add_cookies([{"name": k, "value": v, "url": BASE} for k, v in jar.items()])
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
    page.on("pageerror", lambda e: page.errors.append("PAGEERROR " + str(e)))
    page.on("request", lambda r: EXTERNAL.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED")
    return ctx, page


def visible_cards(page):
    return page.evaluate("() => Array.from(document.querySelectorAll('#deckCards > .deck-card')).filter(el => el.offsetParent !== null).map(el => el.dataset.deck)")


def desktop(b):
    reset()
    admin = new_context(b, 800, 600)

    def api(path, method="GET", body=None):
        return request(admin, method, path, body)[1]
    # 先清场：倒数日 / 便签回到空
    for d in api("/api/deck")["deck"]["days"]:
        api("/api/deck/days/" + d["id"], "DELETE")
    api("/api/deck/memo", "PUT", {"text": ""})

    ctx, page = new_page(b, {"width": 1440, "height": 900})
    check("desktop: default cards are calendar + todo in a side column", visible_cards(page) == ["calendar", "todo"], visible_cards(page))
    geo = page.evaluate("""() => { const d = document.getElementById('homeDeck').getBoundingClientRect(), m = document.querySelector('.home-main').getBoundingClientRect();
        return { deckLeft: d.left, mainRight: m.right, deckW: d.width, tabs: getComputedStyle(document.getElementById('deckTabs')).display, pos: getComputedStyle(document.getElementById('homeDeck')).position }; }""")
    check("desktop: deck sits to the right of the icons, sticky, tab strip hidden", geo["deckLeft"] >= geo["mainRight"] and geo["tabs"] == "none" and geo["pos"] == "sticky", geo)
    check("desktop: no horizontal overflow", page.evaluate("() => document.documentElement.scrollWidth <= 1440"))

    # 日历：下一个节日、翻月、点选、键盘、回到今天
    head = page.inner_text("#calNext")
    check("calendar: shows a next-festival headline with a day count", ("下一个节日" in head or "今天" in head or "放假中" in head) and len(head) > 6, head)
    month = page.inner_text("#calMonth")
    page.click("#calNextMonth")
    check("calendar: next month button changes the month", page.inner_text("#calMonth") != month)
    check("calendar: 'back to today' appears off the current month", page.is_visible("#calToday"))
    cells = page.locator("#calGrid .cal-day:not(.is-other)")
    cells.nth(9).click()
    picked = page.evaluate("() => document.querySelector('#calGrid .is-picked').dataset.calDay")
    check("calendar: clicking a day picks it and keeps focus on it", page.evaluate("() => document.activeElement.classList.contains('is-picked')"))
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowDown")
    moved = page.evaluate("() => document.activeElement.dataset.calDay")
    check("calendar: arrow keys walk by day / week", int(moved) == int(picked) + 8, (picked, moved))
    check("calendar: exactly one tab stop in the grid", page.evaluate("() => document.querySelectorAll('#calGrid [tabindex=\"0\"]').length") == 1)
    check("calendar: detail line follows the picked day", "天后" in page.inner_text("#calDetail") or "天前" in page.inner_text("#calDetail"))
    page.click("#calToday")
    check("calendar: back to today", page.inner_text("#calMonth") == month and not page.is_visible("#calToday")
          and page.evaluate("() => document.activeElement.classList.contains('is-today')"))
    page.evaluate("() => { document.activeElement.blur(); }")

    # 组件弹窗：添加、排序（箭头 + 拖动）、移除
    page.click("#homeDeckBtn")
    check("manager: modal opens listing all six kinds", page.locator("#deckPicker .deck-pick").count() == 6)
    page.locator('[data-deck-pick="days"] .switch').click()
    page.locator('[data-deck-pick="memo"] .switch').click()
    check("manager: switching on adds cards immediately", visible_cards(page) == ["calendar", "todo", "days", "memo"], visible_cards(page))
    page.locator('[data-deck-pick="memo"] [data-deck-move="-1"]').click()
    check("manager: arrow button moves a card up", visible_cards(page) == ["calendar", "todo", "memo", "days"], visible_cards(page))
    check("manager: focus stays on the arrow after the move", page.evaluate("() => document.activeElement.dataset.deckMove") == "-1")
    grip = page.locator('[data-deck-pick="days"] [data-deck-grip]').bounding_box()
    top = page.locator('[data-deck-pick="calendar"]').bounding_box()
    page.mouse.move(grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2)
    page.mouse.down()
    page.mouse.move(grip["x"] + 4, top["y"] + 30, steps=8)
    page.mouse.move(grip["x"] + 4, top["y"] + 4, steps=4)
    page.mouse.up()
    check("manager: dragging the grip reorders", visible_cards(page) == ["days", "calendar", "todo", "memo"], visible_cards(page))
    cookie = page.evaluate("() => document.cookie")
    check("manager: order persisted in the cookie", "bh_home_deck=days.calendar.todo.memo" in cookie, cookie)
    page.locator('[data-deck-pick="days"] [data-deck-grip]').focus()
    page.keyboard.press("ArrowDown")
    check("manager: arrow keys on the grip reorder", visible_cards(page) == ["calendar", "days", "todo", "memo"], visible_cards(page))
    page.screenshot(path=SHOTS + "/v-desk-manager.png")
    page.click("#deckModalClose")
    check("manager: closes and returns focus to the toolbar button", page.evaluate("() => document.activeElement.id") == "homeDeckBtn")

    # 倒数日：添加 → 列表；修改；删除
    page.click("#daysAdd")
    page.fill("#daysName", "妈妈生日 <b>")
    page.fill("#daysDate", "1970-03-08")
    page.select_option("#daysRepeat", "lunar")
    page.click("#daysSave")
    page.wait_for_selector("#daysList .days-item")
    row = page.inner_text("#daysList .days-item")
    check("days: lunar-repeat item is saved and rendered escaped", "妈妈生日 <b>" in row and "农历二月初一" in row and "周年" in row, row)
    check("days: no markup injected", page.locator("#daysList b >> text=妈妈").count() == 0 and page.evaluate("() => !document.querySelector('#daysList .days-name b')"))
    page.click("#daysAdd")
    page.fill("#daysName", "域名到期")
    page.fill("#daysDate", "2026-12-31")
    page.click("#daysSave")
    page.wait_for_function("() => document.querySelectorAll('#daysList .days-item').length === 2")
    page.hover('#daysList .days-item >> nth=0')
    page.locator('#daysList .days-item >> nth=0').locator('[data-day-act="edit"]').click()
    check("days: edit loads the item into the form", page.input_value("#daysName") != "" and page.inner_text("#daysSave") == "保存修改")
    page.keyboard.press("Escape")
    check("days: Escape closes the form without closing anything else", not page.is_visible("#daysForm"))
    saved = api("/api/deck")["deck"]["days"]
    check("days: persisted on the server", sorted(d["name"] for d in saved) == sorted(["妈妈生日 <b>", "域名到期"]), saved)

    # 便签：输入 → 自动保存 → 刷新还在
    page.click("#memoText")
    page.keyboard.type("续费提醒\n第二行")
    page.wait_for_function("() => document.getElementById('deckMemoMeta').textContent.startsWith('已保存')", timeout=5000)
    check("memo: autosaved after typing stops", api("/api/deck")["deck"]["memo"]["text"] == "续费提醒\n第二行")
    # 在便签里打字不该被「直接打字就搜索」抢走焦点
    check("memo: typing stays in the memo (home type-to-search does not steal it)", page.evaluate("() => document.activeElement.id") == "memoText")

    # 卡片菜单：后移 / 移除；收起
    page.locator('#deckCalendar [data-deck-menu]').click()
    check("card menu: opens with 'move up' disabled on the first card", page.is_visible("#deckMenu") and page.locator('#deckMenu [data-deck-act="up"]').is_disabled())
    page.locator('#deckMenu [data-deck-act="down"]').click()
    check("card menu: move down", visible_cards(page)[:2] == ["days", "calendar"], visible_cards(page))
    page.locator('#deckCalendar [data-deck-fold]').click()
    folded = page.evaluate("() => ({ body: document.getElementById('deckCalendarBody').offsetParent === null, aria: document.querySelector('#deckCalendar [data-deck-fold]').getAttribute('aria-expanded') })")
    check("fold: collapses one card only and reports aria-expanded=false", folded == {"body": True, "aria": "false"} and page.is_visible("#todoBody"), folded)
    page.locator('#deckCalendar [data-deck-fold]').click()
    page.locator('#deckMemo [data-deck-menu]').click()
    page.locator('#deckMenu [data-deck-act="remove"]').click()
    check("card menu: remove takes the card off the page", "memo" not in visible_cards(page), visible_cards(page))
    page.reload(wait_until="networkidle")
    page.wait_for_function("() => STATE_LOADED")
    check("persistence: layout survives a reload", visible_cards(page) == ["days", "calendar", "todo"], visible_cards(page))
    # 搜索时不重画月历 / 不丢便签
    page.fill("#homeSearch", "git")
    check("search: typing in the home search leaves the deck alone", visible_cards(page) == ["days", "calendar", "todo"])
    page.fill("#homeSearch", "")
    page.screenshot(path=SHOTS + "/v-desk-final.png", full_page=False)
    check("desktop: no console errors", page.errors == [], page.errors)
    ctx.close()

    # 浅色主题 + 关壁纸：看一眼对比
    ctx, page = new_page(b, {"width": 1440, "height": 900}, cookies={"bh_wallpaper": "off", "bh_home_deck": "calendar.days.expiry.checkin"}, theme="light")
    page.screenshot(path=SHOTS + "/v-desk-light.png", full_page=False)
    check("light theme: renders without errors", page.errors == [], page.errors)
    ctx.close()

    # 完整侧栏 + 1200px：还不到 1280 的门槛，应当是标签条
    ctx, page = new_page(b, {"width": 1200, "height": 850}, cookies={"bh_home_nav": "full"})
    check("full sidebar @1200px: falls back to the tab strip", page.evaluate("() => getComputedStyle(document.getElementById('deckTabs')).display") != "none" and visible_cards(page) == [])
    check("full sidebar @1200px: script agrees it is not the side layout", page.evaluate("() => deckSide()") is False)
    ctx.close()
    ctx, page = new_page(b, {"width": 1200, "height": 850}, cookies={"bh_home_nav": "rail"})
    check("rail @1200px: side column", page.evaluate("() => deckSide()") is True and len(visible_cards(page)) >= 2)
    ctx.close()

    admin.close()


def phone(b):
    reset()
    ctx, page = new_page(b, {"width": 390, "height": 844}, mobile=True, cookies={"bh_home_deck": "calendar.todo.days.memo.expiry.checkin"})
    check("phone: only the tab strip shows by default", visible_cards(page) == [] and page.locator("#deckTabs .deck-tab").count() == 7)
    strip = page.evaluate("() => { const r = document.getElementById('deckTabs').getBoundingClientRect(); return { h: r.height, top: r.top }; }")
    check("phone: the strip costs one row of height", strip["h"] <= 52, strip)
    check("phone: no horizontal overflow", page.evaluate("() => document.documentElement.scrollWidth") <= 390)
    first = page.evaluate("() => document.querySelector('#deckTabs .deck-tab').getBoundingClientRect().left")
    check("phone: overflowing strip still starts at the first tab", first >= 0, first)
    page.locator('[data-deck-tab="calendar"]').tap()
    check("phone: tapping a tab opens that card only", visible_cards(page) == ["calendar"])
    size = page.evaluate("() => { const c = document.querySelector('#calGrid .cal-day').getBoundingClientRect(); return [c.width, c.height]; }")
    check("phone: calendar cells are at least 44px tall", size[1] >= 44 and size[0] >= 40, size)
    page.screenshot(path=SHOTS + "/v-phone-calendar.png")
    page.locator('[data-deck-tab="expiry"]').tap()
    check("phone: opening another tab swaps the card", visible_cards(page) == ["expiry"])
    page.screenshot(path=SHOTS + "/v-phone-expiry.png")
    page.locator('#deckExpiry [data-deck-fold]').tap()
    check("phone: the card's fold button closes it", visible_cards(page) == [])
    page.locator('[data-deck-tab="days"]').tap()
    page.locator("#daysAdd").tap()
    fs = page.evaluate("() => parseFloat(getComputedStyle(document.getElementById('daysName')).fontSize)")
    check("phone: inputs are 16px (no iOS zoom)", fs >= 16, fs)
    page.screenshot(path=SHOTS + "/v-phone-days-form.png")
    page.locator("#daysCancel").tap()
    page.locator("[data-deck-manage]").tap()
    check("phone: manager opens from the strip", page.is_visible("#deckModal.show") or page.evaluate("() => document.getElementById('deckModal').classList.contains('show')"))
    box = page.evaluate("() => { const m = document.querySelector('#deckModal .modal').getBoundingClientRect(); return [m.left, m.right]; }")
    check("phone: modal fits the screen", box[0] >= 0 and box[1] <= 390, box)
    page.screenshot(path=SHOTS + "/v-phone-manager.png")
    page.locator('[data-deck-pick="checkin"] .switch').tap()
    check("phone: switching a kind off removes its tab", page.locator('[data-deck-tab="checkin"]').count() == 0)
    check("phone: no console errors", page.errors == [], page.errors)
    ctx.close()


def visitor(b):
    reset()
    ctx, page = new_page(b, {"width": 1440, "height": 900}, cookies={"bh_home_deck": "calendar.todo.days.memo.checkin"}, unlocked=False)
    check("visitor: calendar works without unlocking", "节" in page.inner_text("#calNext") or "今天" in page.inner_text("#calNext"))
    texts = [page.inner_text(sel) for sel in ("#daysState", "#memoState", "#checkinDeckState", "#todoState")]
    check("visitor: private cards show an unlock prompt instead of data", all("管理员可见" in t for t in texts), texts)
    check("visitor: no memo box, no add button", not page.is_visible("#memoText") and not page.is_visible("#daysAdd"))
    page.screenshot(path=SHOTS + "/v-desk-visitor.png")
    check("visitor: no console errors", page.errors == [], page.errors)
    ctx.close()


if __name__ == "__main__":
    main((desktop, phone, visitor))

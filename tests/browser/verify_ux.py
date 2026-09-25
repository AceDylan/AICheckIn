# -*- coding: utf-8 -*-
"""0925 体验打磨的浏览器验证：签到中心分段标签、命令面板里的页面 / 命令、弹窗焦点、首页小组件与折叠按钮。"""
import json
import os
import sys
from verify import *   # noqa: F401,F403

VIEW = "() => ({ hash: location.hash, view: document.querySelector('.view.active').id, cur: [...document.querySelectorAll('.checkin-tool[aria-current=\"page\"]')].map(b => b.dataset.checkinPage) })"


def seed_configs(ctx):
    for i, (name, on) in enumerate((("公益站 Alpha", True), ("中转站 Beta", True), ("旧站 Delta", False))):
        api(ctx, "POST", "/api/configs", {"name": name, "base_url": "https://s%d.example.com" % i, "user_id": str(1000 + i),
                                          "access_token": "placeholder-%d" % i, "enabled": on})


def checkin_tabs(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light"})
    seed_configs(ctx)
    page.goto(BASE + "/#checkin"); page.reload(); page.wait_for_selector("#cards .card"); page.wait_for_timeout(300)
    v = page.evaluate(VIEW)
    check("签到中心：概览标签标为当前页", v["cur"] == ["overview"], v)
    check("概览：页头右侧只有「运行全部签到」", page.locator("#runAllCheckin").is_visible() and not page.locator("#addCfg").is_visible())
    check("没有「← 返回签到概览」这类二级返回链接", page.locator("text=返回签到概览").count() == 0)
    check("账户不多时默认卡片", page.locator("#cards .card").count() == 2 and page.locator('#ckView [data-ck-view="cards"]').get_attribute("aria-checked") == "true")
    page.locator('#ckView [data-ck-view="list"]').click(); page.wait_for_timeout(200)
    check("切到列表：一行一个账户，选择记进 Cookie", page.locator("#cards .ck-row").count() == 2 and any(c["name"] == "bh_ck_view" and c["value"] == "list" for c in ctx.cookies()))
    shot(page, "U0-checkin-list")
    page.locator('.checkin-tool[data-checkin-page="configs"]').click(); page.wait_for_timeout(300)
    v = page.evaluate(VIEW)
    check("点「服务配置」：当前页标记跟过去、地址栏跟着变", v["cur"] == ["configs"] and v["hash"][1:] == "checkin/configs", v)
    check("服务配置：操作换成导出 / 导入 / 添加账户", page.locator("#addCfg").is_visible() and page.locator("#exportCfg").is_visible() and not page.locator("#runAllCheckin").is_visible())
    check("服务配置标签上带账户数", page.locator("#cfgTabCount").text_content() == "3")
    off = page.locator(".cfg-row.disabled")
    check("停用的账户：名字旁写「已停用」，操作按钮不跟着变淡",
          off.locator(".badge").text_content() == "已停用" and page.evaluate("() => getComputedStyle(document.querySelector('.cfg-row.disabled .cfg-actions')).opacity") == "1")
    check("启用开关有可读的名称", off.locator('input[type="checkbox"]').get_attribute("aria-label") == "启用「旧站 Delta」")
    page.locator('.checkin-tool[data-checkin-page="history"]').click(); page.wait_for_timeout(400)
    check("运行记录：空的时候说清楚怎么产生记录", "运行全部签到" in page.locator("#histEmpty").text_content() and page.locator("#refreshHist").is_visible())
    shot(page, "U1-checkin-history")
    page.locator('.checkin-tool[data-checkin-page="overview"]').click(); page.wait_for_timeout(300)
    page.locator("[data-ov-sched]").click(); page.wait_for_timeout(500)
    check("状态行的「每日定时」直达系统设置里的开关", page.evaluate("() => document.querySelector('.view.active').id") == "view-settings"
          and page.evaluate("() => document.activeElement && document.activeElement.id") == "schedEnabled")
    ctx.close()
    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_theme": "light"})
    page.goto(BASE + "/#checkin/configs"); page.reload(); page.wait_for_selector("#cfgList .cfg-row"); page.wait_for_timeout(300)
    tops = page.evaluate("() => [...document.querySelectorAll('.checkin-tool')].map(b => Math.round(b.getBoundingClientRect().top))")
    fits = page.evaluate("() => { const n = document.querySelector('.checkin-tabs'); return n.scrollWidth <= n.clientWidth + 1 && n.getBoundingClientRect().right <= innerWidth; }")
    check("手机：三个标签排成一行、不溢出", len(set(tops)) == 1 and fits, (tops, fits))
    shot(page, "U2-checkin-configs-m")
    ctx.close()


def palette(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light"})
    check("浅色主题：浏览器顶栏颜色（theme-color）也是浅色", page.evaluate("() => [...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content.slice(1))") == ["f5f2ee", "f5f2ee"])
    page.keyboard.press("Control+k"); page.wait_for_timeout(250)
    check("空查询不列命令（仍是置顶与最近添加）", page.locator(".omni-row .omni-kind", has_text="命令").count() == 0)
    page.keyboard.type("运行记录"); page.wait_for_timeout(250)
    check("输入页面名：命令排第一", page.locator(".omni-row").first.locator(".omni-title").text_content() == "运行记录")
    page.keyboard.press("Enter"); page.wait_for_timeout(500)
    v = page.evaluate(VIEW)
    check("回车执行：面板关掉、到了运行记录", v["hash"][1:] == "checkin/history" and not page.locator("#omniModal.show").count(), v)
    page.keyboard.press("Control+k"); page.wait_for_timeout(250)
    page.keyboard.type("代理"); page.wait_for_timeout(250); page.keyboard.press("Enter"); page.wait_for_timeout(500)
    st = page.evaluate("() => [document.querySelector('.view.active').id, document.activeElement && document.activeElement.id, Math.round(document.getElementById('proxyUrl').getBoundingClientRect().top)]")
    check("搜「代理」直达系统设置的那一节：聚焦输入框、在首屏", st[0] == "view-settings" and st[1] == "proxyUrl" and 0 < st[2] < 600, st)
    page.keyboard.press("Control+k"); page.wait_for_timeout(250)
    page.keyboard.type("深色"); page.wait_for_timeout(250); page.keyboard.press("Enter"); page.wait_for_timeout(300)
    check("切到深色后，浏览器顶栏颜色（theme-color）也跟着变深", page.evaluate("() => [...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content.slice(1))") == ["0e0c09", "0e0c09"])
    check("「切换到深色」立即生效并记进 Cookie", page.evaluate("() => document.documentElement.dataset.theme") == "dark"
          and any(c["name"] == "bh_theme" and c["value"] == "dark" for c in ctx.cookies()))
    ctx.close()
    ctx, page = open_page(b, 1440, 900, unlocked=False)
    page.keyboard.press("Control+k"); page.wait_for_timeout(250)
    page.keyboard.type("签到"); page.wait_for_timeout(250)
    check("未解锁：不列签到相关命令", page.locator(".omni-row .omni-kind", has_text="命令").count() == 0)
    ctx.close()


def modal_focus(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light"})
    gid = page.evaluate("() => STATE.link_groups[0].id")
    page.goto(BASE + "/#links/" + gid); page.wait_for_timeout(500)
    page.locator("#addLinkBtn").focus(); page.keyboard.press("Enter"); page.wait_for_timeout(400)
    check("添加网址：描述 / 标签 / 图标默认收起", page.locator("#linkModal.show").count() == 1 and page.evaluate("() => document.getElementById('lk_more').open") is False)
    page.locator("#linkSave").focus(); page.keyboard.press("Tab"); page.wait_for_timeout(100)
    inside = page.evaluate("() => document.getElementById('linkModal').contains(document.activeElement)")
    check("Tab 到最后一个再按：焦点回到弹窗第一个控件，不跑到遮罩后面", inside, page.evaluate("() => document.activeElement.outerHTML.slice(0, 80)"))
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    check("Esc 关弹窗：焦点回到「添加网址」按钮", page.evaluate("() => document.activeElement && document.activeElement.id") == "addLinkBtn")
    lid = page.evaluate("() => STATE.link_groups[0].links[0].id")
    api(ctx, "PUT", "/api/link_groups/%s/links/%s" % (gid, lid), {"desc": "一句说明"})
    page.reload(); page.wait_for_timeout(700)
    page.evaluate("([g, l]) => editLink(g, l)", [gid, lid]); page.wait_for_timeout(300)
    check("编辑有描述的网址：可选项自动展开", page.evaluate("() => document.getElementById('lk_more').open") is True)
    ctx.close()


DRAG = """([type, html]) => { const dt = new DataTransfer(); dt.setData('text/uri-list', 'https://drop.example.com/docs');
  if (html) dt.setData('text/html', html);
  const fire = (target, name) => target.dispatchEvent(new DragEvent(name, { bubbles: true, cancelable: true, dataTransfer: dt }));
  fire(document.body, 'dragenter');
  const hint = document.getElementById('dropHint'), shown = !hint.hidden, to = document.getElementById('dropHintTo').textContent;
  if (type === 'drop') fire(hint, 'drop'); else fire(hint, 'dragleave');
  return { shown, to, hidden: hint.hidden }; }"""


def drop_link(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light"})
    r = page.evaluate(DRAG, ["leave", ""])
    check("把链接拖进页面：出现「松手收藏」提示并写明加到哪个分组", r["shown"] and r["to"].startswith("添加到「") and r["hidden"], r)
    r = page.evaluate(DRAG, ["drop", '<a href="https://drop.example.com/docs">Drop 文档</a>'])
    page.wait_for_timeout(300)
    vals = page.evaluate("() => [document.getElementById('lk_url').value, document.getElementById('lk_name').value, !!document.querySelector('#linkModal.show')]")
    check("松手：打开添加网址，网址和链接文字都预填好（不直接保存）", vals == ["https://drop.example.com/docs", "Drop 文档", True], vals)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    internal = page.evaluate("""() => { document.dispatchEvent(new DragEvent('dragstart', { bubbles: true }));
      const dt = new DataTransfer(); dt.setData('text/uri-list', 'https://x.example');
      document.body.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: dt }));
      const shown = !document.getElementById('dropHint').hidden; document.dispatchEvent(new DragEvent('dragend', { bubbles: true })); return shown; }""")
    check("页面自己的拖动（图标、网址行）不出这个提示", internal is False)
    ctx.close()
    ctx, page = open_page(b, 1440, 900, unlocked=False)
    r = page.evaluate(DRAG, ["leave", ""])
    check("未解锁：拖链接进来不出提示", not r["shown"], r)
    ctx.close()


def settings_and_toast(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light"})
    page.goto(BASE + "/#settings"); page.reload(); page.wait_for_selector("#diagList .diag-head"); page.wait_for_timeout(300)
    st = page.evaluate("""() => { const box = document.getElementById('diagList'), more = document.getElementById('diagMore');
      return { shown: [...box.querySelectorAll(':scope > .diag-row')].map(r => r.className), okInside: more ? more.querySelectorAll('.diag-row.is-ok').length : 0, open: more ? more.open : null,
               summary: more ? more.querySelector('summary').textContent : '' }; }""")
    check("部署自检：只直接列出要关注的项，正常项收进「其余 N 项正常」", all("is-ok" not in c for c in st["shown"]) and st["okInside"] > 3 and st["open"] is False
          and st["summary"].endswith("%d 项正常" % st["okInside"]), st)
    page.locator("#diagMore summary").click(); page.locator("#runDiag").click(); page.wait_for_timeout(800)
    check("展开过「其余 N 项正常」后重新检查：保持展开", page.evaluate("() => document.getElementById('diagMore').open") is True)
    page.evaluate("() => toast('出错了：这是一条比较长的错误提示，读完需要一点时间', 'err')")
    page.wait_for_timeout(3600)
    check("出错提示多停一会儿（3.6 秒后还在）", page.locator(".toast.err").count() == 1)
    ctx.close()


def locked_settings(b):
    reset()
    for w, h, mobile in ((1440, 900, False), (390, 844, True)):
        ctx, page = open_page(b, w, h, mobile=mobile, unlocked=False)
        page.goto(BASE + "/#settings"); page.reload(); page.wait_for_selector("#adminPanel"); page.wait_for_timeout(400)
        st = page.evaluate("""() => { const tops = [...document.querySelectorAll('#settingsGrid > .panel')].filter(p => p.offsetParent)
            .map(p => [p.id, Math.round(p.getBoundingClientRect().top)]).sort((a, b) => a[1] - b[1]);
            const pw = document.querySelector('#adminPanel input[type=password]'); return { first: tops[0][0], pwBottom: pw ? Math.round(pw.getBoundingClientRect().bottom) : -1, h: innerHeight }; }""")
        check("未解锁（%s）：管理密码一节排在设置页最前、密码框在首屏" % ("手机" if mobile else "电脑"), st["first"] == "adminPanel" and 0 < st["pwBottom"] < st["h"] - 80, st)
        ctx.close()
    ctx, page = open_page(b, 1440, 900)
    page.goto(BASE + "/#settings"); page.reload(); page.wait_for_timeout(600)
    first = page.evaluate("() => [...document.querySelectorAll('#settingsGrid > .panel')].filter(p => p.offsetParent).sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0].id")
    check("已解锁：设置页照旧从定时签到开始", first != "adminPanel", first)
    ctx.close()


def move_menu(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light"})
    gid = page.evaluate("() => STATE.link_groups[0].id")
    page.goto(BASE + "/#links/" + gid); page.wait_for_timeout(500)
    page.locator("#linkList .action-menu > summary").first.click(); page.wait_for_timeout(250)
    st = page.evaluate("""() => { const pop = document.querySelector('#linkList details.action-menu[open] .menu-popover');
        return { flat: [...pop.querySelectorAll(':scope > button')].filter(b => b.textContent.startsWith('移到')).length,
                 sub: !!pop.querySelector('details.menu-sub'), groups: STATE.link_groups.length }; }""")
    check("分组多时「移到」收成一项「移到分组」，不再平铺", st["groups"] > 4 and st["flat"] == 0 and st["sub"], st)
    page.locator("#linkList details.action-menu[open] .menu-sub > summary").click(); page.wait_for_timeout(200)
    target = page.evaluate("() => STATE.link_groups[1].name")
    orig = page.evaluate("() => STATE.link_groups[0].links.map(l => l.id)")
    shot(page, "U3-move-submenu")
    page.locator("#linkList details.action-menu[open] .menu-sub-list button", has_text=target).first.click(); page.wait_for_timeout(600)
    check("点开后选分组即移动", page.locator(".toast", has_text="已移到「%s」" % target).count() == 1)
    page.locator(".toast.has-action .toast-action").first.click(); page.wait_for_timeout(900)
    after = page.evaluate("() => [STATE.link_groups[0].links.map(l => l.id), STATE.link_groups[1].links.map(l => l.id)]")
    check("移动后点「撤销」：回到原分组、原来的位置", after[0] == orig and orig[0] not in after[1], (orig[:3], after[0][:3]))
    ctx.close()


def history_list(b):
    reset()
    res = lambda name, st, label, color, msg="", q="-": {"name": name, "status": st, "status_label": label, "color": color, "message": msg, "quota_awarded": q}
    hist = [{"time": "2026-09-24 08:30:02", "trigger": "scheduled", "summary": {"signed": 2, "skipped": 0, "failed": 1, "quota_total": "0.50"},
             "results": [res("Alpha", "signed", "签到成功", "green", "签到成功", "0.50"), res("Beta", "failed", "失败", "red", "HTTP 401"), res("Gamma", "signed", "签到成功", "green", "", "0.20")]},
            {"time": "2026-09-22 09:10:44", "trigger": "retry", "summary": {"signed": 1, "skipped": 2, "failed": 0, "quota_total": "0.20"}, "results": []}]
    with open(os.path.join(DATA, "history.json"), "w", encoding="utf-8") as fh:
        json.dump(hist, fh, ensure_ascii=False)
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light"})
    page.goto(BASE + "/#checkin/history"); page.reload(); page.wait_for_selector("#histList .hist-item"); page.wait_for_timeout(300)
    st = page.evaluate("""() => ({ pills: [...document.querySelectorAll('#histList .hist-stats .pill')].map(p => p.textContent),
        names: [...document.querySelectorAll('#histList .hist-row-name')].map(n => Math.round(n.getBoundingClientRect().left)),
        quotas: [...document.querySelectorAll('#histList .hist-row-quota')].map(n => Math.round(n.getBoundingClientRect().right)),
        notes: [...document.querySelectorAll('#histList .hist-row-note')].map(n => n.textContent),
        trig: (() => { const h = document.querySelectorAll('#histList .hist-item')[1].querySelector('.hist-head'); return Math.round(h.querySelector('.hist-trigger').getBoundingClientRect().left - h.querySelector('.hist-time').getBoundingClientRect().right); })() })""")
    check("运行记录：为 0 的「跳过 / 失败」不挂胶囊", st["pills"] == ["成功 2", "失败 1", "成功 1", "跳过 2"], st["pills"])
    check("运行记录：各行账户名对齐、额度都靠右", len(set(st["names"])) == 1 and len(set(st["quotas"])) == 1, st)
    check("运行记录：和状态一样的说明不重复、触发方式紧跟时间", st["notes"] == ["HTTP 401"] and 0 <= st["trig"] <= 16, st)
    ctx.close()


def mobile_group_header(b):
    reset()
    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_theme": "light"})
    gid = page.evaluate("() => STATE.link_groups.find(g => !g.desc).id")
    page.goto(BASE + "/#links/" + gid); page.reload(); page.wait_for_selector("#linkList .link-card"); page.wait_for_timeout(400)
    st = page.evaluate("""() => ({ visible: [...document.querySelectorAll('#libActionsGroup > .btn, #libMore')].filter(e => e.offsetParent).map(e => e.id),
        desc: !!document.getElementById('libDesc').offsetParent, top: Math.round(document.querySelector('#linkList .link-card').getBoundingClientRect().top) })""")
    check("手机分组页：页头只剩「添加网址」和「···」，占位描述不显示", st["visible"] == ["addLinkBtn", "libMore"] and not st["desc"], st)
    check("手机分组页：第一个网址在首屏上半部分", st["top"] < 330, st["top"])
    page.locator("#libMore > summary").tap(); page.wait_for_timeout(300)
    pop = page.evaluate("() => { const p = document.querySelector('#libMore .menu-popover'), s = document.querySelector('#libMore > summary'); return [Math.round(p.getBoundingClientRect().top), Math.round(s.getBoundingClientRect().bottom)]; }")
    check("「···」菜单向下展开，不盖住顶上的分组标签条", pop[0] >= pop[1], pop)
    page.locator('#libMore [data-lib-more="editGroupBtn"]').tap(); page.wait_for_timeout(400)
    check("菜单里的「编辑分组」照常打开分组弹窗", page.locator("#groupModal.show").count() == 1)
    ctx.close()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light"})
    page.goto(BASE + "/#links/" + gid); page.reload(); page.wait_for_timeout(500)
    check("电脑上照旧三颗按钮、没有「···」、描述在", page.locator("#checkLinksBtn").is_visible() and page.locator("#editGroupBtn").is_visible()
          and not page.locator("#libMore").is_visible() and page.locator("#libDesc").is_visible())
    ctx.close()


def settings_toc(b):
    reset()
    TOC = "() => [...document.querySelectorAll('#settingsToc [data-toc]')].map(a => [a.textContent, a.getAttribute('aria-current')])"
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light", "bh_wallpaper": "off"})
    page.goto(BASE + "/#settings"); page.reload(); page.wait_for_selector("#settingsToc [data-toc]"); page.wait_for_timeout(400)
    toc = page.evaluate(TOC)
    box = page.locator("#settingsToc").bounding_box()
    grid = page.locator("#settingsGrid").bounding_box()
    check("电脑：设置页右侧有「本页」导航，列出各节、第一节是当前", len(toc) >= 8 and toc[0][1] == "true" and box["x"] > grid["x"] + grid["width"], (toc[:2], box, grid))
    page.locator('#settingsToc [data-toc]', has_text="配置恢复").click(); page.wait_for_timeout(1300)
    st = page.evaluate("() => { const a = document.querySelector('#settingsToc [aria-current]'), p = document.getElementById(a.dataset.toc); return [a.textContent, Math.round(p.getBoundingClientRect().top), Math.round(document.getElementById('settingsToc').getBoundingClientRect().top)]; }")
    check("点「配置恢复」：滚到那一节、高亮它、导航随页面吸顶", st[0] == "配置恢复" and 0 <= st[1] < 80 and 0 <= st[2] < 60, st)
    check("点导航不改地址栏里设置页的路由", page.evaluate("() => location.hash.slice(1)") == "settings")
    ctx.close()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_wallpaper": "aurora"})
    page.goto(BASE + "/#settings"); page.reload(); page.wait_for_timeout(600)
    check("开壁纸（双列玻璃卡片）时电脑上不显示导航", not page.locator("#settingsToc").is_visible())
    ctx.close()
    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_theme": "light"})
    page.goto(BASE + "/#settings"); page.reload(); page.wait_for_selector("#settingsToc [data-toc]"); page.wait_for_timeout(400)
    st = page.evaluate("() => { const n = document.getElementById('settingsToc'), r = n.getBoundingClientRect(); return [getComputedStyle(n).display, Math.round(r.top), document.documentElement.scrollWidth <= innerWidth]; }")
    check("手机：页头下一行可横滑的跳转标签，页面不横向溢出", st[0] == "flex" and st[1] < 260 and st[2], st)
    ctx.close()
    ctx, page = open_page(b, 1440, 900, unlocked=False, cookies={"bh_wallpaper": "off"})
    page.goto(BASE + "/#settings"); page.reload(); page.wait_for_selector("#settingsToc [data-toc]"); page.wait_for_timeout(400)
    check("未解锁：导航第一项也是「管理密码」", page.evaluate(TOC)[0][0] == "管理密码")
    ctx.close()


def home_toolbar(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light", "bh_wallpaper": "off"})
    page.locator("#homeEditBtn").click(); page.wait_for_timeout(300)
    st = page.evaluate("() => { const b = document.getElementById('homeEditBtn'), cs = getComputedStyle(b); return [b.getAttribute('aria-pressed'), cs.backgroundColor, cs.color, getComputedStyle(b.closest('.home-toolbar')).opacity]; }")
    check("编辑中：「编辑」按钮是墨色实心、工具条不再半透明（不像被禁用）", st[0] == "true" and st[1] != "rgba(0, 0, 0, 0)" and st[1] != st[2] and st[3] == "1", st)
    ctx.close()
    ctx, page = open_page(b, 390, 844, mobile=True, cookies={"bh_theme": "light"})
    page.locator("#homeToolsToggle").click(); page.wait_for_timeout(300)
    tops = page.evaluate("() => [...document.querySelectorAll('#homeTools > *')].map(el => Math.round(el.getBoundingClientRect().top))")
    check("手机：「⋯」展开的工具条排成一行", max(tops) - min(tops) <= 6, tops)
    ctx.close()


def home_widgets(b):
    reset()
    ctx, page = open_page(b, 1440, 900, cookies={"bh_theme": "light", "bh_wallpaper": "off"})
    clipped = page.evaluate("() => [...document.querySelectorAll('.widget-value')].filter(v => v.scrollWidth > v.clientWidth + 1).map(v => v.textContent)")
    check("看板小组件的主数值都放得下（不再截成「已过期 16…」）", not clipped, clipped)
    gaps = page.evaluate("""() => [...document.querySelectorAll('.home-section .section-fold')].map(f => {
        const c = f.parentElement.querySelector('.count'); return Math.round(f.getBoundingClientRect().left - c.getBoundingClientRect().right); })""")
    check("折叠按钮紧跟在分组计数后面（各组不再参差）", gaps and all(0 <= g <= 16 for g in gaps), gaps)
    ctx.close()


if __name__ == "__main__":
    main((checkin_tabs, palette, modal_focus, drop_link, settings_and_toast, locked_settings, move_menu, history_list, mobile_group_header, settings_toc, home_toolbar, home_widgets))

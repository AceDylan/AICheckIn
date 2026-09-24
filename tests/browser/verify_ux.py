# -*- coding: utf-8 -*-
"""0925 体验打磨的浏览器验证：签到中心分段标签、命令面板里的页面 / 命令、弹窗焦点、首页小组件与折叠按钮。"""
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
    page.locator('.checkin-tool[data-checkin-page="configs"]').click(); page.wait_for_timeout(300)
    v = page.evaluate(VIEW)
    check("点「服务配置」：当前页标记跟过去、地址栏跟着变", v["cur"] == ["configs"] and v["hash"][1:] == "checkin/configs", v)
    check("服务配置：操作换成导出 / 导入 / 新增账户", page.locator("#addCfg").is_visible() and page.locator("#exportCfg").is_visible() and not page.locator("#runAllCheckin").is_visible())
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
    page.keyboard.press("Control+k"); page.wait_for_timeout(250)
    check("空查询不列命令（仍是置顶与最近添加）", page.locator(".omni-row .omni-kind", has_text="命令").count() == 0)
    page.keyboard.type("运行记录"); page.wait_for_timeout(250)
    check("输入页面名：命令排第一", page.locator(".omni-row").first.locator(".omni-title").text_content() == "运行记录")
    page.keyboard.press("Enter"); page.wait_for_timeout(500)
    v = page.evaluate(VIEW)
    check("回车执行：面板关掉、到了运行记录", v["hash"][1:] == "checkin/history" and not page.locator("#omniModal.show").count(), v)
    page.keyboard.press("Control+k"); page.wait_for_timeout(250)
    page.keyboard.type("深色"); page.wait_for_timeout(250); page.keyboard.press("Enter"); page.wait_for_timeout(300)
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
    main((checkin_tabs, palette, modal_focus, drop_link, settings_and_toast, home_widgets))

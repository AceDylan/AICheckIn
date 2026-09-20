# -*- coding: utf-8 -*-
"""传输层 + 回到页面时静默刷新：gzip / ETag 与 304 / 壁纸新鲜期 / 离开后回来自动对数据 / 首次加载失败自动重试，PC 与手机各一遍。"""
import json
import time

from verify import *   # noqa: F401,F403  复用 check / main / new_context / api


def transport(browser):
    for label, (w, h, mobile) in {"pc": (1440, 900, False), "phone": (390, 844, True)}.items():
        ctx = new_context(browser, w, h, mobile=mobile)
        page = ctx.new_page()
        errors, seen = [], {}
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        def on_resp(r):
            path = r.url.replace(BASE, "").split("?")[0]
            seen.setdefault(path, []).append((r.status, r.headers.get("content-encoding", ""), r.headers.get("etag", ""), r.headers.get("cache-control", "")))
        page.on("response", on_resp)
        page.goto(BASE + "/")
        page.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED")
        page.wait_for_timeout(400)
        check(label + ": 首页 gzip", seen["/"][0][1] == "gzip", seen["/"][0])
        check(label + ": 首页带弱 ETag + no-cache", seen["/"][0][2].startswith('W/"') and seen["/"][0][3] == "no-cache", seen["/"][0])
        check(label + ": CSS gzip", seen["/static/app-v3.css"][0][1] == "gzip", seen["/static/app-v3.css"][0])
        check(label + ": /api/configs gzip + no-store", seen["/api/configs"][0][1] == "gzip" and seen["/api/configs"][0][3] == "no-store", seen["/api/configs"][0])
        wp = [k for k in seen if k.startswith("/static/wallpapers/")]
        check(label + ": 壁纸一天新鲜期", wp and all("max-age=86400" in seen[k][0][3] for k in wp), {k: seen[k][0] for k in wp})
        tiles = page.locator("#homeList .home-tile").count()
        check(label + ": 首页图标渲染", tiles > 5, tiles)
        check(label + ": 控制台无报错", not errors, errors[:3])
        transfer = page.evaluate("() => performance.getEntriesByType('navigation')[0].transferSize")
        decoded = page.evaluate("() => performance.getEntriesByType('navigation')[0].decodedBodySize")
        check(label + ": 首页传输量 < 解码后 40%", 0 < transfer < decoded * 0.4, "%d / %d" % (transfer, decoded))

        # 刷新：外壳应当是 304
        seen.clear()
        page.reload()
        page.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED")
        transfer2 = page.evaluate("() => performance.getEntriesByType('navigation')[0].transferSize")
        check(label + ": 刷新时外壳走 304（传输 < 2KB）", 0 <= transfer2 < 2048, transfer2)
        check(label + ": 刷新后仍正常渲染", page.locator("#homeList .home-tile").count() == tiles)

        # 回到页面时静默刷新：另一台「设备」加一条待办
        other = new_context(browser, 800, 600)
        text = "来自另一台设备 " + label + " " + str(int(time.time()))
        st, data = api(other, "POST", "/api/todos", json.dumps({"text": text}))
        check(label + ": 另一设备添加待办", st == 200 and data.get("ok"), st)
        tid = next(t["id"] for t in data["todos"] if t["text"] == text)
        page.evaluate("""() => { window.__now = Date.now(); const real = Date.now; Date.now = () => real.call(Date) + (window.__skew || 0);
            Object.defineProperty(document, 'hidden', { configurable: true, get: () => !!window.__hidden }); }""")
        def away(ms):
            page.evaluate("(ms) => { window.__hidden = true; document.dispatchEvent(new Event('visibilitychange')); window.__skew = (window.__skew || 0) + ms; window.__hidden = false; document.dispatchEvent(new Event('visibilitychange')); }", ms)
            page.wait_for_timeout(500)
        seen.clear()
        away(5000)
        check(label + ": 离开 5 秒不重取", "/api/configs" not in seen, list(seen))
        check(label + ": 此时页面还看不到新待办", text not in page.evaluate("() => JSON.stringify(STATE.todos)"))
        # 有字的输入框 = 在忙
        page.evaluate("() => { const i = document.getElementById('homeSearch'); i.focus(); i.value = 'abc'; }")
        away(120000)
        check(label + ": 搜索框有字时不打扰", "/api/configs" not in seen, list(seen))
        page.evaluate("() => { const i = document.getElementById('homeSearch'); i.value = ''; }")
        away(120000)
        check(label + ": 离开 2 分钟回来自动重取", "/api/configs" in seen, list(seen))
        check(label + ": 新待办无需刷新即出现", text in page.evaluate("() => JSON.stringify(STATE.todos)"))
        check(label + ": 没有弹任何提示", page.locator(".toast").count() == 0)
        # 弹窗开着不打扰
        page.evaluate("() => openDeckModal()")
        seen.clear()
        away(120000)
        check(label + ": 弹窗开着不重取", "/api/configs" not in seen, list(seen))
        page.evaluate("() => document.getElementById('deckModalClose').click()")
        # 断网回来：静默
        ctx.route("**/api/configs", lambda route: route.abort())
        away(120000)
        check(label + ": 断网回来不弹错误", page.locator(".toast").count() == 0, page.locator(".toast").all_inner_texts())
        check(label + ": 断网回来内容还在", page.locator("#homeList .home-tile").count() == tiles)
        ctx.unroute("**/api/configs")
        # 第一次加载就断网：提示一次，随后静默重试，网络恢复后自动补上
        page2 = ctx.new_page()
        ctx.route("**/api/configs", lambda route: route.abort())
        page2.goto(BASE + "/")
        page2.wait_for_timeout(600)
        check(label + ": 首次加载失败时页面为空且提示一次", page2.locator("#homeList .home-tile").count() == 0 and page2.locator(".toast").count() == 1, page2.locator(".toast").all_inner_texts())
        ctx.unroute("**/api/configs")
        page2.wait_for_function("() => STATE_LOADED", timeout=8000)
        page2.wait_for_timeout(300)
        check(label + ": 静默重试后自动补上内容", page2.locator("#homeList .home-tile").count() == tiles, page2.locator("#homeList .home-tile").count())
        check(label + ": 重试成功没有再弹提示", page2.locator(".toast").count() <= 1)
        page2.screenshot(path=SHOTS + "/T-bootretry-%s.png" % label)
        page2.close()
        page.screenshot(path=SHOTS + "/T-transport-%s.png" % label)
        api(other, "DELETE", "/api/todos/" + tid)
        check(label + ": 全程控制台无报错", not [e for e in errors if "ERR_FAILED" not in e and "Failed to load resource" not in e], errors[:3])
        other.close(); ctx.close()


if __name__ == "__main__":
    main((transport,))

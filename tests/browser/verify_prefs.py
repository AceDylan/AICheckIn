# -*- coding: utf-8 -*-
"""偏好跨浏览器同步：登录的浏览器 A 改了主题 / 搜索引擎 / 壁纸 / 首页组件，另一个登录的浏览器 B 打开或回到页面就照着变；
「此刻展开哪张组件」这类本机状态不同步；未登录访客改了只在自己那里，既不上传也不吃服务器上的偏好。

    python tests/browser/verify_prefs.py
"""
import time

from common import *   # noqa: F401,F403

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + ((" — " + str(detail)[:200]) if detail and not ok else ""), flush=True)


def bh_cookies(ctx):
    return {c["name"]: c["value"] for c in ctx.cookies() if c["name"].startswith("bh_")}


def loaded(page):
    page.goto(BASE + "/")
    page.wait_for_function("() => typeof STATE_LOADED !== 'undefined' && STATE_LOADED")
    page.wait_for_timeout(300)


def theme(page):
    return page.evaluate("() => document.documentElement.dataset.theme")


def main():
    t0 = time.time()
    start_server()
    with sync_playwright() as p:
        b = launch(p)
        ctx_a = new_context(b, 1440, 900, sync_prefs=True)
        ctx_b = new_context(b, 390, 844, mobile=True, sync_prefs=True)
        ctx_c = new_context(b, 1440, 900, unlocked=False, sync_prefs=True)
        errors, puts = [], []
        pa, pb, pc = (ctx.new_page() for ctx in (ctx_a, ctx_b, ctx_c))
        for tag, page in (("A", pa), ("B", pb), ("C", pc)):
            page.on("pageerror", lambda e, tag=tag: errors.append(tag + ": " + str(e)))
            page.on("request", lambda r, tag=tag: puts.append((tag, r.post_data)) if r.url.endswith("/api/deck/prefs") else None)

        loaded(pa)
        check("A：初始深色", theme(pa) == "dark", theme(pa))
        pa.click("#themeToggle")
        pa.evaluate("""() => { setEngine('bing'); writePref('bh_wallpaper', 'dusk'); applyLook();
            writeDeckList('bh_home_deck', ['calendar', 'weather', 'clocks']); renderDeck(); writePref('bh_home_deck_open', 'clocks'); }""")
        pa.wait_for_timeout(1200)
        prefs = api(ctx_a, "GET", "/api/deck")[1]["deck"]["prefs"]
        want = {"bh_theme": "light", "bh_engine": "bing", "bh_wallpaper": "dusk", "bh_home_deck": "calendar.weather.clocks"}
        check("A 的改动到了服务器（展开哪张不算）", prefs == want, prefs)
        check("A：连着几处改动攒成一个请求", len(puts) == 1, puts)

        loaded(pb)
        cookies = bh_cookies(ctx_b)
        check("B：主题跟着变浅", theme(pb) == "light", theme(pb))
        check("B：Cookie 被服务器的值覆盖", all(cookies.get(k) == v for k, v in want.items()), cookies)
        check("B：壁纸场景生效", pb.evaluate("() => document.documentElement.classList.contains('wall-on')"))
        check("B：搜索引擎是必应", pb.evaluate("() => currentEngine().id") == "bing")
        check("B：首页组件一样", pb.evaluate("() => deckOrder().join('.')") == "calendar.weather.clocks")
        check("B：展开哪张不同步", "bh_home_deck_open" not in cookies, cookies)

        # B 一直开着：A 改回深色，B 回到页面（静默对数据）时不用刷新就换过来。
        pa.click("#themeToggle")
        pa.wait_for_timeout(900)
        pb.evaluate("() => loadConfigs({ quiet: true })")
        pb.wait_for_timeout(300)
        check("B：回到页面后跟着变深", theme(pb) == "dark", theme(pb))

        loaded(pc)
        check("访客：不吃服务器上的偏好", pc.evaluate("() => currentEngine().id") == "google")
        pc.click("#themeToggle")
        pc.wait_for_timeout(900)
        check("访客：改主题只在本机生效", theme(pc) == "light" and not [x for x in puts if x[0] == "C"], puts)
        check("访客：服务器上的偏好没被改", api(ctx_a, "GET", "/api/deck")[1]["deck"]["prefs"].get("bh_theme") == "dark")
        check("没有页面错误", not errors, errors)
        b.close()
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS)} checks, {len(failed)} failed, {time.time() - t0:.0f}s")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

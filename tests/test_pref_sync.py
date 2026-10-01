# -*- coding: utf-8 -*-
"""偏好跨浏览器同步：登录后主题、壁纸、首页组件摆放等偏好存进 deck.json 的 prefs，
各浏览器拿到 /api/configs 时用服务器的值覆盖本机 Cookie；本机改了就上传。"""
import json
import re
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from tests.test_deck import DeckCase, PASSWORD
from tests.test_home_deck_ui import DeckScriptCase
from tests.test_script_boot import _inline_script
from app import SYNC_PREF_NAMES, _coerce_deck, read_deck  # noqa: E402


class PrefSyncApiTest(DeckCase):
    def put(self, prefs, **kw):
        return self.client.put("/api/deck/prefs", json={"prefs": prefs}, **kw)

    def test_put_merges_and_persists(self):
        self.assertEqual(self.put({"bh_theme": "light", "bh_home_deck": "calendar.weather"}).status_code, 200)
        resp = self.put({"bh_theme": "dark"})
        self.assertEqual(resp.get_json()["deck"]["prefs"], {"bh_theme": "dark", "bh_home_deck": "calendar.weather"})
        self.assertEqual(read_deck()["prefs"]["bh_home_deck"], "calendar.weather")
        self.assertEqual(self.client.get("/api/configs").get_json()["deck"]["prefs"]["bh_theme"], "dark")

    def test_put_validation(self):
        bad = [{"bh_install_done": "1"}, {"bh_home_hits": "1"}, {"evil": "x"},          # 不在白名单
               {"bh_theme": "a;b"}, {"bh_theme": "a b"}, {"bh_theme": ""}, {"bh_theme": 1}, {"bh_theme": "x" * 1401}]
        for prefs in bad:
            self.assertEqual(self.put(prefs).status_code, 400, prefs)
        self.assertEqual(self.client.put("/api/deck/prefs", json={"prefs": {}}).status_code, 400)
        self.assertEqual(self.client.put("/api/deck/prefs", json={"prefs": []}).status_code, 400)
        self.assertFalse(self.deck_file().exists())

    def test_lenient_read_drops_junk(self):
        deck = _coerce_deck({"prefs": {"bh_theme": "light", "bh_open": "a;b", "evil": "x", "bh_engine": 3}})
        self.assertEqual(deck["prefs"], {"bh_theme": "light"})
        self.assertEqual(_coerce_deck({"prefs": "x"})["prefs"], {})

    def test_needs_admin_and_is_withheld_until_unlocked(self):
        saved = app_module.ADMIN_PASSWORD
        app_module.ADMIN_PASSWORD = PASSWORD
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", saved)
        admin = {"X-Admin-Password": PASSWORD}
        self.assertEqual(self.put({"bh_theme": "light"}).status_code, 403)
        self.assertEqual(self.put({"bh_theme": "light"}, headers=admin).status_code, 200)
        self.assertEqual(self.client.get("/api/configs").get_json()["deck"]["prefs"], {})
        self.assertEqual(self.client.get("/api/configs", headers=admin).get_json()["deck"]["prefs"], {"bh_theme": "light"})

    def test_export_and_import_carry_prefs(self):
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})
        self.put({"bh_wallpaper": "dusk"})
        exported = self.client.get("/api/configs/export").get_json()
        self.assertEqual(exported["deck"]["prefs"], {"bh_wallpaper": "dusk"})
        self.deck_file().unlink()
        self.assertEqual(self.client.post("/api/configs/import", json=exported).status_code, 200)
        self.assertEqual(read_deck()["prefs"], {"bh_wallpaper": "dusk"})


class PrefSyncWhitelistTest(unittest.TestCase):
    def test_page_and_server_agree_on_the_names(self):
        script = _inline_script()
        names = re.search(r"const SYNC_PREFS = \[([^\]]+)\];", script).group(1)
        self.assertEqual(tuple(re.findall(r"'(bh_\w+)'", names)), SYNC_PREF_NAMES)
        # 每一个都确实是页面会写的偏好（改名时别漏了这里）。
        for name in SYNC_PREF_NAMES:
            self.assertRegex(script, r"writePref\('%s'|writeDeckList\('%s'|'%s' :|: '%s'" % (name, name, name, name), name)


# 包一层 fetch：记下每个写请求的地址和请求体。
RECORD = ("{ const inner = globalThis.fetch; globalThis.__SENT = [];"
          "  globalThis.fetch = (url, opts) => { if (opts && opts.method && opts.method !== 'GET')"
          "    __SENT.push([String(url), JSON.parse(opts.body || 'null')]); return inner(url, opts); }; }"
          # 同上，记下主题被设成了什么（桩的 setAttribute 是空操作）。
          "{ globalThis.__THEMES = []; const html = document.documentElement, inner = html.setAttribute;"
          "  html.setAttribute = (k, v) => { if (k === 'data-theme') __THEMES.push(v); return inner.call(html, k, v); }; }")
PREFS_OK = {"/api/deck/prefs": {"ok": True, "deck": {"prefs": {}}}}


class PrefSyncScriptTest(DeckScriptCase):
    def run_sync(self, assertions, server=None, cookies=None, locked=False):
        deck = dict(self.DECK, prefs=server or {})
        self.run_js(assertions, configs={"deck": deck, "deck_locked": locked}, cookies=cookies,
                    extra=PREFS_OK, prelude=RECORD)

    def test_server_values_overwrite_this_browser(self):
        self.run_sync("""
            assert.equal(localPref('bh_theme'), 'light');
            assert.deepEqual(__THEMES, ['dark', 'light']);                   // 先按本机画，数据到了不用刷新就换过来（桩不记 data-theme，记下设的值）
            assert.equal(localPref('bh_home_deck'), 'calendar.weather');
            assert.deepEqual(shown(), ['calendar', 'weather']);
            assert.equal(currentEngine().id, 'bing');
            assert.deepEqual(__SENT, []);                                    // 和服务器一致：什么都不用发
        """, server={"bh_theme": "light", "bh_home_deck": "calendar.weather", "bh_engine": "bing"},
            cookies={"bh_theme": "dark", "bh_engine": "google"})

    def test_choices_the_server_lacks_are_uploaded(self):
        self.run_sync("""
            await tick();
            assert.deepEqual(__SENT, [['/api/deck/prefs', { prefs: { bh_open: 'same' } }]]);
            assert.equal(localPref('bh_theme'), 'light');
        """, server={"bh_theme": "light"}, cookies={"bh_open": "same", "bh_home_deck_open": "todo"})

    def test_changes_are_uploaded_but_device_state_is_not(self):
        self.run_sync("""
            setTheme('light'); setEngine('bing'); writePref('bh_home_deck_open', 'memo'); writePref('bh_home_hits', '1');
            assert.deepEqual(__SENT, []);                                    // 攒一下再发
            await flushPrefSync();
            assert.deepEqual(__SENT, [['/api/deck/prefs', { prefs: { bh_theme: 'light', bh_engine: 'bing' } }]]);
        """)

    def test_pending_change_is_not_reverted_by_a_stale_listing(self):
        self.run_sync("""
            writePref('bh_theme', 'light');
            STATE.deck.prefs = { bh_theme: 'dark' };                        // 上传前回到页面，拿到的还是旧值
            applySyncedPrefs();
            assert.equal(localPref('bh_theme'), 'light');
        """, server={"bh_theme": "dark"}, cookies={"bh_theme": "dark"})

    def test_locked_visitors_keep_preferences_local(self):
        self.run_sync("""
            setTheme('light');
            await flushPrefSync(); await tick();
            assert.deepEqual(__SENT, []);
            assert.equal(localPref('bh_theme'), 'light');
        """, cookies={"bh_open": "same"}, locked=True)


if __name__ == "__main__":
    unittest.main()

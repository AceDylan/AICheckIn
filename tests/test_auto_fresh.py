# -*- coding: utf-8 -*-
"""看板数据「看见的时候是新的」：首页 / 站点看板显示出来时，太旧的站点在后台悄悄刷一次。

服务端：refresh_balance 带 max_age 时，按服务器时钟判断刚取过就直接回现有的值、不再请求对方接口。
页面：把真实页面脚本放进 node 里跑——哪些站点会被刷、多久刷一次、什么时候不碰。"""
import datetime
import unittest
from unittest.mock import patch

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app  # noqa: E402
from tests.test_revisit_refresh import PageScriptCase, NODE  # noqa: E402


def _stamp(seconds_ago):
    return (datetime.datetime.now() - datetime.timedelta(seconds=seconds_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _site(*updated):
    return {"name": "r5", "url": "https://r5.example", "fields": [
        {"id": "f%d" % n, "label": "字段%d" % n, "type": "amount", "enabled": True, "json_path": "data.v",
         "method": "GET", "url": "https://r5.example/api", "headers": {}, "body": None,
         "value": "1.00", "updated_at": at} for n, at in enumerate(updated)]}


class MaxAgeTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def post(self, site, query):
        self.write_config({"configs": [], "proxy_url": "", "schedule": {}, "bookmarks": [site]})
        calls = []

        def fake_refresh(bookmark, proxy_url, field_id=None):
            calls.append(bookmark["name"])
            for f in bookmark["fields"]:
                f["value"], f["updated_at"] = "2.00", _stamp(0)
            return [{"id": f["id"], "label": f["label"], "ok": True, "value": "2.00"} for f in bookmark["fields"]]
        with patch.object(app_module, "refresh_bookmark_fields", fake_refresh):
            data = self.client.post("/api/bookmarks/0/refresh_balance" + query).get_json()
        return data, calls

    def test_recently_fetched_site_is_answered_from_the_store(self):
        data, calls = self.post(_site(_stamp(120), _stamp(30)), "?max_age=600")
        self.assertEqual(calls, [])                                   # 没去打扰对方接口
        self.assertTrue(data["ok"] and data["fresh"], data)
        self.assertEqual(data["bookmark"]["fields"][0]["value"], "1.00")
        self.assertNotIn("url", data["bookmark"]["fields"][0])       # 同样是公开视图，不带请求配置

    def test_any_old_or_unfetched_field_means_a_real_refresh(self):
        for site in (_site(_stamp(30), _stamp(900)), _site(_stamp(30), ""), _site("刚刚")):
            data, calls = self.post(site, "?max_age=600")
            self.assertEqual(calls, ["r5"], site)
            self.assertTrue(data["ok"] and not data.get("fresh"), data)
            self.assertEqual(data["bookmark"]["fields"][0]["value"], "2.00")

    def test_a_timestamp_in_the_future_does_not_count_as_fresh(self):
        # 服务器时钟往回拨过：未来的时间不能让这个站点一直「刚取过」。
        _, calls = self.post(_site(_stamp(-3600)), "?max_age=600")
        self.assertEqual(calls, ["r5"])

    def test_without_a_sane_max_age_it_always_refreshes(self):
        for query in ("", "?max_age=0", "?max_age=-5", "?max_age=abc", "?max_age=999999"):
            _, calls = self.post(_site(_stamp(1)), query)
            self.assertEqual(calls, ["r5"], query)

    def test_max_age_still_honours_the_stable_key(self):
        # 下标错位照旧回 409，不会拿别的站点的值来答。
        self.write_config({"configs": [], "proxy_url": "", "schedule": {}, "bookmarks": [_site(_stamp(1))]})
        resp = self.client.post("/api/bookmarks/0/refresh_balance?expect=other%7Cx&max_age=600")
        self.assertEqual(resp.status_code, 409)


# 页面里：updated_at 按浏览器本地时间写，和 CLOCK（被桩住的 Date.now）对齐。
PRELUDE = """
const pad = (n) => String(n).padStart(2, '0');
const stampAgo = (ms) => { const d = new Date(CLOCK - ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
const cfg = __RESPONSES['/api/configs'];
cfg.bookmarks = [
  { name: '常看的', url: 'https://r5.example', key: 'https://r5.example|常看的', show_on_home: true,
    fields: [{ id: 'f1', label: '余额', type: 'amount', enabled: true, value: '5.16', updated_at: stampAgo(3 * 3600e3) }] },
  { name: '刚刷过', url: 'https://fresh.example', key: 'https://fresh.example|刚刷过', show_on_home: true,
    fields: [{ id: 'f1', label: '余额', type: 'amount', enabled: true, value: '9', updated_at: stampAgo(2 * 60e3) }] },
  { name: '不在首页', url: 'https://side.example', key: 'https://side.example|不在首页',
    fields: [{ id: 'f1', label: '余额', type: 'amount', enabled: true, value: '3', updated_at: stampAgo(3 * 3600e3) }] },
  { name: '先不管', url: 'https://snooze.example', key: 'https://snooze.example|先不管', show_on_home: true,
    fields: [{ id: 'f1', label: '余额', type: 'amount', enabled: true, error: 'HTTP 401', updated_at: stampAgo(3 * 3600e3),
               snooze_until: Math.floor(CLOCK / 1000) + 86400 }] },
];
const hits = () => __CALLS.fetches.filter(u => u.includes('/refresh_balance'));
const settle = () => new Promise(r => setTimeout(r, 480));
"""


@unittest.skipIf(NODE is None, "未安装 node")
class AutoFreshPageTest(PageScriptCase):
    def run_page(self, assertions, extra=""):
        self.run_js(assertions, prelude=PRELUDE + extra)

    def test_home_refreshes_only_what_it_shows_and_is_old(self):
        self.run_page("""
            await settle();
            assert.deepEqual(hits(), ['/api/bookmarks/0/refresh_balance?expect=' + encodeURIComponent('https://r5.example|常看的') + '&max_age=600']);
            assert.deepEqual(toasts, []);                                    // 悄悄的
            openLibPage('monitor'); await settle();                          // 看板：首页没有的那个也旧了
            assert.equal(hits().length, 2);
            assert.ok(hits()[1].startsWith('/api/bookmarks/2/refresh_balance?'));
        """)

    def test_each_site_is_tried_once_per_window(self):
        self.run_page("""
            await settle();
            assert.equal(hits().length, 1);
            await leave(2 * 60e3); await settle();                           // 桩的接口没回新数据，但 10 分钟内不再试
            openLibPage('@home'); await settle();
            assert.equal(hits().length, 1);
            await leave(9 * 60e3); await settle();                           // 过了 10 分钟：「刚刷过」那个也旧了
            assert.equal(hits().length, 3);
        """)

    def test_a_refreshed_value_is_drawn_without_a_toast(self):
        self.run_page("""
            __RESPONSES['/api/bookmarks/0/refresh_balance'] = { ok: true, results: [{ label: '余额', value: '3.20' }],
              bookmark: Object.assign({}, cfg.bookmarks[0], { fields: [Object.assign({}, cfg.bookmarks[0].fields[0], { value: '3.20', updated_at: stampAgo(0) })] }) };
            await settle();
            assert.equal(STATE.bookmarks[0].fields[0].value, '3.20');
            assert.ok($('homeList').innerHTML.includes('3.2'));
            assert.deepEqual(toasts, []);
        """)

    def test_menus_and_dialogs_hold_the_redraw(self):
        self.run_page("""
            __RESPONSES['/api/bookmarks/0/refresh_balance'] = { ok: true, results: [],
              bookmark: Object.assign({}, cfg.bookmarks[0], { fields: [Object.assign({}, cfg.bookmarks[0].fields[0], { value: '3.20', updated_at: stampAgo(0) })] }) };
            const realQuery = document.querySelector;
            let menuOpen = true;
            document.querySelector = (sel) => menuOpen && sel.includes('details.action-menu[open]') ? $('someMenu') : realQuery(sel);
            await settle();
            assert.equal(STATE.bookmarks[0].fields[0].value, '3.20');
            assert.ok(!$('homeList').innerHTML.includes('3.2'), '菜单开着：先不重画');
            menuOpen = false;
            await new Promise(r => setTimeout(r, 1100));
            assert.ok($('homeList').innerHTML.includes('3.2'), '菜单关了：补上');
            document.querySelector = realQuery;
        """)

    def test_nothing_happens_when_locked_hidden_or_elsewhere(self):
        self.run_page("""
            await settle();
            assert.equal(hits().length, 0);
        """, extra="cfg.admin_unlocked = false;")
        self.run_page("""
            await settle();
            assert.equal(hits().length, 0);
        """, extra="document.hidden = true;")
        self.run_page("""
            switchView('settings'); await settle();
            const n = hits().length;
            await leave(3600e3); await settle();                              // 设置页上回来：不碰
            assert.equal(hits().length, n);
        """)

    def test_network_failures_stay_silent(self):
        self.run_page("""
            const realFetch = fetch;
            fetch = (url, opts) => String(url).includes('/refresh_balance')
              ? (__CALLS.fetches.push(String(url)), Promise.reject(new Error('Failed to fetch'))) : realFetch(url, opts);
            await settle();
            assert.equal(hits().length, 1);
            assert.deepEqual(toasts, []);
            fetch = realFetch;
        """)


if __name__ == "__main__":
    unittest.main()

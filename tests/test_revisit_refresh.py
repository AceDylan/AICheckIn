# -*- coding: utf-8 -*-
"""回到页面时对一下数据：起始页的标签一挂半天，另一台设备上的改动要靠它才看得到。

把真实页面脚本放进 node 里跑：离开够久才取、手上有活不打扰、失败不出声。"""
import copy
import json
import subprocess
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script


class PageScriptCase(unittest.TestCase):
    def run_js(self, assertions, offline_at_boot=False, prelude=""):
        responses = copy.deepcopy(RESPONSES)
        responses["/api/configs"].update({"todos": [], "todos_locked": False, "todos_error": "",
                                          "deck": {"days": [], "memo": {"text": "", "updated_at": ""}},
                                          "deck_locked": False, "deck_error": ""})
        script = "\n".join([
            Path(STUB).read_text(),
            "globalThis.__RESPONSES = " + json.dumps(responses) + ";",
            "const assert = require('node:assert/strict');",
            # 桩里的 addEventListener 什么都不记：这里把 document / window 上的监听收起来，好在用例里亲手触发。
            "const DOC_ON = {}, WIN_ON = {};",
            "document.addEventListener = (name, fn) => { (DOC_ON[name] = DOC_ON[name] || []).push(fn); };",
            "globalThis.addEventListener = (name, fn) => { (WIN_ON[name] = WIN_ON[name] || []).push(fn); };",
            "let CLOCK = 1790000000000; Date.now = () => CLOCK;",
            # 开机时断网：/api/configs 发不出去；OFFLINE 拨回 false 就恢复。
            "let OFFLINE = " + ("true" if offline_at_boot else "false") + "; const stubFetch = fetch;",
            "fetch = (url, opts) => { if (!OFFLINE) return stubFetch(url, opts);"
            "  __CALLS.fetches.push(String(url)); return Promise.reject(new Error('Failed to fetch')); };",
            "const toasts = []; globalThis.__toastSink = (msg) => toasts.push(msg);",
            prelude,
            _inline_script().replace("function toast(msg, kind) {", "function toast(msg, kind) { return __toastSink(msg);", 1),
            "const tick = () => new Promise(r => setTimeout(r, 5));",
            "const loads = () => __CALLS.fetches.filter(u => u === '/api/configs').length;",
            "const fire = (map, name, ev) => (map[name] || []).forEach(fn => fn(ev || {}));",
            "const leave = async (ms) => { document.hidden = true; fire(DOC_ON, 'visibilitychange'); CLOCK += ms;"
            "  document.hidden = false; fire(DOC_ON, 'visibilitychange'); await tick(); };",
            "const fresh = (change) => { const next = JSON.parse(JSON.stringify(__RESPONSES['/api/configs']));"
            "  change(next); __RESPONSES['/api/configs'] = next; };",
            "setTimeout(async () => { try {", assertions,
            "assert.deepEqual(__CALLS.errors, []); assert.deepEqual(__CALLS.rejections, []);",
            "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, 30);",
        ])
        proc = subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        self.assertIn("ok", proc.stdout)


@unittest.skipIf(NODE is None, "未安装 node")
class RevisitRefreshTest(PageScriptCase):
    def test_a_long_absence_refetches_and_redraws(self):
        self.run_js("""
            assert.equal(loads(), 1);
            await leave(10 * 1000);                                           // 切出去十秒：不值得
            assert.equal(loads(), 1);
            fresh(d => d.link_groups[0].links.push({ id: 'l2', name: '手机上加的', url: 'https://phone.example', desc: '', icon: '', tags: [], pinned: false, show_on_home: true }));
            await leave(61 * 1000);
            assert.equal(loads(), 2);
            assert.equal(STATE.link_groups[0].links.length, 2);
            assert.ok($('homeList').innerHTML.includes('手机上加的'));
            assert.deepEqual(toasts, []);
            document.hidden = false; fire(DOC_ON, 'visibilitychange'); await tick();   // 没离开过：不重复取
            assert.equal(loads(), 2);
        """)

    def test_work_in_progress_is_never_interrupted(self):
        self.run_js("""
            const realQuery = document.querySelector;
            document.querySelector = (sel) => sel === '.modal-mask.show' ? $('linkModal') : realQuery(sel);
            await leave(3600 * 1000);                                         // 弹窗开着
            document.querySelector = realQuery;
            assert.equal(loads(), 1);
            for (const hold of [() => { ARRANGE.on = true; return () => { ARRANGE.on = false; }; },
                                () => { TODO.editing = 't1'; return () => { TODO.editing = ''; }; },
                                () => { TODO.inflight = 1; return () => { TODO.inflight = 0; }; },
                                () => { DAYS.formOpen = true; return () => { DAYS.formOpen = false; }; },
                                () => { MEMO.dirty = true; return () => { MEMO.dirty = false; }; },
                                () => { document.activeElement = { tagName: 'INPUT', value: '搜到一半' }; return () => { document.activeElement = { tagName: 'INPUT', value: '' }; }; }]) {
              const release = hold();
              await leave(3600 * 1000);
              release();
              assert.equal(loads(), 1);
            }
            switchView('settings');                                           // 设置页有填到一半的表单：不碰
            const before = __CALLS.fetches.length;
            await leave(3600 * 1000);
            assert.equal(__CALLS.fetches.length, before);
            switchView('bookmarks');
            await leave(3600 * 1000);                                         // 空着的搜索框（起始页自动聚焦）不算在忙
            assert.equal(loads(), 2);
        """)

    def test_failures_stay_silent_and_keep_what_is_on_screen(self):
        self.run_js("""
            const realFetch = fetch, shown = $('homeList').innerHTML;
            fetch = (url) => { __CALLS.fetches.push(String(url)); return Promise.reject(new Error('Failed to fetch')); };
            await leave(3600 * 1000);                                         // 刚从睡眠里醒来，网络还没接上
            assert.equal(loads(), 2);
            assert.deepEqual(toasts, []);
            assert.equal($('homeList').innerHTML, shown);
            fetch = realFetch;
            const good = __RESPONSES['/api/configs'];
            __RESPONSES['/api/configs'] = { ok: false, error: '读取配置文件失败' };
            await leave(3600 * 1000);
            assert.deepEqual(toasts, []);
            assert.equal(currentViewName(), 'bookmarks');                     // 不把人拽去「配置恢复」
            assert.equal(STATE.link_groups.length, 1);
            __RESPONSES['/api/configs'] = good;
            assert.equal(await loadConfigs(), true);                          // 手动加载照旧会出声
            __RESPONSES['/api/configs'] = { ok: false, error: '读取配置文件失败' };
            assert.equal(await loadConfigs(), undefined);
            assert.equal(toasts.length, 1);
        """)

    def test_a_failed_first_load_retries_quietly_until_the_network_is_back(self):
        self.run_js("""
            assert.equal(STATE_LOADED, false);
            assert.equal(loads(), 1);
            assert.equal(toasts.length, 1);                                   // 第一次失败照常说一声
            assert.ok(BOOT_RETRY.timer, '已经排上了下一次重试');
            assert.equal(await bootRetry(), false);                           // 还是不通：不再出声，接着排
            assert.equal(toasts.length, 1);
            assert.equal(BOOT_RETRY.n, 2);
            OFFLINE = false;
            fire(WIN_ON, 'online'); await tick();                             // 网络回来：立刻补上
            assert.equal(STATE_LOADED, true);
            assert.equal(STATE.link_groups[0].links[0].name, 'Docs');
            assert.equal(toasts.length, 1);
            const n = loads();
            fire(WIN_ON, 'online'); await tick();                             // 已经有数据了：online 不再触发加载
            assert.equal(await bootRetry(), false);
            assert.equal(loads(), n);
        """, offline_at_boot=True)

    def test_coming_back_to_a_tab_that_never_loaded_retries_at_once(self):
        self.run_js("""
            assert.equal(STATE_LOADED, false);
            OFFLINE = false;
            await leave(1000);                                                // 不必等够一分钟
            assert.equal(STATE_LOADED, true);
            assert.equal(loads(), 2);
        """, offline_at_boot=True)

    def test_retries_give_up_after_a_few_attempts(self):
        self.run_js("""
            while (BOOT_RETRY.n < BOOT_RETRY.delays.length) await bootRetry();
            const n = loads();
            await bootRetry();                                                // 人为再触发一次也只是一次请求，不会再排新的定时
            assert.equal(loads(), n + 1);
            assert.equal(BOOT_RETRY.n, BOOT_RETRY.delays.length);
            assert.equal(toasts.length, 1);
        """, offline_at_boot=True)

    def test_a_server_side_error_is_not_retried(self):
        """配置损坏这类服务端明确报错，重试也没用：照旧提示并把人带去「配置恢复」，不排重试。"""
        self.run_js("""
            assert.equal(BOOT_RETRY.timer, 0);
            assert.equal(BOOT_RETRY.n, 0);
        """)

    def test_back_forward_cache_restores_refresh_too(self):
        self.run_js("""
            fire(WIN_ON, 'pagehide', { persisted: true });
            CLOCK += 5 * 60 * 1000;
            fire(WIN_ON, 'pageshow', { persisted: true }); await tick();
            assert.equal(loads(), 2);
            fire(WIN_ON, 'pageshow', { persisted: false }); await tick();      // 普通加载自己会取数据
            assert.equal(loads(), 2);
        """)

    def test_a_refresh_in_flight_is_not_doubled(self):
        self.run_js("""
            document.hidden = true; fire(DOC_ON, 'visibilitychange'); fire(WIN_ON, 'pagehide', {});
            CLOCK += 3600 * 1000;
            document.hidden = false; fire(DOC_ON, 'visibilitychange'); fire(WIN_ON, 'pageshow', { persisted: true });
            await tick();
            assert.equal(loads(), 2);
        """)


@unittest.skipIf(NODE is None, "未安装 node")
class EarlyConfigsTest(PageScriptCase):
    """<head> 里提前发出的 /api/configs：主脚本的第一次加载接手它，省掉一个来回；只用一次，失败就按老路重发。"""

    def test_head_fires_the_request_before_the_stylesheet(self):
        html = (Path(__file__).resolve().parents[1] / "templates" / "index.html").read_text(encoding="utf-8")
        head = html[:html.index("</head>")]
        self.assertIn("window.__bootConfigs = fetch('/api/configs'", head)
        self.assertLess(head.index("window.__bootConfigs = fetch("), head.index('rel="stylesheet"'))
        # 拿不到就交回 null，让主脚本自己重发；不在这里处理数据、不碰 DOM。
        self.assertIn(".catch(function () { return null; })", head)

    def test_first_load_adopts_the_early_response_without_a_second_request(self):
        self.run_js("""
            assert.equal(STATE_LOADED, true);
            assert.equal(loads(), 0);                                         // 没有再发一次
            assert.equal(STATE.link_groups[0].name, '提前到的');
            assert.equal(window.__bootConfigs, null);                         // 只用一次
            assert.equal(await loadConfigs(), true);
            assert.equal(loads(), 1);                                         // 之后每次都是新请求
            assert.equal(STATE.link_groups[0].name, '常用');
        """, prelude="window.__bootConfigs = Promise.resolve(Object.assign(JSON.parse(JSON.stringify(__RESPONSES['/api/configs'])),"
                     " { link_groups: [{ id: 'early', name: '提前到的', icon: 'globe', color: 'mint', links: [] }] }));")

    def test_a_failed_early_request_falls_back_to_a_normal_one(self):
        self.run_js("""
            assert.equal(STATE_LOADED, true);
            assert.equal(loads(), 1);
            assert.deepEqual(toasts, []);
        """, prelude="window.__bootConfigs = Promise.resolve(null);")

    def test_an_early_server_error_is_reported_like_before(self):
        self.run_js("""
            assert.equal(STATE_LOADED, false);
            assert.equal(toasts.length, 1);
            assert.ok(toasts[0].includes('配置恢复'));
            assert.equal(currentViewName(), 'settings');
        """, prelude="window.__bootConfigs = Promise.resolve({ ok: false, error: '读取配置文件失败' });")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""2026-09-26 体验评估的改进：加载 / 断网状态、看板提醒、数字格式、拼音搜索等。

页面脚本放进 node 的 DOM 替身里真跑（同 test_library_home_ui），需要时在脚本之前换掉 fetch。
"""
import copy
import json
import subprocess
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script


def run_page(assertions, before='', responses=None, delay=30):
    data = copy.deepcopy(RESPONSES) if responses is None else responses
    script = '\n'.join([
        Path(STUB).read_text(),
        'globalThis.__RESPONSES = ' + json.dumps(data) + ';',
        "const assert = require('node:assert/strict');",
        "document.querySelectorAll('.tab').forEach(t => { t.addEventListener = (name, fn) => { t[name] = fn; }; });",
        "{ const t = document.getElementById('navToggle'); t.addEventListener = (name, fn) => { t[name] = fn; }; }",
        before,
        _inline_script(),
        'setTimeout(async () => { try {', assertions,
        "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, %d);" % delay,
    ])
    return subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=20)


@unittest.skipIf(NODE is None, '未安装 node')
class BootStateTest(unittest.TestCase):
    """数据没到 / 没取到时，收藏页不能画成「空库引导」。"""

    def check(self, assertions, before=''):
        proc = run_page(assertions, before)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_pending_load_shows_skeleton_not_the_empty_library_guide(self):
        # /api/configs 迟迟不回：首页只有占位骨架，「从第一个网址开始 / 新建第一个分组」不出现。
        self.check("""
            assert.equal(STATE_LOADED, false);
            assert.ok($('homeList').innerHTML.includes('boot-skeleton'));
            assert.equal($('homeEmpty').style.display, 'none');
            assert.ok(!$('homeEmpty').innerHTML.includes('从第一个网址开始'));
            assert.equal($('homeAddLink').hidden, true);
            openLibPage('monitor');
            assert.ok($('bmList').innerHTML.includes('boot-skeleton'));
            assert.equal($('bmEmpty').style.display, 'none');
        """, before="""
            const realFetch = globalThis.fetch;
            globalThis.fetch = (url, opts) => String(url).startsWith('/api/configs') ? new Promise(() => {}) : realFetch(url, opts);
        """)

    def test_network_failure_says_offline_and_offers_retry(self):
        self.check("""
            assert.equal(STATE_LOADED, false);
            assert.equal(BOOT_FAILED, true);
            const html = $('homeEmpty').innerHTML;
            assert.ok(html.includes('连不上服务器'), html);
            assert.ok(html.includes('retryBoot()'));
            assert.ok(!html.includes('新建第一个分组'));
            assert.ok(!$('homeList').innerHTML.includes('boot-skeleton'));
            // 网络恢复后点「立即重试」：数据到位，首页恢复正常。
            globalThis.__fail = false;
            await retryBoot();
            assert.equal(STATE_LOADED, true);
            assert.equal(BOOT_FAILED, false);
            assert.equal($('homeEmpty').innerHTML.includes('连不上服务器'), false);
        """, before="""
            globalThis.__fail = true;
            const realFetch = globalThis.fetch;
            globalThis.fetch = (url, opts) => (globalThis.__fail && String(url).startsWith('/api/configs'))
              ? Promise.reject(new TypeError('Failed to fetch')) : realFetch(url, opts);
        """)

    def test_normal_load_replaces_the_skeleton(self):
        self.check("""
            assert.equal(STATE_LOADED, true);
            assert.ok(!$('homeList').innerHTML.includes('boot-skeleton'));
        """)


if __name__ == '__main__':
    unittest.main()

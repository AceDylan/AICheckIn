# -*- coding: utf-8 -*-
"""设置页的「设置行」、状态图标（不用 emoji），以及几处启动时的省力写法（Cookie 偏好、数字格式化、小组件时间）。"""
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = Path(ROOT, "templates", "index.html").read_text(encoding="utf-8")
CSS = Path(ROOT, "static", "app-v3.css").read_text(encoding="utf-8")
EMOJI = re.compile("[☀-➿\U0001f300-\U0001faff]")


def _block(start, end):
    i = HTML.index(start)
    return HTML[i:HTML.index(end, i)]


class SettingsRowsTest(unittest.TestCase):
    def test_switches_live_in_a_row_that_is_itself_the_label(self):
        # 开关不再孤零零地待在输入框左边：整行是一个 <label>，点标题、说明、开关都能切换。
        for cid in ("schedEnabled", "refreshEnabled"):
            m = re.search(r'<label class="set-row">(.*?)</label>', HTML[HTML.index('id="%s"' % cid) - 400:], re.S)
            self.assertIsNotNone(m, cid)
            self.assertIn('id="%s"' % cid, m.group(1))
            self.assertIn('<span class="set-text"><b>', m.group(1))
            self.assertIn('<span class="switch"', m.group(1))

    def test_inputs_have_their_own_labelled_row(self):
        for cid in ("schedTime", "refreshInterval"):
            self.assertRegex(HTML, r'<label class="set-text" for="%s"><b>' % cid)
        self.assertNotIn('.controls[style*="flex-end"] > .switch', CSS)   # 旧的对齐补丁跟着删掉
        rule = re.search(r"\n\.set-row \{([^}]*)\}", CSS).group(1)
        for needle in ("display: flex", "flex-wrap: wrap", "justify-content: space-between"):
            self.assertIn(needle, rule)
        # 与输入框并排的按钮和输入框一样高
        self.assertIn(".settings-grid .set-ctrl .btn.sm, .settings-grid .controls > .btn.sm, .settings-grid .admin-form .btn.sm { min-height: 40px; }", CSS)


class StatusIconsTest(unittest.TestCase):
    ICON_NAMES = set(re.findall(r"^      ([a-zA-Z]+): '<", _block("const ICONS = {", "\n    };"), re.M))

    def test_status_maps_use_line_icons_not_emoji(self):
        for start, end in (("const ALERT_META = {", "};"), ("const LINK_CHECK_META = {", "};"), ("const DIAG_ICON = {", "};")):
            block = _block(start, end)
            self.assertIsNone(EMOJI.search(block), block)
            for name in re.findall(r"(?:icon: |: )'([a-z]+)'", block):
                if name.startswith("is"):
                    continue
                self.assertIn(name, self.ICON_NAMES, "%s 里用了没定义的图标 %s" % (start, name))

    def test_status_texts_have_no_emoji(self):
        for start, end in (("function loadDiagnostics", "$('runDiag')"), ("$('adminStatus').innerHTML", "$('adminForm')"),
                           ("async function loadHistory", "const resp")):
            self.assertIsNone(EMOJI.search(_block(start, end)), start)
        self.assertNotIn("📄 选择文件", HTML)

    def test_icons_follow_the_status_colour(self):
        for rule in (".diag-row.is-warn .diag-mark { color: var(--warning); }", ".diag-row.is-error .diag-mark { color: var(--danger); }",
                     ".check-badge.is-error, .check-badge.is-expired { color: var(--danger); }", ".status-pill.is-ok {"):
            self.assertIn(rule, CSS)


@unittest.skipIf(NODE is None, "未安装 node")
class BootHelpersScriptTest(unittest.TestCase):
    def run_js(self, assertions):
        script = "\n".join([
            Path(STUB).read_text(),
            "globalThis.__RESPONSES = " + json.dumps(RESPONSES) + ";",
            "const assert = require('node:assert/strict');",
            _inline_script(),
            "setTimeout(async () => { try {", assertions,
            "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, 30);",
        ])
        proc = subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_pref_written_in_this_task_is_read_back_at_once(self):
        # readPref 在同一轮任务里只读一次 document.cookie；本页自己写 Cookie 必须立刻作废这份缓存。
        self.run_js("""
            assert.equal(openMode(), 'new');
            writePref('bh_open', 'same');
            assert.equal(openMode(), 'same');
            setCookie('bh_open=new; max-age=31536000; path=/; SameSite=Lax');
            assert.equal(openMode(), 'new');
            setCookie('bh_open=evil');
            assert.equal(openMode(), 'new');                                   // 白名单照旧
        """)

    def test_number_format_is_unchanged(self):
        self.run_js("""
            const old = (n) => Number(n).toLocaleString('en-US', { maximumFractionDigits: 2 });
            for (const n of [0, 3, '27.0660232000', 1234.567, -0.004, 1e7, '12,5']) assert.equal(fmtNum(n), old(n), String(n));
        """)

    def test_widget_stamp_is_date_only_unless_today(self):
        self.run_js("""
            assert.equal(dayStamp('2026-01-02 03:04:05'), '01-02');
            const d = new Date(), p = (x) => String(x).padStart(2, '0');
            assert.equal(dayStamp(`${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} 07:08:09`), '07:08');
            assert.equal(dayStamp('昨天'), '昨天');
        """)


if __name__ == "__main__":
    unittest.main()

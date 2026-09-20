# -*- coding: utf-8 -*-
"""运行记录展开明细 + 快捷键速查表。

两个都是「数据/功能早就有，只是没让人看见」的补齐：
- 后端一直在 history.json 里存逐站结果，页面此前只显示汇总数字，失败了看不出是哪个站点；
- 全局搜索、页内搜索、键盘排序都有快捷键，但没有任何地方列出来。
"""
import json
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, read_history, record_history  # noqa: E402


class HistoryPayloadTest(StoreIsolationMixin, unittest.TestCase):
    """先确认后端确实把逐站结果存下来了——前端展开才有东西可显示。"""

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(HistoryPayloadTest, self).setUp()
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})

    def test_results_are_persisted_per_site(self):
        record_history("manual", [
            {"name": "站点A", "status": "signed", "quota": 100},
            {"name": "站点B", "status": "failed", "error": "HTTP 500"},
        ])
        entry = read_history()[0]
        self.assertEqual([r["name"] for r in entry["results"]], ["站点A", "站点B"])
        self.assertEqual(entry["results"][1]["status"], "failed")
        self.assertTrue(entry["results"][0]["status_label"])
        self.assertTrue(entry["results"][0]["color"])

    def test_error_only_entries_have_no_results(self):
        record_history("scheduled", error="定时签到失败：boom")
        entry = read_history()[0]
        self.assertEqual(entry["results"], [])
        self.assertEqual(entry["summary"], None)
        self.assertIn("boom", entry["error"])

    def test_api_returns_the_details(self):
        record_history("manual", [{"name": "站点A", "status": "signed", "quota": 1}])
        data = app.test_client().get("/api/history").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["history"][0]["results"][0]["name"], "站点A")


class HistoryUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_entries_render_as_expandable_details(self):
        self.assertIn("function histItemHtml", self.html)
        self.assertIn("function histResultHtml", self.html)
        self.assertIn('<details class="hist-item"', self.html)

    def test_failed_runs_are_expanded_by_default(self):
        # 打开运行记录就是想看哪里失败了，不该还要再点一下。
        self.assertIn("r.status === 'failed'", self.html)
        self.assertIn("${failed ? ' open' : ''}", self.html)

    def test_entries_without_results_stay_flat(self):
        self.assertIn('if (!results.length) return `<div class="hist-item">', self.html)

    def test_every_status_colour_has_a_pill_style(self):
        # serialize() 会回 green / cyan / red / gray 四种，缺一个就渲染成没样式的裸文字。
        for colour in ("green", "cyan", "red", "gray"):
            self.assertIn(".pill.%s" % colour, self.css)

    def test_detail_styles_exist(self):
        for rule in (".hist-results", ".hist-row-name", ".hist-row-quota", ".hist-toggle"):
            self.assertIn(rule, self.css)


class ShortcutHelpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_modal_and_entry_point_exist(self):
        self.assertIn('id="keysModal"', self.html)
        self.assertIn('id="keysOpen"', self.html)
        self.assertIn("function openKeysModal", self.html)

    def test_question_mark_opens_it(self):
        self.assertIn("event.key === '?'", self.html)

    def test_shortcut_keys_are_not_hijacked_while_typing(self):
        # 在输入框里敲 ? 或 / 必须能正常输入。
        self.assertIn("const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(tag || '')", self.html)
        self.assertIn("event.key === '?' && !typing", self.html)
        self.assertIn("event.key === '/' && !typing", self.html)

    def test_table_documents_the_shortcuts_that_actually_exist(self):
        table = self.html[self.html.index("const SHORTCUTS = ["):self.html.index("function renderShortcuts")]
        for token in ("⌘/Ctrl", "Shift", "Alt", "Esc"):
            self.assertIn(token, table)
        # 表里列的每一项都应当真有实现：抽查几个实现点。
        self.assertIn("event.metaKey || event.ctrlKey", self.html)   # ⌘K
        self.assertIn("omniOpen(omniResults[omniActive], e.shiftKey)", self.html)  # Shift+Enter
        self.assertIn("if (!e.altKey", self.html)                    # Alt+方向键
        self.assertIn("PageUp / PageDown 翻月", table)                 # 首页月历
        self.assertIn("if (e.key === 'PageUp' || e.key === 'PageDown')", self.html)
        self.assertIn("const grip = e.target.closest('[data-deck-grip]')", self.html)   # 组件弹窗里的排序把手

    def test_styles_exist(self):
        for rule in (".keys-list", ".keys-combo", ".keys-hint"):
            self.assertIn(rule, self.css)


if __name__ == "__main__":
    unittest.main()

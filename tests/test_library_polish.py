# -*- coding: utf-8 -*-
"""收藏库的两处补齐：分组导航上的问题计数、导入的文件选择。

都是「功能已经有了，但用起来还差一步」的类型：
- 死链检查是分组逐个跑的，没有导航提示就得挨个点进去才知道哪个分组有问题；
- 导出是直接下载文件的，导入却只能粘贴。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests._support import app_module  # noqa: F401  须早于 app 导入
from app import app  # noqa: E402

NODE = shutil.which("node")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "templates", "index.html")


@unittest.skipIf(NODE is None, "未安装 node，跳过计数逻辑验证")
class GroupIssueCountTest(unittest.TestCase):
    """groupIssueCount 决定导航上显示几。只数「需要处理」的，别把 blocked 混进来。"""

    @classmethod
    def setUpClass(cls):
        with open(TEMPLATE, encoding="utf-8") as fh:
            blob = fh.read()
        # linkCheckState + groupIssueCount 两段纯逻辑
        start = blob.index("const LINK_CHECK_META")
        end = blob.index("function linkCheckTitle")
        cls.state_logic = blob[start:end]
        start = blob.index("function groupIssueCount")
        end = blob.index("function renderLibNav")
        cls.count_logic = blob[start:end]

    def count(self, links):
        # 抽出来的那段本身就带着 linkAlertFilter 的声明，这里不要再声明一次。
        script = "\n".join([
            self.state_logic,
            "const GROUPS = %s;" % json.dumps([{"id": "g", "links": links}]),
            "function findGroup(id) { return GROUPS.find(g => g.id === id) || null; }",
            "function escapeHtml(s) { return String(s == null ? '' : s); }",
            self.count_logic,
            "console.log(JSON.stringify(groupIssueCount('g')));",
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout.strip())
        finally:
            os.unlink(path)

    def test_unchecked_links_count_as_nothing(self):
        self.assertEqual(self.count([{"id": "a"}, {"id": "b"}]), 0)

    def test_healthy_links_count_as_nothing(self):
        self.assertEqual(self.count([{"id": "a", "check": {"status": "ok"}}]), 0)

    def test_guarded_sites_are_not_problems(self):
        # 401/403 的站点还活着，算进去只会让导航常年挂着红点。
        self.assertEqual(self.count([{"id": "a", "check": {"status": "blocked"}}]), 0)

    def test_each_actionable_status_counts(self):
        for status in ("missing", "error", "unreachable"):
            self.assertEqual(self.count([{"id": "a", "check": {"status": status}}]), 1, status)

    def test_skipped_links_never_count(self):
        self.assertEqual(
            self.count([{"id": "a", "skip_check": True, "check": {"status": "missing"}}]), 0)

    def test_mixed_group_counts_only_the_bad_ones(self):
        self.assertEqual(self.count([
            {"id": "a", "check": {"status": "ok"}},
            {"id": "b", "check": {"status": "missing"}},
            {"id": "c", "check": {"status": "unreachable"}},
            {"id": "d", "check": {"status": "blocked"}},
            {"id": "e"},
        ]), 2)

    def test_unknown_status_is_ignored(self):
        # 将来新增状态时，导航不该把不认识的东西当成问题。
        self.assertEqual(self.count([{"id": "a", "check": {"status": "brand-new"}}]), 0)


class NavBadgeUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_badge_is_rendered_in_both_navs(self):
        # 侧栏（桌面）和 chips（移动端）都要有。
        self.assertEqual(self.html.count("${issueBadge(p)}"), 2)

    def test_monitor_page_has_no_badge(self):
        start = self.html.index("function issueBadge")
        self.assertIn("p.id === 'monitor'", self.html[start:start + 200])

    def test_badge_style_exists(self):
        self.assertIn(".issue-dot", self.css)


class ImportFilePickerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.html = app.test_client().get("/").get_data(as_text=True)

    def test_file_input_exists_and_accepts_json(self):
        self.assertIn('id="importFile"', self.html)
        self.assertIn('accept="application/json,.json"', self.html)

    def test_file_is_validated_before_import(self):
        # 先验 JSON，别等按了导入才报错。
        start = self.html.index("$('importFile').addEventListener")
        body = self.html[start:start + 900]
        self.assertIn("JSON.parse(text)", body)
        self.assertIn("不是合法的 JSON", body)

    def test_pasting_still_works(self):
        self.assertIn('id="importText"', self.html)
        self.assertIn("也可以直接把 JSON 粘在这里", self.html)

    def test_opening_the_dialog_clears_previous_selection(self):
        start = self.html.index("$('importCfg').addEventListener")
        body = self.html[start:start + 500]
        self.assertIn("$('importFile').value = ''", body)
        self.assertIn("$('importFileName').textContent = ''", body)

    def test_file_content_goes_through_the_same_endpoint(self):
        # 不新增上传接口：文件只在浏览器里读，仍然走 /api/configs/import。
        self.assertNotIn("FormData", self.html)
        self.assertIn("'/api/configs/import'", self.html)


if __name__ == "__main__":
    unittest.main()

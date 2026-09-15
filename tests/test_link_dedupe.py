# -*- coding: utf-8 -*-
"""网址查重：归一化规则、新增/编辑接口的 duplicates 提示（只提示不拦截），
以及前后端用的是同一套 key（JS 版在 node 里跑同样的用例）。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, find_duplicate_links, link_dedupe_key  # noqa: E402

NODE = shutil.which("node")
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "index.html")

# (a, b, 是否应判为同一网址)
PAIRS = [
    ("https://example.com", "http://example.com/", True),
    ("https://www.Example.com/path/", "https://example.com/path", True),
    ("https://example.com:443/a", "https://example.com/a", True),
    ("http://example.com:80", "https://example.com", True),
    ("https://example.com/a?b=1", "https://example.com/a?b=1", True),
    ("https://example.com/a#top", "https://example.com/a", True),
    ("https://example.com/a", "https://example.com/b", False),
    ("https://example.com/?a=1", "https://example.com/?a=2", False),
    ("https://example.com", "https://other.com", False),
    ("https://example.com:8443/x", "https://example.com/x", False),
]


class DedupeKeyTest(unittest.TestCase):
    def test_normalisation_rules(self):
        for a, b, same in PAIRS:
            self.assertEqual(link_dedupe_key(a) == link_dedupe_key(b), same, "%s vs %s" % (a, b))

    def test_blank_input_never_matches(self):
        for blank in ("", None, "   "):
            self.assertEqual(link_dedupe_key(blank), "")

    def test_unparseable_input_falls_back_to_itself(self):
        # 宁可漏判也不误判：认不出来的串只和自己相等。
        self.assertEqual(link_dedupe_key("Not A Url"), "not a url")
        self.assertNotEqual(link_dedupe_key("Not A Url"), link_dedupe_key("https://example.com"))


@unittest.skipIf(NODE is None, "未安装 node，跳过前端一致性检查")
class DedupeKeyParityTest(unittest.TestCase):
    """前端即时提示与后端提示必须给出一致的判断，否则用户会看到自相矛盾的提示。"""

    def test_js_and_python_agree(self):
        with open(TEMPLATE, encoding="utf-8") as fh:
            blob = fh.read()
        start = blob.index("function dedupeKey(url)")
        end = blob.index("// 在全部分组里找同指一处的既有网址")
        script = (blob[start:end]
                  + "\nconst PAIRS = %s;\n" % json.dumps([[a, b] for a, b, _ in PAIRS])
                  + "console.log(JSON.stringify(PAIRS.map(([a, b]) => dedupeKey(a) === dedupeKey(b))));")
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            js = json.loads(proc.stdout.strip())
        finally:
            os.unlink(path)
        self.assertEqual(js, [same for _, _, same in PAIRS])


class DuplicateApiTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(DuplicateApiTest, self).setUp()
        self.write_config({"configs": [], "bookmarks": [], "link_groups": [
            {"id": "daily", "name": "常用网站", "icon": "globe", "color": "mint", "links": [
                {"id": "l1", "name": "Docs", "url": "https://docs.example.com/guide"},
            ]},
            {"id": "ai", "name": "AI 服务", "icon": "sparkles", "color": "violet", "links": []},
        ]})
        self.client = app.test_client()

    def test_finds_hits_across_groups(self):
        from app import read_store
        hits = find_duplicate_links(read_store(), "http://www.docs.example.com/guide/")
        self.assertEqual([h["group_id"] for h in hits], ["daily"])
        self.assertEqual(hits[0]["name"], "Docs")

    def test_create_reports_but_does_not_block(self):
        resp = self.client.post("/api/link_groups/ai/links",
                                json={"url": "http://www.docs.example.com/guide/"})
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["ok"])  # 不拦截：同一网址收进两个分组可能是刻意的
        self.assertEqual(len(data["duplicates"]), 1)
        self.assertEqual(data["duplicates"][0]["existing"][0]["group_name"], "常用网站")
        self.assertEqual(len(self.read_config()["link_groups"][1]["links"]), 1)

    def test_create_without_duplicates_reports_empty(self):
        data = self.client.post("/api/link_groups/ai/links", json={"url": "https://fresh.example"}).get_json()
        self.assertEqual(data["duplicates"], [])

    def test_bulk_reports_each_offending_row(self):
        data = self.client.post("/api/link_groups/ai/links", json={"links": [
            {"url": "https://docs.example.com/guide"},
            {"url": "https://brand-new.example"},
        ]}).get_json()
        self.assertTrue(data["ok"])
        self.assertEqual([d["url"] for d in data["duplicates"]], ["https://docs.example.com/guide"])

    def test_duplicates_are_computed_before_the_write(self):
        # 否则刚写进去的那几条会和自己撞上，出现「刚添加就说重复」的假报。
        data = self.client.post("/api/link_groups/ai/links", json={"links": [
            {"url": "https://fresh-a.example"}, {"url": "https://fresh-b.example"},
        ]}).get_json()
        self.assertEqual(data["duplicates"], [])

    def test_update_excludes_the_link_itself(self):
        data = self.client.put("/api/link_groups/daily/links/l1",
                               json={"name": "Docs v2"}).get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["duplicates"], [])

    def test_update_reports_when_url_collides_with_another_entry(self):
        self.client.post("/api/link_groups/ai/links", json={"url": "https://other.example"})
        lid = self.read_config()["link_groups"][1]["links"][0]["id"]
        data = self.client.put("/api/link_groups/ai/links/%s" % lid,
                               json={"url": "https://docs.example.com/guide"}).get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["duplicates"][0]["group_id"], "daily")


class DedupeUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_single_mode_hint_is_wired(self):
        self.assertIn('id="lk_dup"', self.html)
        self.assertIn("function refreshDupHint", self.html)
        self.assertIn("$('lk_url').addEventListener('input', refreshDupHint)", self.html)

    def test_bulk_mode_has_a_skip_switch(self):
        self.assertIn('id="lk_skip_dup"', self.html)
        self.assertIn("function renderBulkPreview", self.html)

    def test_hint_styles_exist(self):
        for rule in (".dup-hint", ".dup-jump", ".preview-list .dup"):
            self.assertIn(rule, self.css)


if __name__ == "__main__":
    unittest.main()

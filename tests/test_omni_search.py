# -*- coding: utf-8 -*-
"""全局搜索（⌘K 命令面板）：模板内联脚本的语法体检 + 排序逻辑的行为测试。

页面是单文件模板、没有构建步骤，语法错误只有打开浏览器才会暴露；这里用 node --check
守住。排序部分把「纯逻辑」那一段抽出来在 node 里跑，给它喂一份固定的 STATE。
装了 node 才跑，没有就跳过（不把 node 变成必需依赖）。
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
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "index.html")

# 抽取纯逻辑段：OMNI_LIMIT … 到渲染函数之前，这一段不碰 DOM。
LOGIC_START = "const OMNI_LIMIT"
LOGIC_END = "const OMNI_KIND_LABEL"

# node 侧的替身：只补逻辑段真正用到的几个外部符号。
HARNESS_PRELUDE = """
const STATE = __STATE__;
function libGroups() { return STATE.link_groups || []; }
function siteHost(u) {
  try { return new URL(u).hostname.replace(/^www\\./, ''); }
  catch (_) { return String(u || '').replace(/^https?:\\/\\//, '').split('/')[0]; }
}
"""

FIXTURE = {
    "link_groups": [
        {"id": "daily", "name": "常用网站", "icon": "globe", "color": "mint", "links": [
            {"id": "l1", "name": "Grafana", "url": "https://grafana.example.com", "desc": "监控面板",
             "tags": ["监控", "ops"], "pinned": True, "created_at": "2026-09-01 10:00:00"},
            {"id": "l2", "name": "文档站", "url": "https://docs.example.com", "desc": "内部 wiki",
             "tags": ["doc"], "pinned": False, "created_at": "2026-09-14 10:00:00",
             "check": {"status": "missing", "code": 404, "at": "2026-09-15 08:00:00"}},
        ]},
        {"id": "ai", "name": "AI 服务", "icon": "sparkles", "color": "violet", "links": [
            {"id": "l3", "name": "Grafana Cloud", "url": "https://cloud.grafana.net", "desc": "",
             "tags": ["ops"], "pinned": False,
             "check": {"status": "blocked", "code": 403, "at": "2026-09-15 08:00:00"}},
        ]},
    ],
    "bookmarks": [
        {"name": "看板站", "url": "https://dash.example.com",
         "fields": [{"id": "f1", "label": "余额", "type": "amount", "enabled": True}]},
    ],
    "configs": [
        {"name": "签到站", "base_url": "https://cfg.example.com", "user_id": "42", "enabled": True},
    ],
}


def _script_blocks():
    with open(TEMPLATE, encoding="utf-8") as fh:
        return re.findall(r"<script>(.*?)</script>", fh.read(), re.S)


@unittest.skipIf(NODE is None, "未安装 node，跳过前端脚本检查")
class InlineScriptSyntaxTest(unittest.TestCase):
    def test_every_inline_script_parses(self):
        blocks = _script_blocks()
        self.assertTrue(blocks, "模板里没找到 <script> 块")
        for i, block in enumerate(blocks):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
                fh.write(block)
                path = fh.name
            try:
                proc = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, "第 %d 个 <script> 语法错误：\n%s" % (i + 1, proc.stderr))
            finally:
                os.unlink(path)

    def test_template_has_no_jinja_inside_scripts(self):
        # 模板变量混进 JS 会让上面的 node --check 失去意义，也容易变成注入点。
        for block in _script_blocks():
            self.assertEqual(re.findall(r"\{\{.*?\}\}|\{%.*?%\}", block), [])


@unittest.skipIf(NODE is None, "未安装 node，跳过前端脚本检查")
class OmniSearchRankingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        blob = "\n".join(_script_blocks())
        start, end = blob.index(LOGIC_START), blob.index(LOGIC_END)
        # omniIndex 会给每条网址标上死链状态，把那段真实逻辑一并带进来，
        # 而不是在测试里复制一份可能与实现走样的替身。
        dep_start = blob.index("const LINK_CHECK_META")
        dep_end = blob.index("function linkCheckTitle")
        cls.logic = blob[dep_start:dep_end] + "\n" + blob[start:end]

    def search_full(self, query):
        """返回完整候选对象（而不是 [kind, title]），用于断言附加字段。"""
        script = (HARNESS_PRELUDE.replace("__STATE__", json.dumps(FIXTURE, ensure_ascii=False))
                  + self.logic
                  + "\nconsole.log(JSON.stringify(omniSearch(%s)));" % json.dumps(query, ensure_ascii=False))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout.strip())
        finally:
            os.unlink(path)

    def search(self, query):
        script = (HARNESS_PRELUDE.replace("__STATE__", json.dumps(FIXTURE, ensure_ascii=False))
                  + self.logic
                  + "\nconsole.log(JSON.stringify(omniSearch(%s).map(i => [i.kind, i.title])));"
                  % json.dumps(query, ensure_ascii=False))
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout.strip())
        finally:
            os.unlink(path)

    def test_searches_across_every_group(self):
        titles = [t for _, t in self.search("grafana")]
        self.assertIn("Grafana", titles)
        self.assertIn("Grafana Cloud", titles)  # 另一个分组里的也要出现

    def test_exact_prefix_beats_substring(self):
        self.assertEqual(self.search("grafana")[0][1], "Grafana")

    def test_matches_host_description_and_group_name(self):
        self.assertIn("文档站", [t for _, t in self.search("docs.example")])
        self.assertIn("文档站", [t for _, t in self.search("wiki")])
        self.assertIn("Grafana", [t for _, t in self.search("常用")])

    def test_bookmarks_and_configs_are_searchable(self):
        self.assertEqual(self.search("看板站")[0], ["bookmark", "看板站"])
        self.assertEqual(self.search("签到站")[0], ["config", "签到站"])
        self.assertIn("看板站", [t for _, t in self.search("余额")])  # 字段标签也进索引

    def test_groups_themselves_are_jump_targets(self):
        self.assertIn(["group", "AI 服务"], self.search("AI"))

    def test_hash_prefix_filters_by_tag_only(self):
        kinds = self.search("#ops")
        self.assertEqual(sorted(t for _, t in kinds), ["Grafana", "Grafana Cloud"])
        # 「监控」既是标签也是描述，#前缀下只认标签，不把描述命中的混进来
        self.assertEqual([t for _, t in self.search("#监控")], ["Grafana"])

    def test_bare_hash_lists_everything_tagged(self):
        titles = sorted(t for _, t in self.search("#"))
        self.assertEqual(titles, ["Grafana", "Grafana Cloud", "文档站"])

    def test_no_match_returns_empty(self):
        self.assertEqual(self.search("zzz-nothing-here"), [])

    def test_empty_query_lists_pinned_first(self):
        self.assertEqual(self.search("")[0], ["link", "Grafana"])

    def test_empty_query_then_orders_by_most_recently_added(self):
        # 刚收进来的一批正是接下来要用的；按字典序摆出来基本等于随机。
        titles = [t for kind, t in self.search("") if kind == "link"]
        self.assertEqual(titles[0], "Grafana")            # 置顶优先
        self.assertEqual(titles[1:], ["文档站", "Grafana Cloud"])  # 再按 created_at 倒序

    def test_links_without_a_timestamp_sort_last(self):
        # 旧数据没有 created_at，不该因此插到最前面。
        titles = [t for kind, t in self.search("") if kind == "link"]
        self.assertEqual(titles[-1], "Grafana Cloud")

    def test_dead_links_are_flagged_in_results(self):
        out = self.search_full("文档站")
        self.assertEqual(out[0]["alert"], "missing")

    def test_healthy_and_guarded_links_are_not_flagged(self):
        for title in ("Grafana", "Grafana Cloud"):
            item = next(i for i in self.search_full(title) if i["title"] == title)
            self.assertEqual(item["alert"], "")


class OmniWiringTest(unittest.TestCase):
    """页面挂点：入口、快捷键、标签筛选都在。"""

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_palette_markup_and_entry_point_exist(self):
        for hook in ('id="omniModal"', 'id="omniInput"', 'id="omniList"', 'id="omniOpen"'):
            self.assertIn(hook, self.html)

    def test_cmd_k_shortcut_is_bound(self):
        self.assertIn("event.metaKey || event.ctrlKey", self.html)
        self.assertIn("openOmni(", self.html)

    def test_tags_open_a_tag_scoped_search(self):
        self.assertIn("filterByTag(event,", self.html)
        self.assertIn("openOmni('#' + tag)", self.html)

    def test_styles_ship_with_the_markup(self):
        for rule in (".omni-row", ".omni-input", ".omni-trigger", ".tag-btn"):
            self.assertIn(rule, self.css)

    def test_palette_is_hidden_until_opened(self):
        # .modal-mask 默认 display:none，加 .show 才显示；面板不该默认可见。
        self.assertIn('class="modal-mask omni-mask" id="omniModal"', self.html)


if __name__ == "__main__":
    unittest.main()

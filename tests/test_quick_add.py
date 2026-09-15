# -*- coding: utf-8 -*-
"""快速收藏：PWA 分享目标 + 桌面书签小工具。

两个入口共用同一套查询参数（url / title，兼容把网址塞在 text 里的分享方），
参数只用于预填弹窗——误触分享不该悄悄往收藏库里塞东西。
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests._support import app_module  # noqa: F401  须早于 app 导入
from app import app  # noqa: E402

NODE = shutil.which("node")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "templates", "index.html")
SW_PATH = os.path.join(ROOT, "static", "sw.js")


class ShareTargetManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.manifest = json.loads(
            app.test_client().get("/static/manifest.webmanifest").get_data(as_text=True))

    def test_share_target_is_declared(self):
        target = self.manifest["share_target"]
        self.assertEqual(target["action"], "/")
        self.assertEqual(target["method"], "GET")
        self.assertEqual(target["params"], {"title": "title", "text": "text", "url": "url"})

    def test_action_is_inside_the_app_scope(self):
        # action 落在 scope 之外时浏览器会拒绝安装 share_target。
        self.assertTrue(target_in_scope(self.manifest))


def target_in_scope(manifest):
    return manifest["share_target"]["action"].startswith(manifest["scope"])


class ShareParamsBackendTest(unittest.TestCase):
    """分享参数由前端消费，后端只要照常把页面吐出来即可。"""

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_index_serves_normally_with_share_params(self):
        resp = self.client.get("/?title=Some+Page&url=https%3A%2F%2Fexample.com")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Bookmark Hub", resp.get_data(as_text=True))

    def test_share_params_are_not_reflected_into_the_page(self):
        # 参数只经 JS 读取并预填表单，绝不能被拼进 HTML——那就是个反射型 XSS。
        payload = "</script><img src=x onerror=alert(1)>"
        html = self.client.get("/", query_string={"title": payload, "url": payload}).get_data(as_text=True)
        self.assertNotIn("onerror=alert(1)", html)
        self.assertNotIn(payload, html)


class ServiceWorkerShareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SW_PATH, encoding="utf-8") as fh:
            cls.source = fh.read()

    def test_query_urls_are_never_cached(self):
        # 每次分享都是一个不同的 URL，写进缓存就等于分享一次多一条。
        self.assertIn("if (url.search) {", self.source)
        self.assertIn("caches.match('/')", self.source)

    def test_cache_version_was_bumped(self):
        self.assertIn("bh-shell-v2", self.source)

    @unittest.skipIf(NODE is None, "未安装 node，跳过语法检查")
    def test_parses(self):
        proc = subprocess.run([NODE, "--check", SW_PATH], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


@unittest.skipIf(NODE is None, "未安装 node，跳过参数解析验证")
class ShareParamParsingTest(unittest.TestCase):
    """pendingShare 是纯函数（只读 location.search），抽到 node 里验证各种分享形态。"""

    @classmethod
    def setUpClass(cls):
        with open(TEMPLATE, encoding="utf-8") as fh:
            blob = fh.read()
        start = blob.index("function pendingShare()")
        end = blob.index("function consumeShareParams()")
        cls.logic = blob[start:end]
        # 连同它依赖的两个小工具函数一起抽出来（模板里都是 4 空格缩进的顶层函数）。
        for name in ("function safeUrl(u)", "function normalizeUrlInput(s)"):
            head = blob.index(name)
            tail = blob.index("\n    }", head) + len("\n    }")
            cls.logic = blob[head:tail] + "\n" + cls.logic

    def parse(self, search):
        script = ("globalThis.location = { search: %s };\n" % json.dumps(search)
                  + self.logic
                  + "\nconsole.log(JSON.stringify(pendingShare()));")
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout.strip())
        finally:
            os.unlink(path)

    def test_plain_url_and_title(self):
        got = self.parse("?url=https%3A%2F%2Fexample.com%2Fa&title=Example")
        self.assertEqual(got, {"url": "https://example.com/a", "name": "Example"})

    def test_url_embedded_in_shared_text(self):
        # 不少应用只给 text，网址混在一段文字里。
        got = self.parse("?text=%E7%9C%8B%E7%9C%8B%E8%BF%99%E4%B8%AA%20https%3A%2F%2Fexample.com%2Fb")
        self.assertEqual(got["url"], "https://example.com/b")

    def test_bare_domain_gets_a_scheme(self):
        self.assertEqual(self.parse("?url=example.com")["url"], "https://example.com")

    def test_dangerous_scheme_is_rejected(self):
        # javascript: 混进预填框再被点开就是个 XSS。
        self.assertIsNone(self.parse("?url=javascript%3Aalert(1)"))

    def test_no_params_means_nothing_to_do(self):
        self.assertIsNone(self.parse(""))
        self.assertIsNone(self.parse("?title=OnlyATitle"))


class QuickAddUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_share_params_are_consumed_once_after_load(self):
        self.assertIn("SHARE_HANDLED", self.html)
        self.assertIn("consumeShareParams()", self.html)

    def test_query_string_is_cleaned_from_the_address_bar(self):
        # 否则刷新一次就再弹一次。
        self.assertIn("history.replaceState(null, '', location.pathname + location.hash)", self.html)

    def test_nothing_is_saved_automatically(self):
        # 只预填弹窗，保存仍然要用户按下保存键。
        start = self.html.index("function consumeShareParams()")
        body = self.html[start:start + 900]
        self.assertIn("openLinkModal(", body)
        self.assertNotIn("linkApi(", body)

    def test_locked_users_are_told_to_unlock(self):
        self.assertIn("请先在「系统设置」中解锁后再收藏", self.html)

    def test_bookmarklet_is_offered(self):
        self.assertIn('id="bookmarkletLink"', self.html)
        self.assertIn("function bookmarkletHref", self.html)
        self.assertIn(".bookmarklet", self.css)

    def test_bookmarklet_escapes_what_it_carries(self):
        self.assertIn("encodeURIComponent(location.href)", self.html)
        self.assertIn("encodeURIComponent(document.title)", self.html)

    def test_duplicate_hint_runs_on_prefill(self):
        start = self.html.index("function consumeShareParams()")
        self.assertIn("refreshDupHint()", self.html[start:start + 900])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""PWA：manifest / 图标 / Service Worker 的提供方式与缓存边界。

最关键的一条是缓存边界——Service Worker 绝不能缓存 /api/*：那些响应里有收藏与
签到配置，落进 CacheStorage 就等于在磁盘上多留一份不受管理密码保护的副本。
"""
import json
import os
import re
import shutil
import struct
import subprocess
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app  # noqa: E402

NODE = shutil.which("node")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SW_PATH = os.path.join(ROOT, "static", "sw.js")


def png_size(blob):
    """从 PNG 头部读出宽高，顺便验证它真的是 PNG。"""
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG"
    assert blob[12:16] == b"IHDR", "缺少 IHDR"
    return struct.unpack(">II", blob[16:24])


class ManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()
        cls.manifest = json.loads(cls.client.get("/static/manifest.webmanifest").get_data(as_text=True))

    def test_is_served_and_linked(self):
        resp = self.client.get("/static/manifest.webmanifest")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('rel="manifest"', self.client.get("/").get_data(as_text=True))

    def test_installable_fields_are_present(self):
        self.assertEqual(self.manifest["start_url"], "/")
        self.assertEqual(self.manifest["scope"], "/")
        self.assertEqual(self.manifest["display"], "standalone")
        self.assertTrue(self.manifest["name"])
        self.assertTrue(self.manifest["short_name"])

    def test_has_the_icon_sizes_browsers_require(self):
        sizes = {i["sizes"] for i in self.manifest["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        purposes = {i.get("purpose") for i in self.manifest["icons"]}
        self.assertIn("maskable", purposes)  # Android 自适应图标

    def test_every_declared_icon_actually_exists_at_the_declared_size(self):
        for entry in self.manifest["icons"]:
            resp = self.client.get(entry["src"])
            self.assertEqual(resp.status_code, 200, entry["src"])
            width, height = png_size(resp.get_data())
            self.assertEqual("%dx%d" % (width, height), entry["sizes"], entry["src"])

    def test_apple_touch_icon_is_served(self):
        resp = self.client.get("/static/apple-touch-icon.png")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(png_size(resp.get_data()), (180, 180))
        self.assertIn('rel="apple-touch-icon"', self.client.get("/").get_data(as_text=True))

    def test_theme_color_is_declared_for_both_schemes(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertEqual(html.count('name="theme-color"'), 2)


class ServiceWorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()
        with open(SW_PATH, encoding="utf-8") as fh:
            cls.source = fh.read()

    def test_served_from_root_with_root_scope(self):
        # 放在 /static/ 下作用域只有 /static/，盖不住首页；必须从根路径提供。
        resp = self.client.get("/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Service-Worker-Allowed"], "/")
        self.assertIn("javascript", resp.headers["Content-Type"])

    def test_sw_itself_is_not_cached(self):
        self.assertEqual(self.client.get("/sw.js").headers["Cache-Control"], "no-cache")

    def test_api_responses_are_never_cached(self):
        self.assertIn("url.pathname.startsWith('/api/')", self.source)
        # 兜底：缓存白名单只认 / 与 /static/
        self.assertIn("url.pathname === '/' || url.pathname.startsWith('/static/')", self.source)

    def test_only_same_origin_get_requests_are_handled(self):
        self.assertIn("request.method !== 'GET'", self.source)
        self.assertIn("url.origin !== self.location.origin", self.source)

    def test_old_cache_versions_are_purged_on_activate(self):
        self.assertIn("caches.delete", self.source)
        self.assertIn("CACHE_VERSION", self.source)

    def test_precache_failure_does_not_break_install(self):
        self.assertIn("cache.add(url).catch(", self.source)

    @unittest.skipIf(NODE is None, "未安装 node，跳过语法检查")
    def test_parses(self):
        proc = subprocess.run([NODE, "--check", SW_PATH], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class RegistrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.html = app.test_client().get("/").get_data(as_text=True)

    def test_registers_the_root_scoped_worker(self):
        self.assertIn("navigator.serviceWorker.register('/sw.js')", self.html)

    def test_registration_is_https_only(self):
        # http 下浏览器本来就不给注册（localhost 除外），显式判断免得控制台刷错。
        self.assertIn("location.protocol === 'https:'", self.html)

    def test_install_prompt_is_opt_in_and_remembered(self):
        self.assertIn("beforeinstallprompt", self.html)
        self.assertIn('id="installBtn"', self.html)
        self.assertIn("bh_install_done", self.html)

    def test_csp_allows_the_worker(self):
        # worker-src 回落到 script-src，其中必须含 'self'，否则 SW 注册会被 CSP 拦掉。
        csp = app.test_client().get("/").headers["Content-Security-Policy"]
        script_src = next(part for part in csp.split("; ") if part.startswith("script-src"))
        self.assertIn("'self'", script_src)


if __name__ == "__main__":
    unittest.main()

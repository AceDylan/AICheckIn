# -*- coding: utf-8 -*-
"""添加网址时自动取网页标题（2026-09-24 评审 P2）。"""
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, page_title_from_html  # noqa: E402


class PageTitleParseTest(unittest.TestCase):
    def test_title_is_unescaped_squashed_and_capped(self):
        html = b"<html><head><TITLE>\n  Grafana &amp; Friends\t- Home </TITLE></head>"
        self.assertEqual(page_title_from_html(html), "Grafana & Friends - Home")
        self.assertEqual(len(page_title_from_html(b"<title>" + b"x" * 200 + b"</title>")), 60)
        self.assertEqual(page_title_from_html(b"<html>no title</html>"), "")

    def test_charset_from_header_or_meta(self):
        gbk = "中文标题".encode("gbk")
        self.assertEqual(page_title_from_html(b"<title>" + gbk + b"</title>", "text/html; charset=gbk"), "中文标题")
        self.assertEqual(page_title_from_html(b'<meta charset="gbk"><title>' + gbk + b"</title>"), "中文标题")


class LinkTitleApiTest(StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super(LinkTitleApiTest, self).setUp()
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})
        self.client = app.test_client()
        real = app_module._favicon_http_get
        self.addCleanup(setattr, app_module, "_favicon_http_get", real)
        self.calls = []

        def fake(url, proxy, timeout, max_bytes, truncate=False):
            self.calls.append(url)
            if "down" in url:
                return None, "", "", 0
            if "img" in url:
                return b"\x89PNG", "image/png", url, 200
            return b"<title>My <b>Site</b></title>", "text/html; charset=utf-8", url, 200
        app_module._favicon_http_get = fake

    def test_returns_title_text_only(self):
        data = self.client.get("/api/link_title?url=https://example.com").get_json()
        self.assertEqual(data, {"ok": True, "title": "My <b>Site</b>"})   # 原样文字，页面按文本填入输入框
        self.assertEqual(self.calls, ["https://example.com"])

    def test_failures_and_bad_input(self):
        self.assertFalse(self.client.get("/api/link_title?url=https://down.example").get_json()["ok"])
        self.assertFalse(self.client.get("/api/link_title?url=https://img.example/a.png").get_json()["ok"])
        self.assertEqual(self.client.get("/api/link_title?url=javascript:alert(1)").status_code, 400)


if __name__ == "__main__":
    unittest.main()

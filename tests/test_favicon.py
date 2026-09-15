# -*- coding: utf-8 -*-
"""站点图标：origin 归一化、HTML 候选解析、魔数嗅探、/api/favicon 的白名单与正负缓存，
以及前端「成功才覆盖、失败回退首字母」的渲染挂点。"""
import json
import os
import re
import shutil
import tempfile
import unittest

os.environ["GYQD_SCHEDULER"] = "0"
os.environ.setdefault("GYQD_CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "config.json"))
os.environ["GYQD_ADMIN_PASSWORD"] = ""

import app as app_module  # noqa: E402
from app import app, favicon_candidates, favicon_origin, sniff_image_mime  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
ICO = b"\x00\x00\x01\x00" + b"fake-ico-body"


class OriginTest(unittest.TestCase):
    def test_normalizes_scheme_host_and_port(self):
        self.assertEqual(favicon_origin("https://Demo.Example/a/b?x=1#f"), "https://demo.example")
        self.assertEqual(favicon_origin("http://self.example:8080/panel"), "http://self.example:8080")
        # 默认端口不进 origin，避免同一站点被缓存成两份。
        self.assertEqual(favicon_origin("https://demo.example:443/"), "https://demo.example")
        self.assertEqual(favicon_origin("http://demo.example:80"), "http://demo.example")

    def test_rejects_non_http_and_garbage(self):
        for bad in ("", None, "ftp://a.example", "javascript:alert(1)", "file:///etc/passwd", "https://", "not a url"):
            self.assertEqual(favicon_origin(bad), "", bad)


class SniffTest(unittest.TestCase):
    def test_known_image_magics(self):
        self.assertEqual(sniff_image_mime(PNG), "image/png")
        self.assertEqual(sniff_image_mime(ICO), "image/x-icon")
        self.assertEqual(sniff_image_mime(b"GIF89a..."), "image/gif")
        self.assertEqual(sniff_image_mime(b"\xff\xd8\xff\xe0jpeg"), "image/jpeg")
        self.assertEqual(sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")
        self.assertEqual(sniff_image_mime(b'  <svg xmlns="http://www.w3.org/2000/svg"/>'), "image/svg+xml")

    def test_html_error_page_is_not_an_image(self):
        # 不少站点用 200 返回 HTML 错误页，只看 Content-Type 会把它当图标缓存下来。
        self.assertEqual(sniff_image_mime(b"<!DOCTYPE html><html><body>404</body></html>"), "")
        self.assertEqual(sniff_image_mime(b""), "")
        self.assertEqual(sniff_image_mime(None), "")


class CandidateTest(unittest.TestCase):
    def test_absolute_relative_and_protocol_relative(self):
        html = """<html><head>
          <link rel="stylesheet" href="/a.css">
          <link rel="icon" href="icons/rel.png">
          <link rel="icon" href="//cdn.example/proto.png">
          <link rel="shortcut icon" href="https://cdn.example/abs.png">
        </head><body><link rel="icon" href="/body-ignored.png"></body></html>"""
        urls = favicon_candidates(html, "https://demo.example/app/index.html")
        self.assertIn("https://demo.example/app/icons/rel.png", urls)
        self.assertIn("https://cdn.example/proto.png", urls)
        self.assertIn("https://cdn.example/abs.png", urls)
        self.assertNotIn("https://demo.example/a.css", urls)
        # </head> 之后的 link 不参与，避免把正文里的无关标签也抓进来。
        self.assertNotIn("https://demo.example/body-ignored.png", urls)

    def test_base_href_wins_for_relative_urls(self):
        html = '<head><base href="https://base.example/app/"><link rel="icon" href="i.png"></head>'
        self.assertEqual(favicon_candidates(html, "https://demo.example/x/y"), ["https://base.example/app/i.png"])

    def test_prefers_regular_icon_and_reasonable_size(self):
        html = """<head>
          <link rel="apple-touch-icon" href="/apple.png" sizes="180x180">
          <link rel="icon" href="/tiny.png" sizes="16x16">
          <link rel="icon" href="/good.png" sizes="64x64">
        </head>"""
        urls = favicon_candidates(html, "https://demo.example/")
        self.assertEqual(urls[0], "https://demo.example/good.png")
        # 16x16 太小，排在尺寸合适的 apple-touch-icon 之后。
        self.assertEqual(urls[-1], "https://demo.example/tiny.png")
        self.assertEqual(urls[1], "https://demo.example/apple.png")

    def test_skips_unusable_hrefs(self):
        html = """<head>
          <link rel="icon" href="">
          <link rel="icon" href="data:image/png;base64,AAAA">
          <link rel="icon" href="javascript:alert(1)">
          <link rel="icon" href="/ok.ico">
        </head>"""
        self.assertEqual(favicon_candidates(html, "https://demo.example/"), ["https://demo.example/ok.ico"])

    def test_no_link_tags_yields_nothing(self):
        self.assertEqual(favicon_candidates("<html><head><title>x</title></head></html>", "https://demo.example/"), [])


class FaviconApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def setUp(self):
        with open(app_module.CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump({
                "configs": [{"name": "签到站", "base_url": "https://cfg.example", "user_id": "1",
                             "access_token": "t", "enabled": True}],
                "bookmarks": [{"name": "看板站", "url": "https://demo.example/dash"}],
                "link_groups": [{"id": "daily", "name": "常用", "icon": "globe", "color": "mint", "links": [
                    {"id": "l1", "name": "内网面板", "url": "http://self.example:8080/panel"},
                ]}],
            }, fh, ensure_ascii=False)
        app_module._favicon_ctx["stamp"] = None          # 让 origin 白名单重新解析
        app_module._favicon_locks.clear()
        shutil.rmtree(app_module.FAVICON_DIR, ignore_errors=True)
        self.calls = []

    def tearDown(self):
        shutil.rmtree(app_module.FAVICON_DIR, ignore_errors=True)

    def _patch_http(self, mapping):
        calls = self.calls
        real = app_module._favicon_http_get

        def fake(url, proxy, timeout, max_bytes):
            calls.append(url)
            hit = mapping.get(url)
            return (hit[0], hit[1], url) if hit else (None, "", "")

        app_module._favicon_http_get = fake
        self.addCleanup(setattr, app_module, "_favicon_http_get", real)

    def test_rejects_invalid_url(self):
        self.assertEqual(self.client.get("/api/favicon?u=ftp://a.example").status_code, 400)
        self.assertEqual(self.client.get("/api/favicon").status_code, 400)

    def test_only_origins_present_in_config_are_fetched(self):
        self._patch_http({"https://evil.example/": (b"<html></html>", "text/html")})
        resp = self.client.get("/api/favicon?u=https://evil.example/x")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.calls, [], "不在配置里的站点不应发起任何外部请求")

    def test_fetches_declared_icon_and_caches_it(self):
        self._patch_http({
            "https://demo.example/": (b'<head><link rel="icon" href="/static/i.png"></head>', "text/html"),
            "https://demo.example/static/i.png": (PNG, "image/png"),
        })
        resp = self.client.get("/api/favicon?u=https://demo.example/dash")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "image/png")
        self.assertEqual(resp.get_data(), PNG)
        self.assertIn("max-age", resp.headers["Cache-Control"])
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        # 第三方内容（SVG 可内嵌脚本）直接访问时必须被 CSP 关死。
        self.assertIn("default-src 'none'", resp.headers["Content-Security-Policy"])

        fetched = list(self.calls)
        again = self.client.get("/api/favicon?u=https://demo.example/other")
        self.assertEqual(again.get_data(), PNG)
        self.assertEqual(self.calls, fetched, "命中磁盘缓存时不应再次抓取")

    def test_falls_back_to_favicon_ico(self):
        # 站点没有 <link rel=icon>：回落到约定俗成的 /favicon.ico。
        self._patch_http({
            "http://self.example:8080/": (b"<html><head><title>panel</title></head></html>", "text/html"),
            "http://self.example:8080/favicon.ico": (ICO, "image/vnd.microsoft.icon"),
        })
        resp = self.client.get("/api/favicon?u=http://self.example:8080/panel")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "image/x-icon")
        self.assertIn("http://self.example:8080/favicon.ico", self.calls)

    def test_non_image_payload_is_treated_as_missing(self):
        self._patch_http({
            "https://cfg.example/": (b'<head><link rel="icon" href="/i.png"></head>', "text/html"),
            "https://cfg.example/i.png": (b"<!DOCTYPE html><html>404</html>", "image/png"),
            "https://cfg.example/favicon.ico": (b"<!DOCTYPE html><html>404</html>", "text/html"),
        })
        self.assertEqual(self.client.get("/api/favicon?u=https://cfg.example").status_code, 404)

    def test_failure_is_negatively_cached(self):
        self._patch_http({})
        first = self.client.get("/api/favicon?u=https://demo.example/dash")
        self.assertEqual(first.status_code, 404)
        self.assertIn("max-age", first.headers["Cache-Control"])
        self.assertTrue(self.calls, "首次失败应当真的尝试过抓取")
        tried = list(self.calls)
        self.assertEqual(self.client.get("/api/favicon?u=https://demo.example/dash").status_code, 404)
        self.assertEqual(self.calls, tried, "失败结果应进负缓存，不要每次刷新都重抓")


class FaviconUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_all_site_slots_use_the_shared_avatar_helper(self):
        # 站点看板 / 收藏库网址 / 签到卡片 / 禁用行 / 服务配置行。
        for cls_name in ("'site-avatar'", "'link-avatar'", "'mini-avatar'"):
            self.assertIn("siteAvatarHtml(", self.html)
            self.assertIn(cls_name, self.html)
        self.assertEqual(self.html.count("siteAvatarHtml(b.name, b.url, 'site-avatar')"), 1)
        self.assertEqual(self.html.count("siteAvatarHtml(l.name, l.url, 'link-avatar', l.icon)"), 1)
        self.assertEqual(self.html.count("siteAvatarHtml(c.name, c.base_url, 'mini-avatar')"), 3)
        # 旧的「只有首字母」写法不应残留。
        self.assertNotIn('<div class="site-avatar" aria-hidden="true">', self.html)
        self.assertNotIn('<div class="link-avatar is-emoji"', self.html)

    def test_failed_icon_is_removed_instead_of_showing_broken_image(self):
        self.assertIn("if (state === 'ok') img.classList.add('is-ready'); else img.remove();", self.html)
        self.assertIn("FAVICON_MEMO.set(origin, state)", self.html)
        # 已知失败的站点不再渲染 <img>，避免每次重绘都打一次 404。
        self.assertIn("FAVICON_MEMO.get(origin) !== 'fail'", self.html)
        self.assertIn("img.dataset.favicon", self.html)

    def test_render_paths_kick_the_loader(self):
        self.assertGreaterEqual(self.html.count("pumpFavicons();"), 4)
        self.assertIn("FAVICON_PARALLEL = 3", self.html)

    def test_loader_releases_slots_taken_by_stale_elements(self):
        # 回归：重绘会摘掉还在加载的 <img>，它们的 load/error 可能永远不触发。早先的实现用
        # 计数器限流，这些「僵尸」把额度占死后，队列里剩下的图标就再也发不出去了。
        self.assertIn("for (const img of FAVICON_INFLIGHT) if (!img.isConnected) FAVICON_INFLIGHT.delete(img);", self.html)
        self.assertIn("FAVICON_INFLIGHT.size < FAVICON_PARALLEL", self.html)
        self.assertIn("const timer = setTimeout(() => done(img.isConnected ? 'fail' : ''), FAVICON_TIMEOUT_MS);", self.html)
        self.assertIn("if (settled) return;", self.html)

    def test_icons_are_not_lazy_loaded(self):
        # 回归：未激活的视图是 display:none，浏览器会把里面 loading="lazy" 的图片无限期挂起，
        # 于是这些永远不会 load/error 的元素把限流队列占死，可见页面的图标也发不出去。
        self.assertNotIn('class="avatar-img" data-favicon="${escapeHtml(origin)}" alt="" loading="lazy"', self.html)
        self.assertIn('data-favicon="${escapeHtml(origin)}" alt="" decoding="async"', self.html)
        self.assertIn("else if (img.offsetParent) FAVICON_QUEUE.push(img);", self.html)

    def test_user_emoji_still_wins_over_fetched_icon(self):
        self.assertIn("const origin = emoji ? '' : siteOrigin(url);", self.html)

    def test_icon_overlay_cannot_disturb_layout(self):
        rule = re.search(r"\.avatar-img\s*\{([^}]*)\}", self.css).group(1)
        self.assertIn("position: absolute", rule)
        self.assertIn("object-fit: contain", rule)
        self.assertIn("opacity: 0", rule)
        self.assertIn(".avatar-img.is-ready { opacity: 1; }", self.css)
        # 图标就位后隐藏首字母回退层（相邻兄弟选择器，img 必须排在文本之前）。
        self.assertIn(".avatar-img.is-ready ~ .avatar-text { opacity: 0; }", self.css)
        for selector in (r"\.site-avatar\s*\{([^}]*)\}", r"\.link-avatar\s*\{([^}]*)\}", r"\.mini-avatar\s*\{([^}]*)\}"):
            body = re.search(selector, self.css).group(1)
            self.assertIn("position: relative", body)
            self.assertIn("overflow: hidden", body)


if __name__ == "__main__":
    unittest.main()

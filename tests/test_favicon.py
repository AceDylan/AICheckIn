# -*- coding: utf-8 -*-
"""站点图标：origin 归一化、HTML 候选解析、魔数嗅探、/api/favicon 的白名单与正负缓存，
以及前端「成功才覆盖、失败回退首字母」的渲染挂点。"""
import json
import re
import shutil
import time
import unittest
import urllib.error
import urllib.request

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, favicon_candidates, favicon_origin, sniff_image_mime  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
ICO = b"\x00\x00\x01\x00" + b"fake-ico-body"


class _FakeHTTPResponse(object):
    """够 _favicon_urllib_get 用的最小响应对象：支持 with、按字节数 read、headers、geturl。"""

    def __init__(self, body, ctype, url):
        self._body = body
        self._pos = 0
        self._url = url
        self.status = 200
        self.headers = {"Content-Type": ctype}

    def read(self, amount=None):
        end = len(self._body) if amount is None else min(len(self._body), self._pos + amount)
        chunk = self._body[self._pos:end]
        self._pos = end
        return chunk

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener(object):
    """伪造 urllib 的 opener：让用例跑到真正的 _favicon_urllib_get（体积上限就在那里判）。"""

    def __init__(self, mapping, calls):
        self.mapping = mapping
        self.calls = calls

    def open(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        hit = self.mapping.get(url)
        if hit is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return _FakeHTTPResponse(hit[0], hit[1], url)


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

    def test_prefers_the_sharpest_icon(self):
        # 首页图标放大到 60px 左右：清晰度优先，180px 的 apple-touch-icon 排在 64px 的常规图标之前，
        # 16x16 垫底（放大后会糊）。
        html = """<head>
          <link rel="icon" href="/tiny.png" sizes="16x16">
          <link rel="icon" href="/good.png" sizes="64x64">
          <link rel="apple-touch-icon" href="/apple.png" sizes="180x180">
        </head>"""
        urls = favicon_candidates(html, "https://demo.example/")
        self.assertEqual(urls, ["https://demo.example/apple.png", "https://demo.example/good.png",
                                "https://demo.example/tiny.png"])

    def test_unsized_touch_icon_counts_as_180px(self):
        # 多数站点的 apple-touch-icon 不写 sizes，约定就是 180px：必须排在没写尺寸 / 32px 的 favicon 之前。
        html = """<head>
          <link rel="shortcut icon" href="/favicon.ico">
          <link rel="icon" href="/f32.png" sizes="32x32">
          <link rel="apple-touch-icon" href="/touch.png">
        </head>"""
        self.assertEqual(favicon_candidates(html, "https://demo.example/")[0], "https://demo.example/touch.png")

    def test_vector_and_large_bitmaps_outrank_touch_icon_only_when_sharper(self):
        score = app_module._favicon_link_score
        touch = score({"rel": "apple-touch-icon"})
        self.assertGreater(score({"rel": "icon", "type": "image/svg+xml"}), touch)
        self.assertGreater(score({"rel": "icon", "sizes": "192x192"}), touch)
        self.assertLess(score({"rel": "icon", "sizes": "96x96"}), touch)
        self.assertLess(score({"rel": "icon"}), touch)
        # 超大位图（容易超过体积上限）不如 120~256px 的合适尺寸。
        self.assertLess(score({"rel": "icon", "sizes": "1024x1024"}), score({"rel": "icon", "sizes": "192x192"}))
        # 单色 mask-icon 永远垫底。
        self.assertLess(score({"rel": "mask-icon", "sizes": "any"}), score({"rel": "icon", "sizes": "16x16"}))

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


class FaviconApiTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def setUp(self):
        super(FaviconApiTest, self).setUp()
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

        def fake(url, proxy, timeout, max_bytes, truncate=False):
            calls.append(url)
            hit = mapping.get(url)
            return (hit[0], hit[1], url, 200) if hit else (None, "", "", 404)

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

    def test_unchanged_icon_revalidates_with_a_304(self):
        """一天的新鲜期过后浏览器会回来问：图标没变就别整份重传，首页上有上百个。"""
        self._patch_http({
            "https://demo.example/": (b'<head><link rel="icon" href="/static/i.png"></head>', "text/html"),
            "https://demo.example/static/i.png": (PNG, "image/png"),
        })
        first = self.client.get("/api/favicon?u=https://demo.example/")
        self.assertTrue(first.headers.get("ETag"))
        again = self.client.get("/api/favicon?u=https://demo.example/", headers={"If-None-Match": first.headers["ETag"]})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.get_data(), b"")
        self.assertIn("max-age", again.headers["Cache-Control"])       # 304 把新鲜期续上
        self.assertIn("default-src 'none'", again.headers["Content-Security-Policy"])
        stale = self.client.get("/api/favicon?u=https://demo.example/", headers={"If-None-Match": '"someone-else"'})
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.get_data(), PNG)

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

    def test_waf_blocked_site_is_retried_with_browser_fingerprint(self):
        # Cloudflare 一类 WAF 会因为 urllib 的 TLS 指纹直接 403（线上 linux.do / aihub.top 即如此），
        # 这时换 curl_cffi 的 Chrome 指纹再试一次。
        curl_calls = []
        real_urllib = app_module._favicon_urllib_get
        real_curl = app_module._favicon_curl_get

        def fake_urllib(url, proxy, timeout, max_bytes, truncate=False):
            return (None, "", "", 403)

        def fake_curl(url, proxy, timeout, max_bytes, truncate=False):
            curl_calls.append(url)
            body = (PNG, "image/png") if url.endswith("/favicon.ico") else (b"<html></html>", "text/html")
            return body[0], body[1], url, 200

        app_module._favicon_urllib_get = fake_urllib
        app_module._favicon_curl_get = fake_curl
        self.addCleanup(setattr, app_module, "_favicon_urllib_get", real_urllib)
        self.addCleanup(setattr, app_module, "_favicon_curl_get", real_curl)
        resp = self.client.get("/api/favicon?u=https://demo.example/dash")
        self.assertEqual(resp.status_code, 200, resp.get_data()[:200])
        self.assertEqual(resp.get_data(), PNG)
        self.assertIn("https://demo.example/favicon.ico", curl_calls)

    def test_connection_failures_do_not_pay_for_a_second_attempt(self):
        # 连不上 / 超时（status 0）不重试，否则死站的等待时间会翻倍。
        curl_calls = []
        real_urllib = app_module._favicon_urllib_get
        real_curl = app_module._favicon_curl_get
        app_module._favicon_urllib_get = lambda *a: (None, "", "", 0)
        app_module._favicon_curl_get = lambda url, *a: curl_calls.append(url) or (None, "", "", 0)
        self.addCleanup(setattr, app_module, "_favicon_urllib_get", real_urllib)
        self.addCleanup(setattr, app_module, "_favicon_curl_get", real_curl)
        self.assertEqual(self.client.get("/api/favicon?u=https://demo.example/dash").status_code, 404)
        self.assertEqual(curl_calls, [])

    def test_oversized_payload_is_skipped_but_large_logos_still_fit(self):
        # 线上 catcard.uk 拿 267KB 的 Logo.png 当 favicon，旧的 256KB 上限会把它整个丢掉。
        self.assertGreaterEqual(app_module.FAVICON_MAX_BYTES, 300 * 1024)
        self._patch_http({
            "https://demo.example/": (b'<head><link rel="icon" href="/logo.png"></head>', "text/html"),
            "https://demo.example/logo.png": (PNG + b"x" * (300 * 1024), "image/png"),
        })
        resp = self.client.get("/api/favicon?u=https://demo.example/dash")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_data()), len(PNG) + 300 * 1024)

    def test_unreachable_origin_is_not_probed_twice(self):
        # 连不上的 origin（内网地址 / 域名不存在）不该再去试 /favicon.ico，否则要多等一个超时。
        calls = self.calls
        real = app_module._favicon_http_get
        app_module._favicon_http_get = lambda url, *a, **kw: calls.append(url) or (None, "", "", 0)
        self.addCleanup(setattr, app_module, "_favicon_http_get", real)
        self.assertEqual(self.client.get("/api/favicon?u=http://self.example:8080/panel").status_code, 404)
        self.assertEqual(calls, ["http://self.example:8080/"])

    def test_conventional_touch_icon_beats_a_small_declared_favicon(self):
        # 站点只声明了 32px 的 favicon，但根目录放着 apple-touch-icon.png：拿高清的那张。
        self._patch_http({
            "https://demo.example/": (b'<head><link rel="icon" href="/f32.png" sizes="32x32"></head>', "text/html"),
            "https://demo.example/f32.png": (ICO, "image/x-icon"),
            "https://demo.example/apple-touch-icon.png": (PNG, "image/png"),
        })
        resp = self.client.get("/api/favicon?u=https://demo.example/dash")
        self.assertEqual(resp.get_data(), PNG)
        self.assertNotIn("https://demo.example/f32.png", self.calls)

    def test_declared_hires_icon_skips_the_conventional_probe(self):
        # 已经声明了够清晰的图标就别再多探一次约定路径，省一个请求。
        self._patch_http({
            "https://demo.example/": (b'<head><link rel="icon" href="/i192.png" sizes="192x192"></head>', "text/html"),
            "https://demo.example/i192.png": (PNG, "image/png"),
        })
        self.assertEqual(self.client.get("/api/favicon?u=https://demo.example/dash").status_code, 200)
        self.assertEqual(self.calls, ["https://demo.example/", "https://demo.example/i192.png"])

    def test_missing_conventional_icon_still_falls_back(self):
        self._patch_http({
            "https://demo.example/": (b'<head><link rel="icon" href="/f.ico"></head>', "text/html"),
            "https://demo.example/f.ico": (ICO, "image/x-icon"),
        })
        resp = self.client.get("/api/favicon?u=https://demo.example/dash")
        self.assertEqual(resp.mimetype, "image/x-icon")
        self.assertEqual(self.calls, ["https://demo.example/", "https://demo.example/apple-touch-icon.png",
                                      "https://demo.example/f.ico"])

    def _patch_transport(self, mapping):
        """比 _patch_http 低一层：保留 app 自己的体积上限判断，只把网络换掉。"""
        calls = self.calls
        real = urllib.request.build_opener
        urllib.request.build_opener = lambda *handlers: _FakeOpener(mapping, calls)
        self.addCleanup(setattr, urllib.request, "build_opener", real)

    def test_huge_homepage_still_yields_the_icon_declared_in_its_head(self):
        """回归：首页 HTML 超过 FAVICON_HTML_MAX_BYTES 时曾被整份丢弃，连 <head> 里写好的图标都用不上。"""
        body = (b'<html><head><link rel="icon" sizes="192x192" href="/static/i.png"></head><body>'
                + b"x" * (app_module.FAVICON_HTML_MAX_BYTES + 64 * 1024) + b"</body></html>")
        self.assertGreater(len(body), app_module.FAVICON_HTML_MAX_BYTES)
        self._patch_transport({
            "https://demo.example/": (body, "text/html"),
            "https://demo.example/static/i.png": (PNG, "image/png"),
        })
        resp = self.client.get("/api/favicon?u=https://demo.example/dash")
        self.assertEqual(resp.status_code, 200, resp.get_data()[:200])
        self.assertEqual(resp.get_data(), PNG)
        self.assertIn("https://demo.example/static/i.png", self.calls,
                      "首页超限时应当按上限截断后继续解析 <head>，而不是退化成只试约定路径")

    def test_oversized_icon_body_is_still_refused_instead_of_truncated(self):
        """截断只给首页 HTML 用：图标本体超限必须整份拒收，截一半就是坏图。"""
        huge = PNG + b"x" * app_module.FAVICON_MAX_BYTES
        self._patch_transport({
            "https://demo.example/": (b'<head><link rel="icon" sizes="192x192" href="/huge.png"></head>', "text/html"),
            "https://demo.example/huge.png": (huge, "image/png"),
            "https://demo.example/favicon.ico": (ICO, "image/x-icon"),
        })
        resp = self.client.get("/api/favicon?u=https://demo.example/dash")
        self.assertEqual(resp.status_code, 200, resp.get_data()[:200])
        self.assertIn("https://demo.example/huge.png", self.calls)
        self.assertEqual(resp.get_data(), ICO, "超限的图标不该被截断后当成图标返回")

    def _write_legacy_cache(self, origin, data):
        key = app_module._favicon_key(origin)
        app_module.FAVICON_DIR.mkdir(parents=True, exist_ok=True)
        (app_module.FAVICON_DIR / (key + ".bin")).write_bytes(data)
        (app_module.FAVICON_DIR / (key + ".json")).write_text(json.dumps(
            {"origin": origin, "ok": True, "mime": "image/x-icon", "fetched_at": time.time()}), encoding="utf-8")

    def test_cache_from_the_old_picking_strategy_is_refetched(self):
        # 升级前缓存的是低清 favicon（元数据里没有 v）：不必等 7 天过期，下一次请求就按新策略重抓。
        self._write_legacy_cache("https://demo.example", ICO)
        self._patch_http({
            "https://demo.example/": (b'<head><link rel="apple-touch-icon" href="/touch.png"></head>', "text/html"),
            "https://demo.example/touch.png": (PNG, "image/png"),
        })
        self.assertEqual(self.client.get("/api/favicon?u=https://demo.example/dash").get_data(), PNG)
        tried = list(self.calls)
        self.assertEqual(self.client.get("/api/favicon?u=https://demo.example/dash").get_data(), PNG)
        self.assertEqual(self.calls, tried, "重抓后的新缓存应当直接命中")

    def test_old_icon_survives_a_failed_refetch(self):
        # 重抓失败（站点临时挂了）不能把原来好好的图标弄丢。
        self._write_legacy_cache("https://demo.example", ICO)
        self._patch_http({})
        resp = self.client.get("/api/favicon?u=https://demo.example/dash")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_data(), ICO)

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
        self.assertEqual(self.html.count("siteAvatarHtml(l.name, l.url, 'link-avatar', l.icon, l.custom_icon)"), 1)
        self.assertEqual(self.html.count("siteAvatarHtml(c.name, c.base_url, 'mini-avatar')"), 3)
        # 旧的「只有首字母」写法不应残留。
        self.assertNotIn('<div class="site-avatar" aria-hidden="true">', self.html)
        self.assertNotIn('<div class="link-avatar is-emoji"', self.html)

    def test_failed_icon_is_removed_instead_of_showing_broken_image(self):
        self.assertIn("if (state === 'ok') { img.classList.add('is-ready'); markIconResolution(img); markIconTone(img); } else img.remove();", self.html)
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

    def test_auto_icon_is_tried_even_with_a_text_fallback(self):
        self.assertIn("const origin = siteOrigin(url);", self.html)

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

# -*- coding: utf-8 -*-
"""传输层：文本响应的 gzip、首页外壳的 ETag / 304、内置壁纸的新鲜期。

起始页每开一个标签页就要一次外壳。gunicorn 直接对外时没人替它压缩，首页 HTML 也没有校验头，
Service Worker 的每次后台刷新都是整份重传——这些用例守住「内容没变就别再传一遍」。"""
import gzip
import json
import unittest

from tests._support import StoreIsolationMixin, app_module
from app import app  # noqa: E402

GZIP = {"Accept-Encoding": "gzip, deflate, br"}


def big_store(n=60):
    links = [{"id": "l%d" % i, "name": "站点 %d" % i, "url": "https://site-%d.example/path" % i,
              "desc": "用来把响应撑过压缩门槛的说明文字", "icon": "", "tags": ["标签"], "pinned": False}
             for i in range(n)]
    return {"configs": [{"name": "签到站", "base_url": "https://checkin.example", "user_id": "7",
                         "access_token": "sk-" + "s3cret" * 300, "enabled": True}],
            "bookmarks": [{"name": "看板站", "url": "https://dash.example", "fields": [
                {"id": "f1", "label": "余额", "type": "amount", "enabled": True,
                 "curl": "curl https://dash.example/api -H 'Authorization: Bearer " + "t0ken" * 300 + "'",
                 "path": "data.balance"}]}],
            "link_groups": [{"id": "daily", "name": "常用", "icon": "globe", "color": "mint", "links": links}]}


class TransportCase(StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super(TransportCase, self).setUp()
        self.write_config(big_store())
        self.client = app.test_client()
        saved = app_module.ADMIN_PASSWORD
        app_module.ADMIN_PASSWORD = ""          # 本组用例不关心鉴权：按「未设密码」的本地部署来
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", saved)
        app_module._gzip_cache.clear()

    def fetch(self, path, headers=None, method="get"):
        """取回响应并读完、关掉（静态文件是文件流，不关会留下句柄）。返回 (响应, 字节)。"""
        resp = getattr(self.client, method)(path, headers=headers or {})
        data = resp.get_data()
        resp.close()
        return resp, data


class ShellRevalidationTest(TransportCase):
    def test_index_carries_a_validator_and_must_revalidate(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers.get("ETag"))
        # no-cache = 可以留着，但每次都得回来问；外壳里没有任何数据，留着不泄漏什么。
        self.assertEqual(resp.headers["Cache-Control"], "no-cache")
        self.assertIn("<title>", resp.get_data(as_text=True))

    def test_unchanged_shell_is_a_304_without_a_body(self):
        etag = self.client.get("/").headers["ETag"]
        again = self.client.get("/", headers={"If-None-Match": etag})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.get_data(), b"")
        self.assertEqual(again.headers["Cache-Control"], "no-cache")
        self.assertIn("Accept-Encoding", again.headers.get("Vary", ""))   # 304 和它对应的 200 说法一致

    def test_the_weak_etag_of_the_gzipped_shell_revalidates_too(self):
        first = self.client.get("/", headers=GZIP)
        self.assertTrue(first.headers["ETag"].startswith('W/"'), first.headers["ETag"])
        again = self.client.get("/", headers=dict(GZIP, **{"If-None-Match": first.headers["ETag"]}))
        self.assertEqual(again.status_code, 304)

    def test_share_target_urls_revalidate_like_the_bare_shell(self):
        etag = self.client.get("/").headers["ETag"]
        self.assertEqual(self.client.get("/?url=https%3A%2F%2Fa.example", headers={"If-None-Match": etag}).status_code, 304)

    def test_a_stale_validator_gets_the_full_page(self):
        resp = self.client.get("/", headers={"If-None-Match": '"0000"'})
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.get_data()), 10000)

    def test_etag_follows_the_content(self):
        one, two = app_module._shell_etag(b"<html>one</html>"), app_module._shell_etag(b"<html>two</html>")
        self.assertNotEqual(one, two)
        self.assertEqual(app_module._shell_etag(b"<html>one</html>"), one)

    def test_security_headers_survive_on_the_shell(self):
        resp = self.client.get("/", headers=GZIP)
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", resp.headers["Content-Security-Policy"])


class GzipTest(TransportCase):
    def test_shell_is_compressed_only_when_the_client_asks(self):
        plain = self.client.get("/")
        self.assertNotIn("Content-Encoding", plain.headers)
        self.assertIn("Accept-Encoding", plain.headers.get("Vary", ""))   # 两种表示，缓存必须分开存
        packed = self.client.get("/", headers=GZIP)
        self.assertEqual(packed.headers["Content-Encoding"], "gzip")
        self.assertIn("Accept-Encoding", packed.headers["Vary"])
        self.assertEqual(int(packed.headers["Content-Length"]), len(packed.get_data()))
        self.assertLess(len(packed.get_data()), len(plain.get_data()) * 0.4)
        self.assertEqual(gzip.decompress(packed.get_data()), plain.get_data())

    def test_explicit_refusal_is_respected(self):
        for value in ("identity", "gzip;q=0", "br"):
            resp = self.client.get("/", headers={"Accept-Encoding": value})
            self.assertNotIn("Content-Encoding", resp.headers, value)

    def test_stylesheet_is_compressed_and_still_revalidates(self):
        _, raw = self.fetch("/static/app-v3.css")
        packed, body = self.fetch("/static/app-v3.css", GZIP)
        self.assertEqual(packed.headers["Content-Encoding"], "gzip")
        self.assertEqual(int(packed.headers["Content-Length"]), len(body))
        self.assertEqual(gzip.decompress(body), raw)
        self.assertTrue(packed.headers["ETag"].startswith('W/"'))
        again, _ = self.fetch("/static/app-v3.css", dict(GZIP, **{"If-None-Match": packed.headers["ETag"]}))
        self.assertEqual(again.status_code, 304)

    def test_service_worker_keeps_its_headers(self):
        resp, body = self.fetch("/sw.js", GZIP)
        self.assertEqual(resp.headers["Content-Encoding"], "gzip")
        self.assertEqual(resp.headers["Cache-Control"], "no-cache")
        self.assertEqual(resp.headers["Service-Worker-Allowed"], "/")
        self.assertIn("CACHE_VERSION", gzip.decompress(body).decode("utf-8"))

    def test_compressed_bytes_are_cached_per_validator(self):
        first = self.fetch("/static/app-v3.css", GZIP)[1]
        self.assertEqual([key[0] for key in app_module._gzip_cache], ["/static/app-v3.css"])
        key = next(iter(app_module._gzip_cache))
        self.assertIs(app_module._gzip_cache_get(key), app_module._gzip_cache[key])
        # 第二次直接用缓存：把缓存换成一段记号，响应里出来的就该是这段记号。
        app_module._gzip_cache[key] = gzip.compress(b"from-cache" * 200)
        self.assertEqual(gzip.decompress(self.fetch("/static/app-v3.css", GZIP)[1]), b"from-cache" * 200)
        app_module._gzip_cache.clear()
        self.assertEqual(self.fetch("/static/app-v3.css", GZIP)[1], first)

    def test_cache_cannot_grow_without_bound(self):
        for i in range(app_module._GZIP_CACHE_MAX * 2 + 3):
            app_module._gzip_cache_put(("/x", str(i)), b"x")
        self.assertLessEqual(len(app_module._gzip_cache), app_module._GZIP_CACHE_MAX)

    def test_api_json_is_compressed_but_never_cached(self):
        plain = self.client.get("/api/configs")
        packed = self.client.get("/api/configs", headers=GZIP)
        self.assertGreater(len(plain.get_data()), app_module.GZIP_MIN_BYTES)
        self.assertEqual(packed.headers["Content-Encoding"], "gzip")
        self.assertEqual(packed.headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(gzip.decompress(packed.get_data())), plain.get_json())
        self.assertEqual(app_module._gzip_cache, {}, "接口数据不该留在压缩缓存里")

    def test_tiny_responses_are_left_alone(self):
        resp = self.client.get("/api/health", headers=GZIP)
        self.assertNotIn("Content-Encoding", resp.headers)
        self.assertEqual(resp.get_json(), {"ok": True})

    def test_responses_with_real_credentials_are_never_compressed(self):
        """压缩 + 可观测的密文长度是 BREACH 一类攻击的前提：会回真实凭据的接口一律不压。"""
        for path in ("/api/configs/0/secret", "/api/bookmarks/0/secret", "/api/configs/export"):
            resp = self.client.get(path, headers=GZIP)
            self.assertEqual(resp.status_code, 200, path)
            self.assertGreater(len(resp.get_data()), app_module.GZIP_MIN_BYTES, path)
            self.assertNotIn("Content-Encoding", resp.headers, path)

    def test_every_secret_endpoint_is_on_the_skip_list(self):
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
        secret = {name for name in endpoints if name.endswith("_secret")} | {"api_export"}
        self.assertTrue(secret <= endpoints)
        self.assertEqual(secret, set(app_module._GZIP_SKIP_ENDPOINTS))

    def test_images_partial_and_head_responses_pass_through(self):
        icon, _ = self.fetch("/static/icon-192.png", GZIP)
        self.assertNotIn("Content-Encoding", icon.headers)
        part, body = self.fetch("/static/app-v3.css", dict(GZIP, Range="bytes=0-99"))
        self.assertEqual(part.status_code, 206)
        self.assertNotIn("Content-Encoding", part.headers)
        self.assertEqual(len(body), 100)
        head, _ = self.fetch("/", GZIP, method="head")
        self.assertNotIn("Content-Encoding", head.headers)

    def test_error_pages_are_not_touched(self):
        resp = self.client.get("/api/nope", headers=GZIP)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("Content-Encoding", resp.headers)


class WallpaperFreshnessTest(TransportCase):
    def test_builtin_wallpapers_get_a_day_of_freshness(self):
        resp, _ = self.fetch("/static/wallpapers/aurora.webp")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=86400")
        again, _ = self.fetch("/static/wallpapers/aurora.webp", {"If-None-Match": resp.headers["ETag"]})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.headers["Cache-Control"], "public, max-age=86400")   # 304 也要续上新鲜期

    def test_other_static_files_still_revalidate_every_time(self):
        for path in ("/static/app-v3.css", "/static/manifest.webmanifest"):
            self.assertEqual(self.fetch(path)[0].headers["Cache-Control"], "no-cache", path)
        missing, _ = self.fetch("/static/wallpapers/nope.webp")
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn("max-age", missing.headers.get("Cache-Control", ""))


if __name__ == "__main__":
    unittest.main()

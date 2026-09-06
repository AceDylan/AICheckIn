# -*- coding: utf-8 -*-
"""管理密码解锁的会话持久化：解锁后下发 HttpOnly Cookie，浏览器重开仍保持解锁。"""
import hashlib
import hmac
import os
import pathlib
import re
import tempfile
import time
import unittest

import app as app_module
from app import app

PASSWORD = "unit-test-pwd-9f3a"


class AdminSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        # 模块级 ADMIN_PASSWORD 在导入时读环境变量；测试期直接改模块全局。
        self._orig_password = app_module.ADMIN_PASSWORD
        self._orig_secret_file = app_module.SESSION_SECRET_FILE
        self._orig_secret_cache = app_module._session_secret_cache
        self._tmpdir = tempfile.TemporaryDirectory()
        app_module.ADMIN_PASSWORD = PASSWORD
        app_module.SESSION_SECRET_FILE = pathlib.Path(self._tmpdir.name) / ".session_secret"
        app_module._session_secret_cache = None

    def tearDown(self):
        app_module.ADMIN_PASSWORD = self._orig_password
        app_module.SESSION_SECRET_FILE = self._orig_secret_file
        app_module._session_secret_cache = self._orig_secret_cache
        self._tmpdir.cleanup()

    # ---- 工具 ----

    def _unlock(self, https=True):
        """解锁一次，返回 (Set-Cookie 原文, 令牌值)。"""
        headers = {"X-Admin-Password": PASSWORD}
        if https:
            headers["X-Forwarded-Proto"] = "https"
        response = app.test_client().post("/api/auth", headers=headers)
        self.assertTrue(response.get_json()["ok"])
        raw = next(h for h in response.headers.getlist("Set-Cookie")
                   if h.startswith(app_module.SESSION_COOKIE + "="))
        return raw, raw.split("=", 1)[1].split(";")[0]

    def _browser(self, token=None):
        """一个新开的浏览器；token 为它从磁盘恢复的持久 Cookie。"""
        client = app.test_client()
        if token:
            client.set_cookie(app_module.SESSION_COOKIE, token)
        return client

    # ---- 用例 ----

    def test_locked_without_credentials(self):
        response = self._browser().get("/api/configs")
        self.assertFalse(response.get_json()["admin_unlocked"])
        self.assertEqual(response.headers.getlist("Set-Cookie"), [])

    def test_wrong_password_issues_no_cookie(self):
        response = app.test_client().post("/api/auth", headers={"X-Admin-Password": "wrong"})
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(response.headers.getlist("Set-Cookie"), [])

    def test_cookie_is_persistent_and_hardened(self):
        raw, _ = self._unlock()
        self.assertIn("HttpOnly", raw)      # JS 读不到，XSS 无法直接窃取
        self.assertIn("SameSite=Lax", raw)  # 跨站写请求不携带该 Cookie（CSRF）
        self.assertIn("Secure", raw)        # X-Forwarded-Proto: https
        # 有 Max-Age 才是持久 Cookie；没有则退化为关浏览器即失效的会话 Cookie。
        self.assertEqual(int(re.search(r"Max-Age=(\d+)", raw).group(1)), app_module.SESSION_MAX_AGE)

    def test_plain_http_cookie_is_not_secure_flagged(self):
        raw, _ = self._unlock(https=False)
        self.assertNotIn("Secure", raw)

    def test_cookie_survives_browser_restart(self):
        _, token = self._unlock()
        response = self._browser(token).get("/api/configs")
        self.assertTrue(response.get_json()["admin_unlocked"])

    def test_cookie_authorises_protected_writes(self):
        _, token = self._unlock()
        self.assertEqual(self._browser(token).put("/api/settings", json={"proxy_url": ""}).status_code, 200)
        self.assertEqual(self._browser().put("/api/settings", json={"proxy_url": ""}).status_code, 403)

    def test_forged_or_expired_tokens_are_rejected(self):
        _, token = self._unlock()
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        for bad in (tampered, "9999999999.deadbeef", "", "abc", "not-a-number.abc"):
            self.assertFalse(app_module._session_token_ok(bad), bad)
        expired = str(int(time.time()) - 10)
        signature = hmac.new(app_module._session_key(), expired.encode(), hashlib.sha256).hexdigest()
        self.assertFalse(app_module._session_token_ok(expired + "." + signature))

    def test_password_change_invalidates_old_cookies(self):
        _, token = self._unlock()
        app_module.ADMIN_PASSWORD = "rotated-password"
        self.assertFalse(self._browser(token).get("/api/configs").get_json()["admin_unlocked"])

    def test_logout_clears_cookie(self):
        _, token = self._unlock()
        client = self._browser(token)
        response = client.post("/api/logout")
        self.assertTrue(response.get_json()["ok"])
        raw = next(h for h in response.headers.getlist("Set-Cookie")
                   if h.startswith(app_module.SESSION_COOKIE + "="))
        self.assertIn("Max-Age=0", raw)
        self.assertFalse(client.get("/api/configs").get_json()["admin_unlocked"])

    def test_password_header_still_accepted(self):
        response = self._browser().get("/api/configs", headers={"X-Admin-Password": PASSWORD})
        self.assertTrue(response.get_json()["admin_unlocked"])

    def test_secret_is_persisted_with_owner_only_permissions(self):
        self._unlock()
        self.assertTrue(app_module.SESSION_SECRET_FILE.is_file())
        self.assertEqual(os.stat(app_module.SESSION_SECRET_FILE).st_mode & 0o777, 0o600)

    def test_open_deployment_without_password_needs_no_cookie(self):
        app_module.ADMIN_PASSWORD = ""
        response = app.test_client().post("/api/auth")
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(response.headers.getlist("Set-Cookie"), [])
        self.assertTrue(self._browser().get("/api/configs").get_json()["admin_unlocked"])

    def test_frontend_never_stores_the_password(self):
        html = app.test_client().get("/").get_data(as_text=True)
        self.assertNotIn("sessionStorage", html)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("gyqd_admin", html)


if __name__ == "__main__":
    unittest.main()

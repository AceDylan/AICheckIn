# -*- coding: utf-8 -*-
"""开放接口的凭据暴露面：/api/configs 只下发脱敏视图，管理动作一律需要解锁，
管理密码有防爆破限速，响应带基础安全头。

背景：首页 / 与 /api/configs 是公网可直接访问的，此前 /api/configs 会把收藏站点的
完整 fields（含 Authorization 请求头与整段 curl）、proxy_url 原样返回，
/api/checkin、/api/test/<idx>、/api/history 也完全开放。
"""
import unittest

from tests._support import StoreIsolationMixin, app_module
from app import app  # noqa: E402

PASSWORD = "unit-test-admin-2f81c4"

# 这些串一旦出现在开放接口的响应里就是泄漏；用例只断言「不出现」，不打印内容。
TOKEN = "sk-live-must-not-leak-0001"
COOKIE = "session=must-not-leak-0002"
CHECKIN_TOKEN = "checkin-token-must-not-leak-0003"
TURNSTILE = "turnstile-must-not-leak-0004"
PROXY = "http://proxyuser:must-not-leak-0005@127.0.0.1:7890"
SECRETS = (TOKEN, COOKIE, CHECKIN_TOKEN, TURNSTILE, "must-not-leak-0005")

BOOKMARK = {
    "name": "看板站", "url": "https://demo.example",
    "fields": [{
        "id": "balance", "label": "余额", "type": "amount", "enabled": True,
        "method": "GET", "url": "https://api.demo.example/v1/me?key=" + TOKEN,
        "headers": {"Authorization": "Bearer " + TOKEN, "Cookie": COOKIE},
        "body": None, "json_path": "data.balance", "divisor": 100, "unit": "USD",
        "value": "12.34", "updated_at": "2026-09-15 08:00:00",
        "curl": "curl 'https://api.demo.example/v1/me' -H 'Authorization: Bearer %s'" % TOKEN,
    }],
}
CONFIG = {
    "name": "签到站", "base_url": "https://cfg.example", "user_id": "42",
    "access_token": CHECKIN_TOKEN, "enabled": True, "turnstile": TURNSTILE,
}


class ExposureTestBase(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(ExposureTestBase, self).setUp()
        self._orig_password = app_module.ADMIN_PASSWORD
        app_module.ADMIN_PASSWORD = PASSWORD
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", self._orig_password)
        self.write_config({
            "configs": [CONFIG], "proxy_url": PROXY, "schedule": {"enabled": False},
            "bookmarks": [BOOKMARK], "link_groups": [],
        })
        self.client = app.test_client()

    def unlocked(self):
        client = app.test_client()
        client.environ_base["HTTP_X_ADMIN_PASSWORD"] = PASSWORD
        return client

    def assertNoSecrets(self, blob, where):
        text = blob if isinstance(blob, str) else blob.decode("utf-8", "replace")
        for secret in SECRETS:
            self.assertNotIn(secret, text, "%s 泄漏了凭据" % where)


class ConfigsMaskingTest(ExposureTestBase):
    def test_open_listing_carries_no_credentials(self):
        resp = self.client.get("/api/configs")
        self.assertEqual(resp.status_code, 200)
        self.assertNoSecrets(resp.get_data(), "/api/configs")

    def test_unlocked_listing_also_carries_no_bookmark_credentials(self):
        # 解锁后同样走脱敏视图：凭据只在编辑时单条按需拉取，不随列表广播。
        data = self.unlocked().get("/api/configs").get_json()
        field = data["bookmarks"][0]["fields"][0]
        for key in ("headers", "curl", "body", "url", "method", "json_path"):
            self.assertNotIn(key, field)
        self.assertNotIn("balance_config", data["bookmarks"][0])

    def test_display_data_survives_masking(self):
        bookmark = self.client.get("/api/configs").get_json()["bookmarks"][0]
        self.assertEqual(bookmark["name"], "看板站")
        self.assertEqual(bookmark["url"], "https://demo.example")
        field = bookmark["fields"][0]
        self.assertEqual(field["label"], "余额")
        self.assertEqual(field["value"], "12.34")
        self.assertEqual(field["unit"], "USD")
        self.assertEqual(field["type"], "amount")
        self.assertTrue(field["enabled"])
        self.assertTrue(field["has_request"])

    def test_checkin_token_is_masked_and_turnstile_gated(self):
        locked = self.client.get("/api/configs").get_json()["configs"][0]
        self.assertNotIn(CHECKIN_TOKEN, locked["token_masked"])
        self.assertTrue(locked["has_token"])
        # 未解锁时连首尾片段都不给：那对撞库是有用的信息，且掩码长度固定，
        # 免得从掩码反推出 token 长度。
        self.assertEqual(locked["token_masked"], app_module.OPAQUE_TOKEN_MASK)
        self.assertNotIn(CHECKIN_TOKEN[:4], locked["token_masked"])
        self.assertNotIn(CHECKIN_TOKEN[-4:], locked["token_masked"])
        # 解锁后给首尾各 4 位，便于在多组配置里认出是哪一个。
        revealed = self.unlocked().get("/api/configs").get_json()["configs"][0]["token_masked"]
        self.assertTrue(revealed.startswith(CHECKIN_TOKEN[:4]))
        self.assertTrue(revealed.endswith(CHECKIN_TOKEN[-4:]))
        self.assertNotIn(CHECKIN_TOKEN, revealed)
        self.assertEqual(locked["turnstile"], "")
        self.assertTrue(locked["has_turnstile"])
        # 解锁后 turnstile 原文回填，编辑弹窗不会把已有值清空。
        self.assertEqual(self.unlocked().get("/api/configs").get_json()["configs"][0]["turnstile"], TURNSTILE)

    def test_proxy_url_is_hidden_until_unlocked(self):
        locked = self.client.get("/api/configs").get_json()
        self.assertEqual(locked["proxy_url"], "")
        self.assertTrue(locked["proxy_configured"])
        self.assertEqual(self.unlocked().get("/api/configs").get_json()["proxy_url"], PROXY)

    def test_error_snippet_from_upstream_is_trimmed(self):
        payload = dict(BOOKMARK)
        field = dict(BOOKMARK["fields"][0], error="响应不是合法 JSON：x（响应开头：{\"token\":\"%s\"}）" % TOKEN)
        self.write_config({"configs": [], "proxy_url": "", "bookmarks": [dict(payload, fields=[field])],
                           "link_groups": []})
        resp = self.client.get("/api/configs")
        self.assertNoSecrets(resp.get_data(), "字段错误文案")
        self.assertEqual(resp.get_json()["bookmarks"][0]["fields"][0]["error"], "响应不是合法 JSON：x")


class BookmarkSecretEndpointTest(ExposureTestBase):
    def test_requires_admin(self):
        resp = self.client.get("/api/bookmarks/0/secret")
        self.assertEqual(resp.status_code, 403)
        self.assertNoSecrets(resp.get_data(), "未解锁的 /secret")

    def test_returns_full_config_when_unlocked(self):
        data = self.unlocked().get("/api/bookmarks/0/secret").get_json()
        field = data["bookmark"]["fields"][0]
        self.assertEqual(field["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(field["json_path"], "data.balance")
        self.assertIn("curl", field)

    def test_unknown_index_is_404(self):
        self.assertEqual(self.unlocked().get("/api/bookmarks/9/secret").status_code, 404)

    def test_masked_field_round_trips_without_resubmitting_curl(self):
        # 前端只改标签 / 启用开关时提交的是脱敏字段，后端应沿用旧的请求配置而不是报「curl 必填」。
        masked = self.client.get("/api/configs").get_json()["bookmarks"][0]["fields"][0]
        masked["label"] = "钱包余额"
        masked["enabled"] = False
        resp = self.unlocked().put("/api/bookmarks/0", json={
            "name": "看板站", "url": "https://demo.example", "fields": [masked]})
        self.assertTrue(resp.get_json()["ok"], resp.get_json())
        saved = self.read_config()["bookmarks"][0]["fields"][0]
        self.assertEqual(saved["label"], "钱包余额")
        self.assertFalse(saved["enabled"])
        self.assertEqual(saved["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(saved["json_path"], "data.balance")
        self.assertEqual(saved["value"], "12.34")  # 配置签名未变，取值快照保留


class AdminOnlyActionsTest(ExposureTestBase):
    LOCKED_ENDPOINTS = (
        ("post", "/api/checkin"),
        ("post", "/api/checkin/0"),
        ("post", "/api/test/0"),
        ("get", "/api/history"),
        ("get", "/api/configs/0/secret"),
        ("get", "/api/configs/export"),
    )

    def test_all_account_actions_need_unlock(self):
        for method, path in self.LOCKED_ENDPOINTS:
            resp = getattr(self.client, method)(path)
            self.assertEqual(resp.status_code, 403, "%s %s" % (method.upper(), path))

    def test_history_is_readable_once_unlocked(self):
        resp = self.unlocked().get("/api/history")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])

    def test_open_deployment_keeps_everything_reachable(self):
        # 未设管理密码（本地 / 内网）时行为不变，签到仍然是一键直签。
        app_module.ADMIN_PASSWORD = ""
        self.assertEqual(self.client.get("/api/history").status_code, 200)
        self.assertEqual(self.client.get("/api/bookmarks/0/secret").status_code, 200)


class LoginThrottleTest(ExposureTestBase):
    def _wrong(self, client=None):
        return (client or self.client).post("/api/auth", headers={"X-Admin-Password": "wrong-password"})

    def test_repeated_failures_lock_out_with_retry_after(self):
        for _ in range(app_module.LOGIN_MAX_FAILS):
            self.assertEqual(self._wrong().status_code, 401)
        locked = self._wrong()
        self.assertEqual(locked.status_code, 429)
        self.assertTrue(int(locked.headers["Retry-After"]) > 0)
        # 锁定期内即使密码正确也不放行，避免爆破者「刚好试对」后立刻登入。
        self.assertEqual(self.client.post("/api/auth", headers={"X-Admin-Password": PASSWORD}).status_code, 429)

    def test_successful_unlock_clears_the_counter(self):
        for _ in range(app_module.LOGIN_MAX_FAILS - 1):
            self._wrong()
        self.assertTrue(self.client.post("/api/auth", headers={"X-Admin-Password": PASSWORD}).get_json()["ok"])
        # 计数清零：同一来源重新错满一轮才会再次锁定（新客户端避免带上刚下发的会话 Cookie）。
        for _ in range(app_module.LOGIN_MAX_FAILS):
            self.assertEqual(self._wrong(app.test_client()).status_code, 401)
        self.assertEqual(self._wrong(app.test_client()).status_code, 429)

    def test_counter_is_per_client_ip(self):
        other = app.test_client()
        other.environ_base["HTTP_X_FORWARDED_FOR"] = "203.0.113.9"
        for _ in range(app_module.LOGIN_MAX_FAILS):
            self._wrong()
        self.assertEqual(self._wrong().status_code, 429)
        self.assertEqual(self._wrong(other).status_code, 401)  # 另一个来源不受牵连

    def test_global_backstop_survives_forged_forwarded_for(self):
        # X-Forwarded-For 可伪造：每次换一个 IP 也会被全局桶拦下。
        for i in range(app_module.LOGIN_GLOBAL_MAX_FAILS):
            client = app.test_client()
            client.environ_base["HTTP_X_FORWARDED_FOR"] = "198.51.100.%d" % (i % 254 + 1)
            self._wrong(client)
        fresh = app.test_client()
        fresh.environ_base["HTTP_X_FORWARDED_FOR"] = "198.51.100.254"
        self.assertEqual(self._wrong(fresh).status_code, 429)

    def test_unauthenticated_browsing_does_not_count_as_a_failed_login(self):
        # 没带任何凭据只是「未解锁」，不该把正常访客推进爆破计数里。
        for _ in range(app_module.LOGIN_MAX_FAILS * 2):
            self.assertEqual(self.client.get("/api/history").status_code, 403)
        self.assertEqual(self._wrong().status_code, 401)

    def test_admin_endpoints_share_the_same_throttle(self):
        bad = app.test_client()
        bad.environ_base["HTTP_X_ADMIN_PASSWORD"] = "wrong-password"
        for _ in range(app_module.LOGIN_MAX_FAILS):
            self.assertEqual(bad.get("/api/configs/export").status_code, 403)
        self.assertEqual(bad.get("/api/configs/export").status_code, 429)


class SecurityHeadersTest(ExposureTestBase):
    def test_page_carries_hardening_headers(self):
        resp = self.client.get("/")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        self.assertEqual(resp.headers["Referrer-Policy"], "no-referrer")
        csp = resp.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_api_responses_are_not_cached(self):
        self.assertEqual(self.client.get("/api/configs").headers["Cache-Control"], "no-store")

    def test_favicon_keeps_its_own_cache_policy(self):
        # 图标接口自带长缓存，安全头不应把它改成 no-store。
        resp = self.client.get("/api/favicon?u=https://nobody.invalid")
        self.assertIn("max-age", resp.headers.get("Cache-Control", ""))

    def test_hsts_is_opt_in(self):
        resp = self.client.get("/", headers={"X-Forwarded-Proto": "https"})
        self.assertNotIn("Strict-Transport-Security", resp.headers)
        orig = app_module.HSTS_ENABLED
        app_module.HSTS_ENABLED = True
        self.addCleanup(setattr, app_module, "HSTS_ENABLED", orig)
        resp = self.client.get("/", headers={"X-Forwarded-Proto": "https"})
        self.assertIn("max-age=", resp.headers["Strict-Transport-Security"])


class FrontendWiringTest(ExposureTestBase):
    """前端挂点：页面不再依赖列表里的凭据，账号动作也都先过解锁判断。"""

    def setUp(self):
        super(FrontendWiringTest, self).setUp()
        self.html = self.client.get("/").get_data(as_text=True)

    def test_edit_modal_fetches_the_secret_endpoint(self):
        self.assertIn("/api/bookmarks/${idx}/secret", self.html)

    def test_account_actions_are_gated_behind_unlock(self):
        for marker in ("async function runCheckin", "async function checkinOne", "async function testOne"):
            start = self.html.index(marker)
            self.assertIn("guardAdmin()", self.html[start:start + 400], marker)

    def test_history_tab_shows_a_locked_state_instead_of_requesting(self):
        start = self.html.index("async function loadHistory")
        body = self.html[start:start + 500]
        self.assertIn("canEdit()", body)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""私密模式：连只读浏览也要先解锁。

设了管理密码就默认开启（要公开首页得显式 HUB_PUBLIC_LIBRARY=1；GYQD_PRIVATE=1 仍强制开启）。
开启后公网路人看不到站点清单与自建服务地址，但应用外壳与健康检查必须仍然可达，
否则没法解锁、容器也会被判定为不健康。默认值的解析见 tests/test_chat_embed.py。
"""
import unittest

from tests._support import StoreIsolationMixin, app_module
from app import app  # noqa: E402

PASSWORD = "private-mode-unit-test-8c41"
SECRET_HOST = "https://intranet-panel.invalid"

STORE = {
    "configs": [{"name": "签到站", "base_url": "https://cfg.example", "user_id": "1",
                 "access_token": "tok", "enabled": True}],
    "proxy_url": "",
    "bookmarks": [{"name": "看板站", "url": "https://dash.example", "fields": []}],
    "link_groups": [{"id": "self", "name": "自建服务", "icon": "server", "color": "sky", "links": [
        {"id": "l1", "name": "内网面板", "url": SECRET_HOST},
    ]}],
}


class PrivateModeBase(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(PrivateModeBase, self).setUp()
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", app_module.ADMIN_PASSWORD)
        self.addCleanup(setattr, app_module, "PRIVATE_MODE", app_module.PRIVATE_MODE)
        app_module.ADMIN_PASSWORD = PASSWORD
        app_module.PRIVATE_MODE = True
        self.write_config(dict(STORE))
        self.client = app.test_client()

    def unlocked(self):
        client = app.test_client()
        client.environ_base["HTTP_X_ADMIN_PASSWORD"] = PASSWORD
        return client


class ShellStaysReachableTest(PrivateModeBase):
    """没有外壳就没法解锁，没有健康检查容器会被判定为不健康。"""

    def test_index_is_served(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_health_check_is_not_blocked(self):
        # 容器 HEALTHCHECK 调的就是这个，堵掉会让 docker 反复重启容器。
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_static_assets_and_worker_are_served(self):
        for path in ("/static/app-v3.css", "/sw.js", "/static/manifest.webmanifest"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_auth_endpoint_is_reachable(self):
        resp = self.client.post("/api/auth", headers={"X-Admin-Password": PASSWORD})
        self.assertTrue(resp.get_json()["ok"])


class NothingLeaksWhileLockedTest(PrivateModeBase):
    def test_configs_returns_an_empty_shell(self):
        data = self.client.get("/api/configs").get_json()
        self.assertTrue(data["ok"])          # 200 而不是 403：页面要能渲染出解锁面板
        self.assertTrue(data["private"])
        self.assertTrue(data["locked"])
        self.assertEqual(data["configs"], [])
        self.assertEqual(data["bookmarks"], [])
        self.assertEqual(data["link_groups"], [])

    def test_no_url_from_the_store_appears_anywhere(self):
        blob = self.client.get("/api/configs").get_data(as_text=True)
        for leak in (SECRET_HOST, "dash.example", "cfg.example", "自建服务", "内网面板"):
            self.assertNotIn(leak, blob)

    def test_every_other_api_is_refused(self):
        cases = [
            ("get", "/api/history"), ("get", "/api/configs/export"),
            ("get", "/api/favicon?u=https://x.example"),
            ("get", "/api/bookmarks/0/secret"), ("get", "/api/configs/0/secret"),
            ("post", "/api/checkin"), ("post", "/api/link_groups"),
            ("put", "/api/settings"),
            ("post", "/api/bookmarks/0/fields/f1/snooze"), ("get", "/api/link_icon/self/l1"),
        ]
        for method, path in cases:
            self.assertEqual(getattr(self.client, method)(path).status_code, 403,
                             "%s %s" % (method.upper(), path))

    def test_favicon_does_not_reveal_configured_origins(self):
        # 图标接口只对已配置的 origin 有反应，未解锁时会变成一个「这个站点在不在库里」的探针。
        self.assertEqual(self.client.get("/api/favicon?u=" + SECRET_HOST).status_code, 403)


class UnlockedBehavesNormallyTest(PrivateModeBase):
    def test_full_data_comes_back(self):
        data = self.unlocked().get("/api/configs").get_json()
        self.assertTrue(data["private"])
        self.assertFalse(data["locked"])
        self.assertEqual(len(data["bookmarks"]), 1)
        self.assertEqual(data["link_groups"][0]["links"][0]["url"], SECRET_HOST)

    def test_admin_actions_work(self):
        self.assertEqual(self.unlocked().get("/api/history").status_code, 200)
        self.assertEqual(self.unlocked().put("/api/settings", json={"proxy_url": ""}).status_code, 200)

    def test_bookmark_listing_is_still_desensitised(self):
        # 私密模式不改变脱敏策略：凭据依旧只走 admin-only 的单条接口。
        field = self.unlocked().get("/api/configs").get_json()["bookmarks"][0]
        self.assertNotIn("balance_config", field)


class DisabledByDefaultTest(PrivateModeBase):
    def test_off_means_previous_behaviour(self):
        app_module.PRIVATE_MODE = False
        data = self.client.get("/api/configs").get_json()
        self.assertFalse(data["private"])
        self.assertEqual(len(data["link_groups"]), 1)      # 只读浏览照常开放
        self.assertEqual(self.client.get("/api/history").status_code, 403)  # 管理接口照常需要解锁

    def test_without_a_password_private_mode_is_inert(self):
        # 私密模式靠管理密码兜底；没有密码就无从校验，不能把整站锁死成谁都进不去。
        app_module.ADMIN_PASSWORD = ""
        self.assertFalse(app_module.private_mode_active())
        self.assertEqual(self.client.get("/api/history").status_code, 200)
        self.assertEqual(len(self.client.get("/api/configs").get_json()["link_groups"]), 1)


class PrivateModeUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.html = app.test_client().get("/").get_data(as_text=True)

    def test_locked_state_sends_the_user_to_the_unlock_panel(self):
        self.assertIn("STATE.private && STATE.locked", self.html)
        self.assertIn("switchView('settings')", self.html)

    def test_prompt_only_fires_once(self):
        self.assertIn("PRIVATE_PROMPTED", self.html)

    def test_panel_explains_what_private_mode_means(self):
        self.assertIn("私密模式已开启", self.html)


if __name__ == "__main__":
    unittest.main()

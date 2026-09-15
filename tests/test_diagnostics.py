# -*- coding: utf-8 -*-
"""部署自检：/api/diagnostics 与 /api/health 的分工。

/api/health 是未鉴权可达的容器探针，必须永远只回 {ok:true}——多一个字段就多一分
信息泄漏。细节走 /api/diagnostics，需管理密码，且内容里不能出现任何凭据。
"""
import os
import stat
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, collect_diagnostics  # noqa: E402

PASSWORD = "diagnostics-unit-test-a913f7"
TOKEN = "diag-token-must-not-leak"
HEADER_SECRET = "diag-header-must-not-leak"

STORE = {
    "configs": [{"name": "签到站", "base_url": "https://cfg.example", "user_id": "1",
                 "access_token": TOKEN, "enabled": True}],
    "proxy_url": "http://user:diag-proxy-must-not-leak@127.0.0.1:1080",
    "schedule": {"enabled": False, "time": "08:30"},
    "bookmarks": [{"name": "看板站", "url": "https://dash.example", "fields": [
        {"id": "f1", "label": "余额", "type": "amount", "enabled": True, "method": "GET",
         "url": "https://api.dash.example/me", "headers": {"Authorization": "Bearer " + HEADER_SECRET},
         "body": None, "json_path": "data.balance"}]}],
    "link_groups": [{"id": "daily", "name": "常用", "icon": "globe", "color": "mint", "links": [
        {"id": "l1", "name": "Docs", "url": "https://docs.example"}]}],
}


class DiagnosticsBase(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(DiagnosticsBase, self).setUp()
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", app_module.ADMIN_PASSWORD)
        app_module.ADMIN_PASSWORD = PASSWORD
        self.write_config(dict(STORE))
        self.client = app.test_client()

    def unlocked(self):
        client = app.test_client()
        client.environ_base["HTTP_X_ADMIN_PASSWORD"] = PASSWORD
        return client

    def labels(self, report=None, **kwargs):
        report = report or collect_diagnostics(**kwargs)
        return {c["label"]: c for c in report["checks"]}


class HealthProbeTest(DiagnosticsBase):
    def test_health_stays_minimal_and_open(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"ok": True})

    def test_health_leaks_nothing_even_when_things_are_broken(self):
        app_module.ADMIN_PASSWORD = ""
        self.assertEqual(self.client.get("/api/health").get_json(), {"ok": True})


class AccessTest(DiagnosticsBase):
    def test_requires_admin(self):
        self.assertEqual(self.client.get("/api/diagnostics").status_code, 403)

    def test_available_once_unlocked(self):
        resp = self.unlocked().get("/api/diagnostics")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["checks"])

    def test_report_never_contains_credentials(self):
        blob = self.unlocked().get("/api/diagnostics").get_data(as_text=True)
        for secret in (TOKEN, HEADER_SECRET, "diag-proxy-must-not-leak", PASSWORD):
            self.assertNotIn(secret, blob)

    def test_report_does_not_echo_urls(self):
        blob = self.unlocked().get("/api/diagnostics").get_data(as_text=True)
        for url in ("cfg.example", "dash.example", "docs.example"):
            self.assertNotIn(url, blob)


class ChecksTest(DiagnosticsBase):
    def test_counts_are_reported(self):
        detail = self.labels()["配置文件"]["detail"]
        self.assertIn("签到 1 组", detail)
        self.assertIn("看板 1 个", detail)
        self.assertIn("网址 1 条", detail)

    def test_missing_admin_password_is_an_error(self):
        app_module.ADMIN_PASSWORD = ""
        report = collect_diagnostics()
        self.assertEqual(self.labels(report)["管理密码"]["status"], "error")
        self.assertFalse(report["ok"])

    def test_short_admin_password_is_a_warning(self):
        app_module.ADMIN_PASSWORD = "short"
        report = collect_diagnostics()
        self.assertEqual(self.labels(report)["管理密码"]["status"], "warn")
        self.assertTrue(report["ok"])          # 警告不算阻断

    def test_strong_password_length_is_reported_not_the_password(self):
        detail = self.labels()["管理密码"]["detail"]
        self.assertIn(str(len(PASSWORD)), detail)
        self.assertNotIn(PASSWORD, detail)

    def test_scheduler_off_with_schedule_on_is_an_error(self):
        # 最容易踩的坑：开了定时签到，却把调度线程关了，于是什么都不会发生。
        self.addCleanup(setattr, app_module, "SCHEDULER_ENABLED", app_module.SCHEDULER_ENABLED)
        app_module.SCHEDULER_ENABLED = False
        store = STORE.copy()
        store["schedule"] = {"enabled": True, "time": "08:30"}
        self.write_config(store)
        report = collect_diagnostics()
        self.assertEqual(self.labels(report)["后台调度"]["status"], "error")
        self.assertFalse(report["ok"])

    def test_scheduler_off_without_any_schedule_is_fine(self):
        self.addCleanup(setattr, app_module, "SCHEDULER_ENABLED", app_module.SCHEDULER_ENABLED)
        app_module.SCHEDULER_ENABLED = False
        self.assertEqual(self.labels()["后台调度"]["status"], "ok")

    def test_unwritable_data_dir_is_an_error(self):
        self.addCleanup(setattr, app_module, "DATA_DIR", app_module.DATA_DIR)
        app_module.DATA_DIR = app_module.Path("/definitely/not/here")
        report = collect_diagnostics()
        self.assertEqual(self.labels(report)["数据目录"]["status"], "error")
        self.assertFalse(report["ok"])

    def test_unreadable_config_is_an_error(self):
        with open(app_module.CONFIG_FILE, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        report = collect_diagnostics()
        self.assertEqual(self.labels(report)["配置文件"]["status"], "error")

    def test_plain_http_is_flagged(self):
        # 反代没转发 X-Forwarded-Proto 时会话 Cookie 不会带 Secure。
        self.assertEqual(self.labels(request_is_https=False)["传输"]["status"], "warn")

    def test_https_passes(self):
        self.assertEqual(self.labels(request_is_https=True)["传输"]["status"], "ok")

    def test_unknown_transport_is_simply_omitted(self):
        # 不在请求上下文里时无从判断，跳过该项而不是瞎猜一个结论。
        self.assertNotIn("传输", self.labels(request_is_https=None))

    def test_endpoint_reports_transport_from_the_real_request(self):
        client = self.unlocked()
        plain = client.get("/api/diagnostics").get_json()["checks"]
        secure = client.get("/api/diagnostics", headers={"X-Forwarded-Proto": "https"}).get_json()["checks"]
        self.assertEqual(next(c["status"] for c in plain if c["label"] == "传输"), "warn")
        self.assertEqual(next(c["status"] for c in secure if c["label"] == "传输"), "ok")

    def test_private_mode_without_password_is_an_error(self):
        self.addCleanup(setattr, app_module, "PRIVATE_MODE", app_module.PRIVATE_MODE)
        app_module.PRIVATE_MODE = True
        app_module.ADMIN_PASSWORD = ""
        self.assertEqual(self.labels()["私密模式"]["status"], "error")

    def test_session_secret_permissions_are_checked(self):
        app_module._session_secret()           # 触发落盘
        self.assertEqual(self.labels()["会话密钥"]["status"], "ok")
        os.chmod(str(app_module.SESSION_SECRET_FILE), 0o644)
        self.assertEqual(self.labels()["会话密钥"]["status"], "warn")

    def test_pending_checkins_are_surfaced(self):
        store = STORE.copy()
        store["schedule"] = {"enabled": True, "time": "08:30",
                             "last_run_date": app_module._today_str(), "retry_count": 0}
        self.write_config(store)
        check = self.labels()["定时签到"]
        self.assertEqual(check["status"], "warn")
        self.assertIn("1 个未签成", check["detail"])

    def test_exhausted_retries_escalate_to_error(self):
        store = STORE.copy()
        store["schedule"] = {"enabled": True, "time": "08:30",
                             "last_run_date": app_module._today_str(),
                             "retry_count": app_module.SCHEDULE_RETRY_LIMIT}
        self.write_config(store)
        self.assertEqual(self.labels()["定时签到"]["status"], "error")

    def test_writability_is_probed_not_guessed(self):
        # 只读挂载、磁盘写满都只有真写一次才知道；探测文件必须被清理掉。
        before = set(os.listdir(str(self.data_dir)))
        collect_diagnostics()
        self.assertEqual(set(os.listdir(str(self.data_dir))), before)


class DiagnosticsUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_panel_exists_and_refreshes(self):
        self.assertIn('id="diagList"', self.html)
        self.assertIn('id="runDiag"', self.html)
        self.assertIn("function loadDiagnostics", self.html)

    def test_runs_when_entering_settings(self):
        self.assertIn("if (name === 'settings') loadDiagnostics();", self.html)

    def test_locked_users_see_a_hint_not_a_request(self):
        start = self.html.index("async function loadDiagnostics")
        self.assertIn("if (!canEdit())", self.html[start:start + 260])

    def test_styles_exist(self):
        for rule in (".diag-list", ".diag-row.is-error", ".diag-head.is-ok"):
            self.assertIn(rule, self.css)


if __name__ == "__main__":
    unittest.main()

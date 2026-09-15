# -*- coding: utf-8 -*-
"""定时签到的当日补签。

原先无论成功与否都把当天标记为「已跑」：08:30 恰好断网，这天就彻底没签，
而这正是「无人值守」最该兜住的情况。补签只重跑「今天还没签成功」的账号。
"""
import time
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, metrics_key, pending_configs, read_history, read_store  # noqa: E402

TODAY = None  # setUp 里按运行日填

CFG_A = {"name": "站点A", "base_url": "https://a.example", "user_id": "1", "access_token": "t", "enabled": True}
CFG_B = {"name": "站点B", "base_url": "https://b.example", "user_id": "2", "access_token": "t", "enabled": True}
CFG_OFF = {"name": "禁用站", "base_url": "https://c.example", "user_id": "3", "access_token": "t", "enabled": False}


class RetryTestBase(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(RetryTestBase, self).setUp()
        global TODAY
        TODAY = app_module._today_str()
        self.write_config({
            "configs": [CFG_A, CFG_B, CFG_OFF], "proxy_url": "", "bookmarks": [], "link_groups": [],
            "schedule": {"enabled": True, "time": "08:30", "last_run_date": TODAY,
                         "last_run_time": "2026-09-15 08:30:00", "retry_count": 0, "last_attempt_ts": 0},
        })
        # 签到本身用假函数替换，测试只关心调度决策，不打网络。
        self.runs = []
        real = app_module.run_checkin
        self.addCleanup(setattr, app_module, "run_checkin", real)

        def fake_run(configs, proxy_url):
            self.runs.append([c["name"] for c in configs])
            return [{"name": c["name"], "status": self.outcome.get(c["name"], "failed"), "quota": 1}
                    for c in configs]

        app_module.run_checkin = fake_run
        self.outcome = {}

    def mark_signed(self, name, day=None):
        cfg = {"站点A": CFG_A, "站点B": CFG_B, "禁用站": CFG_OFF}[name]
        app_module.update_metric(cfg, {"wallet_balance": "1"}, mark_signed=True)
        if day:
            metrics = app_module.read_metrics()
            metrics[metrics_key(cfg)]["last_checkin_date"] = day
            app_module.Path(app_module.METRICS_FILE).write_text(
                __import__("json").dumps(metrics, ensure_ascii=False), encoding="utf-8")


class PendingConfigsTest(RetryTestBase):
    def test_everything_enabled_is_pending_at_first(self):
        self.assertEqual([c["name"] for c in pending_configs(read_store())], ["站点A", "站点B"])

    def test_disabled_configs_are_never_pending(self):
        self.assertNotIn("禁用站", [c["name"] for c in pending_configs(read_store())])

    def test_signed_today_drops_out(self):
        self.mark_signed("站点A")
        self.assertEqual([c["name"] for c in pending_configs(read_store())], ["站点B"])

    def test_signed_on_another_day_is_still_pending(self):
        self.mark_signed("站点A", day="2020-01-01")
        self.assertIn("站点A", [c["name"] for c in pending_configs(read_store())])

    def test_config_added_later_in_the_day_is_picked_up(self):
        self.mark_signed("站点A")
        self.mark_signed("站点B")
        self.assertEqual(pending_configs(read_store()), [])
        store = read_store()
        store["configs"].append({"name": "新站", "base_url": "https://d.example", "user_id": "4",
                                 "access_token": "t", "enabled": True})
        app_module.write_store(store)
        self.assertEqual([c["name"] for c in pending_configs(read_store())], ["新站"])


class RetryTickTest(RetryTestBase):
    def test_retries_only_the_accounts_that_failed(self):
        self.mark_signed("站点A")           # A 已成功
        self.outcome = {"站点B": "signed"}
        app_module._retry_tick()
        self.assertEqual(self.runs, [["站点B"]])   # 不给已成功的 A 重复发请求

    def test_successful_retry_clears_the_backlog(self):
        self.outcome = {"站点A": "signed", "站点B": "signed"}
        app_module._retry_tick()
        self.assertEqual(pending_configs(read_store()), [])
        self.assertEqual(read_store()["schedule"]["retry_count"], 1)

    def test_nothing_pending_means_no_run(self):
        self.mark_signed("站点A")
        self.mark_signed("站点B")
        app_module._retry_tick()
        self.assertEqual(self.runs, [])

    def test_waits_for_the_delay_between_attempts(self):
        self.outcome = {}                    # 全失败
        app_module._retry_tick()
        self.assertEqual(len(self.runs), 1)
        app_module._retry_tick()             # 紧接着再跑一次：应当被延迟挡住
        self.assertEqual(len(self.runs), 1)

    def test_runs_again_after_the_delay(self):
        self.outcome = {}
        app_module._retry_tick()
        store = read_store()
        store["schedule"]["last_attempt_ts"] = time.time() - (app_module.SCHEDULE_RETRY_DELAY_MIN * 60 + 5)
        app_module.write_store(store)
        app_module._retry_tick()
        self.assertEqual(len(self.runs), 2)

    def test_gives_up_after_the_limit(self):
        store = read_store()
        store["schedule"]["retry_count"] = app_module.SCHEDULE_RETRY_LIMIT
        app_module.write_store(store)
        app_module._retry_tick()
        self.assertEqual(self.runs, [])

    def test_does_nothing_before_the_main_run(self):
        store = read_store()
        store["schedule"]["last_run_date"] = "2020-01-01"
        app_module.write_store(store)
        app_module._retry_tick()
        self.assertEqual(self.runs, [])

    def test_disabled_schedule_never_retries(self):
        store = read_store()
        store["schedule"]["enabled"] = False
        app_module.write_store(store)
        app_module._retry_tick()
        self.assertEqual(self.runs, [])

    def test_clock_moving_backwards_does_not_wedge_it(self):
        store = read_store()
        store["schedule"]["last_attempt_ts"] = time.time() + 99999
        app_module.write_store(store)
        app_module._retry_tick()
        self.assertEqual(len(self.runs), 1)

    def test_limit_zero_disables_the_feature(self):
        self.addCleanup(setattr, app_module, "SCHEDULE_RETRY_LIMIT", app_module.SCHEDULE_RETRY_LIMIT)
        app_module.SCHEDULE_RETRY_LIMIT = 0
        app_module._retry_tick()
        self.assertEqual(self.runs, [])

    def test_retry_is_recorded_in_history_under_its_own_trigger(self):
        self.outcome = {"站点A": "signed", "站点B": "signed"}
        app_module._retry_tick()
        entry = read_history()[0]
        self.assertEqual(entry["trigger"], "retry")
        self.assertEqual([r["name"] for r in entry["results"]], ["站点A", "站点B"])

    def test_crash_during_retry_still_counts_as_an_attempt(self):
        # 否则一个持续抛异常的故障会让补签无限循环。
        def boom(configs, proxy_url):
            raise RuntimeError("boom")

        app_module.run_checkin = boom
        app_module._retry_tick()
        self.assertEqual(read_store()["schedule"]["retry_count"], 1)
        self.assertIn("boom", read_history()[0]["error"])


class RetryStatusApiTest(RetryTestBase):
    def test_schedule_payload_exposes_retry_state(self):
        sched = app.test_client().get("/api/configs").get_json()["schedule"]
        self.assertEqual(sched["retry_limit"], app_module.SCHEDULE_RETRY_LIMIT)
        self.assertEqual(sched["retry_delay_minutes"], app_module.SCHEDULE_RETRY_DELAY_MIN)
        self.assertEqual(sched["pending_today"], 2)

    def test_pending_count_drops_as_accounts_succeed(self):
        self.mark_signed("站点A")
        sched = app.test_client().get("/api/configs").get_json()["schedule"]
        self.assertEqual(sched["pending_today"], 1)

    def test_no_pending_count_when_schedule_is_off(self):
        store = read_store()
        store["schedule"]["enabled"] = False
        app_module.write_store(store)
        self.assertEqual(app.test_client().get("/api/configs").get_json()["schedule"]["pending_today"], 0)


class RetryUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.html = app.test_client().get("/").get_data(as_text=True)

    def test_settings_explains_the_backlog(self):
        self.assertIn("自动补签", self.html)
        self.assertIn("s.pending_today", self.html)

    def test_history_labels_retry_runs(self):
        self.assertIn("retry: '补签'", self.html)


if __name__ == "__main__":
    unittest.main()

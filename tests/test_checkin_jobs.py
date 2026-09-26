# -*- coding: utf-8 -*-
"""签到这条线的三处加固（2026-09-24 评审 P0）。

1. 按下标定位的接口带 ?expect=<稳定键>：下标已换人时回 409，不会把 A 的令牌配到 B 的地址上。
2. 每个账户最近一次签到的状态、原因、时间写进快照：失败了刷新页面也看得到。
3. 「运行全部签到」是后台任务：立即返回、可轮询进度；按站点分组并发，同站点账户依次签。
"""
import threading
import time
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, metrics_key, read_metrics  # noqa: E402

CFG_A = {"name": "A", "base_url": "https://a.example", "user_id": "1", "access_token": "tok-a", "enabled": True}
CFG_B = {"name": "B", "base_url": "https://b.example", "user_id": "2", "access_token": "tok-b", "enabled": True}
CFG_A2 = {"name": "A2", "base_url": "https://A.example/", "user_id": "9", "access_token": "tok-a2", "enabled": True}
CFG_OFF = {"name": "OFF", "base_url": "https://c.example", "user_id": "3", "access_token": "tok-c", "enabled": False}


class _Base(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(_Base, self).setUp()
        self.client = app.test_client()


class StaleIndexGuardTest(_Base):
    def setUp(self):
        super(StaleIndexGuardTest, self).setUp()
        self.write_config({"configs": [dict(CFG_A), dict(CFG_B)], "bookmarks": [
            {"name": "站1", "url": "https://s1.example", "fields": []},
            {"name": "站2", "url": "https://s2.example", "fields": []},
        ], "link_groups": []})

    def test_public_views_carry_stable_keys(self):
        data = self.client.get("/api/configs").get_json()
        self.assertEqual([c["key"] for c in data["configs"]], ["https://a.example|1", "https://b.example|2"])
        self.assertEqual([b["key"] for b in data["bookmarks"]], ["https://s1.example|站1", "https://s2.example|站2"])

    def test_toggle_from_stale_page_is_rejected_and_keeps_tokens(self):
        # 另一台设备删掉了 A：下标 0 现在是 B。旧页面仍以为下标 0 是 A，令牌留空提交。
        self.client.delete("/api/configs/0")
        resp = self.client.put("/api/configs/0?expect=https://a.example|1", json={
            "name": "A", "base_url": "https://a.example", "user_id": "1", "access_token": "", "enabled": False})
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.get_json()["stale"])
        self.assertEqual(self.read_config()["configs"], [CFG_B])  # B 原封不动，令牌没被挪到 A 的地址上

    def test_matching_key_passes_and_old_clients_still_work(self):
        ok = self.client.put("/api/configs/1?expect=https://b.example|2", json=dict(CFG_B, access_token="", enabled=False))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.read_config()["configs"][1]["access_token"], "tok-b")
        self.assertEqual(self.client.put("/api/configs/1", json=dict(CFG_B, access_token="")).status_code, 200)

    def test_every_index_endpoint_checks_the_key(self):
        bad = "?expect=nope"
        for method, url in (("get", "/api/configs/0/secret"), ("delete", "/api/configs/0"),
                            ("post", "/api/checkin/0"), ("post", "/api/test/0"),
                            ("get", "/api/bookmarks/0/secret"), ("delete", "/api/bookmarks/0"),
                            ("put", "/api/bookmarks/0"), ("post", "/api/bookmarks/0/refresh_balance")):
            resp = getattr(self.client, method)(url + bad, json={})
            self.assertEqual(resp.status_code, 409, url)
        self.assertEqual(len(self.read_config()["configs"]), 2)
        self.assertEqual(len(self.read_config()["bookmarks"]), 2)


class OutcomePersistenceTest(_Base):
    def test_failure_is_recorded_without_touching_metrics(self):
        app_module.update_metric(CFG_A, {"wallet_balance": "$5.00"}, mark_signed=False)
        before = read_metrics()[metrics_key(CFG_A)]["updated_at"]
        app_module.record_outcomes([CFG_A, CFG_OFF], [
            {"name": "A", "status": "failed", "message": "执行签到失败：HTTP 500，炸了（响应开头：<html>secret"},
            {"name": "OFF", "status": "disabled", "message": "配置已禁用"},
        ])
        snap = read_metrics()[metrics_key(CFG_A)]
        self.assertEqual(snap["last_status"], "failed")
        self.assertEqual(snap["last_message"], "执行签到失败：HTTP 500，炸了")  # 上游响应片段剥掉
        self.assertTrue(snap["last_run_at"])
        self.assertEqual(snap["wallet_balance"], "$5.00")
        self.assertEqual(snap["updated_at"], before)
        self.assertNotIn("last_checkin_date", snap)
        self.assertNotIn(metrics_key(CFG_OFF), read_metrics())  # 禁用项不记

    def test_success_marks_today_and_overwrites_failure(self):
        app_module.record_outcomes([CFG_A], [{"name": "A", "status": "failed", "message": "x"}])
        app_module.record_outcomes([CFG_A], [{"name": "A", "status": "signed", "message": "签到成功", "quota": 500000}])
        snap = read_metrics()[metrics_key(CFG_A)]
        self.assertEqual(snap["last_status"], "signed")
        self.assertEqual(snap["last_checkin_date"], app_module._today_str())

    def test_configs_api_exposes_last_outcome(self):
        self.write_config({"configs": [CFG_A], "bookmarks": [], "link_groups": []})
        app_module.record_outcomes([CFG_A], [{"name": "A", "status": "failed", "message": "令牌失效"}])
        cfg = self.client.get("/api/configs").get_json()["configs"][0]
        self.assertEqual(cfg["metrics"]["last_status"], "failed")
        self.assertEqual(cfg["metrics"]["last_message"], "令牌失效")
        self.assertFalse(cfg["checked_in_today"])

    def test_history_and_metrics_are_written_atomically(self):
        calls = []
        real = app_module._write_text_atomic
        self.addCleanup(setattr, app_module, "_write_text_atomic", real)
        app_module._write_text_atomic = lambda path, text: (calls.append(path.name), real(path, text))
        app_module.record_history("manual", [{"name": "A", "status": "signed"}])
        app_module.update_metric(CFG_A, {"wallet_balance": "1"})
        self.assertEqual(calls, ["history.json", "metrics.json"])


class CheckinJobTest(_Base):
    def setUp(self):
        super(CheckinJobTest, self).setUp()
        self.write_config({"configs": [dict(CFG_A), dict(CFG_B), dict(CFG_A2), dict(CFG_OFF)],
                           "bookmarks": [], "link_groups": []})
        self.active = {}
        self.peak = [0]
        self.order = []
        self.gate = threading.Event()
        lock = threading.Lock()
        real_run_one, real_build = app_module.gyqd.run_one, app_module._build_client
        self.addCleanup(setattr, app_module.gyqd, "run_one", real_run_one)
        self.addCleanup(setattr, app_module, "_build_client", real_build)
        app_module._build_client = lambda proxy: object()

        def fake_run_one(config, client):
            if not config.get("enabled", True):
                return {"name": config["name"], "status": "disabled", "message": "配置已禁用"}
            site = app_module._site_of(config)
            with lock:
                self.order.append(config["name"])
                self.active[site] = self.active.get(site, 0) + 1
                self.peak[0] = max(self.peak[0], sum(self.active.values()))
                same_site = self.active[site]
            self.gate.wait(10)   # 放宽到 10 秒：机器忙时线程起得慢，2 秒可能先超时，测到的并发数就不准了
            with lock:
                self.active[site] -= 1
            self.assertEqual(same_site, 1, "同一站点的账户不应同时签")
            if config["name"] == "B":
                return {"name": "B", "status": "failed", "message": "连不上"}
            return {"name": config["name"], "status": "signed", "message": "ok", "quota": 500000,
                    "wallet": {"quota": 1000000}}

        app_module.gyqd.run_one = fake_run_one

    def wait_done(self):
        for _ in range(500):
            job = self.client.get("/api/checkin/jobs").get_json()["job"]
            if not job["running"]:
                return job
            time.sleep(0.02)
        self.fail("后台签到没有结束")

    def test_start_returns_immediately_then_reports_progress(self):
        resp = self.client.post("/api/checkin/jobs")
        self.assertEqual(resp.status_code, 202)
        job = resp.get_json()["job"]
        self.assertTrue(job["running"])
        self.assertEqual(job["total"], 4)
        self.assertEqual([i["key"] for i in job["items"]][0], "https://a.example|1")
        again = self.client.post("/api/checkin/jobs")
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.get_json()["already_running"])
        for _ in range(1000):  # 等两个不同站点同时进行中再放行（最多约 10 秒；正常几十毫秒就到）
            if self.peak[0] >= 2:
                break
            time.sleep(0.01)
        self.gate.set()
        job = self.wait_done()
        self.assertEqual(job["done"], 4)
        self.assertEqual([i["result"]["status"] for i in job["items"]], ["signed", "failed", "signed", "disabled"])
        self.assertEqual(job["summary"]["failed"], 1)
        self.assertGreaterEqual(self.peak[0], 2)  # 不同站点确实并发
        self.assertLess(self.order.index("A"), self.order.index("A2"))  # 同站点按原顺序依次签
        metrics = read_metrics()
        self.assertEqual(metrics[metrics_key(CFG_B)]["last_status"], "failed")
        self.assertEqual(metrics[metrics_key(CFG_A)]["last_checkin_date"], app_module._today_str())
        self.assertEqual(app_module.read_history()[0]["trigger"], "manual")

    def test_startup_error_is_reported_on_the_job(self):
        def boom(proxy):
            raise app_module.gyqd.CheckinError("SOCKS 代理需要 curl_cffi")
        app_module._build_client = boom
        self.client.post("/api/checkin/jobs")
        job = self.wait_done()
        self.assertIn("curl_cffi", job["error"])
        self.assertEqual(job["done"], 0)

    def test_no_configs_is_a_400(self):
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})
        self.assertEqual(self.client.post("/api/checkin/jobs").status_code, 400)


if __name__ == "__main__":
    unittest.main()


class DraftTestAndClockTest(_Base):
    """弹窗里的「测试连接」与服务器时区提示。"""

    def setUp(self):
        super(DraftTestAndClockTest, self).setUp()
        self.write_config({"configs": [dict(CFG_A)], "bookmarks": [], "link_groups": [],
                           "schedule": {"enabled": True, "time": "08:30"}})
        self.seen = []
        real = app_module.test_single
        self.addCleanup(setattr, app_module, "test_single", real)

        def fake(config, proxy):
            self.seen.append(dict(config))
            if config["access_token"] == "bad":
                raise app_module.gyqd.CheckinError("查询钱包额度失败：HTTP 401（响应开头：<html>")
            return {"quota": 1000000, "used_quota": 500000, "request_count": 3}
        app_module.test_single = fake

    def test_draft_uses_form_values_and_saves_nothing(self):
        before = self.read_config()
        resp = self.client.post("/api/test", json={"name": "x", "base_url": "https://new.example", "user_id": "5", "access_token": "tok"})
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual(self.seen[0]["base_url"], "https://new.example")
        self.assertEqual(self.read_config(), before)
        bad = self.client.post("/api/test", json={"name": "x", "base_url": "https://new.example", "user_id": "5", "access_token": "bad"}).get_json()
        self.assertFalse(bad["ok"])
        self.assertNotIn("<html>", bad["error"])

    def test_blank_token_reuses_existing_only_when_key_matches(self):
        body = {"name": "A", "base_url": "https://a.example", "user_id": "1", "access_token": ""}
        ok = self.client.post("/api/test?index=0&expect=https://a.example|1", json=body)
        self.assertTrue(ok.get_json()["ok"])
        self.assertEqual(self.seen[-1]["access_token"], "tok-a")
        self.assertEqual(self.client.post("/api/test?index=0&expect=other", json=body).status_code, 409)
        self.assertEqual(self.client.post("/api/test", json=body).status_code, 400)  # 新建时令牌必填

    def test_clock_and_timezone_check(self):
        clock = self.client.get("/api/configs").get_json()["schedule"]["clock"]
        self.assertEqual(set(clock), {"now", "offset_minutes", "utc_offset", "tz"})
        other = clock["offset_minutes"] + 60
        checks = self.client.get("/api/diagnostics?client_offset=%d" % other).get_json()["checks"]
        tz = [c for c in checks if c["label"] == "服务器时区"][0]
        self.assertEqual(tz["status"], "warn")
        checks = self.client.get("/api/diagnostics?client_offset=%d" % clock["offset_minutes"]).get_json()["checks"]
        self.assertEqual([c for c in checks if c["label"] == "服务器时区"][0]["status"], "ok")


class FieldThresholdTest(_Base):
    def test_thresholds_are_cleaned_and_public(self):
        f = app_module.clean_field({"label": "余额", "type": "amount", "curl": "curl https://x.example/api", "json_path": "a",
                                    "warn_below": "5"})
        self.assertEqual(f["warn_below"], 5.0)
        t = app_module.clean_field({"label": "到期", "type": "time", "curl": "curl https://x.example/api", "json_path": "a",
                                    "warn_days": "30"})
        self.assertEqual(t["warn_days"], 30)
        self.assertEqual(app_module.public_field(t)["warn_days"], 30)
        # 0 = 不提醒（额度重置时间这类只看倒计时的字段）
        quiet = app_module.clean_field({"label": "重置", "type": "time", "curl": "curl https://x.example/api", "json_path": "a",
                                        "warn_days": "0"})
        self.assertEqual(quiet["warn_days"], 0)
        self.assertEqual(app_module.public_field(quiet)["warn_days"], 0)
        for bad in ({"type": "amount", "warn_below": "abc"}, {"type": "time", "warn_days": "-1"}, {"type": "time", "warn_days": "400"}):
            with self.assertRaises(ValueError):
                app_module.clean_field(dict({"label": "x", "curl": "curl https://x.example/api", "json_path": "a"}, **bad))


class DailyBalanceTest(_Base):
    def test_one_point_per_day_capped(self):
        key = metrics_key(CFG_A)
        app_module.update_metric(CFG_A, {"wallet_balance": "$10.00"})
        app_module.update_metric(CFG_A, {"wallet_balance": "$9.50"})   # 同一天只留最后一次
        snap = read_metrics()[key]
        self.assertEqual(snap["daily"], [[app_module._today_str(), "$9.50"]])
        metrics = read_metrics()
        metrics[key]["daily"] = [["2020-01-%02d" % (i % 28 + 1), str(i)] for i in range(120)]
        app_module.Path(app_module.METRICS_FILE).write_text(__import__("json").dumps(metrics), encoding="utf-8")
        app_module.update_metric(CFG_A, {"wallet_balance": "$9.00"})
        daily = read_metrics()[key]["daily"]
        self.assertEqual(len(daily), app_module.METRIC_DAILY_KEEP)
        self.assertEqual(daily[-1], [app_module._today_str(), "$9.00"])
        # 失败不产生点
        app_module.record_outcomes([CFG_A], [{"name": "A", "status": "failed", "message": "x"}])
        self.assertEqual(read_metrics()[key]["daily"], daily)


class BookmarkFromConfigTest(_Base):
    def test_creates_balance_field_once(self):
        self.write_config({"configs": [dict(CFG_A)], "bookmarks": [], "link_groups": []})
        resp = self.client.post("/api/bookmarks/from_config/0?expect=https://a.example|1")
        self.assertEqual(resp.get_json(), {"ok": True, "index": 0})
        bm = self.read_config()["bookmarks"][0]
        self.assertEqual((bm["name"], bm["url"]), ("A", "https://a.example"))
        field = bm["fields"][0]
        self.assertEqual((field["url"], field["json_path"], field["divisor"]), ("https://a.example/api/user/self", "data.quota", 500000))
        self.assertEqual(field["headers"]["Authorization"], "Bearer tok-a")
        self.assertEqual(field["headers"]["New-Api-User"], "1")
        # 公开视图里没有令牌
        self.assertNotIn("tok-a", self.client.get("/api/configs").get_data(as_text=True).replace('"access_token"', ""))
        self.assertEqual(self.client.post("/api/bookmarks/from_config/0").status_code, 409)       # 已经在看板里
        self.assertEqual(self.client.post("/api/bookmarks/from_config/0?expect=x").status_code, 409)

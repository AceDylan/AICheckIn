# -*- coding: utf-8 -*-
"""站点看板定时自动刷新：配置归一化、调度节奏、以及「后台刷新不冲掉用户编辑」。

最后一条是这批改动的重点：后台任务可能跑几十秒，其间用户完全可能保存过配置。
把任务开始时读到的整份 store 写回去会静默丢掉这些编辑，所以写回改成按
(收藏标识, 字段 id) 合并快照。
"""
import time
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import (  # noqa: E402
    DEFAULT_REFRESH_INTERVAL, REFRESH_INTERVALS, app, bookmark_snapshot_key,
    collect_field_snapshots, normalize_refresh, persist_field_snapshots, read_store,
)

FIELD = {
    "id": "f_balance", "label": "余额", "type": "amount", "enabled": True,
    "method": "GET", "url": "https://api.example.com/me", "headers": {}, "body": None,
    "json_path": "data.balance",
}
BOOKMARK = {"name": "看板站", "url": "https://demo.example", "fields": [dict(FIELD)]}


class NormalizeRefreshTest(unittest.TestCase):
    def test_missing_config_gets_safe_defaults(self):
        cfg = normalize_refresh(None)
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["interval_minutes"], DEFAULT_REFRESH_INTERVAL)
        self.assertEqual(cfg["last_run_ts"], 0.0)

    def test_unsupported_interval_falls_back(self):
        # 只接受固定档位，免得有人填 1 分钟把被监控的站点打爆。
        self.assertEqual(normalize_refresh({"interval_minutes": 7})["interval_minutes"], DEFAULT_REFRESH_INTERVAL)
        self.assertEqual(normalize_refresh({"interval_minutes": 0})["interval_minutes"], DEFAULT_REFRESH_INTERVAL)

    def test_string_values_are_coerced(self):
        cfg = normalize_refresh({"enabled": 1, "interval_minutes": "30", "last_run_ts": "12.5"})
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["interval_minutes"], 30)
        self.assertEqual(cfg["last_run_ts"], 12.5)

    def test_garbage_timestamp_does_not_explode(self):
        self.assertEqual(normalize_refresh({"last_run_ts": "nope"})["last_run_ts"], 0.0)


class RefreshSettingsApiTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(RefreshSettingsApiTest, self).setUp()
        self.write_config({"configs": [], "bookmarks": [BOOKMARK], "link_groups": []})
        self.client = app.test_client()

    def test_defaults_are_exposed(self):
        data = self.client.get("/api/configs").get_json()["refresh"]
        self.assertFalse(data["enabled"])
        self.assertEqual(data["interval_minutes"], DEFAULT_REFRESH_INTERVAL)
        self.assertEqual(data["intervals"], list(REFRESH_INTERVALS))

    def test_settings_round_trip(self):
        resp = self.client.put("/api/settings", json={"refresh": {"enabled": True, "interval_minutes": 30}})
        self.assertTrue(resp.get_json()["ok"])
        data = self.client.get("/api/configs").get_json()["refresh"]
        self.assertTrue(data["enabled"])
        self.assertEqual(data["interval_minutes"], 30)
        self.assertEqual(self.read_config()["refresh"]["interval_minutes"], 30)

    def test_bad_interval_is_rejected(self):
        resp = self.client.put("/api/settings", json={"refresh": {"interval_minutes": 3}})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("刷新间隔", resp.get_json()["error"])

    def test_changing_interval_restarts_the_clock(self):
        # 从 1 天改到 15 分钟后不该还要等满一天。
        self.client.put("/api/settings", json={"refresh": {"enabled": True, "interval_minutes": 1440}})
        store = read_store()
        store["refresh"]["last_run_ts"] = time.time()
        app_module.write_store(store)
        self.client.put("/api/settings", json={"refresh": {"interval_minutes": 15}})
        self.assertEqual(read_store()["refresh"]["last_run_ts"], 0.0)

    def test_settings_write_needs_admin(self):
        orig = app_module.ADMIN_PASSWORD
        app_module.ADMIN_PASSWORD = "pwd-for-refresh-test"
        try:
            self.assertEqual(self.client.put("/api/settings", json={"refresh": {"enabled": True}}).status_code, 403)
        finally:
            app_module.ADMIN_PASSWORD = orig


class SnapshotMergeTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(SnapshotMergeTest, self).setUp()
        self.write_config({"configs": [], "bookmarks": [BOOKMARK], "link_groups": []})

    def test_snapshot_round_trip(self):
        store = read_store()
        store["bookmarks"][0]["fields"][0].update({"value": "12.34", "updated_at": "2026-09-15 10:00:00"})
        snapshots = {bookmark_snapshot_key(store["bookmarks"][0]): collect_field_snapshots(store["bookmarks"][0])}
        persist_field_snapshots(snapshots)
        saved = read_store()["bookmarks"][0]["fields"][0]
        self.assertEqual(saved["value"], "12.34")
        self.assertEqual(saved["updated_at"], "2026-09-15 10:00:00")

    def test_concurrent_edit_is_not_clobbered(self):
        # 后台任务开始时读到的 store
        stale = read_store()
        stale["bookmarks"][0]["fields"][0]["value"] = "12.34"
        snapshots = {bookmark_snapshot_key(stale["bookmarks"][0]): collect_field_snapshots(stale["bookmarks"][0])}

        # 期间用户加了一个分组、改了字段标签
        live = read_store()
        live["link_groups"] = [{"id": "new", "name": "新分组", "icon": "folder", "color": "mint", "links": []}]
        live["bookmarks"][0]["fields"][0]["label"] = "钱包余额"
        app_module.write_store(live)

        persist_field_snapshots(snapshots)

        after = read_store()
        self.assertEqual([g["id"] for g in after["link_groups"]], ["new"])   # 用户的新分组还在
        self.assertEqual(after["bookmarks"][0]["fields"][0]["label"], "钱包余额")  # 改名还在
        self.assertEqual(after["bookmarks"][0]["fields"][0]["value"], "12.34")   # 快照也贴上了

    def test_snapshot_for_a_deleted_bookmark_is_dropped(self):
        stale = read_store()
        stale["bookmarks"][0]["fields"][0]["value"] = "12.34"
        snapshots = {bookmark_snapshot_key(stale["bookmarks"][0]): collect_field_snapshots(stale["bookmarks"][0])}
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})
        persist_field_snapshots(snapshots)   # 不该抛错
        self.assertEqual(read_store()["bookmarks"], [])

    def test_cleared_error_is_removed_not_left_behind(self):
        # 上一轮失败写了 error，这一轮成功后必须把它清掉，否则卡片一直挂着旧报错。
        store = read_store()
        store["bookmarks"][0]["fields"][0]["error"] = "HTTP 500"
        app_module.write_store(store)
        fresh = read_store()
        fresh["bookmarks"][0]["fields"][0].pop("error", None)
        fresh["bookmarks"][0]["fields"][0]["value"] = "9.9"
        snapshots = {bookmark_snapshot_key(fresh["bookmarks"][0]): collect_field_snapshots(fresh["bookmarks"][0])}
        persist_field_snapshots(snapshots)
        saved = read_store()["bookmarks"][0]["fields"][0]
        self.assertNotIn("error", saved)
        self.assertEqual(saved["value"], "9.9")

    def test_refresh_run_stamps_the_clock(self):
        persist_field_snapshots({}, refresh_run=True)
        cfg = read_store()["refresh"]
        self.assertTrue(cfg["last_run_time"])
        self.assertGreater(cfg["last_run_ts"], 0)

    def test_schedule_run_stamps_without_touching_refresh(self):
        persist_field_snapshots({}, schedule_run={"last_run_date": "2026-09-15", "last_run_time": "x"})
        store = read_store()
        self.assertEqual(store["schedule"]["last_run_date"], "2026-09-15")
        self.assertEqual(store["refresh"]["last_run_ts"], 0.0)


class RefreshTickTest(StoreIsolationMixin, unittest.TestCase):
    """调度节奏：开关、间隔、无字段跳过、并发跳过。刷新本身用假函数替换，不打网络。"""

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(RefreshTickTest, self).setUp()
        self.calls = []
        real = app_module.refresh_all_bookmarks
        self.addCleanup(setattr, app_module, "refresh_all_bookmarks", real)

        def fake(store):
            self.calls.append(time.time())
            return {}

        app_module.refresh_all_bookmarks = fake
        self.write_config({"configs": [], "bookmarks": [BOOKMARK], "link_groups": [],
                           "refresh": {"enabled": True, "interval_minutes": 60}})

    def test_disabled_does_nothing(self):
        self.write_config({"configs": [], "bookmarks": [BOOKMARK], "link_groups": [],
                           "refresh": {"enabled": False}})
        app_module._refresh_tick()
        self.assertEqual(self.calls, [])

    def test_first_run_happens_immediately(self):
        app_module._refresh_tick()
        self.assertEqual(len(self.calls), 1)

    def test_second_run_waits_for_the_interval(self):
        app_module._refresh_tick()
        app_module._refresh_tick()
        self.assertEqual(len(self.calls), 1)

    def test_runs_again_once_the_interval_elapsed(self):
        app_module._refresh_tick()
        store = read_store()
        store["refresh"]["last_run_ts"] = time.time() - 61 * 60
        app_module.write_store(store)
        app_module._refresh_tick()
        self.assertEqual(len(self.calls), 2)

    def test_clock_moving_backwards_still_runs(self):
        store = read_store()
        store["refresh"]["last_run_ts"] = time.time() + 9999
        app_module.write_store(store)
        app_module._refresh_tick()
        self.assertEqual(len(self.calls), 1)

    def test_bookmarks_without_fields_are_skipped_entirely(self):
        self.write_config({"configs": [], "bookmarks": [{"name": "x", "url": "https://x.example"}],
                           "link_groups": [], "refresh": {"enabled": True, "interval_minutes": 60}})
        app_module._refresh_tick()
        self.assertEqual(self.calls, [])

    def test_overlapping_runs_are_skipped(self):
        # 站点慢的时候不堆叠请求：上一轮还没跑完就跳过这一轮。
        app_module._refresh_lock.acquire()
        try:
            app_module._refresh_tick()
        finally:
            app_module._refresh_lock.release()
        self.assertEqual(self.calls, [])


class RefreshUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.html = app.test_client().get("/").get_data(as_text=True)

    def test_settings_panel_exists(self):
        for hook in ('id="refreshEnabled"', 'id="refreshInterval"', 'id="refreshStatus"'):
            self.assertIn(hook, self.html)
        # 开关与间隔改了就存，不再另有「保存刷新」按钮。
        self.assertNotIn('id="saveRefresh"', self.html)
        self.assertIn("$('refreshEnabled').addEventListener('change', saveRefreshNow)", self.html)
        self.assertIn("$('refreshInterval').addEventListener('change', saveRefreshNow)", self.html)

    def test_interval_labels_are_human_readable(self):
        self.assertIn("function intervalLabel", self.html)

    def test_warns_when_the_scheduler_thread_is_off(self):
        self.assertIn("GYQD_SCHEDULER=0", self.html)


if __name__ == "__main__":
    unittest.main()

import json
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer

# 隔离运行：不启动调度线程，配置文件指向本用例专属临时目录，关闭管理密码。
from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app  # noqa: E402

LEGACY = {
    "name": "old", "url": "https://x.example",
    "balance_config": {"method": "GET", "url": "https://x.example/api", "headers": {"cookie": "c=1"},
                       "body": None, "json_path": ".data.balance", "divisor": 100.0},
    "balance": "1.23", "balance_updated_at": "2026-09-01 08:00:00",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data": {"expire": 1788522599794, "balance_cents": 12345, "note": "hi"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class BookmarkFieldsApiTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def setUp(self):
        super(BookmarkFieldsApiTest, self).setUp()
        self.write_config({"configs": [], "proxy_url": "", "schedule": {}, "bookmarks": [LEGACY]})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()   # shutdown 只停循环，监听套接字要另外关，否则留下 ResourceWarning

    def test_refresh_merges_into_the_latest_config(self):
        # 等对方接口的那几秒里别处改了配置：刷新结果只合并回去，不能把请求开始时读到的旧配置整份写回。
        def slow_refresh(bookmark, proxy_url, field_id=None):
            store = app_module.read_store()
            store["link_groups"] = [{"id": "g", "name": "刷新期间新建的分组", "links": []}]
            app_module.write_store(store)
            for f in bookmark["fields"]:
                f["value"], f["updated_at"] = "9.99", "2026-09-26 10:00:00"
            return [{"ok": True, "label": f["label"], "value": "9.99"} for f in bookmark["fields"]]
        with patch.object(app_module, "refresh_bookmark_fields", slow_refresh):
            rf = self.client.post("/api/bookmarks/0/refresh_balance").get_json()
        self.assertTrue(rf["ok"], rf)
        store = app_module.read_store()
        self.assertEqual([g["name"] for g in store["link_groups"]], ["刷新期间新建的分组"])
        self.assertEqual(store["bookmarks"][0]["fields"][0]["value"], "9.99")
        self.assertEqual(store["bookmarks"][0]["balance"], "9.99")

    def test_changing_only_the_reminder_keeps_the_values(self):
        # 「不再提醒…」→ 保存：只改了提醒天数，请求配置没变，已取到的数值不能被清成「等待首次刷新」。
        curl = "curl 'https://api.example.com/v1/quota' -H 'Authorization: Bearer x'"
        self.client.post("/api/bookmarks", json={"name": "r5", "url": "https://r5.example", "fields": [
            {"label": "刷新时间", "type": "time", "curl": curl, "json_path": "data.reset"}]})
        store = app_module.read_store()
        idx = len(store["bookmarks"]) - 1
        store["bookmarks"][idx]["fields"][0].update(value="2026-09-26 15:11:40", raw=1790406700, updated_at="2026-09-26 10:00:00")
        app_module.write_store(store)
        f = self.client.get("/api/bookmarks/%d/secret" % idx).get_json()["bookmark"]["fields"][0]
        payload = {"id": f["id"], "label": f["label"], "type": "time", "enabled": True, "json_path": f["json_path"],
                   "curl": f["curl"], "warn_days": 0}
        up = self.client.put("/api/bookmarks/%d" % idx, json={"name": "r5", "url": "https://r5.example", "fields": [payload]}).get_json()
        self.assertTrue(up["ok"], up)
        saved = app_module.read_store()["bookmarks"][idx]["fields"][0]
        self.assertEqual((saved["warn_days"], saved.get("value"), saved.get("raw")), (0, "2026-09-26 15:11:40", 1790406700))

    def _failing_site(self):
        curl = "curl 'https://api.example.com/v1/balance' -H 'Authorization: Bearer x'"
        self.client.post("/api/bookmarks", json={"name": "EXA", "url": "https://exa.example", "fields": [
            {"label": "余额", "type": "amount", "curl": curl, "json_path": "data.bal"}]})
        store = app_module.read_store()
        idx = len(store["bookmarks"]) - 1
        store["bookmarks"][idx]["fields"][0].update(value="27.47", error="HTTP 401", updated_at="2026-09-26 10:00:00")
        app_module.write_store(store)
        return idx, store["bookmarks"][idx]["fields"][0]["id"]

    def test_failing_field_can_be_snoozed_and_restored(self):
        import time as _t
        idx, fid = self._failing_site()
        resp = self.client.post("/api/bookmarks/%d/fields/%s/snooze" % (idx, fid), json={"days": 7}).get_json()
        self.assertTrue(resp["ok"], resp)
        until = app_module.read_store()["bookmarks"][idx]["fields"][0]["snooze_until"]
        self.assertAlmostEqual(until, _t.time() + 7 * 86400, delta=60)
        pub = self.client.get("/api/configs").get_json()["bookmarks"][idx]["fields"][0]
        self.assertEqual(pub["snooze_until"], until)          # 页面据此不算预警
        self.assertEqual(pub["error"], "HTTP 401")            # 失败照样显示
        self.assertTrue(self.client.post("/api/bookmarks/%d/fields/%s/snooze" % (idx, fid), json={"days": 0}).get_json()["ok"])
        self.assertNotIn("snooze_until", app_module.read_store()["bookmarks"][idx]["fields"][0])

    def test_snooze_input_is_validated(self):
        idx, fid = self._failing_site()
        for bad in ({"days": 31}, {"days": -1}, {"days": "abc"}):
            self.assertEqual(self.client.post("/api/bookmarks/%d/fields/%s/snooze" % (idx, fid), json=bad).status_code, 400, bad)
        self.assertEqual(self.client.post("/api/bookmarks/%d/fields/nope/snooze" % idx, json={"days": 7}).status_code, 404)
        self.assertEqual(self.client.post("/api/bookmarks/99/fields/%s/snooze" % fid, json={"days": 7}).status_code, 404)
        self.assertEqual(self.client.post("/api/bookmarks/%d/fields/%s/snooze?expect=other|x" % (idx, fid), json={"days": 7}).status_code, 409)

    def test_snooze_survives_an_edit_that_keeps_the_request(self):
        idx, fid = self._failing_site()
        self.client.post("/api/bookmarks/%d/fields/%s/snooze" % (idx, fid), json={"days": 7})
        f = self.client.get("/api/bookmarks/%d/secret" % idx).get_json()["bookmark"]["fields"][0]
        keep = {"id": f["id"], "label": "余额（改名）", "type": "amount", "enabled": True, "json_path": f["json_path"], "curl": f["curl"]}
        self.client.put("/api/bookmarks/%d" % idx, json={"name": "EXA", "url": "https://exa.example", "fields": [keep]})
        self.assertIn("snooze_until", app_module.read_store()["bookmarks"][idx]["fields"][0])
        # 换了 cURL（多半是修好了）：重新开始提醒。
        fresh = dict(keep, curl="curl 'https://api.example.com/v2/balance' -H 'Authorization: Bearer y'")
        self.client.put("/api/bookmarks/%d" % idx, json={"name": "EXA", "url": "https://exa.example", "fields": [fresh]})
        self.assertNotIn("snooze_until", app_module.read_store()["bookmarks"][idx]["fields"][0])

    def test_expired_or_absurd_snooze_is_dropped_when_cleaned(self):
        import time as _t
        base = {"label": "余额", "type": "amount", "curl": "curl https://x.example/api", "json_path": "a"}
        for until in (_t.time() - 10, _t.time() + 400 * 86400, "tomorrow", True):
            self.assertNotIn("snooze_until", app_module.clean_field(dict(base, snooze_until=until)), until)
        self.assertIn("snooze_until", app_module.clean_field(dict(base, snooze_until=_t.time() + 3 * 86400)))

    def test_successful_refreshes_leave_one_balance_point_per_day(self):
        idx, fid = self._failing_site()
        def refresh_to(value, error=""):
            def fake(bookmark, proxy_url, field_id=None):
                for f in bookmark["fields"]:
                    f["value"], f["updated_at"] = value, "2026-09-26 10:00:00"
                    if error:
                        f["error"] = error
                    else:
                        f.pop("error", None)
                return [{"ok": not error, "label": "余额", "value": value, "error": error}]
            with patch.object(app_module, "refresh_bookmark_fields", fake):
                self.client.post("/api/bookmarks/%d/refresh_balance" % idx)
        refresh_to("30.00")
        refresh_to("28.50")             # 同一天再刷：只留最后一次
        refresh_to("99.00", "HTTP 401")  # 失败：显示的是旧值，不记
        key = "https://exa.example|EXA|%s" % fid
        pts = app_module.read_field_history()[key]
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0][1], 28.5)
        # 首页数据里带上走势点，页面据此画线、估日均用量。
        pub = self.client.get("/api/configs").get_json()["bookmarks"][idx]["fields"][0]
        self.assertEqual(pub["daily"], pts)

    def test_history_keeps_ninety_days_and_drops_forgotten_sites(self):
        import datetime as _dt
        old_day = (_dt.date.today() - _dt.timedelta(days=200)).isoformat()
        with open(app_module._field_history_path(), "w", encoding="utf-8") as fh:
            json.dump({"https://gone.example|旧站|x": [[old_day, 1.0]]}, fh)
        idx, fid = self._failing_site()
        store = app_module.read_store()
        store["bookmarks"][idx]["fields"][0].pop("error", None)   # 这回取数成功了
        app_module.write_store(store)
        app_module.persist_field_snapshots({}, refresh_run=True)
        history = app_module.read_field_history()
        self.assertNotIn("https://gone.example|旧站|x", history)   # 200 天没更新的旧序列清掉
        self.assertEqual(history["https://exa.example|EXA|%s" % fid][-1][1], 27.47)

    def test_full_flow(self):
        base = "http://127.0.0.1:%d" % self.port
        c = self.client
        data = c.get("/api/configs").get_json()
        self.assertEqual(data["bookmarks"][0]["fields"][0]["id"], "balance")

        resp = c.post("/api/bookmarks", json={"name": "svc", "url": "https://svc.example", "fields": [
            {"label": "余额", "type": "amount", "curl": "curl '%s/api'" % base, "json_path": "data.balance_cents", "divisor": 100, "unit": "USD"},
            {"label": "到期时间", "type": "time", "curl": "curl '%s/api'" % base, "json_path": "data.expire"},
            {"label": "备注", "type": "raw", "enabled": False, "curl": "curl '%s/api'" % base, "json_path": "data.note"},
        ]}).get_json()
        self.assertTrue(resp["ok"], resp)
        idx = resp["index"]

        pv = c.post("/api/bookmarks/field_preview", json={"label": "t", "type": "time", "curl": "curl '%s/api'" % base, "json_path": "data.expire"}).get_json()
        self.assertTrue(pv["ok"], pv)
        self.assertEqual(pv["value"], "2026-09-04 19:49:59")
        bad = c.post("/api/bookmarks/field_preview", json={"label": "t", "type": "time", "curl": "curl '%s/api'" % base, "json_path": "data.nope"}).get_json()
        self.assertFalse(bad["ok"])

        rf = c.post("/api/bookmarks/%d/refresh_balance" % idx).get_json()
        self.assertTrue(rf["ok"], rf)
        self.assertEqual(len(rf["results"]), 2)
        fields = rf["bookmark"]["fields"]
        self.assertEqual(fields[0]["value"], "123.45")
        self.assertEqual(fields[1]["value"], "2026-09-04 19:49:59")
        self.assertNotIn("value", fields[2])
        self.assertEqual(rf["balance"], "123.45")

        one = c.post("/api/bookmarks/%d/fields/%s/refresh" % (idx, fields[2]["id"])).get_json()
        self.assertTrue(one["ok"], one)
        self.assertEqual(one["results"][0]["value"], "hi")
        self.assertEqual(c.post("/api/bookmarks/%d/fields/nope/refresh" % idx).status_code, 404)

        with open(app_module.CONFIG_FILE, encoding="utf-8") as fh:
            bm = json.load(fh)["bookmarks"][idx]
        self.assertEqual(bm["balance"], "123.45")
        self.assertEqual(bm["balance_config"]["json_path"], "data.balance_cents")
        self.assertEqual(len(bm["fields"]), 3)

        fields[0]["enabled"] = False
        up = c.put("/api/bookmarks/%d" % idx, json={"name": "svc", "url": "https://svc.example", "fields": fields}).get_json()
        self.assertTrue(up["ok"], up)
        got = c.get("/api/configs").get_json()["bookmarks"][idx]
        self.assertNotIn("balance_config", got)
        self.assertEqual(got["fields"][1]["value"], "2026-09-04 19:49:59")

        up = c.put("/api/bookmarks/%d" % idx, json={"name": "svc", "url": "https://svc.example", "fields": []}).get_json()
        self.assertTrue(up["ok"])
        self.assertEqual(c.get("/api/configs").get_json()["bookmarks"][idx]["fields"], [])
        self.assertEqual(c.post("/api/bookmarks/%d/refresh_balance" % idx).status_code, 400)

        imp = c.post("/api/configs/import", json={"configs": [], "bookmarks": [LEGACY]}).get_json()
        self.assertTrue(imp["ok"], imp)
        self.assertEqual(c.get("/api/configs").get_json()["bookmarks"][0]["fields"][0]["value"], "1.23")


if __name__ == "__main__":
    unittest.main()

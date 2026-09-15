import json
import threading
import unittest
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

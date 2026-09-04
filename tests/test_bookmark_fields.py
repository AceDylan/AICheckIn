import json
import os
import tempfile
import unittest

# 隔离运行：不启动调度线程，配置文件指向临时目录，关闭管理密码。
os.environ["GYQD_SCHEDULER"] = "0"
os.environ.setdefault("GYQD_CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "config.json"))
os.environ["GYQD_ADMIN_PASSWORD"] = ""

from app import clean_bookmark, convert_value, normalize_bookmark  # noqa: E402

LEGACY = {
    "name": "old", "url": "https://x.example",
    "balance_config": {"method": "GET", "url": "https://x.example/api", "headers": {"cookie": "c=1"},
                       "body": None, "json_path": ".data.balance", "divisor": 100.0},
    "balance": "1.23", "balance_updated_at": "2026-09-01 08:00:00",
}


class ConvertValueTest(unittest.TestCase):
    def test_ms_timestamp_to_beijing(self):
        display, raw, err = convert_value(1788522599794, {"type": "time"})
        self.assertEqual(err, "")
        self.assertEqual(display, "2026-09-04 19:49:59")
        self.assertAlmostEqual(raw, 1788522599.794, places=3)

    def test_seconds_string_and_explicit_units(self):
        self.assertEqual(convert_value(1788522599, {"type": "time"})[0], "2026-09-04 19:49:59")
        self.assertEqual(convert_value("1788522599794", {"type": "time", "ts_unit": "auto"})[0], "2026-09-04 19:49:59")
        self.assertEqual(convert_value(1788522599794, {"type": "time", "ts_unit": "ms"})[0], "2026-09-04 19:49:59")
        self.assertEqual(convert_value("1788522599", {"type": "time", "ts_unit": "s"})[0], "2026-09-04 19:49:59")

    def test_iso_strings(self):
        self.assertEqual(convert_value("2026-09-04T11:49:59Z", {"type": "time"})[0], "2026-09-04 19:49:59")
        self.assertEqual(convert_value("2026-09-04T19:49:59+08:00", {"type": "time"})[0], "2026-09-04 19:49:59")
        self.assertEqual(convert_value("2026-09-04 19:49:59", {"type": "time"})[0], "2026-09-04 19:49:59")

    def test_time_errors(self):
        self.assertTrue(convert_value("abc", {"type": "time"})[2])
        self.assertTrue(convert_value(True, {"type": "time"})[2])
        self.assertTrue(convert_value("", {"type": "time"})[2])

    def test_amount_keeps_legacy_behaviour(self):
        self.assertEqual(convert_value(12345, {"type": "amount", "divisor": 100})[0], "123.45")
        self.assertEqual(convert_value("85.79980481", {"type": "amount"})[0], "85.79980481")
        self.assertEqual(convert_value(3, {"type": "amount"})[0], "3.00")
        self.assertEqual(convert_value("200", {"type": "amount", "divisor": 100})[0], "2.00")

    def test_raw(self):
        self.assertEqual(convert_value({"a": 1}, {"type": "raw"})[0], json.dumps({"a": 1}))
        self.assertEqual(convert_value(" x ", {"type": "raw"})[0], "x")


class LegacyMigrationTest(unittest.TestCase):
    def test_normalize_synthesises_balance_field(self):
        b = normalize_bookmark(json.loads(json.dumps(LEGACY)))
        self.assertEqual(len(b["fields"]), 1)
        f = b["fields"][0]
        self.assertEqual((f["id"], f["type"], f["label"], f["value"], f["divisor"], f["enabled"]),
                         ("balance", "amount", "余额", "1.23", 100.0, True))
        self.assertEqual(b["balance"], "1.23")
        self.assertEqual(b["balance_config"]["json_path"], ".data.balance")

    def test_fields_payload_keeps_snapshot_and_mirror(self):
        payload = {"name": "old", "url": "https://x.example", "fields": [
            {"id": "balance", "label": "余额", "type": "amount", "enabled": True, "method": "GET",
             "url": "https://x.example/api", "headers": {"cookie": "c=1"}, "body": None,
             "json_path": ".data.balance", "divisor": 100},
            {"label": "到期", "type": "time", "json_path": "data.expire", "ts_unit": "auto",
             "curl": "curl 'https://x.example/api/expire' -H 'authorization: Bearer t'"},
        ]}
        b = clean_bookmark(payload, existing=LEGACY)
        self.assertEqual([f["type"] for f in b["fields"]], ["amount", "time"])
        self.assertEqual(b["fields"][0]["value"], "1.23")
        self.assertEqual(b["fields"][1]["url"], "https://x.example/api/expire")
        self.assertEqual(b["fields"][1]["tz"], "Asia/Shanghai")
        self.assertTrue(b["fields"][1]["id"].startswith("f_"))
        self.assertEqual(b["balance"], "1.23")
        self.assertEqual(b["balance_config"]["url"], "https://x.example/api")

    def test_changed_config_drops_snapshot(self):
        payload = {"name": "old", "url": "https://x.example", "fields": [
            {"id": "balance", "label": "余额", "type": "amount", "method": "GET",
             "url": "https://x.example/api2", "headers": {}, "json_path": ".data.balance"}]}
        b = clean_bookmark(payload, existing=LEGACY)
        self.assertNotIn("value", b["fields"][0])
        self.assertNotIn("balance", b)

    def test_legacy_client_payload_still_accepted(self):
        payload = {"name": "n", "url": "https://x.example",
                   "balance_config": {"curl": "curl 'https://x.example/b' -H 'cookie: a=b'", "json_path": "bal", "divisor": 100}}
        b = clean_bookmark(payload)
        self.assertEqual(b["fields"][0]["id"], "balance")
        self.assertEqual(b["balance_config"]["divisor"], 100.0)
        b2 = clean_bookmark({"name": "n", "url": "https://x.example", "balance_config": None}, existing=b)
        self.assertEqual(b2["fields"], [])
        self.assertNotIn("balance_config", b2)

    def test_no_amount_field_removes_legacy_keys(self):
        payload = {"name": "n", "url": "https://x.example", "fields": [
            {"label": "到期", "type": "time", "curl": "curl https://x.example/e", "json_path": "t"}]}
        b = clean_bookmark(payload, existing=LEGACY)
        self.assertNotIn("balance_config", b)
        self.assertNotIn("balance", b)

    def test_validation_errors(self):
        bad = [
            {"label": "", "type": "time", "curl": "curl https://x/e", "json_path": "t"},
            {"label": "a", "type": "nope", "curl": "curl https://x/e", "json_path": "t"},
            {"label": "a", "type": "amount", "curl": "curl https://x/e", "json_path": "t", "divisor": -1},
            {"label": "a", "type": "time", "curl": "curl https://x/e", "json_path": "t", "tz": "Mars/Base"},
            {"label": "a", "type": "time", "curl": "curl https://x/e", "json_path": ""},
        ]
        for f in bad:
            with self.assertRaises(ValueError):
                clean_bookmark({"name": "n", "url": "https://x", "fields": [f]})


if __name__ == "__main__":
    unittest.main()

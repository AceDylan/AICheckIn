# -*- coding: utf-8 -*-
"""站点看板预警条：把「取数失败 / 已过期 / 即将到期」汇总到列表顶部，点一下只看这一类。

分类逻辑（fieldAlert / bookmarkAlert）是纯函数，抽到 node 里按固定时间戳验证。
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from tests._support import app_module  # noqa: F401  须早于 app 导入
from app import app  # noqa: E402

NODE = shutil.which("node")
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "index.html")

DAY = 86400


def _script():
    with open(TEMPLATE, encoding="utf-8") as fh:
        blob = fh.read()
    start = blob.index("const SOON_DAYS = 7;")
    end = blob.index("const ALERT_META = {")
    return blob[start:end]


@unittest.skipIf(NODE is None, "未安装 node，跳过分类逻辑验证")
class AlertClassificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.logic = _script()

    def classify(self, fields):
        script = ("function enabledFields(b) { return (b.fields || []).filter(f => f.enabled !== false); }\n"
                  + self.logic
                  + "\nconst FIELDS = %s;\n" % json.dumps(fields)
                  + "console.log(JSON.stringify({"
                    "fields: FIELDS.map(fieldAlert), bookmark: bookmarkAlert({ fields: FIELDS })}));")
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout.strip())
        finally:
            os.unlink(path)

    def test_error_field_is_flagged(self):
        out = self.classify([{"id": "a", "type": "amount", "error": "HTTP 500"}])
        self.assertEqual(out["fields"], ["error"])
        self.assertEqual(out["bookmark"], "error")

    def test_custom_thresholds(self):
        # 字段自设「提前 30 天提醒」：20 天后到期算快到期；默认 7 天的字段不算。
        out = self.classify([{"id": "a", "type": "time", "raw": time.time() + 20 * DAY, "warn_days": 30},
                             {"id": "b", "type": "time", "raw": time.time() + 20 * DAY}])
        self.assertEqual(out["fields"], ["soon", ""])
        # 余额低于阈值：余额偏低，排在「快到期」前面。
        out = self.classify([{"id": "a", "type": "amount", "value": "3.50", "warn_below": 5},
                             {"id": "b", "type": "amount", "value": "8", "warn_below": 5},
                             {"id": "c", "type": "time", "raw": time.time() + 2 * DAY}])
        self.assertEqual(out["fields"], ["low", "", "soon"])
        self.assertEqual(out["bookmark"], "low")

    def test_quiet_time_field_never_alerts(self):
        # 「提前几天提醒」填 0：额度重置时间这类倒计时，快到了、刚过了都不算预警。
        out = self.classify([{"id": "a", "type": "time", "raw": time.time() + 3600, "warn_days": 0},
                             {"id": "b", "type": "time", "raw": time.time() - 3600, "warn_days": 0},
                             {"id": "c", "type": "time", "raw": time.time() + 3600, "warn_days": "0"}])
        self.assertEqual(out["fields"], ["", "", ""])
        self.assertEqual(out["bookmark"], "")
        # 取数失败照样算失败。
        out = self.classify([{"id": "a", "type": "time", "error": "HTTP 401", "warn_days": 0}])
        self.assertEqual(out["bookmark"], "error")

    def test_past_expiry_is_expired(self):
        out = self.classify([{"id": "a", "type": "time", "raw": time.time() - DAY}])
        self.assertEqual(out["bookmark"], "expired")

    def test_expiry_within_a_week_is_soon(self):
        out = self.classify([{"id": "a", "type": "time", "raw": time.time() + 3 * DAY}])
        self.assertEqual(out["bookmark"], "soon")

    def test_far_future_expiry_is_quiet(self):
        out = self.classify([{"id": "a", "type": "time", "raw": time.time() + 90 * DAY}])
        self.assertEqual(out["bookmark"], "")

    def test_amount_fields_never_expire(self):
        out = self.classify([{"id": "a", "type": "amount", "value": "1.00", "raw": time.time() - DAY}])
        self.assertEqual(out["fields"], [""])

    def test_missing_or_garbage_timestamp_is_quiet(self):
        for raw in (None, 0, -5, "nope"):
            out = self.classify([{"id": "a", "type": "time", "raw": raw}])
            self.assertEqual(out["bookmark"], "", raw)

    def test_worst_state_wins(self):
        out = self.classify([
            {"id": "a", "type": "time", "raw": time.time() + 2 * DAY},   # soon
            {"id": "b", "type": "time", "raw": time.time() - DAY},       # expired
            {"id": "c", "type": "amount", "error": "boom"},              # error
        ])
        self.assertEqual(out["bookmark"], "error")

    def test_expired_outranks_soon(self):
        out = self.classify([
            {"id": "a", "type": "time", "raw": time.time() + 2 * DAY},
            {"id": "b", "type": "time", "raw": time.time() - DAY},
        ])
        self.assertEqual(out["bookmark"], "expired")

    def test_disabled_fields_do_not_raise_alerts(self):
        out = self.classify([{"id": "a", "type": "amount", "error": "boom", "enabled": False}])
        self.assertEqual(out["bookmark"], "")


class AlertUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_strip_markup_and_handler_exist(self):
        self.assertIn('id="bmAlerts"', self.html)
        self.assertIn("function renderBmAlerts", self.html)
        self.assertIn("data-alert=", self.html)

    def test_clicking_a_chip_toggles_the_filter(self):
        self.assertIn("bmAlertFilter = bmAlertFilter === btn.dataset.alert ? '' : btn.dataset.alert", self.html)

    def test_counts_ignore_the_active_filter(self):
        # 计数必须基于全部站点，否则一筛选数字就跟着变，没法当仪表盘看。
        self.assertIn("预警计数基于全部站点", self.html)

    def test_cards_carry_the_alert_class(self):
        self.assertIn("' alert-' + alert", self.html)

    def test_styles_exist(self):
        for rule in (".alert-strip", ".alert-chip.is-error", ".alert-chip.is-soon",
                     ".bookmark-card.alert-expired"):
            self.assertIn(rule, self.css)


if __name__ == "__main__":
    unittest.main()

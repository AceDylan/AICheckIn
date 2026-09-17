# -*- coding: utf-8 -*-
"""内联脚本的启动回归：把模板里的脚本放进 node + 极简 DOM 替身里真正执行一遍。

页面是单文件模板、没有构建步骤。语法错误有 node --check 挡着，但「脚本跑起来第一秒
就抛异常」这类问题只有真的执行一遍才会暴露——例如按 #history 打开页面时，路由在
脚本顶部就调用了 loadHistory，而它读取的常量声明在近两千行之后（TDZ ReferenceError）。
那次故障是静默的：async 函数把异常变成了未处理的 Promise rejection，页面照常显示，
只有运行记录永远加载不出来。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

NODE = shutil.which("node")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "templates", "index.html")
STUB = os.path.join(ROOT, "tests", "support", "dom_stub.js")

# loadConfigs 拿到的假数据：解了锁、带一个分组，够走完所有渲染分支。
CONFIGS_RESPONSE = {
    "ok": True,
    "configs": [{"name": "站点A", "base_url": "https://a.example", "user_id": "1",
                 "enabled": True, "token_masked": "••••••••", "has_token": True,
                 "has_turnstile": False, "turnstile": "", "metrics": None,
                 "checked_in_today": False}],
    "configs_hidden": False,
    "bookmarks": [{"name": "看板站", "url": "https://dash.example", "fields": [
        {"id": "f1", "label": "余额", "type": "amount", "enabled": True, "value": "1.00"}]}],
    "link_groups": [{"id": "daily", "name": "常用", "icon": "globe", "color": "mint",
                     "links": [{"id": "l1", "name": "Docs", "url": "https://docs.example",
                                "desc": "", "icon": "", "tags": [], "pinned": False}]}],
    "proxy_url": "", "proxy_configured": False,
    "schedule": {"enabled": False, "time": "08:30", "last_run_time": None, "last_run_date": None,
                 "retry_count": 0, "retry_limit": 3, "retry_delay_minutes": 30, "pending_today": 0},
    "refresh": {"enabled": False, "interval_minutes": 60, "last_run_time": "",
                "intervals": [15, 30, 60, 120, 360, 720, 1440]},
    "admin_required": True, "admin_unlocked": True, "scheduler_running": True,
    "private": False, "locked": False,
}
RESPONSES = {
    "/api/configs": CONFIGS_RESPONSE,
    "/api/history": {"ok": True, "history": []},
    "/api/diagnostics": {"ok": True, "checks": [], "generated_at": "2026-09-15 10:00:00"},
}


def _inline_script():
    with open(TEMPLATE, encoding="utf-8") as fh:
        blocks = re.findall(r"<script>(.*?)</script>", fh.read(), re.S)
    # 第一个块是主题预设（依赖 document.cookie），主逻辑在最后一个块。
    return blocks[-1]


@unittest.skipIf(NODE is None, "未安装 node，跳过启动执行验证")
class ScriptBootTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = _inline_script()
        with open(STUB, encoding="utf-8") as fh:
            cls.stub = fh.read()

    def boot(self, hash_value="", search=""):
        """按给定的初始 URL 执行一遍脚本，返回 {fetches, errors, rejections}。"""
        harness = "\n".join([
            self.stub,
            "globalThis.__RESPONSES = %s;" % json.dumps(RESPONSES),
            "location.hash = %s;" % json.dumps(hash_value),
            "location.search = %s;" % json.dumps(search),
            "try {",
            self.script,
            "} catch (e) { __CALLS.errors.push('同步抛出：' + (e && e.message || e)); }",
            # 给 microtask / setTimeout 留出时间，让 loadConfigs 的后续链条跑完。
            "setTimeout(() => { console.log(JSON.stringify(__CALLS)); }, 60);",
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as fh:
            fh.write(harness)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            return json.loads(proc.stdout.strip().splitlines()[-1])
        finally:
            os.unlink(path)

    # ---- 每种入口 URL 都必须干净启动 ----

    def test_boots_cleanly_for_every_entry_hash(self):
        for hash_value in ("", "#bookmarks", "#checkin", "#configs", "#history",
                           "#settings", "#links/monitor", "#links/daily"):
            out = self.boot(hash_value)
            self.assertEqual(out["errors"], [], "%s 启动时抛异常：%s" % (hash_value or "/", out["errors"]))
            self.assertEqual(out["rejections"], [],
                             "%s 启动时有未处理的 Promise 拒绝：%s" % (hash_value or "/", out["rejections"]))

    def test_configs_are_always_loaded(self):
        for hash_value in ("", "#history", "#settings"):
            self.assertIn("/api/configs", self.boot(hash_value)["fetches"], hash_value)

    # ---- 具体回归 ----

    def test_history_hash_actually_loads_history(self):
        # 回归：曾经 TDZ 静默失败，运行记录永远停在「暂无历史记录」。
        out = self.boot("#history")
        self.assertIn("/api/history", out["fetches"])

    def test_history_is_not_requested_before_configs(self):
        # 解锁状态得先知道，否则未解锁的访客会白白打一次 403。
        fetches = self.boot("#history")["fetches"]
        self.assertLess(fetches.index("/api/configs"), fetches.index("/api/history"))

    def test_settings_hash_loads_diagnostics_after_configs(self):
        fetches = self.boot("#settings")["fetches"]
        self.assertIn("/api/diagnostics", fetches)
        self.assertLess(fetches.index("/api/configs"), fetches.index("/api/diagnostics"))

    def test_library_hash_does_not_touch_admin_endpoints(self):
        fetches = self.boot("#bookmarks")["fetches"]
        for path in ("/api/history", "/api/diagnostics"):
            self.assertNotIn(path, fetches)

    def test_share_parameters_do_not_break_boot(self):
        out = self.boot("", "?url=https%3A%2F%2Fshared.example&title=Shared")
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["rejections"], [])

    def test_unknown_hash_falls_back_without_error(self):
        out = self.boot("#no-such-view")
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["rejections"], [])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""死链检查：探测分组内的链接是否还活着。

两条设计约束贯穿全篇：
- 只在用户点击时跑。后台定期扫全部收藏 = 拿自己的服务器周期性敲打别人的站点。
- 结论要能据以行动：403 的站点还活着，只是不给匿名探测，不该报成死链；
  容器连不到内网服务也不等于那条链接坏了。
"""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests._support import StoreIsolationMixin, app_module
from app import (  # noqa: E402
    app, check_group_links, classify_link_code, probe_link,
)

PASSWORD = "link-check-unit-test-4f81"


class _Handler(BaseHTTPRequestHandler):
    """按路径返回约定的状态码；/head-<code> 对 HEAD 返回该状态，GET 返回 200。"""

    def _code(self):
        path = self.path.split("?")[0]
        if path.startswith("/head-"):
            try:
                head_code = int(path.rsplit("-", 1)[1])
            except ValueError:
                head_code = 200
            return head_code if self.command == "HEAD" else 200
        if path.startswith("/status/"):
            try:
                return int(path.rsplit("/", 1)[1])
            except ValueError:
                return 200
        return 200

    def do_HEAD(self):
        self.send_response(self._code())
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        code = self._code()
        body = b"hello"
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ClassifyTest(unittest.TestCase):
    def test_success_and_redirects_are_alive(self):
        for code in (200, 204, 301, 302, 399):
            self.assertEqual(classify_link_code(code), "ok", code)

    def test_auth_walls_are_not_dead_links(self):
        # 服务器答了话就说明它还在；报成死链会淹没真正坏掉的那些。
        for code in (401, 403, 405, 406, 429, 503):
            self.assertEqual(classify_link_code(code), "blocked", code)

    def test_gone_pages_are_the_actionable_case(self):
        for code in (404, 410):
            self.assertEqual(classify_link_code(code), "missing", code)

    def test_other_failures_are_errors(self):
        for code in (500, 502, 418):
            self.assertEqual(classify_link_code(code), "error", code)

    def test_no_connection_is_reported_separately(self):
        # 可能真没了，也可能只是容器访问不到内网，两者要区分开。
        self.assertEqual(classify_link_code(0), "unreachable")


class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_live_url(self):
        self.assertEqual(probe_link(self.base + "/status/200"), ("ok", 200))

    def test_missing_url(self):
        self.assertEqual(probe_link(self.base + "/status/404"), ("missing", 404))

    def test_server_error(self):
        self.assertEqual(probe_link(self.base + "/status/500"), ("error", 500))

    def test_head_method_specific_failures_are_retried_with_get(self):
        # 有些站点对 HEAD 返回认证/不存在/不支持，但正常 GET 实际可访问。
        for code in (401, 404, 405, 410):
            self.assertEqual(probe_link(self.base + "/head-%d" % code), ("ok", 200), code)

    def test_unreachable_host(self):
        status, code = probe_link("http://127.0.0.1:1/nothing-here", timeout=2)
        self.assertEqual(status, "unreachable")
        self.assertEqual(code, 0)

    def test_garbage_url_does_not_raise(self):
        self.assertEqual(probe_link("not-a-url")[0], "unreachable")


class CheckGroupTest(ProbeTest):
    def group(self, *paths, **kwargs):
        links = []
        for i, path in enumerate(paths):
            links.append({"id": "l%d" % i, "name": "n%d" % i, "url": self.base + path,
                          "skip_check": kwargs.get("skip") == i})
        return {"id": "g", "name": "G", "links": links}

    def test_every_link_gets_a_result(self):
        out = check_group_links(self.group("/status/200", "/status/404", "/status/500"))
        self.assertEqual({k: v["status"] for k, v in out.items()},
                         {"l0": "ok", "l1": "missing", "l2": "error"})

    def test_results_carry_a_timestamp_and_code(self):
        out = check_group_links(self.group("/status/200"))
        self.assertEqual(out["l0"]["code"], 200)
        self.assertTrue(out["l0"]["at"])

    def test_skipped_links_are_not_probed(self):
        out = check_group_links(self.group("/status/200", "/status/404", skip=1))
        self.assertEqual(sorted(out), ["l0"])

    def test_empty_group_is_a_no_op(self):
        self.assertEqual(check_group_links({"id": "g", "name": "G", "links": []}), {})

    def test_group_of_only_skipped_links_is_a_no_op(self):
        self.assertEqual(check_group_links(self.group("/status/200", skip=0)), {})

    def test_budget_stops_early_instead_of_hanging(self):
        # 预算耗尽时宁可少检查几条，也不要把一个请求拖到 gunicorn 超时。
        group = self.group(*(["/status/200"] * 8))
        out = check_group_links(group, budget=0)
        self.assertEqual(out, {})


class CheckApiTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        super(CheckApiTest, self).setUp()
        self.write_config({"configs": [], "bookmarks": [], "link_groups": [{
            "id": "daily", "name": "常用", "icon": "globe", "color": "mint", "links": [
                {"id": "l1", "name": "活着", "url": self.base + "/status/200"},
                {"id": "l2", "name": "没了", "url": self.base + "/status/404"},
                {"id": "l3", "name": "内网", "url": "http://127.0.0.1:1/x", "skip_check": True},
            ]}]})
        self.client = app.test_client()

    def unlocked(self):
        c = app.test_client()
        c.environ_base["HTTP_X_ADMIN_PASSWORD"] = PASSWORD
        return c

    def test_requires_admin(self):
        orig = app_module.ADMIN_PASSWORD
        app_module.ADMIN_PASSWORD = PASSWORD
        try:
            self.assertEqual(self.client.post("/api/link_groups/daily/check").status_code, 403)
        finally:
            app_module.ADMIN_PASSWORD = orig

    def test_unknown_group_is_404(self):
        self.assertEqual(self.client.post("/api/link_groups/nope/check").status_code, 404)

    def test_results_are_persisted(self):
        data = self.client.post("/api/link_groups/daily/check").get_json()
        self.assertTrue(data["ok"])
        saved = {l["id"]: l for l in self.read_config()["link_groups"][0]["links"]}
        self.assertEqual(saved["l1"]["check"]["status"], "ok")
        self.assertEqual(saved["l2"]["check"]["status"], "missing")
        self.assertNotIn("check", saved["l3"])          # 被跳过的不写结果

    def test_summary_counts_every_bucket(self):
        summary = self.client.post("/api/link_groups/daily/check").get_json()["summary"]
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["pending"], 0)

    def test_response_carries_the_refreshed_groups(self):
        data = self.client.post("/api/link_groups/daily/check").get_json()
        links = {l["id"]: l for l in data["link_groups"][0]["links"]}
        self.assertEqual(links["l2"]["check"]["status"], "missing")

    def test_check_result_survives_unrelated_edits(self):
        self.client.post("/api/link_groups/daily/check")
        self.client.put("/api/link_groups/daily/links/l2", json={"name": "改个名"})
        saved = {l["id"]: l for l in self.read_config()["link_groups"][0]["links"]}
        self.assertEqual(saved["l2"]["name"], "改个名")
        self.assertEqual(saved["l2"]["check"]["status"], "missing")

    def test_changing_the_url_drops_the_stale_verdict(self):
        # 旧地址的结论对新地址毫无意义，留着会误导。
        self.client.post("/api/link_groups/daily/check")
        self.client.put("/api/link_groups/daily/links/l2",
                        json={"url": self.base + "/status/200"})
        saved = {l["id"]: l for l in self.read_config()["link_groups"][0]["links"]}
        self.assertNotIn("check", saved["l2"])

    def test_skip_flag_can_be_toggled_through_the_normal_update(self):
        self.assertTrue(self.client.put("/api/link_groups/daily/links/l1",
                                        json={"skip_check": True}).get_json()["ok"])
        saved = {l["id"]: l for l in self.read_config()["link_groups"][0]["links"]}
        self.assertTrue(saved["l1"]["skip_check"])
        summary = self.client.post("/api/link_groups/daily/check").get_json()["summary"]
        self.assertEqual(summary["skipped"], 2)

    def test_deleting_a_link_mid_check_does_not_explode(self):
        group = {"id": "daily", "name": "常用", "links": [
            {"id": "gone", "name": "x", "url": self.base + "/status/200"}]}
        results = check_group_links(group)
        self.client.delete("/api/link_groups/daily/links/l1")
        self.assertIsNotNone(app_module.persist_link_checks("daily", results))

    def test_check_on_a_deleted_group_returns_none(self):
        self.assertIsNone(app_module.persist_link_checks("nope", {"l1": {"status": "ok"}}))


class LinkCheckUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_check_is_user_triggered_only(self):
        # 没有任何自动触发：后台定期扫全部收藏 = 周期性敲打别人的站点。
        self.assertIn('id="checkLinksBtn"', self.html)
        self.assertIn("/check", self.html)
        for auto in ("setInterval", "loadConfigs();\n        check"):
            self.assertNotIn(auto, self.html)

    def test_blocked_is_not_shown_as_a_problem(self):
        # LINK_CHECK_META 只列出需要处理的三类；blocked 不在其中。
        start = self.html.index("const LINK_CHECK_META")
        meta = self.html[start:self.html.index("let linkAlertFilter")]
        for kind in ("missing", "error", "unreachable"):
            self.assertIn(kind, meta)
        self.assertNotIn("blocked", meta)

    def test_skipped_links_never_show_a_badge(self):
        start = self.html.index("function linkCheckState")
        self.assertIn("l.skip_check", self.html[start:start + 200])

    def test_unreachable_explains_the_intranet_case(self):
        # 容器连不到内网服务不等于链接坏了，提示里必须说清楚出路。
        self.assertIn("内网服务从容器里本来就连不通", self.html)

    def test_skip_toggle_is_offered_in_the_card_menu(self):
        self.assertIn("toggleSkipCheck(", self.html)
        self.assertIn("忽略死链检查", self.html)
        self.assertIn("恢复死链检查", self.html)

    def test_counts_ignore_the_active_filter(self):
        self.assertIn("计数基于全部网址", self.html)

    def test_drag_handle_is_disabled_while_filtering_by_status(self):
        # 按状态筛选后看到的是子集，按可见顺序写回会打乱其余条目。
        self.assertIn("linkCardHtml(l, i, g, links.length, filtering)", self.html)

    def test_styles_exist(self):
        for rule in (".check-badge", ".link-card.check-missing", ".link-card.check-unreachable"):
            self.assertIn(rule, self.css)


if __name__ == "__main__":
    unittest.main()

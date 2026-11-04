# -*- coding: utf-8 -*-
"""「监控」标签页：与「笔记」同样的票据走法，这里钉住它自己的边界。

  - 未解锁时 /api/configs 里没有它，/monitor/open 只回说明页、不签票；
  - 票据用自己的密钥和用途，接收方是监控站，格式与 Worker 的 verifyHubTicket 一致；
  - 管理密码不合格时绝不签；响应头只许待在本站框里、表单只许交给监控站；
  - 主页面只为监控站多开 frame-src / connect-src，主题参数只认已知值。
"""
import hashlib
import hmac
import re
import time
import unittest

import app as app_module
from app import app
from tests._support import StoreIsolationMixin
from tests.test_vault_embed import _Form, _unb64

PASSWORD = "unit-test-pwd-monitor-embed-7e1a"
SECRET = "unit-test-monitor-secret-" + "0123456789abcdef" * 2
MONITOR = "https://monitor.example"


def parse(html):
    form = _Form()
    form.feed(html)
    return form


def form_target(html):
    found = re.search(r'<form[^>]*\starget="([^"]*)"', html)
    return found.group(1) if found else ""


class MonitorCase(StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        saved = {k: getattr(app_module, k) for k in ("MONITOR_URL", "MONITOR_EMBED_SECRET", "ADMIN_PASSWORD",
                                                     "PRIVATE_MODE", "PUBLIC_ORIGIN")}
        self.addCleanup(lambda: [setattr(app_module, k, v) for k, v in saved.items()])
        app_module.MONITOR_URL = MONITOR
        app_module.MONITOR_EMBED_SECRET = SECRET
        app_module.ADMIN_PASSWORD = PASSWORD
        app_module.PRIVATE_MODE = True
        app_module.PUBLIC_ORIGIN = ""
        self.write_config({"configs": [], "proxy_url": "", "bookmarks": [], "link_groups": []})
        self.client = app.test_client()

    def unlock(self):
        response = self.client.post("/api/auth", headers={"X-Admin-Password": PASSWORD})
        self.assertEqual(response.status_code, 200)

    def test_locked_visitors_get_neither_the_tab_nor_a_ticket(self):
        self.assertIsNone(self.client.get("/api/configs").get_json().get("monitor_embed"))
        response = self.client.get("/monitor/open")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(b'name="ticket"', response.data)

    def test_the_unlocked_admin_gets_the_url_and_no_secret(self):
        self.unlock()
        response = self.client.get("/api/configs")
        self.assertEqual(response.get_json()["monitor_embed"], {"url": MONITOR, "sso": True, "reason": ""})
        self.assertNotIn(SECRET.encode(), response.data)

    def test_the_ticket_verifies_like_the_worker_does(self):
        self.unlock()
        html = self.client.get("/monitor/open?target=hub-fleet-3&theme=light").get_data(as_text=True)
        form = parse(html)
        self.assertEqual(form.forms[0][1], MONITOR + "/auth/hub/sso?theme=light")
        self.assertEqual(form_target(html), "hub-fleet-3")
        parts = form.fields["ticket"].split(".")
        self.assertEqual(len(parts), 7)
        self.assertEqual(parts[:2], ["v2", "monitor"])
        self.assertLessEqual(int(parts[2]) - time.time(), 60)
        self.assertEqual(_unb64(parts[5]), MONITOR)
        key = hashlib.sha256(("hub-monitor-admin|" + SECRET).encode()).digest()
        self.assertEqual(parts[6], hmac.new(key, ".".join(parts[:6]).encode(), hashlib.sha256).hexdigest())

    def test_each_ticket_has_its_own_nonce(self):
        self.unlock()
        first = parse(self.client.get("/monitor/open").get_data(as_text=True)).fields["ticket"]
        second = parse(self.client.get("/monitor/open").get_data(as_text=True)).fields["ticket"]
        self.assertNotEqual(first, second)

    def test_unknown_theme_and_target_are_dropped(self):
        self.unlock()
        html = self.client.get("/monitor/open?theme=//evil&target=other").get_data(as_text=True)
        self.assertEqual(parse(html).forms[0][1], MONITOR + "/auth/hub/sso")
        self.assertEqual(form_target(html), "")

    def test_no_ticket_when_the_admin_password_is_unacceptable(self):
        self.unlock()
        app_module.ADMIN_PASSWORD = "short"
        response = self.client.get("/monitor/open", headers={"X-Admin-Password": "short"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b'name="ticket"', response.data)

    def test_no_ticket_when_the_secret_is_too_short(self):
        self.unlock()
        app_module.MONITOR_EMBED_SECRET = "short"
        self.assertEqual(self.client.get("/monitor/open").status_code, 403)

    def test_cross_site_requests_are_refused(self):
        self.unlock()
        response = self.client.get("/monitor/open", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b'name="ticket"', response.data)

    def test_the_open_page_has_tight_headers(self):
        self.unlock()
        response = self.client.get("/monitor/open")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin")
        csp = response.headers["Content-Security-Policy"]
        self.assertIn("form-action " + MONITOR, csp)
        self.assertIn("frame-ancestors 'self'", csp)

    def test_the_main_page_opens_frame_src_only_for_the_monitor(self):
        csp = self.client.get("/").headers["Content-Security-Policy"]
        self.assertIn(MONITOR, csp.split("frame-src", 1)[1].split(";", 1)[0])
        self.assertIn(MONITOR, csp.split("connect-src", 1)[1].split(";", 1)[0])
        self.assertIn("frame-ancestors 'none'", csp)

    def test_nothing_exists_when_unconfigured(self):
        app_module.MONITOR_URL = ""
        self.assertEqual(self.client.get("/monitor/open").status_code, 404)
        self.assertNotIn(MONITOR, self.client.get("/").headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()

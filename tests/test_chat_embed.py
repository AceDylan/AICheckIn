# -*- coding: utf-8 -*-
"""「AI 聊天」标签页：本站（外层）把自己的 HaloWebUI 嵌进 iframe，并替已解锁的管理员签一张一次性票据。

三件事各有一个「错了会很糟」的方向：
  - 收藏库必须登录后才出现：私密模式是设了管理密码后的默认，未解锁时前后端都不给数据，也没有聊天入口；
  - 票据：只给已解锁的人、只指向配置的那一个源、短命、每张 nonce 不同、放在 # 后面（不进日志）；
    管理密码不合格（没设 / 太短 / 曾公开）时绝不签——它是替 HaloWebUI 开门的钥匙；
  - 响应头：frame-src 只为配置的源开口，本站自己仍然谁都不能嵌（frame-ancestors 'none' + DENY）。
"""
import base64
import hashlib
import hmac
import http.server
import json
import os
import re
import threading
import time
import unittest

import app as app_module
from app import app
from tests._support import StoreIsolationMixin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORD = "unit-test-pwd-chat-embed-7f2a"          # ≥ 12 位，且不在「曾公开」名单里
SECRET = "unit-test-chat-secret-" + "0123456789abcdef" * 2   # ≥ 32 字符；只在测试里用
HALO = "https://host.acedylan.us:3001"
ORIGIN = "http://localhost"                        # Flask 测试客户端的主机名；浏览器会把它填进 Origin
SECRET_HOST = "https://intranet-panel.invalid"

STORE = {
    "configs": [],
    "proxy_url": "",
    "bookmarks": [{"name": "看板站", "url": "https://dash.example", "fields": []}],
    "link_groups": [{"id": "self", "name": "自建服务", "icon": "server", "color": "sky", "links": [
        {"id": "l1", "name": "内网面板", "url": SECRET_HOST},
    ]}],
}


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")


def verify_like_halowebui(ticket, secret=SECRET):
    """按文档里的格式独立验一张票（不调用 app 里的签发代码），两边任何一方漂移这里都会红。"""
    parts = ticket.split(".")
    if len(parts) != 7:
        raise AssertionError("票据应有 7 段：%r" % ticket)
    version, purpose, exp, nonce, issuer, audience, tag = parts
    key = hashlib.sha256(("hub-chat-admin|" + secret).encode("utf-8")).digest()
    expected = hmac.new(key, ".".join(parts[:6]).encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, tag):
        raise AssertionError("签名不匹配")
    return {"version": version, "purpose": purpose, "exp": int(exp), "nonce": nonce,
            "issuer": _unb64(issuer), "audience": _unb64(audience)}


class ChatCase(StoreIsolationMixin, unittest.TestCase):
    """逐用例还原会被改动的模块全局。默认：私密模式 + 管理密码合格 + 配了 HaloWebUI 地址 + 没配密钥。"""

    _GLOBALS = ("ADMIN_PASSWORD", "PRIVATE_MODE", "CHAT_URL", "CHAT_SECRET", "PUBLIC_ORIGIN",
                "_PUBLICLY_KNOWN_PASSWORD_SHA256")

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super().setUp()
        saved = {name: getattr(app_module, name) for name in self._GLOBALS}
        saved_handshake = dict(app_module._chat_handshake)
        self.addCleanup(lambda: [setattr(app_module, k, v) for k, v in saved.items()])
        self.addCleanup(lambda: (app_module._chat_handshake.clear(), app_module._chat_handshake.update(saved_handshake)))
        app_module.ADMIN_PASSWORD = PASSWORD
        app_module.PRIVATE_MODE = True
        app_module.CHAT_URL = HALO
        app_module.CHAT_SECRET = ""
        app_module.PUBLIC_ORIGIN = ""
        # 握手「刚做过且通过」：签票时不会再起后台线程去连 HaloWebUI。
        self.handshake(True, "ok")
        self.write_config(dict(STORE))
        self.client = app.test_client()

    def handshake(self, ok, reason, frame_ancestors=None):
        app_module._chat_handshake.update(ok=ok, reason=reason, checked_at=time.time(),
                                          frame_ancestors=frame_ancestors, running=False)

    def unlock(self, client=None):
        resp = (client or self.client).post("/api/auth", headers={"X-Admin-Password": PASSWORD})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def ticket(self, client=None, **headers):
        headers.setdefault("Origin", ORIGIN)
        return (client or self.client).post("/api/chat/ticket", headers=headers)


# ---------------------------------------------------------------------------
# 私密模式是默认：收藏库只在登录后出现
# ---------------------------------------------------------------------------

class PrivateByDefaultTest(unittest.TestCase):
    def test_a_password_alone_makes_the_library_private(self):
        # 部署里的 .env 多半原样留着 GYQD_PRIVATE=0：它不能再等于「公开」。
        self.assertTrue(app_module._private_mode_from_env("0", "0"))
        self.assertTrue(app_module._private_mode_from_env("", ""))
        self.assertTrue(app_module._private_mode_from_env(None, None))

    def test_public_browsing_is_an_explicit_opt_in(self):
        self.assertFalse(app_module._private_mode_from_env("0", "1"))
        self.assertFalse(app_module._private_mode_from_env("", " 1 "))

    def test_the_legacy_switch_still_forces_private(self):
        self.assertTrue(app_module._private_mode_from_env("1", "1"))


class LibraryRequiresLoginTest(ChatCase):
    def test_visitors_get_an_empty_shell_and_no_chat_entry(self):
        resp = self.client.get("/api/configs")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["private"] and data["locked"])
        self.assertEqual((data["bookmarks"], data["link_groups"]), ([], []))
        self.assertIsNone(data["chat"])
        self.assertNotIn(SECRET_HOST, resp.get_data(as_text=True))
        self.assertNotIn(HALO, resp.get_data(as_text=True))   # 连「有没有 AI 聊天、它在哪」都不说

    def test_visitors_cannot_touch_the_library_api(self):
        for method, path in (("post", "/api/bookmarks"), ("post", "/api/link_groups"),
                             ("get", "/api/configs/export"), ("get", "/api/todos"), ("get", "/api/deck")):
            resp = getattr(self.client, method)(path, json={"name": "x", "url": "https://x.example"})
            self.assertEqual(resp.status_code, 403, (method, path, resp.status_code))

    def test_unlocking_reveals_the_library_and_the_chat_entry(self):
        self.unlock()
        data = self.client.get("/api/configs").get_json()
        self.assertFalse(data["locked"])
        self.assertEqual(data["bookmarks"][0]["url"], "https://dash.example")
        self.assertEqual(data["link_groups"][0]["links"][0]["url"], SECRET_HOST)
        self.assertEqual(data["chat"], {"url": HALO})

    def test_locking_again_hides_everything(self):
        self.unlock()
        self.assertEqual(self.client.post("/api/logout").status_code, 200)
        data = self.client.get("/api/configs").get_json()
        self.assertTrue(data["locked"])
        self.assertIsNone(data["chat"])

    def test_a_wrong_password_is_counted_and_reveals_nothing(self):
        for _ in range(app_module.LOGIN_MAX_FAILS):
            resp = self.client.post("/api/auth", headers={"X-Admin-Password": "not-it-" + PASSWORD})
            self.assertEqual(resp.status_code, 401)
            self.assertNotIn(SECRET_HOST, resp.get_data(as_text=True))
        self.assertEqual(self.client.post("/api/auth", headers={"X-Admin-Password": PASSWORD}).status_code, 429)


class ChatEntryTest(ChatCase):
    def test_no_chat_url_means_no_tab_and_no_endpoint(self):
        app_module.CHAT_URL = ""
        self.unlock()
        self.assertIsNone(self.client.get("/api/configs").get_json()["chat"])
        self.assertEqual(self.ticket().status_code, 404)

    def test_the_ticket_endpoint_is_admin_only(self):
        app_module.CHAT_SECRET = SECRET
        resp = self.ticket()
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("hub_ticket", resp.get_data(as_text=True))
        self.assertNotIn(HALO, resp.get_data(as_text=True))

    def test_the_chat_url_must_be_a_plain_origin(self):
        self.assertEqual(app_module._parse_chat_url(" https://Halo.Example:8443/ "), "https://halo.example:8443")
        self.assertEqual(app_module._parse_chat_url("http://127.0.0.1:3001"), "http://127.0.0.1:3001")
        for bad in ("*", "https://*.example", "https://halo.example/chat", "halo.example", "http://halo.example",
                    "https://user:pw@halo.example", "javascript:alert(1)", "https://a.example https://b.example"):
            self.assertEqual(app_module._parse_chat_url(bad), "", bad)
        self.assertEqual(app_module._parse_chat_url(""), "")


# ---------------------------------------------------------------------------
# 票据签发
# ---------------------------------------------------------------------------

class TicketIssuanceTest(ChatCase):
    def setUp(self):
        super().setUp()
        app_module.CHAT_SECRET = SECRET
        self.unlock()

    def test_an_unlocked_admin_gets_a_single_use_ticket_in_the_fragment(self):
        resp = self.ticket()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        data = resp.get_json()
        self.assertEqual((data["ok"], data["sso"], data["reason"], data["hint"]), (True, True, "", ""))
        prefix = HALO + "/auth#hub_ticket="
        self.assertTrue(data["url"].startswith(prefix), data["url"])
        claims = verify_like_halowebui(data["url"][len(prefix):])
        self.assertEqual((claims["version"], claims["purpose"]), ("v2", "chat"))
        self.assertEqual(claims["issuer"], ORIGIN)      # 浏览器填的 Origin = 本站
        self.assertEqual(claims["audience"], HALO)      # 只对配置的那个 HaloWebUI 有效
        self.assertTrue(0 < claims["exp"] - time.time() <= app_module.CHAT_TICKET_TTL + 1, claims["exp"] - time.time())
        self.assertRegex(claims["nonce"], r"^[A-Za-z0-9_-]{16,64}$")

    def test_every_call_is_a_fresh_ticket(self):
        first = verify_like_halowebui(self.ticket().get_json()["url"].split("#hub_ticket=")[1])
        second = verify_like_halowebui(self.ticket().get_json()["url"].split("#hub_ticket=")[1])
        self.assertNotEqual(first["nonce"], second["nonce"])

    def test_the_ticket_never_appears_in_the_ordinary_config_payload(self):
        self.ticket()
        text = self.client.get("/api/configs").get_data(as_text=True)
        self.assertNotIn("hub_ticket", text)
        self.assertNotIn(SECRET, text)

    def test_the_signing_key_is_domain_separated_from_the_old_direction(self):
        ticket = self.ticket().get_json()["url"].split("#hub_ticket=")[1]
        parts = ticket.split(".")
        old_key = hashlib.sha256(("hub-embed-admin|" + SECRET).encode("utf-8")).digest()
        old_tag = hmac.new(old_key, ".".join(parts[:6]).encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertNotEqual(old_tag, parts[6])

    def test_issuer_comes_from_the_configured_public_origin_first(self):
        app_module.PUBLIC_ORIGIN = "https://hub.example:5526"
        claims = verify_like_halowebui(self.ticket(Origin="http://localhost").get_json()["url"].split("#hub_ticket=")[1])
        self.assertEqual(claims["issuer"], "https://hub.example:5526")

    def test_issuer_falls_back_to_the_request_host_without_an_origin_header(self):
        resp = self.client.post("/api/chat/ticket")   # 老浏览器 / 手写请求：没有 Origin
        claims = verify_like_halowebui(resp.get_json()["url"].split("#hub_ticket=")[1])
        self.assertEqual(claims["issuer"], ORIGIN)

    def test_a_forged_origin_header_cannot_point_the_ticket_elsewhere(self):
        # 票据里的 issuer 只是「本站叫什么」，HaloWebUI 拿它和自己配置的 HUB_URL 比；伪造只会让票据不被认。
        claims = verify_like_halowebui(self.ticket(Origin="https://evil.example").get_json()["url"].split("#hub_ticket=")[1])
        self.assertEqual(claims["issuer"], "https://evil.example")
        self.assertEqual(claims["audience"], HALO)


class TicketRefusalTest(ChatCase):
    """签不了票时标签页照样打得开：回普通地址 + 原因 + 一句提示，绝不带票据。"""

    def assert_plain(self, resp, reason):
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual((data["ok"], data["sso"], data["reason"]), (True, False, reason))
        self.assertEqual(data["url"], HALO + "/")
        self.assertTrue(data["hint"], reason)
        self.assertNotIn("hub_ticket", resp.get_data(as_text=True))

    def test_without_a_shared_secret(self):
        self.unlock()
        self.assert_plain(self.ticket(), "secret_unset")

    def test_a_short_secret_counts_as_unset(self):
        app_module.CHAT_SECRET = "too-short"
        self.unlock()
        self.assert_plain(self.ticket(), "secret_unset")

    def test_a_password_that_was_once_public_never_opens_halowebui(self):
        app_module.CHAT_SECRET = SECRET
        app_module._PUBLICLY_KNOWN_PASSWORD_SHA256 = frozenset({hashlib.sha256(PASSWORD.encode("utf-8")).hexdigest()})
        self.unlock()   # 本站本身照常可解锁
        self.assert_plain(self.ticket(), "password_public")

    def test_a_short_password_never_opens_halowebui(self):
        app_module.CHAT_SECRET = SECRET
        app_module.ADMIN_PASSWORD = "short-pw"
        self.assertEqual(self.client.post("/api/auth", headers={"X-Admin-Password": "short-pw"}).status_code, 200)
        self.assert_plain(self.ticket(), "password_short")

    def test_no_password_means_nobody_is_signed_in(self):
        app_module.CHAT_SECRET = SECRET
        app_module.ADMIN_PASSWORD = ""   # 人人都是「管理员」
        self.assertEqual(app_module.chat_sso_blocker(), "no_password")
        self.assert_plain(self.ticket(), "no_password")

    def test_a_handshake_that_failed_stops_the_tickets(self):
        app_module.CHAT_SECRET = SECRET
        self.unlock()
        for reason in ("secret_mismatch", "peer_not_configured", "issuer", "no_session_user"):
            self.handshake(False, reason)
            self.assert_plain(self.ticket(), reason)

    def test_an_unknown_handshake_still_signs(self):
        # 这台机器连不上 HaloWebUI 不代表管理员的浏览器连不上。
        app_module.CHAT_SECRET = SECRET
        self.unlock()
        self.handshake(None, "unreachable")
        data = self.ticket().get_json()
        self.assertTrue(data["sso"])
        self.assertIn("#hub_ticket=", data["url"])

    def test_framing_verdict_is_reported_when_known(self):
        app_module.CHAT_SECRET = SECRET
        self.unlock()
        self.handshake(True, "ok", frame_ancestors=[ORIGIN])
        self.assertTrue(self.ticket().get_json()["framed_ok"])
        self.handshake(True, "ok", frame_ancestors=["https://someone-else.example"])
        self.assertFalse(self.ticket().get_json()["framed_ok"])
        self.handshake(True, "ok", frame_ancestors=None)
        self.assertIsNone(self.ticket().get_json()["framed_ok"])


class HandshakeTest(ChatCase):
    """真的连一次 HaloWebUI（本地起一个假的），看结论记对了没有。"""

    def serve(self, status, payload):
        secret = SECRET
        test = self

        class Peer(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0) or b"{}")
                test.seen.append((self.path, body.get("ticket", "")))
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.seen = []
        return "http://127.0.0.1:%d" % server.server_address[1]

    def run_handshake(self, status, payload):
        app_module.CHAT_SECRET = SECRET
        app_module.CHAT_URL = self.serve(status, payload)
        app_module.run_chat_handshake("https://hub.example:5526")
        return app_module.chat_handshake_state()

    def test_a_peer_that_accepts_the_probe(self):
        state = self.run_handshake(200, {"ok": True, "frame_ancestors": ["https://hub.example:5526"], "session_user_ready": True})
        self.assertEqual((state["ok"], state["reason"], state["frame_ancestors"]), (True, "ok", ["https://hub.example:5526"]))
        path, probe = self.seen[0]
        self.assertEqual(path, "/api/v1/hub/handshake")
        claims = verify_like_halowebui(probe)
        self.assertEqual((claims["purpose"], claims["issuer"], claims["audience"]), ("probe", "https://hub.example:5526", app_module.CHAT_URL))

    def test_a_peer_without_an_account_to_sign_in(self):
        state = self.run_handshake(200, {"ok": True, "frame_ancestors": [], "session_user_ready": False})
        self.assertEqual((state["ok"], state["reason"]), (False, "no_session_user"))

    def test_a_peer_that_does_not_know_the_secret(self):
        state = self.run_handshake(401, {"detail": {"error": "ticket_refused", "reason": "signature"}})
        self.assertEqual((state["ok"], state["reason"]), (False, "secret_mismatch"))
        state = self.run_handshake(401, {"detail": {"error": "ticket_refused", "reason": "issuer <b>x</b>"}})
        self.assertEqual((state["ok"], state["reason"]), (False, "issuerbxb"))   # 只留小写字母，别的进不了页面

    def test_a_peer_that_is_not_configured_or_unreachable(self):
        self.assertEqual(self.run_handshake(404, {"detail": "Not Found"})["reason"], "peer_not_configured")
        self.assertEqual(self.run_handshake(429, {})["ok"], None)
        app_module.CHAT_URL = "http://127.0.0.1:9"    # 没人监听
        app_module.run_chat_handshake("https://hub.example:5526")
        self.assertEqual((app_module.chat_handshake_state()["ok"], app_module.chat_handshake_state()["reason"]), (None, "unreachable"))

    def test_nothing_is_attempted_while_tickets_are_blocked_anyway(self):
        app_module.CHAT_SECRET = ""
        app_module._chat_handshake.update(checked_at=None, ok=None, reason="not_run")
        state = app_module.ensure_chat_handshake("https://hub.example:5526")
        self.assertEqual(state["reason"], "not_run")


# ---------------------------------------------------------------------------
# 响应头与页面形状
# ---------------------------------------------------------------------------

class FrameHeadersTest(ChatCase):
    def test_frame_src_opens_exactly_the_configured_origin(self):
        csp = self.client.get("/").headers["Content-Security-Policy"]
        self.assertIn("frame-src 'self' " + HALO + ";", csp)
        self.assertTrue(csp.endswith("frame-ancestors 'none'"), csp)
        self.assertNotIn("*", csp)

    def test_without_a_chat_url_nothing_is_opened(self):
        app_module.CHAT_URL = ""
        csp = self.client.get("/").headers["Content-Security-Policy"]
        self.assertIn("frame-src 'self';", csp)
        self.assertNotIn(HALO, csp)

    def test_this_site_itself_can_never_be_framed(self):
        for path in ("/", "/api/configs", "/static/app-v3.css"):
            resp = self.client.get(path)
            self.assertEqual(resp.headers["X-Frame-Options"], "DENY", path)
            self.assertTrue(resp.headers["Content-Security-Policy"].endswith("frame-ancestors 'none'"), path)
            self.assertNotIn("Sec-Fetch-Dest", resp.headers.get("Vary", ""), path)


class PageShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _read("templates", "index.html")
        cls.sw = _read("static", "sw.js")
        cls.css = _read("static", "app-v3.css")

    def test_the_chat_tab_is_hidden_until_the_page_decides(self):
        self.assertRegex(self.html, r'<button class="tab" data-view="chat"[^>]*\bhidden\b')
        self.assertIn("'bookmarks', 'chat', 'checkin', 'settings'", self.html)
        self.assertIn("function chatAvailable() { return !!(STATE.chat && STATE.chat.url) && canEdit(); }", self.html)

    def test_a_locked_library_has_no_entry_anywhere(self):
        self.assertIn("function libraryLocked() { return !!(STATE.private && STATE.locked); }", self.html)
        self.assertIn("if (libTab) libTab.hidden = locked;", self.html)
        self.assertIn("if (STATE_LOADED && libraryLocked() && mainName !== 'settings') { switchView('settings', updateHash); return; }", self.html)

    def test_the_frame_is_sandboxed_without_top_navigation(self):
        sandbox = re.search(r"frame\.setAttribute\('sandbox', '([^']+)'\)", self.html).group(1).split()
        self.assertNotIn("allow-top-navigation", sandbox)
        self.assertNotIn("allow-top-navigation-by-user-activation", sandbox)
        for needed in ("allow-same-origin", "allow-scripts", "allow-forms", "allow-popups", "allow-modals"):
            self.assertIn(needed, sandbox)
        self.assertIn("frame.setAttribute('referrerpolicy', 'no-referrer');", self.html)

    def test_only_addresses_on_the_configured_origin_go_into_the_frame(self):
        self.assertIn("data.url.startsWith(base + '/')) target = data.url;", self.html)

    def test_the_ticket_travels_in_the_fragment(self):
        self.assertIn('CHAT_URL + "/auth#hub_ticket=" + issue_chat_ticket(', _read("app.py"))

    def test_nothing_is_left_of_being_framed_ourselves(self):
        for token in ("data-embedded", "EMBEDDED", "/embed/enter", "consumeSsoNotice", "Sec-Fetch-Dest"):
            self.assertNotIn(token, self.html, token)
        self.assertNotIn("HUB_FRAME_ANCESTORS", _read("app.py"))

    def test_the_chat_view_has_its_styles(self):
        for selector in ("html.chat-on .content", "#view-chat.active", ".chat-stage", ".chat-frame", ".chat-note"):
            self.assertIn(selector, self.css, selector)

    def test_service_worker_cache_was_bumped_for_the_new_shell_and_headers(self):
        version = int(re.search(r"bh-shell-v(\d+)", self.sw).group(1))
        self.assertGreaterEqual(version, 17)

    def test_service_worker_never_touches_the_ticket_endpoint(self):
        # /api/chat/ticket 是 POST + no-store：只走网络。
        self.assertIn("if (!isShellRequest(url)) return;", self.sw)
        self.assertNotIn("/api/chat", self.sw)


if __name__ == "__main__":
    unittest.main()

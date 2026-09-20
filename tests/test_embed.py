# -*- coding: utf-8 -*-
"""被自己的 HaloWebUI 嵌入：iframe 白名单、被嵌入时的外壳、票据换管理员会话。

三件事各有一个「错了会很糟」的方向：
  - 白名单：写错一个字符不能变成「谁都能嵌」（点击劫持）；
  - 被嵌入的外壳：框里「当前页打开」会把整个框导航到外站，只剩白屏；
  - 票据：出现在 URL 里，必须一次性、短命、换不出比密码解锁更大的权限。
"""
import hashlib
import hmac
import os
import re
import time
import unittest

import app as app_module
from app import app
from tests._support import StoreIsolationMixin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORD = "unit-test-pwd-embed-51c7"
SECRET = "unit-test-embed-secret-" + "0123456789abcdef" * 2   # ≥ 32 字符；只在测试里用
HALO = "https://host.acedylan.us:3001"
IFRAME = {"Sec-Fetch-Dest": "iframe"}


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class EmbedCase(StoreIsolationMixin, unittest.TestCase):
    """逐用例还原会被改动的模块全局；默认状态 = 线上默认（白名单空、密钥空）。"""

    _GLOBALS = ("ADMIN_PASSWORD", "FRAME_ANCESTORS", "EMBED_ADMIN_SECRET", "EMBED_SESSION_TTL", "PRIVATE_MODE")

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super().setUp()
        saved = {name: getattr(app_module, name) for name in self._GLOBALS}
        self.addCleanup(lambda: [setattr(app_module, k, v) for k, v in saved.items()])
        self.addCleanup(app_module._embed_nonces.clear)
        app_module._embed_nonces.clear()
        app_module.ADMIN_PASSWORD = PASSWORD
        app_module.FRAME_ANCESTORS = []
        app_module.EMBED_ADMIN_SECRET = ""
        self.client = app.test_client()

    def enable_sso(self):
        app_module.EMBED_ADMIN_SECRET = SECRET

    def session_cookie(self, resp):
        for raw in resp.headers.getlist("Set-Cookie"):
            if raw.startswith(app_module.SESSION_COOKIE + "="):
                return raw
        return None


class FrameAncestorsWhitelistTest(EmbedCase):
    def test_default_only_allows_same_origin_framing(self):
        resp = self.client.get("/")
        self.assertEqual(resp.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertTrue(resp.headers["Content-Security-Policy"].endswith("frame-ancestors 'self'"))

    def test_whitelisted_origin_is_the_only_addition(self):
        app_module.FRAME_ANCESTORS = app_module._parse_frame_ancestors(HALO)
        resp = self.client.get("/")
        self.assertTrue(resp.headers["Content-Security-Policy"].endswith("frame-ancestors 'self' " + HALO))
        # X-Frame-Options 表达不了白名单，留着 SAMEORIGIN 会让老浏览器把白名单里的站也拒掉。
        self.assertNotIn("X-Frame-Options", resp.headers)

    def test_whitelist_covers_every_response_not_just_the_shell(self):
        app_module.FRAME_ANCESTORS = [HALO]
        for path in ("/api/configs", "/static/app-v3.css", "/sw.js", "/no-such-page"):
            resp = self.client.get(path)
            self.assertIn("frame-ancestors 'self' " + HALO, resp.headers["Content-Security-Policy"], path)
            self.assertNotIn("X-Frame-Options", resp.headers, path)

    def test_wildcards_and_sloppy_entries_are_dropped(self):
        parse = app_module._parse_frame_ancestors
        for bad in ("*", "https://*", "https://*.acedylan.us", "https://*.acedylan.us:3001", "host.acedylan.us:3001",
                    "https://host.acedylan.us:3001/hub", "http://host.acedylan.us:3001", "'none'", "data:",
                    "https://host.acedylan.us:3001;script-src *", "https://host..us", "https://-bad.us",
                    "https://host.acedylan.us:3001'", "https://user@host.acedylan.us", "//host.acedylan.us"):
            self.assertEqual(parse(bad), [], bad)

    def test_parsing_normalises_and_dedupes(self):
        parse = app_module._parse_frame_ancestors
        self.assertEqual(parse(" HTTPS://Host.Acedylan.US:3001/ , https://host.acedylan.us:3001\nhttps://b.example "),
                         [HALO, "https://b.example"])
        self.assertEqual(parse(""), [])
        self.assertEqual(parse(None), [])
        # http 只给本机调试用。
        self.assertEqual(parse("http://127.0.0.1:5173 http://localhost:3000 http://10.0.0.2:3000"),
                         ["http://127.0.0.1:5173", "http://localhost:3000"])

    def test_a_bad_entry_does_not_poison_the_good_ones(self):
        self.assertEqual(app_module._parse_frame_ancestors("https://*.evil.example " + HALO), [HALO])


class EmbeddedShellTest(EmbedCase):
    def test_iframe_requests_get_the_embedded_marker(self):
        html = self.client.get("/?embed=1", headers=IFRAME).get_data(as_text=True)
        self.assertRegex(html, r'<html lang="zh-CN" data-theme="dark" data-embedded="1">')

    def test_top_level_requests_do_not(self):
        for headers in ({}, {"Sec-Fetch-Dest": "document"}, {"Sec-Fetch-Dest": "empty"}):
            html = self.client.get("/", headers=headers).get_data(as_text=True)
            self.assertIn('<html lang="zh-CN" data-theme="dark">', html)
            self.assertNotIn('<html lang="zh-CN" data-theme="dark" data-embedded', html)

    def test_the_embed_query_string_alone_changes_nothing(self):
        # 标记只看浏览器填的 Sec-Fetch-Dest：?embed=1 谁都能手写，不能让它改变顶层页面的行为。
        self.assertNotIn('data-theme="dark" data-embedded', self.client.get("/?embed=1").get_data(as_text=True))

    def test_the_two_shells_are_cached_apart(self):
        top, framed = self.client.get("/"), self.client.get("/", headers=IFRAME)
        self.assertNotEqual(top.headers["ETag"], framed.headers["ETag"])
        for resp in (top, framed):
            self.assertIn("Sec-Fetch-Dest", resp.headers["Vary"])
        # 各自的校验器只命中各自的外壳。
        self.assertEqual(self.client.get("/", headers=dict(IFRAME, **{"If-None-Match": framed.headers["ETag"]})).status_code, 304)
        self.assertEqual(self.client.get("/", headers={"If-None-Match": framed.headers["ETag"]}).status_code, 200)

    def test_alternating_shells_keep_their_etags_stable(self):
        first = self.client.get("/").headers["ETag"]
        self.client.get("/", headers=IFRAME)
        self.assertEqual(self.client.get("/").headers["ETag"], first)


class EmbeddedPageScriptTest(unittest.TestCase):
    """模板里的约定：被嵌入时一律新标签页。真实浏览器里的行为由 tests/browser 之外的人工验证覆盖，这里锁住代码形状。"""

    @classmethod
    def setUpClass(cls):
        cls.html = _read("templates", "index.html")

    def test_open_mode_is_forced_to_new_tab_when_embedded(self):
        self.assertIn("const EMBEDDED = document.documentElement.getAttribute('data-embedded') === '1';", self.html)
        self.assertIn("function openMode() { return EMBEDDED ? 'new' : readPref('bh_open', OPEN_MODES, 'new'); }", self.html)

    def test_every_link_builder_still_goes_through_open_mode(self):
        # 链接的 target 只有两个来源：写死的 _blank，或 linkTargetAttrs()/openUrl()——后两者都看 openMode()。
        self.assertIn("function linkTargetAttrs() { return openMode() === 'same'", self.html)
        self.assertIn("if (openMode() === 'same') location.href = safe; else window.open(safe, '_blank', 'noopener');", self.html)
        self.assertEqual(len(re.findall(r"location\.href\s*=(?!=)", self.html)), 1, "新增了绕过 openUrl() 的整页跳转")

    def test_head_script_corrects_the_marker_from_the_browser_side(self):
        head = self.html[:self.html.index("</head>")]
        self.assertIn("window.self !== window.top", head)
        self.assertIn("setAttribute('data-embedded', '1')", head)
        self.assertIn("removeAttribute('data-embedded')", head)   # SW 缓存的「被嵌入」外壳在顶层打开时要摘掉标记

    def test_external_links_without_a_target_are_caught_at_click_time(self):
        self.assertIn("if (EMBEDDED) document.addEventListener('click'", self.html)

    def test_service_worker_cache_was_bumped_for_the_new_headers(self):
        version = int(re.search(r"bh-shell-v(\d+)", _read("static", "sw.js")).group(1))
        self.assertGreaterEqual(version, 16)

    def test_service_worker_leaves_the_ticket_endpoint_alone(self):
        # /embed/enter 带着票据、回的是 Set-Cookie + 303：绝不能经过 SW 的缓存逻辑。
        sw = _read("static", "sw.js")
        self.assertIn("if (!isShellRequest(url)) return;", sw)
        self.assertNotIn("/embed", sw)


class EmbedTicketTest(EmbedCase):
    def ticket(self, purpose="enter", ttl=120, secret=SECRET, now=None):
        """按 HaloWebUI 那边的算法独立签一张（不调 app 里的签发函数，两边实现漂移时这里会红）。"""
        exp = str(int(now if now is not None else time.time()) + ttl)
        nonce = "n" + hashlib.sha256(os.urandom(16)).hexdigest()[:23]
        key = hashlib.sha256(("hub-embed-admin|" + secret).encode("utf-8")).digest()
        message = ".".join(("v1", purpose, exp, nonce))
        return message + "." + hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def enter(self, ticket, client=None, headers=None):
        return (client or self.client).get("/embed/enter", query_string={"ticket": ticket},
                                           headers=dict({"X-Forwarded-Proto": "https"}, **(headers or {})))

    # ---- 关闭状态 ----

    def test_feature_does_not_exist_without_a_secret(self):
        self.assertEqual(self.enter(self.ticket()).status_code, 404)
        self.assertEqual(self.client.post("/api/embed/handshake", json={"ticket": self.ticket("probe")}).status_code, 404)

    def test_a_short_secret_counts_as_no_secret(self):
        app_module.EMBED_ADMIN_SECRET = "too-short"
        self.assertFalse(app_module.trusted_embed_active())
        self.assertEqual(self.enter(self.ticket(secret="too-short")).status_code, 404)

    # ---- 正常换票 ----

    def test_valid_ticket_becomes_the_ordinary_admin_session(self):
        self.enable_sso()
        resp = self.enter(self.ticket(), headers=IFRAME)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["Location"], "/?embed=1")      # 票据不跟着走
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        raw = self.session_cookie(resp)
        self.assertIsNotNone(raw)
        for flag in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/"):
            self.assertIn(flag, raw)
        self.assertEqual(int(re.search(r"Max-Age=(\d+)", raw).group(1)), app_module.EMBED_SESSION_TTL)
        # 同一枚 Cookie、同一套校验：管理接口直接放行。
        self.assertTrue(self.client.get("/api/configs").get_json()["admin_unlocked"])
        self.assertEqual(self.client.get("/api/history").status_code, 200)

    def test_opening_in_a_new_tab_lands_on_the_clean_home_page(self):
        self.enable_sso()
        self.assertEqual(self.enter(self.ticket()).headers["Location"], "/")

    def test_embed_session_is_shorter_than_a_password_unlock(self):
        self.assertLess(app_module.EMBED_SESSION_TTL, app_module.SESSION_MAX_AGE)
        self.enable_sso()
        token = self.session_cookie(self.enter(self.ticket())).split("=", 1)[1].split(";")[0]
        self.assertLessEqual(int(token.split(".")[0]), time.time() + app_module.EMBED_SESSION_TTL + 5)

    def test_an_existing_longer_session_is_not_downgraded(self):
        self.enable_sso()
        self.client.post("/api/auth", headers={"X-Admin-Password": PASSWORD})
        resp = self.enter(self.ticket())
        self.assertEqual(resp.status_code, 303)
        self.assertIsNone(self.session_cookie(resp))

    def test_works_in_private_mode(self):
        self.enable_sso()
        app_module.PRIVATE_MODE = True
        self.assertEqual(self.client.get("/api/history").status_code, 403)
        self.assertIsNotNone(self.session_cookie(self.enter(self.ticket())))
        self.assertEqual(self.client.get("/api/history").status_code, 200)

    def test_nothing_to_unlock_without_an_admin_password(self):
        self.enable_sso()
        app_module.ADMIN_PASSWORD = ""
        resp = self.enter("garbage")
        self.assertEqual((resp.status_code, resp.headers["Location"]), (303, "/"))
        self.assertIsNone(self.session_cookie(resp))

    # ---- 拒收 ----

    def assert_rejected(self, ticket, reason, client=None):
        resp = self.enter(ticket, client=client, headers=IFRAME)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["Location"], "/?embed=1&sso=" + reason)
        self.assertIsNone(self.session_cookie(resp), reason)

    def test_a_ticket_works_exactly_once(self):
        self.enable_sso()
        ticket = self.ticket()
        self.assertIsNotNone(self.session_cookie(self.enter(ticket)))
        self.assert_rejected(ticket, "replayed", client=app.test_client())

    def test_expired_and_overlong_tickets_are_refused(self):
        self.enable_sso()
        self.assert_rejected(self.ticket(ttl=-1), "expired")
        self.assert_rejected(self.ticket(ttl=app_module.EMBED_TICKET_MAX_TTL + 60), "ttl")

    def test_signature_must_match_the_shared_secret(self):
        self.enable_sso()
        self.assert_rejected(self.ticket(secret="another-secret-" + "f" * 32), "signature")
        good = self.ticket()
        head, sig = good.rsplit(".", 1)
        self.assert_rejected(head + "." + ("0" if sig[0] != "0" else "1") + sig[1:], "signature")
        # 改动任何一段都会让签名失效：把过期时间往后拨。
        parts = good.split(".")
        parts[2] = str(int(parts[2]) + 60)
        self.assert_rejected(".".join(parts), "signature")

    def test_the_session_signing_key_cannot_mint_tickets(self):
        # 票据密钥与会话密钥互不相通：拿到一枚会话 Cookie 的人签不出票据，反之亦然。
        self.enable_sso()
        exp = str(int(time.time()) + 60)
        forged = ".".join(("v1", "enter", exp, "n" * 24))
        sig = hmac.new(app_module._session_key(), forged.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assert_rejected(forged + "." + sig, "signature")

    def test_probe_tickets_cannot_be_traded_for_a_session(self):
        self.enable_sso()
        self.assert_rejected(self.ticket("probe"), "purpose")

    def test_malformed_input_never_raises(self):
        self.enable_sso()
        for junk in ("x", "v1.enter.1.2", "v1.enter.abc.%s.%s" % ("n" * 24, "0" * 64), "v2.enter.1.n.s",
                     "v1.ENTER.9999999999.%s.%s" % ("n" * 24, "0" * 64), "a" * 5000, "v1.enter.9999999999.短.sig"):
            app_module._login_fails.clear()   # 这条用例看的是「不抛异常」，别让防爆破先一步拦下
            self.assert_rejected(junk, "malformed")
        self.assert_rejected("", "missing")

    def test_guessing_is_throttled_like_password_guessing(self):
        self.enable_sso()
        for _ in range(app_module.LOGIN_MAX_FAILS):
            self.assert_rejected(self.ticket(secret="guess-" + "9" * 40), "signature")
        # 锁定期间连真票据也不看，并且不消耗它。
        good = self.ticket()
        self.assert_rejected(good, "throttled")
        self.assertEqual(self.client.post("/api/auth", headers={"X-Admin-Password": PASSWORD}).status_code, 429)
        app_module._login_fails.clear()
        self.assertIsNotNone(self.session_cookie(self.enter(good)))

    def test_clock_skew_does_not_lock_the_admin_out(self):
        # 签名是对的、只是过期：说明对方持有密钥，多半是两台机器时钟没对上——不能因此把密码登录也锁了。
        self.enable_sso()
        for _ in range(app_module.LOGIN_MAX_FAILS * 2):
            self.assert_rejected(self.ticket(ttl=-5), "expired")
        self.assertTrue(self.client.post("/api/auth", headers={"X-Admin-Password": PASSWORD}).get_json()["ok"])

    def test_app_and_independent_signer_agree(self):
        self.enable_sso()
        self.assertIsNotNone(self.session_cookie(self.enter(app_module.issue_embed_ticket("enter"))))

    # ---- 启动握手 ----

    def test_handshake_confirms_the_secret_without_issuing_anything(self):
        self.enable_sso()
        app_module.FRAME_ANCESTORS = [HALO]
        resp = self.client.post("/api/embed/handshake", json={"ticket": self.ticket("probe")})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual((body["ok"], body["frame_ancestors"], body["session_ttl"]),
                         (True, [HALO], app_module.EMBED_SESSION_TTL))
        self.assertEqual(resp.headers.getlist("Set-Cookie"), [])
        self.assertNotIn(SECRET, resp.get_data(as_text=True))

    def test_handshake_rejects_a_mismatched_secret(self):
        self.enable_sso()
        resp = self.client.post("/api/embed/handshake", json={"ticket": self.ticket("probe", secret="x" * 40)})
        self.assertEqual((resp.status_code, resp.get_json()["reason"]), (401, "signature"))
        for body in (None, [], {"ticket": 5}, {"ticket": self.ticket("enter")}):
            self.assertEqual(self.client.post("/api/embed/handshake", json=body).status_code, 401, body)


if __name__ == "__main__":
    unittest.main()

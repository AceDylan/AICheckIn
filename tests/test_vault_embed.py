# -*- coding: utf-8 -*-
"""「笔记」标签页：本站（外层）把自己的 WebObsidian 嵌进 iframe，并替已解锁的管理员登录它。

与「AI 聊天」同一种票据，但走法更严，这里逐条钉住：
  - 入口只给已解锁的人：未解锁时 /api/configs 里没有它，/vault/open 只回说明页、不签票；
  - 票据只出现在 /vault/open 的响应体（隐藏表单字段）里：不进任何地址，页面脚本拿不到；
    60 秒、每次 nonce 不同、接收方是配置的 WebObsidian、密钥与 HaloWebUI 那把分开派生；
  - 管理密码不合格（没设 / 太短 / 曾公开）或密钥太短时绝不签；
  - 响应头：/vault/open 只能待在本站自己的框里、表单只许交给 WebObsidian、不缓存；
    全站其余响应照旧 frame-ancestors 'none'，主页面只为 WebObsidian 多开 frame-src / connect-src；
  - 锁定时页面请 WebObsidian 结束由本站登录的会话。
"""
import base64
import hashlib
import hmac
import html.parser
import os
import re
import time
import unittest

import app as app_module
from app import app
from tests._support import StoreIsolationMixin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORD = "unit-test-pwd-vault-embed-3c9d"                 # ≥ 12 位，且不在「曾公开」名单里
SECRET = "unit-test-vault-secret-" + "fedcba9876543210" * 2  # ≥ 32 字符；只在测试里用
VAULT = "https://host.acedylan.us:3003"
HUB = "http://localhost"                                      # Flask 测试客户端的主机名 = 本站对外的源


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")


def verify_like_webobsidian(ticket, secret=SECRET):
    """按文档里的格式独立验一张票（不调用 app 里的签发代码），两边任何一方漂移这里都会红。"""
    parts = ticket.split(".")
    if len(parts) != 7:
        raise AssertionError("票据应有 7 段：%r" % ticket)
    version, purpose, exp, nonce, issuer, audience, tag = parts
    key = hashlib.sha256(("hub-vault-admin|" + secret).encode("utf-8")).digest()
    expected = hmac.new(key, ".".join(parts[:6]).encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, tag):
        raise AssertionError("签名不匹配")
    return {"version": version, "purpose": purpose, "exp": int(exp), "nonce": nonce,
            "issuer": _unb64(issuer), "audience": _unb64(audience)}


class _Form(html.parser.HTMLParser):
    """把 /vault/open 页面里的表单拆出来：action、隐藏字段、脚本的 nonce。"""

    def __init__(self):
        super().__init__()
        self.forms, self.fields, self.script_nonces, self.links = [], {}, [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.forms.append((attrs.get("method"), attrs.get("action")))
        elif tag == "input" and attrs.get("type") == "hidden":
            self.fields[attrs.get("name")] = attrs.get("value")
        elif tag == "script":
            self.script_nonces.append(attrs.get("nonce"))
        elif tag == "a":
            self.links.append(attrs.get("href"))


def parse(resp):
    form = _Form()
    form.feed(resp.get_data(as_text=True))
    return form


class VaultCase(StoreIsolationMixin, unittest.TestCase):
    """逐用例还原会被改动的模块全局。默认：私密模式 + 管理密码合格 + 配了 WebObsidian 地址与密钥。"""

    _GLOBALS = ("ADMIN_PASSWORD", "PRIVATE_MODE", "VAULT_URL", "VAULT_EMBED_SECRET", "VAULT_API_KEY",
                "CHAT_URL", "CHAT_SECRET", "PUBLIC_ORIGIN", "_PUBLICLY_KNOWN_PASSWORD_SHA256")

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super().setUp()
        saved = {name: getattr(app_module, name) for name in self._GLOBALS}
        self.addCleanup(lambda: [setattr(app_module, k, v) for k, v in saved.items()])
        app_module.ADMIN_PASSWORD = PASSWORD
        app_module.PRIVATE_MODE = True
        app_module.VAULT_URL = VAULT
        app_module.VAULT_EMBED_SECRET = SECRET
        app_module.VAULT_API_KEY = ""          # 标签页不依赖 Agent API 的 key
        app_module.CHAT_URL = ""
        app_module.CHAT_SECRET = ""
        app_module.PUBLIC_ORIGIN = ""
        self.write_config({"configs": [], "proxy_url": "", "bookmarks": [], "link_groups": []})
        self.client = app.test_client()

    def unlock(self, client=None):
        resp = (client or self.client).post("/api/auth", headers={"X-Admin-Password": PASSWORD})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def open_page(self, query="", **headers):
        return self.client.get("/vault/open" + query, headers=headers)


# ---------------------------------------------------------------------------
# 入口：只给已解锁的人
# ---------------------------------------------------------------------------

class EntryTest(VaultCase):
    def test_visitors_learn_nothing_about_the_vault_tab(self):
        data = self.client.get("/api/configs").get_json()
        self.assertTrue(data["locked"])
        self.assertIsNone(data["vault_embed"])
        self.assertNotIn(VAULT, self.client.get("/api/configs").get_data(as_text=True))

    def test_unlocking_reveals_the_tab_without_any_credential(self):
        self.unlock()
        data = self.client.get("/api/configs").get_json()
        self.assertEqual(data["vault_embed"], {"url": VAULT, "sso": True, "reason": "", "hint": ""})
        body = self.client.get("/api/configs").get_data(as_text=True)
        self.assertNotIn(SECRET, body)
        self.assertNotIn("v2.vault.", body)

    def test_no_secret_means_no_tab_and_no_page(self):
        app_module.VAULT_EMBED_SECRET = ""
        self.unlock()
        self.assertIsNone(self.client.get("/api/configs").get_json()["vault_embed"])
        self.assertEqual(self.open_page().status_code, 404)

    def test_no_vault_url_means_no_tab_and_no_page(self):
        app_module.VAULT_URL = ""
        self.unlock()
        self.assertIsNone(self.client.get("/api/configs").get_json()["vault_embed"])
        self.assertEqual(self.open_page().status_code, 404)

    def test_the_tab_does_not_need_the_agent_api_key_and_vice_versa(self):
        self.unlock()
        data = self.client.get("/api/configs").get_json()
        self.assertIsNone(data["vault"])          # 没配 key：速记 / 搜索 / 镜像照旧没有
        self.assertIsNotNone(data["vault_embed"])  # 但标签页在

    def test_locking_again_hides_the_tab(self):
        self.unlock()
        self.client.post("/api/logout")
        self.assertIsNone(self.client.get("/api/configs").get_json()["vault_embed"])


# ---------------------------------------------------------------------------
# /vault/open：框里的第一页
# ---------------------------------------------------------------------------

class OpenPageTest(VaultCase):
    def test_a_visitor_gets_an_explanation_and_no_ticket(self):
        resp = self.open_page()
        self.assertEqual(resp.status_code, 401)
        form = parse(resp)
        self.assertEqual(form.forms, [])
        self.assertEqual(form.fields, {})
        self.assertNotIn("v2.vault.", resp.get_data(as_text=True))
        self.assertIn("解锁", resp.get_data(as_text=True))

    def test_an_unlocked_admin_gets_a_self_submitting_form_with_a_single_use_ticket(self):
        self.unlock()
        resp = self.open_page("?to=%2Fnote%2FInbox%2FToday.md")
        self.assertEqual(resp.status_code, 200)
        form = parse(resp)
        self.assertEqual(form.forms, [("post", VAULT + "/auth/hub/sso")])
        self.assertEqual(form.fields["to"], "/note/Inbox/Today.md")
        claims = verify_like_webobsidian(form.fields["ticket"])
        self.assertEqual(claims["version"], "v2")
        self.assertEqual(claims["purpose"], "vault")
        self.assertEqual(claims["issuer"], HUB)
        self.assertEqual(claims["audience"], VAULT)
        self.assertTrue(time.time() < claims["exp"] <= time.time() + 61)
        # 只有带 nonce 的那一行脚本能跑，它只做「提交表单」这一件事。
        csp = resp.headers["Content-Security-Policy"]
        nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
        self.assertEqual(form.script_nonces, [nonce])
        self.assertIn("document.forms[0].submit();", resp.get_data(as_text=True))

    def test_every_open_is_a_fresh_ticket(self):
        self.unlock()
        one = verify_like_webobsidian(parse(self.open_page()).fields["ticket"])
        two = verify_like_webobsidian(parse(self.open_page()).fields["ticket"])
        self.assertNotEqual(one["nonce"], two["nonce"])

    def test_the_ticket_is_never_in_an_address_or_a_header(self):
        self.unlock()
        resp = self.open_page()
        ticket = parse(resp).fields["ticket"]
        self.assertNotIn("Location", resp.headers)
        for name, value in resp.headers.items():
            self.assertNotIn(ticket, value, name)
        self.assertNotIn("hub_ticket", resp.get_data(as_text=True))
        self.assertNotIn(ticket, VAULT + "/auth/hub/sso")

    def test_the_page_can_only_live_in_this_sites_own_frame_and_post_to_the_vault(self):
        self.unlock()
        for resp in (self.open_page(), self.client.get("/vault/open")):
            self.assertEqual(resp.headers["X-Frame-Options"], "SAMEORIGIN")
            csp = resp.headers["Content-Security-Policy"]
            self.assertIn("frame-ancestors 'self'", csp)
            self.assertIn("form-action " + VAULT + ";", csp)
            self.assertIn("default-src 'none'", csp)
            self.assertRegex(csp, r"(^|; )script-src 'nonce-[A-Za-z0-9_-]+';")   # 没有 'unsafe-inline'，也没有 'self'
            self.assertEqual(resp.headers["Cache-Control"], "no-store")
            # no-referrer 会让跨源表单 POST 的 Origin 变成 null，对面就认不出本站；strict-origin 只带出源。
            self.assertEqual(resp.headers["Referrer-Policy"], "strict-origin")

    def test_the_page_is_never_compressed(self):
        self.unlock()
        resp = self.client.get("/vault/open?to=%2F" + "a" * 2000, headers={"Accept-Encoding": "gzip"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Content-Encoding", resp.headers)

    def test_other_sites_cannot_send_people_here(self):
        self.unlock()
        resp = self.open_page(**{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("v2.vault.", resp.get_data(as_text=True))
        for site in ("same-origin", "same-site", "none"):
            self.assertEqual(self.open_page(**{"Sec-Fetch-Site": site}).status_code, 200, site)

    def test_the_landing_path_is_a_path_on_the_vault_and_nothing_else(self):
        self.unlock()
        for to in ("//evil.example/x", "https://evil.example/", "/\\evil.example", "javascript:alert(1)",
                   "note", "/a\x00b", "/" + "x" * 2000, ""):
            self.assertEqual(parse(self.open_page("?" + app_module.urlencode({"to": to}))).fields["to"], "/", repr(to))

    def test_the_landing_path_is_escaped_in_the_page(self):
        self.unlock()
        body = self.open_page("?" + app_module.urlencode({"to": '/x"><script>alert(1)</script>'})).get_data(as_text=True)
        self.assertNotIn("<script>alert(1)", body)
        self.assertEqual(body.count("<script"), 1)

    def test_the_issuer_comes_from_the_configured_public_origin_first(self):
        app_module.PUBLIC_ORIGIN = "https://best.acedylan.us:5526"
        self.unlock()
        claims = verify_like_webobsidian(parse(self.open_page()).fields["ticket"])
        self.assertEqual(claims["issuer"], "https://best.acedylan.us:5526")

    def test_the_signing_key_is_separate_from_the_chat_bridge(self):
        # 即便有人把两把密钥设成同一个值，一边签的票在另一边也验不过：派生前缀不同。
        app_module.CHAT_SECRET = SECRET
        self.unlock()
        ticket = parse(self.open_page()).fields["ticket"]
        parts = ticket.split(".")
        chat_key = hashlib.sha256(("hub-chat-admin|" + SECRET).encode("utf-8")).digest()
        self.assertNotEqual(hmac.new(chat_key, ".".join(parts[:6]).encode("utf-8"), hashlib.sha256).hexdigest(),
                            parts[6])
        self.assertNotEqual(app_module._vault_key(), app_module._chat_key())


class NeverSignTest(VaultCase):
    def assert_no_ticket(self, reason_word):
        self.unlock()
        resp = self.open_page()
        self.assertEqual(resp.status_code, 403)
        body = resp.get_data(as_text=True)
        self.assertNotIn("v2.vault.", body)
        self.assertEqual(parse(resp).forms, [])
        data = self.client.get("/api/configs").get_json()["vault_embed"]
        self.assertFalse(data["sso"])
        self.assertEqual(data["reason"], reason_word)
        self.assertTrue(data["hint"])

    def test_a_short_secret_counts_as_unset(self):
        app_module.VAULT_EMBED_SECRET = "too-short"
        self.assert_no_ticket("secret_unset")

    def test_a_password_that_was_once_public_never_opens_the_vault(self):
        app_module._PUBLICLY_KNOWN_PASSWORD_SHA256 = frozenset({hashlib.sha256(PASSWORD.encode("utf-8")).hexdigest()})
        self.assert_no_ticket("password_public")

    def test_a_short_password_never_opens_the_vault(self):
        app_module.ADMIN_PASSWORD = "short-pw"
        resp = self.client.post("/api/auth", headers={"X-Admin-Password": "short-pw"})
        self.assertEqual(resp.status_code, 200)
        resp = self.open_page()
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("v2.vault.", resp.get_data(as_text=True))

    def test_no_password_means_nobody_is_signed_in(self):
        app_module.ADMIN_PASSWORD = ""
        resp = self.open_page()
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("v2.vault.", resp.get_data(as_text=True))


# ---------------------------------------------------------------------------
# 主页面的响应头
# ---------------------------------------------------------------------------

class MainPageHeadersTest(VaultCase):
    def test_frame_src_and_connect_src_open_exactly_the_vault(self):
        csp = self.client.get("/").headers["Content-Security-Policy"]
        self.assertIn("frame-src 'self' " + VAULT + ";", csp)
        self.assertIn("connect-src 'self' " + VAULT + ";", csp)
        self.assertTrue(csp.endswith("frame-ancestors 'none'"), csp)
        self.assertNotIn("*", csp)

    def test_both_frames_can_coexist(self):
        app_module.CHAT_URL = "https://host.acedylan.us:3001"
        csp = self.client.get("/").headers["Content-Security-Policy"]
        self.assertIn("frame-src 'self' https://host.acedylan.us:3001 " + VAULT + ";", csp)

    def test_without_the_tab_nothing_extra_is_opened(self):
        app_module.VAULT_EMBED_SECRET = ""
        csp = self.client.get("/").headers["Content-Security-Policy"]
        self.assertIn("frame-src 'self';", csp)
        self.assertIn("connect-src 'self';", csp)
        self.assertNotIn(VAULT, csp)

    def test_everything_else_still_refuses_to_be_framed(self):
        for path in ("/", "/api/configs", "/static/app-v3.css"):
            resp = self.client.get(path)
            self.assertEqual(resp.headers["X-Frame-Options"], "DENY", path)
            self.assertTrue(resp.headers["Content-Security-Policy"].endswith("frame-ancestors 'none'"), path)


# ---------------------------------------------------------------------------
# 页面形状
# ---------------------------------------------------------------------------

class PageShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _read("templates", "index.html")
        cls.sw = _read("static", "sw.js")
        cls.css = _read("static", "app-v3.css")
        start = cls.html.index("// ===== 笔记 =====")
        cls.js = cls.html[start:cls.html.index("// =====", start + 10)]

    def test_the_tab_is_hidden_until_the_page_decides(self):
        self.assertRegex(self.html, r'<button class="tab" data-view="vault"[^>]*\bhidden\b')
        self.assertIn("function vaultEmbedAvailable() { return !!(STATE.vault_embed && STATE.vault_embed.url) && canEdit(); }", self.html)
        self.assertIn("if (vaultTab) vaultTab.hidden = !vaultEmbedAvailable();", self.html)
        self.assertIn("if (mainName === 'vault' && STATE_LOADED && !vaultEmbedAvailable()) { switchView('bookmarks'); return; }", self.html)

    def test_the_frame_only_ever_loads_this_sites_own_page(self):
        # 票据不经过页面脚本：框里放的是本站的 /vault/open，脚本里没有任何票据的影子。
        self.assertIn("frame.src = '/vault/open?to=%2F';", self.js)
        self.assertNotIn("ticket", self.js.lower().replace("一次性票据", ""))
        self.assertEqual(re.findall(r"frame\.src = ([^;]+);", self.js), ["'/vault/open?to=%2F'"])
        self.assertIn('id="vaultPopout" href="/vault/open"', self.html)

    def test_the_frame_is_sandboxed_without_top_navigation(self):
        sandbox = re.search(r"frame\.setAttribute\('sandbox', '([^']+)'\)", self.js).group(1).split()
        self.assertNotIn("allow-top-navigation", sandbox)
        self.assertNotIn("allow-top-navigation-by-user-activation", sandbox)
        for needed in ("allow-same-origin", "allow-scripts", "allow-forms"):
            self.assertIn(needed, sandbox)
        self.assertIn("frame.setAttribute('referrerpolicy', 'no-referrer');", self.js)

    def test_locking_signs_the_vault_out_too(self):
        self.assertIn("fetch(url + '/auth/hub/logout', { method: 'POST', mode: 'no-cors', credentials: 'include', keepalive: true, referrerPolicy: 'strict-origin' })", self.js)
        lock = self.html[self.html.index("$('lockBtn').addEventListener"):]
        self.assertIn("closeVault(true);", lock[:600])
        self.assertIn("if (!vaultEmbedAvailable()) closeVault(true);", self.html)

    def test_the_view_reuses_the_chat_layout(self):
        self.assertIn("#view-chat.active, #view-vault.active {", self.css)
        self.assertIn("document.documentElement.classList.toggle('chat-on', mainName === 'chat' || mainName === 'vault');", self.html)

    def test_service_worker_was_bumped_and_never_touches_the_vault_page(self):
        self.assertGreaterEqual(int(re.search(r"bh-shell-v(\d+)", self.sw).group(1)), 20)
        self.assertNotIn("/vault", self.sw)
        self.assertIn("if (!isShellRequest(url)) return;", self.sw)


if __name__ == "__main__":
    unittest.main()

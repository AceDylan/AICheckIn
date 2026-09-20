# -*- coding: utf-8 -*-
"""「发送到 AI 聊天」与「笔记（WebObsidian）」。

两条都是把本站接到别的服务上，各有一个「错了会很糟」的方向：

  - **带着问题进聊天**：问题被编进地址（`/auth?redirect=/?q=…#hub_ticket=…`）。
    编码必须扛得住 `& # % +` 和中文，否则对面收到的是半句话；地址必须有硬上限，
    否则一篇 2000 字的便签会生成一条被反代砍掉的请求行。票据仍然只许待在 `#` 后面。
  - **写笔记**：给本站一把 write key 就等于「攻破本站的管理密码 = 能写整个 Vault」。
    所以写入路径由后端自己拼，前端递不了路径，而且最后还要过一道「只许收件箱」的闸。
    搜索结果里的路径同样要洗一遍——那是对面给的字符串，会变成页面上的链接。
"""
import datetime
import http.server
import json
import os
import re
import threading
import unittest
from urllib.parse import parse_qs, quote, unquote, urlparse

import app as app_module
from app import app
from tests._support import StoreIsolationMixin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORD = "unit-test-pwd-ask-vault-91c4"     # ≥ 12 位，且不在「曾公开」名单里
SECRET = "unit-test-chat-secret-" + "0123456789abcdef" * 2
HALO = "https://halo.acedylan.us:3001"
VAULT = "https://notes.acedylan.us:3003"
ORIGIN = "http://localhost"
INBOX = "收件箱"

STORE = {
    "configs": [],
    "proxy_url": "",
    "bookmarks": [],
    "link_groups": [{"id": "self", "name": "自建服务", "icon": "server", "color": "sky", "links": [
        {"id": "l1", "name": "面板", "url": "https://panel.example"},
    ]}],
}


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _address_like_the_hub(base, text, ticket):
    """本站生成地址的规则，在测试里独立写一遍：实现漂移时这里会先红。"""
    return base + "/auth?redirect=" + quote("/?q=" + quote(text, safe=""), safe="") + "#hub_ticket=" + ticket


def like_halowebui(url):
    """把地址按 HaloWebUI 的读法解一遍，返回 (redirect 路径, q 的值, 票据)。

    对面做的事：`/auth` 读 `?redirect=`（URLSearchParams 解一次），
    `safeRedirectPath()` 用 `new URL()` 归一，落地页再读 `?q=`（又解一次）。
    """
    parsed = urlparse(url)
    redirect = parse_qs(parsed.query).get("redirect", [""])[0]
    landing = urlparse(redirect)
    ticket = parse_qs(parsed.fragment).get("hub_ticket", [""])[0]
    return redirect, parse_qs(landing.query).get("q", [""])[0], ticket


# ---------------------------------------------------------------------------
# 编进地址的那段问题
# ---------------------------------------------------------------------------

class PromptEncodingTest(unittest.TestCase):
    def test_without_a_prompt_nothing_changes(self):
        self.assertEqual(app_module.chat_target_url(HALO, "", "TKT"),
                         (HALO + "/auth#hub_ticket=TKT", "", False))
        self.assertEqual(app_module.chat_target_url(HALO, "", ""), (HALO + "/", "", False))

    def test_the_prompt_arrives_at_the_other_side_byte_for_byte(self):
        # 会把地址拆散的每一类字符：查询串分隔、片段起点、百分号、加号（会被解成空格）、
        # 斜杠（redirect 里是路径分隔）、引号、换行、中文、emoji。
        for prompt in (
            "帮我读一下 https://example.com/a?b=1&c=2#top 这一页",
            "100% 确定吗？a+b c/d 'e' \"f\"",
            "第一行\n第二行\n\n第四行",
            "tag:#收件箱 & path:项目/2026",
            "emoji 🐈 也要活着回来",
        ):
            url, sent, truncated = app_module.chat_target_url(HALO, prompt, "TKT")
            self.assertFalse(truncated, prompt)
            self.assertEqual(sent, prompt)
            redirect, q, ticket = like_halowebui(url)
            self.assertEqual(q, prompt, prompt)
            self.assertTrue(redirect.startswith("/?q="), redirect)
            self.assertEqual(ticket, "TKT")

    def test_the_landing_path_stays_a_same_site_path(self):
        # HaloWebUI 的 safeRedirectPath() 只放行以单个 / 开头的站内路径。
        for prompt in ("//evil.example", "/\\evil.example", "javascript:alert(1)", "https://evil.example"):
            url, _, _ = app_module.chat_target_url(HALO, prompt, "TKT")
            redirect, q, _ = like_halowebui(url)
            self.assertTrue(redirect.startswith("/?q="), redirect)
            self.assertFalse(redirect.startswith("//"), redirect)
            self.assertEqual(q, prompt)

    def test_the_ticket_never_leaves_the_fragment(self):
        url, _, _ = app_module.chat_target_url(HALO, "问题", "TKT")
        self.assertNotIn("hub_ticket", url.split("#", 1)[0])
        self.assertTrue(url.endswith("#hub_ticket=TKT"))

    def test_a_long_prompt_is_cut_until_the_address_fits(self):
        # 一个汉字编码两次要 15 个字符：几百字就能撑爆一条请求行。
        url, sent, truncated = app_module.chat_target_url(HALO, "很长的中文" * 900, "TKT")
        self.assertTrue(truncated)
        self.assertLessEqual(len(url), app_module.CHAT_URL_MAX)
        self.assertTrue(sent.endswith("…"))
        self.assertGreater(len(sent), 20)          # 别砍到只剩省略号
        _, q, _ = like_halowebui(url)
        self.assertEqual(q, sent)                  # 截断之后仍然是完整可解的一段

    def test_the_cut_is_the_longest_prefix_that_fits(self):
        # 不是「砍一半了事」：留下的必须是还放得下的最长前缀，多一个字就超。
        for prompt in ("a" * 20000, "中" * 4000, "混合 mixed 文本 " * 500):
            url, sent, truncated = app_module.chat_target_url(HALO, prompt, "TKT")
            self.assertTrue(truncated, prompt[:10])
            self.assertLessEqual(len(url), app_module.CHAT_URL_MAX)
            kept = len(sent) - 1                       # 末尾的省略号不是原文
            self.assertEqual(sent, prompt[:kept] + "…")
            one_more = _address_like_the_hub(HALO, prompt[:kept + 1] + "…", "TKT")
            self.assertGreater(len(one_more), app_module.CHAT_URL_MAX, prompt[:10])

    def test_an_absurdly_long_base_gives_up_on_the_prompt_rather_than_the_address(self):
        base = "https://" + "a" * (app_module.CHAT_URL_MAX + 100) + ".example"
        url, sent, truncated = app_module.chat_target_url(base, "问题", "TKT")
        self.assertEqual((url, sent, truncated), (base + "/auth#hub_ticket=TKT", "", True))

    def test_the_prompt_is_cleaned_before_it_goes_anywhere(self):
        self.assertEqual(app_module.clean_chat_prompt("  两边有空白  "), "两边有空白")
        self.assertEqual(app_module.clean_chat_prompt("保留\n换行"), "保留\n换行")
        self.assertEqual(app_module.clean_chat_prompt("windows\r\n行尾"), "windows\n行尾")
        self.assertEqual(app_module.clean_chat_prompt("制表\t符变空格"), "制表 符变空格")
        self.assertEqual(app_module.clean_chat_prompt("控制\x00字符\x07没了"), "控制字符没了")
        self.assertEqual(app_module.clean_chat_prompt("空\n\n\n\n行收敛"), "空\n\n行收敛")
        self.assertEqual(len(app_module.clean_chat_prompt("字" * 99999)), app_module.CHAT_PROMPT_MAX)
        for junk in (None, 42, {"a": 1}, [], True):
            self.assertEqual(app_module.clean_chat_prompt(junk), "")


# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------

class HubCase(StoreIsolationMixin, unittest.TestCase):
    """默认：公开收藏库 + 管理密码合格 + 接了 HaloWebUI 和笔记服务。"""

    _GLOBALS = ("ADMIN_PASSWORD", "PRIVATE_MODE", "CHAT_URL", "CHAT_SECRET", "PUBLIC_ORIGIN",
                "VAULT_URL", "VAULT_API_KEY", "VAULT_INBOX", "VAULT_TIMEOUT", "VAULT_TODO_MIRROR")

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super().setUp()
        saved = {name: getattr(app_module, name) for name in self._GLOBALS}
        saved_handshake = dict(app_module._chat_handshake)
        self.addCleanup(lambda: [setattr(app_module, k, v) for k, v in saved.items()])
        self.addCleanup(lambda: (app_module._chat_handshake.clear(),
                                 app_module._chat_handshake.update(saved_handshake)))
        app_module.ADMIN_PASSWORD = PASSWORD
        app_module.PRIVATE_MODE = False
        app_module.CHAT_URL = HALO
        app_module.CHAT_SECRET = SECRET
        app_module.PUBLIC_ORIGIN = ""
        app_module.VAULT_URL = VAULT
        app_module.VAULT_API_KEY = "wok_unit_test_key"
        app_module.VAULT_INBOX = INBOX
        app_module.VAULT_TIMEOUT = 2.0
        app_module.VAULT_TODO_MIRROR = False
        app_module._chat_handshake.update(ok=True, reason="ok", checked_at=9e9,
                                          frame_ancestors=[ORIGIN], running=False)
        self.write_config(dict(STORE))
        self.client = app.test_client()

    def unlock(self):
        resp = self.client.post("/api/auth", headers={"X-Admin-Password": PASSWORD})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def fake_vault(self, status=200, body=None):
        """替掉出站调用，记下每一次 (method, path, payload, params)。"""
        calls = []

        def stub(method, path, payload=None, params=""):
            calls.append({"method": method, "path": path, "payload": payload, "params": params})
            return status, (body if body is not None else {"ok": True})

        original = app_module.vault_call
        app_module.vault_call = stub
        self.addCleanup(lambda: setattr(app_module, "vault_call", original))
        return calls


# ---------------------------------------------------------------------------
# POST /api/chat/ticket 带着问题
# ---------------------------------------------------------------------------

class ChatTicketWithPromptTest(HubCase):
    def post(self, body=None, **headers):
        headers.setdefault("Origin", ORIGIN)
        return self.client.post("/api/chat/ticket", json=body, headers=headers)

    def test_an_unlocked_admin_gets_the_prompt_back_inside_the_address(self):
        self.unlock()
        data = self.post({"prompt": "帮我看看 a&b=1#c"}).get_json()
        self.assertTrue(data["ok"] and data["sso"])
        self.assertEqual(data["prompt"], "帮我看看 a&b=1#c")
        self.assertFalse(data["prompt_truncated"])
        _, q, ticket = like_halowebui(data["url"])
        self.assertEqual(q, "帮我看看 a&b=1#c")
        self.assertTrue(ticket.startswith("v2.chat."))

    def test_a_visitor_gets_nothing_at_all(self):
        app_module.PRIVATE_MODE = True
        resp = self.post({"prompt": "秘密问题"})
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("秘密问题", resp.get_data(as_text=True))
        self.assertNotIn("hub_ticket", resp.get_data(as_text=True))

    def test_the_prompt_still_travels_when_the_ticket_cannot_be_signed(self):
        # 签不了票（没配密钥）时照样把问题带过去：在那边自己登录完就落到同一个问题上。
        app_module.CHAT_SECRET = ""
        self.unlock()
        data = self.post({"prompt": "还是要问"}).get_json()
        self.assertFalse(data["sso"])
        self.assertEqual(data["reason"], "secret_unset")
        redirect, q, ticket = like_halowebui(data["url"])
        self.assertEqual((q, ticket), ("还是要问", ""))
        self.assertTrue(data["url"].startswith(HALO + "/auth?redirect="))
        self.assertNotIn("#", data["url"])

    def test_no_prompt_means_the_old_address(self):
        self.unlock()
        for body in (None, {}, {"prompt": ""}, {"prompt": "   "}, {"prompt": 42}):
            data = self.post(body).get_json()
            self.assertTrue(data["url"].startswith(HALO + "/auth#hub_ticket="), body)
            self.assertEqual(data["prompt"], "")

    def test_a_long_prompt_comes_back_marked_as_cut(self):
        self.unlock()
        data = self.post({"prompt": "长" * 3000}).get_json()
        self.assertTrue(data["prompt_truncated"])
        self.assertLessEqual(len(data["url"]), app_module.CHAT_URL_MAX)
        self.assertEqual(data["prompt"], like_halowebui(data["url"])[1])

    def test_the_response_is_never_cached(self):
        self.unlock()
        self.assertEqual(self.post({"prompt": "x"}).headers["Cache-Control"], "no-store")


# ---------------------------------------------------------------------------
# 路径：只许写收件箱
# ---------------------------------------------------------------------------

class VaultPathTest(HubCase):
    def test_an_ordinary_relative_path_survives(self):
        for good in ("Welcome.md", "项目/2026/计划.md", "a b/c d.md", INBOX + "/2026-09-20.md"):
            self.assertEqual(app_module.clean_vault_path(good), good)
        self.assertEqual(app_module.clean_vault_path(" /项目/a.md "), "项目/a.md")

    def test_anything_that_could_leave_the_vault_is_refused(self):
        for bad in ("../../etc/passwd", "项目/../../etc/passwd", "..\\..\\windows", ".git/config",
                    "项目/.git/config", ".trash/x.md", ".obsidian/workspace.json", "a//b.md",
                    "a/\x00b.md", "a/b\n.md", "x" * 500, "", "   ", None, 42, [], {"a": 1}):
            self.assertEqual(app_module.clean_vault_path(bad), "", repr(bad))

    def test_writing_is_confined_to_the_inbox(self):
        self.assertEqual(app_module.vault_writable_path(INBOX + "/2026-09-20.md"), INBOX + "/2026-09-20.md")
        self.assertEqual(app_module.vault_writable_path(INBOX + "/深/一层.md"), INBOX + "/深/一层.md")
        for outside in ("知识库/密码.md", "00-主页.md", INBOX, INBOX + "/", INBOX + "x/a.md",
                        "../" + INBOX + "/a.md", "/" + INBOX, INBOX + "/../知识库/a.md"):
            self.assertEqual(app_module.vault_writable_path(outside), "", outside)

    def test_no_inbox_configured_means_nothing_is_writable(self):
        app_module.VAULT_INBOX = ""
        self.assertEqual(app_module.vault_writable_path("任意/a.md"), "")
        self.assertEqual(app_module.vault_note_path("a.md"), "")
        self.assertFalse(app_module.vault_enabled())

    def test_the_two_notes_this_site_writes_are_both_in_the_inbox(self):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(app_module.vault_capture_note(), "%s/%s.md" % (INBOX, today))
        self.assertEqual(app_module.vault_todo_note(), INBOX + "/待办.md")

    def test_the_inbox_name_itself_is_validated(self):
        self.assertEqual(app_module._parse_vault_folder(" /收件箱/ "), "收件箱")
        self.assertEqual(app_module._parse_vault_folder("Hub/收件箱"), "Hub/收件箱")
        for bad in ("", "  ", "..", "../外面", ".git", "a/../b", "a//b", "收\x00件箱", "x" * 200, None):
            self.assertEqual(app_module._parse_vault_folder(bad), "", repr(bad))

    def test_a_deep_link_percent_encodes_every_segment(self):
        self.assertEqual(app_module.vault_note_url("项目/a b.md"),
                         VAULT + "/note/%E9%A1%B9%E7%9B%AE/a%20b.md")
        self.assertEqual(app_module.vault_note_url(""), "")


# ---------------------------------------------------------------------------
# 出站调用的形状
# ---------------------------------------------------------------------------

class VaultCallTest(HubCase):
    """真的起一个本机 HTTP 服务，验「key 在头里、不在地址里」和超时确实传下去了。"""

    def setUp(self):
        super().setUp()
        self.seen = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _record(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.seen.append({
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": self.rfile.read(length).decode("utf-8") if length else "",
                })
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            do_GET = do_PUT = do_PATCH = _record

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        app_module.VAULT_URL = "http://127.0.0.1:%d" % self.server.server_address[1]

    def test_the_key_rides_in_a_header_and_never_in_the_address(self):
        status, body = app_module.vault_call("GET", "/health")
        self.assertEqual((status, body), (200, {"ok": True}))
        call = self.seen[0]
        self.assertEqual(call["headers"]["x-api-key"], "wok_unit_test_key")
        self.assertNotIn("wok_unit_test_key", call["path"])
        self.assertNotIn("authorization", call["headers"])

    def test_a_patch_carries_json(self):
        app_module.vault_call("PATCH", "/notes/" + INBOX + "/a.md", {"append": "一行"})
        call = self.seen[0]
        self.assertEqual(call["method"], "PATCH")
        self.assertEqual(json.loads(call["body"]), {"append": "一行"})
        self.assertEqual(call["headers"]["content-type"], "application/json")

    def test_query_parameters_are_passed_through(self):
        app_module.vault_call("GET", "/search", None, "q=%E5%9B%BE&limit=3")
        self.assertEqual(self.seen[0]["path"], "/api/v1/search?q=%E5%9B%BE&limit=3")

    def test_a_redirect_is_refused_so_the_key_never_follows_it(self):
        # 跟重定向 = 把 X-API-Key 原样递给 Location 指的那台机器。
        moved = []
        outer = self

        class Moved(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                moved.append(self.headers.get("X-API-Key"))
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%d/api/v1/health" % outer.server.server_address[1])
                self.end_headers()

            def log_message(self, *args):
                pass

        hop = http.server.HTTPServer(("127.0.0.1", 0), Moved)
        threading.Thread(target=hop.serve_forever, daemon=True).start()
        self.addCleanup(hop.server_close)
        self.addCleanup(hop.shutdown)
        app_module.VAULT_URL = "http://127.0.0.1:%d" % hop.server_address[1]

        status, body = app_module.vault_call("GET", "/health")
        self.assertEqual((status, body), (302, {}))   # 原样回报，不跟过去
        self.assertEqual(len(moved), 1)               # 只发了一次
        self.assertEqual(self.seen, [])               # 目的地那台机器一个请求都没收到

    def test_a_dead_host_is_reported_not_raised(self):
        app_module.VAULT_URL = "http://127.0.0.1:1"      # 没人听
        app_module.VAULT_TIMEOUT = 1.0
        self.assertEqual(app_module.vault_call("GET", "/health"), (0, {}))

    def test_the_timeout_is_clamped_into_a_sane_range(self):
        self.assertEqual(app_module._parse_vault_timeout("0.01"), 1.0)
        self.assertEqual(app_module._parse_vault_timeout("900"), 15.0)
        self.assertEqual(app_module._parse_vault_timeout("nonsense"), 5.0)
        self.assertEqual(app_module._parse_vault_timeout(""), 5.0)
        self.assertEqual(app_module._parse_vault_timeout("7"), 7.0)


# ---------------------------------------------------------------------------
# 三个接口
# ---------------------------------------------------------------------------

class VaultEndpointsTest(HubCase):
    def test_nothing_exists_until_the_deployment_configures_it(self):
        self.unlock()
        for missing in ("VAULT_URL", "VAULT_API_KEY", "VAULT_INBOX"):
            with self.subTest(missing=missing):
                saved = getattr(app_module, missing)
                setattr(app_module, missing, "")
                try:
                    self.assertEqual(self.client.post("/api/vault/capture", json={"text": "x"}).status_code, 404)
                    self.assertEqual(self.client.get("/api/vault/search?q=x").status_code, 404)
                    self.assertEqual(self.client.post("/api/vault/todos/sync").status_code, 404)
                    self.assertIsNone(self.client.get("/api/configs").get_json()["vault"])
                finally:
                    setattr(app_module, missing, saved)

    def test_all_three_endpoints_are_admin_only(self):
        calls = self.fake_vault()
        for method, path, body in (("post", "/api/vault/capture", {"text": "秘密"}),
                                   ("get", "/api/vault/search?q=秘密", None),
                                   ("post", "/api/vault/todos/sync", None)):
            resp = getattr(self.client, method)(path, json=body)
            self.assertEqual(resp.status_code, 403, path)
            self.assertNotIn("秘密", resp.get_data(as_text=True))
        self.assertEqual(calls, [])   # 未解锁时一个出站请求都没发

    def test_the_address_is_only_shown_to_an_unlocked_admin(self):
        app_module.PRIVATE_MODE = True
        self.assertIsNone(self.client.get("/api/configs").get_json()["vault"])
        self.unlock()
        self.assertEqual(self.client.get("/api/configs").get_json()["vault"],
                         {"url": VAULT, "inbox": INBOX})

    def test_the_api_key_is_never_sent_to_the_browser(self):
        self.unlock()
        for path in ("/", "/api/configs", "/api/diagnostics"):
            self.assertNotIn("wok_unit_test_key", self.client.get(path).get_data(as_text=True))

    # -- capture --------------------------------------------------------

    def test_a_capture_appends_to_todays_note_in_the_inbox(self):
        calls = self.fake_vault()
        self.unlock()
        resp = self.client.post("/api/vault/capture", json={"text": "记一笔", "source": "memo"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(data["path"], "%s/%s.md" % (INBOX, today))
        self.assertTrue(data["url"].startswith(VAULT + "/note/"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["method"], "PATCH")
        self.assertIn(INBOX, unquote(calls[0]["path"]))
        appended = calls[0]["payload"]["append"]
        self.assertIn("便签", appended)
        self.assertIn("  记一笔", appended)
        self.assertRegex(appended, r"- \*\*\d\d:\d\d\*\*")

    def test_the_caller_cannot_choose_a_path(self):
        # 请求体里塞 path / note / file 都不该有任何作用。
        calls = self.fake_vault()
        self.unlock()
        self.client.post("/api/vault/capture", json={
            "text": "x", "path": "知识库/密码.md", "note": "../../etc/passwd", "file": "/etc/passwd",
        })
        self.assertEqual(len(calls), 1)
        self.assertTrue(unquote(calls[0]["path"]).startswith("/notes/" + INBOX + "/"))
        self.assertNotIn("密码", calls[0]["path"])
        self.assertNotIn("passwd", calls[0]["path"])

    def test_an_unknown_source_simply_gets_no_label(self):
        calls = self.fake_vault()
        self.unlock()
        for source in ("<script>", "知识库/../..", "", None, 7, "MEMO"):
            calls.clear()
            self.client.post("/api/vault/capture", json={"text": "正文", "source": source})
            appended = calls[0]["payload"]["append"]
            self.assertNotIn("script", appended)
            if source == "MEMO":
                self.assertIn("便签", appended)      # 大小写不敏感，仍然只认表里的词
            else:
                self.assertEqual(appended.splitlines()[1].count("·"), 0)

    def test_an_empty_capture_is_refused_before_any_request(self):
        calls = self.fake_vault()
        self.unlock()
        for body in ({}, {"text": ""}, {"text": "   \n  "}, {"text": 42}, {"text": None}):
            self.assertEqual(self.client.post("/api/vault/capture", json=body).status_code, 400, body)
        self.assertEqual(calls, [])

    def test_a_multi_line_capture_stays_one_block(self):
        calls = self.fake_vault()
        self.unlock()
        self.client.post("/api/vault/capture", json={"text": "第一行\n第二行", "source": "note"})
        lines = calls[0]["payload"]["append"].splitlines()
        self.assertEqual(lines[2:4], ["  第一行", "  第二行"])

    def test_an_upstream_failure_is_reported_without_pretending_it_worked(self):
        for status, expected in ((0, "连不上"), (401, "API key"), (403, "scope"), (429, "限流"), (500, "HTTP 500")):
            with self.subTest(status=status):
                self.fake_vault(status=status, body={})
                self.unlock()
                resp = self.client.post("/api/vault/capture", json={"text": "x"})
                self.assertEqual(resp.status_code, 502)
                self.assertIn(expected, resp.get_json()["error"])

    # -- search ---------------------------------------------------------

    def test_a_search_cleans_every_path_the_other_side_returned(self):
        self.fake_vault(body={"hits": [
            {"path": "项目/计划.md", "title": "计划", "snippet": "换\n行   收敛"},
            {"path": "../../etc/passwd", "title": "坏的"},
            {"path": ".git/config"},
            "不是字典",
            {"title": "没有路径"},
        ]})
        self.unlock()
        hits = self.client.get("/api/vault/search?q=计划").get_json()["hits"]
        self.assertEqual([h["path"] for h in hits], ["项目/计划.md"])
        self.assertEqual(hits[0]["snippet"], "换 行 收敛")
        self.assertEqual(hits[0]["url"], VAULT + "/note/%E9%A1%B9%E7%9B%AE/%E8%AE%A1%E5%88%92.md")

    def test_the_search_limit_cannot_be_argued_past(self):
        calls = self.fake_vault(body={"hits": []})
        self.unlock()
        for asked, expected in (("1", 1), ("3", 3), ("999", app_module.VAULT_SEARCH_LIMIT),
                                ("-5", 1), ("abc", app_module.VAULT_SEARCH_LIMIT), ("", app_module.VAULT_SEARCH_LIMIT)):
            calls.clear()
            self.client.get("/api/vault/search?q=x&limit=" + asked)
            self.assertEqual(parse_qs(calls[0]["params"])["limit"], [str(expected)], asked)

    def test_an_empty_query_never_reaches_the_other_side(self):
        calls = self.fake_vault()
        self.unlock()
        for query in ("", "   "):
            data = self.client.get("/api/vault/search?q=" + query).get_json()
            self.assertEqual(data, {"ok": True, "hits": []})
        self.assertEqual(calls, [])

    def test_a_failed_search_says_so(self):
        self.fake_vault(status=429, body={})
        self.unlock()
        resp = self.client.get("/api/vault/search?q=x")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("限流", resp.get_json()["error"])

    # -- 待办镜像 --------------------------------------------------------

    def test_the_todo_mirror_overwrites_one_note_in_the_inbox(self):
        calls = self.fake_vault()
        self.unlock()
        self.client.post("/api/todos", json={"text": "写完这件事"})
        self.client.post("/api/todos", json={"text": "已经做完的"})
        done = self.client.get("/api/todos").get_json()["todos"][0]["id"]
        self.client.put("/api/todos/" + done, json={"done": True})
        calls.clear()

        resp = self.client.post("/api/vault/todos/sync")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["path"], INBOX + "/待办.md")
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["method"], "PUT")     # 整篇覆盖，不是追加
        content = calls[0]["payload"]["content"]
        self.assertIn("- [ ] 写完这件事", content)
        self.assertIn("- [x] 已经做完的", content)
        self.assertIn("单向写入", content)              # 笔记自己说清楚它不是真相源

    def test_the_mirror_of_an_empty_list_is_still_a_valid_note(self):
        calls = self.fake_vault()
        self.unlock()
        self.client.post("/api/vault/todos/sync")
        content = calls[0]["payload"]["content"]
        self.assertIn("## 未完成（0）", content)
        self.assertIn("## 已完成（0）", content)
        self.assertEqual(content.count("（空）"), 2)

    def test_a_multi_line_todo_stays_on_one_bullet(self):
        content = app_module.vault_todo_markdown([{"text": "第一行\n第二行", "done": False}])
        self.assertIn("- [ ] 第一行 第二行", content)

    def test_the_mirror_is_off_unless_the_deployment_turns_it_on(self):
        calls = self.fake_vault()
        self.unlock()
        self.client.post("/api/todos", json={"text": "不该触发镜像"})
        self.assertEqual(calls, [])
        self.assertIsNone(app_module.mirror_todos_later([{"text": "x", "done": False}]))

    def test_with_the_mirror_on_a_todo_change_rewrites_the_note(self):
        calls = self.fake_vault()
        app_module.VAULT_TODO_MIRROR = True
        self.unlock()
        thread = app_module.mirror_todos_later([{"text": "镜像我", "done": False}])
        self.assertIsNotNone(thread)
        thread.join(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["method"], "PUT")
        self.assertIn("- [ ] 镜像我", calls[0]["payload"]["content"])

    def test_a_failing_mirror_never_breaks_the_todo_itself(self):
        self.fake_vault(status=500, body={})
        app_module.VAULT_TODO_MIRROR = True
        self.unlock()
        resp = self.client.post("/api/todos", json={"text": "照样要加上"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["todos"][0]["text"], "照样要加上")


# ---------------------------------------------------------------------------
# 页面：入口、编辑框、以及「不许边打边搜」
# ---------------------------------------------------------------------------

class PageShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _read("templates", "index.html")
        cls.css = _read("static", "app-v3.css")

    def test_the_composer_exists_with_both_destinations(self):
        for token in ('id="askModal"', 'id="askText"', 'id="askSend"', 'id="askVault"',
                      'id="askCancel"', 'id="askCount"', 'id="askHint"'):
            self.assertIn(token, self.html, token)

    def test_every_entry_point_is_wired(self):
        for token in ('id="memoAsk"', 'data-todo-act="ask"', 'askAboutLink(', 'id="todoVaultSync"',
                      "type: 'ai'", "type: 'notes'"):
            self.assertIn(token, self.html, token)

    def test_the_composer_has_its_styles(self):
        for selector in (".ask-form", ".ask-text", ".ask-foot", ".ask-count", ".home-search-state"):
            self.assertIn(selector, self.css, selector)

    def test_notes_are_only_searched_when_the_row_is_chosen(self):
        # 边打边搜 = 每敲一个字一次跨机调用。这里守住「只有 runHomeSearchRow 会发请求」。
        body = self.html[self.html.index("async function runNoteSearch"):]
        body = body[:body.index("\n    }")]
        self.assertIn("/api/vault/search", body)
        callers = re.findall(r"runNoteSearch\(", self.html)
        self.assertEqual(len(callers), 2, "runNoteSearch 只该有一处定义和一处调用")
        trigger = self.html[self.html.index("function runHomeSearchRow"):]
        self.assertIn("runNoteSearch(row.query)", trigger[:trigger.index("\n    }")])
        for handler in ("$('homeSearch').addEventListener('input'", "$('homeSearch').addEventListener('focus'"):
            line = self.html[self.html.index(handler):]
            self.assertNotIn("runNoteSearch", line[:line.index("\n")])

    def test_the_vault_entry_points_are_hidden_until_the_deployment_has_one(self):
        self.assertIn("$('todoVaultSync').hidden = !vaultAvailable();", self.html)
        self.assertIn("$('askVault').hidden = !vaultAvailable();", self.html)

    def test_the_prompt_goes_out_as_json_not_in_the_address(self):
        block = self.html[self.html.index("async function openChat"):]
        block = block[:block.index("$('chatReload')")]
        self.assertIn("body: JSON.stringify(prompt ? { prompt } : {})", block)
        self.assertNotIn("?q=", block)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""「AI 聊天」框里的会话过期后自己续上：HaloWebUI 发来 reauth，本站换一张新票据重开框、落回刚才那个对话。

本站替它登录的会话 12 小时到期，而本页常常一开好几天。以前框里只剩一句「会话已过期，请重新登录」。
前端部分把模板里的主脚本放进 node + DOM 替身里真跑（同 test_chat_activity）；后端部分看票据接口的落点。
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest

from tests.test_chat_embed import HALO as HALO_ORIGIN, ChatCase
from tests.test_script_boot import CONFIGS_RESPONSE, NODE, RESPONSES, STUB, _inline_script
import app as app_module

HALO = "https://halo.example"

SCENARIO = r"""
const L = {};
globalThis.addEventListener = (type, fn) => { (L[type] = L[type] || []).push(fn); };
VIEWS.push('chat');
{
  const v = el('view-chat');
  v.id = 'view-chat';
  v.classList.toggle = function (cls, on) { if (cls === 'active' && on) activeView = 'chat'; origToggle.call(this, cls, on); };
}
const bodies = [];
const realFetch = globalThis.fetch;
globalThis.fetch = (url, opts) => {
  if (String(url) === '/api/chat/ticket') bodies.push(JSON.parse((opts && opts.body) || '{}'));
  return realFetch(url, opts);
};
const frames = [];
el('chatStage').appendChild = (f) => { f.contentWindow = { n: frames.length }; frames.push(f); };
el('chatStage').querySelector = () => frames.length ? frames[frames.length - 1] : null;
__SCRIPT__
const tick = () => new Promise((r) => setTimeout(r, 30));
let clock = 1_000_000_000;
Date.now = () => clock;
setTimeout(async () => {
  const out = { steps: [] };
  const snap = (label) => out.steps.push({ label, frames: frames.length, body: bodies[bodies.length - 1] || null,
                                           src: frames.length ? frames[frames.length - 1].src : '' });
  const send = (path, extra) => (L.message || []).forEach((fn) => fn(Object.assign({
    source: frames[frames.length - 1].contentWindow, origin: __HALO__, data: { source: 'halowebui', type: 'reauth', path },
  }, extra || {})));
  switchView('chat'); await tick(); snap('opened');
  send('/c/abc-123', { origin: 'https://evil.example' }); await tick(); snap('wrong origin');
  send('/c/abc-123', { source: {} }); await tick(); snap('wrong window');
  send('/c/abc-123'); await tick(); snap('expired');
  clock += 5_000;
  send('/c/abc-123'); await tick(); snap('again within a minute');
  clock += 61_000;
  send('/?q=' + encodeURIComponent('再问一遍')); await tick(); snap('a query is not a place to go back to');
  clock += 61_000;
  send('/c/../admin'); await tick(); snap('odd path');
  __RESPONSES['/api/chat/ticket'] = { ok: true, sso: false, url: __HALO__ + '/' };
  openChat(true); await tick(); snap('no ticket this time');
  clock += 61_000;
  send('/c/abc-123'); await tick(); snap('not signed in by us');
  out.errors = __CALLS.errors;
  console.log(JSON.stringify(out));
}, 60);
"""


@unittest.skipIf(NODE is None, "未安装 node，跳过")
class ChatReauthScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        responses = copy.deepcopy(RESPONSES)
        responses["/api/configs"] = dict(copy.deepcopy(CONFIGS_RESPONSE), chat={"url": HALO})
        responses["/api/chat/ticket"] = {"ok": True, "sso": True, "url": HALO + "/auth#hub_ticket=T"}
        with open(STUB, encoding="utf-8") as fh:
            stub = fh.read()
        harness = "\n".join([
            stub,
            "globalThis.__RESPONSES = %s;" % json.dumps(responses),
            SCENARIO.replace("__SCRIPT__", _inline_script()).replace("__HALO__", json.dumps(HALO)),
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as fh:
            fh.write(harness)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(path)
        if proc.returncode != 0:
            raise AssertionError(proc.stderr[-2000:])
        cls.out = json.loads(proc.stdout.strip().splitlines()[-1])
        cls.steps = {step["label"]: step for step in cls.out["steps"]}

    def test_script_runs_without_errors(self):
        self.assertEqual(self.out["errors"], [])

    def test_opening_the_tab_asks_for_no_particular_chat(self):
        step = self.steps["opened"]
        self.assertEqual((step["frames"], step["body"]), (1, {}))

    def test_only_the_current_chat_frame_on_the_configured_origin_is_heard(self):
        for label in ("wrong origin", "wrong window"):
            self.assertEqual(self.steps[label]["frames"], 1, label)

    def test_an_expired_session_gets_a_new_frame_back_on_the_same_chat(self):
        step = self.steps["expired"]
        self.assertEqual(step["frames"], 2)
        self.assertEqual(step["body"], {"back": "/c/abc-123"})
        self.assertEqual(step["src"], HALO + "/auth#hub_ticket=T")

    def test_at_most_once_a_minute(self):
        self.assertEqual(self.steps["again within a minute"]["frames"], 2)

    def test_only_a_chat_path_is_passed_on(self):
        for label in ("a query is not a place to go back to", "odd path"):
            step = self.steps[label]
            self.assertEqual(step["body"], {}, label)
        self.assertEqual(self.steps["odd path"]["frames"], 4)

    def test_a_frame_we_did_not_sign_in_is_left_alone(self):
        self.assertEqual(self.steps["no ticket this time"]["frames"], 5)
        self.assertEqual(self.steps["not signed in by us"]["frames"], 5)


class ChatBackUrlTest(unittest.TestCase):
    def test_back_lands_on_that_chat_after_sign_in(self):
        url, sent, cut = app_module.chat_target_url(HALO, "", "TICKET", "gpt-chat", "/c/abc-123")
        self.assertEqual((url, sent, cut), (HALO + "/auth?redirect=%2Fc%2Fabc-123#hub_ticket=TICKET", "", False))
        # 签不了票：直接去那个对话，对面自己登录后也回得来。
        self.assertEqual(app_module.chat_target_url(HALO, "", "", "", "/c/abc-123")[0], HALO + "/c/abc-123")

    def test_a_prompt_wins_over_back(self):
        url, sent, _ = app_module.chat_target_url(HALO, "你好", "TICKET", "", "/c/abc-123")
        self.assertIn("%2F%3Fq%3D", url)
        self.assertNotIn("abc-123", url)

    def test_only_chat_paths_are_accepted(self):
        self.assertEqual(app_module.clean_chat_back("/c/0b5e-42"), "/c/0b5e-42")
        for bad in ("/", "/?q=x", "/c/", "/c/a/b", "/c/../admin", "/c/abc?x=1", "/c/abc#x", "https://evil/c/abc",
                    "//evil/c/abc", "/c/abc\n", "/c/" + "a" * 65, None, 42, ["/c/abc"]):
            self.assertEqual(app_module.clean_chat_back(bad), "", repr(bad))


class ChatBackEndpointTest(ChatCase):
    def setUp(self):
        super().setUp()
        app_module.CHAT_SECRET = "unit-test-chat-secret-" + "0123456789abcdef" * 2
        self.unlock()

    def test_the_ticket_endpoint_honours_back(self):
        resp = self.client.post("/api/chat/ticket", json={"back": "/c/abc-123"}, headers={"Origin": "http://localhost"})
        data = resp.get_json()
        self.assertTrue(data["sso"], data)
        self.assertTrue(data["url"].startswith(HALO_ORIGIN + "/auth?redirect=%2Fc%2Fabc-123#hub_ticket=v2.chat."), data["url"])

    def test_a_bad_back_falls_back_to_the_home_page(self):
        resp = self.client.post("/api/chat/ticket", json={"back": "https://evil.example/"}, headers={"Origin": "http://localhost"})
        self.assertTrue(resp.get_json()["url"].startswith(HALO_ORIGIN + "/auth#hub_ticket="))


if __name__ == "__main__":
    unittest.main()

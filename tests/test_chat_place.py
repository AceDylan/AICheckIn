# -*- coding: utf-8 -*-
"""「AI 聊天」的「重新载入」「新标签页打开」回到框里正开着的那个对话。

以前两者都从 HaloWebUI 首页重新开始：框卡住了点「重新载入」，回来是一个空白新对话；
想把对话挪到单独的标签页里，打开的也是首页。现在框里的 HaloWebUI 每换一个对话报一次
{source:'halowebui', type:'place', path:'/c/<id>' | '/'}，本站记下来。
前端部分把模板里的主脚本放进 node + DOM 替身里真跑（同 test_chat_reauth）。
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest

from tests.test_script_boot import CONFIGS_RESPONSE, NODE, RESPONSES, STUB, _inline_script

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
// 替身的 addEventListener 是空操作：「重新载入」的点击处理留下来，下面直接调用。
el('chatReload').addEventListener = (type, fn) => { if (type === 'click') el('chatReload').__click = fn; };
__SCRIPT__
const tick = () => new Promise((r) => setTimeout(r, 30));
setTimeout(async () => {
  const out = { steps: [] };
  const snap = (label) => out.steps.push({ label, frames: frames.length, body: bodies[bodies.length - 1] || null,
                                           popout: el('chatPopout').href || '' });
  const send = (path, extra) => (L.message || []).forEach((fn) => fn(Object.assign({
    source: frames[frames.length - 1].contentWindow, origin: __HALO__, data: { source: 'halowebui', type: 'place', path },
  }, extra || {})));
  switchView('chat'); await tick(); snap('opened');
  send('/c/abc-123'); await tick(); snap('on a chat');
  send('/c/evil', { origin: 'https://evil.example' }); await tick(); snap('wrong origin');
  send('/c/evil', { source: {} }); await tick(); snap('wrong window');
  send('/?q=' + encodeURIComponent('再问一遍')); await tick(); snap('a query is not a place');
  send('/c/abc-123'); await tick();
  el('chatReload').__click(); await tick(); snap('reload');
  send('/'); await tick(); snap('back home');
  el('chatReload').__click(); await tick(); snap('reload at home');
  out.errors = __CALLS.errors;
  console.log(JSON.stringify(out));
}, 60);
"""


@unittest.skipIf(NODE is None, "未安装 node，跳过")
class ChatPlaceScriptTest(unittest.TestCase):
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

    def test_the_popout_starts_at_the_home_page(self):
        self.assertEqual(self.steps["opened"]["popout"], HALO + "/")

    def test_the_popout_follows_the_chat_on_screen(self):
        self.assertEqual(self.steps["on a chat"]["popout"], HALO + "/c/abc-123")

    def test_only_the_current_chat_frame_on_the_configured_origin_is_heard(self):
        for label in ("wrong origin", "wrong window"):
            self.assertEqual(self.steps[label]["popout"], HALO + "/c/abc-123", label)

    def test_only_a_chat_path_counts(self):
        self.assertEqual(self.steps["a query is not a place"]["popout"], HALO + "/")

    def test_reload_lands_on_the_same_chat(self):
        step = self.steps["reload"]
        self.assertEqual(step["frames"], 2)
        self.assertEqual(step["body"], {"back": "/c/abc-123"})
        self.assertEqual(step["popout"], HALO + "/c/abc-123")

    def test_reload_at_home_asks_for_no_particular_chat(self):
        step = self.steps["reload at home"]
        self.assertEqual((step["frames"], step["body"], step["popout"]), (3, {}, HALO + "/"))


if __name__ == "__main__":
    unittest.main()

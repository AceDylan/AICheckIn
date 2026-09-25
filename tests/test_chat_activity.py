# -*- coding: utf-8 -*-
"""「AI 聊天」标签上的回复状态：框里的 HaloWebUI 发来 running / done，本站在标签上点蓝点 / 绿点、
标签页标题加 ● / ✓；回到聊天页就清掉；别的窗口、别的源发来的消息一概不理。

把模板里的主脚本放进 node + DOM 替身里真跑（同 test_script_boot），再模拟框发来的 message 事件。
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
__SCRIPT__
setTimeout(() => {
  const out = { steps: [] };
  const frame = { contentWindow: { name: 'halo' }, remove() {} };
  el('chatStage').querySelector = () => frame;
  const dot = el('chatDot');
  const snap = (label) => out.steps.push({
    label, hidden: dot.hidden, running: dot.classList.contains('is-running'),
    done: dot.classList.contains('is-done'), title: document.title,
  });
  const send = (state, extra) => (L.message || []).forEach((fn) => fn(Object.assign({
    source: frame.contentWindow, origin: __HALO__, data: { source: 'halowebui', type: 'activity', state },
  }, extra || {})));
  snap('start');
  send('running'); snap('running elsewhere');
  send('done', { origin: 'https://evil.example' }); snap('wrong origin');
  send('done', { source: {} }); snap('wrong window');
  send('done', { data: { source: 'other', type: 'activity', state: 'done' } }); snap('wrong sender');
  send('bogus'); snap('unknown state');
  send('done'); snap('done elsewhere');
  send('idle'); snap('idle keeps unseen');
  switchView('chat'); snap('opened chat');
  send('running'); snap('running while watching');
  send('done'); snap('done while watching');
  switchView('bookmarks'); snap('left chat after seeing');
  out.errors = __CALLS.errors;
  console.log(JSON.stringify(out));
}, 60);
"""


@unittest.skipIf(NODE is None, "未安装 node，跳过")
class ChatActivityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        responses = copy.deepcopy(RESPONSES)
        responses["/api/configs"] = dict(copy.deepcopy(CONFIGS_RESPONSE), chat={"url": HALO})
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

    def test_nothing_to_show_at_first(self):
        start = self.steps["start"]
        self.assertTrue(start["hidden"])
        self.assertFalse(start["title"].startswith(("● ", "✓ ")))

    def test_a_running_reply_shows_while_on_another_page(self):
        step = self.steps["running elsewhere"]
        self.assertFalse(step["hidden"])
        self.assertTrue(step["running"])
        self.assertTrue(step["title"].startswith("● "))

    def test_messages_from_anything_but_the_chat_frame_are_ignored(self):
        for label in ("wrong origin", "wrong window", "wrong sender", "unknown state"):
            step = self.steps[label]
            self.assertTrue(step["running"], label)
            self.assertFalse(step["done"], label)

    def test_a_reply_finished_unseen_leaves_a_green_dot_until_the_chat_is_opened(self):
        for label in ("done elsewhere", "idle keeps unseen"):
            step = self.steps[label]
            self.assertFalse(step["hidden"], label)
            self.assertTrue(step["done"], label)
            self.assertTrue(step["title"].startswith("✓ "), label)
        opened = self.steps["opened chat"]
        self.assertTrue(opened["hidden"])
        self.assertFalse(opened["title"].startswith(("● ", "✓ ")))

    def test_a_reply_finished_while_watching_leaves_nothing_behind(self):
        running = self.steps["running while watching"]
        self.assertTrue(running["hidden"])          # 就在聊天页，不用点
        self.assertTrue(running["title"].startswith("● "))
        for label in ("done while watching", "left chat after seeing"):
            step = self.steps[label]
            self.assertTrue(step["hidden"], label)
            self.assertFalse(step["title"].startswith(("● ", "✓ ")), label)


class ChatActivityShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "templates", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()
        with open(os.path.join(root, "static", "app-v3.css"), encoding="utf-8") as fh:
            cls.css = fh.read()

    def test_only_the_current_chat_frame_on_the_configured_origin_is_heard(self):
        self.assertIn("e.source !== frame.contentWindow || !e.origin || e.origin !== chatOrigin()", self.html)

    def test_the_dot_has_its_styles(self):
        for selector in (".tab .chat-dot.is-done", ".tab .chat-dot.is-running", ".tab .chat-dot[hidden]"):
            self.assertIn(selector, self.css, selector)


if __name__ == "__main__":
    unittest.main()

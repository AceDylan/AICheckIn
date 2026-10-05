# -*- coding: utf-8 -*-
"""在「笔记」标签页里打开某一篇：AI 聊天框发来 open-note、首页搜到的笔记、?note= 深链。

把模板里的主脚本放进 node + DOM 替身里真跑（同 test_chat_activity），看笔记框被换成了哪个地址。
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from urllib.parse import quote

from tests.test_script_boot import CONFIGS_RESPONSE, NODE, RESPONSES, STUB, _inline_script

HALO = "https://halo.example"
VAULT = "https://notes.example"


def frame_src(path):
    """/vault/open?to= 里的落点：/note/ 后每段各自编码，整个落点再编码一次（与 encodeURIComponent 一致）。"""
    to = "/note/" + "/".join(quote(part, safe="") for part in path.split("/"))
    return "/vault/open?to=" + quote(to, safe="") + "&theme=dark"   # 本站此刻的深浅色，见 test_frame_theme

SCENARIO = r"""
const L = {};
globalThis.addEventListener = (type, fn) => { (L[type] = L[type] || []).push(fn); };
VIEWS.push('chat', 'vault');
for (const n of ['chat', 'vault']) {
  const v = el('view-' + n);
  v.id = 'view-' + n;
  v.classList.toggle = function (cls, on) { if (cls === 'active' && on) activeView = n; origToggle.call(this, cls, on); };
}
const frames = [], hops = [];
el('vaultStage').appendChild = (f) => { frames.push(f); };
el('vaultStage').querySelector = () => frames.length ? frames[frames.length - 1] : null;
document.body.appendChild = (h) => { hops.push(h); };   // 替笔记框登录的那张看不见的小框
__SCRIPT__
setTimeout(() => {
  const out = { steps: [] };
  const chat = { contentWindow: { name: 'halo' }, remove() {} };
  el('chatStage').querySelector = () => chat;
  // 笔记框自己是空框（名字 hub-vault-<序号>），登录那一页开在小框里、表单 target 指回它。
  const snap = (label) => {
    const frame = frames[frames.length - 1], hop = hops[hops.length - 1];
    const m = hop ? /^(.*)&target=(hub-vault-[0-9]+)$/.exec(hop.src) : null;
    out.steps.push({ label, view: currentViewName(), frames: frames.length, hops: hops.length,
                     src: m ? m[1] : '', target: m ? m[2] : '', name: frame ? frame.name : '', frameSrc: frame ? (frame.src || '') : '' });
  };
  const send = (path, extra) => (L.message || []).forEach((fn) => fn(Object.assign({
    source: chat.contentWindow, origin: __HALO__, data: { source: 'halowebui', type: 'open-note', path },
  }, extra || {})));
  snap('start');
  send('项目/HaloWebUI.md', { origin: 'https://evil.example' }); snap('wrong origin');
  send('项目/HaloWebUI.md', { source: {} }); snap('wrong window');
  for (const bad of ['../x.md', '/etc/passwd', '.obsidian/app.json', 'a//b.md', 'a\\b.md', '', 42, 'x\u0000.md']) {
    send(bad); snap('bad ' + JSON.stringify(bad));
  }
  send('项目/HaloWebUI.md'); snap('from chat');
  send('运维/a b#c?.md'); snap('again while on the vault tab');
  switchView('bookmarks');
  runHomeSearchRow({ type: 'note', hit: { path: '知识库/x.md', url: __VAULT__ + '/note/x' } }); snap('search hit');
  switchView('bookmarks');
  location.search = '?note=' + encodeURIComponent('项目/z.md');
  out.noteParam = consumeNoteParam(); snap('note param');
  location.search = '';
  out.noParam = consumeNoteParam();
  out.opened = [];
  globalThis.open = (u) => out.opened.push(u);
  STATE.vault_embed = null;
  switchView('bookmarks');
  runHomeSearchRow({ type: 'note', hit: { path: '知识库/y.md', url: __VAULT__ + '/note/y' } }); snap('no embed falls back');

  // 笔记框里的 WebObsidian 说过「准备好了」之后，就在框里打开，不再换框重新登录。
  STATE.vault_embed = { url: __VAULT__ };
  const posted = [];
  const live = () => { const cw = { postMessage: (d, o) => posted.push({ d, o }) }; frames[frames.length - 1].contentWindow = cw; return cw; };
  const fromVault = (cw, data, extra) => (L.message || []).forEach((fn) => fn(Object.assign({
    source: cw, origin: __VAULT__, data: Object.assign({ source: 'webobsidian' }, data),
  }, extra || {})));
  const hit = (path) => { switchView('bookmarks'); runHomeSearchRow({ type: 'note', hit: { path, url: __VAULT__ + '/note/x' } }); };
  // 框说 ready 时 Hub 还会发一条 enter（转场用），按下标取请求 id 只数 open-note。
  const asks = () => posted.filter((m) => m.d.type === 'open-note');
  hit('p/1.md'); snap('fresh before ready');
  let cw = live();
  fromVault(cw, { type: 'ready' }, { origin: 'https://evil.example' });
  fromVault({}, { type: 'ready' });
  fromVault(cw, { type: 'ready' }, { data: { source: 'halowebui', type: 'ready' } });
  hit('p/2.md'); snap('ready from elsewhere ignored');
  cw = live();
  fromVault(cw, { type: 'ready' });
  hit('p/3.md'); snap('in place');
  fromVault(cw, { type: 'open-note', id: asks()[0].d.id, ok: true });
  send('p/4.md'); snap('in place from chat');
  fromVault(cw, { type: 'open-note', id: asks()[1].d.id + 99, ok: false });   // 别的请求的回话不算数
  snap('stray answer ignored');
  fromVault(cw, { type: 'open-note', id: asks()[1].d.id, ok: false }); snap('refused');
  cw = live();
  fromVault(cw, { type: 'ready' });
  send('p/5.md'); snap('waiting for an answer');
  setTimeout(() => {
    snap('no answer');
    // 键盘挡住了笔记框底部多少：只在笔记页、没缩放时算，数没变不重发。
    const kbFrame = frames[frames.length - 1];
    live();
    fromVault(kbFrame.contentWindow, { type: 'ready' });
    kbFrame.getBoundingClientRect = () => ({ bottom: 724 });
    globalThis.visualViewport = { scale: 1, offsetTop: 0, height: 800 };
    const kbFrom = posted.length;
    tellVaultKeyboard();
    visualViewport.height = 420; tellVaultKeyboard(); tellVaultKeyboard();
    visualViewport.offsetTop = 100; tellVaultKeyboard();
    visualViewport.scale = 1.5; tellVaultKeyboard();
    visualViewport.scale = 1; tellVaultKeyboard();
    switchView('bookmarks'); tellVaultKeyboard();
    out.keyboard = posted.slice(kbFrom).map((m) => [m.d.type, m.d.covered, m.o]);
    out.posted = posted.filter((m) => m.d.type === 'open-note').map((m) => ({ type: m.d.type, source: m.d.source, path: m.d.path, origin: m.o }));
    out.errors = __CALLS.errors;
    console.log(JSON.stringify(out));
  }, 2700);
}, 60);
"""


@unittest.skipIf(NODE is None, "未安装 node，跳过")
class OpenNoteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        responses = copy.deepcopy(RESPONSES)
        responses["/api/configs"] = dict(copy.deepcopy(CONFIGS_RESPONSE), chat={"url": HALO},
                                         vault={"url": VAULT, "inbox": "收件箱"}, vault_embed={"url": VAULT})
        with open(STUB, encoding="utf-8") as fh:
            stub = fh.read()
        harness = "\n".join([
            stub,
            "globalThis.__RESPONSES = %s;" % json.dumps(responses),
            SCENARIO.replace("__SCRIPT__", _inline_script()).replace("__HALO__", json.dumps(HALO))
                    .replace("__VAULT__", json.dumps(VAULT)),
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

    def test_only_the_chat_frame_on_its_origin_can_ask(self):
        for label in ("wrong origin", "wrong window"):
            self.assertEqual(self.steps[label]["frames"], 0, label)
            self.assertEqual(self.steps[label]["view"], "bookmarks", label)

    def test_paths_that_could_leave_the_vault_are_ignored(self):
        bad = [s for s in self.out["steps"] if s["label"].startswith("bad ")]
        self.assertEqual(len(bad), 8)
        for step in bad:
            self.assertEqual(step["frames"], 0, step["label"])

    def test_a_note_from_the_chat_opens_in_the_vault_tab(self):
        step = self.steps["from chat"]
        self.assertEqual(step["view"], "vault")
        self.assertEqual(step["frames"], 1)
        self.assertEqual(step["src"], frame_src("项目/HaloWebUI.md"))

    def test_the_sign_in_hop_posts_into_the_blank_frame_it_was_opened_for(self):
        # 笔记框本身不带地址（空框的第一次导航不在整页历史里记条目），登录页在小框里打开、表单 target 指回它。
        for label in ("from chat", "again while on the vault tab", "search hit", "note param"):
            step = self.steps[label]
            self.assertEqual(step["frameSrc"], "", label)
            self.assertRegex(step["name"], r"^hub-vault-[0-9]+$", label)
            self.assertEqual(step["target"], step["name"], label)
            self.assertEqual(step["hops"], step["frames"], label)
        self.assertNotEqual(self.steps["from chat"]["name"], self.steps["again while on the vault tab"]["name"])

    def test_already_on_the_vault_tab_a_fresh_frame_lands_on_the_new_note(self):
        step = self.steps["again while on the vault tab"]
        self.assertEqual(step["frames"], 2)
        # 每一段各自编码：空格、#、? 都不会把地址截断。
        self.assertEqual(step["src"], frame_src("运维/a b#c?.md"))
        self.assertIn("a%2520b%2523c%253F.md", step["src"])

    def test_a_search_hit_stays_in_the_hub(self):
        step = self.steps["search hit"]
        self.assertEqual(step["view"], "vault")
        self.assertEqual(step["src"], frame_src("知识库/x.md"))

    def test_without_the_vault_tab_a_search_hit_opens_the_note_site(self):
        step = self.steps["no embed falls back"]
        self.assertEqual(step["frames"], 4)
        self.assertEqual(self.out["opened"], [VAULT + "/note/y"])

    def test_before_the_frame_says_ready_a_fresh_frame_signs_in(self):
        for label, path in (("fresh before ready", "p/1.md"), ("ready from elsewhere ignored", "p/2.md")):
            step = self.steps[label]
            self.assertEqual(step["view"], "vault", label)
            self.assertEqual(step["src"], frame_src(path), label)
        self.assertEqual(self.steps["ready from elsewhere ignored"]["frames"],
                         self.steps["fresh before ready"]["frames"] + 1)

    def test_a_ready_frame_opens_the_note_in_place(self):
        before = self.steps["ready from elsewhere ignored"]
        for label in ("in place", "in place from chat", "stray answer ignored"):
            step = self.steps[label]
            self.assertEqual(step["view"], "vault", label)
            self.assertEqual(step["frames"], before["frames"], label)
            self.assertEqual(step["hops"], before["hops"], label)
        self.assertEqual(self.out["posted"][:2], [
            {"type": "open-note", "source": "hub", "path": "p/3.md", "origin": VAULT},
            {"type": "open-note", "source": "hub", "path": "p/4.md", "origin": VAULT},
        ])

    def test_a_refusal_or_no_answer_falls_back_to_a_fresh_frame(self):
        before = self.steps["ready from elsewhere ignored"]["frames"]
        refused = self.steps["refused"]
        self.assertEqual(refused["frames"], before + 1)
        self.assertEqual(refused["src"], frame_src("p/4.md"))
        waiting = self.steps["waiting for an answer"]
        self.assertEqual(waiting["frames"], before + 1)
        late = self.steps["no answer"]
        self.assertEqual(late["frames"], before + 2)
        self.assertEqual(late["src"], frame_src("p/5.md"))
        self.assertEqual(len(self.out["posted"]), 3)

    def test_the_frame_learns_how_much_the_keyboard_covers(self):
        self.assertEqual(self.out["keyboard"], [
            ["keyboard", 304, VAULT],   # 724 - (0 + 420)
            ["keyboard", 204, VAULT],   # 可视视口往下滚了 100
            ["keyboard", 0, VAULT],     # 双指缩放，不算键盘
            ["keyboard", 204, VAULT],
            ["keyboard", 0, VAULT],     # 离开笔记页
        ])

    def test_a_note_address_from_elsewhere_opens_in_the_vault_tab(self):
        step = self.steps["note param"]
        self.assertTrue(self.out["noteParam"])
        self.assertFalse(self.out["noParam"])   # 没有 ?note= 就交给分享参数处理
        self.assertEqual(step["view"], "vault")
        self.assertEqual(step["src"], frame_src("项目/z.md"))


class OpenNoteShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "templates", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()

    def test_a_note_link_from_elsewhere_is_read_from_the_query(self):
        self.assertIn("new URLSearchParams(location.search).get('note')", self.html)
        self.assertIn("if (!consumeNoteParam()) consumeShareParams();", self.html)

    def test_a_capture_offers_to_show_the_note(self):
        self.assertIn("toastAction(`已存入「${data.path}」`, '查看', () => openVaultNote(data.path)", self.html)


if __name__ == "__main__":
    unittest.main()

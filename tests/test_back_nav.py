# -*- coding: utf-8 -*-
"""浏览器的「返回」：收藏库是首页，别的页面按「返回」先回收藏库，再按一次才离开本站。

原来切页一律只改当前这一条历史：在「AI 聊天」「笔记」里按返回（手机的返回手势）直接离开 Hub；
从聊天框点回「收藏库」后，前两次返回只是藏着的聊天框在后台翻对话，眼前什么都没变。

把模板里的主脚本放进 node + DOM 替身里真跑（同 test_open_note），用一个假的 Navigation API
记下每一次 pushState / replaceState / traverseTo，看历史长成什么样。
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest

from tests.test_script_boot import CONFIGS_RESPONSE, NODE, RESPONSES, STUB, _inline_script

HALO = "https://halo.example"
VAULT = "https://notes.example"

# 假的会话历史：entries 是本页自己的那几条（url 只看 #hash），index 是当前那条。
SCENARIO = r"""
const L = {};
globalThis.addEventListener = (type, fn) => { (L[type] = L[type] || []).push(fn); };
VIEWS.push('chat', 'vault');
for (const n of ['chat', 'vault']) {
  const v = el('view-' + n);
  v.id = 'view-' + n;
  v.classList.toggle = function (cls, on) { if (cls === 'active' && on) activeView = n; origToggle.call(this, cls, on); };
}
const LOG = [];
let seq = 0;
const H = { entries: [{ key: 'k0', hash: __START__, sameDocument: true }], index: 0 };
__BEFORE__
const setHash = (hash) => { location.hash = hash.startsWith('#') ? hash : ''; };
history.pushState = (s, t, url) => {
  LOG.push('push ' + url);
  H.entries = H.entries.slice(0, H.index + 1);
  H.entries.push({ key: 'k' + (++seq), hash: url, sameDocument: true });
  H.index += 1; setHash(url);
};
history.replaceState = (s, t, url) => {
  LOG.push('replace ' + url);
  H.entries[H.index].hash = url; setHash(url);
};
if (__NAV__) {
  globalThis.navigation = {
    get currentEntry() { return { index: H.index, key: H.entries[H.index].key }; },
    entries() { return H.entries.map((e, i) => ({ index: i, key: e.key, sameDocument: e.sameDocument,
                                                  url: 'https://hub.example/' + (e.hash.startsWith('#') ? e.hash : '') })); },
    traverseTo(key) {
      LOG.push('traverse ' + key);
      const i = H.entries.findIndex((e) => e.key === key);
      // 真浏览器里落地是异步的：committed 先兑现，hashchange 随后才派发。
      const committed = Promise.resolve().then(() => { H.index = i; setHash(H.entries[i].hash); });
      committed.then(() => setTimeout(() => (L.hashchange || []).forEach((fn) => fn()), 0));
      return { committed, finished: committed };
    },
  };
}
__SCRIPT__
const back = () => new Promise((done) => {   // 浏览器的「返回」：退一条，再派发 hashchange
  LOG.push('BACK');
  H.index -= 1; setHash(H.entries[H.index].hash);
  (L.hashchange || []).forEach((fn) => fn());
  setTimeout(done, 5);
});
const tab = (view) => { switchView(view); if (view === 'bookmarks') openLibPage('@home'); };
const settle = () => new Promise((done) => setTimeout(done, 10));
setTimeout(async () => {
  const out = { steps: [] };
  const snap = (label) => {
    out.steps.push({ label, view: currentViewName(), hash: location.hash, index: H.index,
                     entries: H.entries.map((e) => e.hash), log: LOG.splice(0) });
  };
  snap('start');
  __STEPS__
  out.errors = __CALLS.errors.concat(__CALLS.rejections || []);
  console.log(JSON.stringify(out));
}, 60);
"""

STEPS_MAIN = r"""
  tab('chat'); snap('library -> chat');
  tab('vault'); snap('chat -> vault');
  tab('settings'); snap('vault -> settings');
  tab('bookmarks'); snap('settings -> library (tap)');
  await settle(); snap('landed');
  tab('checkin'); snap('library -> checkin');
  switchView('checkin/configs'); snap('checkin inner page');
  await back(); snap('back from checkin');
  tab('bookmarks'); await settle(); snap('tap library while on library');
  tab('chat'); snap('library -> chat again');
  await back(); snap('back from chat');
"""

STEPS_NO_NAV = r"""
  tab('chat'); snap('library -> chat');
  tab('vault'); snap('chat -> vault');
  tab('bookmarks'); await settle(); snap('tap library');
  tab('checkin'); snap('library -> checkin');
"""

# 刷新过的页面（下面那条是上一个文档的）、从别处直接打开 #chat：没有本页自己的收藏库那一条可退，只改当前这一条。
STEPS_RELOADED = r"""
  tab('bookmarks'); await settle(); snap('tap library');
"""


def run(start="", nav=True, steps=STEPS_MAIN, before=""):
    responses = copy.deepcopy(RESPONSES)
    responses["/api/configs"] = dict(copy.deepcopy(CONFIGS_RESPONSE),
                                     chat={"url": HALO}, vault_embed={"url": VAULT})
    with open(STUB, encoding="utf-8") as fh:
        stub = fh.read()
    harness = "\n".join([
        stub,
        "globalThis.__RESPONSES = %s;" % json.dumps(responses),
        "location.hash = %s;" % json.dumps(start),
        SCENARIO.replace("__SCRIPT__", _inline_script()).replace("__START__", json.dumps(start))
                .replace("__NAV__", "true" if nav else "false").replace("__STEPS__", steps)
                .replace("__BEFORE__", before),
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
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    return out, {step["label"]: step for step in out["steps"]}


@unittest.skipIf(NODE is None, "未安装 node，跳过")
class BackNavTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out, cls.steps = run()

    def test_script_runs_without_errors(self):
        self.assertEqual(self.out["errors"], [])

    def test_leaving_the_library_records_one_entry(self):
        step = self.steps["library -> chat"]
        self.assertEqual(step["log"], ["push #chat"])
        self.assertEqual(step["entries"], ["", "#chat"])   # 打开时的地址没有 #，原样留着
        self.assertEqual(step["view"], "chat")

    def test_moving_between_other_pages_only_rewrites_that_entry(self):
        self.assertEqual(self.steps["chat -> vault"]["log"], ["replace #vault"])
        self.assertEqual(self.steps["vault -> settings"]["log"], ["replace #settings"])
        self.assertEqual(self.steps["vault -> settings"]["entries"], ["", "#settings"])
        self.assertEqual(self.steps["checkin inner page"]["log"], ["replace #checkin/configs"])

    def test_tapping_the_library_goes_back_to_its_entry_instead_of_adding_one(self):
        tap = self.steps["settings -> library (tap)"]
        self.assertEqual(tap["view"], "bookmarks")                 # 页面立刻就换了
        self.assertEqual(tap["log"], ["traverse k0"])              # 地址等落地后再写，不会改到要离开的那一条
        landed = self.steps["landed"]
        self.assertEqual(landed["index"], 0)
        self.assertEqual(landed["hash"], "#bookmarks")
        self.assertEqual(landed["view"], "bookmarks")
        self.assertEqual(landed["entries"], ["#bookmarks", "#settings"])   # 刚才那页留作「前进」
        self.assertEqual(landed["log"], ["replace #bookmarks"])

    def test_back_from_another_page_lands_on_the_library(self):
        step = self.steps["back from checkin"]
        self.assertEqual(step["view"], "bookmarks")
        self.assertEqual(step["index"], 0)
        self.assertEqual(self.steps["library -> checkin"]["entries"], ["#bookmarks", "#checkin"])   # 截掉了前进的那一条
        again = self.steps["back from chat"]
        self.assertEqual(again["view"], "bookmarks")
        self.assertEqual(again["index"], 0)

    def test_the_library_itself_never_stacks_entries(self):
        step = self.steps["tap library while on library"]
        self.assertNotIn("push", " ".join(step["log"]))
        self.assertNotIn("traverse", " ".join(step["log"]))
        self.assertEqual(self.steps["library -> chat again"]["entries"], ["#bookmarks", "#chat"])

    def test_without_the_navigation_api_nothing_changes(self):
        out, steps = run(nav=False, steps=STEPS_NO_NAV)
        self.assertEqual(out["errors"], [])
        for step in out["steps"]:
            self.assertFalse([x for x in step["log"] if not x.startswith("replace ")], step["label"])
            self.assertEqual(step["index"], 0, step["label"])
        self.assertEqual(steps["library -> checkin"]["view"], "checkin")

    def test_a_reloaded_page_has_no_library_entry_of_its_own_to_go_back_to(self):
        out, steps = run(start="#chat", steps=STEPS_RELOADED,
                         before="H.entries = [{ key: 'old', hash: '', sameDocument: false }, { key: 'k0', hash: '#chat', sameDocument: true }]; H.index = 1;")
        self.assertEqual(out["errors"], [])
        step = steps["tap library"]
        self.assertEqual(step["view"], "bookmarks")
        self.assertEqual([x for x in step["log"] if not x.startswith("replace ")], [])
        self.assertEqual(step["index"], 1)


if __name__ == "__main__":
    unittest.main()

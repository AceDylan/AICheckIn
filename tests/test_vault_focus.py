# -*- coding: utf-8 -*-
"""到了「笔记」页，键盘交给笔记框（有鼠标键盘的设备上）。

焦点原来停在本站的标签按钮上：框里提示的 Ctrl+O / Ctrl+P 按下去是浏览器的「打开文件」「打印」。
把模板里的主脚本放进 node + DOM 替身里真跑（同 test_open_note），看框有没有被 focus()。
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest

from tests.test_script_boot import CONFIGS_RESPONSE, NODE, RESPONSES, STUB, _inline_script

VAULT = "https://notes.example"

SCENARIO = r"""
VIEWS.push('vault');
{
  const v = el('view-vault');
  v.id = 'view-vault';
  v.classList.toggle = function (cls, on) { if (cls === 'active' && on) activeView = 'vault'; origToggle.call(this, cls, on); };
}
let fine = __FINE__;
globalThis.matchMedia = (q) => ({ matches: q.includes('pointer: fine') ? fine : false, addEventListener() {}, addListener() {} });
const frames = [];
let focused = 0;
el('vaultStage').appendChild = (f) => { f.focus = () => { focused += 1; }; frames.push(f); };
el('vaultStage').querySelector = () => frames.length ? frames[frames.length - 1] : null;
__SCRIPT__
setTimeout(() => {
  const out = { steps: [] };
  const snap = (label) => out.steps.push({ label, focused, frames: frames.length });
  const typing = { closest: () => null };           // 本站自己的某个输入框
  const tabButton = { closest: (sel) => sel.includes('.tabs') ? {} : null };
  Object.defineProperty(document, 'activeElement', { configurable: true, get: () => ACTIVE });
  globalThis.ACTIVE = tabButton;
  switchView('vault'); snap('switched from the tab bar');
  ACTIVE = typing;
  switchView('bookmarks'); switchView('vault'); snap('switched while typing elsewhere');
  ACTIVE = tabButton;
  switchView('bookmarks'); switchView('vault'); snap('back again');
  out.errors = __CALLS.errors;
  console.log(JSON.stringify(out));
}, 60);
"""


def run(fine):
    responses = copy.deepcopy(RESPONSES)
    responses["/api/configs"] = dict(copy.deepcopy(CONFIGS_RESPONSE), vault_embed={"url": VAULT})
    with open(STUB, encoding="utf-8") as fh:
        stub = fh.read()
    harness = "\n".join([
        stub,
        "globalThis.__RESPONSES = %s;" % json.dumps(responses),
        SCENARIO.replace("__SCRIPT__", _inline_script()).replace("__FINE__", "true" if fine else "false"),
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
class VaultFocusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.desk, cls.desk_steps = run(True)
        cls.phone, cls.phone_steps = run(False)

    def test_script_runs_without_errors(self):
        self.assertEqual(self.desk["errors"], [])
        self.assertEqual(self.phone["errors"], [])

    def test_the_keyboard_goes_to_the_frame(self):
        self.assertEqual(self.desk_steps["switched from the tab bar"]["frames"], 1)
        self.assertEqual(self.desk_steps["switched from the tab bar"]["focused"], 1)
        self.assertEqual(self.desk_steps["back again"]["focused"], 2)   # 框没换，回来照样交过去

    def test_focus_is_not_taken_from_an_input_on_this_page(self):
        self.assertEqual(self.desk_steps["switched while typing elsewhere"]["focused"], 1)

    def test_nothing_happens_on_a_touch_screen(self):
        for step in self.phone["steps"]:
            self.assertEqual(step["focused"], 0, step["label"])


if __name__ == "__main__":
    unittest.main()

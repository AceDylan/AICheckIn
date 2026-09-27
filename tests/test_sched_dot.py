# -*- coding: utf-8 -*-
"""「系统设置」标签上的点只在要人管的时候亮。

以前「开着定时签到」就一直亮着：天天开着的人永远有一个消不掉的点，真出事时反而看不出来。
现在只在两种情况下亮：开着定时签到但后台调度没运行；今天那一轮跑过了、仍有账号没签上、补签次数也用完了。
把模板里的主脚本放进 node + DOM 替身里真跑，逐个换 STATE 再 renderSettings()。
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest

from tests.test_script_boot import CONFIGS_RESPONSE, NODE, RESPONSES, STUB, _inline_script

SCENARIO = r"""
__SCRIPT__
setTimeout(() => {
  const out = {};
  const base = { enabled: true, time: '08:30', retry_limit: 3, retry_count: 0, pending_today: 0,
                 last_run_date: '2026-09-27', clock: { now: '2026-09-27 17:00:00' } };
  const cases = {
    all_signed: [base, true],
    disabled: [Object.assign({}, base, { enabled: false }), true],
    scheduler_off: [base, false],
    retrying: [Object.assign({}, base, { pending_today: 1, retry_count: 1 }), true],
    retries_used_up: [Object.assign({}, base, { pending_today: 2, retry_count: 3 }), true],
    before_todays_run: [Object.assign({}, base, { pending_today: 2, retry_count: 3, last_run_date: '2026-09-26' }), true],
  };
  for (const [name, [sched, running]] of Object.entries(cases)) {
    STATE.schedule = sched; STATE.scheduler_running = running;
    renderSettings();
    out[name] = { shown: el('schedDot').style.display === 'inline-block', title: el('schedDot').title || '' };
  }
  out.errors = __CALLS.errors;
  console.log(JSON.stringify(out));
}, 60);
"""


@unittest.skipIf(NODE is None, "未安装 node，跳过")
class SchedDotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        responses = copy.deepcopy(RESPONSES)
        responses["/api/configs"] = copy.deepcopy(CONFIGS_RESPONSE)
        with open(STUB, encoding="utf-8") as fh:
            stub = fh.read()
        harness = "\n".join([stub, "globalThis.__RESPONSES = %s;" % json.dumps(responses),
                             SCENARIO.replace("__SCRIPT__", _inline_script())])
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

    def test_script_runs_without_errors(self):
        self.assertEqual(self.out["errors"], [])

    def test_quiet_when_nothing_needs_doing(self):
        for name in ("all_signed", "disabled", "retrying", "before_todays_run"):
            self.assertFalse(self.out[name]["shown"], name)

    def test_lit_when_the_scheduler_is_not_running(self):
        self.assertTrue(self.out["scheduler_off"]["shown"])
        self.assertIn("调度", self.out["scheduler_off"]["title"])

    def test_lit_when_retries_are_used_up_today(self):
        self.assertTrue(self.out["retries_used_up"]["shown"])
        self.assertIn("2 个账号", self.out["retries_used_up"]["title"])


if __name__ == "__main__":
    unittest.main()

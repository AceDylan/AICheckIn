# -*- coding: utf-8 -*-
"""依次跑同目录下的每一套浏览器回归（各起各的隔离服务），最后给一张汇总；任何一套有失败，整体以非零退出。

    python tests/browser/run_all.py
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ("verify", "verify_polish", "verify_railtip", "verify_transport", "verify_deck", "verify_chat", "verify_ask")


def run(name):
    t0 = time.time()
    proc = subprocess.run([sys.executable, os.path.join(HERE, name + ".py")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    fails = [line for line in proc.stdout.splitlines() if line.startswith("FAIL ")]
    total = re.search(r"^(\d+) checks, (\d+) failed", proc.stdout, re.M)
    for line in fails:
        print("  " + line)
    if not total:   # 连汇总行都没打出来 = 脚本自己崩了，把尾巴亮出来
        print(proc.stdout[-1500:])
    ok = proc.returncode == 0 and bool(total) and total.group(2) == "0"
    print("%-18s %s  %s 项，失败 %s，%.0fs" % (name, "OK  " if ok else "FAIL", total.group(1) if total else "?", total.group(2) if total else "?", time.time() - t0), flush=True)
    return ok


if __name__ == "__main__":
    results = [run(name) for name in SUITES]
    sys.exit(0 if all(results) else 1)

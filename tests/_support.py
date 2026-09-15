# -*- coding: utf-8 -*-
"""测试共用：让每个测试用例拥有独立的数据目录。

app.py 在导入时从环境变量解析一次 CONFIG_FILE / DATA_DIR，各测试模块顶部的
`os.environ.setdefault("GYQD_CONFIG_FILE", ...)` 只有最先被导入的那个生效。
一次跑完整套件时所有模块因此共用同一份 config.json，setUp 之间互相覆盖，
单跑全绿、合跑报错。这里改为在 setUp 阶段直接重定向模块全局，逐个用例隔离。
"""
import json
import shutil
import tempfile
from pathlib import Path

import app as app_module  # tests/__init__.py 已提前备好 app 导入期要读的环境变量

# 所有从 DATA_DIR 派生、需要一起重定向的模块全局。
_REDIRECTED = ("CONFIG_FILE", "DATA_DIR", "HISTORY_FILE", "METRICS_FILE",
               "FAVICON_DIR", "SESSION_SECRET_FILE")


class StoreIsolationMixin(object):
    """把 app 的数据目录换成本用例专属的临时目录；tearDown 阶段自动还原并清理。

    用法：`class XxxTest(StoreIsolationMixin, unittest.TestCase)`，
    子类的 setUp 里先调用 `super().setUp()` 再写配置。
    """

    def setUp(self):
        super(StoreIsolationMixin, self).setUp()
        self.data_dir = Path(tempfile.mkdtemp())
        saved = {name: getattr(app_module, name) for name in _REDIRECTED}
        saved["_session_secret_cache"] = app_module._session_secret_cache
        app_module.CONFIG_FILE = str(self.data_dir / "config.json")
        app_module.DATA_DIR = self.data_dir
        app_module.HISTORY_FILE = str(self.data_dir / "history.json")
        app_module.METRICS_FILE = str(self.data_dir / "metrics.json")
        app_module.FAVICON_DIR = self.data_dir / "favicons"
        app_module.SESSION_SECRET_FILE = self.data_dir / ".session_secret"
        app_module._session_secret_cache = None
        app_module._login_fails.clear()  # 防爆破计数是进程级的，逐用例清零免得互相影响
        self.addCleanup(self._restore_store, saved)

    def _restore_store(self, saved):
        for name, value in saved.items():
            setattr(app_module, name, value)
        shutil.rmtree(str(self.data_dir), ignore_errors=True)

    # ---- 便捷读写 ----

    def write_config(self, payload):
        with open(app_module.CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def read_config(self):
        with open(app_module.CONFIG_FILE, encoding="utf-8") as fh:
            return json.load(fh)

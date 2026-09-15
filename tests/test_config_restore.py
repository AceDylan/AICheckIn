# -*- coding: utf-8 -*-
"""配置恢复：从备份把 config.json 救回来。

config.json 读不出来时（磁盘故障、手工编辑写坏了）整个服务就是一堆 500，
而能救命的备份就躺在同一个目录里。这套接口的要点：
- 列备份时**不能**依赖 read_store —— 坏掉的正是那个文件；
- 备份标识只接受固定形态，绝不能把用户给的字符串拼进路径；
- 坏备份不许拿来覆盖（把一个问题变成两个）；
- 被覆盖的那份必须留证。
"""
import json
import os
import unittest

from tests._support import StoreIsolationMixin, app_module
from app import app, list_config_backups, write_store  # noqa: E402

PASSWORD = "restore-unit-test-91ac3f"
SAMPLE = {"configs": [], "proxy_url": "", "schedule": {}, "bookmarks": [],
          "link_groups": [{"id": "g", "name": "组", "icon": "folder", "color": "mint",
                           "links": [{"id": "l1", "name": "x", "url": "https://x.example"}]}]}


class RestoreBase(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(RestoreBase, self).setUp()
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", app_module.ADMIN_PASSWORD)
        app_module.ADMIN_PASSWORD = PASSWORD
        self.c = app.test_client()
        self.c.environ_base["HTTP_X_ADMIN_PASSWORD"] = PASSWORD

    def corrupt(self):
        with open(app_module.CONFIG_FILE, "w", encoding="utf-8") as fh:
            fh.write('{"configs": [')

    def files(self):
        return sorted(os.listdir(str(self.data_dir)))


class BackupPathSafetyTest(RestoreBase):
    """备份标识直接决定读哪个文件、以及用什么覆盖 config.json —— 只能是白名单。"""

    def test_traversal_attempts_resolve_to_nothing(self):
        for bad in ("../../etc/passwd", "bak/../x", "/etc/passwd", "..", "./bak",
                    "config.json", "nope", "", None, "2026-9-1", "20260901"):
            self.assertIsNone(app_module._backup_path(bad), bad)

    def test_only_the_two_known_shapes_resolve(self):
        self.assertTrue(str(app_module._backup_path("bak")).endswith("config.json.bak"))
        self.assertTrue(str(app_module._backup_path("2026-09-15")).endswith("config.json.2026-09-15.bak"))

    def test_api_rejects_a_bad_identifier(self):
        for bad in ("../../etc/passwd", "nope", ""):
            resp = self.c.post("/api/configs/restore", json={"source": bad})
            self.assertEqual(resp.status_code, 400, bad)


class ListBackupsTest(RestoreBase):
    def test_empty_when_nothing_written_yet(self):
        self.assertEqual(list_config_backups(), [])

    def test_lists_both_kinds_after_writes(self):
        write_store(dict(SAMPLE, proxy_url="v0"))
        write_store(dict(SAMPLE, proxy_url="v1"))
        ids = [b["id"] for b in list_config_backups()]
        self.assertIn("bak", ids)
        self.assertIn(app_module._today_str(), ids)

    def test_summary_describes_contents_without_credentials(self):
        write_store(dict(SAMPLE, configs=[{"name": "n", "base_url": "https://b.example",
                                           "user_id": "1", "access_token": "must-not-appear",
                                           "enabled": True}]))
        write_store(dict(SAMPLE, proxy_url="again"))
        blob = json.dumps(list_config_backups(), ensure_ascii=False)
        self.assertNotIn("must-not-appear", blob)
        self.assertNotIn("b.example", blob)
        self.assertIn("网址", blob)

    def test_listing_works_while_config_is_corrupt(self):
        # 最关键的一条：坏掉的正是 config.json，列备份不能依赖它。
        write_store(dict(SAMPLE))
        write_store(dict(SAMPLE, proxy_url="v1"))
        self.corrupt()
        resp = self.c.get("/api/backups")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["backups"])

    def test_broken_backup_is_listed_as_unusable(self):
        write_store(dict(SAMPLE))
        with open(app_module.CONFIG_FILE + ".bak", "w", encoding="utf-8") as fh:
            fh.write("not json")
        entry = next(b for b in list_config_backups() if b["id"] == "bak")
        self.assertFalse(entry["valid"])
        self.assertIn("无法解析", entry["summary"])

    def test_requires_admin(self):
        self.assertEqual(app.test_client().get("/api/backups").status_code, 403)


class RestoreTest(RestoreBase):
    def test_restores_the_previous_content(self):
        write_store(dict(SAMPLE, proxy_url="good"))     # 首次写入，此时还没有 .bak
        write_store(dict(SAMPLE, proxy_url="newer"))    # .bak ← "good"
        self.corrupt()                                  # 直接写坏文件，不经过 write_store
        resp = self.c.post("/api/configs/restore", json={"source": "bak"})
        self.assertTrue(resp.get_json()["ok"], resp.get_json())
        # .bak 保存的是「最后一次正常写入之前」的内容，也就是 "good"。
        self.assertEqual(self.read_config()["proxy_url"], "good")
        self.assertEqual(self.c.get("/api/configs").get_json()["ok"], True)

    def test_the_overwritten_file_is_kept_for_forensics(self):
        write_store(dict(SAMPLE))
        write_store(dict(SAMPLE, proxy_url="x"))
        self.corrupt()
        self.c.post("/api/configs/restore", json={"source": "bak"})
        kept = [n for n in self.files() if ".corrupt-" in n]
        self.assertEqual(len(kept), 1, self.files())
        with open(os.path.join(str(self.data_dir), kept[0]), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), '{"configs": [')

    def test_restoring_a_broken_backup_is_refused(self):
        # 拿一份坏备份去覆盖，只会把一个问题变成两个。
        write_store(dict(SAMPLE, proxy_url="still-good"))
        with open(app_module.CONFIG_FILE + ".bak", "w", encoding="utf-8") as fh:
            fh.write("not json")
        resp = self.c.post("/api/configs/restore", json={"source": "bak"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("读不出来", resp.get_json()["error"])
        self.assertEqual(self.read_config()["proxy_url"], "still-good")   # 原文件没被动

    def test_missing_backup_is_404(self):
        write_store(dict(SAMPLE))
        resp = self.c.post("/api/configs/restore", json={"source": "2020-01-01"})
        self.assertEqual(resp.status_code, 404)

    def test_requires_admin(self):
        write_store(dict(SAMPLE))
        self.assertEqual(app.test_client().post("/api/configs/restore",
                                                json={"source": "bak"}).status_code, 403)

    def test_restore_from_a_daily_snapshot(self):
        write_store(dict(SAMPLE, proxy_url="day-start"))
        write_store(dict(SAMPLE, proxy_url="later"))
        resp = self.c.post("/api/configs/restore", json={"source": app_module._today_str()})
        self.assertTrue(resp.get_json()["ok"], resp.get_json())
        self.assertEqual(self.read_config()["proxy_url"], "day-start")

    def test_favicon_allowlist_is_recomputed_after_restore(self):
        # 图标缓存按 config.json 的 mtime 判定失效；恢复后不重算会继续用旧白名单。
        write_store(dict(SAMPLE))
        write_store(dict(SAMPLE, proxy_url="x"))
        app_module._favicon_ctx["stamp"] = 12345
        self.c.post("/api/configs/restore", json={"source": "bak"})
        self.assertIsNone(app_module._favicon_ctx["stamp"])


class DiagnosticsMentionsBackupsTest(RestoreBase):
    def test_broken_config_points_at_the_recovery_path(self):
        write_store(dict(SAMPLE))
        write_store(dict(SAMPLE, proxy_url="x"))
        self.corrupt()
        check = next(c for c in app_module.collect_diagnostics()["checks"] if c["label"] == "配置文件")
        self.assertEqual(check["status"], "error")
        self.assertIn("配置恢复", check["detail"])

    def test_says_so_when_there_is_nothing_to_restore_from(self):
        self.corrupt()
        check = next(c for c in app_module.collect_diagnostics()["checks"] if c["label"] == "配置文件")
        self.assertIn("没有找到可用备份", check["detail"])


class RestoreUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_panel_exists(self):
        self.assertIn('id="backupList"', self.html)
        self.assertIn('id="loadBackups"', self.html)
        self.assertIn("function loadBackups", self.html)

    def test_restore_asks_for_confirmation(self):
        # 覆盖全部配置不可逆，不能一点就走。
        start = self.html.index("$('backupList').addEventListener")
        self.assertIn("confirm(", self.html[start:start + 600])

    def test_panel_is_not_loaded_automatically(self):
        # 平时拉一串备份清单只是噪音；这个面板是「出事了才用」的。
        self.assertNotIn("if (name === 'settings') { loadDiagnostics(); loadBackups(); }", self.html)
        self.assertIn("刻意不自动加载", self.html)

    def test_failed_config_load_points_at_the_panel(self):
        self.assertIn("可在「系统设置 → 配置恢复」里从备份回退", self.html)
        start = self.html.index("可在「系统设置 → 配置恢复」里从备份回退")
        self.assertIn("loadBackups()", self.html[start:start + 200])

    def test_unusable_backups_offer_no_restore_button(self):
        self.assertIn("b.valid", self.html)
        self.assertIn("不可用", self.html)

    def test_styles_exist(self):
        for rule in (".backup-list", ".backup-row", ".backup-bad"):
            self.assertIn(rule, self.css)


if __name__ == "__main__":
    unittest.main()

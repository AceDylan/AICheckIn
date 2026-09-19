# -*- coding: utf-8 -*-
"""「从 WeTab 导入」是一次性迁移：做完后入口、接口和样式都已移除，这里防止它们悄悄回来。

迁移前留下的回溯点（config.json.pre-import.bak）仍要能在「配置恢复」里用——那是那次迁移唯一的撤回途径。
"""
import json
import re
import unittest
from pathlib import Path

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "wetab-removed-test-password"


class WetabImportRemovedTest(unittest.TestCase):
    def test_no_import_route_is_left(self):
        routes = sorted(rule.rule for rule in app.url_map.iter_rules() if "wetab" in rule.rule.lower())
        self.assertEqual(routes, [])
        self.assertEqual([r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/api/import")], [])

    def test_posting_a_backup_writes_nothing(self):
        client = app.test_client()
        resp = client.post("/api/import/wetab", json={"backup": {"data": {"store-icon": {"icons": []}}}})
        self.assertIn(resp.status_code, (404, 405))

    def test_page_has_no_entry_modal_or_script(self):
        html = app.test_client().get("/").get_data(as_text=True).lower()
        for gone in ("wetab", "/api/import/"):
            self.assertNotIn(gone, html)

    def test_no_leftover_code_or_styles(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for gone in ("parse_wetab_backup", "plan_wetab_import", "WETAB_", "import ipaddress"):
            self.assertNotIn(gone, source)
        self.assertNotIn("wetab", (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8").lower())

    def test_shell_cache_was_bumped_with_the_removal(self):
        sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
        self.assertGreaterEqual(int(re.search(r"bh-shell-v(\d+)", sw).group(1)), 11)


class PreImportRestorePointTest(StoreIsolationMixin, unittest.TestCase):
    """回溯点文件由迁移脚本 / 旧版本留下；本站只负责列出与恢复。"""

    def setUp(self):
        super(PreImportRestorePointTest, self).setUp()
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.before = {"configs": [], "bookmarks": [],
                       "link_groups": [{"id": "g1", "name": "常用", "links": [
                           {"id": "l1", "name": "Example", "url": "https://example.com/"}]}]}
        self.write_config(self.before)

    def snapshot_path(self):
        return self.data_dir / "config.json.pre-import.bak"

    def test_not_listed_when_the_file_is_absent(self):
        ids = [b["id"] for b in self.client.get("/api/backups").get_json()["backups"]]
        self.assertNotIn("pre-import", ids)
        self.assertEqual(self.client.post("/api/configs/restore", json={"source": "pre-import"}).status_code, 404)

    def test_listed_and_restorable_even_after_later_edits(self):
        self.snapshot_path().write_text(json.dumps(self.before, ensure_ascii=False), encoding="utf-8")
        # 迁移之后的日常改动会冲掉 .bak，但冲不掉这个回溯点。
        self.assertEqual(self.client.post("/api/link_groups", json={"name": "迁移进来的"}).status_code, 200)
        self.assertEqual(self.client.post("/api/link_groups", json={"name": "后来加的"}).status_code, 200)
        self.assertEqual(len(self.read_config()["link_groups"]), 3)

        listed = {b["id"]: b for b in self.client.get("/api/backups").get_json()["backups"]}
        self.assertTrue(listed["pre-import"]["valid"])
        self.assertIn("1 条", listed["pre-import"]["summary"])
        resp = self.client.post("/api/configs/restore", json={"source": "pre-import"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        groups = self.read_config()["link_groups"]
        self.assertEqual([g["name"] for g in groups], ["常用"])
        self.assertEqual([l["url"] for l in groups[0]["links"]], ["https://example.com/"])

    def test_restore_still_refuses_arbitrary_ids(self):
        self.snapshot_path().write_text(json.dumps(self.before), encoding="utf-8")
        for bad in ("pre-import.bak", "../config.json", "pre-import/../x", "PRE-IMPORT"):
            self.assertEqual(self.client.post("/api/configs/restore", json={"source": bad}).status_code, 400, bad)

    def test_restore_needs_unlock(self):
        self.snapshot_path().write_text(json.dumps(self.before), encoding="utf-8")
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", app_module.ADMIN_PASSWORD)
        app_module.ADMIN_PASSWORD = PASSWORD
        self.assertEqual(self.client.get("/api/backups").status_code, 403)
        self.assertEqual(self.client.post("/api/configs/restore", json={"source": "pre-import"}).status_code, 403)
        ok = self.client.post("/api/configs/restore", json={"source": "pre-import"}, headers={"X-Admin-Password": PASSWORD})
        self.assertEqual(ok.status_code, 200)


if __name__ == "__main__":
    unittest.main()

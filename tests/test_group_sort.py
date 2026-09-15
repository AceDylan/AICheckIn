# -*- coding: utf-8 -*-
"""收藏库分组自定义排序：顺序落盘到 config.json、重新读取后恢复、旧数据兼容，
以及前端拖拽 / 键盘排序的挂点。"""
import json
import re
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, read_store  # noqa: E402


class GroupOrderPersistenceTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def setUp(self):
        super(GroupOrderPersistenceTest, self).setUp()
        self.write_config({"configs": [], "bookmarks": [], "link_groups": [
            {"id": "a", "name": "甲", "icon": "folder", "color": "mint", "links": [
                {"id": "l1", "name": "x", "url": "https://x.example"}]},
            {"id": "b", "name": "乙", "icon": "globe", "color": "sky", "links": []},
            {"id": "c", "name": "丙", "icon": "code", "color": "amber", "links": []},
        ]})

    def _ids(self):
        return [g["id"] for g in self.client.get("/api/configs").get_json()["link_groups"]]

    def test_custom_order_is_written_to_disk_and_survives_reload(self):
        resp = self.client.post("/api/link_groups/reorder", json={"order": ["c", "a", "b"]})
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual([g["id"] for g in resp.get_json()["link_groups"]], ["c", "a", "b"])
        # 落盘：直接读文件，确认不是只改了内存。
        with open(app_module.CONFIG_FILE, encoding="utf-8") as fh:
            self.assertEqual([g["id"] for g in json.load(fh)["link_groups"]], ["c", "a", "b"])
        # 「刷新页面」：重新读 store / 重新请求 /api/configs 都应保持这个顺序。
        self.assertEqual([g["id"] for g in read_store()["link_groups"]], ["c", "a", "b"])
        self.assertEqual(self._ids(), ["c", "a", "b"])
        # 排序不动内容。
        links = self.client.get("/api/configs").get_json()["link_groups"][1]["links"]
        self.assertEqual([l["url"] for l in links], ["https://x.example"])

    def test_partial_or_unknown_order_is_rejected(self):
        for bad in (["a", "b"], ["a", "b", "zzz"], ["a", "b", "b"], "abc", None):
            self.assertEqual(self.client.post("/api/link_groups/reorder", json={"order": bad}).status_code, 400, bad)
        self.assertEqual(self._ids(), ["a", "b", "c"])

    def test_legacy_config_without_link_groups_still_sorts(self):
        # 旧数据没有 link_groups 键：读取时合成默认三组，排序后才第一次落盘。
        self.write_config({"configs": [], "bookmarks": []})
        ids = self._ids()
        self.assertEqual(ids, ["self-hosted", "daily", "ai"])
        self.assertTrue(self.client.post("/api/link_groups/reorder", json={"order": ids[::-1]}).get_json()["ok"])
        self.assertEqual(self._ids(), ids[::-1])
        with open(app_module.CONFIG_FILE, encoding="utf-8") as fh:
            self.assertEqual([g["id"] for g in json.load(fh)["link_groups"]], ids[::-1])

    def test_reorder_requires_admin_when_password_is_set(self):
        original = app_module.ADMIN_PASSWORD
        app_module.ADMIN_PASSWORD = "secret"
        try:
            self.assertEqual(self.client.post("/api/link_groups/reorder", json={"order": ["c", "b", "a"]}).status_code, 403)
        finally:
            app_module.ADMIN_PASSWORD = original
        self.assertEqual(self._ids(), ["a", "b", "c"])


class GroupSortUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_only_groups_are_marked_sortable(self):
        # 「站点看板」固定首位，不带 data-sortable；分组项都带。
        self.assertIn("p.id === 'monitor' ? '' : ` data-sortable=\"1\"", self.html)
        self.assertIn("querySelectorAll('[data-sortable]')", self.html)

    def test_drag_handlers_are_bound_to_both_navs(self):
        self.assertIn("[$('libSubnav'), $('libChips')].forEach(container => {", self.html)
        for event in ("pointerdown", "pointermove", "pointerup", "pointercancel", "keydown"):
            self.assertIn("container.addEventListener('%s'" % event, self.html)
        # 触摸指针交给滚动 / 点击，不参与拖拽。
        self.assertIn("if (e.pointerType === 'touch' || e.button !== 0 || !canEdit()) return;", self.html)
        # 小幅抖动仍算点击，拖完那一次 click 不应切页。
        self.assertIn("if (Math.abs(pos - SORT.start) < 6) return;", self.html)
        self.assertIn("if (Date.now() < SORT.blockClickUntil) return;", self.html)

    def test_keyboard_reordering_is_available(self):
        self.assertIn("if (!e.altKey || e.ctrlKey || e.metaKey) return;", self.html)
        self.assertIn("moveGroup(el.dataset.lib, dir);", self.html)

    def test_order_is_persisted_through_the_existing_endpoint(self):
        self.assertIn("linkApi('/api/link_groups/reorder', 'POST', { order: ids })", self.html)
        self.assertEqual(self.html.count("'/api/link_groups/reorder'"), 1, "写回路径应只有 applyGroupOrder 一处")
        # 弹窗里的上移 / 下移（触摸端入口）也复用同一条写回路径。
        self.assertIn("if (!await applyGroupOrder(ids)) return;", self.html)

    def test_drag_feedback_styles_exist(self):
        self.assertIn(".subnav-item.is-dragging, .chip.is-dragging", self.css)
        self.assertIn(".subnav.is-sorting, .chip-row.is-sorting", self.css)
        self.assertIsNotNone(re.search(r"\.subnav-item\.is-dragging[^{]*\{[^}]*opacity", self.css))


if __name__ == "__main__":
    unittest.main()

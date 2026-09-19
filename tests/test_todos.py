# -*- coding: utf-8 -*-
"""首页待办：独立存储（todos.json）、增删改 / 完成状态 / 排序、管理密码保护、随配置导出导入。"""
import json
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, _coerce_todos, read_todos  # noqa: E402

PASSWORD = "todo-test-password-1234"


class TodoCase(StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super(TodoCase, self).setUp()
        self.client = app.test_client()

    def todos_file(self):
        return self.data_dir / "todos.json"

    def add(self, text):
        resp = self.client.post("/api/todos", json={"text": text})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return resp.get_json()["todos"]


class TodoCrudTest(TodoCase):
    def test_starts_empty_without_creating_a_file(self):
        self.assertEqual(self.client.get("/api/todos").get_json(), {"ok": True, "todos": []})
        self.assertFalse(self.todos_file().exists())

    def test_create_puts_newest_first_and_persists(self):
        self.add("买牛奶")
        todos = self.add("  续费   域名  ")
        self.assertEqual([t["text"] for t in todos], ["续费 域名", "买牛奶"])
        self.assertTrue(all(t["done"] is False and t["done_at"] == "" for t in todos))
        self.assertRegex(todos[0]["created_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertNotEqual(todos[0]["id"], todos[1]["id"])
        # 落盘在独立文件里，读回来一致——刷新页面 / 重启服务后还在。
        on_disk = json.loads(self.todos_file().read_text(encoding="utf-8"))
        self.assertEqual([t["text"] for t in on_disk["todos"]], ["续费 域名", "买牛奶"])
        self.assertEqual(read_todos(), todos)

    def test_create_rejects_empty_overlong_and_non_text(self):
        for bad, needle in (("   ", "不能为空"), ("x" * 201, "过长"), (123, "文字"), (None, "文字")):
            resp = self.client.post("/api/todos", json={"text": bad})
            self.assertEqual(resp.status_code, 400, bad)
            self.assertIn(needle, resp.get_json()["error"])
        self.assertEqual(self.client.post("/api/todos", data="not json").status_code, 400)
        self.assertFalse(self.todos_file().exists())

    def test_toggle_done_stamps_and_clears_done_at(self):
        tid = self.add("写周报")[0]["id"]
        done = self.client.put("/api/todos/" + tid, json={"done": True}).get_json()["todos"][0]
        self.assertTrue(done["done"])
        self.assertRegex(done["done_at"], r"^\d{4}-")
        undone = self.client.put("/api/todos/" + tid, json={"done": False}).get_json()["todos"][0]
        self.assertFalse(undone["done"])
        self.assertEqual(undone["done_at"], "")

    def test_edit_text_keeps_id_and_done_state(self):
        tid = self.add("写周抱")[0]["id"]
        self.client.put("/api/todos/" + tid, json={"done": True})
        item = self.client.put("/api/todos/" + tid, json={"text": "写周报"}).get_json()["todos"][0]
        self.assertEqual((item["id"], item["text"], item["done"]), (tid, "写周报", True))

    def test_update_validation(self):
        tid = self.add("a")[0]["id"]
        self.assertEqual(self.client.put("/api/todos/" + tid, json={}).status_code, 400)
        self.assertEqual(self.client.put("/api/todos/" + tid, json={"done": "yes"}).status_code, 400)
        self.assertEqual(self.client.put("/api/todos/" + tid, json={"text": ""}).status_code, 400)
        self.assertEqual(self.client.put("/api/todos/nope", json={"done": True}).status_code, 404)
        self.assertEqual(read_todos()[0]["text"], "a")

    def test_delete_and_clear_done(self):
        a = self.add("a")[0]["id"]
        b = self.add("b")[0]["id"]
        self.add("c")
        self.client.put("/api/todos/" + a, json={"done": True})
        self.client.put("/api/todos/" + b, json={"done": True})
        left = self.client.delete("/api/todos/" + a).get_json()["todos"]
        self.assertEqual([t["text"] for t in left], ["c", "b"])
        self.assertEqual(self.client.delete("/api/todos/" + a).status_code, 404)
        left = self.client.post("/api/todos/clear_done").get_json()["todos"]
        self.assertEqual([t["text"] for t in left], ["c"])

    def test_reorder_needs_an_exact_permutation(self):
        ids = [t["id"] for t in (self.add("a"), self.add("b"), self.add("c"))[-1]]
        flipped = list(reversed(ids))
        resp = self.client.post("/api/todos/reorder", json={"order": flipped})
        self.assertEqual([t["id"] for t in resp.get_json()["todos"]], flipped)
        for bad in (ids[:2], ids + ["x"], "abc", None):
            self.assertEqual(self.client.post("/api/todos/reorder", json={"order": bad}).status_code, 400, bad)
        self.assertEqual([t["id"] for t in read_todos()], flipped)

    def test_list_is_capped(self):
        self.addCleanup(setattr, app_module, "MAX_TODOS", app_module.MAX_TODOS)
        app_module.MAX_TODOS = 2
        self.add("a")
        self.add("b")
        resp = self.client.post("/api/todos", json={"text": "c"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("最多", resp.get_json()["error"])


class TodoStorageTest(TodoCase):
    def test_todos_never_touch_config_json(self):
        """勾一下待办不该重写装着凭据的 config.json，更不该把它的 .bak 冲掉。"""
        self.write_config({"configs": [], "link_groups": []})
        before = (self.data_dir / "config.json").read_bytes()
        tid = self.add("a")[0]["id"]
        self.client.put("/api/todos/" + tid, json={"done": True})
        self.assertEqual((self.data_dir / "config.json").read_bytes(), before)
        self.assertFalse((self.data_dir / "config.json.bak").exists())
        self.assertNotIn("todos", self.read_config())

    def test_previous_version_is_kept_as_bak(self):
        self.add("a")
        self.add("b")
        bak = json.loads((self.data_dir / "todos.json.bak").read_text(encoding="utf-8"))
        self.assertEqual([t["text"] for t in bak["todos"]], ["a"])

    def test_corrupt_file_is_reported_not_silently_overwritten(self):
        self.todos_file().write_text("{broken", encoding="utf-8")
        self.assertEqual(self.client.get("/api/todos").status_code, 500)
        self.assertEqual(self.client.post("/api/todos", json={"text": "x"}).status_code, 500)
        self.assertEqual(self.todos_file().read_text(encoding="utf-8"), "{broken")
        # 页面初始数据不能因此整个挂掉：收藏照常下发，待办带上出错原因。
        listing = self.client.get("/api/configs").get_json()
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["todos"], [])
        self.assertIn("读取待办失败", listing["todos_error"])

    def test_lenient_read_repairs_bad_entries(self):
        items = _coerce_todos([
            "junk", {"text": "  "}, {"id": "same", "text": "a", "done": 1, "done_at": "garbage"},
            {"id": "same", "text": "b", "created_at": "2026-01-02 03:04:05"}, {"id": "bad id!", "text": "c" * 300},
        ])
        self.assertEqual([t["text"] for t in items], ["a", "b", "c" * 200])
        self.assertEqual(len({t["id"] for t in items}), 3)
        self.assertEqual((items[0]["done"], items[0]["done_at"]), (True, ""))
        self.assertEqual(items[1]["updated_at"], "2026-01-02 03:04:05")
        self.assertEqual(_coerce_todos({"not": "a list"}), [])


class TodoAccessTest(TodoCase):
    def setUp(self):
        super(TodoAccessTest, self).setUp()
        self.add("私事")
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", app_module.ADMIN_PASSWORD)
        app_module.ADMIN_PASSWORD = PASSWORD

    def test_every_todo_endpoint_needs_unlock(self):
        tid = read_todos()[0]["id"]
        calls = (("get", "/api/todos", None), ("post", "/api/todos", {"text": "x"}),
                 ("put", "/api/todos/" + tid, {"done": True}), ("delete", "/api/todos/" + tid, None),
                 ("post", "/api/todos/clear_done", None), ("post", "/api/todos/reorder", {"order": [tid]}))
        for method, url, body in calls:
            resp = getattr(self.client, method)(url, json=body)
            self.assertEqual(resp.status_code, 403, url)
            self.assertNotIn("私事", resp.get_data(as_text=True))
        self.assertEqual([t["text"] for t in read_todos()], ["私事"])

    def test_open_listing_withholds_todos_until_unlocked(self):
        listing = self.client.get("/api/configs").get_json()
        self.assertEqual((listing["todos"], listing["todos_locked"]), ([], True))
        self.assertNotIn("私事", json.dumps(listing, ensure_ascii=False))
        unlocked = self.client.get("/api/configs", headers={"X-Admin-Password": PASSWORD}).get_json()
        self.assertEqual([t["text"] for t in unlocked["todos"]], ["私事"])
        self.assertFalse(unlocked["todos_locked"])

    def test_private_mode_shell_has_the_same_shape(self):
        self.addCleanup(setattr, app_module, "PRIVATE_MODE", app_module.PRIVATE_MODE)
        app_module.PRIVATE_MODE = True
        shell = self.client.get("/api/configs").get_json()
        self.assertEqual((shell["todos"], shell["todos_locked"]), ([], True))
        self.assertEqual(self.client.get("/api/todos").status_code, 403)


class TodoBackupTest(TodoCase):
    def test_export_carries_todos_and_import_restores_them(self):
        self.write_config({"configs": [], "link_groups": []})
        self.add("a")
        tid = self.add("b")[0]["id"]
        self.client.put("/api/todos/" + tid, json={"done": True})
        exported = self.client.get("/api/configs/export").get_json()
        self.assertEqual([(t["text"], t["done"]) for t in exported["todos"]], [("b", True), ("a", False)])

        self.client.post("/api/todos/clear_done")
        self.client.post("/api/todos", json={"text": "c"})
        self.assertEqual(self.client.post("/api/configs/import", json=exported).status_code, 200)
        self.assertEqual([(t["text"], t["done"]) for t in read_todos()], [("b", True), ("a", False)])
        self.assertNotIn("todos", self.read_config())

    def test_import_without_todos_key_leaves_them_alone(self):
        """旧版导出的文件没有 todos 键：导入它不应把现有待办清空。"""
        self.add("留着")
        self.assertEqual(self.client.post("/api/configs/import", json={"configs": []}).status_code, 200)
        self.assertEqual([t["text"] for t in read_todos()], ["留着"])


if __name__ == "__main__":
    unittest.main()

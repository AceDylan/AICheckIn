# -*- coding: utf-8 -*-
"""从 WeTab 备份导入：白名单取字段、统一进「WeTab」分组、预览、幂等、回溯点与管理密码保护。

夹具是按 WeTab 备份的真实结构手写的假数据——真实备份是用户的私人数据，不进仓库。
"""
import copy
import json
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, parse_wetab_backup, read_todos  # noqa: E402

PASSWORD = "wetab-test-password-1234"
SECRET_MARK = "SHOULD-NEVER-BE-STORED"


def site(name, target, **extra):
    node = {"id": "x", "type": "site", "name": name, "target": target, "bgType": "image",
            "bgImage": "https://cdn.wetab.example/icon.png?" + SECRET_MARK, "bgText": "", "origin": "online"}
    node.update(extra)
    return node


def backup():
    return {
        "version": "2.0", "branch": "release", "timestamp": 1790000000000, "platform": "chrome",
        "data": {
            "store-icon": {"icons": [
                {"id": "p1", "name": "主页", "iconClass": "icon-zhuye", "children": [
                    {"id": "w1", "type": "widget", "name": "天气", "widgetName": "weather", "widgetData": {"k": SECRET_MARK}},
                    {"id": "f1", "type": "folder-icon", "name": "开发 常用", "children": [
                        site("GitHub", "https://github.com/"),
                        site("路由器", "http://192.168.1.1/", bgType="color", bgText="路由", bgColor="#123456"),
                    ]},
                    site("", "https://www.example.org/path?tab=a"),
                ]},
                {"id": "p2", "name": "工具", "iconClass": "icon-gongju", "children": [
                    site("GitHub 重复", "http://www.github.com"),
                    site("带密码", "https://user:" + SECRET_MARK + "@intranet.example/"),
                    site("扩展页", "chrome://extensions"),
                    site("没网址", ""),
                    site("长名字" * 30, "https://long.example/"),
                    "junk",
                ]},
            ], "dockIdList": ["x"]},
            "store-todo": {"todos": [{"id": "l1", "name": "我的待办", "children": [
                {"id": "t1", "content": "  续费  域名 ", "finished": False, "updateTime": 1767225600000},
                {"id": "t2", "content": "备份照片", "finished": True, "updateTime": 1767225600000},
                {"id": "t3", "content": "续费 域名", "finished": True, "updateTime": "bad"},
                {"id": "t4", "content": "   ", "finished": False, "updateTime": 1},
            ]}]},
            "store-note": {"notes": [{"title": "私密便签", "content": SECRET_MARK}]},
            "store-weather": {"addedCity": [{"name": SECRET_MARK, "lat": "1", "lon": "2"}]},
            "store-chatgpt": {"theme": SECRET_MARK},
        },
    }


class ParseTest(unittest.TestCase):
    def test_sites_are_collected_across_pages_and_folders(self):
        parsed = parse_wetab_backup(backup())
        self.assertEqual([l["url"] for l in parsed["links"]], [
            "https://github.com/", "http://192.168.1.1/", "https://www.example.org/path?tab=a", "https://long.example/"])
        self.assertEqual([p["count"] for p in parsed["pages"]], [3, 1])
        self.assertEqual(parsed["skipped"], {"widgets": 1, "invalid": 2, "credentials": 1, "duplicates": 1})

    def test_page_and_folder_names_become_tags(self):
        links = {l["url"]: l for l in parse_wetab_backup(backup())["links"]}
        self.assertEqual(links["https://github.com/"]["tags"], ["主页", "开发-常用"])
        self.assertEqual(links["https://www.example.org/path?tab=a"]["tags"], ["主页"])
        self.assertEqual(links["https://long.example/"]["tags"], ["工具"])

    def test_field_mapping(self):
        links = {l["url"]: l for l in parse_wetab_backup(backup())["links"]}
        router = links["http://192.168.1.1/"]
        self.assertEqual((router["icon"], router["skip_check"]), ("路由", True))   # 文字图标带过来；内网地址不参与死链检查
        self.assertEqual((links["https://github.com/"]["icon"], links["https://github.com/"]["skip_check"]), ("", False))
        self.assertEqual(links["https://www.example.org/path?tab=a"]["name"], "example.org")  # 空名称回退到域名
        self.assertEqual(len(links["https://long.example/"]["name"]), 60)
        self.assertEqual(set(router), {"name", "url", "tags", "icon", "skip_check"})

    def test_todos_are_squashed_deduped_and_stamped(self):
        todos = parse_wetab_backup(backup())["todos"]
        self.assertEqual([(t["text"], t["done"]) for t in todos], [("续费 域名", False), ("备份照片", True)])
        self.assertRegex(todos[0]["stamp"], r"^202[56]-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_nothing_outside_the_whitelist_survives(self):
        dumped = json.dumps(parse_wetab_backup(backup()), ensure_ascii=False)
        for leaked in (SECRET_MARK, "cdn.wetab.example", "私密便签", "bgImage", "widgetData"):
            self.assertNotIn(leaked, dumped)

    def test_unrecognised_files_are_rejected(self):
        for bad in (None, [], {}, {"data": []}, {"data": {}}, {"data": {"store-icon": {"icons": "x"}}}):
            with self.assertRaises(ValueError):
                parse_wetab_backup(bad)

    def test_only_one_of_the_two_stores_is_enough(self):
        only_todos = {"data": {"store-todo": backup()["data"]["store-todo"]}}
        self.assertEqual((len(parse_wetab_backup(only_todos)["links"]), len(parse_wetab_backup(only_todos)["todos"])), (0, 2))

    def test_hostile_nesting_is_bounded(self):
        node = site("deep", "https://deep.example/")
        for _ in range(50):
            node = {"type": "folder-icon", "name": "f", "children": [node]}
        parsed = parse_wetab_backup({"data": {"store-icon": {"icons": [{"name": "p", "children": [node]}]}}})
        self.assertEqual(parsed["links"], [])
        wide = {"data": {"store-icon": {"icons": [{"name": "p", "children": [
            site("s%d" % i, "https://s%d.example/" % i) for i in range(app_module.WETAB_MAX_NODES + 50)]}]}}}
        self.assertEqual(len(parse_wetab_backup(wide)["links"]), app_module.WETAB_MAX_NODES)


class ImportCase(StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super(ImportCase, self).setUp()
        self.client = app.test_client()
        self.write_config({"configs": [], "link_groups": [
            {"id": "daily", "name": "常用网站", "icon": "globe", "color": "mint",
             "links": [{"id": "gh", "name": "GitHub", "url": "https://github.com"}]}]})

    def run_import(self, **options):
        body = dict({"backup": backup()}, **options)
        return self.client.post("/api/import/wetab", json=body)

    def wetab_group(self):
        return next((g for g in self.read_config()["link_groups"] if g["name"] == "WeTab"), None)


class ImportTest(ImportCase):
    def test_dry_run_reports_without_writing(self):
        before = (self.data_dir / "config.json").read_bytes()
        body = self.run_import(dry_run=True).get_json()
        self.assertTrue(body["ok"] and body["dry_run"])
        self.assertEqual(body["links"], {"found": 4, "new": 4, "already": 0, "elsewhere": 1, "skip_existing": False})
        self.assertEqual(body["todos"], {"found": 2, "new": 2, "already": 0, "done": 1})
        self.assertEqual(body["group"], {"name": "WeTab", "exists": False})
        self.assertEqual(body["sample"][0], {"name": "GitHub", "host": "github.com"})
        self.assertNotIn("link_groups", body)
        self.assertEqual((self.data_dir / "config.json").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.data_dir.iterdir()), ["config.json"])

    def test_everything_lands_in_one_wetab_group(self):
        body = self.run_import().get_json()
        self.assertTrue(body["ok"])
        groups = self.read_config()["link_groups"]
        self.assertEqual([g["name"] for g in groups], ["常用网站", "WeTab"])
        self.assertEqual(len(groups[0]["links"]), 1)                       # 原有分组原样不动
        group = self.wetab_group()
        self.assertEqual((group["icon"], group["color"], body["group"]["id"]), ("cloud", "sky", group["id"]))
        self.assertEqual([l["name"] for l in group["links"]][:3], ["GitHub", "路由器", "example.org"])
        self.assertTrue(all(l["show_on_home"] is False and l["pinned"] is False for l in group["links"]))
        self.assertEqual(len({l["id"] for l in group["links"]}), 4)
        self.assertEqual(body["link_groups"], groups)
        self.assertEqual([(t["text"], t["done"], bool(t["done_at"])) for t in read_todos()],
                         [("续费 域名", False, False), ("备份照片", True, True)])
        self.assertEqual(body["todo_items"], read_todos())

    def test_stored_data_carries_nothing_outside_the_whitelist(self):
        self.run_import()
        stored = (self.data_dir / "config.json").read_text(encoding="utf-8") + (self.data_dir / "todos.json").read_text(encoding="utf-8")
        for leaked in (SECRET_MARK, "cdn.wetab.example", "私密便签", "intranet.example"):
            self.assertNotIn(leaked, stored)

    def test_reimporting_the_same_backup_adds_nothing(self):
        self.run_import()
        snapshot = (self.data_dir / "config.json").read_bytes()
        body = self.run_import().get_json()
        self.assertEqual((body["links"]["new"], body["links"]["already"]), (0, 4))
        self.assertEqual((body["todos"]["new"], body["todos"]["already"]), (0, 2))
        self.assertEqual((self.data_dir / "config.json").read_bytes(), snapshot)
        self.assertEqual(len(read_todos()), 2)

    def test_existing_wetab_group_is_reused_case_insensitively(self):
        store = self.read_config()
        store["link_groups"].append({"id": "mine", "name": "wetab", "icon": "star", "color": "rose",
                                     "links": [{"id": "r", "name": "路由", "url": "http://192.168.1.1"}]})
        self.write_config(store)
        body = self.run_import().get_json()
        self.assertEqual((body["group"]["exists"], body["links"]["new"], body["links"]["already"]), (True, 3, 1))
        groups = self.read_config()["link_groups"]
        self.assertEqual([g["name"] for g in groups], ["常用网站", "wetab"])
        self.assertEqual((groups[1]["icon"], len(groups[1]["links"])), ("star", 4))

    def test_skip_existing_leaves_out_urls_already_in_other_groups(self):
        body = self.run_import(skip_existing=True).get_json()
        self.assertEqual((body["links"]["new"], body["links"]["elsewhere"]), (3, 1))
        self.assertNotIn("https://github.com/", [l["url"] for l in self.wetab_group()["links"]])

    def test_links_or_todos_can_be_left_out(self):
        self.run_import(include_todos=False)
        self.assertEqual(read_todos(), [])
        self.assertEqual(len(self.wetab_group()["links"]), 4)
        body = self.run_import(include_links=False).get_json()
        self.assertEqual((body["links"]["found"], body["todos"]["new"]), (0, 2))
        self.assertEqual(len(read_todos()), 2)

    def test_capacity_is_checked_before_anything_is_written(self):
        before = (self.data_dir / "config.json").read_bytes()
        self.addCleanup(setattr, app_module, "MAX_LINKS_PER_GROUP", app_module.MAX_LINKS_PER_GROUP)
        app_module.MAX_LINKS_PER_GROUP = 3
        resp = self.run_import()
        self.assertEqual(resp.status_code, 400)
        self.assertIn("放不下", resp.get_json()["error"])
        app_module.MAX_LINKS_PER_GROUP = 300
        self.addCleanup(setattr, app_module, "MAX_TODOS", app_module.MAX_TODOS)
        app_module.MAX_TODOS = 1
        self.assertIn("待办放不下", self.run_import().get_json()["error"])
        self.assertEqual((self.data_dir / "config.json").read_bytes(), before)
        self.assertFalse((self.data_dir / "todos.json").exists())

    def test_bad_requests(self):
        for body in (None, [], {}, {"backup": "text"}, {"backup": {"data": {}}}):
            resp = self.client.post("/api/import/wetab", json=body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertFalse(resp.get_json()["ok"])


class RollbackTest(ImportCase):
    def test_pre_import_snapshot_survives_later_edits_and_can_be_restored(self):
        self.client.post("/api/todos", json={"text": "导入前就有的"})
        original = self.read_config()
        self.run_import()
        # 之后的日常改动会冲掉 .bak，但冲不掉导入前的回溯点。
        self.client.post("/api/link_groups", json={"name": "后来加的"})
        self.client.post("/api/todos", json={"text": "后来加的"})
        snap = json.loads((self.data_dir / "config.json.pre-import.bak").read_text(encoding="utf-8"))
        self.assertEqual(snap, original)
        todo_snap = json.loads((self.data_dir / "todos.json.pre-import.bak").read_text(encoding="utf-8"))
        self.assertEqual([t["text"] for t in todo_snap["todos"]], ["导入前就有的"])

        listed = {b["id"]: b for b in self.client.get("/api/backups").get_json()["backups"]}
        self.assertIn("pre-import", listed)
        self.assertTrue(listed["pre-import"]["valid"])
        self.assertIn("批量导入前", listed["pre-import"]["label"])
        resp = self.client.post("/api/configs/restore", json={"source": "pre-import"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(self.read_config(), original)
        self.assertIsNone(self.wetab_group())

    def test_no_snapshot_is_listed_before_any_import(self):
        ids = [b["id"] for b in self.client.get("/api/backups").get_json()["backups"]]
        self.assertNotIn("pre-import", ids)
        self.assertEqual(self.client.post("/api/configs/restore", json={"source": "pre-import"}).status_code, 404)

    def test_restore_still_refuses_arbitrary_ids(self):
        for bad in ("pre-import.bak", "../config.json", "pre-import/../x"):
            self.assertEqual(self.client.post("/api/configs/restore", json={"source": bad}).status_code, 400, bad)


class AccessTest(ImportCase):
    def setUp(self):
        super(AccessTest, self).setUp()
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", app_module.ADMIN_PASSWORD)
        app_module.ADMIN_PASSWORD = PASSWORD

    def test_import_and_preview_need_unlock(self):
        before = copy.deepcopy(self.read_config())
        for options in ({}, {"dry_run": True}):
            resp = self.run_import(**options)
            self.assertEqual(resp.status_code, 403)
            self.assertNotIn("GitHub", resp.get_data(as_text=True))
        self.assertEqual(self.read_config(), before)
        ok = self.client.post("/api/import/wetab", json={"backup": backup()}, headers={"X-Admin-Password": PASSWORD})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(len(self.wetab_group()["links"]), 4)


if __name__ == "__main__":
    unittest.main()

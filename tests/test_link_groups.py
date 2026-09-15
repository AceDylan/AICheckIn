# -*- coding: utf-8 -*-
"""收藏库子页面（链接分组）：默认分组合成、CRUD、排序、移动、批量新增、导入导出与管理密码保护。"""
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, clean_link, clean_link_group, normalize_link_groups  # noqa: E402


class NormalizeTest(unittest.TestCase):
    def test_missing_key_yields_three_default_groups(self):
        groups = normalize_link_groups(None)
        self.assertEqual([g["id"] for g in groups], ["self-hosted", "daily", "ai"])
        self.assertEqual([g["name"] for g in groups], ["自建服务", "常用网站", "AI 服务"])
        self.assertTrue(all(g["links"] == [] for g in groups))

    def test_empty_list_stays_empty(self):
        self.assertEqual(normalize_link_groups([]), [])

    def test_lenient_read_fixes_bad_entries(self):
        groups = normalize_link_groups([
            "junk",
            {"name": "  A   B ", "icon": "nope", "color": "nope", "links": [
                {"url": "ftp://bad"}, {"url": "https://ok.example", "tags": ["x", 1, " y "]}, "junk",
            ]},
            {"id": "dup"}, {"id": "dup"},
        ])
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0]["name"], "A B")
        self.assertEqual(groups[0]["icon"], "folder")
        self.assertEqual(groups[0]["color"], "mint")
        self.assertEqual(len(groups[0]["links"]), 1)
        self.assertEqual(groups[0]["links"][0]["name"], "ok.example")
        self.assertEqual(groups[0]["links"][0]["tags"], ["x", "y"])
        self.assertEqual(groups[1]["name"], "未命名分组")
        self.assertNotEqual(groups[1]["id"], groups[2]["id"])

    def test_clean_link_validation(self):
        with self.assertRaisesRegex(ValueError, "网址必填"):
            clean_link({"name": "x"})
        with self.assertRaisesRegex(ValueError, "http"):
            clean_link({"url": "javascript:alert(1)"})
        with self.assertRaisesRegex(ValueError, "标签最多"):
            clean_link({"url": "https://a.example", "tags": [str(i) for i in range(9)]})
        link = clean_link({"url": "https://www.Example.com/path", "tags": "a, b，a"})
        self.assertEqual(link["name"], "example.com")
        self.assertEqual(link["tags"], ["a", "b"])
        self.assertFalse(link["pinned"])
        self.assertRegex(link["created_at"], r"^\d{4}-\d{2}-\d{2} ")

    def test_clean_link_group_validation(self):
        with self.assertRaisesRegex(ValueError, "分组名称必填"):
            clean_link_group({"name": " "})
        with self.assertRaisesRegex(ValueError, "图标"):
            clean_link_group({"name": "x", "icon": "bogus"})
        g = clean_link_group({"name": "工具", "color": "amber", "links": [{"url": "https://t.example"}]}, with_links=True)
        self.assertEqual(g["icon"], "folder")
        self.assertEqual(len(g["links"]), 1)


class LinkGroupApiTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def setUp(self):
        super(LinkGroupApiTest, self).setUp()
        # 旧数据：没有 link_groups 键，且已有一条收藏与一组签到配置，验证两者不受影响。
        self.write_config({
            "configs": [{"name": "svc", "base_url": "https://svc.example", "user_id": "1",
                         "access_token": "tok", "enabled": True, "turnstile": ""}],
            "proxy_url": "", "schedule": {"enabled": False},
            "bookmarks": [{"name": "site", "url": "https://site.example", "fields": []}],
        })

    def _groups(self):
        data = self.client.get("/api/configs").get_json()
        self.assertTrue(data["ok"])
        return data["link_groups"]

    def test_defaults_are_exposed_but_not_persisted_until_write(self):
        groups = self._groups()
        self.assertEqual([g["id"] for g in groups], ["self-hosted", "daily", "ai"])
        self.assertNotIn("link_groups", self.read_config())

    def test_link_crud_and_persistence(self):
        c = self.client
        resp = c.post("/api/link_groups/daily/links", json={"name": "Docs", "url": "https://docs.example", "desc": "文档", "tags": ["doc"]})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        link = data["links"][0]
        self.assertEqual(data["group_id"], "daily")
        self.assertEqual(link["name"], "Docs")
        saved = self.read_config()
        self.assertEqual(saved["link_groups"][1]["links"][0]["url"], "https://docs.example")
        # 原有收藏与签到配置原样保留。
        self.assertEqual(saved["bookmarks"][0]["name"], "site")
        self.assertEqual(saved["configs"][0]["name"], "svc")

        resp = c.put("/api/link_groups/daily/links/%s" % link["id"], json={"name": "Docs v2", "pinned": True})
        self.assertTrue(resp.get_json()["ok"])
        updated = resp.get_json()["link"]
        self.assertEqual(updated["name"], "Docs v2")
        self.assertTrue(updated["pinned"])
        self.assertEqual(updated["url"], "https://docs.example")
        self.assertEqual(updated["created_at"], link["created_at"])

        resp = c.put("/api/link_groups/daily/links/%s" % link["id"], json={"url": "notaurl"})
        self.assertEqual(resp.status_code, 400)

        resp = c.delete("/api/link_groups/daily/links/%s" % link["id"])
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual(self.read_config()["link_groups"][1]["links"], [])

        self.assertEqual(c.delete("/api/link_groups/daily/links/missing").status_code, 404)
        self.assertEqual(c.post("/api/link_groups/nope/links", json={"url": "https://x.example"}).status_code, 404)

    def test_bulk_create_is_atomic(self):
        c = self.client
        resp = c.post("/api/link_groups/ai/links", json={"links": [
            {"url": "https://one.example"}, {"url": "https://two.example", "name": "Two"},
        ]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([l["name"] for l in resp.get_json()["links"]], ["one.example", "Two"])
        self.assertEqual(len(self.read_config()["link_groups"][2]["links"]), 2)

        resp = c.post("/api/link_groups/ai/links", json={"links": [{"url": "https://three.example"}, {"url": "bad"}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("第 2 条", resp.get_json()["error"])
        self.assertEqual(len(self.read_config()["link_groups"][2]["links"]), 2)

        self.assertEqual(c.post("/api/link_groups/ai/links", json={"links": []}).status_code, 400)

    def test_reorder_and_move(self):
        c = self.client
        ids = [c.post("/api/link_groups/daily/links", json={"url": "https://%s.example" % n}).get_json()["links"][0]["id"]
               for n in ("a", "b", "c")]
        resp = c.post("/api/link_groups/daily/links/reorder", json={"order": [ids[2], ids[0], ids[1]]})
        self.assertTrue(resp.get_json()["ok"])
        daily = next(g for g in self._groups() if g["id"] == "daily")
        self.assertEqual([l["id"] for l in daily["links"]], [ids[2], ids[0], ids[1]])
        self.assertEqual(c.post("/api/link_groups/daily/links/reorder", json={"order": ids[:2]}).status_code, 400)

        resp = c.put("/api/link_groups/daily/links/%s" % ids[0], json={"group": "ai"})
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual(resp.get_json()["group_id"], "ai")
        groups = {g["id"]: g for g in self._groups()}
        self.assertEqual([l["id"] for l in groups["daily"]["links"]], [ids[2], ids[1]])
        self.assertEqual([l["id"] for l in groups["ai"]["links"]], [ids[0]])
        self.assertEqual(c.put("/api/link_groups/daily/links/%s" % ids[1], json={"group": "nope"}).status_code, 404)

    def test_group_crud_and_reorder(self):
        c = self.client
        resp = c.post("/api/link_groups", json={"name": "开发工具", "icon": "code", "color": "amber", "desc": "d"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        gid = resp.get_json()["group"]["id"]
        self.assertEqual([g["id"] for g in resp.get_json()["link_groups"]][-1], gid)

        resp = c.put("/api/link_groups/%s" % gid, json={"name": "工具箱", "color": "rose"})
        self.assertEqual(resp.get_json()["group"]["name"], "工具箱")
        self.assertEqual(resp.get_json()["group"]["icon"], "code")
        self.assertEqual(resp.get_json()["group"]["desc"], "d")
        self.assertEqual(c.put("/api/link_groups/%s" % gid, json={"name": ""}).status_code, 400)
        self.assertEqual(c.put("/api/link_groups/missing", json={"name": "x"}).status_code, 404)

        resp = c.post("/api/link_groups/reorder", json={"order": [gid, "ai", "daily", "self-hosted"]})
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual([g["id"] for g in self._groups()], [gid, "ai", "daily", "self-hosted"])
        self.assertEqual(c.post("/api/link_groups/reorder", json={"order": ["ai"]}).status_code, 400)

        c.post("/api/link_groups/%s/links" % gid, json={"url": "https://tool.example"})
        resp = c.delete("/api/link_groups/%s" % gid)
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual([g["id"] for g in self._groups()], ["ai", "daily", "self-hosted"])
        self.assertEqual(c.delete("/api/link_groups/%s" % gid).status_code, 404)

    def test_clearing_all_groups_is_remembered(self):
        c = self.client
        for gid in ("self-hosted", "daily", "ai"):
            self.assertTrue(c.delete("/api/link_groups/%s" % gid).get_json()["ok"])
        self.assertEqual(self._groups(), [])
        self.assertEqual(self.read_config()["link_groups"], [])

    def test_export_import_roundtrip_and_optional_key(self):
        c = self.client
        c.post("/api/link_groups/daily/links", json={"url": "https://keep.example", "name": "Keep"})
        exported = c.get("/api/configs/export").get_json()
        self.assertEqual(exported["link_groups"][1]["links"][0]["name"], "Keep")

        # 导入不含 link_groups 的旧格式：分组保留。
        resp = c.post("/api/configs/import", json={"configs": exported["configs"]})
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual(self._groups()[1]["links"][0]["name"], "Keep")

        # 导入含 link_groups：整体覆盖并校验。
        payload = dict(exported, link_groups=[{"name": "仅一个", "links": [{"url": "https://only.example"}]}])
        resp = c.post("/api/configs/import", json=payload)
        self.assertTrue(resp.get_json()["ok"], resp.get_json())
        groups = self._groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["links"][0]["name"], "only.example")

        bad = dict(exported, link_groups=[{"name": "坏", "links": [{"url": "nope"}]}])
        resp = c.post("/api/configs/import", json=bad)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("第 1 个分组", resp.get_json()["error"])

    def test_writes_require_admin_when_password_set(self):
        orig = app_module.ADMIN_PASSWORD
        app_module.ADMIN_PASSWORD = "secret-for-test"
        try:
            c = self.client
            self.assertEqual(c.post("/api/link_groups", json={"name": "x"}).status_code, 403)
            self.assertEqual(c.post("/api/link_groups/daily/links", json={"url": "https://x.example"}).status_code, 403)
            self.assertEqual(c.delete("/api/link_groups/daily").status_code, 403)
            # 读取始终开放。
            self.assertEqual(len(self._groups()), 3)
            resp = c.post("/api/link_groups/daily/links", json={"url": "https://x.example"},
                          headers={"X-Admin-Password": "secret-for-test"})
            self.assertEqual(resp.status_code, 200)
        finally:
            app_module.ADMIN_PASSWORD = orig


if __name__ == "__main__":
    unittest.main()

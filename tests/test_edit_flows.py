# -*- coding: utf-8 -*-
"""编辑态端到端回归。

脱敏改造最大的风险是「读得到的不够编辑用」。这里按前端的真实调用顺序把三条编辑
链路各走一遍，确认解锁后仍然改得动、且不会因为回存脱敏视图而把配置或凭据弄丢。
"""
import unittest

from tests._support import StoreIsolationMixin, app_module
from app import app  # noqa: E402

PASSWORD = "edit-flow-unit-test-6b2d1e"
TOKEN = "checkin-token-edit-flow"
HEADER = "Bearer bookmark-header-edit-flow"

STORE = {
    "configs": [{"name": "签到站", "base_url": "https://cfg.example", "user_id": "42",
                 "access_token": TOKEN, "enabled": True, "turnstile": "ts-value"}],
    "proxy_url": "http://user:proxy-pass-edit-flow@127.0.0.1:1080",
    "schedule": {"enabled": False, "time": "08:30"},
    "bookmarks": [{"name": "看板站", "url": "https://dash.example", "fields": [
        {"id": "f_bal", "label": "余额", "type": "amount", "enabled": True, "unit": "USD",
         "method": "GET", "url": "https://api.dash.example/me",
         "headers": {"Authorization": HEADER}, "body": None, "json_path": "data.balance",
         "value": "12.34", "updated_at": "2026-09-15 08:00:00"}]}],
    "link_groups": [{"id": "daily", "name": "常用", "icon": "globe", "color": "mint", "links": [
        {"id": "l1", "name": "Docs", "url": "https://docs.example", "tags": ["doc"], "pinned": False}]}],
}


class EditFlowBase(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(EditFlowBase, self).setUp()
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", app_module.ADMIN_PASSWORD)
        app_module.ADMIN_PASSWORD = PASSWORD
        self.write_config(dict(STORE))
        self.c = app.test_client()
        self.c.environ_base["HTTP_X_ADMIN_PASSWORD"] = PASSWORD


class CheckinConfigEditTest(EditFlowBase):
    """签到配置：前端拿脱敏列表 → 需要时单独取 token → 保存。"""

    def test_token_is_reachable_for_editing(self):
        data = self.c.get("/api/configs/0/secret").get_json()
        self.assertEqual(data["access_token"], TOKEN)

    def test_turnstile_comes_back_for_prefill(self):
        # 编辑弹窗直接用列表里的 turnstile 回填；不回填就会在保存时被清空。
        self.assertEqual(self.c.get("/api/configs").get_json()["configs"][0]["turnstile"], "ts-value")

    def test_saving_without_retyping_the_token_keeps_it(self):
        # 前端保存时 access_token 传空串，表示「沿用旧值」。
        resp = self.c.put("/api/configs/0", json={
            "name": "签到站改名", "base_url": "https://cfg.example", "user_id": "42",
            "turnstile": "ts-value", "enabled": True, "access_token": ""})
        self.assertTrue(resp.get_json()["ok"], resp.get_json())
        saved = self.read_config()["configs"][0]
        self.assertEqual(saved["name"], "签到站改名")
        self.assertEqual(saved["access_token"], TOKEN)

    def test_toggling_enabled_keeps_the_token(self):
        # 列表上的启用开关走的也是整条 PUT。
        cfg = self.c.get("/api/configs").get_json()["configs"][0]
        self.c.put("/api/configs/0", json={
            "name": cfg["name"], "base_url": cfg["base_url"], "user_id": cfg["user_id"],
            "turnstile": cfg["turnstile"], "enabled": False, "access_token": ""})
        saved = self.read_config()["configs"][0]
        self.assertFalse(saved["enabled"])
        self.assertEqual(saved["access_token"], TOKEN)


class BookmarkEditTest(EditFlowBase):
    """收藏站点：列表是脱敏的，编辑前单独取原文。"""

    def test_edit_modal_gets_the_full_request_config(self):
        field = self.c.get("/api/bookmarks/0/secret").get_json()["bookmark"]["fields"][0]
        self.assertEqual(field["headers"]["Authorization"], HEADER)
        self.assertEqual(field["json_path"], "data.balance")
        self.assertEqual(field["url"], "https://api.dash.example/me")

    def test_saving_back_what_the_modal_loaded_changes_nothing_unintended(self):
        bookmark = self.c.get("/api/bookmarks/0/secret").get_json()["bookmark"]
        resp = self.c.put("/api/bookmarks/0", json={
            "name": bookmark["name"], "url": bookmark["url"], "fields": bookmark["fields"]})
        self.assertTrue(resp.get_json()["ok"], resp.get_json())
        saved = self.read_config()["bookmarks"][0]["fields"][0]
        self.assertEqual(saved["headers"]["Authorization"], HEADER)
        self.assertEqual(saved["json_path"], "data.balance")
        self.assertEqual(saved["value"], "12.34")       # 配置签名未变，快照保留
        self.assertEqual(saved["unit"], "USD")

    def test_adding_a_second_field_keeps_the_first_intact(self):
        bookmark = self.c.get("/api/bookmarks/0/secret").get_json()["bookmark"]
        fields = list(bookmark["fields"]) + [{
            "label": "到期", "type": "time",
            "curl": "curl 'https://api.dash.example/exp' -H 'Authorization: %s'" % HEADER,
            "json_path": "data.expire"}]
        self.assertTrue(self.c.put("/api/bookmarks/0", json={
            "name": bookmark["name"], "url": bookmark["url"], "fields": fields}).get_json()["ok"])
        saved = self.read_config()["bookmarks"][0]["fields"]
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["headers"]["Authorization"], HEADER)
        self.assertEqual(saved[1]["type"], "time")

    def test_deleting_a_field_does_not_disturb_the_rest(self):
        bookmark = self.c.get("/api/bookmarks/0/secret").get_json()["bookmark"]
        self.assertTrue(self.c.put("/api/bookmarks/0", json={
            "name": bookmark["name"], "url": bookmark["url"], "fields": []}).get_json()["ok"])
        saved = self.read_config()["bookmarks"][0]
        self.assertEqual(saved["fields"], [])
        self.assertEqual(saved["name"], "看板站")


class FieldRequestConfigTest(EditFlowBase):
    """字段请求配置的「不传即沿用」语义，别把已存的请求体悄悄抹掉。"""

    def _post_field_store(self):
        return {"configs": [], "proxy_url": "", "bookmarks": [{
            "name": "POST 站", "url": "https://p.example", "fields": [{
                "id": "f_post", "label": "余额", "type": "amount", "enabled": True,
                "method": "POST", "url": "https://api.p.example/q", "headers": {"X-Key": "k"},
                "body": '{"q":1}', "json_path": "data.balance"}]}], "link_groups": []}

    def test_explicit_config_without_body_keeps_the_stored_body(self):
        self.write_config(self._post_field_store())
        field = self.c.get("/api/bookmarks/0/secret").get_json()["bookmark"]["fields"][0]
        field.pop("body")           # 客户端只改了 header，没带 body
        resp = self.c.put("/api/bookmarks/0", json={
            "name": "POST 站", "url": "https://p.example", "fields": [field]})
        self.assertTrue(resp.get_json()["ok"], resp.get_json())
        self.assertEqual(self.read_config()["bookmarks"][0]["fields"][0]["body"], '{"q":1}')

    def test_explicit_null_body_still_clears_it(self):
        self.write_config(self._post_field_store())
        field = self.c.get("/api/bookmarks/0/secret").get_json()["bookmark"]["fields"][0]
        field["body"] = None        # 明确要求清空
        self.assertTrue(self.c.put("/api/bookmarks/0", json={
            "name": "POST 站", "url": "https://p.example", "fields": [field]}).get_json()["ok"])
        self.assertIsNone(self.read_config()["bookmarks"][0]["fields"][0]["body"])

    def test_a_brand_new_field_has_no_body_to_inherit(self):
        self.write_config(self._post_field_store())
        resp = self.c.put("/api/bookmarks/0", json={"name": "POST 站", "url": "https://p.example", "fields": [{
            "label": "新字段", "type": "raw", "method": "GET",
            "url": "https://api.p.example/x", "headers": {}, "json_path": "a"}]})
        self.assertTrue(resp.get_json()["ok"], resp.get_json())
        self.assertIsNone(self.read_config()["bookmarks"][0]["fields"][0]["body"])


class LinkEditTest(EditFlowBase):
    """网址：没有凭据，列表本身就是可编辑的全量数据。"""

    def test_list_carries_everything_the_modal_needs(self):
        link = self.c.get("/api/configs").get_json()["link_groups"][0]["links"][0]
        for key in ("id", "name", "url", "desc", "icon", "tags", "pinned"):
            self.assertIn(key, link)

    def test_partial_update_keeps_the_other_fields(self):
        data = self.c.put("/api/link_groups/daily/links/l1", json={"name": "Docs v2"}).get_json()
        self.assertTrue(data["ok"])
        link = data["link"]
        self.assertEqual(link["name"], "Docs v2")
        self.assertEqual(link["url"], "https://docs.example")
        self.assertEqual(link["tags"], ["doc"])


class SettingsEditTest(EditFlowBase):
    """全局设置：代理串对未解锁者不下发，解锁后必须能原样改回去。"""

    def test_proxy_is_readable_when_unlocked(self):
        self.assertEqual(self.c.get("/api/configs").get_json()["proxy_url"], STORE["proxy_url"])

    def test_saving_settings_does_not_wipe_the_proxy(self):
        # 只改定时，不带 proxy_url —— 代理必须原样保留。
        self.assertTrue(self.c.put("/api/settings", json={
            "schedule": {"enabled": True, "time": "09:00"}}).get_json()["ok"])
        self.assertEqual(self.read_config()["proxy_url"], STORE["proxy_url"])

    def test_export_still_carries_everything_needed_to_restore(self):
        dump = self.c.get("/api/configs/export").get_json()
        self.assertEqual(dump["configs"][0]["access_token"], TOKEN)
        self.assertEqual(dump["bookmarks"][0]["fields"][0]["headers"]["Authorization"], HEADER)
        self.assertEqual(dump["proxy_url"], STORE["proxy_url"])

    def test_export_import_round_trip_is_lossless(self):
        dump = self.c.get("/api/configs/export").get_json()
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})
        self.assertTrue(self.c.post("/api/configs/import", json=dump).get_json()["ok"])
        restored = self.read_config()
        self.assertEqual(restored["configs"][0]["access_token"], TOKEN)
        self.assertEqual(restored["bookmarks"][0]["fields"][0]["headers"]["Authorization"], HEADER)
        self.assertEqual(restored["link_groups"][0]["links"][0]["url"], "https://docs.example")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""首页组件数据（倒数日 / 便签）：独立存储（deck.json）、校验、管理密码保护、随配置导出导入。"""
import json
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, _coerce_deck, read_deck  # noqa: E402

PASSWORD = "deck-test-password-1234"
EMPTY = {"days": [], "memo": {"text": "", "updated_at": ""}}


class DeckCase(StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super(DeckCase, self).setUp()
        self.client = app.test_client()

    def deck_file(self):
        return self.data_dir / "deck.json"

    def add_day(self, name, date, repeat="none"):
        resp = self.client.post("/api/deck/days", json={"name": name, "date": date, "repeat": repeat})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return resp.get_json()["deck"]["days"]


class DeckDaysTest(DeckCase):
    def test_starts_empty_without_creating_a_file(self):
        self.assertEqual(self.client.get("/api/deck").get_json(), {"ok": True, "deck": EMPTY})
        self.assertFalse(self.deck_file().exists())

    def test_create_appends_and_persists(self):
        self.add_day("  妈妈   生日 ", "1970-03-08", "lunar")
        days = self.add_day("域名到期", "2027-01-15")
        self.assertEqual([(d["name"], d["date"], d["repeat"]) for d in days],
                         [("妈妈 生日", "1970-03-08", "lunar"), ("域名到期", "2027-01-15", "none")])
        self.assertNotEqual(days[0]["id"], days[1]["id"])
        self.assertRegex(days[0]["created_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        on_disk = json.loads(self.deck_file().read_text(encoding="utf-8"))
        self.assertEqual([d["name"] for d in on_disk["days"]], ["妈妈 生日", "域名到期"])
        self.assertEqual(read_deck()["days"], days)

    def test_create_validation(self):
        bad = [
            ({"name": "  ", "date": "2027-01-01"}, "名称"),
            ({"name": "x" * 41, "date": "2027-01-01"}, "过长"),
            ({"name": 5, "date": "2027-01-01"}, "名称"),
            ({"name": "a", "date": "2027-02-30"}, "日期"),          # 没有这一天
            ({"name": "a", "date": "2027-1-1"}, "日期"),
            ({"name": "a", "date": "1899-12-31"}, "日期"),
            ({"name": "a", "date": 20270101}, "日期"),
            ({"name": "a"}, "日期"),
            ({"name": "a", "date": "2027-01-01", "repeat": "weekly"}, "重复"),
        ]
        for payload, needle in bad:
            resp = self.client.post("/api/deck/days", json=payload)
            self.assertEqual(resp.status_code, 400, payload)
            self.assertIn(needle, resp.get_json()["error"])
        self.assertEqual(self.client.post("/api/deck/days", data="not json").status_code, 400)
        self.assertFalse(self.deck_file().exists())

    def test_leap_day_is_a_real_date(self):
        self.assertEqual(self.add_day("闰日", "2028-02-29", "year")[0]["date"], "2028-02-29")

    def test_update_keeps_id_and_validates(self):
        did = self.add_day("考试", "2026-12-20")[0]["id"]
        resp = self.client.put("/api/deck/days/" + did, json={"name": "期末考试", "date": "2026-12-21", "repeat": "none"})
        day = resp.get_json()["deck"]["days"][0]
        self.assertEqual((day["id"], day["name"], day["date"]), (did, "期末考试", "2026-12-21"))
        self.assertEqual(self.client.put("/api/deck/days/" + did, json={"name": "", "date": "2026-12-21"}).status_code, 400)
        self.assertEqual(self.client.put("/api/deck/days/nope", json={"name": "a", "date": "2026-12-21"}).status_code, 404)
        self.assertEqual(read_deck()["days"][0]["name"], "期末考试")

    def test_delete(self):
        a = self.add_day("a", "2027-01-01")[0]["id"]
        self.add_day("b", "2027-01-02")
        left = self.client.delete("/api/deck/days/" + a).get_json()["deck"]["days"]
        self.assertEqual([d["name"] for d in left], ["b"])
        self.assertEqual(self.client.delete("/api/deck/days/" + a).status_code, 404)

    def test_count_is_capped(self):
        for i in range(app_module.MAX_DECK_DAYS):
            self.add_day("d%d" % i, "2027-01-01")
        resp = self.client.post("/api/deck/days", json={"name": "one more", "date": "2027-01-01"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("最多", resp.get_json()["error"])


class DeckMemoTest(DeckCase):
    def test_memo_round_trips_with_newlines_and_a_stamp(self):
        resp = self.client.put("/api/deck/memo", json={"text": "第一行\r\n第二行\x00\x07\n\t缩进"})
        memo = resp.get_json()["deck"]["memo"]
        self.assertEqual(memo["text"], "第一行\n第二行\n\t缩进")
        self.assertRegex(memo["updated_at"], r"^\d{4}-")
        self.assertEqual(read_deck()["memo"], memo)

    def test_memo_can_be_cleared_and_is_length_capped(self):
        self.client.put("/api/deck/memo", json={"text": "x"})
        self.assertEqual(self.client.put("/api/deck/memo", json={"text": ""}).get_json()["deck"]["memo"]["text"], "")
        resp = self.client.put("/api/deck/memo", json={"text": "x" * (app_module.DECK_MEMO_MAX + 1)})
        self.assertEqual(resp.status_code, 400)
        for bad in ({"text": 1}, {}, []):
            self.assertEqual(self.client.put("/api/deck/memo", json=bad).status_code, 400, bad)

    def test_memo_write_does_not_touch_days_or_config(self):
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})
        before = self.read_config()
        self.add_day("a", "2027-01-01")
        self.client.put("/api/deck/memo", json={"text": "hello"})
        self.assertEqual([d["name"] for d in read_deck()["days"]], ["a"])
        self.assertEqual(self.read_config(), before)
        self.assertFalse((self.data_dir / "config.json.bak").exists())   # 写便签不该去冲 config.json 的 .bak

    def test_previous_version_is_kept_as_bak(self):
        self.client.put("/api/deck/memo", json={"text": "v1"})
        self.client.put("/api/deck/memo", json={"text": "v2"})
        bak = json.loads((self.data_dir / "deck.json.bak").read_text(encoding="utf-8"))
        self.assertEqual(bak["memo"]["text"], "v1")


class DeckCoerceTest(unittest.TestCase):
    def test_lenient_cleanup_never_raises(self):
        for junk in (None, [], "x", 3, {"days": "x", "memo": []}):
            self.assertEqual(_coerce_deck(junk), EMPTY)
        deck = _coerce_deck({
            "days": [{"id": "ok1", "name": "保留", "date": "2027-05-01", "repeat": "year", "created_at": "2026-01-01 00:00:00"},
                     {"id": "ok1", "name": "重复 id", "date": "2027-05-02"},
                     {"id": "<script>", "name": "坏 id", "date": "2027-05-03", "repeat": "bogus", "created_at": "yesterday"},
                     {"name": "", "date": "2027-05-04"}, {"name": "没日期"}, {"name": "坏日期", "date": "2027-13-01"}, "junk"],
            "memo": {"text": "a\r\nb" + "x" * 5000, "updated_at": "nope"},
        })
        self.assertEqual([d["name"] for d in deck["days"]], ["保留", "重复 id", "坏 id"])
        ids = [d["id"] for d in deck["days"]]
        self.assertEqual(ids[0], "ok1")
        self.assertEqual(len(set(ids)), 3)
        self.assertRegex(ids[2], r"^[0-9a-f]{8}$")
        self.assertEqual([d["repeat"] for d in deck["days"]], ["year", "none", "none"])
        self.assertEqual(deck["days"][2]["created_at"], "")
        self.assertEqual(len(deck["memo"]["text"]), app_module.DECK_MEMO_MAX)
        self.assertTrue(deck["memo"]["text"].startswith("a\nb"))
        self.assertEqual(deck["memo"]["updated_at"], "")

    def test_unreadable_file_is_reported_not_swallowed(self):
        case = DeckCase("setUp")
        case.setUp()
        try:
            case.deck_file().write_text("{not json", encoding="utf-8")
            resp = case.client.get("/api/deck")
            self.assertEqual(resp.status_code, 500)
            self.assertIn("读取首页组件数据失败", resp.get_json()["error"])
            # 整页不受牵连：/api/configs 照常返回，只把原因带给那两个组件。
            listing = case.client.get("/api/configs").get_json()
            self.assertTrue(listing["ok"])
            self.assertEqual(listing["deck"], EMPTY)
            self.assertIn("读取首页组件数据失败", listing["deck_error"])
        finally:
            case.doCleanups()


class DeckAccessTest(DeckCase):
    def setUp(self):
        super(DeckAccessTest, self).setUp()
        saved = app_module.ADMIN_PASSWORD
        app_module.ADMIN_PASSWORD = PASSWORD
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", saved)
        self.admin = {"X-Admin-Password": PASSWORD}

    def test_every_endpoint_needs_the_admin_password(self):
        calls = [("get", "/api/deck", None), ("post", "/api/deck/days", {"name": "a", "date": "2027-01-01"}),
                 ("put", "/api/deck/days/x", {"name": "a", "date": "2027-01-01"}), ("delete", "/api/deck/days/x", None),
                 ("put", "/api/deck/memo", {"text": "secret"})]
        for method, url, body in calls:
            resp = getattr(self.client, method)(url, json=body)
            self.assertEqual(resp.status_code, 403, url)
        self.assertFalse(self.deck_file().exists())

    def test_open_listing_withholds_the_data_until_unlocked(self):
        self.client.post("/api/deck/days", json={"name": "体检", "date": "2027-01-01"}, headers=self.admin)
        self.client.put("/api/deck/memo", json={"text": "只给自己看"}, headers=self.admin)
        locked = self.client.get("/api/configs").get_json()
        self.assertEqual(locked["deck"], EMPTY)
        self.assertTrue(locked["deck_locked"])
        self.assertNotIn("只给自己看", json.dumps(locked, ensure_ascii=False))
        unlocked = self.client.get("/api/configs", headers=self.admin).get_json()
        self.assertFalse(unlocked["deck_locked"])
        self.assertEqual(unlocked["deck"]["memo"]["text"], "只给自己看")
        self.assertEqual(unlocked["deck"]["days"][0]["name"], "体检")

    def test_private_shell_carries_no_deck_data(self):
        self.addCleanup(setattr, app_module, "PRIVATE_MODE", app_module.PRIVATE_MODE)
        app_module.PRIVATE_MODE = True
        self.client.put("/api/deck/memo", json={"text": "私密"}, headers=self.admin)
        shell = self.client.get("/api/configs").get_json()
        self.assertTrue(shell["locked"])
        self.assertEqual(shell["deck"], EMPTY)
        self.assertTrue(shell["deck_locked"])


class DeckBackupTest(DeckCase):
    def test_export_carries_deck_and_import_restores_it(self):
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})
        self.add_day("纪念日", "2020-05-20", "year")
        self.client.put("/api/deck/memo", json={"text": "备份我"})
        exported = self.client.get("/api/configs/export").get_json()
        self.assertEqual(exported["deck"]["memo"]["text"], "备份我")
        self.assertEqual(exported["deck"]["days"][0]["name"], "纪念日")
        # 清空后导入：回来了。
        self.deck_file().unlink()
        self.assertEqual(self.client.post("/api/configs/import", json=exported).status_code, 200)
        restored = read_deck()
        self.assertEqual(restored["memo"]["text"], "备份我")
        self.assertEqual([d["name"] for d in restored["days"]], ["纪念日"])

    def test_import_without_the_key_leaves_deck_alone(self):
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})
        self.client.put("/api/deck/memo", json={"text": "别动我"})
        old_style = {"configs": [], "todos": []}
        self.assertEqual(self.client.post("/api/configs/import", json=old_style).status_code, 200)
        self.assertEqual(read_deck()["memo"]["text"], "别动我")
        # 键在但不是对象：同样不覆盖。
        self.assertEqual(self.client.post("/api/configs/import", json={"configs": [], "deck": []}).status_code, 200)
        self.assertEqual(read_deck()["memo"]["text"], "别动我")


if __name__ == "__main__":
    unittest.main()

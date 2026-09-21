# -*- coding: utf-8 -*-
"""日历「同步放假安排」：从中国政府网取国务院办公厅的通知 → 解析 → 自检 → 预览 → 确认后保存。

夹具是 2022–2026 五年真实通知的正文区（tests/fixtures/holiday_notices/），句式各有不同：
带 / 不带星期几、合并放假（国庆节、中秋节）、跨年的元旦、「与周末连休」、「放假1天，不调休」。
网络一律打桩，测试不连外网。"""
import copy
import json
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from tests.test_home_deck_ui import DeckScriptCase
from app import app  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "holiday_notices"
PASSWORD = "holiday-sync-test-pw"
NOTICE_URLS = {
    2026: "https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm",
    2025: "https://www.gov.cn/zhengce/zhengceku/202411/content_6986383.htm",
    2024: "https://www.gov.cn/zhengce/zhengceku/202310/content_6911528.htm",
}
BUILTIN_2026 = {
    "off": [["元旦", "2026-01-01", "2026-01-03"], ["春节", "2026-02-15", "2026-02-23"], ["清明节", "2026-04-04", "2026-04-06"],
            ["劳动节", "2026-05-01", "2026-05-05"], ["端午节", "2026-06-19", "2026-06-21"], ["中秋节", "2026-09-25", "2026-09-27"],
            ["国庆节", "2026-10-01", "2026-10-07"]],
    "work": ["2026-01-04", "2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10"],
}


def notice(year):
    return (FIXTURES / ("%d.html" % year)).read_bytes()


def search_payload(*entries):
    """仿中国政府网检索接口的返回：标题里带 <em> 高亮。"""
    return json.dumps({"code": 200, "searchVO": {"listVO": [
        {"title": title, "url": url, "pcode": pcode, "puborg": org} for title, url, pcode, org in entries
    ]}}, ensure_ascii=False).encode("utf-8")


def search_hit(year, url=None, org="国务院办公厅"):
    return ("国务院办公厅关于%d年<em>部分</em><em>节假日</em><em>安排</em>的通知" % year, url or NOTICE_URLS[year],
            "国办发明电〔%d〕7号" % (year - 1), org)


class ParseNoticeTest(unittest.TestCase):
    def test_2026_matches_the_table_built_into_the_page(self):
        plan = app_module.parse_holiday_notice(notice(2026), 2026)
        self.assertEqual({"off": plan["off"], "work": plan["work"]}, BUILTIN_2026)
        self.assertEqual(plan["source"], {"title": "国务院办公厅关于2026年部分节假日安排的通知",
                                          "doc_no": "国办发明电〔2025〕7号", "published": "2025-11-04"})

    def test_merged_festivals_and_single_day_new_year(self):
        plan = app_module.parse_holiday_notice(notice(2025), 2025)
        self.assertIn(["元旦", "2025-01-01", "2025-01-01"], plan["off"])                 # 放假1天，不调休
        self.assertIn(["国庆节、中秋节", "2025-10-01", "2025-10-08"], plan["off"])       # 合并放假
        self.assertEqual(len(plan["off"]), 6)
        self.assertEqual(plan["work"], ["2025-01-26", "2025-02-08", "2025-04-27", "2025-09-28", "2025-10-11"])

    def test_joined_with_the_weekend_extends_over_adjacent_saturday_and_sunday(self):
        plan = app_module.parse_holiday_notice(notice(2024), 2024)
        self.assertIn(["元旦", "2023-12-30", "2024-01-01"], plan["off"])                 # 1月1日放假，与周末连休（跨年）
        self.assertIn(["端午节", "2024-06-08", "2024-06-10"], plan["off"])
        self.assertIn("2024-09-14", plan["work"])

    def test_explicit_years_and_older_wording(self):
        plan = app_module.parse_holiday_notice(notice(2023), 2023)
        self.assertEqual(plan["off"][0], ["元旦", "2022-12-31", "2023-01-02"])
        self.assertIn(["中秋节、国庆节", "2023-09-29", "2023-10-06"], plan["off"])
        plan = app_module.parse_holiday_notice(notice(2022), 2022)
        self.assertEqual(plan["off"][0], ["元旦", "2022-01-01", "2022-01-03"])
        self.assertEqual(len(plan["work"]), 7)

    def test_the_page_must_be_the_notice_for_the_requested_year(self):
        with self.assertRaisesRegex(app_module.HolidaySyncError, "2027"):
            app_module.parse_holiday_notice(notice(2026), 2027)
        with self.assertRaises(app_module.HolidaySyncError):
            app_module.parse_holiday_notice("<html><body>国务院办公厅 放假 通知</body></html>".encode(), None)

    def test_anything_that_does_not_add_up_is_rejected_whole(self):
        page = notice(2026).decode("utf-8")
        tampered = {
            "星期几对不上": page.replace("1月1日（周四）", "1月1日（周五）"),
            "天数对不上": page.replace("共9天", "共8天"),
            "少了一个节日": page.replace("<strong>五、端午节：</strong>", "<strong>五、端午：</strong>"),
            "调休上班落在工作日": page.replace("5月9日（周六）上班", "5月6日上班"),
            "假期越界": page.replace("10月1日（周四）至7日（周三）放假调休，共7天", "10月1日（周四）至20日（周二）放假调休，共20天"),
            "国庆不含 10 月 1 日": page.replace("10月1日（周四）至7日（周三）", "10月2日（周五）至8日（周四）"),
            "重复的节日": page.replace("六、中秋节：", "六、国庆节："),
        }
        for label, text in tampered.items():
            with self.assertRaises(app_module.HolidaySyncError, msg=label):
                app_module.parse_holiday_notice(text.encode("utf-8"), 2026)

    def test_script_and_markup_in_the_page_never_reach_the_result(self):
        page = notice(2026).decode("utf-8").replace(
            "<strong>一、元旦：</strong>", "<script>alert('一、元旦：1月2日放假')</script><strong>一、元旦：</strong>")
        plan = app_module.parse_holiday_notice(page.encode("utf-8"), 2026)
        self.assertEqual(plan["off"], BUILTIN_2026["off"])
        self.assertNotIn("<", json.dumps(plan, ensure_ascii=False))


class NoticeUrlTest(unittest.TestCase):
    def test_only_www_gov_cn_policy_pages_are_accepted(self):
        ok = {
            "https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm": "https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm",
            "http://www.gov.cn/zhengce/content/2021-10/25/content_5644835.htm": "https://www.gov.cn/zhengce/content/2021-10/25/content_5644835.htm",
            "www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm": "https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm",
            "https://WWW.GOV.CN/zhengce/zhengceku/202511/content_7047091.htm?x=1#y": "https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm",
        }
        for raw, want in ok.items():
            self.assertEqual(app_module.holiday_notice_url(raw), want, raw)
        for raw in ("https://gov.cn/zhengce/zhengceku/a.htm", "https://www.gov.cn.evil.com/zhengce/zhengceku/a.htm",
                    "https://evil.com/?u=https://www.gov.cn/zhengce/zhengceku/a.htm", "https://www.gov.cn/zhengce/../../etc/passwd.htm",
                    "https://user:pw@www.gov.cn/zhengce/zhengceku/a.htm", "https://www.gov.cn:8443/zhengce/zhengceku/a.htm",
                    "ftp://www.gov.cn/zhengce/zhengceku/a.htm", "javascript:alert(1)", "https://www.gov.cn/xinwen/2025-11/04/a.htm", ""):
            self.assertEqual(app_module.holiday_notice_url(raw), "", raw)

    def test_fetching_refuses_anything_off_the_whitelist_before_touching_the_network(self):
        with mock.patch("urllib.request.build_opener") as opener:
            with self.assertRaises(app_module.HolidaySyncError):
                app_module._holiday_get("https://example.com/zhengce/zhengceku/a.htm")
            opener.assert_not_called()

    def test_redirects_off_the_whitelist_are_refused(self):
        handler = app_module._HolidayRedirectHandler()
        req = mock.Mock()
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/zhengce/zhengceku/a.htm")

    def test_search_keeps_only_state_council_notices_on_www_gov_cn(self):
        payload = search_payload(search_hit(2026), search_hit(2025),
                                 search_hit(2027, url="https://evil.example/zhengce/zhengceku/202611/content_1.htm"),
                                 search_hit(2024, org="某某部"),
                                 ("关于2027年部分节假日安排的解读", NOTICE_URLS[2026], "", "国务院办公厅"))
        with mock.patch.object(app_module, "_holiday_get", return_value=payload):
            found = app_module.search_holiday_notices()
        self.assertEqual(sorted(found), [2025, 2026])
        self.assertEqual(found[2026]["url"], NOTICE_URLS[2026])
        self.assertEqual(found[2026]["title"], "国务院办公厅关于2026年部分节假日安排的通知")

    def test_unreadable_search_response_is_a_clear_error(self):
        with mock.patch.object(app_module, "_holiday_get", return_value=b"<html>busy</html>"):
            with self.assertRaisesRegex(app_module.HolidaySyncError, "粘贴通知链接"):
                app_module.search_holiday_notices()


class FakeGovCn(object):
    """按 URL 回夹具；记录被请求过的地址。"""

    def __init__(self, search=None, pages=None):
        self.search = search if search is not None else search_payload(search_hit(2026), search_hit(2025))
        self.pages = pages if pages is not None else {NOTICE_URLS[y]: notice(y) for y in NOTICE_URLS}
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url.startswith(app_module.HOLIDAY_SEARCH_URL):
            if isinstance(self.search, Exception):
                raise self.search
            return self.search
        if url not in self.pages:
            raise app_module.HolidaySyncError("中国政府网返回 HTTP 404")
        return self.pages[url]


class HolidayApiTest(StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super(HolidayApiTest, self).setUp()
        self.client = app.test_client()
        app_module._holiday_cache.update(checks={}, previews={})
        self.addCleanup(app_module._holiday_cache.update, checks={}, previews={})
        patcher = mock.patch.object(app_module, "_holiday_this_year", return_value=2026)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gov = FakeGovCn()
        patcher = mock.patch.object(app_module, "_holiday_get", self.gov)
        patcher.start()
        self.addCleanup(patcher.stop)

    def holidays_file(self):
        return self.data_dir / "holidays.json"

    def check(self, **body):
        return self.client.post("/api/holidays/check", json=body)

    def test_check_previews_this_and_next_year_without_writing_anything(self):
        resp = self.check()
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual([(r["year"], r["status"]) for r in data["years"]], [(2026, "found"), (2027, "unpublished")])
        row = data["years"][0]
        self.assertEqual({"off": row["off"], "work": row["work"]}, BUILTIN_2026)
        self.assertEqual(row["source"]["url"], NOTICE_URLS[2026])
        self.assertFalse(row["stored"])
        self.assertTrue(data["token"])
        self.assertFalse(self.holidays_file().exists())
        self.assertEqual(self.client.get("/api/configs").get_json()["holidays"], {"plans": {}})

    def test_apply_saves_what_was_previewed_and_the_calendar_gets_it(self):
        self.gov.search = search_payload(search_hit(2027, url=NOTICE_URLS[2025]), search_hit(2026))
        self.gov.pages[NOTICE_URLS[2025]] = notice(2026).replace("2026年".encode(), "2027年".encode())
        data = self.check().get_json()
        # 2027 年这篇是拿 2026 年改了年份凑的：星期几对不上，整份拒收，但 2026 年照常能用。
        self.assertEqual(data["years"][1]["status"], "error")
        self.assertIn("不是周", data["years"][1]["error"])
        resp = self.client.post("/api/holidays/apply", json={"token": data["token"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["changed"], [2026])
        saved = json.loads(self.holidays_file().read_text(encoding="utf-8"))["plans"]
        self.assertEqual(sorted(saved), ["2026"])
        self.assertEqual(saved["2026"]["source"]["doc_no"], "国办发明电〔2025〕7号")
        self.assertTrue(saved["2026"]["synced_at"])
        listing = self.client.get("/api/configs").get_json()["holidays"]["plans"]
        self.assertEqual(listing["2026"]["work"], BUILTIN_2026["work"])

    def test_syncing_again_is_a_no_op(self):
        token = self.check().get_json()["token"]
        self.client.post("/api/holidays/apply", json={"token": token})
        stamp = self.holidays_file().stat().st_mtime_ns
        again = self.check().get_json()
        self.assertTrue(again["years"][0]["stored"])
        resp = self.client.post("/api/holidays/apply", json={"token": again["token"]}).get_json()
        self.assertEqual(resp["changed"], [])
        self.assertEqual(self.holidays_file().stat().st_mtime_ns, stamp)
        self.assertFalse(self.holidays_file().with_name("holidays.json.bak").exists())

    def test_a_second_click_within_a_minute_reuses_the_result(self):
        self.check()
        calls = len(self.gov.calls)
        data = self.check().get_json()
        self.assertTrue(data["reused"])
        self.assertEqual(len(self.gov.calls), calls)

    def test_apply_rechecks_when_the_preview_is_gone_and_refuses_if_gov_cn_changed(self):
        token = self.check().get_json()["token"]
        app_module._holiday_cache.update(checks={}, previews={})         # 进程重启过 / 预览过期
        self.gov.pages[NOTICE_URLS[2026]] = notice(2026).replace("5月9日（周六）上班".encode(), "5月9日（周六）、5月10日（周日）上班".encode())
        resp = self.client.post("/api/holidays/apply", json={"token": token})
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(self.holidays_file().exists())
        self.assertEqual(self.client.post("/api/holidays/apply", json={}).status_code, 400)

    def test_network_failure_is_reported_and_offers_the_paste_fallback(self):
        self.gov.search = app_module.HolidaySyncError("连不上中国政府网（URLError），稍后再试")
        resp = self.check()
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.get_json(), {"ok": False, "error": "连不上中国政府网（URLError），稍后再试", "can_paste": True})
        self.assertFalse(self.holidays_file().exists())

    def test_pasted_notice_link_works_without_the_search_api(self):
        self.gov.search = app_module.HolidaySyncError("检索接口挂了")
        data = self.check(url="www.gov.cn/zhengce/zhengceku/202411/content_6986383.htm").get_json()
        self.assertEqual([(r["year"], r["status"]) for r in data["years"]], [(2025, "found")])
        self.assertEqual(data["years"][0]["source"]["url"], NOTICE_URLS[2025])
        bad = self.check(url="https://evil.example/zhengce/zhengceku/202411/content_6986383.htm")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("只接受中国政府网", bad.get_json()["error"])
        self.assertEqual(self.client.post("/api/holidays/apply", json={"token": "t", "url": "http://127.0.0.1/zhengce/content/a.htm"}).status_code, 400)
        self.assertEqual([u for u in self.gov.calls if "evil" in u], [])

    def test_only_one_check_runs_at_a_time(self):
        self.assertTrue(app_module._holiday_check_lock.acquire(blocking=False))
        try:
            self.assertEqual(self.check().status_code, 429)
        finally:
            app_module._holiday_check_lock.release()

    def test_check_and_apply_need_the_admin_password(self):
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", app_module.ADMIN_PASSWORD)
        app_module.ADMIN_PASSWORD = PASSWORD
        self.assertEqual(self.check().status_code, 403)
        self.assertEqual(self.client.post("/api/holidays/apply", json={"token": "x"}).status_code, 403)
        self.assertEqual(self.gov.calls, [])
        ok = self.client.post("/api/holidays/check", json={}, headers={"X-Admin-Password": PASSWORD})
        self.assertEqual(ok.status_code, 200)
        # 已保存的安排是公开信息：未解锁的访客也能拿到（日历访客可见）。
        self.client.post("/api/holidays/apply", json={"token": ok.get_json()["token"]}, headers={"X-Admin-Password": PASSWORD})
        self.assertIn("2026", self.client.get("/api/configs").get_json()["holidays"]["plans"])

    def test_tampered_file_falls_back_year_by_year(self):
        good = dict(copy.deepcopy(BUILTIN_2026), source={"url": "javascript:alert(1)", "doc_no": "<b>x</b>" * 20})
        bad = {"off": [["元旦", "2027-01-01", "2027-01-01"]], "work": ["2027-01-06"]}
        self.holidays_file().write_text(json.dumps({"plans": {"2026": good, "2027": bad, "x": {}, "1999": good}}), encoding="utf-8")
        plans = app_module.read_holidays()
        self.assertEqual(sorted(plans), ["2026"])
        self.assertEqual(plans["2026"]["source"]["url"], "")
        self.assertLessEqual(len(plans["2026"]["source"]["doc_no"]), 40)
        self.holidays_file().write_text("{not json", encoding="utf-8")
        listing = self.client.get("/api/configs").get_json()
        self.assertEqual(listing["holidays"]["plans"], {})
        self.assertIn("读取放假安排失败", listing["holidays"]["error"])


class HolidayScriptTest(DeckScriptCase):
    """页面脚本：同步来的年份并进日历；预览、保存、失败的样子；访客看不到入口。"""
    PLAN_2027 = {
        "year": 2027,
        "off": [["元旦", "2027-01-01", "2027-01-03"], ["春节", "2027-02-05", "2027-02-12"], ["清明节", "2027-04-03", "2027-04-05"],
                ["劳动节", "2027-05-01", "2027-05-05"], ["端午节", "2027-06-09", "2027-06-11"], ["中秋节", "2027-09-15", "2027-09-17"],
                ["国庆节", "2027-10-01", "2027-10-07"]],
        "work": ["2027-02-20", "2027-10-09"],
        "source": {"title": "国务院办公厅关于2027年部分节假日安排的通知", "url": "https://www.gov.cn/zhengce/zhengceku/202611/content_1.htm",
                   "doc_no": "国办发明电〔2026〕9号", "published": "2026-11-05"},
    }

    def test_synced_years_join_the_calendar_and_merged_holidays_still_match(self):
        merged = {"year": 2025, "off": [["国庆节、中秋节", "2025-10-01", "2025-10-08"]], "work": ["2025-09-28", "2025-10-11"]}
        self.run_js("""
            let plan = holidayPlan();
            assert.equal(plan.years['2027'], 'synced');
            assert.equal(plan.years['2026'], 'builtin');
            assert.ok(plan.off.has(dayNum(2027, 2, 6)) && plan.work.has(dayNum(2027, 2, 20)));
            assert.ok(plan.off.has(dayNum(2025, 10, 6)));
            // 合并放假：中秋节当天照样认得出它的假期
            assert.equal(festivalSpan({ name: '中秋节', n: dayNum(2025, 10, 6) }).name, '国庆节、中秋节');
            pickCalDay(dayNum(2027, 2, 20));
            assert.ok($('calDetail').textContent.includes('调休上班'));
            assert.equal($('calPlan').hidden, false);
            assert.equal($('calPlanText').textContent, '放假安排：2026 年已收录 · 2027 年已收录');
            // 换了一份 STATE（重新加载后）才重算；同一份不重算。
            assert.equal(holidayPlan(), plan);
            STATE.holidays = { plans: {} };
            assert.notEqual(holidayPlan(), plan);
            assert.equal(holidayPlan().years['2027'], undefined);
        """, configs={"holidays": {"plans": {"2027": self.PLAN_2027, "2025": merged}}})

    def test_guests_do_not_see_the_sync_entry(self):
        self.run_js("""
            assert.equal($('calPlan').hidden, true);
            assert.equal($('calSync').hidden, true);
        """, configs={"admin_required": True, "admin_unlocked": False})

    def test_preview_then_save(self):
        preview = {"ok": True, "token": "t1", "years": [
            dict(self.PLAN_2027, status="found", stored=False),
            {"year": 2026, "status": "found", "stored": False, "off": BUILTIN_2026["off"], "work": BUILTIN_2026["work"],
             "source": {"url": "javascript:alert(1)", "doc_no": "<img src=x onerror=alert(1)>", "published": "2025-11-04"}},
        ]}
        applied = {"ok": True, "changed": [2027], "holidays": {"plans": {"2027": self.PLAN_2027}}}
        self.run_js("""
            const sent = [];
            const plain = globalThis.fetch;
            globalThis.fetch = (url, opts) => { sent.push([url, opts && opts.method, opts && opts.body]); return plain(url, opts); };
            assert.equal($('calPlanText').textContent, '放假安排：2026 年已收录 · 2027 年未收录');
            await checkHolidays('');
            assert.deepEqual(sent.shift(), ['/api/holidays/check', 'POST', '{}']);
            const html = $('calSync').innerHTML;
            assert.equal($('calSync').hidden, false);
            assert.ok(html.includes('<b>2027 年</b><span class="cal-sync-tag is-new">新增</span>'));
            assert.ok(html.includes('<span class="cal-sync-tag is-same">已是最新</span>'));      // 2026 年和页面内置的一模一样
            assert.ok(html.includes('href="https://www.gov.cn/zhengce/zhengceku/202611/content_1.htm" target="_blank" rel="noopener noreferrer"'));
            assert.ok(html.includes('元旦 1/1–1/3，春节 2/5–2/12') && html.includes('调休上班 2/20、10/9'));
            assert.ok(html.includes('保存到日历（2027 年）'));
            assert.ok(!html.includes('<img') && html.includes('&lt;img') && !html.includes('javascript:'));
            assert.ok(!holidayPlan().off.has(dayNum(2027, 2, 6)));                          // 预览不改日历
            await saveHolidays();
            assert.deepEqual(sent.shift(), ['/api/holidays/apply', 'POST', JSON.stringify({ token: 't1', url: '' })]);
            assert.equal($('calSync').hidden, true);
            assert.ok(holidayPlan().off.has(dayNum(2027, 2, 6)));
            assert.equal($('calPlanText').textContent, '放假安排：2026 年已收录 · 2027 年已收录');
        """, responses={
            "/api/holidays/check": preview, "/api/holidays/apply": applied})

    def test_up_to_date_unpublished_and_failure_states(self):
        self.run_js("""
            __RESPONSES['/api/holidays/check'] = { ok: true, token: 't', years: [
              { year: 2026, status: 'found', off: HOLIDAY_PLANS[2026].off, work: HOLIDAY_PLANS[2026].work, source: {} },
              { year: 2027, status: 'unpublished' }] };
            await checkHolidays('');
            let html = $('calSync').innerHTML;
            assert.ok(html.includes('还没公布') && html.includes('日历里的放假安排已是最新') && !html.includes('data-hsync="save"'));
            __RESPONSES['/api/holidays/check'] = { ok: false, error: '连不上中国政府网（URLError），稍后再试', can_paste: true };
            await checkHolidays('');
            html = $('calSync').innerHTML;
            assert.ok(html.includes('连不上中国政府网（URLError），稍后再试（日历没有改动）'));
            assert.ok(html.includes('id="calSyncUrl"') && html.includes('data-hsync="retry"'));
            assert.equal(HSYNC.busy, '');
            closeCalSync();
            assert.equal($('calSync').hidden, true);
        """)

    def run_js(self, assertions, configs=None, cookies=None, wide=False, responses=None):
        base = {"holidays": {"plans": {}}}
        base.update(configs or {})
        extra = "".join("__RESPONSES[%s] = %s;" % (json.dumps(k), json.dumps(v, ensure_ascii=False)) for k, v in (responses or {}).items())
        super(HolidayScriptTest, self).run_js(extra + assertions, configs=base, cookies=cookies, wide=wide)


if __name__ == "__main__":
    unittest.main()

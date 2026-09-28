# -*- coding: utf-8 -*-
"""首页组件第二批：天气（服务端去 Open-Meteo 取）、余额概览、世界时钟、时间进度。
服务端：城市 / 时钟列表的校验与存储、天气接口的白名单、缓存与旧数据兜底、管理权限；
页面：把真实脚本放进 node 里跑，核对各组件画出来的内容。"""
import json
import time
import unittest
from unittest import mock

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, _coerce_deck, read_deck  # noqa: E402
from tests.test_home_deck_ui import DeckScriptCase, deck_section  # noqa: E402
from tests.test_script_boot import _inline_script  # noqa: E402

PASSWORD = "deck-widgets-password-1234"
SHANGHAI = {"name": "上海", "region": "上海市，中国", "lat": 31.22222, "lon": 121.45806}
FORECAST = {
    "timezone": "Asia/Shanghai",
    "current": {"time": "2026-09-28T21:45", "temperature_2m": 24.7, "apparent_temperature": 27.7, "relative_humidity_2m": 81,
                "weather_code": 3, "wind_speed_10m": 10.1, "is_day": 0},
    "hourly": {"time": ["2026-09-28T%02d:00" % h for h in range(21, 24)] + ["2026-09-29T%02d:00" % h for h in range(0, 9)],
               "precipitation_probability": [16, 8, 6, 13, 25, 37, 47, 56, 65, 74, 82, 86]},
    "daily": {"time": ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"],
              "weather_code": [53, 80, 55, 53, 53], "temperature_2m_max": [28.1, 25, 26.3, 25.6, 23.1],
              "temperature_2m_min": [24.3, 23, 23, 21.6, 19.8], "precipitation_probability_max": [85, 86, 92, 49, 24],
              "sunrise": ["2026-09-28T05:45"], "sunset": ["2026-09-28T17:43"]},
}


class WidgetCase(StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super(WidgetCase, self).setUp()
        self.client = app.test_client()
        app_module._weather_cache.clear()
        app_module._weather_place_cache.clear()
        self.addCleanup(app_module._weather_cache.clear)
        self.addCleanup(app_module._weather_place_cache.clear)


class WeatherPlaceTest(WidgetCase):
    def test_place_is_validated_rounded_and_clearable(self):
        resp = self.client.put("/api/deck/weather", json=dict(SHANGHAI, extra="丢掉"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["deck"]["weather"], {"name": "上海", "region": "上海市，中国", "lat": 31.222, "lon": 121.458})
        self.assertEqual(read_deck()["weather"]["lat"], 31.222)          # 只留三位小数（约百米）
        for bad in ({"name": "", "lat": 1, "lon": 1}, {"name": "x", "lat": 91, "lon": 1}, {"name": "x", "lat": "nan", "lon": 1},
                    {"name": "x", "lat": 1, "lon": "inf"}, {"name": "x", "lat": None, "lon": 1}, {"name": "x", "lat": 1, "lon": 181}):
            self.assertEqual(self.client.put("/api/deck/weather", json=bad).status_code, 400, bad)
        self.assertEqual(read_deck()["weather"]["name"], "上海")         # 失败的写入不动原值
        self.assertIsNone(self.client.delete("/api/deck/weather").get_json()["deck"]["weather"])

    def test_coerce_drops_malformed_settings(self):
        deck = _coerce_deck({"weather": {"name": "x", "lat": "abc"}, "clocks": "Asia/Tokyo"})
        self.assertIsNone(deck["weather"])
        self.assertIsNone(deck["clocks"])                                  # 没设过：页面用默认城市
        deck = _coerce_deck({"clocks": [{"name": "东京", "tz": "Asia/Tokyo"}, {"name": "x", "tz": "../../etc/passwd"},
                                        "junk", {"name": "", "tz": "UTC"}] + [{"name": "c%d" % i, "tz": "Etc/GMT+%d" % i} for i in range(9)]})
        self.assertEqual(deck["clocks"][0], {"name": "东京", "tz": "Asia/Tokyo"})
        self.assertEqual(len(deck["clocks"]), 6)
        self.assertNotIn("../../etc/passwd", json.dumps(deck))


class ClocksTest(WidgetCase):
    def test_put_replaces_the_list(self):
        clocks = [{"name": "东京", "tz": "Asia/Tokyo"}, {"name": "  纽约  ", "tz": "America/New_York"}, {"name": "UTC", "tz": "UTC"},
                  {"name": "印第安纳", "tz": "America/Indiana/Indianapolis"}, {"name": "GMT+8", "tz": "Etc/GMT-8"}]
        resp = self.client.put("/api/deck/clocks", json={"clocks": clocks})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual([c["name"] for c in read_deck()["clocks"]], ["东京", "纽约", "UTC", "印第安纳", "GMT+8"])
        self.assertEqual(self.client.put("/api/deck/clocks", json={"clocks": []}).get_json()["deck"]["clocks"], [])   # 一个都不要

    def test_rejects_bad_lists(self):
        for body in ({"clocks": "Asia/Tokyo"}, {}, {"clocks": [{"name": "x", "tz": "tokyo"}]},
                     {"clocks": [{"name": "x", "tz": "Asia/Tokyo; rm -rf /"}]}, {"clocks": [{"name": "", "tz": "UTC"}]},
                     {"clocks": [{"name": "a", "tz": "UTC"}, {"name": "b", "tz": "UTC"}]},
                     {"clocks": [{"name": str(i), "tz": "Etc/GMT+%d" % i} for i in range(7)]}):
            self.assertEqual(self.client.put("/api/deck/clocks", json=body).status_code, 400, body)
        self.assertIsNone(read_deck()["clocks"])


class WeatherFetchTest(WidgetCase):
    def setUp(self):
        super(WeatherFetchTest, self).setUp()
        self.client.put("/api/deck/weather", json=SHANGHAI)

    def test_without_a_city_nothing_is_fetched(self):
        self.client.delete("/api/deck/weather")
        with mock.patch.object(app_module, "_weather_get") as get:
            self.assertEqual(self.client.get("/api/deck/weather").get_json(), {"ok": True, "place": None, "weather": None})
        get.assert_not_called()

    def test_forecast_is_normalized_and_cached(self):
        with mock.patch.object(app_module, "_weather_get", return_value=json.loads(json.dumps(FORECAST))) as get:
            first = self.client.get("/api/deck/weather").get_json()
            second = self.client.get("/api/deck/weather").get_json()
        self.assertEqual(get.call_count, 1)                                # 20 分钟内同一个地点只问一次
        base, params = get.call_args[0]
        self.assertEqual(base, "https://api.open-meteo.com/v1/forecast")
        self.assertIn(("latitude", 31.222), params)
        w = first["weather"]
        self.assertTrue(first["ok"])
        self.assertFalse(first["stale"])
        self.assertEqual(w["now"], {"temp": 24.7, "feels": 27.7, "humidity": 81, "wind": 10.1, "code": 3, "is_day": False, "time": "2026-09-28T21:45"})
        self.assertEqual(w["rain"], {"pop": 86, "at": "08:00"})           # 12 小时里概率最高的那个钟点
        self.assertEqual([d["date"] for d in w["days"]], FORECAST["daily"]["time"])
        self.assertEqual(w["days"][0], {"date": "2026-09-28", "code": 53, "max": 28.1, "min": 24.3, "pop": 85})
        self.assertEqual(w["sun"], {"rise": "05:45", "set": "17:43"})
        self.assertEqual(second["weather"], w)
        with mock.patch.object(app_module, "_weather_get", return_value=json.loads(json.dumps(FORECAST))) as again:
            self.client.get("/api/deck/weather?force=1")                   # 一分钟内的「重试」也复用
            self.assertEqual(again.call_count, 0)
            key = (31.222, 121.458)
            app_module._weather_cache[key] = (app_module._weather_cache[key][0] - 61, app_module._weather_cache[key][1])
            self.client.get("/api/deck/weather?force=1")
            self.assertEqual(again.call_count, 1)
        self.assertIsInstance(second["age"], int)

    def test_old_data_is_served_when_upstream_fails(self):
        with mock.patch.object(app_module, "_weather_get", return_value=json.loads(json.dumps(FORECAST))):
            self.client.get("/api/deck/weather")
        key = (31.222, 121.458)
        at, data = app_module._weather_cache[key]
        app_module._weather_cache[key] = (at - app_module.WEATHER_TTL - 5, data)
        with mock.patch.object(app_module, "_weather_get", side_effect=app_module.WeatherError("连不上 Open-Meteo（URLError）")):
            stale = self.client.get("/api/deck/weather").get_json()
            self.assertTrue(stale["ok"] and stale["stale"])
            app_module._weather_cache[key] = (time.time() - app_module.WEATHER_STALE_MAX - 5, data)
            gone = self.client.get("/api/deck/weather")
        self.assertEqual(gone.status_code, 502)
        self.assertIn("连不上", gone.get_json()["error"])

    def test_malformed_upstream_is_an_error_not_a_crash(self):
        for raw in ([], {"current": {}}, {"current": {"temperature_2m": "hot", "weather_code": 1}}, {"current": {"temperature_2m": 1, "weather_code": True}}):
            app_module._weather_cache.clear()
            with mock.patch.object(app_module, "_weather_get", return_value=raw):
                resp = self.client.get("/api/deck/weather")
            self.assertEqual(resp.status_code, 502, raw)
        junk = json.loads(json.dumps(FORECAST))
        junk["daily"] = {"time": ["2026-09-28", "<script>", 5], "weather_code": ["x"], "temperature_2m_max": [None]}
        junk["hourly"] = {"time": 1}
        app_module._weather_cache.clear()
        with mock.patch.object(app_module, "_weather_get", return_value=junk):
            w = self.client.get("/api/deck/weather").get_json()["weather"]
        self.assertEqual(w["days"], [{"date": "2026-09-28", "code": None, "max": None, "min": None, "pop": None}])
        self.assertIsNone(w["rain"])

    def test_only_the_two_open_meteo_hosts_are_ever_contacted(self):
        for url in ("https://example.com/v1/forecast", "http://api.open-meteo.com/v1/forecast", "https://api.open-meteo.com.evil.test/x"):
            with self.assertRaises(app_module.WeatherError):
                app_module._weather_get(url, (("a", "b"),))
        section = open(app_module.__file__, encoding="utf-8").read()
        section = section[section.index("# ----- 天气：由服务端去 Open-Meteo 取 -----"):section.index("# 法定节假日安排")]
        self.assertEqual(sorted(set(__import__("re").findall(r"https://([a-z.-]+)", section))), ["api.open-meteo.com", "geocoding-api.open-meteo.com"])

    def test_place_search(self):
        raw = {"results": [{"name": "上海", "latitude": 31.22222, "longitude": 121.45806, "admin1": "上海市", "country": "中国"},
                           {"name": "上海", "latitude": 31.22222, "longitude": 121.45806, "admin1": "上海市", "country": "中国"},
                           {"name": "上海", "latitude": 29.33, "longitude": 121.06, "admin1": "浙江", "country": "中国"},
                           {"name": "", "latitude": 1, "longitude": 1}, {"name": "坏的", "latitude": "x"}, "junk"]}
        with mock.patch.object(app_module, "_weather_get", return_value=raw) as get:
            places = self.client.get("/api/deck/weather/places?q=%20上海%20").get_json()["places"]
            self.client.get("/api/deck/weather/places?q=上海")
        self.assertEqual(get.call_count, 1)                                # 同一个关键字一小时内复用
        self.assertIn(("name", "上海"), get.call_args[0][1])
        self.assertEqual(places, [{"name": "上海", "region": "上海市，中国", "lat": 31.222, "lon": 121.458},
                                  {"name": "上海", "region": "浙江，中国", "lat": 29.33, "lon": 121.06}])
        self.assertEqual(self.client.get("/api/deck/weather/places?q=%20").status_code, 400)


class WidgetAccessTest(WidgetCase):
    def setUp(self):
        super(WidgetAccessTest, self).setUp()
        saved = app_module.ADMIN_PASSWORD
        app_module.ADMIN_PASSWORD = PASSWORD
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", saved)
        self.admin = {"X-Admin-Password": PASSWORD}

    def test_every_endpoint_needs_the_admin_password(self):
        with mock.patch.object(app_module, "_weather_get") as get:
            for method, url, body in (("get", "/api/deck/weather", None), ("get", "/api/deck/weather/places?q=上海", None),
                                      ("put", "/api/deck/weather", SHANGHAI), ("delete", "/api/deck/weather", None),
                                      ("put", "/api/deck/clocks", {"clocks": []})):
                self.assertEqual(getattr(self.client, method)(url, json=body).status_code, 403, url)
        get.assert_not_called()

    def test_city_is_not_leaked_to_visitors(self):
        self.client.put("/api/deck/weather", json=SHANGHAI, headers=self.admin)
        self.client.put("/api/deck/clocks", json={"clocks": [{"name": "东京", "tz": "Asia/Tokyo"}]}, headers=self.admin)
        locked = self.client.get("/api/configs").get_json()
        self.assertIsNone(locked["deck"]["weather"])
        self.assertNotIn("上海", json.dumps(locked, ensure_ascii=False))
        unlocked = self.client.get("/api/configs", headers=self.admin).get_json()
        self.assertEqual(unlocked["deck"]["weather"]["name"], "上海")
        self.assertEqual(unlocked["deck"]["clocks"], [{"name": "东京", "tz": "Asia/Tokyo"}])
        exported = self.client.get("/api/configs/export", headers=self.admin).get_json()
        self.assertEqual(exported["deck"]["weather"]["name"], "上海")      # 「导出 JSON」一并带上


class WidgetScriptTest(DeckScriptCase):
    """夹具时间：2026-09-20（周日）10:30。"""

    def test_clocks_default_and_offsets(self):
        self.run_js("""
            const rows = clockRows(CAL.now());
            assert.deepEqual(rows.map(r => [r.name, r.time, r.day, r.offset]), [
              ['洛杉矶', '19:30', '昨天', '晚 15 小时'], ['纽约', '22:30', '昨天', '晚 12 小时'],
              ['伦敦', '03:30', '今天', '晚 7 小时'], ['东京', '11:30', '今天', '早 1 小时']]);
            assert.ok($('clockList').innerHTML.includes('洛杉矶') && $('clockList').innerHTML.includes('11:30'));
            assert.equal($('clocksEdit').hidden, false);                      // 没设管理密码 = 能改
            assert.ok($('deckTabs').innerHTML.includes('洛杉矶 19:30'));
        """, cookies={"bh_home_deck": "clocks"}, tz="Asia/Shanghai")
        self.run_js("""
            assert.deepEqual(clockRows(CAL.now()).map(r => [r.name, r.offset]), [['新德里', '晚 2.5 小时'], ['北京', '和这里同一时间']]);
            assert.equal($('clockList').innerHTML.includes('Mars'), false);   // 浏览器不认识的时区：那一行不显示
            CLOCKS.editing = true; CLOCKS.sig = ''; renderClocks();
            assert.equal($('clocksEditor').hidden, false);
            assert.ok(!$('clockAddSel').innerHTML.includes('Asia/Kolkata'));    // 已经加过的不再列出
            assert.ok($('clockList').innerHTML.includes('data-clock-del="Asia/Shanghai"'));
        """, cookies={"bh_home_deck": "clocks"}, tz="Asia/Shanghai",
            configs={"deck": dict(self.DECK, clocks=[{"name": "新德里", "tz": "Asia/Kolkata"}, {"name": "北京", "tz": "Asia/Shanghai"},
                                                      {"name": "火星", "tz": "Mars/Olympus"}])})

    def test_locked_visitor_sees_default_clocks_without_edit(self):
        self.run_js("""
            assert.equal(clockRows(CAL.now()).length, 4);
            assert.equal($('clocksEdit').hidden, true);
        """, cookies={"bh_home_deck": "clocks"}, configs={"admin_required": True, "admin_unlocked": False, "deck_locked": True})

    def test_progress(self):
        self.run_js("""
            const rows = progressRows(CAL.now());
            assert.deepEqual(rows.map(r => [r.name, Math.floor(r.pct), r.left]), [
              ['今天', 43, '还剩 13 小时 30 分'], ['本周', 91, '最后一天'], ['本月', 64, '还剩 10 天'], ['今年', 71, '还剩 102 天']]);
            assert.equal(rows[3].nth, 263);
            assert.equal($('deckProgressMeta').textContent, '第 38 周');
            assert.ok($('progressList').innerHTML.includes('第 263 天'));
            assert.ok($('deckTabs').innerHTML.includes('今年 71%'));
            assert.equal(isoWeek(new Date(2027, 0, 1)), 53);                    // 2027-01-01 属于 2026 年第 53 周
            assert.equal(isoWeek(new Date(2026, 11, 28)), 53);
            assert.equal(isoWeek(new Date(2026, 0, 1)), 1);
        """, cookies={"bh_home_deck": "progress"})

    def test_balance_sorts_low_then_runway(self):
        amount = lambda **kw: dict({"id": "f", "label": "余额", "type": "amount", "enabled": True}, **kw)
        self.run_js("""
            const rows = balanceRows();
            assert.deepEqual(rows.map(r => r.b.name), ['偏低', '快用完', '没走势', '会重置', '<b>坏']);
            const html = $('balanceList').innerHTML;
            assert.ok(html.includes('低于提醒值 1,000'), html);
            assert.ok(html.includes('约还能用 3 天') && html.includes('额度会定期重置'), html);
            assert.ok(html.includes('&lt;b&gt;') && !html.includes('<b>坏'), html);   // 站名转义
            assert.ok(!html.includes('取数失败'));
            assert.equal($('deckBalanceMeta').textContent, '1 个偏低');
            assert.ok(html.indexOf('偏低') < html.indexOf('快用完'));
            assert.ok(html.includes('balance-item is-past') && html.includes('balance-item is-soon'));
        """, cookies={"bh_home_deck": "balance"}, configs={"bookmarks": [
            {"name": "没走势", "url": "https://c.example", "fields": [amount(value="80")]},
            {"name": "快用完", "url": "https://a.example", "fields": [amount(value="12.5", daily=[["2026-09-18", 20], ["2026-09-20", 12.5]])]},
            {"name": "取数失败", "url": "https://d.example", "fields": [amount(value="5", error="HTTP 401")]},
            {"name": "偏低", "url": "https://b.example", "fields": [amount(value="500", warn_below=1000)]},
            {"name": "会重置", "url": "https://e.example", "fields": [amount(value="3", daily=[["2026-09-18", 9], ["2026-09-20", 3]]),
                                                                     {"id": "t", "label": "重置时间", "type": "time", "enabled": True, "value": "x", "raw": 4102444800}]},
            {"name": "<b>坏", "url": "https://f.example", "fields": [amount(value="1,234.5", unit="$")]},
            {"name": "只有到期", "url": "https://g.example", "fields": [{"id": "t", "label": "到期", "type": "time", "enabled": True, "value": "x", "raw": 4102444800}]},
        ]})

    WEATHER = {"ok": True, "place": {"name": "上海", "region": "上海市，中国", "lat": 31.222, "lon": 121.458}, "stale": False, "age": 180,
               "weather": {"now": {"temp": 24.7, "feels": 27.7, "humidity": 81, "wind": 10.1, "code": 3, "is_day": True, "time": ""},
                           "rain": {"pop": 86, "at": "08:00"}, "sun": {"rise": "05:45", "set": "17:43"}, "tz": "Asia/Shanghai",
                           "days": [{"date": "2026-09-2%d" % i, "code": c, "max": 28 - i, "min": 22 - i, "pop": 50} for i, c in enumerate((3, 61, 0, 95, 71))]}}

    def test_weather_renders_after_one_fetch(self):
        self.run_js("""
            await tick(); await tick();
            const box = $('weatherBox').innerHTML;
            assert.equal($('weatherBox').hidden, false);
            assert.ok(box.includes('25°') && box.includes('阴') && box.includes('体感 28°') && box.includes('湿度 81%'), box);
            assert.ok(box.includes('08:00 前后可能下雨（86%）') && box.includes('今天 22° ~ 28°'), box);
            assert.deepEqual(Array.from(box.matchAll(/wx-day-name">([^<]+)</g)).map(m => m[1]), ['今天', '明天', '周二', '周三', '周四']);
            assert.ok(box.includes('雷阵雨') && box.includes('小雪'));
            assert.equal($('deckWeatherMeta').textContent, '上海');
            assert.ok($('weatherFootText').innerHTML.includes('3 分钟前'), $('weatherFootText').innerHTML);   // 按浏览器自己的钟算
            assert.equal($('weatherForm').hidden, true);
            assert.ok($('deckTabs').innerHTML.includes('25° 阴'));
            renderDeck(); deckTick(); await tick();
            assert.equal(__CALLS.fetches.filter(u => u.startsWith('/api/deck/weather')).length, 1);   // 还新鲜，不重复取
            WEATHER.at -= WEATHER_FRESH_MS + 1; deckTick(); await tick();
            assert.equal(__CALLS.fetches.filter(u => u.startsWith('/api/deck/weather')).length, 2);   // 放久了，下一次节拍重新取
        """, cookies={"bh_home_deck": "weather"}, configs={"deck": dict(self.DECK, weather=self.WEATHER["place"])},
            extra={"/api/deck/weather": self.WEATHER})

    def test_weather_without_city_or_when_locked_never_fetches(self):
        self.run_js("""
            await tick();
            assert.equal($('weatherForm').hidden, false);                     // 没选城市：直接给搜索框
            assert.ok($('weatherState').innerHTML.includes('搜一个城市'));
            assert.equal(__CALLS.fetches.filter(u => u.startsWith('/api/deck/weather')).length, 0);
        """, cookies={"bh_home_deck": "weather"})
        self.run_js("""
            await tick();
            assert.ok($('weatherState').innerHTML.includes('data-deck-unlock'));
            assert.equal($('weatherForm').hidden, true);
            assert.equal(__CALLS.fetches.filter(u => u.startsWith('/api/deck/weather')).length, 0);
        """, cookies={"bh_home_deck": "weather"}, configs={"admin_required": True, "admin_unlocked": False, "deck_locked": True})

    def test_weather_error_offers_retry(self):
        self.run_js("""
            await tick(); await tick();
            assert.ok($('weatherState').innerHTML.includes('暂时取不到天气：连不上 Open-Meteo') && $('weatherState').innerHTML.includes('data-weather-retry'));
            assert.equal($('weatherBox').hidden, true);
            deckTick(); await tick();
            assert.equal(__CALLS.fetches.filter(u => u.startsWith('/api/deck/weather')).length, 1);   // 两分钟内不重试
        """, cookies={"bh_home_deck": "weather"}, configs={"deck": dict(self.DECK, weather=self.WEATHER["place"])},
            extra={"/api/deck/weather": {"ok": False, "error": "连不上 Open-Meteo（URLError）"}})

    def test_script_guards(self):
        section = deck_section(_inline_script())
        self.assertIn("deckApi('/api/deck/weather' + (force ? '?force=1' : ''), 'GET')", section)
        self.assertIn("deckApi('/api/deck/weather/places?q=' + encodeURIComponent(q), 'GET')", section)
        self.assertNotIn("open-meteo.com", section.lower())              # 浏览器从不直接连 Open-Meteo
        for needle in ("name = escapeHtml(r.b.name)", "${escapeHtml(pl.name)}", "${escapeHtml(r.name)}"):
            self.assertIn(needle, section)


if __name__ == "__main__":
    unittest.main()

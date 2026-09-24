# -*- coding: utf-8 -*-
"""首页组件（日历 / 待办 / 倒数日 / 便签 / 到期提醒 / 签到状态）：页面结构、偏好白名单、样式护栏，
以及把真实页面脚本放进 node 里跑：组件的添加 / 移除 / 排序、日历与节日、倒数日的重复规则、便签自动保存。"""
import copy
import json
import re
import subprocess
import unittest
from pathlib import Path

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script
from app import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KINDS = ["calendar", "todo", "days", "memo", "expiry", "checkin"]
CARD_IDS = {"calendar": "deckCalendar", "todo": "homeTodo", "days": "deckDays", "memo": "deckMemo",
            "expiry": "deckExpiry", "checkin": "deckCheckin"}


def deck_section(script):
    return script[script.index("// ===== 首页组件 ====="):script.index("// ===== 首页待办 =====")]


class DeckMarkupTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        cls.script = _inline_script()

    def block(self, start, end):
        return self.html[self.html.index(start):self.html.index(end, self.html.index(start))]

    def test_every_card_is_a_static_labelled_node_outside_the_rerendered_list(self):
        """renderHome() 每敲一个搜索字符都会重写 #homeList：组件放在里面，便签写到一半的字和焦点就没了。"""
        deck = self.block('id="homeDeck"', 'class="home-main"')
        self.assertNotIn('id="homeList"', deck)
        self.assertLess(self.html.index('id="homeDeck"'), self.html.index('id="homeList"'))
        self.assertIn('<div class="home-deck" id="homeDeck" hidden>', self.html)            # 数据到位前不露面
        self.assertRegex(deck, r'id="deckTabs" role="group" aria-label="首页组件"')
        for kind in KINDS:
            card = CARD_IDS[kind]
            tag = re.search(r'<section class="deck-card[^"]*" id="%s" data-deck="%s" aria-labelledby="(\w+)" hidden>' % (card, kind), deck)
            self.assertIsNotNone(tag, kind)
            self.assertRegex(deck, r'<h2[^>]*id="%s"' % tag.group(1))
            # 标题行两个按钮：菜单（移动 / 移除）与收起，都声明了自己的状态。
            self.assertRegex(deck, r'data-deck-menu="%s" aria-haspopup="menu" aria-expanded="false"' % kind)
            self.assertRegex(deck, r'data-deck-fold="%s" aria-expanded="true" aria-controls="\w+Body"' % kind)
        self.assertEqual(sorted(re.findall(r'data-deck="(\w+)"', deck)), sorted(KINDS))

    def test_script_and_markup_agree_on_the_kinds(self):
        kinds = re.findall(r"\{ id: '(\w+)', el: '(\w+)', name: '", deck_section(self.script))
        self.assertEqual(dict(kinds), CARD_IDS)
        self.assertIn("const DECK_DEFAULT = ['calendar', 'todo'];", self.script)

    def test_inputs_live_in_their_own_forms_and_are_ignored_by_password_managers(self):
        """页面上有管理密码框：不在自己表单里的文本框会被当成「用户名」，手机上一点就弹已存密码。"""
        for form_id, fields in (("daysForm", ("daysName", "daysDate")), ("memoForm", ("memoText",))):
            form = self.block('id="%s"' % form_id, "</form>")
            self.assertIn('autocomplete="off"', form)
            self.assertNotIn('type="password"', form)
            for field in fields:
                tag = re.search(r'<(?:input|textarea)[^>]*id="%s"[^>]*>' % field, form).group(0)
                for marker in ("data-1p-ignore", 'data-lpignore="true"', "data-bwignore", 'data-form-type="other"'):
                    self.assertIn(marker, tag, field)
        self.assertRegex(self.html, r'id="daysName"[^>]*maxlength="40"')
        self.assertRegex(self.html, r'id="memoText"[^>]*maxlength="2000"')
        self.assertRegex(self.html, r'<label class="sr-only" for="memoText">')

    def test_menu_and_manager_live_outside_the_workspace(self):
        """菜单挂在 <body> 下：右侧那一列自己会滚动，放在里面最后一张卡片的菜单会被裁掉。"""
        menu = self.block('id="deckMenu"', "</div>")
        self.assertIn('role="menu"', menu)
        self.assertIn(" hidden>", menu)
        self.assertEqual(re.findall(r'role="menuitem" data-deck-act="(\w+)"', menu), ["up", "down", "remove", "manage"])
        self.assertLess(self.html.index("</main>"), self.html.index('id="deckMenu"'))
        modal = self.block('id="deckModal"', 'id="todoSortMenu"')
        self.assertIn('role="dialog" aria-modal="true" aria-labelledby="deckModalTitle"', modal)
        for needle in ('id="deckPicker"', 'id="deckLive" aria-live="polite"', 'id="deckSortHint"', 'id="deckReset"', 'id="deckModalClose"'):
            self.assertIn(needle, modal)
        # 入口：首页工具条、「外观」弹窗、标签条末尾。
        self.assertIn('id="homeDeckBtn"', self.block('id="homeTools"', 'id="homeSearchBox"'))
        self.assertIn('id="openDeckModal"', self.block('id="homeLookModal"', 'id="deckMenu"'))
        self.assertIn("data-deck-manage", self.script)

    def test_layout_cookies_are_whitelisted(self):
        section = deck_section(self.script)
        self.assertIn("new RegExp('(?:^|; )' + name + '=([a-z.]{1,80})(?:;|$)')", section)
        self.assertIn("filter(id => DECK_IDS.includes(id) && !seen.has(id) && seen.add(id))", section)
        self.assertIn("readPref('bh_home_deck_open', DECK_IDS.concat('none'), 'none')", section)
        names = set(re.findall(r"write(?:Pref|DeckList)\('(bh_\w+)'", section))
        self.assertEqual(names, {"bh_home_deck", "bh_home_deck_fold", "bh_home_deck_open"})

    def test_no_browser_storage_polling_or_third_party_requests(self):
        section = deck_section(self.script)
        for banned in ("localStorage", "sessionStorage", "setInterval", "eval(", "XMLHttpRequest", "WebSocket"):
            self.assertNotIn(banned, section)
        # 日历、节日全在浏览器里现算，不问任何外部服务；同步放假安排也只问本站（由服务端去中国政府网取）。
        self.assertNotRegex(section, r"https?://")
        urls = re.findall(r"(?:deckApi|holidayApi)\('([^']+)'", section)
        self.assertGreaterEqual(len(urls), 6)
        for url in urls:
            self.assertTrue(url.startswith(("/api/deck/", "/api/holidays/")), url)
        self.assertEqual(re.findall(r"fetch\(([^,)]+)", section), ["url"])          # 只有 deckApi 这一处发请求
        # 常驻定时器只有时钟那一个；便签的防抖定时器在 node 里要 unref，否则测试进程不退出。
        self.assertIn("if (MEMO.timer && typeof MEMO.timer.unref === 'function') MEMO.timer.unref();", section)
        self.assertIn("deckTick();", self.script[self.script.index("function tickHeroClock()"):])

    def test_user_text_is_escaped_before_it_reaches_html(self):
        section = deck_section(self.script)
        self.assertIn("name = escapeHtml(item.name)", section)
        self.assertIn("name = escapeHtml(r.b.name)", section)
        self.assertNotIn("innerHTML = item.name", section)
        self.assertNotRegex(section, r"\$\{(?:item|r\.b)\.name\}")
        self.assertNotIn("innerHTML", section[section.index("function renderMemo()"):section.index("// ===== 首页组件：到期提醒")]
                         .replace("state.innerHTML = deckLockedHtml('便签')", ""))      # 便签内容只走 textarea.value

    def test_page_is_served_with_the_deck_and_unchanged_csp(self):
        resp = app.test_client().get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="homeDeck"', resp.get_data(as_text=True))
        csp = resp.headers["Content-Security-Policy"]
        self.assertIn("img-src 'self' data:", csp)
        self.assertIn("connect-src 'self'", csp)


class DeckStyleGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        css = (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8")
        cls.css = css
        cls.deck = css[css.index("首页组件（"):css.index("首页待办（")]

    def test_hidden_attribute_wins_over_display_rules(self):
        rule = re.search(r"\n(\.home-deck\[hidden\][^{]*)\{ display: none; \}", self.deck).group(1)
        for sel in (".deck-card[hidden]", ".deck-state[hidden]", ".days-form[hidden]", ".memo-form[hidden]", ".cal-today[hidden]"):
            self.assertIn(sel, rule)

    def test_narrow_layout_is_a_tab_strip_with_one_open_card(self):
        self.assertIn(".deck-card:not(.is-open) { display: none; }", self.deck)
        tabs = re.search(r"\n\.deck-tabs \{([^}]*)\}", self.deck).group(1)
        self.assertIn("overflow-x: auto", tabs)
        # 放得下居中、放不下能滑到第一枚：靠首尾的 auto 外边距，不用 justify-content:center（溢出时左端会被裁掉）。
        self.assertNotIn("justify-content", tabs)
        self.assertIn(".deck-tab:first-child { margin-left: auto; }", self.deck)
        self.assertIn(".deck-tab:last-child { margin-right: auto; }", self.deck)

    def test_side_column_only_on_wide_screens_and_follows_the_nav(self):
        rail = self.deck[self.deck.index("@media (min-width: 1100px)"):self.deck.index("@media (min-width: 1280px)")]
        full = self.deck[self.deck.index("@media (min-width: 1280px)"):self.deck.index("@media (min-width: 1440px)")]
        for block, prefix in ((rail, "html.nav-rail "), (full, "")):
            self.assertIn("\n  " + prefix + ".home-body.has-deck { display: grid;", block)
            self.assertRegex(block, re.escape(prefix) + r"\.home-body\.has-deck \.home-deck \{ position: sticky;[^}]*max-height: calc\(100vh - 48px\);[^}]*overflow-y: auto;")
            self.assertIn(prefix + ".home-body.has-deck .deck-tabs { display: none; }", block)
            self.assertIn(prefix + ".home-body.has-deck .deck-card:not([hidden]) { display: block;", block)      # 不能把 hidden 的卡片也放出来
            self.assertIn(prefix + ".home-body.has-deck .deck-card.is-folded .deck-body { display: none; }", block)
        # 两段规则只差前缀；脚本里判断「是不是右侧一列」用的是同一对门槛。
        rules = [line for line in rail.splitlines() if not line.lstrip().startswith("/*")]
        self.assertEqual("\n".join(rules).replace("html.nav-rail ", "").replace("1100px", "1280px").strip(), full.strip())
        script = _inline_script()
        self.assertIn("const DECK_SIDE_QUERIES = { rail: '(min-width: 1100px)', full: '(min-width: 1280px)' };", script)

    def test_touch_targets_and_ios_zoom(self):
        mobile = self.deck[self.deck.rindex("@media (max-width: 760px)"):]
        self.assertRegex(mobile, r"\.deck-tab \{[^}]*min-height: 40px")
        self.assertRegex(mobile, r"\.cal-day \{ min-height: 44px; \}")
        self.assertRegex(mobile, r"\.memo-text \{ font-size: 16px; \}")
        self.assertRegex(self.deck, r"\.deck-pick-grip|touch-action")
        self.assertRegex(self.css, r"\.todo-grip \{[^}]*touch-action: none")      # 弹窗里的把手复用它

    def test_focus_and_degradation_paths(self):
        self.assertRegex(self.deck, r"\.deck-tab:focus-visible[^{]*\{ outline: 2px solid var\(--accent\)")
        self.assertIn(".cal-day:focus-visible { outline: 2px solid var(--accent);", self.deck)
        self.assertIn("@media (forced-colors: active) { .cal-day.is-picked { outline: 2px solid Highlight; }", self.deck)
        self.assertRegex(self.deck, r"@media \(prefers-reduced-motion: reduce\) \{ \.deck-tab, \.deck-fold \.ic")
        self.assertNotIn("backdrop-filter", self.deck)


@unittest.skipIf(NODE is None, "未安装 node")
class DeckScriptCase(unittest.TestCase):
    DECK = {"days": [], "memo": {"text": "", "updated_at": ""}}
    # 夹具里的「今天」：2026-09-20（周日，国庆调休上班日），离中秋节（9 月 25 日）5 天。
    NOW = "CAL.now = () => new Date(2026, 8, 20, 10, 30);"

    def run_js(self, assertions, configs=None, cookies=None, wide=False):
        responses = copy.deepcopy(RESPONSES)
        responses["/api/configs"].update({"todos": [], "todos_locked": False, "todos_error": "",
                                          "deck": copy.deepcopy(self.DECK), "deck_locked": False, "deck_error": ""})
        responses["/api/configs"].update(configs or {})
        script = "\n".join([
            Path(STUB).read_text(),
            "globalThis.__RESPONSES = " + json.dumps(responses) + ";",
            # DOM 桩的 document.cookie 只是个字符串属性，写一个会冲掉其它的；这里换成一只真正按名字存取的罐子。
            "{ const jar = new Map(); Object.defineProperty(document, 'cookie', { configurable: true,"
            "  get() { return Array.from(jar, ([k, v]) => k + '=' + v).join('; '); },"
            "  set(text) { const m = /^\\s*([^=;]+)=([^;]*)/.exec(String(text)); if (!m) return;"
            "    if (/max-age=0/.test(text)) jar.delete(m[1]); else jar.set(m[1], m[2]); } }); }",
            "for (const [k, v] of Object.entries(" + json.dumps(cookies or {}) + ")) document.cookie = k + '=' + v;",
            "globalThis.matchMedia = () => ({ matches: " + ("true" if wide else "false") + ", addEventListener() {} });",
            "const assert = require('node:assert/strict');",
            "document.querySelectorAll('.tab').forEach(t => { t.addEventListener = (name, fn) => { t[name] = fn; }; });",
            "for (const id of ['memoText', 'daysForm', 'daysList']) { const t = document.getElementById(id); t.on = {}; t.addEventListener = (name, fn) => { t.on[name] = fn; }; }",
            _inline_script().replace("const CAL = { now: () => new Date(),", "const CAL = { now: () => new Date(2026, 8, 20, 10, 30),"),
            "const tick = () => new Promise(r => setTimeout(r, 5));",
            "const shown = () => " + json.dumps(list(CARD_IDS.items())) + ".filter(([, el]) => !$(el).hidden).map(([id]) => id);",
            "setTimeout(async () => { try {", assertions,
            "assert.deepEqual(__CALLS.errors, []); assert.deepEqual(__CALLS.rejections, []);",
            "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, 30);",
        ])
        proc = subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        self.assertIn("ok", proc.stdout)


class DeckLayoutScriptTest(DeckScriptCase):
    def test_defaults_are_calendar_and_todo(self):
        self.run_js("""
            assert.deepEqual(deckOrder(), ['calendar', 'todo']);
            assert.deepEqual(shown(), ['calendar', 'todo']);
            assert.equal($('homeDeck').hidden, false);
            assert.ok($('homeBody').classList.contains('has-deck'));
            assert.deepEqual(deckFolds(), []);
            assert.equal(deckOpenId(), '');                                  // 手机上默认全收着，只露一行标签
        """)

    def test_cookie_values_outside_the_whitelist_are_dropped(self):
        self.run_js("""
            assert.deepEqual(deckOrder(), ['memo', 'calendar']);             // 不认识的 id、重复的 id 都丢掉，顺序保留
            assert.deepEqual(shown().sort(), ['calendar', 'memo']);
            assert.deepEqual(deckFolds(), ['memo']);
            assert.equal(deckOpenId(), '');                                  // 展开的那张已经不在首页上：作废
            document.cookie = 'bh_home_deck=<img src=x>'; renderDeck();      // 整个格式不对：当没写过，回落到默认
            assert.deepEqual(deckOrder(), ['calendar', 'todo']);
            document.cookie = 'bh_home_deck=none'; renderDeck();             // 明确的「一个都不要」
            assert.deepEqual(deckOrder(), []);
            assert.equal($('homeDeck').hidden, true);
            assert.ok(!$('homeBody').classList.contains('has-deck'));
            assert.deepEqual(shown(), []);
        """, cookies={"bh_home_deck": "memo.evil.calendar.memo", "bh_home_deck_fold": "memo.nope", "bh_home_deck_open": "todo"})

    def test_add_remove_and_reorder_persist_to_the_cookie(self):
        self.run_js("""
            setDeckOn('days', true);
            assert.deepEqual(deckOrder(), ['calendar', 'todo', 'days']);      // 新加的排最后
            assert.equal(deckOpenId(), 'days');                               // 标签条布局：顺手展开给人看
            assert.ok($('deckDays').classList.contains('is-open'));
            assert.equal(moveDeck('days', -1), true);
            assert.deepEqual(deckOrder(), ['calendar', 'days', 'todo']);
            assert.equal($('deckLive').textContent, '「倒数日」移到第 2 位，共 3 个');
            assert.equal(moveDeck('calendar', -1), false);                    // 已经在头上
            assert.equal(moveDeck('todo', 1), false);
            assert.equal(moveDeck('memo', 1), false);                         // 没放上首页的挪不了
            setDeckOn('calendar', false);
            assert.deepEqual(deckOrder(), ['days', 'todo']);
            assert.deepEqual(shown().sort(), ['days', 'todo']);
            setDeckOn('bogus', true);
            assert.deepEqual(deckOrder(), ['days', 'todo']);
            setDeckOn('days', false); setDeckOn('todo', false);
            assert.equal(document.cookie.includes('bh_home_deck=none'), true);
            assert.equal($('homeDeck').hidden, true);
        """)

    def test_fold_button_means_different_things_in_the_two_layouts(self):
        self.run_js("""
            toggleDeckFold('calendar');                                        // 右侧一列：各收各的
            assert.deepEqual(deckFolds(), ['calendar']);
            assert.ok($('deckCalendar').classList.contains('is-folded'));
            assert.ok(!$('homeTodo').classList.contains('is-folded'));
            toggleDeckFold('calendar');
            assert.deepEqual(deckFolds(), []);
            assert.equal(document.cookie.includes('bh_home_deck_fold=none'), true);
        """, wide=True)
        self.run_js("""
            toggleDeckOpen('todo');                                            // 标签条：同一时间只展开一张
            assert.equal(deckOpenId(), 'todo');
            toggleDeckOpen('calendar');
            assert.equal(deckOpenId(), 'calendar');
            assert.ok($('deckCalendar').classList.contains('is-open') && !$('homeTodo').classList.contains('is-open'));
            toggleDeckFold('calendar');                                        // 卡片上的按钮此时就是「收起来」
            assert.equal(deckOpenId(), '');
            assert.deepEqual(deckFolds(), []);                                 // 不去动右侧一列的收起状态
        """)

    def test_tab_strip_carries_a_one_line_summary(self):
        self.run_js("""
            const tabs = $('deckTabs').innerHTML;
            assert.ok(tabs.includes('data-deck-tab="calendar"') && tabs.includes('中秋节 5 天'));
            assert.ok(tabs.includes('data-deck-tab="todo"') && tabs.includes('2 项'));
            assert.ok(tabs.includes('妈妈&lt;生日&gt; 12 天'));               // 用户起的名字进 HTML 前转义
            assert.ok(tabs.includes('1 个临期'));
            assert.ok(tabs.includes('>1/2<'));
            assert.ok(tabs.includes('data-deck-manage'));
            assert.ok(tabs.indexOf('data-deck-tab="todo"') < tabs.indexOf('data-deck-tab="calendar"'));   // 跟着 Cookie 里的顺序
            // 内容没变就不重写（重写会丢掉标签上的焦点）。
            $('deckTabs').innerHTML = 'UNTOUCHED'; renderDeck();
            assert.equal($('deckTabs').innerHTML, 'UNTOUCHED');
        """, cookies={"bh_home_deck": "todo.calendar.days.expiry.checkin"}, configs={
            "todos": [{"id": "a", "text": "x", "done": False}, {"id": "b", "text": "y", "done": False}, {"id": "c", "text": "z", "done": True}],
            "deck": {"days": [{"id": "d1", "name": "妈妈<生日>", "date": "2026-10-02", "repeat": "none"}], "memo": {"text": "", "updated_at": ""}},
            "bookmarks": [{"name": "VPS", "url": "https://vps.example", "fields": [
                {"id": "f", "label": "到期", "type": "time", "enabled": True, "value": "2026-09-22", "raw": 4102444800}]},
                {"name": "旧域名", "url": "https://old.example", "fields": [
                    {"id": "f", "label": "到期", "type": "time", "enabled": True, "value": "2001-01-01", "raw": 978307200}]}],
            "configs": [dict(RESPONSES["/api/configs"]["configs"][0], checked_in_today=True),
                        dict(RESPONSES["/api/configs"]["configs"][0], name="站点B"),
                        dict(RESPONSES["/api/configs"]["configs"][0], name="停用", enabled=False)],
        })

    def test_private_shell_hides_every_card(self):
        self.run_js("assert.equal($('homeDeck').hidden, true); assert.deepEqual(shown(), []);",
                    configs={"private": True, "locked": True, "todos": [], "todos_locked": True, "deck_locked": True,
                             "link_groups": [], "bookmarks": [], "configs": []})

    def test_locked_visitor_sees_prompts_not_data(self):
        self.run_js("""
            for (const id of ['daysState', 'memoState', 'checkinDeckState']) assert.ok($(id).innerHTML.includes('data-deck-unlock'), id);
            assert.equal($('daysAdd').hidden, true);
            assert.equal($('memoForm').hidden, true);
            assert.equal($('deckCalendar').hidden, false);                    // 日历不含任何私人数据，访客也能用
            assert.ok($('calNext').innerHTML.includes('中秋节'));
        """, cookies={"bh_home_deck": "calendar.days.memo.checkin"},
            configs={"admin_required": True, "admin_unlocked": False, "todos_locked": True, "deck_locked": True,
                     "configs": [], "configs_hidden": True})

    def test_picker_lists_enabled_first_then_the_rest(self):
        self.run_js("""
            renderDeckPicker();
            const html = $('deckPicker').innerHTML;
            const order = Array.from(html.matchAll(/data-deck-pick="(\\w+)"/g)).map(m => m[1]);
            assert.deepEqual(order, ['memo', 'calendar', 'todo', 'days', 'expiry', 'checkin']);
            assert.equal((html.match(/ is-on"/g) || []).length, 2);
            assert.equal((html.match(/data-deck-grip/g) || []).length, 2);       // 只有已添加的能排序
            assert.equal((html.match(/data-deck-toggle checked/g) || []).length, 2);
            assert.ok(html.includes('aria-label="在首页显示日历"'));
            assert.equal(deckSummaryText(), '便签、日历');
        """, cookies={"bh_home_deck": "memo.calendar"})


class CalendarScriptTest(DeckScriptCase):
    def test_lunar_dates_and_festivals(self):
        self.run_js("""
            const N = (y, m, d) => dayNum(y, m, d);
            assert.equal(lunarText(lunarOf(N(2026, 9, 25))), '八月十五');
            assert.equal(lunarText(lunarOf(N(2025, 7, 25))), '闰六月初一');
            assert.deepEqual(festivalsOf(N(2026, 9, 25)), ['中秋节']);
            assert.deepEqual(festivalsOf(N(2026, 2, 17)), ['春节']);
            assert.deepEqual(festivalsOf(N(2026, 2, 16)), ['除夕']);             // 乙巳年腊月只有 29 天：除夕是廿九
            assert.deepEqual(festivalsOf(N(2025, 1, 28)), ['除夕']);             // 甲辰年腊月也只有 29 天
            assert.deepEqual(festivalsOf(N(2024, 2, 9)), ['除夕']);              // 癸卯年腊月有三十
            assert.deepEqual(festivalsOf(N(2024, 2, 8)), []);
            assert.deepEqual(festivalsOf(N(2026, 6, 19)), ['端午节']);
            assert.deepEqual(festivalsOf(N(2026, 10, 18)), ['重阳节']);
            assert.deepEqual(festivalsOf(N(2026, 5, 10)), ['母亲节']);
            assert.deepEqual(festivalsOf(N(2026, 6, 21)), ['父亲节']);
            assert.deepEqual(festivalsOf(N(2026, 10, 1)), ['国庆节']);
            assert.deepEqual(festivalsOf(N(2025, 7, 29)), []);                   // 闰六月初五不是端午
            assert.deepEqual(festivalsOf(N(2026, 9, 21)), []);
            // 清明是节气：2024–2028 分别落在 4、4、5、5、4 日。
            assert.deepEqual([2024, 2025, 2026, 2027, 2028].map(qingmingDay), [4, 4, 5, 5, 4]);
            assert.deepEqual(festivalsOf(N(2026, 4, 5)), ['清明节']);
            // 同一天两个节日：放假的排前面。2020-10-01 既是国庆也是中秋。
            assert.deepEqual(festivalsOf(N(2020, 10, 1)), ['国庆节', '中秋节']);
        """)

    def test_next_festival_headline_and_official_holiday_plan(self):
        self.run_js("""
            const today = todayNum();
            assert.equal(dayKey(today), '2026-09-20');
            const next = upcomingFestivals(today, 3);
            assert.deepEqual(next.map(f => [f.name, f.in, f.off]), [['中秋节', 5, true], ['国庆节', 11, true], ['重阳节', 28, false]]);
            const head = $('calNext').innerHTML;
            assert.ok(head.includes('下一个节日') && head.includes('<strong class="cal-next-name">中秋节</strong>'));
            assert.ok(head.includes('<b>5</b>') && head.includes('9月25日 周五 · 放假 9/25–9/27 共 3 天'));
            assert.ok(head.includes('国庆节<b>11 天</b>') && head.includes('重阳节<b>28 天</b>'));
            assert.equal($('deckCalendarMeta').textContent, '9月20日 周日 · 调休上班');      // 今天正好是国庆的调休上班日
            assert.equal($('calMonth').textContent, '2026年9月');
            assert.equal($('calToday').hidden, true);
            // 月历：周一起头；今天那格标「班」，中秋三天标「休」，节日名顶替农历。
            const grid = $('calGrid').innerHTML;
            assert.ok(grid.indexOf('data-cal-day="' + dayNum(2026, 8, 31) + '"') < grid.indexOf('data-cal-day="' + dayNum(2026, 9, 1) + '"'));
            const cell = (n) => { const at = grid.indexOf('data-cal-day="' + n + '"'); return grid.slice(grid.lastIndexOf('<button', at)).split('</button>')[0]; };
            assert.ok(/is-today is-picked[^"]*is-work/.test(cell(today)) && cell(today).includes('>班<'));
            assert.ok(cell(today).includes('tabindex="0"') && cell(today).includes('农历八月初十'));
            const moon = cell(dayNum(2026, 9, 25));
            assert.ok(moon.includes('is-fest') && moon.includes('is-off') && moon.includes('>休<') && moon.includes('<span class="cal-sub">中秋节</span>'));
            assert.ok(moon.includes('tabindex="-1"') && moon.includes('中秋节假期') && moon.includes('5 天后'));
            assert.ok(cell(dayNum(2026, 9, 11)).includes('<span class="cal-sub">八月</span>'));            // 初一显示月名
            assert.equal($('calDetail').textContent, '9月20日 周日 · 农历八月初十 · 调休上班 · 今天');
        """)

    def test_headline_on_a_festival_day_and_inside_a_holiday(self):
        self.run_js("""
            const at = (y, m, d) => { CAL.now = () => new Date(y, m - 1, d, 9); CAL.y = 0; CAL.picked = 0; renderDeck(); return $('calNext').innerHTML; };
            let html = at(2026, 9, 25);
            assert.ok(html.includes('>今天<') && html.includes('>中秋节<') && html.includes('放假 9/25–9/27 共 3 天 · 第 1 天'));
            assert.ok($('deckTabs').innerHTML.includes('今天 中秋节'));
            html = at(2026, 10, 3);
            assert.ok(html.includes('>放假中<') && html.includes('国庆节假期') && html.includes('10/1–10/7 · 第 3 天，共 7 天'));
            assert.ok(html.includes('<b>4</b>') && html.includes('天后收假'));
            assert.ok(html.includes('重阳节<b>15 天</b>'));
            assert.ok(/is-off[^>]*>元旦<b>90 天<\\/b>/.test(html));                  // 后面几个里总带一个放假的
            html = at(2026, 2, 16);
            assert.ok(html.includes('>今天<') && html.includes('>除夕<') && html.includes('放假 2/15–2/23 共 9 天 · 第 2 天'));
            // 还没公布放假安排的年份：节日照常，只是没有「休 / 班」和放假区间。
            html = at(2027, 9, 10);
            assert.ok(html.includes('>今天<') && html.includes('>教师节<'));
            assert.ok(html.includes('中秋节<b>5 天</b>'));
            assert.ok(!$('calGrid').innerHTML.includes('cal-badge'));
        """)

    def test_picking_days_and_changing_months(self):
        self.run_js("""
            const today = todayNum();
            pickCalDay(dayNum(2026, 10, 1));
            assert.equal($('calMonth').textContent, '2026年10月');
            assert.equal($('calToday').hidden, false);
            assert.equal($('calDetail').textContent, '10月1日 周四 · 农历八月廿一 · 国庆节 · 国庆节假期 · 11 天后');
            assert.ok(/is-picked[^>]*data-cal-day="%d"/.test($('calGrid').innerHTML.replace(/\\n\\s*/g, ' ')) || $('calGrid').innerHTML.includes('is-picked'));
            shiftCalMonth(1); shiftCalMonth(1); shiftCalMonth(1);
            assert.equal($('calMonth').textContent, '2027年1月');
            shiftCalMonth(-1);
            assert.equal($('calMonth').textContent, '2026年12月');
            pickCalDay(today);
            assert.equal(CAL.picked, 0);                                        // 点回今天 = 跟着今天走
            assert.equal($('calMonth').textContent, '2026年9月');
            assert.equal($('calToday').hidden, true);
            // 没变就不重画：搜索框每敲一个字都会 renderHome()，月历里的键盘焦点不能因此丢掉。
            $('calGrid').innerHTML = 'UNTOUCHED'; renderHome();
            assert.equal($('calGrid').innerHTML, 'UNTOUCHED');
        """.replace("%d", "\\\\d+"))

    def test_midnight_rollover_follows_today(self):
        self.run_js("""
            assert.ok($('calDetail').textContent.startsWith('9月20日'));
            CAL.now = () => new Date(2026, 8, 21, 0, 0, 30);
            tickHeroClock();
            assert.ok($('calDetail').textContent.startsWith('9月21日'));
            assert.ok($('calNext').innerHTML.includes('<b>4</b>'));
            assert.equal($('deckCalendarMeta').textContent, '9月21日 周一');
        """)

    def test_lunar_table_matches_the_reference_day_by_day(self):
        """农历表逐日锁定：1900-01-31 到 2100-12-31 共 73384 天的换算结果，校验值取自与香港天文台数据逐日核对过的一份。

        表里任何一位数字被改错，这里都会红。特意不用浏览器的 Intl 农历：它把 2027 年春节算成 2 月 7 日。"""
        self.run_js("""
            let h = 0x811c9dc5, days = 0;
            for (let n = dayNum(1900, 1, 31); n <= dayNum(2100, 12, 31); n++) {
                const lu = lunarOf(n);
                LUNAR.cache.clear();
                for (const ch of [lu.y, lu.m, lu.d, lu.leap ? 1 : 0].join('.') + ';') { h ^= ch.charCodeAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
                days++;
            }
            assert.equal(days, 73384);
            assert.equal(h.toString(16), '2a6fd7a');
            // 表的范围之外：不给农历，别的照常。
            assert.equal(lunarOf(dayNum(1900, 1, 30)), null);
            assert.equal(lunarOf(dayNum(2101, 1, 29)), null);
            assert.deepEqual(festivalsOf(dayNum(2150, 10, 1)), ['国庆节']);
        """)

    def test_well_known_festival_dates(self):
        self.run_js("""
            const on = (name, from, to) => { for (let n = from; n <= to; n++) if (festivalsOf(n).includes(name)) return dayKey(n); return ''; };
            const year = (name, y) => on(name, dayNum(y, 1, 1), dayNum(y, 12, 31));
            assert.deepEqual([2024, 2025, 2026, 2027, 2028, 2029, 2030].map(y => year('春节', y)),
                ['2024-02-10', '2025-01-29', '2026-02-17', '2027-02-06', '2028-01-26', '2029-02-13', '2030-02-03']);
            assert.deepEqual([2025, 2026, 2027].map(y => year('中秋节', y)), ['2025-10-06', '2026-09-25', '2027-09-15']);
            assert.deepEqual([2025, 2026, 2027].map(y => year('端午节', y)), ['2025-05-31', '2026-06-19', '2027-06-09']);
            assert.deepEqual([year('元宵节', 2026), year('七夕', 2026), year('重阳节', 2026), year('腊八节', 2026)],
                ['2026-03-03', '2026-08-19', '2026-10-18', '2026-01-26']);
        """)

    def test_holiday_plan_table_is_well_formed(self):
        """放假安排是照抄的：每段假期起止有序、调休上班日不落在假期里、2026 年七个法定节日一个不少。"""
        self.run_js("""
            const plan = holidayPlan();
            assert.deepEqual(plan.spans.filter(s => dayOf(s.from).y === 2026).map(s => s.name), STATUTORY_FESTIVALS);
            for (const s of plan.spans) assert.ok(s.to >= s.from && s.to - s.from < 10, s.name);
            for (const n of plan.work) { assert.ok(!plan.off.has(n)); assert.ok([0, 6].includes(dayOf(n).w), dayKey(n)); }   // 调休上班只会占周末
            // 每个法定节日当天都落在它自己的假期里。
            for (const s of plan.spans) {
                let hit = false;
                for (let n = s.from; n <= s.to; n++) if (festivalsOf(n).includes(s.name)) hit = true;
                assert.ok(hit, s.name);
            }
            assert.equal(plan.spans.filter(s => dayOf(s.from).y === 2026).reduce((sum, s) => sum + s.to - s.from + 1, 0), 3 + 9 + 3 + 5 + 3 + 3 + 7);   // 以后加了别的年份也不受影响
        """)


class DaysScriptTest(DeckScriptCase):
    def test_recurrence_rules(self):
        self.run_js("""
            const today = todayNum(), N = dayNum;
            const occ = (date, repeat) => dayOccurrence({ date, repeat }, today);
            assert.deepEqual(occ('2026-12-01', 'none'), { n: N(2026, 12, 1), years: 0, lunar: null });
            assert.equal(occ('2020-01-01', 'none').n, N(2020, 1, 1));                     // 不重复：过去了就是过去了
            assert.deepEqual([occ('2020-10-05', 'year').n, occ('2020-10-05', 'year').years], [N(2026, 10, 5), 6]);
            assert.deepEqual([occ('2020-09-20', 'year').n, occ('2020-09-20', 'year').years], [today, 6]);   // 今天也算
            assert.deepEqual([occ('2020-03-08', 'year').n, occ('2020-03-08', 'year').years], [N(2027, 3, 8), 7]);
            assert.equal(occ('2024-02-29', 'year').n, N(2027, 2, 28));                    // 平年没有 2 月 29 日：落在月末
            assert.equal(occ('2030-05-01', 'year').n, N(2030, 5, 1));                     // 还没到的第一次
            // 农历：1990-10-03 是农历八月十五，下一个八月十五是 2026-09-25。
            const moon = occ('1990-10-03', 'lunar');
            assert.deepEqual([moon.n, moon.years, lunarText(moon.lunar)], [N(2026, 9, 25), 36, '八月十五']);
            // 农历三十：当年那个月只有 29 天就落在廿九。1985-02-19 是腊月三十；乙巳年（2025）腊月只有 29 天 → 2026-02-16。
            CAL.now = () => new Date(2026, 0, 10);
            assert.equal(dayOccurrence({ date: '1985-02-19', repeat: 'lunar' }, todayNum()).n, N(2026, 2, 16));
            assert.equal(dayOccurrence({ date: 'garbage', repeat: 'none' }, todayNum()), null);
        """)

    def test_rows_are_sorted_and_escaped(self):
        self.run_js("""
            const rows = dayRows(todayNum());
            assert.deepEqual(rows.map(r => [r.item.id, r.diff]), [['today', 0], ['soon', 3], ['later', 100], ['recent', -2], ['old', -262]]);
            const html = $('daysList').innerHTML;
            assert.ok(html.includes('&lt;img src=x&gt;') && !html.includes('<img'));
            assert.ok(html.includes('aria-label="删除：&lt;img src=x&gt;"'));
            assert.ok(/days-item is-today" data-day="today"/.test(html) && /days-item is-soon" data-day="soon"/.test(html) && /days-item is-past" data-day="old"/.test(html));
            assert.ok(html.includes('每年 9月20日 · 6 周年'));
            assert.equal($('deckDaysMeta').textContent, '就是今天');
            assert.equal($('daysState').hidden, true);
            assert.equal($('daysForm').hidden, true);
            assert.equal($('daysAdd').hidden, false);
        """, cookies={"bh_home_deck": "days"}, configs={"deck": {"memo": {"text": "", "updated_at": ""}, "days": [
            {"id": "old", "name": "元旦", "date": "2026-01-01", "repeat": "none"},
            {"id": "later", "name": "<img src=x>", "date": "2026-12-29", "repeat": "none"},
            {"id": "recent", "name": "体检", "date": "2026-09-18", "repeat": "none"},
            {"id": "soon", "name": "交房租", "date": "2026-09-23", "repeat": "none"},
            {"id": "today", "name": "纪念日", "date": "2020-09-20", "repeat": "year"},
        ]}})

    def test_form_posts_to_the_deck_api_and_adopts_the_reply(self):
        self.run_js("""
            const sent = []; const realFetch = globalThis.fetch;
            globalThis.fetch = (url, opts) => { sent.push([opts && opts.method || 'GET', String(url), opts && opts.body ? JSON.parse(opts.body) : null]); return realFetch(url, opts); };
            const ev = { preventDefault() {}, stopPropagation() {} };
            openDaysForm('');
            assert.equal($('daysForm').hidden, false);
            assert.equal($('daysDate').value, '2026-09-20');                               // 新建时带上今天 / 月历里点选的那天
            await $('daysForm').on.submit(ev);
            assert.equal($('daysFormHint').textContent, '先起个名字');                       // 空名字不发请求
            assert.equal(sent.length, 0);
            $('daysName').value = '  发   工资 '; $('daysDate').value = '2026-10-10'; $('daysRepeat').value = 'none';
            __RESPONSES['/api/deck/days'] = { ok: true, deck: { days: [{ id: 'n1', name: '发 工资', date: '2026-10-10', repeat: 'none' }], memo: { text: '', updated_at: '' } } };
            await $('daysForm').on.submit(ev);
            assert.deepEqual(sent, [['POST', '/api/deck/days', { name: '发 工资', date: '2026-10-10', repeat: 'none' }]]);
            assert.equal($('daysForm').hidden, true);
            assert.ok($('daysList').innerHTML.includes('data-day="n1"') && $('daysList').innerHTML.includes('<b>20</b>'));
            // 修改：表单带上原值，走 PUT。
            openDaysForm('n1');
            assert.deepEqual([$('daysName').value, $('daysDate').value, $('daysSave').textContent], ['发 工资', '2026-10-10', '保存修改']);
            __RESPONSES['/api/deck/days/n1'] = { ok: false, error: '日期无效（格式 YYYY-MM-DD，1900–2200 年）' };
            await $('daysForm').on.submit(ev);
            assert.deepEqual(sent[1].slice(0, 2), ['PUT', '/api/deck/days/n1']);
            assert.equal($('daysFormHint').textContent, '日期无效（格式 YYYY-MM-DD，1900–2200 年）');     // 服务端拒了：表单留着，原因写明
            assert.equal($('daysForm').hidden, false);
        """, cookies={"bh_home_deck": "days"})


class MemoScriptTest(DeckScriptCase):
    CONFIGS = {"deck": {"days": [], "memo": {"text": "服务器上的内容", "updated_at": "2026-09-20 08:00:00"}}}

    def test_render_fills_the_box_but_never_clobbers_unsaved_typing(self):
        self.run_js("""
            assert.equal($('memoText').value, '服务器上的内容');
            assert.match($('deckMemoMeta').textContent, /^保存于 (09-20 )?08:00$/);         // 当天只显示时分（看的是真实的今天）
            $('memoText').value = '写到一半'; $('memoText').on.input();
            assert.equal($('deckMemoMeta').textContent, '有未保存的修改…');
            STATE.deck.memo.text = '另一台设备改的'; renderDeck();                        // 后台刷新到了新内容
            assert.equal($('memoText').value, '写到一半');                                // 手里这份不能被冲掉
        """, cookies={"bh_home_deck": "memo"}, configs=self.CONFIGS)

    def test_autosave_puts_the_text_and_reports_the_outcome(self):
        self.run_js("""
            const sent = []; const realFetch = globalThis.fetch;
            globalThis.fetch = (url, opts) => { sent.push([opts && opts.method || 'GET', String(url), opts && opts.body ? JSON.parse(opts.body) : null, !!(opts && opts.keepalive)]); return realFetch(url, opts); };
            await saveMemo();
            assert.equal(sent.length, 0);                                                  // 没改过：不发请求
            $('memoText').value = '第一行\\n第二行'; $('memoText').on.input();
            __RESPONSES['/api/deck/memo'] = { ok: true, deck: { days: [], memo: { text: '第一行\\n第二行', updated_at: '2026-09-20 10:31:00' } } };
            await $('memoText').on.blur();                                                 // 失焦立刻保存，不等防抖
            assert.deepEqual(sent, [['PUT', '/api/deck/memo', { text: '第一行\\n第二行' }, false]]);
            assert.match($('deckMemoMeta').textContent, /^已保存 (09-20 )?10:31$/);
            assert.equal(MEMO.dirty, false);
            // 失败：保留「没保存」的状态，下次输入 / 失焦再试。
            $('memoText').value = '再改'; $('memoText').on.input();
            __RESPONSES['/api/deck/memo'] = { ok: false, error: '便签过长（最多 2000 字）' };
            await saveMemo(true);
            assert.equal(sent[1][3], true);                                                // 切到后台时带 keepalive，页面关掉请求也发得出去
            assert.equal($('deckMemoMeta').textContent, '保存失败：便签过长（最多 2000 字）');
            assert.equal(MEMO.dirty, true);
        """, cookies={"bh_home_deck": "memo"}, configs=self.CONFIGS)


class ExpiryAndCheckinScriptTest(DeckScriptCase):
    def test_expiry_rows_take_the_earliest_time_field_and_sort_by_it(self):
        now = 1790000000      # 夹具里的相对时间只看「已过期 / 7 天内 / 以后」三档，不依赖具体的今天
        self.run_js("""
            Date.now = () => %d * 1000;
            EXPIRY.sig = ''; renderDeck();
            const rows = expiryRows();
            assert.deepEqual(rows.map(r => [r.b.name, r.f.label, r.kind]), [['过期<b>站', '到期', 'expired'], ['快到期', '续费', 'soon'], ['还早', '到期', '']]);
            const html = $('expiryList').innerHTML;
            assert.ok(html.includes('过期&lt;b&gt;站') && !html.includes('<b>站'));
            assert.ok(html.includes('已过期 1 天<') && html.includes('还有 2 天'));
            assert.ok(html.includes('href="https://soon.example"') && html.includes('rel="noopener noreferrer"'));
            assert.ok(!html.includes('javascript:'));                                       // 不安全的网址不变成链接
            assert.equal($('deckExpiryMeta').textContent, '2 个要留意');
            assert.ok(html.includes('data-expiry-all'));
        """ % now, cookies={"bh_home_deck": "expiry"}, configs={"bookmarks": [
            {"name": "还早", "url": "https://later.example", "fields": [{"id": "a", "label": "到期", "type": "time", "enabled": True, "value": "x", "raw": now + 90 * 86400}]},
            {"name": "快到期", "url": "https://soon.example", "fields": [
                {"id": "a", "label": "到期", "type": "time", "enabled": True, "value": "x", "raw": now + 40 * 86400},
                {"id": "b", "label": "续费", "type": "time", "enabled": True, "value": "y", "raw": now + 2 * 86400 + 60},
                {"id": "c", "label": "停用的", "type": "time", "enabled": False, "value": "z", "raw": now - 5 * 86400}]},
            {"name": "过期<b>站", "url": "javascript:alert(1)", "fields": [{"id": "a", "label": "到期", "type": "time", "enabled": True, "value": "x", "raw": now - 86400 - 60}]},
            {"name": "只有余额", "url": "https://money.example", "fields": [{"id": "a", "label": "余额", "type": "amount", "enabled": True, "value": "1"}]},
            {"name": "取数失败的不算", "url": "https://err.example", "fields": [{"id": "a", "label": "到期", "type": "time", "enabled": True, "value": "", "raw": now + 86400, "error": "HTTP 502"}]},
        ]})

    def test_expiry_empty_states(self):
        self.run_js("""
            assert.equal($('expiryState').hidden, false);
            assert.ok($('expiryState').textContent.includes('「时间」类型的字段'));
            assert.equal($('expiryList').hidden, true);
        """, cookies={"bh_home_deck": "expiry"})

    def test_checkin_card_counts_enabled_accounts_only(self):
        base = RESPONSES["/api/configs"]["configs"][0]
        self.run_js("""
            assert.deepEqual(checkinStats(), { total: 2, done: 1 });
            const html = $('checkinDeck').innerHTML;
            assert.ok(html.includes('<b>1</b> / 2') && html.includes('width:50%'));
            assert.ok(html.includes('每天 08:30 自动签到 · 1 个等补签'));
            assert.equal($('deckCheckinMeta').textContent, '还差 1 个');
            assert.equal($('checkinDeckState').hidden, true);
            STATE.configs = []; renderDeck();
            assert.ok($('checkinDeckState').innerHTML.includes('还没有签到账户'));
            assert.equal($('checkinDeck').hidden, true);
        """, cookies={"bh_home_deck": "checkin"}, configs={
            "configs": [dict(base, checked_in_today=True), dict(base, name="B"), dict(base, name="停用", enabled=False, checked_in_today=False)],
            "schedule": dict(RESPONSES["/api/configs"]["schedule"], enabled=True, pending_today=1)})


if __name__ == "__main__":
    unittest.main()

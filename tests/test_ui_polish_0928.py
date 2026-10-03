# -*- coding: utf-8 -*-
"""2026-09-28 纯 UI 复查的修正：手机网址行标签错位、看板走势文案、小组件副行截断、浅色对比度、组件列淡出、
「需手动签到」卡片上的「已停用」。

前半读样式 / 模板做护栏，后半把页面脚本放进 node 的 DOM 替身里真跑（同 test_ux_0926）。版面效果由浏览器回归覆盖。"""
import re
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, _inline_script
from tests.test_ux_0926 import run_page

ROOT = Path(__file__).resolve().parents[1]


class PolishGuardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8")
        cls.script = _inline_script()

    def test_mobile_link_row_pins_rows_so_tags_stay_on_the_host_line(self):
        block = self.css[self.css.index("/* 网址行更紧凑：标签并到域名那一行"):]
        block = block[:block.index("\n\n")]
        self.assertIn(".link-body > .link-host, .link-body > .link-tags { grid-row: 2; }", block)
        self.assertIn(".link-body > .link-desc { grid-row: 3; }", block)

    def test_light_theme_small_text_is_darkened_but_not_on_wallpaper(self):
        self.assertIn('html[data-theme="light"]:not(.wall-on) .tag { color: color-mix(in srgb, var(--tone, var(--accent)) 72%, #000); }', self.css)
        self.assertIn('html[data-theme="light"]:not(.wall-on) :is(.badge, .pill).green { color: color-mix(in srgb, var(--success) 85%, #000); }', self.css)

    def test_calendar_workday_cell_text_stays_readable(self):
        self.assertIn(".cal-day.is-work .cal-sub { color: var(--text-2); }", self.css)
        self.assertIn('html[data-theme="light"]:not(.wall-on) .cal-day.is-work:not(.is-picked) .cal-badge { color: color-mix(in srgb, var(--warning) 80%, #000); }', self.css)

    def test_two_cell_widget_leaves_room_for_its_sub_line(self):
        # 桌面两格宽的小组件：文字区 = 202 − 18（左缩进）− 2（边框）− 24（内边距）− 40（头像）− 10（间距）= 108px。
        self.assertIn("  display: flex; align-items: center; gap: 10px; min-height: 88px; padding: 12px;\n", self.css)
        self.assertIn(".tile-avatar.widget-avatar { width: 40px; height: 40px; flex: 0 0 40px;", self.css)

    def test_avatar_letters_use_their_own_lightness_in_every_scene(self):
        # 首字母不再和底板共用 --avatar-l：深色 70%、浅色 28%、壁纸场景 74%（浅色主题开壁纸时别把深字带进深底）。
        self.assertNotIn("color: hsl(var(--hue, 165) var(--avatar-sat) var(--avatar-l));", self.css)
        self.assertEqual(self.css.count("color: hsl(var(--hue, 165) var(--avatar-sat) var(--avatar-ink-l));"), 4)
        self.assertRegex(self.css, r":root, html\.wall-on \.modal-mask[^{]*\{[^}]*--avatar-ink-l: 70%;")
        self.assertRegex(self.css, r'html\[data-theme="light"\] \{[^}]*--avatar-ink-l: 28%;')
        self.assertIn("--avatar-sat: 70%; --avatar-l: 74%; --avatar-ink-l: 74%;", self.css)

    def test_side_deck_fades_at_the_end_that_has_more(self):
        self.assertRegex(self.css, r"\.home-deck\.fade-bottom \{[^}]*mask-image: linear-gradient")
        self.assertRegex(self.css, r"\.home-deck\.fade-top\.fade-bottom \{[^}]*mask-image: linear-gradient")
        self.assertIn("$('homeDeck').addEventListener('scroll', () => syncScrollFade($('homeDeck')), { passive: true });", self.script)
        self.assertIn("deckFade.observe($('deckCards'));", self.script)


@unittest.skipIf(NODE is None, '未安装 node')
class PolishScriptTest(unittest.TestCase):
    def check(self, assertions, before=''):
        proc = run_page(assertions, before)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_runway_never_says_zero_days(self):
        self.check("""
            assert.equal(runwayText(0.77), '照这个速度不到 1 天用完');
            assert.equal(runwayText(1.9), '约还能用 1 天');
            assert.equal(runwayText(539), '约还能用一年以上');
            const html = trendHtml([['2026-09-26', 32.53], ['2026-09-28', 9.01]]);
            assert.ok(html.includes('近 2 天日均用 11.76') && html.includes('不到 1 天用完') && !html.includes('0 天'), html);
        """)

    def test_resetting_quota_gets_a_line_but_no_usage_estimate(self):
        # reclaude-5 这类额度按「刷新时间」回满：每天记的只是某一刻的余量，日均和还能用几天都没有意义。
        self.check("""
            const b = { name: 'reclaude-5', url: 'https://r.example', fields: [
                { id: 'bal', label: '余额', type: 'amount', enabled: true, value: '9.01',
                  daily: [['2026-09-26', 32.53], ['2026-09-27', 22.74], ['2026-09-28', 9.01]] },
                { id: 'rst', label: '刷新时间', type: 'time', enabled: true, value: 'x', raw: Date.now() / 1000 + 7200, warn_days: 0 }] };
            const html = fieldsBlockHtml(b, 0);
            assert.ok(html.includes('trend-line'), html);
            assert.ok(!html.includes('日均用') && !html.includes('还能用'), html);
            // 没有重置时间的站点照旧估算。
            const plain = Object.assign({}, b, { fields: [b.fields[0]] });
            assert.ok(fieldsBlockHtml(plain, 0).includes('近 2 天日均用'));
        """)

    def test_flat_line_is_not_drawn(self):
        self.check("""
            const html = trendHtml([['2026-09-26', 27.3], ['2026-09-28', 27.3]]);
            assert.ok(!html.includes('trend-line') && html.includes('近 2 天余额没变'), html);
            assert.ok(trendHtml([['2026-09-26', 27.3], ['2026-09-28', 30]]).includes('trend-line'));
        """)

    def test_widget_sub_says_when_it_resets_in_few_characters(self):
        self.check("""
            const day = 86400, now = Date.now() / 1000;
            const site = (fields) => ({ name: 'S', url: 'https://s.example', fields: fields.map((f, k) => Object.assign({ id: 'f' + k, enabled: true }, f)) });
            let w = siteWidgetParts(site([{ label: '余额', type: 'amount', value: '3' }, { label: '到期时间', type: 'time', value: 'x', raw: now + 10 * day }]));
            assert.ok(/^\\d+ 天后到期$/.test(w.sub) && w.subTitle.startsWith('到期时间 · 还有 '), w.sub);
            w = siteWidgetParts(site([{ label: '余额', type: 'amount', value: '3' }, { label: '到期时间', type: 'time', value: 'x', raw: now - 2 * day }]));
            assert.ok(/^\\d+ 天前到期$/.test(w.sub), w.sub);
            // 名字不是「××时间 / 日期」的照旧写「名字 · 值」，也不带悬停提示。
            w = siteWidgetParts(site([{ label: '余额', type: 'amount', value: '3' }, { label: '到期', type: 'time', value: 'x', raw: now + 40 * day }]));
            assert.ok(w.sub.startsWith('到期 · 还有 ') && w.subTitle === '', w.sub);
            w = siteWidgetParts(site([{ label: '余额', type: 'amount', value: '3' }, { label: '刷新时间', type: 'time', value: 'x', error: 'HTTP 500' }]));
            assert.equal(w.sub, '刷新时间 · 取数失败');
            assert.ok(!homeSiteWidgetHtml(site([{ label: '余额', type: 'amount', value: '3' }]), 0).includes('widget-sub" title='));
        """)

    def test_manual_checkin_card_no_longer_says_disabled(self):
        self.check("""
            STATE.configs = [{ name: 'anyrouter', base_url: 'https://anyrouter.example', enabled: false }];
            const html = disabledItemHtml(STATE.configs[0], 0);
            assert.ok(!html.includes('已停用') && html.includes('去签到') && html.includes('不参与一键 / 定时签到'), html);
        """)


if __name__ == '__main__':
    unittest.main()

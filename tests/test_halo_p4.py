# -*- coding: utf-8 -*-
"""Halo 改版 P4（2026-10-04）：看板卡片以数值为主——头部即「打开站点」、刷新和「···」在头部、底下不再有整宽按钮；
主字段的大数字和走势线并排；记录不满两天的金额字段不再印出「null」；分组列表在桌面上也把标签并到域名那一行。"""
import unittest
from pathlib import Path

from tests.test_script_boot import NODE
from tests.test_ux_0926 import run_page

ROOT = Path(__file__).resolve().parents[1]


class BoardCardStyleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8")

    def test_card_has_no_fixed_height_and_no_full_width_open_bar(self):
        rule = self.css[self.css.index(".bookmark-card {"):]
        rule = rule[:rule.index("}")]
        self.assertNotIn("min-height", rule)
        self.assertNotIn(".bookmark-actions .btn.open", self.css)

    def test_primary_value_and_trend_share_a_row(self):
        self.assertIn(".field-item.is-primary:has(> .trend .trend-plot) > .trend { display: contents; }", self.css)
        self.assertIn(".field-item.is-primary:has(> .trend .trend-plot) .trend-plot { grid-column: 2; grid-row: 2;", self.css)

    def test_tags_join_the_host_line_on_every_screen(self):
        start = self.css.index("/* 网址行更紧凑：标签并到域名那一行")
        before = self.css[:start]
        # 不在任何媒体查询里：前面打开的 { 与 } 数目相等。
        self.assertEqual(before.count("{"), before.count("}"))
        self.assertIn(".link-card:has(.link-desc) .link-main { align-items: flex-start; }", self.css)


@unittest.skipIf(NODE is None, '未安装 node')
class BoardCardScriptTest(unittest.TestCase):
    def check(self, assertions):
        proc = run_page(assertions)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_header_opens_the_site_and_holds_the_actions(self):
        self.check("""
            const b = { name: 'EXA', url: 'https://exa.example', fields: [
                { id: 'bal', label: '余额', type: 'amount', enabled: true, value: '27.95' }] };
            const html = bookmarkCardHtml(b, 0, {});
            const top = html.slice(html.indexOf('bookmark-top'), html.indexOf('bm-fields-block'));
            assert.ok(/<a class="bookmark-open" href="https:\\/\\/exa\\.example"/.test(top), top);
            assert.ok(top.includes('bm-refresh') && top.includes('menu-trigger') && top.includes('data-card-grip'), top);
            assert.ok(!html.includes('打开站点'), html);
            assert.ok(!html.includes('null'), '只有一天记录的金额字段：没有走势，也不能印出 null');
            // 网址不可用：头部不是链接，直接说出来。
            const bad = bookmarkCardHtml(Object.assign({}, b, { url: 'javascript:alert(1)' }), 0, {});
            assert.ok(!bad.includes('<a class="bookmark-open"') && bad.includes('网址不可用'), bad);
        """)


if __name__ == "__main__":
    unittest.main()

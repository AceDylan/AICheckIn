# -*- coding: utf-8 -*-
"""首页管理闭环（docs/hub-ui-strategy.md 的 P0 / P1-3 / P1-4）：编辑弹窗里原地删除、首页搜索「查看全部」与 ⌘K 带字、
编辑首页时不抢焦点、编辑态轻点图标出菜单、手机弹窗 ✕、分组标签滚进视野。

前半读源码做结构护栏，后半把真实页面脚本放进 node 里执行；真机上的轻点 / 拖动区分由浏览器验证覆盖。"""
import copy
import json
import re
import subprocess
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script

ROOT = Path(__file__).resolve().parents[1]


class ClosureGuardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8")
        cls.script = _inline_script()

    def modal_actions(self, modal_id):
        start = self.html.index(f'id="{modal_id}"')
        start = self.html.index('<div class="modal-actions"', start)
        return self.html[start:self.html.index("</div>", start)]

    def test_edit_modals_offer_delete_on_the_left(self):
        for modal, btn, label in (("linkModal", "linkDelete", "删除网址"), ("bmModal", "bmDelete", "删除站点")):
            actions = self.modal_actions(modal)
            self.assertIn(f'<button class="btn danger sm" id="{btn}" hidden>{label}</button>', actions)
            # 删除在左、取消 / 保存在右，中间用 spacer 推开。
            self.assertLess(actions.index(btn), actions.index('class="spacer"'))
            self.assertLess(actions.index('class="spacer"'), actions.index("Cancel"))

    def test_delete_closes_the_modal_first_and_reuses_undoable_delete(self):
        link = self.script[self.script.index("$('linkDelete').addEventListener"):]
        link = link[:link.index("});") + 3]
        self.assertLess(link.index("$('linkModal').classList.remove('show')"), link.index("deleteLink(gid, lid)"))
        bm = self.script[self.script.index("$('bmDelete').addEventListener"):]
        bm = bm[:bm.index("});") + 3]
        # 站点按 key 找回下标：弹窗开着时列表可能已经被别处改过顺序。
        self.assertIn("b.key === bmEditKey", bm)
        self.assertLess(bm.index("$('bmModal').classList.remove('show')"), bm.index("deleteBm(at)"))
        self.assertNotIn("confirm(", link + bm)   # 有「撤销」，不再二次确认

    def test_cmd_k_carries_the_home_search_text(self):
        handler = self.script[self.script.index("// ⌘K / Ctrl+K：全局搜索"):]
        handler = handler[:handler.index("return;")]
        self.assertIn("from === $('homeSearch')", handler)
        self.assertIn("clearHomeSearch()", handler)
        self.assertIn("openOmni(preset)", handler)

    def test_arrange_menu_is_built_outside_the_arrange_section(self):
        # 编辑首页小节不许出现 innerHTML（test_home_layout 的护栏）；菜单元素在渲染区里拼，内容复用平时的「···」。
        builder = self.script[self.script.index("function arrangeMenuElement("):]
        builder = builder[:builder.index("\n    }\n") + 6]
        self.assertIn("homeSiteMenuHtml(b, Number(id))", builder)
        self.assertIn("homeLinkMenuHtml(l, g)", builder)
        self.assertIn("'action-menu tile-menu arrange-menu'", builder)
        # 触屏上 .tile-menu 整个隐藏；编辑态的菜单要例外。
        self.assertIn(".tile-menu.arrange-menu { display: block; }", self.css)

    def test_arrange_tap_ignores_the_click_right_after_a_drag(self):
        start = self.script.index("// ===== 编辑首页")
        section = self.script[start:self.script.index("// ----- 自定义首页弹窗", start)]
        self.assertIn("ARRANGE.dropAt = Date.now();", section)
        self.assertIn("Date.now() - (ARRANGE.dropAt || 0) > 400", section)
        # 按在菜单上不开始拖。
        self.assertIn("e.target.closest('[data-arrange-remove], .arrange-menu')", section)

    def test_modal_close_button_only_on_small_screens(self):
        self.assertIn(".modal-x { display: none; }", self.css)
        small = self.css[self.css.index("  .modal-mask { align-items: flex-end; padding: 0; }"):]
        small = small[:small.index("  .modal, .modal.modal-wide {")]
        rule = re.search(r"\.modal-x \{([^}]*)\}", small).group(1)
        self.assertIn("display: inline-flex", rule)
        self.assertIn("width: 44px; height: 44px", rule)   # 触控区域不小于 44px
        self.assertIn("overscroll-behavior: contain", re.search(r"\n\.modal \{([^}]*)\}", self.css).group(1))
        fn = self.script[self.script.index("function addModalCloseButton(m)"):]
        fn = fn[:fn.index("\n    }\n")]
        self.assertIn("'.modal-actions [id$=\"Cancel\"], .modal-actions [id$=\"Close\"]'", fn)
        self.assertIn("omni-mask", fn)    # ⌘K 面板有自己的关闭方式

    def test_chip_row_scrolls_horizontally_only(self):
        fn = self.script[self.script.index("function syncSubnavScroll(reveal)"):]
        fn = fn[:fn.index("const nav = $('libSubnav');")]
        self.assertIn("chips.scrollLeft", fn)
        self.assertNotIn("scrollIntoView(", fn)


@unittest.skipIf(NODE is None, '未安装 node')
class ClosureScriptTest(unittest.TestCase):
    def run_js(self, assertions):
        responses = copy.deepcopy(RESPONSES)
        groups = responses['/api/configs']['link_groups']
        groups.append({'id': 'many', 'name': '多', 'color': 'mint', 'icon': 'folder', 'links': [
            {'id': f'm{i}', 'name': f'Zeta mirror {i}', 'url': f'https://z{i}.example', 'show_on_home': i < 2} for i in range(9)
        ] + [{'id': 'evil', 'name': '<img src=x onerror=1>', 'url': 'https://evil.example', 'show_on_home': True}]})
        script = '\n'.join([
            Path(STUB).read_text(),
            'globalThis.__RESPONSES = ' + json.dumps(responses) + ';',
            "const assert = require('node:assert/strict');",
            "document.querySelectorAll('.tab').forEach(t => { t.addEventListener = (name, fn) => { t[name] = fn; }; });",
            "{ const t = document.getElementById('navToggle'); t.addEventListener = (name, fn) => { t[name] = fn; }; }",
            _inline_script(),
            'setTimeout(async () => { try {', assertions,
            'assert.deepEqual(__CALLS.errors, []); assert.deepEqual(__CALLS.rejections, []);',
            "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, 30);",
        ])
        proc = subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_home_search_offers_see_all_beyond_the_limit(self):
        self.run_js("""
            let rows = homeSearchRows('zeta mirror');
            const items = rows.filter(r => r.type === 'item');
            assert.equal(items.length, HOME_SEARCH_LIMIT);
            const more = rows.find(r => r.type === 'more');
            assert.ok(more, '超出上限要给「查看全部」');
            assert.equal(more.count, omniSearch('zeta mirror').length);
            assert.equal(more.query, 'zeta mirror');
            // 紧跟在收藏结果后面、在「用 xx 搜索」之前；能被方向键选中。
            assert.equal(rows.indexOf(more), rows.lastIndexOf(items[items.length - 1]) + 1);
            assert.ok(homeRowSelectable(more));
            assert.ok(homeSearchRowHtml(more, 0).includes(`查看全部 <b>${more.count}</b> 条结果`));
            // 没超出时和原来一模一样。
            assert.ok(!homeSearchRows('zeta mirror 3').some(r => r.type === 'more'));
            // 选中这一行：收起首页下拉，带着原来的字打开 ⌘K 面板。
            let left = 0, opened = null;
            const realOpen = openOmni;
            openOmni = (q) => { opened = q; };
            try { runSearchRow(more, () => { left++; }); } finally { openOmni = realOpen; }
            assert.deepEqual([left, opened], [1, 'zeta mirror']);
        """)

    def test_typing_while_arranging_does_not_steal_focus(self):
        self.run_js("""
            assert.equal(homeSearchReady(), true);
            setArrange(true);
            assert.equal(homeSearchReady(), false);
            setArrange(false);
            assert.equal(homeSearchReady(), true);
        """)

    def test_link_modal_shows_delete_only_when_editing(self):
        self.run_js("""
            const g = STATE.link_groups.find(x => x.id === 'many');
            openLinkModal('many', 'm1', 'single');
            assert.equal($('linkDelete').hidden, false);
            assert.equal(linkEdit.lid, 'm1');
            openLinkModal('many', null, 'single');
            assert.equal($('linkDelete').hidden, true);
            openLinkModal('many', null, 'bulk');
            assert.equal($('linkDelete').hidden, true);
        """)

    def test_arrange_menu_reuses_the_tile_menu_and_escapes(self):
        self.run_js("""
            const link = arrangeMenuElement('many', 'evil');
            assert.ok(link.className.includes('arrange-menu') && link.className.includes('tile-menu'));
            assert.ok(link.innerHTML.includes("editLink('many','evil')"));
            assert.ok(link.innerHTML.includes("toggleHomeLink('many','evil')"));
            assert.ok(!link.innerHTML.includes('<img src=x'));
            assert.equal(arrangeMenuElement('many', 'nope'), null);
            assert.equal(arrangeMenuElement('@sites', '99'), null);
        """)


if __name__ == '__main__':
    unittest.main()

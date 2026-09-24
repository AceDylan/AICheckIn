# -*- coding: utf-8 -*-
"""收藏首页的版面与整理：统一列网格、右上角工具条、编辑首页（拖拽排序 / 移除）、自定义首页弹窗、侧栏分组段滚动。

前半是页面结构与样式护栏（读文件），后半把真实页面脚本放进 node 里执行。真机上的拖拽、触摸、截图由浏览器验证脚本覆盖。"""
import copy
import json
import re
import subprocess
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script

ROOT = Path(__file__).resolve().parents[1]


class HomeLayoutGuardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8")
        cls.script = _inline_script()

    def block(self, start, end):
        return self.html[self.html.index(start):self.html.index(end, self.html.index(start))]

    # ---- 工具条 ----

    def test_tools_live_with_the_clock_not_between_search_and_icons(self):
        top = self.block('<div class="home-top">', '<div class="home-search"')
        for needle in ('id="heroClock"', 'id="homeTools"', 'id="homeViewToggle"', 'id="homeLookBtn"', 'id="homeAddLink"', 'id="homeEditBtn"'):
            self.assertIn(needle, top)
        between = self.block('id="homeSearchBox"', 'id="homeBody"')
        self.assertNotIn('class="home-toolbar"', between)
        # 计数挪到页脚：纯展示的信息不占首屏。
        self.assertLess(self.html.index('id="homeBody"'), self.html.index('id="homeCount"'))
        self.assertIn('<div class="home-foot"><span class="toolbar-meta" id="homeCount"></span></div>', self.html)

    def test_tools_toggle_is_a_labelled_disclosure_button(self):
        self.assertRegex(self.html, r'<button type="button" class="home-tools-toggle" id="homeToolsToggle" aria-expanded="false" aria-controls="homeTools"[^>]*aria-label="首页设置"')
        self.assertIn("$('homeToolsToggle').setAttribute('aria-expanded', String(open));", self.script)
        # 图标按钮收起文字之后仍然有名字。
        for label in ('aria-label="极简"', 'aria-label="信息"', 'aria-label="卡片"', 'aria-label="外观"'):
            self.assertIn(label, self.block('id="homeTools"', '</div>\n            </div>'))

    def test_toolbar_is_collapsed_by_default_and_docks_by_container_width(self):
        """看的是主区宽度（侧栏收没收都会变）而不是视口：用容器查询；不支持容器查询的浏览器退回「⋯」展开。"""
        self.assertIn(".home-top { position: relative; container-type: inline-size; }", self.css)
        rule = re.search(r"\n\.home-toolbar \{([^}]*)\}", self.css).group(1)
        self.assertIn("display: none", rule)
        self.assertIn("flex-wrap: wrap", rule)      # 回归：放不下就换行，绝不把手机页面撑宽
        self.assertIn(".home-top.tools-open .home-toolbar { display: flex; }", self.css)
        docked = self.css[self.css.index("@container (min-width: 880px)"):self.css.index("@container (min-width: 1240px)")]
        self.assertIn(".home-tools-toggle { display: none; }", docked)
        self.assertRegex(docked, r"\.home-toolbar, \.home-top\.tools-open \.home-toolbar \{ position: absolute; top: 0; right: 0; display: flex;")

    # ---- 列网格 ----

    def test_icon_view_is_one_column_grid_shared_by_every_group(self):
        self.assertIn("grid-template-columns: repeat(var(--cols, 8), var(--tile-w)); justify-content: center;", self.css)
        self.assertIn(".home-list.is-tiles .home-section { grid-column: span var(--span, 4); min-width: 0; margin: 0; }", self.css)
        # 分组内的图标靠左起排（居中的话，标题和第一个图标就对不齐了）；标题缩进到图标的可见边缘。
        self.assertRegex(self.css, r"\n\.tile-grid \{[^}]*justify-content: start;")
        self.assertIn(".home-list.is-tiles .home-section .section-label { padding-inline: 18px; }", self.css)
        self.assertIn(".home-widget { padding-left: 18px; }", self.css)
        # 旧的「每个分组按自己的宽度各自居中」不能回来。
        self.assertNotIn("flex: 0 1 calc(max(var(--n", self.css)

    def test_icons_and_deck_are_centred_as_one_block(self):
        deck = self.css[self.css.index("首页组件（"):]
        rail = deck[deck.index("@media (min-width: 1100px)"):deck.index("@media (min-width: 1280px)")]
        full = deck[deck.index("@media (min-width: 1280px)"):deck.index("@media (min-width: 1440px)")]
        for block, prefix in ((rail, "html.nav-rail "), (full, "")):
            self.assertIn(prefix + ".home-body.has-deck.is-tiles { grid-template-columns: minmax(0, var(--grid-w, 1fr)) var(--deck-col); justify-content: center; }", block)

    def test_column_count_is_measured_not_guessed(self):
        self.assertIn("const HOME_GRID = { cols: 4, max: 10 };", self.script)
        self.assertIn("new ResizeObserver(relayoutHomeGrid).observe($('homeBody'))", self.script)
        self.assertIn("else window.addEventListener('resize', relayoutHomeGrid);", self.script)
        # 右侧组件那一列出现 / 消失，主区宽度跟着变，要重新量。
        self.assertIn("if (box.hidden !== wasHidden) relayoutHomeGrid();", self.script)

    # ---- 侧栏 ----

    def test_only_the_group_list_scrolls_in_the_sidebar(self):
        rule = re.search(r"\n\.subnav \{([^}]*)\}", self.css).group(1)
        for needle in ("flex: 0 1 auto", "min-height: 78px", "overflow-y: auto", "scrollbar-width: none"):
            self.assertIn(needle, rule)
        self.assertIn(".subnav > * { flex-shrink: 0; }", self.css)
        self.assertRegex(self.css, r"\.subnav\.fade-bottom \{[^}]*mask-image: linear-gradient")
        self.assertIn("queueSubnavSync(true);", self.script)   # 重画侧栏后下一帧再量，不当场逼出排版

    # ---- 编辑首页 ----

    def test_arrange_bar_markup(self):
        bar = self.block('id="homeArrangeBar"', '</div>')
        self.assertIn('role="region" aria-label="编辑首页" hidden', bar)
        for needle in ('id="homeArrangeTip"', 'id="chooseHomeLinks"', 'id="homeArrangeDone"'):
            self.assertIn(needle, bar)
        self.assertIn('<div class="sr-only" id="homeArrangeLive" aria-live="polite"></div>', self.html)
        self.assertRegex(self.html, r'id="homeEditBtn"[^>]*aria-pressed="false"')

    def test_arrange_styles_are_calm_and_touch_friendly(self):
        start = self.css.index("编辑首页：拖动排序 / 移除")
        arrange = self.css[start:self.css.index("/* 极简密度", start)]
        self.assertNotIn("backdrop-filter", arrange)
        self.assertNotIn("@keyframes", arrange)               # 不做「抖动」：晃眼，也违背减少动态效果
        self.assertIn("-webkit-touch-callout: none", arrange)  # 长按图标不弹系统菜单
        self.assertIn(".is-arranging .home-tile[data-arrange-id] img { pointer-events: none;", arrange)
        self.assertIn('.tile-remove::after { content: ""; position: absolute; inset: -8px; }', arrange)
        self.assertIn(".home-list.is-sorting .tile-grid:not(.is-drag-scope) .home-tile { opacity: .32;", arrange)
        self.assertIn("@media (forced-colors: active)", arrange)
        self.assertIn("position: sticky", re.search(r"\.home-arrange-bar \{([^}]*)\}", arrange).group(1))

    def test_arrange_script_guards(self):
        start = self.script.index("// ===== 编辑首页")
        section = self.script[start:self.script.index("// ----- 自定义首页弹窗", start)]
        for banned in ("localStorage", "sessionStorage", "setInterval", "eval(", "innerHTML"):
            self.assertNotIn(banned, section)
        self.assertNotRegex(section, r"https?://")
        # 只打这两个既有的排序接口，而且都要带管理权限。
        self.assertEqual(sorted(set(re.findall(r"url = [`'](/api/[^`']+)[`']", section))),
                         ["/api/bookmarks/reorder", "/api/link_groups/${encodeURIComponent(group)}/links/reorder"])
        self.assertIn("if (on && !guardAdmin()) return;", section)
        # 手指要先按住再拖；拖起来之后才拦 touchmove（平时滑动照常滚动页面），监听只在编辑期间挂着。
        self.assertIn("const ARRANGE_HOLD_MS = 240;", section)
        self.assertIn("function onArrangeTouchMove(e) { if (ARRANGE.active && e.cancelable) e.preventDefault(); }", section)
        self.assertIn("list.addEventListener('touchmove', onArrangeTouchMove, { passive: false });", section)
        self.assertIn("else list.removeEventListener('touchmove', onArrangeTouchMove);", section)
        self.assertIn("window.matchMedia('(prefers-reduced-motion: reduce)').matches", section)

    # ---- 自定义首页弹窗 ----

    def test_picker_has_fixed_head_and_foot_and_a_filter(self):
        modal = self.block('id="homeModal"', '<div class="modal-mask" id="groupModal">')
        self.assertIn('class="modal modal-wide home-picker" role="dialog" aria-modal="true" aria-labelledby="homeModalTitle"', modal)
        for marker in ('id="homeFilter" type="search"', 'aria-label="筛选网址"', "data-1p-ignore", 'data-lpignore="true"', "data-bwignore", 'data-form-type="other"'):
            self.assertIn(marker, modal)
        self.assertLess(modal.index('id="homeFilter"'), modal.index('id="homeChoices"'))
        self.assertLess(modal.index('id="homeChoices"'), modal.index('id="homeSave"'))
        self.assertIn(".modal.home-picker { display: flex; flex-direction: column; overflow: hidden; }", self.css)
        self.assertRegex(self.css, r"\.picker-list \{[^}]*overflow-y: auto")
        mobile = self.css[self.css.index("@media (max-width: 760px)"):]
        self.assertIn(".picker-bar .input { font-size: 16px; }", mobile)
        self.assertIn(".picker-rows { grid-template-columns: 1fr; }", mobile)

    def test_picker_saves_hidden_rows_too_and_bulk_actions_respect_the_filter(self):
        self.assertIn("const HOME_PICK = '[data-home-link], [data-home-site]';", self.script)
        # 保存：按勾选框本身取（筛选只是藏行），组头的「全选」框不算一条网址。
        self.assertIn("Array.from($('homeChoices').querySelectorAll(HOME_PICK)).filter(input => input.checked)", self.script)
        self.assertIn("homePickRows(null, true).forEach(row => { row.querySelector('input').checked = checked; });", self.script)


@unittest.skipIf(NODE is None, '未安装 node')
class HomeLayoutScriptTest(unittest.TestCase):
    def run_js(self, assertions, cookie=""):
        responses = copy.deepcopy(RESPONSES)
        cfg = responses['/api/configs']
        cfg['link_groups'][0]['links'][0].update(show_on_home=True)
        cfg['bookmarks'] = [
            {'name': '看板甲', 'url': 'https://a.example', 'show_on_home': True, 'fields': [
                {'id': 'f1', 'label': '余额', 'type': 'amount', 'enabled': True, 'value': '1.5'}]},
            {'name': '不上首页', 'url': 'https://skip.example', 'show_on_home': False, 'fields': []},
            {'name': '看板乙', 'url': 'https://b.example', 'show_on_home': True, 'fields': []},
        ]
        cfg['link_groups'].append({'id': 'big', 'name': '大<b>组</b>', 'color': 'sky', 'icon': 'folder', 'links': [
            {'id': 'k%d' % i, 'name': '站点%d' % i, 'url': 'https://s%d.example' % i, 'show_on_home': i != 2} for i in range(12)]})
        script = '\n'.join([
            Path(STUB).read_text(),
            'globalThis.__RESPONSES = ' + json.dumps(responses) + ';',
            'document.cookie = ' + json.dumps(cookie) + ';',
            "const assert = require('node:assert/strict');",
            "document.querySelectorAll('.tab').forEach(t => { t.addEventListener = (name, fn) => { t[name] = fn; }; });",
            "{ const t = document.getElementById('navToggle'); t.addEventListener = (name, fn) => { t[name] = fn; }; }",
            _inline_script(),
            "const SENT = []; { const real = globalThis.fetch; globalThis.fetch = (url, opts) => { SENT.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null }); return real(url, opts); }; }",
            "const tick = () => new Promise(r => setTimeout(r, 5));",
            'setTimeout(async () => { try {', assertions,
            'assert.deepEqual(__CALLS.errors, []); assert.deepEqual(__CALLS.rejections, []);',
            "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, 30);",
        ])
        proc = subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_group_span_is_capped_by_the_measured_column_count(self):
        self.run_js("""
            // 没量过（首页没显示 / 没有样式系统）：按手机的 4 列兜底。
            let html = $('homeList').innerHTML;
            assert.ok(html.includes('data-units="11" style="--n:11;--span:4"'));
            assert.ok(html.includes('data-units="3" style="--n:3;--span:3"'));     // 小组件 2 格 + 普通图标 1 格
            setHomeCols(8); renderHome();
            html = $('homeList').innerHTML;
            assert.ok(html.includes('--n:11;--span:8') && html.includes('--n:3;--span:3') && html.includes('--n:1;--span:1'));
            assert.equal($('homeBody').style['--cols'], '8');
        """)

    def test_measure_leaves_room_for_the_deck_column(self):
        self.run_js("""
            const vars = { '--tile-w': '96px', '--tile-gap': '10px' };
            let side = true;
            globalThis.getComputedStyle = (el) => ({ getPropertyValue: k => vars[k] || '', display: el === $('homeBody') && side ? 'grid' : 'flex', columnGap: '32px' });
            $('homeBody').clientWidth = 1278; $('homeDeck').offsetWidth = 320; $('homeDeck').hidden = false;
            measureHomeGrid();
            assert.equal(HOME_GRID.cols, 8);                                 // (1278 − 320 − 32 + 10) / 106 = 8.8
            assert.equal($('homeBody').style['--grid-w'], '838px');
            vars['--deck-col'] = '300px'; measureHomeGrid();                   // 有样式变量时以它为准（那一列的 offsetWidth 含借来的外边距）
            assert.equal(HOME_GRID.cols, 9);                                 // (1278 − 300 − 32 + 10) / 106 = 9.02
            delete vars['--deck-col'];
            side = false; measureHomeGrid();                                   // 组件叠在上方：整行都给图标，但不超过 10 列
            assert.equal(HOME_GRID.cols, 10);
            assert.equal($('homeBody').style['--grid-w'], '1050px');
            $('homeBody').clientWidth = 200; measureHomeGrid();
            assert.equal(HOME_GRID.cols, 3);                                  // 再窄也留 3 列
            globalThis.matchMedia = (q) => ({ matches: q === HOME_PHONE_QUERY, addEventListener() {} });
            measureHomeGrid();
            assert.equal(HOME_GRID.cols, 4);                                  // 手机固定 4 列，格子宽度随屏
            assert.equal($('homeBody').style['--grid-w'], '');
        """)

    def test_arrange_mode_renders_draggable_non_link_tiles(self):
        self.run_js("""
            assert.ok($('homeList').innerHTML.includes('href="https://s0.example"'));
            setArrange(true);
            const html = $('homeList').innerHTML;
            assert.equal(ARRANGE.on, true);
            assert.equal($('homeArrangeBar').hidden, false);
            assert.ok($('homePage').classList.contains('is-arranging'));
            assert.ok(html.includes('data-arrange-group="big"') && html.includes('data-arrange-group="@sites"'));
            assert.ok(html.includes('data-arrange-id="k0"') && html.includes('data-arrange-id="0"') && html.includes('data-arrange-id="2"'));
            assert.ok(!html.includes('data-arrange-id="k2"') && !html.includes('data-arrange-id="1"'));   // 没上首页的不出现
            assert.ok(!html.includes('href=') && !html.includes('target="_blank"'));                         // 点了不跳转，长按不弹链接菜单
            assert.ok(html.includes('data-arrange-remove') && !html.includes('tile-menu'));
            assert.ok(html.includes('tabindex="0" role="group"'));
            assert.ok(html.includes('aria-label="从首页移除 站点0"'));
            setArrange(false);
            assert.equal($('homeArrangeBar').hidden, true);
            assert.ok($('homeList').innerHTML.includes('href="https://s0.example"') && $('homeList').innerHTML.includes('tile-menu'));
        """)

    def test_arrange_mode_escapes_names_and_forces_the_icon_view(self):
        self.run_js("""
            STATE.link_groups[1].links[0].name = '"><img src=x onerror=alert(1)>';
            setArrange(true);
            const html = $('homeList').innerHTML;
            assert.equal($('homeList').className, 'home-list is-tiles');         // 卡片密度下临时按图标画
            assert.ok(!html.includes('<img src=x'));
            assert.ok(html.includes('aria-label="&quot;&gt;&lt;img src=x onerror=alert(1)&gt;：方向键调整顺序'));
            assert.ok(html.includes('aria-label="大&lt;b&gt;组&lt;/b&gt;"'));
            setArrange(false);
            assert.equal($('homeList').className, 'home-list is-cards');
        """, cookie="bh_home_view=cards")

    def test_arrange_needs_admin_and_ends_when_leaving_home(self):
        self.run_js("""
            STATE.admin_required = true; STATE.admin_unlocked = false;
            setArrange(true);
            assert.equal(ARRANGE.on, false);
            STATE.admin_unlocked = true;
            setArrange(true);
            assert.equal(ARRANGE.on, true);
            openLibPage('monitor');
            assert.equal(ARRANGE.on, false);
            assert.equal($('homeArrangeBar').hidden, true);
            openLibPage('@home'); setArrange(true); switchView('settings');
            assert.equal(ARRANGE.on, false);
        """)

    def test_home_order_is_merged_back_into_the_full_group_order(self):
        self.run_js("""
            assert.deepEqual(mergeHomeOrder(['a', 'b', 'c', 'd'], x => x !== 'b', ['d', 'a', 'c']), ['d', 'b', 'a', 'c']);
            const g = findGroup('big'), before = g.links.map(l => l.id);
            assert.equal(before[2], 'k2');                                           // k2 没上首页
            const shown = before.filter(id => id !== 'k2');
            const wanted = [shown[3]].concat(shown.filter((_, i) => i !== 3));       // 把第 4 个拖到最前
            const focus = persistArrange('big', wanted, shown[3]);
            assert.deepEqual(focus, { group: 'big', id: 'k4' });
            const after = findGroup('big').links.map(l => l.id);
            assert.equal(after[2], 'k2');                                            // 没上首页的原地不动
            assert.deepEqual(after.filter(id => id !== 'k2'), wanted);
            await tick();
            assert.deepEqual(SENT[SENT.length - 1], { url: '/api/link_groups/big/links/reorder', body: { order: after } });
            // 过期 / 对不上的顺序：不改本地，也不发请求。
            const n = SENT.length;
            assert.equal(persistArrange('big', ['k0', 'nope'], 'k0'), null);
            assert.equal(persistArrange('gone', ['x'], 'x'), null);
            assert.equal(SENT.length, n);
            assert.deepEqual(findGroup('big').links.map(l => l.id), after);
        """)

    def test_dashboard_sites_reorder_by_index_and_focus_follows_the_new_index(self):
        self.run_js("""
            // 首页上是下标 0、2 两个站点；把「看板乙」(2) 拖到「看板甲」(0) 前面，中间那个不上首页的原地不动。
            const focus = persistArrange('@sites', ['2', '0'], '2');
            assert.deepEqual(STATE.bookmarks.map(b => b.name), ['看板乙', '不上首页', '看板甲']);
            assert.deepEqual(focus, { group: '@sites', id: '0' });
            await tick();
            assert.deepEqual(SENT[SENT.length - 1], { url: '/api/bookmarks/reorder', body: { order: [2, 1, 0] } });
            assert.equal(persistArrange('@sites', ['7', '0'], '7'), null);
        """)

    def test_a_failed_reorder_reloads_the_authoritative_state(self):
        self.run_js("""
            __RESPONSES['/api/link_groups/big/links/reorder'] = { ok: false, error: '排序参数无效' };
            __RESPONSES['/api/configs'] = JSON.parse(JSON.stringify(STATE));   // 桩里 STATE 和响应是同一个对象：先给「服务端」留一份独立的副本
            const n = SENT.filter(r => r.url === '/api/configs').length;
            persistArrange('big', findGroup('big').links.filter(l => l.show_on_home).map(l => l.id).reverse(), 'k0');
            await tick(); await tick();
            assert.equal(SENT.filter(r => r.url === '/api/configs').length, n + 1);
            assert.equal(findGroup('big').links[0].id, 'k0');                         // 回到服务端的顺序
        """)

    def test_picker_rows_are_escaped_searchable_and_grouped(self):
        self.run_js("""
            STATE.link_groups[1].links[1].name = 'Evil"><script>x</script>';
            openHomeModal();
            const html = $('homeChoices').innerHTML;
            assert.ok(!html.includes('<script>x'));
            assert.ok(html.includes('data-pick-text="evil&quot;&gt;&lt;script&gt;x&lt;/script&gt; https://s1.example"'));
            assert.ok(html.includes('data-home-all="big"') && html.includes('data-home-all="@sites"'));
            assert.ok(html.includes('data-pick-name="大&lt;b&gt;组&lt;/b&gt;"'));
            assert.ok(html.includes('data-home-group="big" data-home-link="k2">'));              // 没上首页的：未勾选
            assert.ok(html.includes('data-home-group="big" data-home-link="k0" checked>'));
            assert.equal($('homeFilter').value, '');
            assert.equal($('homeOnlyChecked').checked, false);
        """)


if __name__ == "__main__":
    unittest.main()

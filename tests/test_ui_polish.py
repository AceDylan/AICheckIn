# -*- coding: utf-8 -*-
"""界面打磨的护栏：小字对比度、壁纸场景里的浮层、键盘弹起时的搜索下拉、手机时钟行不换行、图标栏的即时提示。"""
import copy
import json
import re
import subprocess
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script

ROOT = Path(__file__).resolve().parents[1]


def _luminance(hex_color):
    channels = [int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class UiPolishStyleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8")
        cls.html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        cls.dark = re.search(r":root, html\.wall-on \.modal-mask[^{]*\{([^}]*)\}", cls.css).group(1)
        cls.light = re.search(r'html\[data-theme="light"\] \{([^}]*)\}', cls.css).group(1)

    @staticmethod
    def token(block, name):
        return re.search(r"--%s: (#[0-9a-fA-F]{6});" % re.escape(name), block).group(1)

    def test_small_secondary_text_meets_aa_on_every_resting_surface(self):
        # --text-3 用在 11–12px 的域名 / 副标题 / 快捷键提示上，属于「小号正文」：AA 要 4.5:1。
        # 深色主题曾用 #64748b，在 --surface 上只有 3.7:1。（--surface-hover 是悬停瞬态，不在此列。）
        for name, block in (("dark", self.dark), ("light", self.light)):
            fg = self.token(block, "text-3")
            for surface in ("bg", "surface"):
                self.assertGreaterEqual(contrast(fg, self.token(block, surface)), 4.5, (name, surface))
        for surface in ("surface-2", "surface-input"):
            self.assertGreaterEqual(contrast(self.token(self.dark, "text-3"), self.token(self.dark, surface)), 4.5, surface)
        # 层级还在：次要文字不能比 --text-2 更亮。
        self.assertLess(contrast(self.token(self.dark, "text-3"), self.token(self.dark, "bg")),
                        contrast(self.token(self.dark, "text-2"), self.token(self.dark, "bg")))

    def test_overlays_stay_dark_inside_the_wallpaper_scene(self):
        # 浮层在 .app-shell 之外：浅色主题 + 壁纸时曾从深色场景里弹出一块纯白弹窗。
        # 深色颜色令牌直接声明在浮层自己身上（压过 <html> 继承来的浅色值），尺寸 / 字体令牌不重复声明。
        selector = re.search(r"(:root, html\.wall-on \.modal-mask[^{]*)\{", self.css).group(1)
        for overlay in ("html.wall-on .modal-mask", "html.wall-on .toast-wrap", "html.wall-on .todo-sort-menu"):
            self.assertIn(overlay, selector)
        for decl in ("color-scheme: dark", "--surface: #151519", "--text: #f4f4f6", "--scrim:", "--toast-bg:", "--shadow-pop:"):
            self.assertIn(decl, self.dark)
        for layout_token in ("--sidebar-w", "--radius", "--font"):
            self.assertNotIn(layout_token, self.dark)   # 重新声明会盖掉 html.nav-rail / 媒体查询里的值
        # color 是继承来的计算值，要在浮层上重新取；分组色调在弹窗里也回到深色那一组，优先级高过浅色覆盖。
        self.assertIn("html.wall-on .modal-mask, html.wall-on .toast-wrap, html.wall-on .todo-sort-menu { color: var(--text); }", self.css)
        for tone in ("mint", "sky", "violet", "amber", "rose", "slate"):
            self.assertIn(".tone-%s, html.wall-on .modal-mask .tone-%s { --tone:" % (tone, tone), self.css)
        # 每个浮层确实都在 .app-shell 之外，设置页把这件事说清楚。
        for overlay in ('class="toast-wrap"', 'class="todo-sort-menu"'):
            pos = self.html.index(overlay)
            self.assertFalse(self.html.index('<div class="app-shell">') < pos < self.html.index("</main>"), overlay)
        self.assertIn("启用壁纸时，整个界面（含弹窗）固定为深色场景", self.html)

    def test_search_dropdown_is_capped_by_the_visual_viewport(self):
        # 虚拟键盘只缩小可视视口：脚本量出剩余高度写进 --dd-max，样式取小。
        rule = re.search(r"\.home-search-dropdown \{([^}]*)\}", self.css).group(1)
        self.assertIn("max-height: min(60vh, 440px, var(--dd-max, 440px));", rule)
        self.assertIn("overscroll-behavior: contain", rule)
        # 带 var() 的声明遇到不认识的单位会在计算期整条作废（退成 none）：dvh 版本必须包在 @supports 里。
        self.assertIn("@supports (height: 1dvh) { .home-search-dropdown { max-height: min(60dvh, 440px, var(--dd-max, 440px)); } }", self.css)
        self.assertEqual(len(re.findall(r"[0-9]dvh", self.css.replace("@supports (height: 1dvh)", ""))), 1)
        block = self.html[self.html.index("function fitHomeSearchList()"):self.html.index("function clearHomeSearch()")]
        for needle in ("window.visualViewport", "vv.offsetTop + vv.height", "getBoundingClientRect().bottom", "setProperty('--dd-max'",
                       "for (const type of ['resize', 'scroll']) window.visualViewport.addEventListener(type, fitHomeSearchList)"):
            self.assertIn(needle, block)
        self.assertIn("if (!vv || $('homeSearchList').hidden) return;", block)   # 老浏览器没有 visualViewport：保持原上限
        self.assertNotIn("setInterval", block)

    def test_mobile_clock_row_never_wraps(self):
        mobile = self.css[self.css.index("/* 起始页在手机上"):self.css.index(".home-search { margin-top: 12px; }")]
        meta = re.search(r"\.hero-meta \{([^}]*)\}", mobile).group(1)
        for decl in ("display: block", "min-width: 0", "overflow: hidden", "white-space: nowrap", "text-overflow: ellipsis"):
            self.assertIn(decl, meta)
        self.assertIn(".hero-clock { flex: 0 0 auto;", mobile)
        # 极窄屏先收星期，问候和日期留全；星期因此单独成段。
        self.assertIn("@media (max-width: 359px) { .hero-weekday { display: none; } }", self.css)
        self.assertIn('<span class="hero-date"><time id="heroDate"></time> <span class="hero-weekday" id="heroWeekday"></span></span>', self.html)
        self.assertIn("$('heroWeekday').textContent = WEEKDAYS[now.getDay()];", self.html)

    def test_rail_tip_is_a_body_level_fixed_hint(self):
        # 侧栏的导航是滚动区，伸出去的伪元素会被裁掉：提示必须是 <body> 下的 fixed 节点，且不接收指针。
        self.assertLess(self.html.index('id="railTip"'), self.html.index('<div class="app-shell">'))
        self.assertIn('<div class="rail-tip" id="railTip" aria-hidden="true" hidden></div>', self.html)
        rule = re.search(r"\.rail-tip \{([^}]*)\}", self.css).group(1)
        for decl in ("position: fixed", "pointer-events: none", "white-space: nowrap", "text-overflow: ellipsis"):
            self.assertIn(decl, rule)
        self.assertNotIn("backdrop-filter", rule)
        self.assertIn(".rail-tip[hidden] { display: none; }", self.css)
        # 固定深底浅字：不依赖主题令牌，壁纸 / 浅色页面上都 ≥ 4.5:1。
        self.assertGreaterEqual(contrast("#f4f4f6", "#1b1b20"), 4.5)
        block = self.html[self.html.index("const RAIL_TIP = "):self.html.index("// ===== Toast =====")]
        for needle in ("e.pointerType !== 'touch'", "t.matches(':focus-visible')", "bar.addEventListener('focusout', hideRailTip)",
                       "!e.buttons", "railTipKey(t) === RAIL_TIP.mute", "bar.addEventListener('scroll', hideRailTip, true)",
                       "if (e.key === 'Escape') hideRailTip();", "text.offsetParent !== null"):
            self.assertIn(needle, block)
        self.assertNotIn("setInterval", block)
        self.assertNotIn("innerHTML", block)   # 分组名是用户数据：只走 textContent


@unittest.skipIf(NODE is None, '未安装 node')
class RailTipBehaviourTest(unittest.TestCase):
    def run_js(self, assertions):
        script = '\n'.join([
            Path(STUB).read_text(),
            'globalThis.__RESPONSES = ' + json.dumps(copy.deepcopy(RESPONSES)) + ';',
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

    def test_tip_shows_only_for_collapsed_labels_and_restores_the_native_title(self):
        self.run_js("""
            const sleep = (ms) => new Promise(r => setTimeout(r, ms));
            function item(attrs, text, textVisible) {
                const a = { ...attrs };
                return { dataset: {}, isConnected: true, attrs: a,
                    getAttribute: (k) => (k in a ? a[k] : null), hasAttribute: (k) => k in a,
                    setAttribute: (k, v) => { a[k] = String(v); }, removeAttribute: (k) => { delete a[k]; },
                    querySelector: () => (text === null ? null : { textContent: text, offsetParent: textVisible ? {} : null }),
                    getBoundingClientRect: () => ({ top: 100, right: 60, height: 36 }) };
            }
            const tip = $('railTip');
            tip.hidden = true;
            // 图标栏：文字被收起 → 出提示；名字只走 textContent（不解析 HTML）
            const group = item({ title: '<b>开发</b>｜按住拖动可调整顺序（Alt+↑/↓）' }, '<b>开发</b>', false);
            showRailTip(group, 0);
            assert.equal(group.attrs.title, undefined);                       // 原生 title 暂存，不会一秒后再叠一层
            assert.equal(group.attrs['aria-label'], '<b>开发</b>｜按住拖动可调整顺序（Alt+↑/↓）');   // 可访问名称还在
            await sleep(5);
            assert.equal(tip.hidden, false);
            assert.equal(tip.textContent, '<b>开发</b>');
            assert.equal(tip.innerHTML, '');
            assert.equal(tip.style.left, '70px');
            assert.equal(tip.style.top, '118px');
            hideRailTip();
            assert.equal(tip.hidden, true);
            assert.equal(group.attrs.title, '<b>开发</b>｜按住拖动可调整顺序（Alt+↑/↓）');
            assert.ok(!('aria-label' in group.attrs) && !group.dataset.railTitle && !group.dataset.railAria);
            // 自带 aria-label 的入口：还原时不能把它删掉
            const tab = item({ title: '收藏库', 'aria-label': '收藏库' }, '收藏库', false);
            showRailTip(tab, 0); await sleep(5); hideRailTip();
            assert.deepEqual(tab.attrs, { title: '收藏库', 'aria-label': '收藏库' });
            // 完整侧栏 / 手机底栏：文字看得见 → 不出提示、不动 title
            const full = item({ title: '收藏库' }, '收藏库', true);
            showRailTip(full, 0); await sleep(5);
            assert.equal(tip.hidden, true);
            assert.equal(full.attrs.title, '收藏库');
            // 悬停延时内就移开 / 节点被重绘换掉：不出提示
            showRailTip(group, 20); hideRailTip(); await sleep(30);
            assert.equal(tip.hidden, true);
            const gone = item({ title: 'x' }, 'x', false);
            showRailTip(gone, 0); gone.isConnected = false; await sleep(5);
            assert.equal(tip.hidden, true);
            // 导航重绘会收起提示
            showRailTip(group, 0); await sleep(5);
            assert.equal(tip.hidden, false);
            renderLibNav();
            assert.equal(tip.hidden, true);
            assert.equal(railTipLabel(item({ 'aria-label': '全局搜索' }, null)), '全局搜索');
        """)


if __name__ == "__main__":
    unittest.main()

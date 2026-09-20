# -*- coding: utf-8 -*-
"""收藏首页的三样小便利：分组折叠、待办里的网址可点、「常用」一行。

前半是页面结构与样式护栏（读文件），后半把真实页面脚本放进 node 里执行。
页面脚本的桩里 document.cookie 只是个字符串，这里换成一个最小的 Cookie 罐，才能同时存几个偏好。"""
import copy
import json
import re
import subprocess
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script

ROOT = Path(__file__).resolve().parents[1]

COOKIE_JAR = """
{ const jar = new Map();
  Object.defineProperty(document, 'cookie', { configurable: true,
    get() { return Array.from(jar.entries()).map(([k, v]) => k + '=' + v).join('; '); },
    set(text) { const first = String(text).split(';')[0], at = first.indexOf('='); if (at < 0) return;
      const name = first.slice(0, at).trim(), value = first.slice(at + 1);
      if (/max-age=0/.test(text)) jar.delete(name); else jar.set(name, value); } }); }
"""


class HomeExtrasGuardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8")
        cls.script = _inline_script()

    def test_fold_button_is_a_real_disclosure_control(self):
        for attr in ('class="section-fold" data-home-fold="${pid}"', 'aria-expanded="${!o.folded}"', 'aria-controls="homeSec-${pid}"',
                     "aria-label=\"${o.folded ? '展开' : '折叠'}「${name}」\""):
            self.assertIn(attr, self.script)
        # 折叠后内容不渲染（不去取那一组的图标），但被 aria-controls 指着的容器还在。
        self.assertIn("""<div class="section-body"${o.plain ? '' : ` id="homeSec-${pid}"`}${o.folded ? ' hidden' : ''}>${o.folded ? '' : body}</div>""", self.script)

    def test_fold_styles(self):
        self.assertIn(".home-section .section-label::after { order: 1; }", self.css)          # 按钮排在分隔线之后
        self.assertRegex(self.css, r"\.section-fold \{[^}]*order: 2;")
        self.assertIn('.section-fold::after { content: ""; position: absolute; inset: -9px; }', self.css)   # 触摸目标
        self.assertIn(".section-fold:focus-visible { outline: 2px solid var(--accent);", self.css)
        self.assertIn("@media (hover: none) { .section-fold { opacity: .85; } }", self.css)
        self.assertIn("@media (prefers-reduced-motion: reduce) { .section-fold, .section-fold .ic { transition: none; } }", self.css)
        self.assertIn("@media (forced-colors: active) { .section-fold { opacity: 1; } }", self.css)
        self.assertIn(".home-list.is-minimal .home-section, .home-list.is-minimal .section-body, .home-list.is-minimal .tile-grid { display: contents; }", self.css)

    def test_fold_and_frequent_preferences_live_in_whitelisted_cookies(self):
        self.assertIn(r"document.cookie.match(/(?:^|; )bh_home_fold=([A-Za-z0-9_.-]{1,1400})(?:;|$)/)", self.script)
        self.assertIn(r"document.cookie.match(/(?:^|; )bh_home_hits=([0-9]{1,6}(?:\.[a-z0-9]{1,8}-[0-9]{1,4}){0,24})(?:;|$)/)", self.script)
        self.assertIn("const HOME_FREQ_PREFS = ['on', 'off'];", self.script)
        self.assertIn("readPref('bh_home_freq', HOME_FREQ_PREFS, 'on')", self.script)

    def test_frequent_row_controls_are_in_the_look_modal(self):
        modal = self.html[self.html.index('id="homeLookModal"'):self.html.index('id="homeLookClose"')]
        self.assertRegex(modal, r'<div class="segmented" id="homeFreqSeg" role="radiogroup" aria-labelledby="homeFreqLabel">')
        self.assertIn('data-home-freq="on"', modal)
        self.assertIn('data-home-freq="off"', modal)
        self.assertIn('id="homeFreqClear"', modal)
        self.assertIn("不含网址本身", modal)
        # 关掉 = 不想被记：连已有记录一起清。
        self.assertIn("if (btn.dataset.homeFreq === 'off') clearHomeHits();", self.script)

    def test_hits_are_counted_on_click_and_middle_click_without_rerendering(self):
        self.assertIn("document.addEventListener('click', onHitClick, true);", self.script)
        self.assertIn("document.addEventListener('auxclick', onHitClick, true);", self.script)
        self.assertIn("if (e.type === 'auxclick' && e.button !== 1) return;", self.script)
        body = self.script[self.script.index("function onHitClick"):self.script.index("document.addEventListener('click', onHitClick")]
        self.assertNotIn("renderHome", body)
        self.assertNotIn("fetch(", body)                     # 纯本机：点了什么不上报服务端

    def test_todo_links_open_safely(self):
        helper = self.script[self.script.index("const LINKIFY_RE"):self.script.index("function siteHost")]
        self.assertIn('target="_blank" rel="noopener noreferrer nofollow" draggable="false"', helper)
        self.assertIn("const url = safeUrl(trimUrlTail(m[0]));", helper)
        self.assertIn("escapeHtml(linkLabel(parsed))", helper)
        self.assertEqual(helper.count("escapeHtml("), 5)     # 前文、href、title、显示文字、尾巴——拼进 HTML 的每一段都转义
        self.assertIn("${linkifyHtml(t.text)}</label>", self.script)
        # 在链接上按下是要点它，不是要拖这一行。
        self.assertIn("e.target.closest('.todo-actions, a')", self.script)

    def test_todo_link_styles(self):
        self.assertRegex(self.css, r"\.todo-link \{[^}]*text-decoration: underline;[^}]*overflow-wrap: anywhere;")
        self.assertIn(".todo-link:focus-visible { outline: 2px solid var(--accent);", self.css)
        todo = self.css[self.css.index("首页待办"):]
        mobile = todo[todo.rindex("@media (max-width: 760px)"):]
        self.assertIn(".todo-link { padding-block: 5px; }", mobile)


@unittest.skipIf(NODE is None, '未安装 node')
class HomeExtrasScriptTest(unittest.TestCase):
    def run_js(self, assertions, cookie=""):
        responses = copy.deepcopy(RESPONSES)
        cfg = responses['/api/configs']
        cfg['link_groups'][0]['links'][0].update(show_on_home=True)
        cfg['bookmarks'] = [{'name': '看板甲', 'url': 'https://a.example', 'show_on_home': True, 'fields': []}]
        cfg['link_groups'].append({'id': 'big', 'name': '大<b>组</b>', 'color': 'sky', 'icon': 'folder', 'links': [
            {'id': 'k%d' % i, 'name': '站点%d' % i, 'url': 'https://s%d.example' % i, 'show_on_home': i != 2} for i in range(12)]})
        cfg['todos'] = []
        cfg['todos_locked'] = False
        script = '\n'.join([
            Path(STUB).read_text(), COOKIE_JAR,
            'globalThis.__RESPONSES = ' + json.dumps(responses) + ';',
            ''.join('document.cookie = %s;' % json.dumps(c) for c in cookie.split('; ') if c),
            "const assert = require('node:assert/strict');",
            "document.querySelectorAll('.tab').forEach(t => { t.addEventListener = (name, fn) => { t[name] = fn; }; });",
            "{ const t = document.getElementById('navToggle'); t.addEventListener = (name, fn) => { t[name] = fn; }; }",
            _inline_script(),
            "const hit = (u, n) => { for (let i = 0; i < n; i++) recordHit(u); };",
            "const html = () => $('homeList').innerHTML;",
            "const today = Math.floor(Date.now() / 86400000);",
            'setTimeout(async () => { try {', assertions,
            'assert.deepEqual(__CALLS.errors, []); assert.deepEqual(__CALLS.rejections, []);',
            "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, 30);",
        ])
        proc = subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    # ---- 待办里的网址 ----

    def test_linkify_only_links_http_urls_and_escapes_everything_else(self):
        self.run_js(r"""
            assert.equal(linkifyHtml('<b>x</b> & "y"'), '&lt;b&gt;x&lt;/b&gt; &amp; &quot;y&quot;');
            for (const bad of ['javascript:alert(1)', 'data:text/html,<script>1</script>', 'JaVaScRiPt://x', 'ftp://a.example', '//a.example', 'vbscript:x'])
                assert.ok(!linkifyHtml(bad).includes('<a '), bad);
            const one = linkifyHtml('看 https://a.example/x?y=1&z=2 再说');
            assert.equal(one, '看 <a class="todo-link" href="https://a.example/x?y=1&amp;z=2" target="_blank" rel="noopener noreferrer nofollow" draggable="false" title="https://a.example/x?y=1&amp;z=2">a.example/x?…</a> 再说');
            // 想从属性里逃出来：网址在引号 / 尖括号处就停了，剩下的当文字转义。
            const evil = linkifyHtml('https://a.example/"onmouseover="alert(1) <img src=x onerror=1>');
            assert.equal((evil.match(/<a /g) || []).length, 1);
            assert.ok(evil.includes('href="https://a.example/"') && evil.includes('&quot;onmouseover=&quot;alert(1) &lt;img src=x'));
            assert.ok(!/<img|onerror="|onmouseover="/.test(evil));
        """)

    def test_linkify_handles_chinese_text_punctuation_and_deception(self):
        self.run_js(r"""
            const hrefs = (s) => Array.from(linkifyHtml(s).matchAll(/href="([^"]+)"/g)).map(m => m[1]);
            const labels = (s) => Array.from(linkifyHtml(s).matchAll(/>([^<]+)<\/a>/g)).map(m => m[1]);
            // 中文里网址后面不空格直接跟标点 / 正文：遇到全角字符就停。
            assert.deepEqual(hrefs('见https://a.example/doc。然后（参考 https://zh.wikipedia.org/wiki/Foo_(bar)）再说'), ['https://a.example/doc', 'https://zh.wikipedia.org/wiki/Foo_(bar)']);
            assert.ok(linkifyHtml('见https://a.example/doc。然后').endsWith('</a>。然后'));
            assert.deepEqual(hrefs('(see https://a.example/x), ok? https://b.example/y!'), ['https://a.example/x', 'https://b.example/y']);
            // 显示的是解析后的主机名：user@host 的障眼法骗不了人；非 ASCII 的同形异义域名干脆不当链接。
            assert.deepEqual(labels('https://bank.example@evil.example/login'), ['evil.example/login']);
            assert.deepEqual(hrefs('https://аpple.com/login'), []);
            assert.deepEqual(labels('https://www.a.example:8443/'), ['a.example:8443']);
            const long = 'https://a.example/' + 'p'.repeat(80);
            assert.equal(labels(long)[0].length, 42);
            assert.ok(linkifyHtml(long).includes('title="' + long + '"'));
            assert.equal(linkifyHtml('https://'), 'https://');
        """)

    def test_todo_rows_render_links_but_keep_raw_text_for_labels_and_editing(self):
        self.run_js("""
            STATE.todos = [{ id: 'u1', text: '续费 https://a.example/pay <b>急</b>', done: false }, { id: 'u2', text: '别的', done: false }];
            renderTodos();
            let h = $('todoList').innerHTML;
            assert.ok(h.includes('<a class="todo-link" href="https://a.example/pay"'));
            assert.ok(h.includes('&lt;b&gt;急&lt;/b&gt;') && !h.includes('<b>急'));
            assert.ok(h.includes('aria-label="标记为完成：续费 https://a.example/pay &lt;b&gt;急&lt;/b&gt;"'));
            TODO.editing = 'u1'; renderTodos();
            h = $('todoList').innerHTML;
            assert.ok(h.includes('value="续费 https://a.example/pay &lt;b&gt;急&lt;/b&gt;"') && !h.includes('data-todo="u1"><a'));
        """)

    # ---- 分组折叠 ----

    def test_folding_hides_a_group_and_is_remembered(self):
        self.run_js("""
            assert.deepEqual(homeFolds(), []);
            assert.ok(html().includes('站点0') && html().includes('data-home-fold="big"') && html().includes('data-home-fold="monitor"'));
            toggleHomeFold('big');
            assert.deepEqual(homeFolds(), ['big']);
            assert.ok(document.cookie.includes('bh_home_fold=big'));
            let h = html();
            assert.ok(!h.includes('站点0') && h.includes('看板甲'));                       // 内容不渲染，别的分组不受影响
            assert.ok(h.includes('is-folded') && h.includes('data-home-fold="big" aria-expanded="false" aria-controls="homeSec-big" title="展开" aria-label="展开「大&lt;b&gt;组&lt;/b&gt;」"'));
            assert.ok(h.includes('<div class="section-body" id="homeSec-big" hidden></div>'));
            assert.ok(h.includes('<span class="count">11</span>'));                          // 折叠着也看得见里面有几个
            toggleHomeFold('monitor');
            assert.deepEqual(homeFolds(), ['big', 'monitor']);
            assert.ok(!html().includes('看板甲'));
            toggleHomeFold('big');
            assert.deepEqual(homeFolds(), ['monitor']);
            assert.ok(html().includes('站点0') && html().includes('data-home-fold="big" aria-expanded="true"'));
        """)

    def test_fold_cookie_is_whitelisted_and_pruned(self):
        self.run_js("""
            assert.deepEqual(homeFolds(), []);                                                // 白名单之外的字符：整个值作废
            toggleHomeFold('nope');                                                           // 不存在的分组：不写
            assert.ok(!document.cookie.includes('bh_home_fold=nope'));
            document.cookie = 'bh_home_fold=gone.big.also-gone';
            assert.ok(!html().includes('站点0') || (renderHome(), !html().includes('站点0')));
            toggleHomeFold('daily');                                                          // 写的时候顺手清掉已删除的分组
            assert.deepEqual(homeFolds(), ['big', 'daily']);
        """, cookie="bh_home_fold=<script>alert(1)</script>")

    def test_minimal_density_ignores_folds(self):
        self.run_js("""
            assert.equal($('homeList').className, 'home-list is-tiles is-minimal');
            assert.ok(html().includes('站点0') && !html().includes('is-folded'));            // 极简没有分组标题，折叠了就再也点不开
        """, cookie="bh_home_view=minimal; bh_home_fold=big")

    # ---- 常用一行 ----

    def test_frequent_row_needs_enough_evidence_then_ranks_by_use(self):
        self.run_js("""
            assert.ok(!html().includes('is-frequent'));
            hit('https://s5.example', 5); hit('https://s1.example', 1); renderHome();
            assert.ok(!html().includes('is-frequent'));                                       // 只有一个够格：不出现
            hit('https://s2.example', 3); hit('https://a.example', 2); hit('https://s1.example', 1); renderHome();
            const h = html();
            assert.ok(h.includes('is-frequent') && h.indexOf('is-frequent') < h.indexOf('data-home-fold="monitor"'));   // 排在最前
            const row = h.slice(h.indexOf('is-frequent'), h.indexOf('aria-label="站点看板"'));
            const names = Array.from(row.matchAll(/tile-title">([^<]+)</g)).map(m => m[1]);
            assert.deepEqual(names, ['站点5', '站点2', '看板甲', '站点1']);                    // 次数多的在前；同次数按原顺序；没上首页的（站点2）也能进
            assert.ok(row.includes('<span class="section-name"') && !row.includes('data-home-fold'));   // 标题不是入口，也不折叠
            assert.ok(row.includes("dropHomeHit('") && row.includes('前往「大&lt;b&gt;组&lt;/b&gt;」'));
            dropHomeHit(urlKey('https://s5.example'));
            assert.ok(!html().slice(html().indexOf('is-frequent'), html().indexOf('aria-label="站点看板"')).includes('站点5'));
        """)

    def test_frequent_row_is_exactly_one_row(self):
        self.run_js("""
            for (let i = 0; i < 12; i++) hit('https://s' + i + '.example', 2 + i);
            setHomeCols(4); renderHome();
            const row = html().slice(html().indexOf('is-frequent'), html().indexOf('aria-label="站点看板"'));
            assert.equal((row.match(/data-freq-i=/g) || []).length, 10);                     // 最多备 10 个
            assert.equal((row.match(/data-freq-i="\\d+" hidden/g) || []).length, 6);           // 当前 4 列：后 6 个先藏着
            assert.ok(row.includes('data-freq-i="3">') && row.includes('data-freq-i="4" hidden>'));
            assert.ok(row.includes('data-units="10" style="--n:10;--span:4"'));
        """)

    def test_frequent_row_steps_aside(self):
        self.run_js("""
            hit('https://s5.example', 3); hit('https://s6.example', 3); hit('https://s7.example', 3); renderHome();
            assert.ok(html().includes('is-frequent'));
            setArrange(true);  assert.ok(!html().includes('is-frequent'));                    // 编辑首页时让路（它不可拖）
            setArrange(false); assert.ok(html().includes('is-frequent'));
            setHomeView('cards'); assert.ok(!html().includes('is-frequent'));                 // 卡片密度不放图标行
            setHomeView('tiles');
            document.cookie = 'bh_home_freq=off'; renderHome();
            assert.ok(!html().includes('is-frequent'));
            const before = document.cookie;
            recordHit('https://s8.example');                                                   // 关掉之后不再记录
            assert.equal(document.cookie, before);
        """)

    def test_hits_cookie_is_strict_small_and_forgets(self):
        self.run_js("""
            hit('https://s1.example/secret-path?token=abc', 3);
            const raw = document.cookie.split('; ').find(c => c.startsWith('bh_home_hits='));
            assert.match(raw, /^bh_home_hits=[0-9]{1,6}\\.[a-z0-9]{1,8}-3$/);
            assert.ok(!/example|secret|token/.test(document.cookie));                          // Cookie 里没有网址本身
            // 被改坏的值整个作废，不会被拼进页面。
            document.cookie = 'bh_home_hits=1.<script>-5';
            assert.equal(readHits().counts.size, 0);
            document.cookie = 'bh_home_hits=' + today + '.abc-5.zzz-99999';
            assert.equal(readHits().counts.size, 0);
            // 14 天减半：28 天前的 8 次，现在算 2 次；减到 0 的直接丢。
            document.cookie = 'bh_home_hits=' + (today - 28) + '.aaa-8.bbb-3.ccc-1';
            const old = readHits();
            assert.deepEqual(Array.from(old.counts.entries()), [['aaa', 2]]);
            assert.equal(old.day, today);
            document.cookie = 'bh_home_hits=' + (today + 400) + '.aaa-8';                      // 未来的日期（时钟改过）：按今天算
            assert.equal(readHits().counts.get('aaa'), 8);
            // 最多 24 条；满了让次数最少的让位，新网址挤得进来。
            clearHomeHits();
            for (let i = 0; i < 30; i++) hit('https://n' + i + '.example', i < 24 ? 5 : 1);
            const now = readHits().counts;
            assert.equal(now.size, 24);
            assert.ok(now.has(urlKey('https://n29.example')));
            assert.ok(document.cookie.length < 600);
            recordHit('javascript:alert(1)'); recordHit('');
            assert.equal(readHits().counts.size, 24);
            clearHomeHits();
            assert.ok(!document.cookie.includes('bh_home_hits'));
        """)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""首页待办（首页组件里的一张卡片）：页面结构、无障碍标注、旧偏好的迁移、样式护栏，以及用真实页面脚本跑一遍渲染。

卡片的显隐 / 先后 / 收起归「首页组件」管，见 tests/test_home_deck_ui.py。"""
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


class PageMarkupTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8")

    def block(self, start, end):
        return self.html[self.html.index(start):self.html.index(end, self.html.index(start))]

    def test_widget_is_a_static_node_outside_the_rerendered_list(self):
        """renderHome() 每敲一个搜索字符都会重写 #homeList；待办放在里面，输入到一半的字和焦点就没了。"""
        body = self.block('id="homeBody"', 'id="bmPage"')
        self.assertLess(body.index('id="homeTodo"'), body.index('id="homeList"'))
        todo = self.block('id="homeTodo"', '</section>')
        self.assertNotIn('id="homeList"', todo)
        self.assertIn("if (!TODO.editing) renderTodos();", self.html)
        # 卡片在组件容器里，和其它组件一样是静态节点；renderHome() 只通过 renderDeck() 间接碰它。
        deck = self.block('id="homeDeck"', 'class="home-main"')
        self.assertIn('id="homeTodo" data-deck="todo"', deck)

    def test_widget_is_labelled_for_assistive_tech(self):
        todo = self.block('id="homeTodo"', '</section>')
        self.assertIn('aria-labelledby="todoTitle"', todo)
        self.assertRegex(todo, r'<h2[^>]*id="todoTitle"')
        self.assertRegex(todo, r'id="todoCount"[^>]*aria-live="polite"')
        self.assertRegex(todo, r'id="todoCollapse"[^>]*aria-expanded="true"[^>]*aria-controls="todoBody"')
        self.assertRegex(todo, r'<label class="sr-only" for="todoInput">')
        self.assertRegex(todo, r'id="todoAdd"[^>]*aria-label="添加待办"')
        for list_id, label in (("todoList", "未完成的待办"), ("todoDoneList", "已完成的待办")):
            self.assertRegex(todo, r'<ul[^>]*id="%s"[^>]*aria-label="%s"' % (list_id, label))
        # 每一条：真正的复选框 + 指向它的 <label>，两个图标按钮都带上「对哪一条操作」。
        self.assertIn('<input type="checkbox" class="todo-check" id="todo-${id}"', self.html)
        self.assertIn('<label class="todo-text" for="todo-${id}"', self.html)
        for verb in ("修改", "删除"):
            self.assertIn('aria-label="%s：${text}"' % verb, self.html)

    def test_sort_handle_menu_and_live_region_are_wired_for_assistive_tech(self):
        """排序有三条路：拖把手、点把手出菜单、把手上按方向键。后两条不依赖拖拽，读屏和键盘用户走得通。"""
        todo = self.block('id="homeTodo"', '</section>')
        self.assertRegex(todo, r'<div class="sr-only" id="todoLive" aria-live="polite"></div>')
        self.assertRegex(todo, r'<p class="sr-only" id="todoSortHint">[^<]*方向键[^<]*</p>')
        self.assertLess(todo.index('id="todoLive"'), todo.index('id="todoList"'))
        # 把手是真正的 <button>：可聚焦、有名字、声明自己会弹菜单，并挂上用法说明。
        for attr in ('type="button" class="todo-grip" data-todo-grip', 'aria-label="排序：${text}"',
                     'aria-describedby="todoSortHint"', 'aria-haspopup="menu"', 'aria-expanded="false"'):
            self.assertIn(attr, self.html)
        # 菜单挂在 <body> 下（不在列表的滚动区里，否则最后几行的菜单会被裁掉），四个动作各是一个 menuitem。
        menu = self.block('id="todoSortMenu"', '</div>')
        self.assertIn('role="menu"', menu)
        self.assertIn(' hidden>', menu)
        self.assertEqual(re.findall(r'role="menuitem" data-todo-sort="(\w+)"', menu), ["top", "up", "down", "bottom"])
        self.assertLess(self.html.index('</main>'), self.html.index('id="todoSortMenu"'))

    def test_input_lives_in_its_own_form_and_is_ignored_by_password_managers(self):
        """页面上有管理密码框：不在自己表单里的文本框会被当成「用户名」，手机上一点就弹已存密码。"""
        form = self.block('id="todoForm"', '</form>')
        self.assertIn('id="todoInput"', form)
        self.assertIn('autocomplete="off"', form)
        for marker in ("data-1p-ignore", 'data-lpignore="true"', "data-bwignore", 'data-form-type="other"'):
            self.assertIn(marker, form)
        self.assertIn('maxlength="200"', form)
        self.assertNotIn('type="password"', form)

    def test_legacy_preference_cookie_is_read_only_and_whitelisted(self):
        """旧版的 bh_home_todo（open / closed / off）只用来推默认值：照旧按白名单读，但不再有任何地方写它。"""
        self.assertIn("function legacyTodoPref() { return readPref('bh_home_todo', ['open', 'closed', 'off'], ''); }", self.html)
        self.assertEqual(re.findall(r"writePref\('bh_home_todo'", self.html), [])
        self.assertNotIn('data-home-todo=', self.html)      # 「外观」里的显示 / 隐藏开关换成了「首页组件」入口
        self.assertIn('id="openDeckModal"', self.html)

    def test_no_browser_storage_or_polling(self):
        script = _inline_script()
        section = script[script.index("// ===== 首页待办 ====="):script.index("function updateHomeSelectionCount")]
        for banned in ("localStorage", "sessionStorage", "setInterval", "innerHTML = t.text", "eval("):
            self.assertNotIn(banned, section)
        # 待办内容是用户输入，拼进 HTML 前必须转义。
        self.assertIn("text = escapeHtml(t.text)", section)

    def test_todo_text_is_never_sent_anywhere_but_this_site(self):
        script = _inline_script()
        section = script[script.index("// ===== 首页待办 ====="):script.index("function updateHomeSelectionCount")]
        urls = re.findall(r"todoApi\('([^']+)'", section)
        self.assertGreaterEqual(len(urls), 6)
        for url in urls:
            self.assertTrue(url.startswith("/api/todos"), url)
        self.assertIn("'/api/todos/' + encodeURIComponent(id) + '/move'", section)
        self.assertNotRegex(section, r"https?://")

    def test_page_is_served_with_the_widget_and_unchanged_csp(self):
        resp = app.test_client().get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="homeTodo"', resp.get_data(as_text=True))
        csp = resp.headers["Content-Security-Policy"]
        self.assertIn("img-src 'self' data:", csp)
        self.assertIn("connect-src 'self'", csp)


class StyleGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "static" / "app-v3.css").read_text(encoding="utf-8")
        cls.todo = cls.css[cls.css.index("首页待办"):]

    def test_clickable_tags_keep_the_tag_font_size(self):
        """`.tag-btn { font: inherit }` 曾把 .tag 的 10.5px 冲成卡片正文的 14px。"""
        rule = re.search(r"\.tag-btn \{([^}]*)\}", self.css).group(1)
        self.assertNotRegex(rule, r"(?<![-\w])font:\s*inherit")
        self.assertIn("font-family: inherit", rule)
        self.assertLess(self.css.index(".tag {"), self.css.index(".tag-btn {"))

    def test_card_uses_a_solid_surface_not_per_item_blur(self):
        deck = self.css[self.css.index("首页组件（"):]
        self.assertNotIn("backdrop-filter", deck)
        self.assertRegex(deck, r"\n\.deck-card \{[^}]*background: var\(--surface\)")

    def test_hidden_attribute_wins_over_display_rules(self):
        self.assertRegex(self.todo, r"\.todo-body\[hidden\][^{]*\{ display: none; \}")

    def test_lists_get_taller_in_the_side_column(self):
        rail = self.todo[self.todo.index("@media (min-width: 1100px)"):self.todo.index("@media (min-width: 1280px)")]
        self.assertIn("html.nav-rail .home-body.has-deck .todo-list { max-height: min(36vh, 420px); }", rail)
        full = self.todo[self.todo.index("@media (min-width: 1280px)"):self.todo.index("@media (max-width: 760px)")]
        self.assertRegex(full, r"\n  \.home-body\.has-deck \.todo-list \{ max-height: min\(36vh, 420px\); \}")

    def test_touch_and_keyboard_affordances(self):
        mobile = self.todo[self.todo.index("@media (max-width: 760px)"):]
        self.assertRegex(mobile, r"\.todo-input, \.todo-edit \{[^}]*font-size: 16px")      # iOS 聚焦不放大页面
        self.assertRegex(mobile, r"\.todo-check \{[^}]*width: 22px")
        self.assertRegex(mobile, r"\.todo-icon-btn \{[^}]*width: 32px")
        self.assertRegex(self.todo, r"\.todo-check:focus-visible[^{]*\{ outline: 2px solid var\(--accent\)")
        # 悬停才出现的操作按钮：键盘聚焦到行内时同样要显形；触屏（没有悬停）则常驻。
        hover = self.todo[self.todo.index("@media (hover: hover)"):self.todo.index("html.wall-on .home-todo")]
        self.assertIn(".todo-item:focus-within .todo-actions { opacity: 1;", hover)
        self.assertNotIn("opacity: 0", self.todo[:self.todo.index("@media (hover: hover)")].split(".todo-actions {")[-1].split("}")[0])

    def test_sort_handle_is_touch_draggable_and_big_enough(self):
        grip = re.search(r"\.todo-grip \{([^}]*)\}", self.todo).group(1)
        self.assertIn("touch-action: none", grip)          # 触摸拖拽只从把手起步，行上其余地方留给列表滚动
        self.assertIn("cursor: grab", grip)
        self.assertNotRegex(grip, r"opacity: 0[;\s]")       # 把手常驻（只是很淡）：看得见才知道能拖
        mobile = self.todo[self.todo.rindex("@media (max-width: 760px)"):]
        self.assertRegex(mobile, r"\.todo-grip \{[^}]*width: 28px; height: 32px")
        self.assertRegex(mobile, r"\.todo-sort-menu button \{[^}]*min-height: 44px")
        self.assertRegex(self.todo, r"\.todo-grip:focus-visible \{ outline: 2px solid var\(--accent\)")
        # 拖动中：整张卡片禁选文字；被拖的那一行有可见的强调（强制颜色模式下换成系统高亮色的描边）。
        self.assertIn(".home-todo.is-sorting .todo-list { user-select: none;", self.todo)
        self.assertRegex(self.todo, r"\.todo-item\.is-dragging \{[^}]*box-shadow: inset 0 0 0 1px var\(--accent\)")
        self.assertIn("@media (forced-colors: active) { .todo-item.is-dragging { outline: 2px solid Highlight; }", self.todo)
        # 菜单压在任意背景上都得是实底，且不靠毛玻璃。
        menu = re.search(r"\.todo-sort-menu \{([^}]*)\}", self.todo).group(1)
        self.assertIn("position: fixed", menu)
        self.assertNotIn("backdrop-filter", menu)

    def test_clear_done_confirm_state_is_styled_and_timed_without_polling(self):
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        block = html[html.index("function resetTodoClear()"):html.index("$('homeTodo').addEventListener('change'")]
        self.assertNotIn("confirm(", block)
        self.assertIn("setTimeout(resetTodoClear, TODO_CLEAR_MS)", block)
        self.assertNotIn("setInterval", block)
        self.assertIn(".todo-clear:hover, .todo-clear.confirming {", self.css)

    def test_degradation_paths(self):
        self.assertRegex(self.todo, r"@media \(forced-colors: active\) \{ \.todo-check \{ border-color: CanvasText; \}")
        self.assertIn("@media (prefers-reduced-motion: reduce) { .todo-done > summary::before", self.todo)
        self.assertIn(".todo-sort-menu { animation: none; }", self.todo)
        # 让路动画由脚本写行内过渡，「减少动态效果」要在脚本里判。
        self.assertIn("window.matchMedia('(prefers-reduced-motion: reduce)').matches", (ROOT / "templates" / "index.html").read_text(encoding="utf-8"))


@unittest.skipIf(NODE is None, '未安装 node')
class TodoRenderTest(unittest.TestCase):
    TODOS = [
        {"id": "t1", "text": "续费 <b>域名</b>", "done": False, "created_at": "2026-09-18 09:00:00", "updated_at": "", "done_at": ""},
        {"id": "t2", "text": "备份照片", "done": True, "created_at": "2026-09-10 09:00:00", "updated_at": "", "done_at": "2026-09-12 08:00:00"},
        {"id": "t3", "text": "回邮件", "done": False, "created_at": "", "updated_at": "", "done_at": ""},
    ]

    def run_js(self, assertions, configs=None, cookie=""):
        responses = copy.deepcopy(RESPONSES)
        responses['/api/configs'].update({"todos": copy.deepcopy(self.TODOS), "todos_locked": False, "todos_error": ""})
        responses['/api/configs'].update(configs or {})
        script = '\n'.join([
            Path(STUB).read_text(),
            'globalThis.__RESPONSES = ' + json.dumps(responses) + ';',
            'document.cookie = ' + json.dumps(cookie) + ';',
            "const assert = require('node:assert/strict');",
            "document.querySelectorAll('.tab').forEach(t => { t.addEventListener = (name, fn) => { t[name] = fn; }; });",
            "{ const t = document.getElementById('navToggle'); t.addEventListener = (name, fn) => { t[name] = fn; }; }",
            "{ const t = document.getElementById('todoClear'); t.on = {}; t.addEventListener = (name, fn) => { t.on[name] = fn; }; }",
            _inline_script(),
            'setTimeout(async () => { try {', assertions,
            'assert.deepEqual(__CALLS.errors, []); assert.deepEqual(__CALLS.rejections, []);',
            "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, 30);",
        ])
        proc = subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_pending_and_done_are_split_and_text_is_escaped(self):
        self.run_js("""
            assert.equal($('homeTodo').hidden, false);
            assert.equal($('todoCount').textContent, '2 项未完成');
            const pending = $('todoList').innerHTML, done = $('todoDoneList').innerHTML;
            assert.ok(pending.includes('data-todo="t1"') && pending.includes('data-todo="t3"') && !pending.includes('data-todo="t2"'));
            assert.ok(done.includes('data-todo="t2"') && done.includes(' checked'));
            assert.ok(pending.includes('续费 &lt;b&gt;域名&lt;/b&gt;') && !pending.includes('<b>域名'));
            assert.ok(pending.includes('aria-label="标记为完成：续费 &lt;b&gt;域名&lt;/b&gt;"'));
            assert.ok(done.includes('aria-label="已完成：备份照片"'));
            assert.equal($('todoDone').hidden, false);
            assert.equal($('todoDoneLabel').textContent, '已完成 1');
            assert.equal($('todoState').hidden, true);
            assert.equal($('todoForm').hidden, false);
        """)

    def test_empty_all_done_locked_and_broken_states(self):
        self.run_js("""
            assert.equal($('todoState').textContent, '还没有待办。在上面记下一件接下来要做的事吧。');
            assert.equal($('todoList').hidden, true);
            assert.equal($('todoDone').hidden, true);
            assert.equal($('todoCount').textContent, '');
            STATE.todos = [{ id: 'a', text: 'x', done: true }]; renderTodos();
            assert.equal($('todoCount').textContent, '全部完成');
            assert.equal($('todoState').textContent, '清单清空了，做得好。');
            STATE.todos = []; STATE.todos_locked = true; renderTodos();
            assert.ok($('todoState').innerHTML.includes('data-todo-act="unlock"'));
            assert.equal($('todoForm').hidden, true);
            STATE.todos_locked = false; STATE.todos_error = '读取待办失败：boom'; renderTodos();
            assert.equal($('todoState').textContent, '读取待办失败：boom');
            assert.equal($('todoForm').hidden, true);
        """, configs={"todos": []})

    def test_legacy_cookie_still_decides_visibility_and_collapse_until_the_deck_is_customised(self):
        """老用户升级：以前把待办藏起来 / 收起来的，换成组件之后还是藏着 / 收着；一旦动过组件设置，就以新 Cookie 为准。"""
        self.run_js("""
            globalThis.matchMedia = () => ({ matches: true, addEventListener() {} });      // 宽屏：右侧一列
            assert.equal(legacyTodoPref(), 'off');
            assert.deepEqual(deckOrder(), ['calendar']);
            assert.equal($('homeTodo').hidden, true);
            assert.equal($('deckCalendar').hidden, false);
            document.cookie = 'bh_home_todo=closed'; renderDeck();
            assert.equal($('homeTodo').hidden, false);
            assert.ok($('homeTodo').classList.contains('is-folded'));
            assert.ok(!$('deckCalendar').classList.contains('is-folded'));
            document.cookie = 'bh_home_todo=<script>'; renderDeck();      // 白名单之外的值一律当没设
            assert.equal(legacyTodoPref(), '');
            assert.deepEqual(deckOrder(), ['calendar', 'todo']);
            assert.ok(!$('homeTodo').classList.contains('is-folded'));
            // 新 Cookie 写过之后，旧 Cookie 不再作数。
            document.cookie = 'bh_home_todo=off; bh_home_deck=todo; bh_home_deck_fold=none'; renderDeck();
            assert.deepEqual(deckOrder(), ['todo']);
            assert.equal($('homeTodo').hidden, false);
            assert.equal($('deckCalendar').hidden, true);
        """, cookie="bh_home_todo=off")

    def test_private_shell_hides_the_widget(self):
        self.run_js("assert.equal($('homeTodo').hidden, true);",
                    configs={"private": True, "locked": True, "todos": [], "todos_locked": True,
                             "link_groups": [], "bookmarks": [], "configs": []})

    def test_editing_row_swaps_in_a_labelled_input_and_search_does_not_clobber_it(self):
        self.run_js("""
            TODO.editing = 't1'; renderTodos();
            let html = $('todoList').innerHTML;
            assert.ok(html.includes('data-todo-edit') && html.includes('value="续费 &lt;b&gt;域名&lt;/b&gt;"'));
            assert.ok(html.includes('aria-label="修改待办内容，回车保存，Esc 取消"'));
            $('todoList').innerHTML = 'UNTOUCHED';
            renderHome();                                   // 搜索框每敲一个字都会走到这里
            assert.equal($('todoList').innerHTML, 'UNTOUCHED');
            TODO.editing = ''; renderHome();
            assert.ok($('todoList').innerHTML.includes('data-todo="t1"'));
        """)

    # 记录每次请求的方法 / 地址 / 载荷；gate() 之后的请求先挂起，release() 才放行——用来验证「按序、不丢」。
    FETCH_SPY = """
        const SENT = []; let HOLD = null;
        const CANNED = new Set(Object.keys(__RESPONSES));
        const realFetch = globalThis.fetch;
        globalThis.fetch = (url, opts) => {
            opts = opts || {};
            const rec = { url: String(url), method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null };
            SENT.push(rec);
            // 和真实服务端一样：每个写接口都回带整份列表（桩里就用当时的本地列表顶替）。
            const answer = () => {
                const path = String(url).split('?')[0];
                if (!CANNED.has(path) && !(path in __RESPONSES && __RESPONSES[path].canned)) __RESPONSES[path] = { ok: true, todos: JSON.parse(JSON.stringify(STATE.todos)) };
                return realFetch(url, opts);
            };
            return HOLD ? HOLD.then(answer) : answer();
        };
        const gate = () => { let open; HOLD = new Promise(r => { open = r; }); return () => { HOLD = null; open(); }; };
        const ids = () => STATE.todos.map(t => t.id).join(',');
        const tick = () => new Promise(r => setTimeout(r, 5));
    """

    def test_only_a_sortable_pending_list_gets_handles(self):
        self.run_js("""
            const pending = $('todoList').innerHTML, done = $('todoDoneList').innerHTML;
            assert.equal((pending.match(/data-todo-grip/g) || []).length, 2);
            assert.ok(pending.includes('aria-label="排序：续费 &lt;b&gt;域名&lt;/b&gt;"'));       // 待办文字进属性前同样转义
            assert.ok(!done.includes('data-todo-grip'));
            STATE.todos = STATE.todos.filter(t => t.id !== 't3'); renderTodos();                  // 只剩一条未完成：没什么可排
            assert.ok(!$('todoList').innerHTML.includes('data-todo-grip'));
        """)

    def test_move_reorders_locally_and_asks_the_server_for_a_relative_move(self):
        self.run_js(self.FETCH_SPY + """
            assert.equal(ids(), 't1,t2,t3');
            assert.equal(moveTodoTo('t3', 0), true);
            assert.equal(ids(), 't3,t1,t2');                                   // 本地立刻生效，不等响应
            assert.ok($('todoList').innerHTML.indexOf('data-todo="t3"') < $('todoList').innerHTML.indexOf('data-todo="t1"'));
            assert.equal($('todoLive').textContent, '「回邮件」移到第 1 位，共 2 项');
            await tick();
            assert.deepEqual(SENT, [{ url: '/api/todos/t3/move', method: 'POST', body: { before: 't1' } }]);
            // 挪到组内最后：参照物是同组最后一条（after），夹在中间的已完成项不动。
            assert.equal(moveTodoTo('t3', Infinity), true);
            assert.equal(ids(), 't1,t3,t2');
            await tick();
            assert.deepEqual(SENT[1].body, { after: 't1' });
            // 原地不动 / 不存在的 id：不发请求。
            assert.equal(moveTodoTo('t3', 1), false);
            assert.equal(moveTodoTo('nope', 0), false);
            assert.equal(moveTodo('t1', -1), false);
            assert.equal(SENT.length, 2);
        """)

    def test_rapid_moves_of_one_item_collapse_into_a_single_request(self):
        self.run_js(self.FETCH_SPY + """
            STATE.todos = ['a', 'b', 'c', 'd'].map(id => ({ id, text: id, done: false })); renderTodos();
            const release = gate();
            moveTodo('a', 1);                      // 第一步已经发出（挂起中）
            await tick();
            moveTodo('a', 1); moveTodo('a', 1);    // 后两步还没发：并成一次，只改目的地
            assert.equal(ids(), 'b,c,d,a');
            release(); await tick(); await tick();
            const moves = SENT.filter(r => r.url === '/api/todos/a/move');
            assert.deepEqual(moves.map(r => r.body), [{ before: 'c' }, { after: 'd' }]);
        """)

    def test_writes_are_queued_in_order_and_none_is_dropped(self):
        """回归：以前「上一个请求没回来就丢掉这一个」，连着勾两条，第二条会被悄悄还原。"""
        self.run_js(self.FETCH_SPY + """
            STATE.todos = ['a', 'b', 'c'].map(id => ({ id, text: id, done: false })); renderTodos();
            const release = gate();
            const put = (id) => todoRun(() => todoApi('/api/todos/' + id, 'PUT', { done: true }), '', undefined,
                () => { STATE.todos.find(t => t.id === id).done = true; });
            put('a'); put('b');
            await tick();
            assert.equal(SENT.length, 1);                                       // 第二个排着队，没有并发，也没有被丢
            assert.deepEqual(STATE.todos.filter(t => t.done).map(t => t.id), ['a', 'b']);   // 两条都已乐观勾上
            assert.ok($('homeTodo').classList.contains('is-busy'));
            release(); await tick(); await tick();
            assert.deepEqual(SENT.map(r => r.url), ['/api/todos/a', '/api/todos/b']);
            assert.equal(TODO.inflight, 0);
            assert.ok(!$('homeTodo').classList.contains('is-busy'));
        """)

    def test_a_failed_write_resyncs_from_the_server(self):
        self.run_js(self.FETCH_SPY + """
            __RESPONSES['/api/todos/t1'] = { ok: false, error: '这条待办已不存在，请刷新', canned: true };
            __RESPONSES['/api/todos'] = { ok: true, todos: [{ id: 't9', text: '服务端的权威列表', done: false }], canned: true };
            await todoRun(() => todoApi('/api/todos/t1', 'DELETE'), '', undefined, () => { STATE.todos = STATE.todos.filter(t => t.id !== 't1'); });
            assert.deepEqual(SENT.map(r => r.method + ' ' + r.url), ['DELETE /api/todos/t1', 'GET /api/todos']);
            assert.equal(ids(), 't9');                                          // 乐观删除被服务端状态纠正
            assert.ok($('todoList').innerHTML.includes('服务端的权威列表'));
        """)

    def test_render_is_deferred_while_a_row_is_being_dragged(self):
        self.run_js("""
            $('todoList').innerHTML = 'MID-DRAG';
            TODO_SORT.active = true; renderTodos();
            assert.equal($('todoList').innerHTML, 'MID-DRAG');                  // 拖到一半不换 DOM
            TODO_SORT.active = false; renderTodos();
            assert.ok($('todoList').innerHTML.includes('data-todo="t1"'));
        """)

    def test_clear_done_confirms_in_place_instead_of_a_native_dialog(self):
        """「清除已完成」：第一下只换文案，第二下才发请求；失焦 / Esc / 条数变了都还原。全程不碰原生 confirm。"""
        self.run_js("""
            globalThis.confirm = () => { throw new Error('native confirm must not be used'); };
            const btn = $('todoClear'), ev = () => ({ preventDefault() {}, stopPropagation() {}, currentTarget: btn, key: 'Escape' });
            const cleared = () => __CALLS.fetches.filter(u => u.includes('/api/todos/clear_done')).length;
            await btn.on.click(ev());
            assert.ok(btn.classList.contains('confirming'));
            assert.equal(btn.textContent, '确认清除 1 条？');
            assert.ok($('todoLive').textContent.includes('再按一次'));
            assert.equal(cleared(), 0);
            // 失焦、Esc 都取消
            btn.on.blur(ev());
            assert.ok(!btn.classList.contains('confirming'));
            assert.equal(btn.textContent, '清除已完成');
            await btn.on.click(ev());
            btn.on.keydown(ev());
            assert.ok(!btn.classList.contains('confirming'));
            // 确认期间已完成的条数变了：按钮上的数字不作数，还原
            await btn.on.click(ev());
            STATE.todos.find(t => t.id === 't1').done = true;
            renderTodos();
            assert.ok(!btn.classList.contains('confirming'));
            assert.equal(cleared(), 0);
            // 连点两下才真删
            await btn.on.click(ev());
            assert.equal(btn.textContent, '确认清除 2 条？');
            await btn.on.click(ev());
            assert.equal(cleared(), 1);
            assert.ok(!btn.classList.contains('confirming'));
            assert.deepEqual(todoItems().filter(t => t.done), []);
            // 没有已完成的：点了也不进确认态
            await btn.on.click(ev());
            assert.ok(!btn.classList.contains('confirming'));
        """)

    def test_neighbour_lookup_stays_inside_the_same_list(self):
        self.run_js("""
            assert.equal(neighbourTodoId('t1'), 't3');     // 未完成的下一条，跳过夹在中间的已完成项
            assert.equal(neighbourTodoId('t3'), 't1');
            assert.equal(neighbourTodoId('t2'), '');       // 已完成里只有它自己：焦点回输入框
        """)


if __name__ == "__main__":
    unittest.main()

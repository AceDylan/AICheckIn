# -*- coding: utf-8 -*-
"""首页待办组件：页面结构、无障碍标注、偏好白名单、样式护栏，以及用真实页面脚本跑一遍渲染。"""
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

    def test_input_lives_in_its_own_form_and_is_ignored_by_password_managers(self):
        """页面上有管理密码框：不在自己表单里的文本框会被当成「用户名」，手机上一点就弹已存密码。"""
        form = self.block('id="todoForm"', '</form>')
        self.assertIn('id="todoInput"', form)
        self.assertIn('autocomplete="off"', form)
        for marker in ("data-1p-ignore", 'data-lpignore="true"', "data-bwignore", 'data-form-type="other"'):
            self.assertIn(marker, form)
        self.assertIn('maxlength="200"', form)
        self.assertNotIn('type="password"', form)

    def test_preference_cookie_is_whitelisted(self):
        self.assertIn("const TODO_PREFS = ['open', 'closed', 'off'];", self.html)
        self.assertIn("readPref('bh_home_todo', TODO_PREFS,", self.html)
        writes = re.findall(r"writePref\('bh_home_todo', (.+?)\);", self.html)
        self.assertEqual(sorted(writes), sorted(["'off'", "'open'", "todoPref() === 'closed' ? 'open' : 'closed'"]))
        self.assertIn('data-home-todo="on"', self.html)
        self.assertIn('data-home-todo="off"', self.html)

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
        for url in re.findall(r"todoApi\('([^']+)'", section):
            self.assertTrue(url.startswith("/api/todos"), url)
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
        self.assertNotIn("backdrop-filter", self.todo)
        self.assertRegex(self.todo, r"\.home-todo \{[^}]*background: var\(--surface\)")

    def test_hidden_attribute_wins_over_display_rules(self):
        self.assertRegex(self.todo, r"\.home-todo\[hidden\][^{]*\{ display: none; \}")

    def test_side_column_only_on_wide_screens_and_follows_the_nav(self):
        rail = self.todo[self.todo.index("@media (min-width: 1100px)"):self.todo.index("@media (min-width: 1280px)")]
        self.assertIn("html.nav-rail .home-body.has-todo { display: grid;", rail)
        full = self.todo[self.todo.index("@media (min-width: 1280px)"):self.todo.index("@media (min-width: 1440px)")]
        self.assertRegex(full, r"\n  \.home-body\.has-todo \{ display: grid;")
        self.assertIn("position: sticky", full)
        self.assertRegex(self.css, r"const TODO_WIDE_QUERY|" + re.escape(".home-body { display: flex; flex-direction: column;"))

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

    def test_degradation_paths(self):
        self.assertRegex(self.todo, r"@media \(forced-colors: active\) \{ \.todo-check \{ border-color: CanvasText; \}")
        self.assertIn("@media (prefers-reduced-motion: reduce) { #todoCollapse .ic", self.todo)


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

    def test_cookie_controls_visibility_and_collapse(self):
        self.run_js("""
            assert.equal(todoPref(), 'off');
            assert.equal($('homeTodo').hidden, true);
            document.cookie = 'bh_home_todo=closed'; renderTodos();
            assert.equal($('homeTodo').hidden, false);
            assert.equal($('todoBody').hidden, true);
            assert.equal($('todoCollapse').title, '展开待办');     // DOM 桩不存 attribute；aria-expanded 由浏览器验证覆盖
            document.cookie = 'bh_home_todo=<script>'; renderTodos();      // 白名单之外的值一律当没设
            assert.ok(['open', 'closed'].includes(todoPref()));
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

    def test_neighbour_lookup_stays_inside_the_same_list(self):
        self.run_js("""
            assert.equal(neighbourTodoId('t1'), 't3');     // 未完成的下一条，跳过夹在中间的已完成项
            assert.equal(neighbourTodoId('t3'), 't1');
            assert.equal(neighbourTodoId('t2'), '');       // 已完成里只有它自己：焦点回输入框
        """)


if __name__ == "__main__":
    unittest.main()

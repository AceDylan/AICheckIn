# -*- coding: utf-8 -*-
"""执行真实页面脚本，检查首页路由、跨分组渲染与图标回退。"""
import copy
import json
import subprocess
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script
from tests.test_library_home import PNG


@unittest.skipIf(NODE is None, '未安装 node')
class LibraryHomeUiTest(unittest.TestCase):
    def run_js(self, assertions):
        responses = copy.deepcopy(RESPONSES)
        groups = responses['/api/configs']['link_groups']
        groups[0]['links'][0].update(show_on_home=True, custom_icon=PNG)
        groups.append({'id': 'second', 'name': '第二分组', 'color': 'mint', 'icon': 'folder', 'links': [
            {'id': 'l1', 'name': 'Other selected', 'url': 'https://other.example', 'show_on_home': True},
            {'id': 'l2', 'name': 'Hidden site', 'url': 'https://hidden.example'},
        ]})
        script = '\n'.join([
            Path(STUB).read_text(),
            'globalThis.__RESPONSES = ' + json.dumps(responses) + ';',
            "const assert = require('node:assert/strict');",
            "document.querySelectorAll('.tab').forEach(t => { t.addEventListener = (name, fn) => { t[name] = fn; }; });",
            _inline_script(),
            'setTimeout(async () => { try {', assertions,
            'assert.deepEqual(__CALLS.errors, []); assert.deepEqual(__CALLS.rejections, []);',
            "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, 30);",
        ])
        proc = subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_parent_tab_always_opens_home_and_monitor_keeps_its_route(self):
        self.run_js("""
            assert.equal(LIB.page, '@home');
            assert.equal($('libTitle').textContent, '收藏首页');
            assert.equal($('homePage').hidden, false);
            assert.equal($('bmPage').hidden, true);
            openLibPage('monitor');
            assert.equal(libHash(), '#links/monitor');
            assert.equal($('bmPage').hidden, false);
            document.querySelector('.tab[data-view="bookmarks"]').click();
            assert.equal(LIB.page, '@home');
            assert.equal(libHash(), '#bookmarks');
            openLibPage('deleted');
            assert.equal(LIB.page, '@home');
        """)

    def test_home_is_derived_from_selected_links_in_all_groups(self):
        self.run_js("""
            assert.deepEqual(homeGroups().map(g => g.id), ['daily', 'second']);
            assert.equal($('bmVisibleCount').textContent, '2 个网址');
            assert.ok($('homeList').innerHTML.includes('Other selected'));
            assert.ok(!$('homeList').innerHTML.includes('Hidden site'));
            assert.ok($('homeList').innerHTML.includes("editLink('second','l1')"));
            assert.equal(homeGroups('第二分组')[0].id, 'second');
            STATE.link_groups[1].links[0].name = 'Renamed';
            renderHome();
            assert.ok($('homeList').innerHTML.includes('Renamed'));
            STATE.link_groups[1].links.splice(0, 1);
            renderHome();
            assert.equal(homeGroups().length, 1);
            STATE.link_groups[0].links[0].show_on_home = false;
            renderHome();
            assert.equal($('homeList').innerHTML, '');
            assert.ok($('homeEmpty').innerHTML.includes('自定义首页'));
        """)

    def test_fetched_icon_and_uploaded_fallback_are_independent_layers(self):
        self.run_js("""
            const uploaded = STATE.link_groups[0].links[0].custom_icon;
            const html = siteAvatarHtml('Docs', 'https://docs.example', 'link-avatar', '', uploaded);
            assert.ok(html.includes('data-favicon="https://docs.example"'));
            assert.ok(html.includes('avatar-upload'));
            assert.ok(html.includes(uploaded));
            FAVICON_MEMO.set('https://docs.example', 'fail');
            const failed = siteAvatarHtml('Docs', 'https://docs.example', 'link-avatar', '', uploaded);
            assert.ok(!failed.includes('data-favicon='));
            assert.ok(failed.includes(uploaded));
            const initials = siteAvatarHtml('Docs', 'https://docs.example', 'link-avatar');
            assert.ok(!initials.includes('<img'));
            assert.ok(initials.includes('DO'));
            assert.ok(!siteAvatarHtml('Docs', '', 'link-avatar', '', 'javascript:alert(1)').includes('<img'));
            const handlers = {};
            const img = makeEl('auto');
            img.dataset.favicon = 'https://recovered.example';
            img.addEventListener = (name, fn) => { handlers[name] = fn; };
            startFavicon(img);
            handlers.load();
            assert.equal(FAVICON_MEMO.get(img.dataset.favicon), 'ok');
            assert.ok(img.classList.contains('is-ready'));
            let removed = false;
            const bad = makeEl('bad');
            bad.dataset.favicon = 'https://failed.example';
            bad.addEventListener = (name, fn) => { handlers[name] = fn; };
            bad.remove = () => { removed = true; };
            startFavicon(bad);
            handlers.error();
            assert.ok(removed);
            assert.equal(FAVICON_MEMO.get(bad.dataset.favicon), 'fail');
        """)

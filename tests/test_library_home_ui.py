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
        # 看板里两个站点：一个上首页（带余额字段），一个不上。
        responses['/api/configs']['bookmarks'] = [
            {'name': '看板站', 'url': 'https://dash.example', 'show_on_home': True, 'fields': [
                {'id': 'f1', 'label': '余额', 'type': 'amount', 'enabled': True, 'value': '27.0660232000'}]},
            {'name': '未选站', 'url': 'https://unpicked.example', 'show_on_home': False, 'fields': []},
        ]
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
            assert.equal($('bmVisibleCount').textContent, '3 个网址');
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
            STATE.bookmarks[0].show_on_home = false;
            renderHome();
            assert.equal($('homeList').innerHTML, '');
            assert.ok($('homeEmpty').innerHTML.includes('自定义首页'));
        """)

    def test_dashboard_sites_join_the_home_in_both_densities(self):
        self.run_js("""
            assert.deepEqual(homeSites().map(x => x.i), [0]);
            assert.equal(libPages()[0].count, 3);
            // 默认是图标视图：看板站点排在分组之前，带一条压缩过的指标。
            assert.equal(homeView(), 'tiles');
            let html = $('homeList').innerHTML;
            assert.equal($('homeList').className, 'home-list is-tiles');
            assert.ok(html.indexOf('看板站') < html.indexOf('Other selected'));
            assert.ok(html.includes('tile-metric'));
            assert.ok(html.includes('>27.07<'));
            assert.ok(!html.includes('27.0660232000'));
            assert.ok(!html.includes('未选站'));
            assert.ok(html.includes('toggleHomeSite(0)'));
            // 卡片视图复用看板卡片，字段完整显示；首页上不提供排序。
            setHomeView('cards');
            html = $('homeList').innerHTML;
            assert.equal($('homeList').className, 'home-list is-cards');
            assert.ok(html.includes('bookmark-card'));
            assert.ok(html.includes('27.0660232000'));
            assert.ok(html.includes('从首页移除'));
            assert.ok(!html.includes(\"moveBm(0,'top')\"));
            assert.ok(document.cookie.includes('bh_home_view=cards'));
            // 看板页自己的卡片仍带排序，并能把站点放上首页。
            const own = bookmarkCardHtml(STATE.bookmarks[1], 1, {});
            assert.ok(own.includes(\"moveBm(1,'top')\"));
            assert.ok(own.includes('展示到首页'));
        """)

    def test_site_metric_states_stay_short(self):
        self.run_js("""
            const site = (field) => ({ name: 'S', url: 'https://s.example', fields: field ? [Object.assign({ id: 'f', label: '余额', enabled: true }, field)] : [] });
            assert.equal(siteMetricHtml(site(null)), '');
            assert.ok(siteMetricHtml(site({ type: 'amount', value: '1234.5', unit: 'USD' })).includes('>1,234.5 USD<'));
            assert.ok(siteMetricHtml(site({ type: 'amount' })).includes('is-stale'));
            const failed = siteMetricHtml(site({ type: 'amount', value: '3', error: '<b>HTTP 500</b>' }));
            assert.ok(failed.includes('is-error') && failed.includes('取数失败'));
            assert.ok(!failed.includes('<b>HTTP'));
            const soon = siteMetricHtml(site({ type: 'time', value: '2030-01-01', raw: Date.now() / 1000 + 86400 * 2 }));
            assert.ok(soon.includes('is-warning') && soon.includes('天后'));
            const past = siteMetricHtml(site({ type: 'time', value: '2020-01-01', raw: 1577836800 }));
            assert.ok(past.includes('is-error'));
        """)

    def test_home_search_prefers_bookmarks_then_falls_back_to_the_web(self):
        self.run_js("""
            assert.deepEqual(homeSearchRows('   '), []);
            let rows = homeSearchRows('other');
            assert.equal(rows[0].type, 'item');
            assert.equal(rows[0].item.title, 'Other selected');
            assert.equal(rows[rows.length - 1].type, 'web');
            // 搜的是整个收藏库，不只是首页上那几个。
            assert.equal(homeSearchRows('hidden')[0].item.title, 'Hidden site');
            // 像网址：先给「直接打开」，库里没有时再给「收藏它」。
            rows = homeSearchRows('new-site.example/path');
            assert.deepEqual(rows.map(r => r.type), ['url', 'add', 'web']);
            assert.equal(rows[0].url, 'https://new-site.example/path');
            assert.deepEqual(homeSearchRows('docs.example').map(r => r.type), ['url', 'item', 'web']);
            // #标签 是库内语法，不丢给搜索引擎。
            assert.ok(!homeSearchRows('#ai').some(r => r.type === 'web'));
            for (const text of ['hello world', 'javascript:alert(1)', 'just-a-word', 'a b.com'])
                assert.equal(looksLikeUrl(text), '', text);
            for (const text of ['http://x.example', 'localhost:8080', '192.168.6.1/ui', 'Sub.Example.COM'])
                assert.ok(looksLikeUrl(text), text);
            // 行内容全部转义。
            const html = homeSearchRowHtml({ type: 'web', query: '<img src=x onerror=1>' }, 0);
            assert.ok(!html.includes('<img'));
        """)

    def test_enter_runs_a_web_search_with_the_chosen_engine(self):
        self.run_js("""
            const opened = [];
            globalThis.open = (url) => { opened.push(url); };
            runHomeSearchRow({ type: 'web', query: 'a b&c' });
            assert.deepEqual(opened, ['https://www.google.com/search?q=a%20b%26c']);
            cycleEngine();
            assert.equal(currentEngine().id, 'bing');
            assert.equal($('homeEngineName').textContent, 'Bing');
            runHomeSearchRow({ type: 'web', query: 'x' });
            assert.equal(opened[1], 'https://www.bing.com/search?q=x');
            runHomeSearchRow({ type: 'url', url: 'javascript:alert(1)' });
            assert.equal(opened.length, 2);
        """)

    def test_preferences_fall_back_when_the_cookie_is_tampered(self):
        self.run_js("""
            assert.ok(linkTargetAttrs().includes('target="_blank"'));
            document.cookie = 'bh_open=same';
            assert.equal(openMode(), 'same');
            assert.ok(!linkTargetAttrs().includes('target='));
            assert.ok(linkTargetAttrs().includes('noopener'));
            document.cookie = 'bh_open=evil; bh_engine=nope; bh_home_view=grid';
            assert.equal(openMode(), 'new');
            assert.equal(currentEngine().id, 'google');
            assert.equal(homeView(), 'tiles');
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

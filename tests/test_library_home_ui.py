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
            "{ const t = document.getElementById('navToggle'); t.addEventListener = (name, fn) => { t[name] = fn; }; }",
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

    def test_empty_home_guides_by_cause(self):
        """首页空着的三种原因各有引导；库本身是空的（新部署）不能再引去「自定义首页」——那里只有一张空表。"""
        self.run_js("""
            const empty = () => $('homeEmpty').innerHTML;
            // 1) 筛选没命中
            $('bmSearch').value = 'zzz-no-such-site';
            renderHome();
            assert.ok(empty().includes('没有匹配的网址') && !empty().includes('<button'));
            $('bmSearch').value = '';
            // 2) 库里有网址，只是都没上首页
            STATE.bookmarks.forEach(b => { b.show_on_home = false; });
            STATE.link_groups.forEach(g => g.links.forEach(l => { l.show_on_home = false; }));
            renderHome();
            assert.ok(empty().includes('openHomeModal()') && empty().includes('自定义首页'));
            // 3) 有分组、没有任何网址：添加第一个网址 + 导入
            STATE.bookmarks = [];
            STATE.link_groups.forEach(g => { g.links = []; });
            renderHome();
            assert.ok(!empty().includes('自定义首页'));
            assert.ok(empty().includes('添加第一个网址') && empty().includes("$('homeAddLink').click()"));
            assert.ok(empty().includes('导入 JSON / 备份') && empty().includes("$('importCfg').click()"));
            assert.ok(empty().includes('class="btn ghost sm"'));
            // 4) 连分组都没有：先建分组
            STATE.link_groups = [];
            renderHome();
            assert.ok(empty().includes('新建第一个分组') && empty().includes('openGroupModal(null)'));
            assert.ok(!empty().includes('添加第一个网址'));
            // 5) 访客（未解锁）看不到写操作，只给解锁入口
            STATE.admin_required = true; STATE.admin_unlocked = false;
            renderHome();
            assert.ok(empty().includes('去解锁') && empty().includes("switchView('settings')"));
            assert.ok(!empty().includes('importCfg') && !empty().includes('openGroupModal'));
        """)

    def test_dashboard_sites_join_the_home_in_every_density(self):
        self.run_js("""
            assert.deepEqual(homeSites().map(x => x.i), [0]);
            assert.equal(libPages()[0].count, 3);
            // 默认是「信息」密度（沿用旧 Cookie 值 tiles）：看板站点排在分组之前，带监控字段的渲染成 2×1 小组件。
            assert.equal(homeView(), 'tiles');
            let html = $('homeList').innerHTML;
            assert.equal($('homeList').className, 'home-list is-tiles');
            assert.ok(html.indexOf('看板站') < html.indexOf('Other selected'));
            assert.ok(html.includes('home-widget'));
            assert.ok(html.includes('>27.07<'));
            assert.ok(!html.includes('27.0660232000'));
            assert.ok(!html.includes('未选站'));
            assert.ok(html.includes('toggleHomeSite(0)'));
            // 小组件占 2 格：分组宽度与手机列数按格数算，而不是按站点个数。
            assert.ok(html.includes('--n:2;--span:2'));
            // 极简密度：只有图标和名字；没有要处理的事就不显示指标，分组标题由样式收起。
            setHomeView('minimal');
            html = $('homeList').innerHTML;
            assert.equal($('homeList').className, 'home-list is-tiles is-minimal');
            assert.ok(!html.includes('home-widget') && !html.includes('tile-metric'));
            assert.ok(html.includes('看板站') && html.includes('toggleHomeSite(0)'));
            assert.ok(html.includes('--n:1;--span:1'));
            // ……一旦取数失败，极简密度也要把它亮出来。
            STATE.bookmarks[0].fields[0].error = 'HTTP 500';
            renderHome();
            assert.ok($('homeList').innerHTML.includes('tile-metric is-error'));
            delete STATE.bookmarks[0].fields[0].error;
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

    def test_widget_shows_balance_expiry_and_status(self):
        self.run_js("""
            const day = 86400, now = Date.now() / 1000;
            const site = (fields) => ({ name: 'S', url: 'https://s.example', fields: fields.map((f, k) => Object.assign({ id: 'f' + k, enabled: true }, f)) });
            // 主数值取金额字段，次要信息优先取时间字段（到期）。
            let w = siteWidgetParts(site([
                { label: '到期', type: 'time', value: '2030-01-01', raw: now + 40 * day },
                { label: '余额', type: 'amount', value: '1234.5', unit: 'USD' }]));
            assert.deepEqual([w.state, w.value, w.unit, w.label], ['ok', '1,234.5', 'USD', '余额']);
            assert.ok(w.sub.startsWith('到期 · 还有 '));
            // 状态取最严重的一类：快到期 → 警告；已过期 / 取数失败 → 错误。
            w = siteWidgetParts(site([{ label: '余额', type: 'amount', value: '3' }, { label: '到期', type: 'time', value: 'x', raw: now + 2 * day }]));
            assert.equal(w.state, 'soon');
            assert.ok(homeSiteWidgetHtml(site([{ label: '到期', type: 'time', value: 'x', raw: now - day }]), 0).includes('home-widget tone-mint is-error'));
            w = siteWidgetParts(site([{ label: '余额', type: 'amount', value: '3', error: '<b>HTTP 500</b>' }]));
            assert.deepEqual([w.state, w.value, w.valueCls], ['error', '取数失败', ' is-error']);
            // 还没刷新过：灰色「待刷新」，不是绿色「正常」。
            w = siteWidgetParts(site([{ label: '余额', type: 'amount' }]));
            assert.deepEqual([w.state, w.value], ['stale', '待刷新']);
            // 只有一个字段时，次要信息退回「字段名 · 更新时间」。
            w = siteWidgetParts(site([{ label: '余额', type: 'amount', value: '3', updated_at: '2026-01-02 03:04:05' }]));
            assert.ok(w.sub.startsWith('余额 · ') && w.sub.endsWith('更新'));   // 「余额 · 3 天前更新」：新旧用相对时间说
            // 状态不能只靠颜色：状态点带文字标签；站点名、字段值一律转义。
            const html = homeSiteWidgetHtml({ name: '<i>x</i>', url: 'https://s.example', fields: [{ id: 'f', enabled: true, label: '余额', type: 'raw', value: '<script>' }] }, 3);
            assert.ok(html.includes('aria-label="状态：正常"'));
            assert.ok(!html.includes('<i>x</i>') && !html.includes('<script>'));
            assert.ok(html.includes('refreshBmBalance(3)') && html.includes('data-bm-index="3"'));
            // 没配监控字段的站点不是小组件，就是普通图标。
            assert.ok(!homeSiteHtml({ name: 'P', url: 'https://p.example', fields: [] }, 1, false).includes('home-widget'));
        """)

    def test_tiles_are_borderless_icons_with_name_only(self):
        self.run_js("""
            const html = homeLinkTileHtml({ id: 'l9', name: 'Docs', url: 'https://docs.example/path' }, { id: 'daily', name: '常用', color: 'sky' });
            assert.ok(html.includes('tile-avatar') && html.includes('tile-title'));
            // 域名不再占一行，收进 title 里。
            assert.ok(!html.includes('tile-host'));
            assert.ok(html.includes('title="Docs · https://docs.example/path"'));
            // 低清 favicon 不放大：量到原图不足 64px 就标记，矢量图（naturalWidth 0）与高清图照常铺满。
            const img = (w) => { const el = document.createElement('img'); el.naturalWidth = w; markIconResolution(el); return el.classList.contains('is-lowres'); };
            assert.deepEqual([img(16), img(32), img(64), img(180), img(0)], [true, true, false, false, false]);
        """)

    def test_wallpaper_and_rail_are_global_and_follow_the_cookie(self):
        self.run_js("""
            const root = document.documentElement, layer = $('homeWall');
            const look = () => [root.classList.contains('wall-on'), root.classList.contains('nav-rail')];
            // 默认不开壁纸（内置壁纸都是深色，一开整页固定深色场景）：只有图标栏。
            assert.equal(currentWallpaper(), null);
            assert.deepEqual(look(), [false, true]);
            // 手动选了壁纸就全局生效，不看当前在哪个页面：首页、看板、分组、签到、设置都是同一套。
            setCookie('bh_wallpaper=aurora');
            applyLook();
            assert.equal(currentWallpaper().id, 'aurora');
            assert.deepEqual(look(), [true, true]);
            assert.equal(layer.style['--wall-color'], '#112f49');
            openLibPage('monitor');
            assert.deepEqual(look(), [true, true]);
            openLibPage('second');
            assert.deepEqual(look(), [true, true]);
            switchView('checkin');
            assert.deepEqual(look(), [true, true]);
            switchView('settings');
            assert.deepEqual(look(), [true, true]);
            document.querySelector('.tab[data-view="bookmarks"]').click();
            assert.deepEqual(look(), [true, true]);
            // 关掉壁纸 / 换成完整侧栏：各自独立，同样对所有页面生效。
            setCookie('bh_wallpaper=off; bh_home_nav=full');
            applyLook();
            assert.deepEqual(look(), [false, false]);
            switchView('settings');
            assert.deepEqual(look(), [false, false]);
            setCookie('bh_wallpaper=dusk; bh_home_nav=full');   // 替身里的 cookie 是整串覆盖，不是罐子
            openLibPage('monitor');
            assert.deepEqual(look(), [true, false]);
            assert.equal(layer.style['--wall-color'], '#573352');
            // 侧栏底部的按钮和外观弹窗是同一个偏好：点一下收起，再点一下展开，提示文字跟着换。
            $('navToggle').click();
            assert.deepEqual([homeNavPref(), look()[1], $('navToggle').title], ['rail', true, '展开侧栏']);
            $('navToggle').click();
            assert.deepEqual([homeNavPref(), look()[1], $('navToggle').title], ['full', false, '收起侧栏']);
            setCookie('bh_wallpaper=dusk; bh_home_nav=full');   // 上面 writePref 把整串 cookie 换掉了，壁纸选择重新写回
            // 高对比 / 强制颜色：壁纸整个让路，导航形态不受影响。
            const mm = globalThis.matchMedia;
            globalThis.matchMedia = (q) => ({ matches: q.includes('forced-colors'), addEventListener() {} });
            applyLook();
            assert.deepEqual(look(), [false, false]);
            globalThis.matchMedia = mm;
            applyLook();
            assert.deepEqual(look(), [true, false]);
            // Cookie 被改成别的值只会回落到默认，不会拿去拼地址。
            setCookie('bh_wallpaper=../../evil; bh_wp_dim=9; bh_home_nav=x');
            assert.deepEqual([wallPref(), wallDimPref(), homeNavPref()], ['off', 'medium', 'rail']);
            assert.equal(currentWallpaper(), null);
            // 选了「自定义」但服务器上没有：回落到默认内置；有了才用，地址带内容哈希。
            setCookie('bh_wallpaper=custom');
            assert.equal(currentWallpaper().id, 'aurora');
            STATE.wallpaper = { custom: true, v: '0123456789abcdef', lum: 0.9 };
            assert.equal(currentWallpaper().url, '/api/wallpaper?v=0123456789abcdef');
            STATE.wallpaper = { custom: true, v: '"><img src=x>', lum: 0.9 };
            assert.equal(currentWallpaper().id, 'aurora');
        """)

    def test_scrim_is_strong_enough_for_white_text(self):
        self.run_js("""
            // 白字 4.5:1 → 背景相对亮度 ≤ 0.183。按页面真实的合成方式验算：遮罩色是 rgb(4 7 12) 而不是纯黑，
            // 在 sRGB 值上按 alpha 混合，再用标准传递函数换回相对亮度（取灰阶画面，亮度为 lum）。
            const lin = (c) => c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
            const enc = (l) => l <= 0.0031308 ? l * 12.92 : 1.055 * Math.pow(l, 1 / 2.4) - 0.055;
            const after = (lum, dim) => {
                const c = enc(lum), mix = (s) => lin(c * (1 - dim) + (s / 255) * dim);
                return 0.2126 * mix(4) + 0.7152 * mix(7) + 0.0722 * mix(12);
            };
            for (const lum of [0.17, 0.2, 0.4, 0.7, 0.92, 1]) {
                const dim = wallDimFor({ lum });
                assert.ok(after(lum, dim) <= 0.183, 'lum ' + lum + ' dim ' + dim + ' -> ' + after(lum, dim));
                assert.ok((1.05) / (after(lum, dim) + 0.05) >= 4.5, 'contrast at lum ' + lum);
            }
            assert.equal(wallMinDim(0.1), 0);
            // 量不到亮度（旧数据 / 被篡改）按最亮处理，而不是当成不需要遮罩。
            assert.equal(wallMinDim(null), wallMinDim(1));
            assert.equal(wallMinDim('0.1'), wallMinDim(1));
            // 内置壁纸在「柔和」档也够暗；偏亮的自定义图会自动加深，用户档位只能更暗不能更亮。
            setCookie('bh_wp_dim=soft');
            for (const w of WALLPAPERS) assert.ok(after(w.lum, wallDimFor(w)) <= 0.183, w.id);
            assert.equal(wallDimFor({ lum: 0.1 }), WALL_DIMS.soft);
            assert.ok(wallDimFor({ lum: 0.95 }) > WALL_DIMS.soft);
            // 写进样式变量的值向上取整到两位小数，不会因为取整把余量舍掉。
            assert.ok(wallDimFor({ lum: 0.92 }) >= wallMinDim(0.92));
            assert.equal(String(wallDimFor({ lum: 0.92 })).length <= 4, true);
            setCookie('bh_wp_dim=strong');
            assert.equal(wallDimFor({ lum: 0.3 }), WALL_DIMS.strong);
        """)

    def test_icon_plate_follows_the_icons_own_brightness(self):
        # 回归：透明底的白色 logo 垫在浅色底板上是一片纯白（壁纸首页上尤其显眼）。底板按图标自身明暗来选。
        self.run_js("""
            const px = (n, rgba) => { const out = []; for (let i = 0; i < n; i++) out.push(...rgba); return out; };
            const clear = (n) => px(n, [0, 0, 0, 0]);
            // 透明底 + 白色 / 浅黄图形 → 'light'（换深色底板）；透明底 + 黑色 / 深蓝图形 → 'dark'（换浅色底板）。
            assert.equal(iconToneFromPixels(px(200, [255, 255, 255, 255]).concat(clear(376))), 'light');
            assert.equal(iconToneFromPixels(px(200, [255, 210, 30, 255]).concat(clear(376))), 'light');
            assert.equal(iconToneFromPixels(px(200, [36, 41, 47, 255]).concat(clear(376))), 'dark');
            assert.equal(iconToneFromPixels(px(200, [10, 37, 64, 255]).concat(clear(376))), 'dark');
            // 彩色图标保持默认底板：不因为「不够白也不够黑」就乱换。
            assert.equal(iconToneFromPixels(px(200, [255, 69, 0, 255]).concat(clear(376))), '');
            assert.equal(iconToneFromPixels(px(120, [255, 255, 255, 255]).concat(px(120, [20, 20, 20, 255]), clear(336))), '');
            // 彩色圆底 + 白色小图形（多数像素是彩色）不算浅色图标。
            assert.equal(iconToneFromPixels(px(300, [40, 160, 230, 255]).concat(px(100, [255, 255, 255, 255]), clear(176))), '');
            // 自带底色、几乎铺满的图标：底板露不出来，不挂类；全透明的空图也一样。
            assert.equal(iconToneFromPixels(px(560, [255, 255, 255, 255]).concat(clear(16))), '');
            assert.equal(iconToneFromPixels(clear(576)), '');
            // 半透明的抗锯齿边缘按不透明度计权，近乎透明的像素忽略。
            assert.equal(iconToneFromPixels(px(150, [255, 255, 255, 255]).concat(px(400, [0, 0, 0, 20]), clear(26))), 'light');

            // markIconTone：量一次、按站点记住，只在头像容器上挂类；图标本身不加任何滤镜 / 反色。
            let draws = 0, boom = false, pixels = px(200, [255, 255, 255, 255]).concat(clear(376));
            const realCreate = document.createElement;
            document.createElement = (tag) => tag !== 'canvas' ? realCreate(tag) : {
                getContext: () => ({ clearRect() {}, drawImage() { draws++; },
                    getImageData: () => { if (boom) throw new Error('tainted'); return { data: pixels }; } }) };
            const avatar = makeEl('avatar'), img = makeEl('img');
            img.parentElement = avatar;
            img.dataset.favicon = 'https://white-logo.example';
            markIconTone(img);
            assert.deepEqual([avatar.classList.contains('icon-light'), avatar.classList.contains('icon-dark'), draws], [true, false, 1]);
            assert.deepEqual(Object.keys(img.style), []);
            assert.ok(!img.classList.contains('icon-light'));
            markIconTone(img);
            assert.equal(draws, 1);
            // 换成深色图标的站点：类跟着换，不会两个都挂着。
            pixels = px(200, [0, 0, 0, 255]).concat(clear(376));
            const other = makeEl('img2');
            other.parentElement = avatar;
            other.dataset.favicon = 'https://black-logo.example';
            markIconTone(other);
            assert.deepEqual([avatar.classList.contains('icon-light'), avatar.classList.contains('icon-dark')], [false, true]);
            // 上传的备用图：自动图标已经就位时不再量；自动图标缺席时才由它决定底板。
            const upload = makeEl('upload'), auto = makeEl('auto'), plate = makeEl('plate');
            upload.parentElement = plate;
            upload.previousElementSibling = auto;
            auto.classList.add('is-ready');
            uploadedIconReady(upload);
            assert.deepEqual([upload.classList.contains('is-ready'), plate.classList.contains('icon-dark'), draws], [true, false, 2]);
            auto.classList.remove('is-ready');
            uploadedIconReady(upload);
            assert.deepEqual([plate.classList.contains('icon-dark'), draws], [true, 3]);
            // 读像素抛异常（解码失败之类）：回到默认底板，不报错；已经从文档里摘掉的图标直接跳过。
            document.createElement = realCreate;
            boom = true;
            const img3 = makeEl('img3');
            img3.parentElement = plate;
            img3.dataset.favicon = 'https://broken.example';
            markIconTone(img3);
            assert.ok(!plate.classList.contains('icon-light') && !plate.classList.contains('icon-dark'));
            markIconTone(makeEl('orphan'));
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
            assert.ok(soon.includes('is-warning') && soon.includes('还有 '));
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
            setCookie('bh_open=same');
            assert.equal(openMode(), 'same');
            assert.ok(!linkTargetAttrs().includes('target='));
            assert.ok(linkTargetAttrs().includes('noopener'));
            setCookie('bh_open=evil; bh_engine=nope; bh_home_view=grid');
            assert.equal(openMode(), 'new');
            assert.equal(currentEngine().id, 'google');
            assert.equal(homeView(), 'tiles');
            setCookie('bh_home_view=minimal');
            assert.equal(homeView(), 'minimal');
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
            // 失败只挡一阵子：刚失败时不再渲染 <img>，过了重试间隔，下一次重绘又会带上它。
            assert.ok(!siteAvatarHtml('Bad', 'https://failed.example', 'link-avatar').includes('data-favicon='));
            FAVICON_FAILED_AT.set('https://failed.example', Date.now() - FAVICON_RETRY_MS - 1);
            assert.ok(siteAvatarHtml('Bad', 'https://failed.example', 'link-avatar').includes('data-favicon="https://failed.example"'));
            assert.equal(FAVICON_MEMO.has('https://failed.example'), false);
        """)

    def test_engine_button_opens_a_menu_instead_of_cycling(self):
        self.run_js("""
            setEngineMenu(false);
            assert.equal(engineMenuOpen(), false);
            setEngineMenu(true);
            assert.equal(engineMenuOpen(), true);
            const html = $('homeEngineMenu').innerHTML;
            for (const id of ['google', 'bing', 'baidu', 'ddg']) assert.ok(html.includes(`data-engine="${id}"`), id);
            // 只有当前引擎带选中标记；打开菜单本身不改变引擎。
            assert.equal((html.match(/is-current/g) || []).length, 1);
            assert.ok(/is-current[^>]*data-engine="google"/.test(html));
            assert.equal(currentEngine().id, 'google');
            pickEngine('ddg');
            assert.equal(currentEngine().id, 'ddg');
            assert.equal($('homeEngineName').textContent, 'DuckDuckGo');
            assert.equal(engineMenuOpen(), false);
            assert.ok(document.cookie.includes('bh_engine=ddg'));
            // 菜单开着时用 Tab 轮换，列表里的选中项要跟着变。
            setEngineMenu(true);
            cycleEngine();
            assert.ok(/is-current[^>]*data-engine="google"/.test($('homeEngineMenu').innerHTML));
        """)

    def test_password_field_is_only_mounted_on_locked_settings(self):
        # 页面上只要存在密码框，手机浏览器就会把首页搜索框当成「用户名」来提示 / 填充已存密码。
        self.run_js("""
            let mounted = 0, removed = 0;
            ADMIN_PWD.isConnected = false;
            ADMIN_PWD.remove = () => { ADMIN_PWD.isConnected = false; removed++; };
            $('adminPwdSlot').appendChild = (el) => { assert.equal(el, ADMIN_PWD); el.isConnected = true; mounted++; };
            STATE.admin_required = true;
            STATE.admin_unlocked = false;
            // 锁定态停在首页：不挂。
            syncAdminPwd();
            assert.deepEqual([mounted, ADMIN_PWD.isConnected], [0, false]);
            // 进系统设置：挂上，且不重复挂。
            switchView('settings');
            assert.deepEqual([mounted, ADMIN_PWD.isConnected], [1, true]);
            syncAdminPwd();
            assert.equal(mounted, 1);
            // 解锁后立刻摘掉，并清空残留输入。
            ADMIN_PWD.value = 'typed-but-not-submitted';
            STATE.admin_unlocked = true;
            renderSettings();
            assert.deepEqual([removed, ADMIN_PWD.isConnected, ADMIN_PWD.value], [1, false, '']);
            // 重新锁定仍在设置页：挂回；离开设置页：再摘掉。
            STATE.admin_unlocked = false;
            renderSettings();
            assert.equal(ADMIN_PWD.isConnected, true);
            switchView('bookmarks');
            assert.equal(ADMIN_PWD.isConnected, false);
            // 没设管理密码的实例永远不需要它。
            STATE.admin_required = false;
            switchView('settings');
            assert.equal(ADMIN_PWD.isConnected, false);
        """)


class StartPageFormsMarkupTest(unittest.TestCase):
    """搜索框与管理密码框必须各自成表单：两者都「无表单」时会被浏览器归成同一个虚拟登录表单。"""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.html = app.test_client().get('/').get_data(as_text=True)

    def block(self, start, end):
        begin = self.html.index(start)
        return self.html[begin:self.html.index(end, begin)]

    def test_search_lives_in_its_own_search_form(self):
        form = self.block('<form class="home-search-bar" id="homeSearchForm"', '</form>')
        self.assertIn('role="search"', form)
        self.assertIn('id="homeSearch" name="q" type="search" autocomplete="off"', form)
        for hint in ('data-1p-ignore', 'data-lpignore="true"', 'data-bwignore', 'data-form-type="other"'):
            self.assertIn(hint, form)
        self.assertNotIn('type="password"', form)
        # 隐式提交（手机键盘的「搜索」键）要被接住，不能真的把表单提交出去。
        self.assertIn("$('homeSearchForm').addEventListener('submit'", self.html)

    def test_password_has_its_own_form_with_a_username_anchor(self):
        self.assertEqual(self.html.count('type="password"'), 1)
        form = self.block('<form class="admin-form" id="adminForm"', '</form>')
        self.assertIn('type="password"', form)
        self.assertIn('autocomplete="current-password"', form)
        self.assertIn('autocomplete="username"', form)
        self.assertIn('<button type="submit" class="btn sm" id="unlockBtn">', form)
        self.assertNotIn('id="homeSearch"', form)
        # 密码只经请求头发送；表单提交必须被拦下，否则会以 GET 把密码带进地址栏。
        submit = self.block("$('adminForm').addEventListener('submit'", "});")
        self.assertIn('e.preventDefault();', submit)
        self.assertIn("'X-Admin-Password': pwd", submit)

    def test_password_input_is_detached_at_boot(self):
        boot = self.block("const ADMIN_PWD = $('adminPwd');", 'function syncAdminPwd()')
        self.assertIn('ADMIN_PWD.remove();', boot)

    def test_engine_button_is_a_dropdown_trigger(self):
        self.assertIn('id="homeEngineBtn" title="选择网页搜索引擎"', self.html)
        self.assertIn('aria-haspopup="listbox"', self.html)
        self.assertIn('id="homeEngineMenu" role="listbox"', self.html)
        click = self.block("$('homeEngineBtn').addEventListener('click'", "});")
        self.assertIn('setEngineMenu(open);', click)
        self.assertNotIn('cycleEngine', click)


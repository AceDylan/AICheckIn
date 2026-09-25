# -*- coding: utf-8 -*-
"""0925 体验打磨：签到中心分段标签、命令面板里的页面 / 命令、小组件到期时间不再被截断。"""
import unittest
from pathlib import Path

from tests import test_library_home_ui as home_ui
from tests.test_script_boot import NODE, TEMPLATE

CSS = Path(TEMPLATE).parent.parent / 'static' / 'app-v3.css'


class ThemeColorTest(unittest.TestCase):
    def test_theme_color_follows_the_chosen_theme(self):
        html = Path(TEMPLATE).read_text()
        self.assertIn("function applyThemeColor(pref)", html)
        self.assertIn("applyThemeColor(pref);", html)
        self.assertIn("applyThemeColor(w ? 'dark' : readTheme());", html)


class CheckinTabsMarkupTest(unittest.TestCase):
    def test_tabs_are_one_segmented_nav_and_subpages_have_no_back_link(self):
        html = Path(TEMPLATE).read_text()
        self.assertIn('<nav class="segmented checkin-tabs" aria-label="签到中心">', html)
        self.assertNotIn('返回签到概览', html)
        self.assertNotIn('workspace-subheader', html)
        # 内页自己的操作都在标签那一行，按内页切换显示。
        for act in ('checkinActOverview', 'checkinActConfigs', 'checkinActHistory'):
            self.assertIn(f'id="{act}"', html)
        css = CSS.read_text()
        self.assertNotIn('.back-link', css)
        self.assertIn('.home-list.is-tiles .home-section .section-label::after { order: 3; }', css)


@unittest.skipIf(NODE is None, '未安装 node')
class UxPolishJsTest(unittest.TestCase):
    run_js = home_ui.LibraryHomeUiTest.run_js

    def test_current_checkin_tab_is_marked_and_actions_follow_it(self):
        self.run_js("""
            switchView('checkin/configs');
            assert.deepEqual([$('checkinActOverview').hidden, $('checkinActConfigs').hidden, $('checkinActHistory').hidden], [true, false, true]);
            switchView('checkin');
            assert.deepEqual([$('checkinActOverview').hidden, $('checkinActConfigs').hidden, $('checkinActHistory').hidden], [false, true, true]);
        """)

    def test_palette_lists_pages_and_commands_only_after_typing(self):
        self.run_js("""
            assert.ok(!omniSearch('').some(i => i.kind === 'command'));
            const titles = (q) => omniSearch(q).filter(i => i.kind === 'command').map(i => i.title);
            assert.ok(titles('签到').includes('签到中心') && titles('签到').includes('运行全部签到'));
            assert.equal(omniSearch('设置')[0].title, '系统设置');
            assert.ok(titles('theme').length === 1 && /^切换到/.test(titles('theme')[0]));
            // 设置页的各节也能搜到。
            assert.ok(titles('代理').includes('全局代理') && titles('备份').includes('配置恢复（备份）'));
            // 跑一条命令：关掉面板、切到对应页面。
            omniOpen(omniSearch('运行记录').find(i => i.kind === 'command'));
            assert.equal(currentViewName(), 'history');
            // 没有编辑权限时，签到 / 添加这类命令根本不出现。
            STATE.admin_required = true; STATE.admin_unlocked = false;
            assert.deepEqual(titles('签到'), []);
            assert.deepEqual(titles('添加网址'), []);
            assert.ok(titles('设置').includes('系统设置'));
            assert.deepEqual(titles('代理'), []);   // 改不了的设置节也不列
            assert.ok(titles('密码').includes('管理密码 · 解锁'));
        """)

    def test_widget_time_value_splits_number_from_words(self):
        self.run_js("""
            const now = Date.now() / 1000;
            const site = (fields) => ({ name: 'S', url: 'https://s.example', fields: fields.map((f, k) => Object.assign({ id: 'f' + k, enabled: true }, f)) });
            let w = siteWidgetParts(site([{ label: '到期', type: 'time', value: 'x', raw: now - 16 * 3600 - 60 }]));
            assert.deepEqual([w.value, w.unit, w.valueCls], ['16', '小时前过期', ' is-past']);
            w = siteWidgetParts(site([{ label: '到期', type: 'time', value: 'x', raw: now + 70 * 86400 }]));
            assert.deepEqual([w.value, w.unit, w.valueCls], ['2', '个月后到期', '']);
        """)

    def test_checkin_accounts_switch_to_list_when_many_or_when_chosen(self):
        self.run_js("""
            assert.equal(ckView(3), 'cards');
            assert.equal(ckView(7), 'list');
            setCookie('bh_ck_view=cards; path=/');
            assert.equal(ckView(12), 'cards');
            setCookie('bh_ck_view=bogus; path=/');
            assert.equal(ckView(2), 'cards');
            const row = enabledRowHtml({ name: '<b>x</b>', base_url: 'https://a.example', user_id: '1', enabled: true, metrics: {} }, 0);
            assert.ok(row.includes('checkinOne(0)') && row.includes('testOne(0)') && !row.includes('<b>x</b>'));
        """)

    def test_stale_field_says_how_old_in_words(self):
        self.run_js("""
            const pad = (n) => String(n).padStart(2, '0');
            const stamp = (ms) => { const d = new Date(Date.now() - ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:00`; };
            assert.equal(agoText(stamp(5 * 86400000 + 60000)), '5 天前');
            assert.equal(agoText(stamp(3 * 3600000 + 60000)), '3 小时前');
            assert.equal(agoText('bogus'), '');
            assert.equal(agoText(stamp(65 * 86400000)), '2 个月前');
            // 取数 / 签到这类「多久以前」统一说相对时间，解析不了的原样退回短时间格式。
            assert.equal(agoStamp(stamp(2 * 86400000 + 60000)), '2 天前');
            assert.equal(agoStamp('昨天'), '昨天');
        """)

    def test_run_all_finish_toast_counts_everything(self):
        self.run_js("""
            const seen = [];
            toast = (msg, kind) => seen.push([msg, kind]);
            finishCheckinJob({ finished_at: '08:30', summary: { signed: 3, skipped: 1, failed: 1, quota_total: '0.70' } });
            finishCheckinJob({ finished_at: '08:31', summary: { signed: 2, skipped: 0, failed: 0, quota_total: '0' } });
            assert.deepEqual(seen, [['签到完成：3 个成功，1 个今天已签过，1 个失败（原因见卡片），额度 +0.70', 'err'], ['签到完成：2 个成功', 'ok']]);
        """)

    def test_relative_times_tick_with_the_clock_without_rerendering(self):
        self.run_js("""
            const pad = (n) => String(n).padStart(2, '0');
            const d = new Date(Date.now() - 3 * 60000);
            const raw = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
            assert.ok(agoSpan(raw).startsWith('<span class="ago" data-ago="'));
            assert.ok(!agoSpan('<b>').includes('<b>'));
            assert.ok(tickHeroClock.toString().includes('refreshAgo()'));
            // 运行记录的明细行照常渲染说明（曾被一次批量替换误改成未定义的变量，整页记录加载失败）。
            assert.ok(histResultHtml({ name: 'A', status: 'failed', status_label: '失败', color: 'red', message: 'HTTP <401>' }).includes('HTTP &lt;401&gt;'));
        """)

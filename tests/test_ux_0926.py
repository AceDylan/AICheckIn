# -*- coding: utf-8 -*-
"""2026-09-26 体验评估的改进：加载 / 断网状态、看板提醒、数字格式、拼音搜索等。

页面脚本放进 node 的 DOM 替身里真跑（同 test_library_home_ui），需要时在脚本之前换掉 fetch。
"""
import copy
import json
import subprocess
import unittest
from pathlib import Path

from tests.test_script_boot import NODE, STUB, RESPONSES, _inline_script


def run_page(assertions, before='', responses=None, delay=30):
    data = copy.deepcopy(RESPONSES) if responses is None else responses
    script = '\n'.join([
        Path(STUB).read_text(),
        'globalThis.__RESPONSES = ' + json.dumps(data) + ';',
        "const assert = require('node:assert/strict');",
        "document.querySelectorAll('.tab').forEach(t => { t.addEventListener = (name, fn) => { t[name] = fn; }; });",
        "{ const t = document.getElementById('navToggle'); t.addEventListener = (name, fn) => { t[name] = fn; }; }",
        before,
        _inline_script(),
        'setTimeout(async () => { try {', assertions,
        "console.log('ok'); } catch(e) { console.error(e); process.exitCode = 1; } }, %d);" % delay,
    ])
    return subprocess.run([NODE], input=script, text=True, capture_output=True, timeout=20)


@unittest.skipIf(NODE is None, '未安装 node')
class BootStateTest(unittest.TestCase):
    """数据没到 / 没取到时，收藏页不能画成「空库引导」。"""

    def check(self, assertions, before=''):
        proc = run_page(assertions, before)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_pending_load_shows_skeleton_not_the_empty_library_guide(self):
        # /api/configs 迟迟不回：首页只有占位骨架，「从第一个网址开始 / 新建第一个分组」不出现。
        self.check("""
            assert.equal(STATE_LOADED, false);
            assert.ok($('homeList').innerHTML.includes('boot-skeleton'));
            assert.equal($('homeEmpty').style.display, 'none');
            assert.ok(!$('homeEmpty').innerHTML.includes('从第一个网址开始'));
            assert.equal($('homeAddLink').hidden, true);
            openLibPage('monitor');
            assert.ok($('bmList').innerHTML.includes('boot-skeleton'));
            assert.equal($('bmEmpty').style.display, 'none');
        """, before="""
            const realFetch = globalThis.fetch;
            globalThis.fetch = (url, opts) => String(url).startsWith('/api/configs') ? new Promise(() => {}) : realFetch(url, opts);
        """)

    def test_network_failure_says_offline_and_offers_retry(self):
        self.check("""
            assert.equal(STATE_LOADED, false);
            assert.equal(BOOT_FAILED, true);
            const html = $('homeEmpty').innerHTML;
            assert.ok(html.includes('连不上服务器'), html);
            assert.ok(html.includes('retryBoot()'));
            assert.ok(!html.includes('新建第一个分组'));
            assert.ok(!$('homeList').innerHTML.includes('boot-skeleton'));
            // 网络恢复后点「立即重试」：数据到位，首页恢复正常。
            globalThis.__fail = false;
            await retryBoot();
            assert.equal(STATE_LOADED, true);
            assert.equal(BOOT_FAILED, false);
            assert.equal($('homeEmpty').innerHTML.includes('连不上服务器'), false);
        """, before="""
            globalThis.__fail = true;
            const realFetch = globalThis.fetch;
            globalThis.fetch = (url, opts) => (globalThis.__fail && String(url).startsWith('/api/configs'))
              ? Promise.reject(new TypeError('Failed to fetch')) : realFetch(url, opts);
        """)

    def test_normal_load_replaces_the_skeleton(self):
        self.check("""
            assert.equal(STATE_LOADED, true);
            assert.ok(!$('homeList').innerHTML.includes('boot-skeleton'));
        """)


def locked_responses():
    data = copy.deepcopy(RESPONSES)
    data['/api/configs'].update({'private': True, 'locked': True, 'admin_required': True, 'admin_unlocked': False,
                                 'configs': [], 'bookmarks': [], 'link_groups': [], 'todos_locked': True, 'deck_locked': True,
                                 'chat': None, 'vault': None, 'vault_embed': None})
    return data


@unittest.skipIf(NODE is None, '未安装 node')
class LockedHomeTest(unittest.TestCase):
    """私密模式锁定（会话过期 / 新设备）：起始页还是起始页——时钟、搜索框照常，中间一张解锁卡片。"""

    def check(self, assertions, before=''):
        proc = run_page(assertions, before, responses=locked_responses())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_locked_start_page_keeps_search_and_offers_unlock(self):
        self.check("""
            assert.equal(currentViewName(), 'bookmarks');
            assert.equal(LIB.page, '@home');
            const card = $('homeEmpty').innerHTML;
            assert.ok(card.includes('收藏已锁定') && card.includes('unlockFromHome()'), card);
            assert.equal($('homeList').innerHTML, '');
            assert.equal($('homeDeckBtn').hidden, true);
            assert.equal($('homeAddLink').hidden, true);
            // 带数据的入口都收着：分组导航、全局搜索、签到中心。
            assert.equal($('libSubnav').hidden, true);
            assert.equal($('omniOpen').hidden, true);
            assert.equal(document.querySelector('.tab[data-view="checkin"]').hidden, true);
            // 手敲别的收藏页 / 签到页地址，都回到首页的解锁卡片。
            openLibPage('monitor');
            assert.equal(LIB.page, '@home');
            switchView('checkin');
            assert.equal(currentViewName(), 'bookmarks');
            // 设置页里收起占位数值那几节靠 CSS 选择器，DOM 替身不支持，放在真实浏览器回归里验（verify_0926）。
            unlockFromHome();
            assert.equal(currentViewName(), 'settings');
            assert.equal(UNLOCK_RETURN, true);
        """)

    def test_settings_hash_goes_straight_to_the_password(self):
        self.check("""
            assert.equal(currentViewName(), 'settings');
        """, before="location.hash = '#settings';")


def dashboard_responses():
    data = copy.deepcopy(RESPONSES)
    now = __import__('time').time()
    data['/api/configs']['bookmarks'] = [
        {'name': 'reclaude-5', 'url': 'https://reclaude.example', 'show_on_home': True, 'key': 'k0', 'fields': [
            {'id': 'bal', 'label': '余额', 'type': 'amount', 'enabled': True, 'value': '13.4625268000'},
            {'id': 'reset', 'label': '刷新时间', 'type': 'time', 'enabled': True, 'raw': now + 3 * 3600 + 120,
             'value': '2026-09-26 15:11:40', 'warn_days': 0}]},
        {'name': 'EXA', 'url': 'https://exa.example', 'show_on_home': False, 'key': 'k1', 'fields': [
            {'id': 'bal', 'label': '余额', 'type': 'amount', 'enabled': True, 'value': '27.47', 'error': 'HTTP 401'}]},
        {'name': 'VPS', 'url': 'https://vps.example', 'show_on_home': False, 'key': 'k2', 'fields': [
            {'id': 'exp', 'label': '到期', 'type': 'time', 'enabled': True, 'raw': now + 3 * 86400, 'value': 'x'}]},
    ]
    data['/api/bookmarks/1/secret'] = {'ok': True, 'bookmark': {'name': 'EXA', 'url': 'https://exa.example', 'fields': [
        {'id': 'other', 'label': '用量', 'type': 'amount', 'enabled': True, 'curl': "curl 'https://exa.example/a'", 'json_path': 'a'},
        {'id': 'bal', 'label': '余额', 'type': 'amount', 'enabled': True, 'curl': "curl 'https://exa.example/b'", 'json_path': 'b'}]}}
    return data


@unittest.skipIf(NODE is None, '未安装 node')
class DashboardDisplayTest(unittest.TestCase):
    def check(self, assertions):
        proc = run_page(assertions, responses=dashboard_responses())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_quiet_reset_time_is_a_countdown_not_an_expiry(self):
        self.check("""
            const b = STATE.bookmarks[0];
            assert.equal(bookmarkAlert(b), '');
            assert.equal(compactFieldValue(b.fields[1]), '还有 3 小时');
            const html = fieldItemHtml(b.fields[1], false, 0);
            assert.ok(html.includes('还有 3 小时'));
            assert.ok(!html.includes('is-soon') && !html.includes('到期'), html);
            // 首页提醒条只数真正的问题：EXA 取数失败 + VPS 3 天后到期，reclaude-5 的重置时间不算。
            renderHomeNotice();
            const notice = $('homeNotice').innerHTML;
            assert.ok(notice.includes('1 个取数失败') && notice.includes('1 个快到期'), notice);
            // 到期提醒组件里也没有它。
            assert.deepEqual(expiryRows().map(r => r.b.name), ['VPS']);
            // 小组件把倒计时当副标题，主值是余额。
            const parts = siteWidgetParts(b);
            assert.equal(parts.value, '13.46');
            assert.ok(parts.sub.includes('刷新时间 · 还有 3 小时'), parts.sub);
            assert.equal(parts.state, 'ok');
        """)

    def test_dashboard_amounts_match_the_home_widget(self):
        self.check("""
            const html = fieldItemHtml(STATE.bookmarks[0].fields[0], true, 0);
            assert.ok(html.includes('>13.46<'), html);
            assert.ok(html.includes('title="13.4625268000"'));
            assert.equal(amountText('1234567.891'), '1,234,567.89');
            assert.equal(amountText('$5'), '$5');
        """)

    def test_auth_failure_explains_itself_and_opens_the_right_field(self):
        self.check("""
            const html = fieldItemHtml(STATE.bookmarks[1].fields[0], true, 1);
            assert.ok(html.includes('登录凭据可能过期了'), html);
            assert.ok(html.includes('更新 cURL'));
            assert.ok(html.includes('fixBmField(1, this.dataset.fixField)'));
            assert.ok(html.includes('data-fix-field="bal"'));
            // 其它错误也给入口，但不乱猜原因。
            const other = fieldItemHtml(Object.assign({}, STATE.bookmarks[1].fields[0], { error: 'JSON 路径不存在' }), true, 1);
            assert.ok(other.includes('检查字段') && !other.includes('登录凭据'));
            await fixBmField(1, 'bal');
            assert.equal(bmDraftFields.length, 2);
            assert.deepEqual(bmDraftFields.map(d => d._open), [false, true]);
        """)

    def test_reset_like_time_field_offers_a_quiet_shortcut(self):
        self.check("""
            // 名字像「刷新 / 重置时间」、还按到期在提醒的字段：给「不再提醒…」；真正的到期字段（VPS）不给。
            const reset = Object.assign({}, STATE.bookmarks[0].fields[1], { warn_days: '' });
            const html = fieldItemHtml(reset, false, 0);
            assert.ok(html.includes('不再提醒…') && html.includes("fixBmField(0, this.dataset.fixField, 'quiet')"), html);
            assert.ok(!fieldItemHtml(STATE.bookmarks[2].fields[0], true, 2).includes('不再提醒'));
            assert.ok(!fieldItemHtml(STATE.bookmarks[0].fields[1], false, 0).includes('不再提醒'), '已经是不提醒的就不再给');
            // 点了：打开编辑、定位到这个字段、提醒天数预填 0（保存才生效）。
            STATE.bookmarks[1].fields[0] = Object.assign({}, STATE.bookmarks[1].fields[0], { error: '' });
            await fixBmField(1, 'bal', 'quiet');
            const d = bmDraftFields.find(x => x.id === 'bal');
            assert.equal(d.warn_days, '0');
            assert.deepEqual(bmDraftFields.map(x => x._open), [false, true]);
        """)

    def test_dashboard_meta_shows_the_refresh_cadence(self):
        self.check("""
            STATE.refresh = { enabled: true, interval_minutes: 360, last_run_time: '2026-09-26 06:00:00' };
            assert.ok(bmRefreshMetaHtml().includes('每 6 小时自动刷新') && bmRefreshMetaHtml().includes('上次'), bmRefreshMetaHtml());
            STATE.refresh = { enabled: false, interval_minutes: 360 };
            assert.ok(bmRefreshMetaHtml().includes('没开自动刷新') && bmRefreshMetaHtml().includes("goSettingsSection('refreshEnabled')"));
        """)

    def test_quiet_value_accepted_by_editor_validation(self):
        self.check("""
            const d = draftFromField({ id: 'r', label: '重置', type: 'time', curl: "curl 'https://x.example'", json_path: 'a', warn_days: 0 });
            assert.equal(d.warn_days, '0');
            assert.equal(validateDraft(d), '');
            assert.equal(draftToPayload(d).warn_days, 0);
            assert.ok(validateDraft(Object.assign({}, d, { warn_days: '-1' })).includes('0–365'));
        """)


@unittest.skipIf(NODE is None, '未安装 node')
class LibraryViewsTest(unittest.TestCase):
    """分组页「列表 / 图标」、看板「卡片 / 列表」两种显示方式。"""

    def check(self, assertions, responses=None):
        proc = run_page(assertions, responses=responses)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ok', proc.stdout)

    def test_group_page_can_switch_to_icon_tiles(self):
        self.check("""
            openLibPage('daily');
            assert.equal(linkView(), 'rows');
            assert.equal($('linkList').className, 'link-grid');
            assert.ok($('linkList').innerHTML.includes('link-card'));
            setCookie('bh_link_view=tiles; path=/');
            renderCurrentLibraryPage();
            assert.equal($('linkList').className, 'link-tiles');
            const html = $('linkList').innerHTML;
            assert.ok(html.includes('home-tile') && html.includes('Docs'), html);
            assert.ok(html.includes("editLink('daily','l1')"));    // 图标上的「···」仍能编辑
            assert.equal($('libView').hidden, false);
            setCookie('bh_link_view=evil; path=/');
            assert.equal(linkView(), 'rows');
            openLibPage('@home');
            assert.equal($('libView').hidden, true);
        """)

    def test_dashboard_list_rows(self):
        data = dashboard_responses()
        self.check("""
            openLibPage('monitor');
            assert.equal(bmView(), 'cards');   // 电脑上默认卡片（DOM 替身没有 matchMedia，算宽屏）
            setCookie('bh_bm_view=list; path=/');
            renderCurrentLibraryPage();
            assert.equal($('bmList').className, 'bm-rows');
            const html = $('bmList').innerHTML;
            assert.ok(html.includes('bm-row') && html.includes('reclaude-5'));
            assert.ok(html.includes('>13.46<'), '列表里的金额同首页小组件格式');
            assert.ok(html.includes('更新 cURL'), 'EXA 的 401 在列表里也有修复入口');
            assert.ok(html.includes('bm-refresh'), '刷新按钮仍带 bm-refresh（刷新时的忙碌态要找它）');
            // 没配字段的站点不报错
            STATE.bookmarks.push({ name: '空站', url: 'https://empty.example', fields: [] });
            assert.ok(bmRowHtml(STATE.bookmarks[3], 3, false).includes('未配置字段'));
        """, responses=data)


if __name__ == '__main__':
    unittest.main()

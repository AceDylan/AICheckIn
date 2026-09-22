# -*- coding: utf-8 -*-
"""签到内页保持导航归属、旧链接和解锁边界，精简主导航不丢失原功能。"""
import unittest
from html.parser import HTMLParser
from pathlib import Path

from tests import test_library_home_ui as home_ui
from tests.test_script_boot import NODE, TEMPLATE


class WorkspaceMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.parents = {}
        self.tabs = []
        self.tools = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'section':
            self.parents[attrs.get('id')] = list(self.sections)
            self.sections.append(attrs.get('id'))
        if tag == 'button' and 'tab' in attrs.get('class', '').split():
            self.tabs.append(attrs.get('data-view'))
        if tag == 'button' and 'checkin-tool' in attrs.get('class', '').split():
            self.tools.append((attrs.get('data-checkin-page'), list(self.sections)))

    def handle_endtag(self, tag):
        if tag == 'section':
            self.sections.pop()


class CheckinMarkupTest(unittest.TestCase):
    def test_management_panels_and_buttons_live_inside_checkin(self):
        markup = WorkspaceMarkup()
        markup.feed(Path(TEMPLATE).read_text())
        self.assertEqual(markup.tabs, ['bookmarks', 'chat', 'vault', 'checkin', 'settings'])
        for panel in ('checkinOverview', 'view-configs', 'view-history'):
            self.assertEqual(markup.parents[panel], ['view-checkin'])
        self.assertEqual(markup.tools, [('configs', ['view-checkin']), ('history', ['view-checkin'])])


@unittest.skipIf(NODE is None, '未安装 node')
class CheckinRoutingTest(unittest.TestCase):
    run_js = home_ui.LibraryHomeUiTest.run_js

    def test_switching_panels_keeps_checkin_active_and_returns_to_overview(self):
        self.run_js("""
            switchView('configs');
            assert.equal(currentViewName(), 'configs');
            assert.ok($('view-checkin').classList.contains('active'));
            assert.ok(document.querySelector('.tab[data-view="checkin"]').classList.contains('active'));
            assert.equal($('view-configs').hidden, false);
            assert.equal($('view-history').hidden, true);
            assert.equal($('checkinOverview').hidden, true);
            assert.equal($('libSubnav').hidden, true);
            switchView('history');
            assert.equal(currentViewName(), 'history');
            assert.equal($('view-configs').hidden, true);
            assert.equal($('view-history').hidden, false);
            assert.ok(__CALLS.fetches.includes('/api/history'));
            switchView('checkin/overview');
            assert.equal(currentViewName(), 'checkin');
            assert.equal($('checkinOverview').hidden, false);
            assert.equal($('runAllCheckin').hidden, false);
            assert.equal($('view-history').hidden, true);
            switchView('bookmarks');
            assert.equal($('libSubnav').hidden, false);
        """)

    def test_old_and_new_routes_open_the_same_panels(self):
        self.run_js("""
            let hash;
            history.replaceState = (_state, _title, url) => { hash = url; };
            for (const section of ['configs', 'history']) {
              location.hash = '#' + section;
              initRoute();
              assert.equal(currentViewName(), section);
              assert.equal(hash, '#checkin/' + section);
              location.hash = '#checkin/' + section;
              initRoute();
              assert.equal(currentViewName(), section);
            }
            location.hash = '#checkin/not-a-panel';
            initRoute();
            assert.equal(currentViewName(), 'bookmarks');
            location.hash = '#links/second';
            initRoute();
            assert.equal($('libTitle').textContent, '第二分组');
        """)

    def test_locked_users_cannot_open_management_panels_or_load_history(self):
        self.run_js("""
            STATE.admin_required = true;
            STATE.admin_unlocked = false;
            syncAdminViews();
            const before = __CALLS.fetches.length;
            for (const name of ['checkin', 'configs', 'history', 'checkin/history']) {
              switchView(name);
              assert.equal(currentViewName(), 'bookmarks');
              assert.equal($('view-checkin').classList.contains('active'), false);
            }
            assert.equal(__CALLS.fetches.length, before);
            assert.equal(document.querySelector('.tab[data-view="checkin"]').hidden, true);
            STATE.admin_unlocked = true;
            syncAdminViews();
            switchView('checkin/configs');
            assert.equal(currentViewName(), 'configs');
            assert.equal(document.querySelector('.tab[data-view="checkin"]').hidden, false);
        """)

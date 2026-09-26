# -*- coding: utf-8 -*-
"""首页选择和备用图标必须与同一条网址一起持久化，不能依赖浏览器状态。"""
import base64
import unittest
from unittest.mock import patch

from tests._support import StoreIsolationMixin, app_module
from app import app, clean_custom_icon, normalize_link_groups, read_store

PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII='


class LibraryHomeApiTest(StoreIsolationMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = app.test_client()
        self.write_config({'configs': [], 'bookmarks': [], 'link_groups': [
            {'id': 'a', 'name': 'A', 'links': [
                {'id': 'same', 'name': 'One', 'url': 'https://one.example'},
                {'id': 'other', 'name': 'Two', 'url': 'https://two.example'}]},
            {'id': 'b', 'name': 'B', 'links': [
                {'id': 'same', 'name': 'Three', 'url': 'https://three.example'}]},
        ]})

    def selections(self):
        return {(g['id'], l['id']) for g in read_store()['link_groups']
                for l in g['links'] if l['show_on_home']}

    def test_selection_uses_group_and_link_id_and_survives_reload(self):
        resp = self.client.put('/api/library/home', json={'links': [
            {'group': 'a', 'id': 'same'}, {'group': 'b', 'id': 'same'}]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.selections(), {('a', 'same'), ('b', 'same')})
        self.assertTrue(self.read_config()['link_groups'][0]['links'][0]['show_on_home'])
        self.client.put('/api/library/home', json={'links': [{'group': 'b', 'id': 'same'}]})
        self.assertEqual(self.selections(), {('b', 'same')})
        self.client.put('/api/library/home', json={'links': []})
        self.assertEqual(self.selections(), set())
        self.assertEqual(sum(len(g['links']) for g in read_store()['link_groups']), 3)

    def test_invalid_or_stale_selection_is_atomic(self):
        original = self.read_config()
        for body, code in [({}, 400), ([], 400), ({'links': [None]}, 400),
                           ({'links': [{'group': [], 'id': 'same'}]}, 400),
                           ({'links': [{'group': 'a', 'id': 'same'},
                                       {'group': 'b', 'id': 'deleted'}]}, 409)]:
            self.assertEqual(self.client.put('/api/library/home', json=body).status_code, code)
            self.assertEqual(self.read_config(), original)

    def test_home_and_upload_changes_require_admin(self):
        with patch.object(app_module, 'ADMIN_PASSWORD', 'a-secret-password'):
            self.assertEqual(self.client.put('/api/library/home', json={'links': []}).status_code, 403)
            self.assertEqual(self.client.put('/api/link_groups/a/links/same', json={
                'show_on_home': True, 'custom_icon': PNG}).status_code, 403)

    def test_edit_move_and_delete_follow_original_link(self):
        self.client.put('/api/link_groups/a/links/same', json={'show_on_home': True, 'custom_icon': PNG})
        updated = self.client.put('/api/link_groups/a/links/same', json={
            'name': 'Updated', 'group': 'b'}).get_json()['link']
        # 目标分组已有同 id，移动会重新分配 id；首页选择与图标仍保留。
        self.assertNotEqual(updated['id'], 'same')
        # 响应里只有带版本号的图标地址，原图留在存储里。
        self.assertNotIn('custom_icon', updated)
        self.assertTrue(updated['custom_icon_url'].startswith('/api/link_icon/b/' + updated['id'] + '?v='))
        self.assertEqual(read_store()['link_groups'][1]['links'][-1]['custom_icon'], PNG)
        self.assertEqual(self.selections(), {('b', updated['id'])})
        self.assertEqual(updated['name'], 'Updated')
        self.client.delete('/api/link_groups/b/links/' + updated['id'])
        self.assertEqual(self.selections(), set())

    def test_delete_group_removes_its_home_links(self):
        self.client.put('/api/link_groups/a/links/same', json={'show_on_home': True})
        self.client.delete('/api/link_groups/a')
        self.assertEqual(self.selections(), set())

    def test_upload_and_home_selection_round_trip_through_export(self):
        self.client.put('/api/link_groups/a/links/same', json={'show_on_home': True, 'custom_icon': PNG})
        exported = self.client.get('/api/configs/export').get_json()
        self.write_config({'configs': [], 'bookmarks': [], 'link_groups': []})
        self.assertEqual(self.client.post('/api/configs/import', json=exported).status_code, 200)
        self.assertEqual(self.selections(), {('a', 'same')})
        self.assertEqual(read_store()['link_groups'][0]['links'][0]['custom_icon'], PNG)
        self.client.put('/api/link_groups/a/links/same', json={'custom_icon': ''})
        self.assertEqual(read_store()['link_groups'][0]['links'][0]['custom_icon'], '')
        self.assertEqual(self.selections(), {('a', 'same')})

    def test_new_link_can_include_home_selection_and_icon(self):
        resp = self.client.post('/api/link_groups/a/links', json={
            'url': 'https://new.example', 'show_on_home': True, 'custom_icon': PNG})
        self.assertEqual(resp.status_code, 200)
        link = resp.get_json()['links'][0]
        self.assertEqual(self.selections(), {('a', link['id'])})
        self.assertNotIn('custom_icon', link)
        self.assertIn('custom_icon_url', link)
        self.assertEqual(read_store()['link_groups'][0]['links'][-1]['custom_icon'], PNG)

    def test_bad_image_is_rejected_without_overwriting_existing_icon(self):
        self.client.put('/api/link_groups/a/links/same', json={'custom_icon': PNG})
        resp = self.client.put('/api/link_groups/a/links/same', json={'custom_icon': 'https://tracking.example/icon.png'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(read_store()['link_groups'][0]['links'][0]['custom_icon'], PNG)


class LinkIconEndpointTest(StoreIsolationMixin, unittest.TestCase):
    """上传图标不再随 /api/configs 下发，改走带版本号、可长期缓存的独立地址。"""

    def setUp(self):
        super().setUp()
        self.client = app.test_client()
        self.write_config({'configs': [], 'bookmarks': [], 'link_groups': [
            {'id': 'a', 'name': 'A', 'links': [
                {'id': 'ico', 'name': 'With icon', 'url': 'https://one.example', 'custom_icon': PNG},
                {'id': 'plain', 'name': 'Plain', 'url': 'https://two.example'}]},
        ]})

    def icon_url(self):
        link = self.client.get('/api/configs').get_json()['link_groups'][0]['links'][0]
        return link['custom_icon_url']

    def test_configs_carry_a_versioned_url_instead_of_the_image(self):
        resp = self.client.get('/api/configs')
        self.assertNotIn('data:image', resp.get_data(as_text=True))
        links = resp.get_json()['link_groups'][0]['links']
        self.assertRegex(links[0]['custom_icon_url'], r'^/api/link_icon/a/ico\?v=[0-9a-f]{12}$')
        self.assertNotIn('custom_icon', links[0])
        self.assertNotIn('custom_icon_url', links[1])

    def test_icon_is_served_with_long_cache_and_revalidates(self):
        url = self.icon_url()
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'image/png')
        self.assertEqual(resp.data, base64.b64decode(PNG.split(',', 1)[1]))
        self.assertIn('immutable', resp.headers['Cache-Control'])
        self.assertTrue(resp.headers['Cache-Control'].startswith('private'))
        etag = resp.headers['ETag']
        self.assertEqual(self.client.get(url, headers={'If-None-Match': etag}).status_code, 304)
        # 版本号对不上（旧页面拿着旧地址）：照样给图，但不许长期缓存。
        stale = self.client.get('/api/link_icon/a/ico?v=000000000000')
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.headers['Cache-Control'], 'private, no-cache')

    def test_icon_url_follows_the_image(self):
        before = self.icon_url()
        self.client.put('/api/link_groups/a/links/ico', json={'custom_icon': ''})
        self.assertEqual(self.client.get(before.split('?')[0]).status_code, 404)
        self.client.put('/api/link_groups/a/links/ico', json={'custom_icon': PNG})
        self.assertEqual(self.icon_url(), before)

    def test_missing_icon_and_unknown_link_are_404(self):
        for path in ('/api/link_icon/a/plain', '/api/link_icon/a/nope', '/api/link_icon/zz/ico'):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_editing_without_the_icon_key_keeps_the_icon(self):
        self.client.put('/api/link_groups/a/links/ico', json={'name': 'Renamed', 'show_on_home': True})
        self.assertEqual(read_store()['link_groups'][0]['links'][0]['custom_icon'], PNG)

    def test_every_link_groups_response_is_stripped(self):
        body = self.client.put('/api/link_groups/a', json={'name': 'A2'}).get_json()
        self.assertNotIn('data:image', str(body))
        body = self.client.put('/api/library/home', json={'links': [{'group': 'a', 'id': 'ico'}]}).get_json()
        self.assertNotIn('data:image', str(body))

    def test_export_still_contains_the_full_image(self):
        exported = self.client.get('/api/configs/export').get_json()
        self.assertEqual(exported['link_groups'][0]['links'][0]['custom_icon'], PNG)

    def test_private_mode_requires_unlock(self):
        with patch.object(app_module, 'ADMIN_PASSWORD', 'a-secret-password'), \
                patch.object(app_module, 'PRIVATE_MODE', True):
            self.assertEqual(self.client.get('/api/link_icon/a/ico').status_code, 403)


class DashboardHomeApiTest(StoreIsolationMixin, unittest.TestCase):
    """站点看板里的站点也能上首页：没有稳定 id，用「下标 + 网址」定位。"""

    def setUp(self):
        super().setUp()
        self.client = app.test_client()
        self.write_config({'configs': [], 'link_groups': [
            {'id': 'a', 'name': 'A', 'links': [{'id': 'l1', 'name': 'One', 'url': 'https://one.example'}]}],
            'bookmarks': [
                {'name': 'First', 'url': 'https://first.example', 'fields': [
                    # 与真实落盘的字段同构（经 clean_field 写入的字段一定带 method），否则导入校验会拒收。
                    {'id': 'f1', 'label': '余额', 'type': 'amount', 'method': 'GET', 'url': 'https://first.example/api',
                     'headers': {'Authorization': 'Bearer secret-token'}, 'body': None,
                     'json_path': 'data.balance', 'value': '12.50'}]},
                {'name': 'Second', 'url': 'https://second.example'},
            ]})

    def sites(self):
        return [b['name'] for b in read_store()['bookmarks'] if b['show_on_home']]

    def test_old_bookmarks_default_to_not_on_home(self):
        self.assertEqual(self.sites(), [])
        public = self.client.get('/api/configs').get_json()['bookmarks']
        self.assertEqual([b['show_on_home'] for b in public], [False, False])

    def test_home_selection_can_include_dashboard_sites(self):
        resp = self.client.put('/api/library/home', json={
            'links': [{'group': 'a', 'id': 'l1'}],
            'bookmarks': [{'index': 1, 'url': 'https://second.example'}]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sites(), ['Second'])
        body = resp.get_json()
        self.assertEqual([b['show_on_home'] for b in body['bookmarks']], [False, True])
        self.assertTrue(body['link_groups'][0]['links'][0]['show_on_home'])
        # 对外响应不得带出接口字段的请求配置与凭据。
        self.assertNotIn('secret-token', resp.get_data(as_text=True))
        self.client.put('/api/library/home', json={'links': [], 'bookmarks': []})
        self.assertEqual(self.sites(), [])

    def test_omitting_bookmarks_keeps_dashboard_selection(self):
        self.client.put('/api/bookmarks/0/home', json={'show_on_home': True, 'url': 'https://first.example'})
        self.client.put('/api/library/home', json={'links': []})
        self.assertEqual(self.sites(), ['First'])

    def test_stale_or_malformed_site_selection_is_atomic(self):
        original = self.read_config()
        for sites, code in [('nope', 400), ([None], 400), ([{'index': '0', 'url': 'https://first.example'}], 400),
                            ([{'index': True, 'url': 'https://first.example'}], 400),
                            ([{'index': 0}], 400),
                            ([{'index': 0, 'url': 'https://second.example'}], 409),
                            ([{'index': 5, 'url': 'https://first.example'}], 409),
                            ([{'index': -1, 'url': 'https://second.example'}], 409)]:
            with self.subTest(sites=sites):
                resp = self.client.put('/api/library/home', json={
                    'links': [{'group': 'a', 'id': 'l1'}], 'bookmarks': sites})
                self.assertEqual(resp.status_code, code)
                self.assertEqual(self.read_config(), original)

    def test_single_site_toggle_checks_position_and_keeps_fields(self):
        resp = self.client.put('/api/bookmarks/0/home', json={'show_on_home': True, 'url': 'https://first.example'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sites(), ['First'])
        field = read_store()['bookmarks'][0]['fields'][0]
        self.assertEqual((field['value'], field['headers']['Authorization']), ('12.50', 'Bearer secret-token'))
        self.assertNotIn('secret-token', resp.get_data(as_text=True))
        for body, code in [({'show_on_home': True, 'url': 'https://second.example'}, 409),
                           ({'show_on_home': 'yes', 'url': 'https://first.example'}, 400),
                           ({'show_on_home': False}, 400), ([], 400)]:
            self.assertEqual(self.client.put('/api/bookmarks/0/home', json=body).status_code, code)
        self.assertEqual(self.client.put('/api/bookmarks/9/home', json={
            'show_on_home': True, 'url': 'https://first.example'}).status_code, 409)
        self.assertEqual(self.sites(), ['First'])

    def test_editing_a_site_keeps_it_on_home_and_reorder_moves_the_flag(self):
        self.client.put('/api/bookmarks/1/home', json={'show_on_home': True, 'url': 'https://second.example'})
        # 编辑弹窗提交的负载不带 show_on_home。
        self.assertEqual(self.client.put('/api/bookmarks/1', json={
            'name': 'Renamed', 'url': 'https://second.example'}).status_code, 200)
        self.assertEqual(self.sites(), ['Renamed'])
        self.client.post('/api/bookmarks/reorder', json={'order': [1, 0]})
        self.assertEqual([b['name'] for b in read_store()['bookmarks']], ['Renamed', 'First'])
        self.assertEqual(self.sites(), ['Renamed'])
        self.client.delete('/api/bookmarks/0')
        self.assertEqual(self.sites(), [])

    def test_site_home_flag_round_trips_through_export_and_requires_admin(self):
        self.client.put('/api/bookmarks/0/home', json={'show_on_home': True, 'url': 'https://first.example'})
        exported = self.client.get('/api/configs/export').get_json()
        self.write_config({'configs': [], 'bookmarks': [], 'link_groups': []})
        self.assertEqual(self.client.post('/api/configs/import', json=exported).status_code, 200)
        self.assertEqual(self.sites(), ['First'])
        with patch.object(app_module, 'ADMIN_PASSWORD', 'a-secret-password'):
            self.assertEqual(self.client.put('/api/bookmarks/0/home', json={
                'show_on_home': False, 'url': 'https://first.example'}).status_code, 403)
            self.assertEqual(self.client.put('/api/library/home', json={
                'links': [], 'bookmarks': []}).status_code, 403)
        self.assertEqual(self.sites(), ['First'])


class CustomIconValidationTest(unittest.TestCase):
    def test_only_bounded_raster_data_is_accepted(self):
        self.assertEqual(clean_custom_icon(PNG), PNG)
        for bad in ['data:image/svg+xml;base64,PHN2Zy8+', 'data:image/png;base64,PGh0bWw+',
                    'data:image/jpeg;base64,' + PNG.split(',')[1], 'data:image/png;base64,====',
                    'javascript:alert(1)', {'url': PNG},
                    'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'x' * 65536).decode()]:
            with self.subTest(value=str(bad)[:60]), self.assertRaises(ValueError):
                clean_custom_icon(bad)

    def test_old_config_defaults_and_bad_saved_icon_do_not_break_loading(self):
        groups = normalize_link_groups([{'id': 'a', 'links': [
            {'id': 'old', 'url': 'https://old.example'},
            {'id': 'bad', 'url': 'https://bad.example', 'custom_icon': 'not-an-image'},
        ]}])
        for link in groups[0]['links']:
            self.assertFalse(link['show_on_home'])
            self.assertEqual(link['custom_icon'], '')


if __name__ == '__main__':
    unittest.main()

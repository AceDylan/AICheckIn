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
        self.assertEqual(updated['custom_icon'], PNG)
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
        self.assertEqual(link['custom_icon'], PNG)

    def test_bad_image_is_rejected_without_overwriting_existing_icon(self):
        self.client.put('/api/link_groups/a/links/same', json={'custom_icon': PNG})
        resp = self.client.put('/api/link_groups/a/links/same', json={'custom_icon': 'https://tracking.example/icon.png'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(read_store()['link_groups'][0]['links'][0]['custom_icon'], PNG)


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

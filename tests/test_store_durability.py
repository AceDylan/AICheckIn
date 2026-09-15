# -*- coding: utf-8 -*-
"""配置落盘的耐久性与请求体上限。

config.json 装着全部凭据。就地截断写入一旦在中途失败（磁盘写满、容器被杀），
留下的是半截 JSON，整份配置就读不出来了——这里守住「要么旧的完整内容、
要么新的完整内容」。
"""
import errno
import json
import os
import pathlib
import stat
import unittest


def os_path_cls():
    """当前平台的具体 Path 类（PosixPath / WindowsPath）；Path 本身不能直接继承。"""
    return pathlib.Path(".")

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app, read_store, write_store  # noqa: E402

SAMPLE = {"configs": [], "proxy_url": "", "schedule": {}, "bookmarks": [], "link_groups": []}


class AtomicWriteTest(StoreIsolationMixin, unittest.TestCase):
    def test_round_trip(self):
        write_store(dict(SAMPLE, proxy_url="http://127.0.0.1:1080"))
        self.assertEqual(read_store()["proxy_url"], "http://127.0.0.1:1080")

    def test_no_temp_files_are_left_behind(self):
        write_store(dict(SAMPLE))
        write_store(dict(SAMPLE))
        leftovers = [n for n in os.listdir(str(self.data_dir)) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_target_is_replaced_not_truncated(self):
        # 核心保证：新内容先落到同目录的临时文件，再整体 rename 过去。
        write_store(dict(SAMPLE, proxy_url="v1"))
        seen = []
        real_replace = os.replace

        def spy(src, dst):
            seen.append((src, dst))
            return real_replace(src, dst)

        os.replace = spy
        try:
            write_store(dict(SAMPLE, proxy_url="v2"))
        finally:
            os.replace = real_replace
        target = [pair for pair in seen if pair[1] == app_module.CONFIG_FILE]
        self.assertTrue(target, "配置文件没有走原子替换：%r" % (seen,))
        src = target[-1][0]
        self.assertTrue(src.endswith(".tmp"), src)
        # 必须同目录，跨文件系统 rename 不是原子的。
        self.assertEqual(os.path.dirname(src), os.path.dirname(app_module.CONFIG_FILE))

    def test_previous_content_survives_a_failed_write(self):
        write_store(dict(SAMPLE, proxy_url="first"))
        original = self.read_config()

        # 原子替换与就地回退双双失败（磁盘写满），write_store 必须抛 RuntimeError。
        base = type(os_path_cls())

        class ExplodingPath(base):
            def write_text(self, *args, **kwargs):
                raise OSError(errno.ENOSPC, "No space left on device")

        real_replace, real_path = os.replace, app_module.Path

        def boom(src, dst):
            raise OSError(errno.ENOSPC, "No space left on device")

        os.replace = boom
        app_module.Path = ExplodingPath
        try:
            with self.assertRaises(RuntimeError):
                write_store(dict(SAMPLE, proxy_url="second"))
        finally:
            os.replace = real_replace
            app_module.Path = real_path

        # 旧文件仍是完整的、可解析的，且临时文件已清理。
        self.assertEqual(self.read_config(), original)
        self.assertEqual(read_store()["proxy_url"], "first")
        self.assertEqual([n for n in os.listdir(str(self.data_dir)) if n.endswith(".tmp")], [])

    def test_falls_back_when_rename_is_unavailable(self):
        # config.json 本身被做成 bind mount 时 rename 会失败，必须退回就地写入而不是报错。
        write_store(dict(SAMPLE, proxy_url="before"))
        real_replace = os.replace

        def busy(src, dst):
            raise OSError(errno.EBUSY, "Device or resource busy")

        os.replace = busy
        try:
            write_store(dict(SAMPLE, proxy_url="after"))
        finally:
            os.replace = real_replace
        self.assertEqual(read_store()["proxy_url"], "after")
        self.assertEqual([n for n in os.listdir(str(self.data_dir)) if n.endswith(".tmp")], [])

    def test_a_backup_of_the_previous_content_is_kept(self):
        write_store(dict(SAMPLE, proxy_url="v1"))
        write_store(dict(SAMPLE, proxy_url="v2"))
        with open(app_module.CONFIG_FILE + ".bak", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["proxy_url"], "v1")

    def test_new_files_are_owner_only(self):
        # 新建的配置文件里全是凭据，默认不该让同机其它用户读到。
        write_store(dict(SAMPLE))
        mode = stat.S_IMODE(os.stat(app_module.CONFIG_FILE).st_mode)
        self.assertEqual(mode & 0o077, 0, oct(mode))

    def test_existing_permissions_are_preserved(self):
        # 已有部署可能刻意放宽过权限，不擅自改变现状。
        write_store(dict(SAMPLE))
        os.chmod(app_module.CONFIG_FILE, 0o644)
        write_store(dict(SAMPLE, proxy_url="again"))
        self.assertEqual(stat.S_IMODE(os.stat(app_module.CONFIG_FILE).st_mode), 0o644)

    def test_unserialisable_payload_never_touches_the_file(self):
        write_store(dict(SAMPLE, proxy_url="safe"))
        with self.assertRaises(TypeError):
            write_store(dict(SAMPLE, bookmarks=[{"bad": object()}]))
        self.assertEqual(read_store()["proxy_url"], "safe")


class DailyBackupTest(StoreIsolationMixin, unittest.TestCase):
    """.bak 只保留上一次写入前的内容：误删之后又随手改两下，好数据就没了。
    按天的回溯点才救得回这类「隔天才发现」的问题。"""

    def snapshots(self):
        return sorted(n for n in os.listdir(str(self.data_dir))
                      if n.startswith("config.json.") and n.endswith(".bak")
                      and n != "config.json.bak")

    def test_first_write_of_the_day_archives_the_previous_content(self):
        write_store(dict(SAMPLE, proxy_url="v0"))
        write_store(dict(SAMPLE, proxy_url="v1"))
        names = self.snapshots()
        self.assertEqual(len(names), 1, names)
        self.assertIn(app_module._today_str(), names[0])
        with open(os.path.join(str(self.data_dir), names[0]), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["proxy_url"], "v0")

    def test_later_writes_do_not_overwrite_todays_snapshot(self):
        # 快照必须停在「今天第一次改动之前」，否则改几次就又被冲掉了。
        for value in ("v0", "v1", "v2", "v3"):
            write_store(dict(SAMPLE, proxy_url=value))
        names = self.snapshots()
        self.assertEqual(len(names), 1)
        with open(os.path.join(str(self.data_dir), names[0]), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["proxy_url"], "v0")

    def test_plain_bak_still_tracks_the_previous_write(self):
        for value in ("v0", "v1", "v2"):
            write_store(dict(SAMPLE, proxy_url=value))
        with open(app_module.CONFIG_FILE + ".bak", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["proxy_url"], "v1")

    def test_old_archives_are_pruned(self):
        write_store(dict(SAMPLE, proxy_url="seed"))
        # 造出比保留天数更多的历史快照。
        keep = app_module.CONFIG_BACKUP_DAYS
        made = []
        for day in range(1, keep + 4):
            name = "config.json.2020-01-%02d.bak" % day
            path = os.path.join(str(self.data_dir), name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{}")
            made.append(name)
        write_store(dict(SAMPLE, proxy_url="trigger"))
        left = self.snapshots()
        self.assertEqual(len(left), keep, left)
        self.assertIn("config.json.%s.bak" % app_module._today_str(), left)
        # 删掉的应当是最旧的那几份。
        self.assertNotIn(made[0], left)

    def test_unrelated_bak_files_are_never_deleted(self):
        write_store(dict(SAMPLE))
        stray = os.path.join(str(self.data_dir), "config.json.manual-copy.bak")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("{}")
        for day in range(1, app_module.CONFIG_BACKUP_DAYS + 3):
            with open(os.path.join(str(self.data_dir), "config.json.2020-02-%02d.bak" % day),
                      "w", encoding="utf-8") as fh:
                fh.write("{}")
        write_store(dict(SAMPLE, proxy_url="trigger"))
        self.assertTrue(os.path.isfile(stray), "手工命名的备份被误删了")
        self.assertTrue(os.path.isfile(app_module.CONFIG_FILE + ".bak"))

    def test_disabling_the_feature_keeps_only_bak(self):
        self.addCleanup(setattr, app_module, "CONFIG_BACKUP_DAYS", app_module.CONFIG_BACKUP_DAYS)
        app_module.CONFIG_BACKUP_DAYS = 0
        write_store(dict(SAMPLE, proxy_url="v0"))
        write_store(dict(SAMPLE, proxy_url="v1"))
        self.assertEqual(self.snapshots(), [])
        self.assertTrue(os.path.isfile(app_module.CONFIG_FILE + ".bak"))

    def test_backup_failure_does_not_block_saving(self):
        # 备份是附带保障，不该因为它失败就让用户存不了东西。
        write_store(dict(SAMPLE, proxy_url="v0"))
        real = app_module._rotate_daily_backup

        def boom(path, previous_text):
            raise OSError(errno.ENOSPC, "No space left on device")

        app_module._rotate_daily_backup = boom
        try:
            write_store(dict(SAMPLE, proxy_url="v1"))
        finally:
            app_module._rotate_daily_backup = real
        self.assertEqual(read_store()["proxy_url"], "v1")


class RequestLimitTest(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(RequestLimitTest, self).setUp()
        self.write_config(dict(SAMPLE))
        self.client = app.test_client()

    def test_limit_is_configured(self):
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], app_module.MAX_REQUEST_BYTES)
        self.assertGreaterEqual(app_module.MAX_REQUEST_BYTES, 64 * 1024)

    def test_oversized_body_is_rejected_as_json(self):
        blob = b"x" * (app_module.MAX_REQUEST_BYTES + 1024)
        resp = self.client.post("/api/configs/import", data=blob, content_type="application/json")
        self.assertEqual(resp.status_code, 413)
        self.assertFalse(resp.get_json()["ok"])

    def test_normal_payload_still_goes_through(self):
        resp = self.client.post("/api/configs/import", json={"configs": []})
        self.assertEqual(resp.status_code, 200)


class ApiErrorShapeTest(unittest.TestCase):
    """前端所有接口调用都走 resp.json()；错误页回 HTML 会让它们抛解析异常。"""

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_unknown_api_path_returns_json(self):
        resp = self.client.get("/api/definitely-not-here")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.get_json()["ok"])

    def test_wrong_method_returns_json(self):
        resp = self.client.get("/api/checkin")  # 只接受 POST
        self.assertEqual(resp.status_code, 405)
        self.assertFalse(resp.get_json()["ok"])

    def test_page_routes_keep_html_errors(self):
        resp = self.client.get("/definitely-not-here")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("text/html", resp.headers["Content-Type"])

    def test_error_responses_still_carry_security_headers(self):
        resp = self.client.get("/api/definitely-not-here")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()

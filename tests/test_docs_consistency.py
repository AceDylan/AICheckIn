# -*- coding: utf-8 -*-
"""文档与代码的一致性守卫。

配置项和接口清单最容易随改动漂移：加了个环境变量却忘了写进 SECURITY.md，
用户就只能读源码。这里把「文档必须跟上」变成一条会失败的测试。
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class EnvVarDocsTest(unittest.TestCase):
    def setUp(self):
        self.code = _read("app.py")
        self.security = _read("SECURITY.md")

    def used_in_code(self):
        return set(re.findall(r'os\.environ\.get\("(GYQD_[A-Z_]+)"', self.code))

    def documented(self):
        return set(re.findall(r"`(GYQD_[A-Z_]+)`", self.security))

    def test_every_env_var_is_documented(self):
        missing = sorted(self.used_in_code() - self.documented())
        self.assertEqual(missing, [], "这些环境变量没有写进 SECURITY.md：%s" % missing)

    def test_no_documented_var_has_been_removed_from_the_code(self):
        # 反向漂移同样误导人：文档里写着、代码里早就不读了。
        stale = sorted(self.documented() - self.used_in_code())
        self.assertEqual(stale, [], "SECURITY.md 里这些变量代码已不再读取：%s" % stale)

    def test_env_example_covers_the_deployment_critical_ones(self):
        example = _read(".env.example")
        for name in ("GYQD_ADMIN_PASSWORD", "GYQD_PRIVATE", "GYQD_HSTS"):
            self.assertIn(name, example, "%s 应当在 .env.example 里有位置" % name)

    def test_compose_passes_through_what_env_example_offers(self):
        # .env 里填了却没被 compose 透传进容器 = 静默失效。
        compose = _read("docker-compose.yml")
        for name in ("GYQD_ADMIN_PASSWORD", "GYQD_PRIVATE", "GYQD_HSTS"):
            self.assertIn(name, compose, "%s 没有在 docker-compose.yml 里透传" % name)


class SecurityDocAccuracyTest(unittest.TestCase):
    def setUp(self):
        self.code = _read("app.py")
        self.security = _read("SECURITY.md")

    def test_documented_defaults_match_the_code(self):
        # 表格里的默认值必须和代码一致，否则照着文档配会得到意外行为。
        expected = {
            "GYQD_LOGIN_MAX_FAILS": "8",
            "GYQD_LOGIN_GLOBAL_MAX_FAILS": "40",
            "GYQD_LOGIN_WINDOW": "900",
            "GYQD_RETRY_LIMIT": "3",
            "GYQD_RETRY_DELAY_MINUTES": "30",
        }
        for name, default in expected.items():
            self.assertIn('os.environ.get("%s", "%s")' % (name, default), self.code,
                          "%s 的代码默认值已不是 %s" % (name, default))
            row = next((l for l in self.security.splitlines() if "`%s`" % name in l), "")
            self.assertIn("`%s`" % default, row, "SECURITY.md 里 %s 的默认值与代码不符" % name)

    def test_admin_only_endpoints_listed_in_the_table_are_really_guarded(self):
        for path, func in (("/api/diagnostics", "api_diagnostics"),
                           ("/api/history", "api_history"),
                           ("/api/configs/export", "api_export"),
                           ("/api/todos", "api_todos"),
                           ("/api/deck", "api_deck")):
            self.assertIn(path, self.security, "%s 未出现在 SECURITY.md 的权限表里" % path)
            body = self.code[self.code.index("def %s(" % func):]
            self.assertIn("_guard_admin()", body[:400], "%s 实际并未加管理鉴权" % func)

    def test_private_mode_open_endpoints_are_documented(self):
        # 私密模式下仍开放的端点是最需要说清楚的一组。
        for name in ("/api/health", "/api/auth", "/sw.js"):
            self.assertIn(name, self.security, "%s 应在私密模式说明里点名" % name)


class ReadmeTest(unittest.TestCase):
    def test_upgrade_section_warns_about_the_env_prerequisite(self):
        readme = _read("README.md")
        self.assertIn(".env", readme)
        self.assertIn("--build", readme)   # 只 up 不 build 会让 .dockerignore 不生效

    def test_test_command_in_readme_actually_works(self):
        readme = _read("README.md")
        self.assertIn("python -m unittest discover -s tests -t .", readme)


if __name__ == "__main__":
    unittest.main()

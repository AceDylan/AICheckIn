# -*- coding: utf-8 -*-
"""tests/browser/ 的静态守卫。

那几套真实浏览器回归要 Chromium，不在 unittest 里跑，最怕「页面改了、脚本没跟上」：
首页组件那次提交把待办挪进右侧一列、删掉了 [data-home-todo]，旧脚本从那一刻起就跑不通，
却直到下一次有人想拿它做回归才被发现。这里不开浏览器，只核对脚本里点名的 #id / data-* 在页面里都还在，
于是删掉页面元素的那次提交自己就会红。顺带守住两条底线：种子数据里不许出现凭据，脚本里不许写死本机路径或密码。
"""
import ast
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "tests", "browser")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _scripts():
    return sorted(name for name in os.listdir(SUITE) if name.endswith(".py"))


def _strings(source):
    """脚本里的字符串常量（含 f-string 的固定部分），不含注释和文档字符串。"""
    tree = ast.parse(source)
    docs = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs]


def _kebab(camel):
    return "data-" + re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), camel)


class SelectorsStillExistTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = _read("templates", "index.html")
        cls.ids, cls.attrs = {}, {}
        for name in _scripts():
            for text in _strings(_read("tests", "browser", name)):
                for found in re.findall(r"(?<![\w/&'\"])#([A-Za-z][\w-]*)", text) + re.findall(r"getElementById\('([\w-]+)'\)", text):
                    cls.ids.setdefault(found, name)
                for found in re.findall(r"\[(data-[a-z][a-z-]*)", text):
                    cls.attrs.setdefault(found, name)
                for found in re.findall(r"\bdataset\.([a-z][A-Za-z]*)", text):
                    cls.attrs.setdefault(_kebab(found), name)

    def test_the_scan_actually_found_selectors(self):
        # 扫描写坏了会「什么都没找到、于是全过」——先确认它真的在干活。
        self.assertGreater(len(self.ids), 40, sorted(self.ids))
        self.assertGreater(len(self.attrs), 15, sorted(self.attrs))
        for must in ("homeTodo", "todoList", "deckTabs", "homeDeckBtn"):
            self.assertIn(must, self.ids)
        for must in ("data-todo-grip", "data-deck-tab", "data-arrange-id"):
            self.assertIn(must, self.attrs)

    def test_every_id_the_scripts_name_is_still_in_the_page(self):
        missing = sorted("%s（%s）" % (i, where) for i, where in self.ids.items() if 'id="%s"' % i not in self.page)
        self.assertEqual(missing, [], "浏览器回归脚本点名的这些 #id 页面里已经没有了，脚本要跟着改：%s" % missing)

    def test_every_data_attribute_the_scripts_name_is_still_in_the_page(self):
        def present(attr):
            camel = re.sub(r"-([a-z])", lambda m: m.group(1).upper(), attr[len("data-"):])
            return attr in self.page or ("dataset." + camel) in self.page
        missing = sorted("%s（%s）" % (a, where) for a, where in self.attrs.items() if not present(a))
        self.assertEqual(missing, [], "浏览器回归脚本点名的这些 data-* 属性页面里已经没有了，脚本要跟着改：%s" % missing)


class SuiteHygieneTest(unittest.TestCase):
    def test_scripts_compile(self):
        for name in _scripts():
            compile(_read("tests", "browser", name), name, "exec")

    def test_no_machine_specific_paths_or_fixed_password(self):
        for name in _scripts():
            source = _read("tests", "browser", name)
            for banned in ("/root/", "/home/", ".admin_pw", "127.0.0.1:55"):
                self.assertNotIn(banned, source, "%s 里写死了本机的东西：%s" % (name, banned))
        common = _read("tests", "browser", "common.py")
        self.assertIn("PASSWORD = secrets.token_urlsafe(", common, "管理密码应当每次运行现生成，不能写死")
        self.assertIn('host=\'127.0.0.1\'', common, "测试实例只能绑回环地址")

    def test_run_all_covers_every_suite(self):
        listed = set(re.search(r"SUITES = \(([^)]*)\)", _read("tests", "browser", "run_all.py")).group(1).replace('"', "").replace(" ", "").split(","))
        on_disk = {name[:-3] for name in _scripts() if name.startswith("verify")}
        self.assertEqual(listed, on_disk)

    def test_readme_tells_how_to_run_it(self):
        readme = _read("README.md")
        # assertTrue 而不是 assertIn：失败时不把整份 README 倒进终端。
        self.assertTrue("python tests/browser/run_all.py" in readme, "README 的「开发」一节应当写明浏览器回归怎么跑")
        self.assertTrue("tests/browser/" in readme, "README 的目录结构里应当有 tests/browser/")

    def test_unittest_discovery_does_not_pick_the_suite_up(self):
        # 没装 playwright 的机器上 `unittest discover` 也必须照常能跑：目录不是包，文件名也不以 test 开头。
        self.assertFalse(os.path.exists(os.path.join(SUITE, "__init__.py")))
        self.assertEqual([name for name in os.listdir(SUITE) if name.startswith("test")], [])


class SeedDataIsSyntheticTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(_read("tests", "browser", "seed", "config.json"))
        self.todos = json.loads(_read("tests", "browser", "seed", "todos.json"))

    def test_no_checkin_accounts_and_no_proxy(self):
        self.assertEqual(self.config["configs"], [], "签到账号带着令牌，种子数据里不许有")
        self.assertEqual(self.config["proxy_url"], "")

    def test_no_credential_like_keys_or_values(self):
        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertIsNone(re.search(r"token|secret|passw|cookie|authoriz|api_?key|session", key, re.I), "种子数据里出现了像凭据的字段：%s/%s" % (path, key))
                    walk(value, path + "/" + key)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, "%s[%d]" % (path, index))
            elif isinstance(node, str):
                self.assertIsNone(re.search(r"Bearer\s|sk-[A-Za-z0-9]{8}|eyJ[A-Za-z0-9_-]{10}", node), "种子数据里出现了像令牌的值：%s" % path)
        walk(self.config)
        walk(self.todos)

    def test_dashboard_sites_point_at_reserved_example_domains(self):
        # 站点看板会带着「接口字段」去请求对方：种子里只许用 RFC 2606 的保留域名，永远解析不到真实服务。
        for site in self.config["bookmarks"]:
            host = re.sub(r"^https?://([^/:]+).*$", r"\1", site["url"])
            self.assertTrue(host.endswith((".example.com", ".example.org", ".example.net")), site["url"])


if __name__ == "__main__":
    unittest.main()

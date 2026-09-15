# -*- coding: utf-8 -*-
"""构建上下文：凭据不得被烘进镜像层。

Dockerfile 里是 `COPY . .`。没有 .dockerignore 时，部署目录下装着全部凭据的
data/config.json 与存管理密码的 .env 会被原样复制进镜像。运行时 bind mount 会把
/app/data 盖住，所以平时看不出异常——但凭据确实留在镜像层里，镜像一旦被导出
或推送就是明文泄漏。
"""
import fnmatch
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _patterns():
    with open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8") as fh:
        return [line.strip() for line in fh
                if line.strip() and not line.strip().startswith("#")]


def ignored(rel_path):
    """粗粒度模拟 docker 的忽略判定：整串匹配、首段匹配、**/ 前缀匹配。"""
    parts = rel_path.split("/")
    for pat in _patterns():
        bare = pat.rstrip("/")
        if fnmatch.fnmatch(rel_path, bare) or fnmatch.fnmatch(parts[0], bare):
            return pat
        if bare.startswith("**/") and any(fnmatch.fnmatch(p, bare[3:]) for p in parts):
            return pat
    return None


class DockerignoreExistsTest(unittest.TestCase):
    def test_file_is_present(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".dockerignore")),
                        "缺少 .dockerignore，COPY . . 会把 data/ 和 .env 烘进镜像")

    def test_dockerfile_still_copies_everything(self):
        # 这个测试的前提就是 `COPY . .`；哪天改成逐个 COPY，本文件的理由需要重新评估。
        with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as fh:
            self.assertIn("COPY . .", fh.read())


class SecretsAreExcludedTest(unittest.TestCase):
    def test_data_directory_never_enters_the_image(self):
        for path in ("data/config.json", "data/config.json.bak",
                     "data/.session_secret", "data/history.json", "data/favicons/x.png"):
            self.assertIsNotNone(ignored(path), path)

    def test_env_files_never_enter_the_image(self):
        for path in (".env", ".env.local", ".env.production"):
            self.assertIsNotNone(ignored(path), path)

    def test_git_history_is_excluded(self):
        # .git 里有仓库全部历史，没必要进镜像，也是一份额外的泄漏面。
        self.assertIsNotNone(ignored(".git/config"))

    def test_example_env_is_still_allowed(self):
        # .env.example 只是模板，没有真实值；排不排都行，但不该因为它被排掉而误导用户。
        # 这里只要求它不会被当成 .env 处理导致构建报错。
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".env.example")))


class RuntimeFilesAreKeptTest(unittest.TestCase):
    """排错了方向更糟：把运行必需的文件挡在镜像外，容器直接起不来。"""

    RUNTIME = [
        "app.py", "gyqd.py", "entrypoint.sh", "requirements.txt",
        "templates/index.html",
        "static/app-v3.css", "static/sw.js", "static/manifest.webmanifest",
        "static/icon-192.png", "static/icon-512.png",
        "static/icon-maskable-512.png", "static/apple-touch-icon.png",
    ]

    def test_every_runtime_file_survives(self):
        for path in self.RUNTIME:
            self.assertIsNone(ignored(path), "%s 被 .dockerignore 排除了" % path)

    def test_every_runtime_file_actually_exists(self):
        for path in self.RUNTIME:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, path)), path)

    def test_static_assets_referenced_by_the_manifest_are_all_present(self):
        import json
        with open(os.path.join(ROOT, "static", "manifest.webmanifest"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        for entry in manifest["icons"]:
            rel = entry["src"].lstrip("/")
            self.assertTrue(os.path.isfile(os.path.join(ROOT, rel)), rel)
            self.assertIsNone(ignored(rel), rel)


if __name__ == "__main__":
    unittest.main()

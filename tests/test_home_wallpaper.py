# -*- coding: utf-8 -*-
"""壁纸：自定义上传接口的权限 / 校验 / 缓存，内置素材的体积预算与清单一致性，
以及「全部页面同一套场景与导航、不为壁纸放宽 CSP、降级路径齐全」这些页面侧的护栏。"""
import json
import os
import re
import unittest

from tests._support import StoreIsolationMixin, app_module
from app import app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WALL_DIR = os.path.join(ROOT, "static", "wallpapers")
PASSWORD = "wallpaper-unit-test-51d0"

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"fake-webp-body"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
GIF = b"GIF89a" + b"fake-gif-body"


class WallpaperApiBase(StoreIsolationMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True

    def setUp(self):
        super(WallpaperApiBase, self).setUp()
        self.addCleanup(setattr, app_module, "ADMIN_PASSWORD", app_module.ADMIN_PASSWORD)
        self.addCleanup(setattr, app_module, "PRIVATE_MODE", app_module.PRIVATE_MODE)
        app_module.ADMIN_PASSWORD = PASSWORD
        app_module.PRIVATE_MODE = False
        self.write_config({"configs": [], "bookmarks": [], "link_groups": []})
        self.anon = app.test_client()
        self.admin = app.test_client()
        self.admin.environ_base["HTTP_X_ADMIN_PASSWORD"] = PASSWORD

    def upload(self, data, client=None, query="?lum=0.42"):
        return (client or self.admin).put("/api/wallpaper" + query, data=data, content_type="image/webp")


class WallpaperUploadTest(WallpaperApiBase):
    def test_upload_and_removal_need_admin(self):
        self.assertEqual(self.upload(WEBP, client=self.anon).status_code, 403)
        self.assertEqual(self.anon.get("/api/wallpaper").status_code, 404, "被拒的上传不应落盘")
        self.assertEqual(self.upload(WEBP).status_code, 200)
        self.assertEqual(self.anon.delete("/api/wallpaper").status_code, 403)
        self.assertEqual(self.anon.get("/api/wallpaper").status_code, 200, "被拒的删除不应生效")

    def test_roundtrip_reports_version_and_luminance(self):
        body = self.upload(WEBP).get_json()
        self.assertTrue(body["ok"])
        info = body["wallpaper"]
        self.assertTrue(info["custom"])
        self.assertRegex(info["v"], r"^[0-9a-f]{16}$")
        self.assertEqual(info["lum"], 0.42)
        # 页面靠 /api/configs 知道有没有自定义壁纸；匿名访客也拿得到（壁纸本身就是公开外观）。
        self.assertEqual(self.anon.get("/api/configs").get_json()["wallpaper"], info)
        resp = self.anon.get("/api/wallpaper?v=" + info["v"])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "image/webp")
        self.assertEqual(resp.get_data(), WEBP)

    def test_type_is_decided_by_magic_bytes_not_by_the_header(self):
        for payload, mime in ((PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")):
            self.assertEqual(self.upload(payload).status_code, 200, mime)
            self.assertEqual(self.anon.get("/api/wallpaper").mimetype, mime)
        # SVG 能内嵌脚本；GIF / HTML / 空请求体同样不收——即使请求头自称是 image/webp。
        for payload in (SVG, GIF, b"<!DOCTYPE html><html></html>", b""):
            self.assertEqual(self.upload(payload).status_code, 400, payload[:12])
        self.assertEqual(self.anon.get("/api/wallpaper").mimetype, "image/webp", "被拒的上传不应覆盖已有壁纸")

    def test_oversized_upload_is_rejected(self):
        self.assertLessEqual(app_module.WALLPAPER_MAX_BYTES, app_module.MAX_REQUEST_BYTES,
                             "壁纸上限不能超过全局请求体上限，否则大图先被 413 拦掉、提示对不上")
        resp = self.upload(WEBP + b"x" * app_module.WALLPAPER_MAX_BYTES)
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(self.anon.get("/api/wallpaper").status_code, 404)

    def test_untrusted_luminance_is_dropped(self):
        # 亮度提示来自浏览器，只接受 0~1 的数；其余一律丢掉，页面会按最亮画面处理（遮罩压到最暗）。
        for raw in ("2", "-0.1", "nan", "abc", ""):
            info = self.upload(WEBP, query="?lum=" + raw).get_json()["wallpaper"]
            self.assertIsNone(info["lum"], raw)
        self.assertIsNone(self.upload(WEBP, query="").get_json()["wallpaper"]["lum"])

    def test_response_is_locked_down_and_cacheable(self):
        v = self.upload(PNG).get_json()["wallpaper"]["v"]
        resp = self.anon.get("/api/wallpaper?v=" + v)
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'none'", resp.headers["Content-Security-Policy"])
        # 带内容哈希的地址可以长缓存；裸地址 / 旧哈希每次校验，换图后不会一直看到旧的。
        self.assertIn("immutable", resp.headers["Cache-Control"])
        self.assertIn("no-cache", self.anon.get("/api/wallpaper").headers["Cache-Control"])
        self.assertIn("no-cache", self.anon.get("/api/wallpaper?v=0000000000000000").headers["Cache-Control"])
        self.assertNotIn("public", resp.headers["Cache-Control"], "私密模式下壁纸不该进共享缓存")
        revalidate = self.anon.get("/api/wallpaper", headers={"If-None-Match": resp.headers["ETag"]})
        self.assertEqual(revalidate.status_code, 304)

    def test_replacing_changes_the_version(self):
        first = self.upload(PNG).get_json()["wallpaper"]["v"]
        second = self.upload(JPEG).get_json()["wallpaper"]["v"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.anon.get("/api/wallpaper").get_data(), JPEG)

    def test_removal_is_idempotent(self):
        self.upload(WEBP)
        for _ in range(2):
            body = self.admin.delete("/api/wallpaper").get_json()
            self.assertEqual(body, {"ok": True, "wallpaper": {"custom": False, "v": "", "lum": None}})
        self.assertEqual(self.anon.get("/api/wallpaper").status_code, 404)
        self.assertFalse(self.anon.get("/api/configs").get_json()["wallpaper"]["custom"])

    def test_corrupt_metadata_reads_as_no_wallpaper(self):
        self.upload(WEBP)
        meta_path = app_module._wallpaper_paths()[2]
        for garbage in ("{not json", json.dumps({"v": "../../etc/passwd", "mime": "image/webp"}),
                        json.dumps({"v": "0123456789abcdef", "mime": "text/html"})):
            meta_path.write_text(garbage, encoding="utf-8")
            self.assertEqual(self.anon.get("/api/wallpaper").status_code, 404, garbage)
            self.assertFalse(self.anon.get("/api/configs").get_json()["wallpaper"]["custom"])

    def test_wallpaper_stays_out_of_config_and_export(self):
        self.upload(WEBP)
        self.assertNotIn("wallpaper", self.read_config())
        self.assertNotIn("wallpaper", self.admin.get("/api/configs/export").get_json())
        self.assertTrue(str(app_module._wallpaper_paths()[0]).startswith(str(self.data_dir)),
                        "壁纸必须落在数据目录（bind mount）里，否则重建容器就丢了")


class WallpaperPrivateModeTest(WallpaperApiBase):
    def test_locked_visitors_cannot_fetch_the_custom_wallpaper(self):
        self.upload(WEBP)
        app_module.PRIVATE_MODE = True
        self.assertEqual(self.anon.get("/api/wallpaper").status_code, 403)
        # 空壳里如实说「没有」，页面回落到内置壁纸，而不是去请求一个注定 403 的地址。
        shell = self.anon.get("/api/configs").get_json()
        self.assertTrue(shell["locked"])
        self.assertEqual(shell["wallpaper"], {"custom": False, "v": "", "lum": None})
        # 内置壁纸属于应用外壳，未解锁也能加载，解锁页不至于光秃秃。
        self.assertEqual(self.anon.get("/static/wallpapers/aurora.webp").status_code, 200)
        self.assertEqual(self.admin.get("/api/wallpaper").status_code, 200)


class BuiltinWallpaperAssetsTest(unittest.TestCase):
    """内置素材由 tools/make_wallpapers.py 绘制：体积预算、格式、与页面常量的一致性。"""

    BUDGET = {"": 320 * 1024, "-m": 200 * 1024, "-thumb": 12 * 1024}

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(WALL_DIR, "manifest.json"), encoding="utf-8") as fh:
            cls.manifest = json.load(fh)
        with open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()

    def test_every_variant_exists_is_webp_and_within_budget(self):
        self.assertGreaterEqual(len(self.manifest), 3)
        for entry in self.manifest:
            for suffix, budget in self.BUDGET.items():
                path = os.path.join(WALL_DIR, entry["id"] + suffix + ".webp")
                self.assertTrue(os.path.isfile(path), path)
                with open(path, "rb") as fh:
                    head = fh.read(12)
                self.assertEqual((head[:4], head[8:12]), (b"RIFF", b"WEBP"), path)
                self.assertLessEqual(os.path.getsize(path), budget, "%s 超出体积预算" % path)

    def test_page_constants_match_the_manifest(self):
        # 页面里的主色占位与亮度是手抄的：重画壁纸后忘了同步，遮罩就会按旧亮度算。
        block = re.search(r"const WALLPAPERS = \[(.*?)\];", self.html, re.S).group(1)
        found = re.findall(r"id: '([a-z]+)', name: '([^']+)', color: '(#[0-9a-f]{6})', lum: ([0-9.]+)", block)
        self.assertEqual([(e["id"], e["name"], e["color"], e["lum"]) for e in self.manifest],
                         [(i, n, c, float(lum)) for i, n, c, lum in found])

    def test_builtins_are_dark_enough_for_the_softest_scrim(self):
        # 白字 4.5:1 → 背景相对亮度 ≤ 0.183；遮罩「柔和」档 0.25，亮度约按 (1 - 0.25)^2.2 下降。
        for entry in self.manifest:
            self.assertLessEqual(entry["lum"] * (0.75 ** 2.2), 0.183, entry["id"])

    def test_assets_are_original_and_documented(self):
        with open(os.path.join(ROOT, "tools", "make_wallpapers.py"), encoding="utf-8") as fh:
            tool = fh.read()
        self.assertIn("程序化绘制", tool)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("tools/make_wallpapers.py", readme)
        self.assertIn("/api/wallpaper", readme)

    def test_wallpapers_survive_the_docker_build_context(self):
        from tests.test_build_context import ignored
        for entry in self.manifest:
            self.assertIsNone(ignored("static/wallpapers/%s.webp" % entry["id"]))


class WallpaperPageGuardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.resp = client.get("/")
        cls.html = cls.resp.get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)
        with open(os.path.join(ROOT, "static", "sw.js"), encoding="utf-8") as fh:
            cls.sw = fh.read()

    def test_csp_was_not_relaxed_for_wallpapers(self):
        csp = self.resp.headers["Content-Security-Policy"]
        self.assertIn("img-src 'self' data:;", csp)
        for opened in ("blob:", "https:", "http:", "*"):
            self.assertNotIn(opened, csp)

    def test_wallpaper_urls_are_same_origin_only(self):
        block = self.html[self.html.index("function currentWallpaper()"):self.html.index("function wallMinDim(")]
        self.assertEqual(sorted(set(re.findall(r"'(/[a-z/]+)[?/']", block))), ["/api/wallpaper", "/static/wallpapers/"])
        self.assertNotIn("http", block)
        # 上传前的压缩走 createImageBitmap + canvas，不需要 blob: 地址（CSP 没放行它）。
        upload = self.html[self.html.index("async function encodeWallpaper("):self.html.index("async function wallpaperApi(")]
        self.assertIn("createImageBitmap(file)", upload)
        self.assertNotIn("createObjectURL", upload)

    def test_wall_layer_sits_behind_everything_and_ignores_input(self):
        self.assertIn('<div class="home-wall" id="homeWall" aria-hidden="true"></div>', self.html)
        rule = re.search(r"\.home-wall \{([^}]*)\}", self.css).group(1)
        for decl in ("position: fixed", "z-index: -1", "pointer-events: none", "visibility: hidden"):
            self.assertIn(decl, rule)

    def test_glass_is_limited_to_a_few_large_surfaces(self):
        # 毛玻璃只给搜索框和导航；每个图标 / 小组件都加 backdrop-filter 会让手机掉帧。
        for selector in (".home-tile", ".tile-link", ".tile-avatar", ".widget-link", ".home-widget", ".tile-metric"):
            for rule in re.findall(r"(?:^|\})\s*([^{}]*%s[^{}]*)\{([^}]*)\}" % re.escape(selector), self.css):
                self.assertNotIn("backdrop-filter: blur", rule[1], rule[0].strip())
        glass = re.search(r"html\.wall-on \.home-search-bar, html\.wall-on \.sidebar \{([^}]*)\}", self.css)
        self.assertIn("backdrop-filter", glass.group(1))
        # 壁纸扩到全部页面后，分组页一屏几十张卡片：卡片自带的 blur 在壁纸场景里必须关掉。
        self.assertIn("html.wall-on .link-card { backdrop-filter: none; -webkit-backdrop-filter: none; }", self.css)

    def test_wallpaper_scene_covers_the_whole_workspace(self):
        # 回归：场景令牌曾经只挂在 #view-bookmarks 和 .sidebar 上，离开收藏首页就变回工作台样式，观感割裂。
        # 现在挂在 .app-shell（侧栏 + 全部视图）上；弹窗 / 命令面板 / 提示条在它之外，用文件顶部的深色令牌（见 test_ui_polish）。
        tokens = re.search(r"html\.wall-on \.app-shell \{([^}]*)\}", self.css).group(1)
        for decl in ("color-scheme: dark", "--surface: rgba(", "--text: #ffffff", "--border: rgba("):
            self.assertIn(decl, tokens)
        self.assertNotIn("#view-bookmarks", self.css[self.css.index("html.wall-on .home-wall"):self.css.index("/* 外观弹窗 */")])
        for gone in ("home-wall-on", "home-rail"):
            self.assertNotIn(gone, self.css)
            self.assertNotIn(gone, self.html)
        shell = self.html[self.html.index('<div class="app-shell">'):self.html.index("</main>")]
        for view in ('id="view-bookmarks"', 'id="view-checkin"', 'id="view-settings"', 'class="sidebar"'):
            self.assertIn(view, shell)
        for overlay in ('id="homeLookModal"', 'id="omniModal"'):
            self.assertGreater(self.html.index(overlay), self.html.index("</main>"))
        # 浅色主题的分组色调不能压过场景色调：场景选择器的优先级必须更高。
        self.assertIn("html.wall-on .app-shell .tone-mint {", self.css)
        # 浮层压在画面上必须是实底；根滚动条 / 页面底色跟着场景走，浅色主题下不露白边。
        self.assertRegex(self.css, r"html\.wall-on \.app-shell \.menu-popover[^{]*\{ background: #16161b;")
        self.assertIn("html.wall-on body { background: #0b0b0e; }", self.css)

    def test_look_does_not_depend_on_the_current_page(self):
        block = self.html[self.html.index("function applyLook()"):self.html.index("// 侧栏底部的收起 / 展开")]
        for page_state in ("LIB.page", "currentViewName", "onHomePage"):
            self.assertNotIn(page_state, block)
        self.assertIn("root.classList.toggle('wall-on', !!w);", block)
        self.assertIn("root.classList.toggle('nav-rail', rail);", block)
        # 导航形态在样式加载前就定下来（<head> 里的第一段脚本），打开页面时侧栏不会先宽后窄地跳一下。
        head = self.html[:self.html.index("</head>")]
        self.assertIn("bh_home_nav=full", head)
        self.assertIn("classList.add('nav-rail')", head)
        # 文案不再说「只影响收藏首页」。
        self.assertNotIn("只影响收藏首页", self.html)
        self.assertIn("对全部页面生效", self.html)

    def test_sidebar_offers_a_collapse_toggle_with_tooltips(self):
        self.assertIn('<button class="icon-btn nav-toggle" id="navToggle" type="button"', self.html)
        self.assertIn("writePref('bh_home_nav', homeNavPref() === 'rail' ? 'full' : 'rail');", self.html)
        # 图标栏里文字全部收起，靠 title 当提示：三个主标签、搜索入口、新建分组都要有。
        for needle in ('data-view="bookmarks" title="收藏库"', 'data-view="checkin" title="签到中心"',
                       'data-view="settings" title="系统设置"', 'id="omniOpen" type="button" title="搜索全部收藏"',
                       'data-act="add-group" title="新建分组"'):
            self.assertIn(needle, self.html)

    def test_icon_plates_keep_single_colour_logos_visible(self):
        # 回归：白色透明 logo 垫在浅色底板上显示成一片纯白。底板按图标明暗二选一，且两种组合的对比度都要够。
        def lum(hex_color):
            chans = [int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
            lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
            return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

        dark_plate = re.search(r"\.icon-light \{ --icon-plate: (#[0-9a-f]{6});", self.css).group(1)
        light_plate = re.search(r"\.icon-dark \{ --icon-plate: (#[0-9a-f]{6});", self.css).group(1)
        light_min = float(re.search(r"const ICON_LIGHT_LUM = ([0-9.]+);", self.html).group(1))
        dark_max = float(re.search(r"const ICON_DARK_LUM = ([0-9.]+);", self.html).group(1))
        # 被判成「浅色图标」的最暗像素压在深色底板上、被判成「深色图标」的最亮像素压在浅色底板上，都不低于 4.5:1。
        self.assertGreaterEqual((light_min + 0.05) / (lum(dark_plate) + 0.05), 4.5)
        self.assertGreaterEqual((lum(light_plate) + 0.05) / (dark_max + 0.05), 4.5)
        # ……而它们留在原来的底板上确实看不清（不到 1.6:1 / 2:1），所以才需要换。
        self.assertLess((lum(light_plate) + 0.05) / (light_min + 0.05), 1.6)
        # 四种头像共用这套底板；首页大图标的默认浅色底板让位给量出来的结果。
        self.assertIn(":is(.site-avatar, .mini-avatar, .link-avatar, .tile-avatar):is(.icon-light, .icon-dark) "
                      "{ background: var(--icon-plate); border-color: var(--icon-ring); }", self.css)
        self.assertIn(".tile-avatar:has(.avatar-img.is-ready) { background: var(--icon-plate, #f4f6fa); }", self.css)
        # 只换底板，不动图标本身的颜色：头像 / 图标规则里不能出现滤镜、反色、混合模式。
        for selector, body in re.findall(r"(?:^|\})\s*([^{}]*(?:avatar|icon-light|icon-dark)[^{}]*)\{([^}]*)\}", self.css):
            for banned in ("filter:", "invert(", "mix-blend-mode"):
                if banned == "filter:" and "backdrop-filter" in body:
                    continue
                self.assertNotIn(banned, body, selector.strip())
        # 无障碍：强制颜色模式下底板保留自己的颜色（否则背景被系统色替换，白色 logo 又看不见），高对比下描边加重。
        self.assertIn("@media (forced-colors: active) { .icon-light, .icon-dark { forced-color-adjust: none; } }", self.css)
        contrast = self.css[self.css.index("@media (prefers-contrast: more) {"):]
        self.assertIn(".icon-light { --icon-ring:", contrast[:contrast.index("}\n}") + 3])
        # 成功加载的自动图标与上传的备用图都会量。
        self.assertIn("markIconResolution(img); markIconTone(img);", self.html)
        self.assertIn('onload="uploadedIconReady(this)"', self.html)

    def test_degradation_paths_exist(self):
        self.assertIn("@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)))", self.css)
        self.assertIn("@media (prefers-reduced-transparency: reduce)", self.css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)
        # 高对比 / 强制颜色：壁纸整个让路（脚本里判，避免白字令牌残留在浅色主题上）；省流量模式不下载图片。
        self.assertIn("'(prefers-contrast: more), (forced-colors: active)'", self.html)
        self.assertIn("if (id === 'off' || wallBlocked()) return null;", self.html)
        self.assertIn("if (!url || saveDataOn()) return;", self.html)

    def test_rail_is_desktop_only(self):
        start = self.css.index("html.nav-rail { --sidebar-w: 72px; }")
        media = self.css.rfind("@media", 0, start)
        self.assertEqual(self.css[media:self.css.index("{", media)].strip(), "@media (min-width: 761px)")

    def test_small_groups_share_a_row_on_phones(self):
        mobile = self.css[self.css.index("@media (max-width: 760px)"):]
        self.assertIn(".home-list.is-tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));", mobile)
        self.assertIn(".home-list.is-tiles .home-section { grid-column: span var(--span, 4);", mobile)
        self.assertIn(".home-widget { grid-column: span 2; }", self.css)

    def test_home_toolbar_wraps_instead_of_widening_the_page(self):
        # 回归：加了第三档密度和「外观」按钮后，工具条在 390px 手机上比视口宽 24px——页面一旦超宽，
        # 手机浏览器会把整页缩小，连弹窗右侧都被裁掉（「完成」按钮点不到）。放不下就换行。
        # 现在工具条在手机上默认收在「⋯」后面（见 test_home_layout），展开时这条护栏照样成立。
        rule = re.search(r"\n\.home-toolbar \{([^}]*)\}", self.css).group(1)
        self.assertIn("flex-wrap: wrap", rule)
        mobile = self.css[self.css.index("@media (max-width: 760px)"):]
        self.assertIn(".home-look-btn span { display: none; }", mobile)
        self.assertIn(".home-view-toggle button span { display: none; }", mobile)

    def test_preferences_never_touch_web_storage(self):
        for name in ("bh_wallpaper", "bh_wp_dim", "bh_home_nav"):
            self.assertIn("readPref('%s'" % name, self.html)
            self.assertIn("writePref('%s'" % name, self.html)

    def test_service_worker_caches_wallpapers_first_and_was_bumped(self):
        self.assertGreaterEqual(int(re.search(r"bh-shell-v(\d+)", self.sw).group(1)), 9)
        self.assertIn("url.pathname.startsWith('/static/wallpapers/')", self.sw)
        # 接口（含 /api/wallpaper）仍然完全不经过 SW。
        self.assertLess(self.sw.index("if (url.pathname.startsWith('/api/')) return;"),
                        self.sw.index("if (isWallpaperRequest(url))"))


if __name__ == "__main__":
    unittest.main()

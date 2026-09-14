# -*- coding: utf-8 -*-
"""卡片「···」菜单的层级与展开方向：菜单必须浮在相邻卡片之上，不被裁切或被底栏遮住。

回归背景：卡片 :hover 的 transform 会新建层叠上下文，把 .menu-popover 的 z-index 关在卡片
内部，导致菜单被 DOM 顺序靠后的下一行卡片覆盖。修复方式是给展开菜单的卡片加 .menu-open
并提升卡片自身层级，浏览器端行为由 Playwright 验证，这里守住样式与脚本挂点不被改回去。
"""
import re
import unittest

from app import app


class MenuLayeringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()
        cls.css = cls.client.get("/static/app-v3.css").get_data(as_text=True)
        cls.html = cls.client.get("/").get_data(as_text=True)

    def test_open_menu_raises_both_card_types(self):
        rule = re.search(r"\.bookmark-card\.menu-open[^{]*\.link-card\.menu-open\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(rule, "缺少 .bookmark-card/.link-card .menu-open 提升层级的规则")
        z = re.search(r"z-index:\s*(\d+)", rule.group(1))
        self.assertIsNotNone(z, "menu-open 规则必须设置 z-index")
        # 必须高于普通卡片（auto/0），又要低于固定底栏 .sidebar 的 z-index: 30。
        self.assertGreater(int(z.group(1)), 12)
        self.assertLess(int(z.group(1)), 30)

    def test_popover_is_scrollable_and_height_capped(self):
        rule = re.search(r"\.menu-popover\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(rule)
        self.assertIn("overflow-y: auto", rule.group(1))
        self.assertIn("max-height", rule.group(1))

    def test_toggle_handler_marks_card_and_picks_direction(self):
        self.assertIn("card.classList.toggle('menu-open', d.open)", self.html)
        # 方向按真实可用空间判断，而不是旧的「视口上半部就向下」硬阈值。
        self.assertNotIn("rect.top < window.innerHeight / 2", self.html)
        self.assertIn("pop.classList.toggle('drop-down', dropDown)", self.html)
        # 移动端固定底栏要参与可用空间计算，并在触发按钮被底栏压住时整体上移。
        self.assertIn("getComputedStyle(nav).position === 'fixed'", self.html)
        self.assertIn("pop.style.transform = 'translateY(-'", self.html)
        # 菜单开着时滚动/缩放要重新定位，否则会滑到固定底栏下面。
        self.assertIn("window.addEventListener('scroll', replaceOpenMenus", self.html)
        self.assertIn("window.addEventListener('resize', replaceOpenMenus)", self.html)

    def test_card_grids_are_not_clipped(self):
        for selector in (r"\.bookmark-grid\s*\{([^}]*)\}", r"\.link-grid\s*\{([^}]*)\}"):
            body = re.search(selector, self.css).group(1)
            self.assertNotIn("overflow: hidden", body)


if __name__ == "__main__":
    unittest.main()

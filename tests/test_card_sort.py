# -*- coding: utf-8 -*-
"""卡片网格拖拽排序：站点看板与分组内的网址都能按住把手拖动改顺序。

要点：
- 只有把手能发起拖拽（卡片本体是 <a>，整卡可拖会和点击打架）；
- 搜索过滤时把手禁用（看到的只是子集，按可见顺序写回会打乱被过滤掉的条目）；
- 把手 touch-action:none，触摸端也能拖；
- 顺序未变时不打接口。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests._support import StoreIsolationMixin, app_module  # noqa: F401  须早于 app 导入
from app import app  # noqa: E402

NODE = shutil.which("node")
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "index.html")


class CardSortUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        client = app.test_client()
        cls.html = client.get("/").get_data(as_text=True)
        cls.css = client.get("/static/app-v3.css").get_data(as_text=True)

    def test_both_grids_are_wired(self):
        self.assertIn("[$('bmList'), $('linkList')].forEach(list =>", self.html)
        for handler in ("onCardPointerDown", "onCardPointerMove", "onCardPointerUp", "onCardKeyDown"):
            self.assertIn(handler, self.html)

    def test_cards_carry_a_sort_id(self):
        # 网址用 link id，看板用原始下标——两套接口的 order 形态不同。
        self.assertIn('data-card-id="${lid}"', self.html)
        self.assertIn('data-card-id="${i}"', self.html)

    def test_only_the_grip_starts_a_drag(self):
        self.assertIn("e.target.closest('[data-card-grip]')", self.html)
        self.assertIn("data-card-grip", self.html)

    def test_grip_is_disabled_while_filtering(self):
        # 网址卡片看 searching，看板卡片看 filtering（搜索或预警筛选）；两处都要禁用。
        # 看板卡片同时用在首页的卡片视图里，那里顺序跟随看板、不提供排序，所以也禁用。
        self.assertIn("${searching ? ' disabled' : ''}>${icon('grip')}", self.html)
        self.assertIn("${o.filtering || o.home ? ' disabled' : ''}>${icon('grip')}", self.html)
        self.assertIn("bookmarkCardHtml(b, i, { filtering })", self.html)

    def test_touch_drag_is_possible(self):
        grip_rule = re.search(r"\.card-grip \{[^}]*\}", self.css).group(0)
        self.assertIn("touch-action: none", grip_rule)

    def test_keyboard_fallback_exists(self):
        self.assertIn("e.altKey", self.html)
        self.assertIn("moveBm(Number(el.dataset.cardId), dir)", self.html)
        self.assertIn("moveLink(LIB.page, el.dataset.cardId, dir)", self.html)

    def test_drag_styles_exist(self):
        for rule in (".card-grip", ".bookmark-card.is-dragging", ".link-grid.is-sorting"):
            self.assertIn(rule, self.css)


@unittest.skipIf(NODE is None, "未安装 node，跳过插入点算法验证")
class InsertPointTest(unittest.TestCase):
    """cardInsertBefore 是纯几何函数：给一组矩形和指针坐标，验证它选中的插入点。"""

    @classmethod
    def setUpClass(cls):
        with open(TEMPLATE, encoding="utf-8") as fh:
            blob = fh.read()
        start = blob.index("function cardInsertBefore")
        end = blob.index("// 把当前 DOM 顺序写回后端")
        cls.logic = blob[start:end]

    def where(self, x, y, dragged="a"):
        # 3 列 × 2 行，每格 100x50，列间距 10。
        rects = {}
        ids = ["a", "b", "c", "d", "e", "f"]
        for i, cid in enumerate(ids):
            row, col = divmod(i, 3)
            rects[cid] = {"left": col * 110, "top": row * 60, "width": 100, "height": 50}
        script = """
const RECTS = %s;
const IDS = %s;
function box(id) {
  const r = RECTS[id];
  return { left: r.left, top: r.top, width: r.width, height: r.height,
           right: r.left + r.width, bottom: r.top + r.height };
}
const cards = IDS.map(id => ({ id, getBoundingClientRect: () => box(id) }));
function cardsIn() { return cards; }
%s
const dragged = cards.find(c => c.id === %s);
const hit = cardInsertBefore(null, dragged, %d, %d);
console.log(JSON.stringify(hit ? hit.id : null));
""" % (json.dumps(rects), json.dumps(ids), self.logic, json.dumps(dragged), x, y)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout.strip())
        finally:
            os.unlink(path)

    def test_left_half_of_a_card_inserts_before_it(self):
        self.assertEqual(self.where(120, 25), "b")   # b 的左半边（110..160）

    def test_right_half_inserts_after(self):
        self.assertEqual(self.where(175, 25), "c")   # 越过 b 的中线，落到 c 之前

    def test_pointer_above_the_first_row_goes_to_the_front(self):
        self.assertEqual(self.where(50, -20, dragged="d"), "a")

    def test_pointer_below_everything_appends_to_the_end(self):
        self.assertIsNone(self.where(400, 500))

    def test_second_row_is_reachable(self):
        self.assertEqual(self.where(10, 85), "d")    # 第二行第一格的左半边

    def test_dragged_card_never_matches_itself(self):
        # 指针停在自己身上时不应返回自己，否则 insertBefore(self, self) 白折腾。
        self.assertNotEqual(self.where(10, 25, dragged="a"), "a")


if __name__ == "__main__":
    unittest.main()

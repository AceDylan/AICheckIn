# -*- coding: utf-8 -*-
"""取值路径里的四则运算算式：普通路径照旧，带运算符的按算式算（如 Mistral /v1/billing/budget 的已用 / 剩余额度）。"""
import unittest

from app import clean_field, convert_value, resolve_json_path

BUDGET = {
    "vibe_budget": {"usage_percentage": 1.048248816, "initial_budget": 255.0, "currency": "EUR",
                    "reset_at": "2026-12-01T00:00:00Z", "payg_enabled": False},
    "api_budget": {"usage_percentage": 0.7878240933333334, "initial_budget": 25.5, "currency": "EUR",
                   "reset_at": "2026-12-01T00:00:00Z", "payg_enabled": False},
    "data": {"remain-quota": "12.5", "items": [{"v": 3}]},
}


def amount(expr):
    value, err = resolve_json_path(BUDGET, expr)
    assert not err, err
    return convert_value(value, {"type": "amount"})[0]


class PathExprTest(unittest.TestCase):
    def test_budget_used_and_remaining(self):
        self.assertEqual(amount("vibe_budget.initial_budget * vibe_budget.usage_percentage / 100"), "2.67")
        self.assertEqual(amount("vibe_budget.initial_budget * (1 - vibe_budget.usage_percentage / 100)"), "252.33")
        self.assertEqual(amount("vibe_budget.initial_budget - vibe_budget.initial_budget * vibe_budget.usage_percentage / 100"), "252.33")
        self.assertEqual(amount("api_budget.initial_budget * api_budget.usage_percentage / 100"), "0.20")
        self.assertEqual(amount("api_budget.initial_budget * (100 - api_budget.usage_percentage) / 100"), "25.30")

    def test_plain_paths_unchanged(self):
        self.assertEqual(resolve_json_path(BUDGET, "vibe_budget.currency"), ("EUR", None))
        # 键名里带减号的旧配置仍按普通路径取值。
        self.assertEqual(resolve_json_path(BUDGET, "data.remain-quota"), ("12.5", None))
        self.assertEqual(resolve_json_path(BUDGET, "data.nope"), (None, "未找到路径 data.nope"))

    def test_numeric_strings_list_index_and_unary_minus(self):
        self.assertEqual(resolve_json_path(BUDGET, "data.remain-quota * 2"), (25.0, None))
        self.assertEqual(resolve_json_path(BUDGET, "-data.items.0.v + 10"), (7.0, None))
        self.assertEqual(resolve_json_path(BUDGET, "(data.items.0.v)"), (3.0, None))

    def test_runtime_errors(self):
        self.assertEqual(resolve_json_path(BUDGET, "vibe_budget.missing * 2")[1], "未找到路径 vibe_budget.missing")
        self.assertIn("不是数字", resolve_json_path(BUDGET, "vibe_budget.currency * 2")[1])
        self.assertIn("不是数字", resolve_json_path(BUDGET, "vibe_budget.payg_enabled + 1")[1])
        self.assertIn("除数为 0", resolve_json_path(BUDGET, "1 / (data.items.0.v - 3)")[1])

    def test_syntax_checked_on_save(self):
        base = {"label": "已用", "type": "amount", "method": "GET", "url": "https://api.example.com/v1/billing/budget", "headers": {}}
        ok = clean_field(dict(base, json_path="vibe_budget.initial_budget * vibe_budget.usage_percentage / 100"))
        self.assertIn("*", ok["json_path"])
        for bad in ("a.b * (c", "a.b *", "* a.b", "a.b ) + 1", "nan * 2 )"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                clean_field(dict(base, json_path=bad))
            self.assertIn("取值算式有误", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

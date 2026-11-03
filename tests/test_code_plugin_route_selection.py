from __future__ import annotations

import unittest

from experiments.select_code_plugin_route import select_minimum_non_regressing_groups


class CodePluginRouteSelectionTests(unittest.TestCase):
    def test_selects_minimum_non_regressing_crossover(self) -> None:
        selected, diagnostics = select_minimum_non_regressing_groups(
            cues=("cumulative refund cap",),
            group_pool=(
                ("订单", "退款", "天花板"),
                ("订单", "退款", "不超过", "销售额"),
                ("退款", "金额"),
            ),
            development_instructions={
                "a": "订单退款设天花板",
                "b": "订单退款不超过销售额",
                "c": "Use a cumulative refund cap",
            },
            legacy_instructions={"old": "统计退款金额"},
        )

        self.assertEqual(
            selected,
            (("订单", "退款", "天花板"), ("订单", "退款", "不超过", "销售额")),
        )
        self.assertEqual(diagnostics["safe_pool_size"], 2)
        self.assertEqual(diagnostics["unsafe_groups"], [["退款", "金额"]])

    def test_returns_none_when_archive_cannot_cover_development(self) -> None:
        selected, _ = select_minimum_non_regressing_groups(
            cues=("cumulative refund cap",),
            group_pool=(("订单", "退款", "上限"),),
            development_instructions={"a": "净额不得为负数"},
            legacy_instructions={},
        )

        self.assertIsNone(selected)


if __name__ == "__main__":
    unittest.main()

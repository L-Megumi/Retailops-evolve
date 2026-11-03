from __future__ import annotations

import unittest

from retailops_agent.plugin_sandbox import UnsafePluginError, run_refund_plugin


VALID_PLUGIN = """def transform_refunds(order_totals_cents, refunds):
    remaining = dict(order_totals_cents)
    result = []
    for refund in refunds:
        order_id = refund["order_id"]
        available = remaining.get(order_id, 0)
        amount = min(refund["refund_cents"], available)
        if amount <= 0:
            continue
        row = dict(refund)
        row["refund_cents"] = amount
        result.append(row)
        remaining[order_id] = available - amount
    return result
"""


class PluginSandboxTests(unittest.TestCase):
    def test_valid_plugin_caps_cumulative_refunds(self):
        result = run_refund_plugin(
            VALID_PLUGIN,
            order_totals_cents={"O1": 1000},
            refunds=[
                {
                    "refund_id": "R1",
                    "order_id": "O1",
                    "refund_date": "2026-10-01",
                    "refund_cents": 800,
                },
                {
                    "refund_id": "R2",
                    "order_id": "O1",
                    "refund_date": "2026-10-02",
                    "refund_cents": 600,
                },
            ],
        )
        self.assertEqual([row["refund_cents"] for row in result], [800, 200])

    def test_import_is_rejected(self):
        with self.assertRaises(UnsafePluginError):
            run_refund_plugin(
                "import os\ndef transform_refunds(order_totals_cents, refunds):\n return refunds\n",
                order_totals_cents={},
                refunds=[],
            )

    def test_dunder_access_is_rejected(self):
        with self.assertRaises(UnsafePluginError):
            run_refund_plugin(
                "def transform_refunds(order_totals_cents, refunds):\n return refunds.__class__\n",
                order_totals_cents={},
                refunds=[],
            )


if __name__ == "__main__":
    unittest.main()

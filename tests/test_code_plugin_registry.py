from __future__ import annotations

import unittest

from retailops_agent.code_plugin_registry import CodePluginRegistration


class CodePluginRegistryTests(unittest.TestCase):
    def test_literal_cues_match_case_insensitively(self):
        registration = CodePluginRegistration(
            plugin_id="cap",
            cues=("累计退款上限", "cumulative refund cap"),
            source_sha256="a" * 64,
            source_path="candidate.py",
            keyword_groups=(("退款", "上限"), ("refund", "cap")),
        )
        self.assertTrue(registration.matches("执行累计退款上限检查"))
        self.assertTrue(registration.matches("Apply a CUMULATIVE REFUND CAP."))
        self.assertFalse(registration.matches("普通退款日汇总"))
        self.assertTrue(registration.matches("该任务要求给退款金额设置业务上限"))
        self.assertTrue(registration.matches("cap every REFUND at order value"))

    def test_broad_or_duplicate_cues_are_rejected(self):
        with self.assertRaises(ValueError):
            CodePluginRegistration(
                plugin_id="cap",
                cues=("退款",),
                source_sha256="a" * 64,
                source_path="candidate.py",
            )
        with self.assertRaises(ValueError):
            CodePluginRegistration(
                plugin_id="cap",
                cues=("累计退款上限", "累计退款上限"),
                source_sha256="a" * 64,
                source_path="candidate.py",
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from harness2.core.spec import ChannelSpec
    from retailops_agent.harness2_adapter import BASE_SCRIPT, DeepSeekRetailOpsProposer
except ImportError as exc:  # Harness2 is an optional project dependency.
    raise unittest.SkipTest(str(exc)) from exc


class FakeDeepSeekModel:
    last_usage = {"prompt_tokens": 10, "completion_tokens": 8}

    def complete_json(self, **_kwargs):
        return {
            "refund_rate_denominator": "distinct_orders",
            "category_refund_allocation": "proportional",
            "refund_time_attribution": "order_date",
            "reason": "validator feedback requires both definitions",
        }


class FakePartialDeepSeekModel:
    last_usage = {"prompt_tokens": 6, "completion_tokens": 4}

    def complete_json(self, **_kwargs):
        return {
            "category_refund_allocation": "proportional",
            "reason": "only the flagged category policy is returned",
        }


class DeepSeekProposerTests(unittest.TestCase):
    def test_policy_response_is_applied_to_harness(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            script = context / "harness/scripts/analyze.py"
            script.parent.mkdir(parents=True)
            script.write_text(BASE_SCRIPT, encoding="utf-8")
            rollouts = context / "rollouts"
            rollouts.mkdir()
            (rollouts / "base1.md").write_text(
                '{"ok":false,"policy":{"refund_rate_denominator":"order_lines",'
                '"category_refund_allocation":"first_category",'
                '"refund_time_attribution":"refund_date"},'
                '"issues":[{"code":"refund_rate_definition",'
                '"expected":"distinct_orders"},'
                '{"code":"category_refund_allocation",'
                '"expected":"proportional"}]}',
                encoding="utf-8",
            )
            proposer = DeepSeekRetailOpsProposer(
                ChannelSpec("opencode", "deepseek/deepseek-flash"),
                FakeDeepSeekModel(),
                audit_dir=context / "audit",
            )
            reply = proposer.call("repair retail metrics", context, "inv1")
            self.assertTrue(reply.ok)
            updated = script.read_text(encoding="utf-8")
            self.assertIn('refund_rate_denominator="distinct_orders"', updated)
            self.assertIn('category_refund_allocation="proportional"', updated)
            self.assertIn('refund_time_attribution="refund_date"', updated)
            self.assertTrue((context / "audit/inv1.json").is_file())

    def test_unflagged_policy_fields_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            script = context / "harness/scripts/analyze.py"
            script.parent.mkdir(parents=True)
            script.write_text(BASE_SCRIPT, encoding="utf-8")
            rollouts = context / "rollouts"
            rollouts.mkdir()
            (rollouts / "base1.md").write_text(
                '{"ok":false,"policy":{"refund_rate_denominator":"order_lines",'
                '"category_refund_allocation":"first_category",'
                '"refund_time_attribution":"refund_date"},'
                '"issues":[{"code":"refund_time_attribution",'
                '"expected":"order_date"}]}',
                encoding="utf-8",
            )
            proposer = DeepSeekRetailOpsProposer(
                ChannelSpec("opencode", "deepseek/deepseek-flash"),
                FakeDeepSeekModel(),
            )
            proposer.call("repair daily attribution", context, "inv1")
            updated = script.read_text(encoding="utf-8")
            self.assertIn('refund_rate_denominator="order_lines"', updated)
            self.assertIn('category_refund_allocation="first_category"', updated)
            self.assertIn('refund_time_attribution="order_date"', updated)

    def test_missing_unflagged_fields_use_current_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            script = context / "harness/scripts/analyze.py"
            script.parent.mkdir(parents=True)
            script.write_text(BASE_SCRIPT, encoding="utf-8")
            rollouts = context / "rollouts"
            rollouts.mkdir()
            (rollouts / "base1.md").write_text(
                '{"ok":false,"policy":{"refund_rate_denominator":"order_lines",'
                '"category_refund_allocation":"first_category",'
                '"refund_time_attribution":"refund_date"},'
                '"issues":[{"code":"category_refund_allocation",'
                '"expected":"proportional"}]}',
                encoding="utf-8",
            )
            proposer = DeepSeekRetailOpsProposer(
                ChannelSpec("opencode", "deepseek/deepseek-flash"),
                FakePartialDeepSeekModel(),
            )
            proposer.call("repair category allocation", context, "inv1")
            updated = script.read_text(encoding="utf-8")
            self.assertIn('refund_rate_denominator="order_lines"', updated)
            self.assertIn('category_refund_allocation="proportional"', updated)
            self.assertIn('refund_time_attribution="refund_date"', updated)


if __name__ == "__main__":
    unittest.main()

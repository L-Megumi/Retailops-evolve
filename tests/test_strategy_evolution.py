from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from experiments.run_rsi_evolution import DeterministicRuleProposer, run_evolution


ROOT = Path(__file__).resolve().parents[1]


class OverfitThenGeneralizeProposer(DeterministicRuleProposer):
    def propose(self, **kwargs):
        instruction = str(kwargs["instruction"])
        rejection = str(kwargs["rejection"])
        if "逐笔计算退款日与原订单日的自然日间隔" in instruction:
            self.request_count += 1
            return {
                "cue": "审计优先规则" if not rejection else "间隔不超过",
                "action": "hybrid_refund_time",
                "reason": "generalize after the validation gain floor rejects a numeric cue",
            }
        return super().propose(**kwargs)


class StrategyEvolutionTests(unittest.TestCase):
    def test_four_bounded_generations_improve_unseen_transfer_without_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_evolution(
                suite=ROOT / "benchmarks/retailops/rsi-v2",
                output=Path(directory) / "evolution",
                proposer=DeterministicRuleProposer(),
                max_generations=6,
                candidates_per_generation=1,
            )
            self.assertEqual(result["accepted_generations"], 4)
            self.assertEqual(result["candidate_attempts"], 4)
            self.assertEqual(result["initial"]["regression"]["passed"], 40)
            self.assertEqual(result["initial"]["evolution"]["passed"], 32)
            self.assertEqual(result["initial"]["validation"]["passed"], 24)
            for split in ("regression", "evolution", "validation", "transfer"):
                self.assertEqual(
                    result["final"][split]["passed"],
                    result["final"][split]["task_count"],
                )
            self.assertTrue(result["transfer_opened_once_after_search"])

    def test_validation_gate_rejects_narrow_overfit_then_accepts_general_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_evolution(
                suite=ROOT / "benchmarks/retailops/rsi-v2",
                output=Path(directory) / "regularized-evolution",
                proposer=OverfitThenGeneralizeProposer(),
                max_generations=6,
                candidates_per_generation=2,
                minimum_split_gain=4,
            )
            self.assertEqual(result["accepted_generations"], 4)
            self.assertEqual(result["candidate_attempts"], 5)
            rejected = result["ledger"][-2]
            accepted = result["ledger"][-1]
            self.assertFalse(rejected["decision"]["accepted"])
            self.assertFalse(all(rejected["decision"]["gate_checks"].values()))
            self.assertTrue(accepted["decision"]["accepted"])
            self.assertEqual(result["final"]["transfer"]["passed"], 40)


if __name__ == "__main__":
    unittest.main()

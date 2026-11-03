from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.evaluate_rsi_benchmark import evaluate_suite
from experiments.generate_rsi_benchmark import generate_suite
from experiments.verify_rsi_benchmark import verify_suite


ROOT = Path(__file__).resolve().parents[1]


class RsiBenchmarkTests(unittest.TestCase):
    def test_generated_suite_is_isolated_reproducible_and_solvable(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = Path(directory) / "rsi-smoke"
            manifest = generate_suite(
                suite,
                ROOT / "benchmarks/retailops/holdout-v4",
                evolution_count=4,
                validation_count=4,
                transfer_count=4,
                capability_count=4,
                seed=17,
            )
            self.assertEqual(manifest["count"], 64)
            self.assertEqual(verify_suite(suite), [])

            for task_path in (suite / "tasks").glob("*/*/task.json"):
                task = json.loads(task_path.read_text(encoding="utf-8"))
                self.assertNotIn("metric_contract", task)
                self.assertFalse((task_path.parent / "expected.json").exists())

            result = evaluate_suite(suite)
            self.assertEqual(result["strategies"]["oracle_ceiling_ok"], 64)
            self.assertLess(result["strategies"]["feedback_repair_ok"], 64)
            self.assertLess(result["strategies"]["naive_ok"], 64)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from retailops_agent.evolution import CapabilityEvolver
from retailops_agent.plugin_manager import PluginManager
from tests.test_plugin_manager import METRIC_SOURCE


class CodeModel:
    def __init__(self, sources):
        self.sources = iter(sources)
        self.requests = []

    def complete_json(self, **request):
        self.requests.append(request)
        return {"source": next(self.sources), "reason": "fixture generation"}


@unittest.skipUnless(sys.platform.startswith("linux"), "requires sandbox")
class OnlineEvolutionTests(unittest.TestCase):
    def test_rejected_candidate_is_repaired_then_persisted_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "plugins/registry.json"
            path.parent.mkdir()
            path.write_text('{"schema_version":2,"plugins":[]}', encoding="utf-8")
            model = CodeModel(["import os\n", METRIC_SOURCE])
            evolver = CapabilityEvolver(model, root / "artifacts")
            manager = PluginManager.from_manifest(path)
            receipt = evolver.ensure(manager, "refund_replay_rate", "Calculate refund replay rate")
            self.assertEqual(receipt["attempts"], 2)
            self.assertIn("critic", json.loads(model.requests[1]["user"]))
            self.assertEqual(receipt["frozen_audit_cases"], 5)
            loaded = PluginManager.from_manifest(path)
            self.assertEqual(len(loaded.records), 1)
            reused = evolver.ensure(loaded, "refund_replay_rate", "another paraphrase")
            self.assertTrue(reused["reused"])
            self.assertEqual(len(model.requests), 2)
            generated = list((path.parent / "generated").glob("*.py"))
            self.assertEqual(len(generated), 1)
            self.assertEqual(generated[0].read_text(), METRIC_SOURCE)

    def test_unknown_verifier_stops_before_model_request(self):
        with tempfile.TemporaryDirectory() as directory:
            model = CodeModel([])
            with self.assertRaises(ValueError):
                CapabilityEvolver(model, Path(directory)).ensure(PluginManager(), "unknown_metric", "invent a KPI")
            self.assertEqual(model.requests, [])

    def test_generation_budget_does_not_change_live_registry_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager()
            model = CodeModel(["import os\n", "import sys\n"])
            with self.assertRaises(RuntimeError):
                CapabilityEvolver(model, Path(directory)).ensure(manager, "refund_replay_rate", "Calculate refund replay rate")
            self.assertFalse(manager.records)
            self.assertEqual(len(model.requests), 2)

    def test_second_metric_uses_its_own_definition_and_verifier(self):
        source = """def compute_metric(orders, refunds):
    seen = {}
    for row in refunds:
        seen[row.get('refund_id')] = 1
    return {'metric':'unique_refund_events','data':{'unique_refund_events':len(seen)}}
"""
        with tempfile.TemporaryDirectory() as directory:
            model = CodeModel([source])
            manager = PluginManager()
            CapabilityEvolver(model, Path(directory)).ensure(manager, "unique_refund_events", "Count unique refund events")
            self.assertEqual(manager.records[0].spec.output_name, "unique_refund_events")
            self.assertEqual(manager.records[0].status, "active")


if __name__ == "__main__":
    unittest.main()

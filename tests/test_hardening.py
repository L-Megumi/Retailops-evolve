from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retailops_agent.agent import RetailOpsAgent
from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.metric_catalog import metric_definition
from retailops_agent.plugin_manager import PluginExecutionError, PluginManager, RegistryConflict
from retailops_agent.plugin_protocol import PluginSpec
from retailops_agent.plugin_sandbox import UnsafePluginError, run_json_plugin, validate_plugin_source
from retailops_agent.semantic_plugin_router import CapabilitySpec
from retailops_agent.task_contract import TaskContract, infer_task_contract
from tests.test_plugin_manager import (CAPABILITY, DEDUP_CAPABILITY, DEDUP_SOURCE,
    DEDUP_SPEC, DUPLICATE_REFUNDS, GOOD_SOURCE, METRIC_SOURCE, ORDERS, REFUNDS,
    dedup_registration, install, registration)


def metric_registration(source=METRIC_SOURCE):
    return CodePluginRegistration("replay", ("refund replay rate",),
                                  hashlib.sha256(source.encode()).hexdigest(), "metric.py")


def install_metric(manager, source=METRIC_SOURCE):
    return manager.install(version="1", registration=metric_registration(source),
                           capability=CapabilitySpec("replay", "raw refund replay rate", ()),
                           source=source, spec=PluginSpec.metric_tool("refund_replay_rate"),
                           activate=True)


class StaticSafetyTests(unittest.TestCase):
    def test_attribute_chain_is_rejected_before_execution(self):
        with self.assertRaises(UnsafePluginError):
            validate_plugin_source("def transform_refunds(order_totals_cents, refunds):\n    list = refunds.__class__.__base__.__subclasses__\n    return list()\n")

    def test_safe_method_cannot_be_extracted_as_alias(self):
        with self.assertRaises(UnsafePluginError):
            validate_plugin_source("def transform_refunds(order_totals_cents, refunds):\n    dict = refunds.copy\n    return dict()\n")

    def test_builtin_cannot_be_shadowed_even_by_local_assignment(self):
        with self.assertRaises(UnsafePluginError):
            validate_plugin_source("def transform_refunds(order_totals_cents, refunds):\n    len = 1\n    return refunds\n")

    def test_default_expressions_and_nested_functions_are_rejected(self):
        for source in (
            "def transform_refunds(order_totals_cents, refunds=[]):\n    return refunds\n",
            "def transform_refunds(order_totals_cents, refunds):\n    def dict():\n        return refunds\n    return dict()\n",
        ):
            with self.subTest(source=source), self.assertRaises(UnsafePluginError):
                validate_plugin_source(source)

    def test_source_is_rejected_without_launching_a_worker(self):
        with patch("retailops_agent.plugin_sandbox.subprocess.run") as runner:
            with self.assertRaises(UnsafePluginError):
                run_json_plugin("def compute_metric(orders, refunds):\n    return refunds.__class__\n",
                                entrypoint="compute_metric", argument_names=("orders", "refunds"),
                                arguments={"orders": [], "refunds": []})
            runner.assert_not_called()


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux seccomp")
class KernelIsolationTests(unittest.TestCase):
    def test_seccomp_denies_files_network_and_process_creation(self):
        module_root = Path(__file__).resolve().parents[1] / "src/retailops_agent"
        program = (
            "import sys, os, socket, json; "
            f"sys.path.insert(0, {str(module_root)!r}); "
            "from plugin_worker import _apply_limits, _lock_syscalls; "
            "_apply_limits(); _lock_syscalls()\n"
            "out = {}\n"
            "for name, operation in [('file', lambda: open('/etc/hostname')), "
            "('network', lambda: socket.socket()), ('process', lambda: os.fork())]:\n"
            "    try:\n        operation(); out[name] = 'ALLOWED'\n"
            "    except OSError as exc:\n        out[name] = exc.errno\n"
            "print(json.dumps(out))\n"
        )
        result = subprocess.run([sys.executable, "-I", "-S", "-c", program],
                                text=True, capture_output=True, timeout=3, env={})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"file": 1, "network": 1, "process": 1})

    def test_resource_exhaustion_is_stopped(self):
        source = "def compute_metric(orders, refunds):\n    return 'x' * 1000000000\n"
        with self.assertRaises(UnsafePluginError):
            run_json_plugin(source, entrypoint="compute_metric", argument_names=("orders", "refunds"),
                            arguments={"orders": [], "refunds": []})

    def test_parent_environment_is_not_forwarded(self):
        with patch.dict(os.environ, {"RETAILOPS_TEST_SECRET": "never-forward"}):
            with patch("retailops_agent.plugin_sandbox.subprocess.run") as runner:
                runner.return_value = subprocess.CompletedProcess([], 0, "{}", "")
                run_json_plugin("def compute_metric(orders, refunds):\n    return {}\n",
                                entrypoint="compute_metric", argument_names=("orders", "refunds"),
                                arguments={"orders": [], "refunds": []})
                self.assertNotIn("RETAILOPS_TEST_SECRET", runner.call_args.kwargs["env"])
                self.assertTrue(runner.call_args.kwargs["close_fds"])


class ContractTests(unittest.TestCase):
    def test_new_metric_inferred_without_required_results(self):
        contract = infer_task_contract("Calculate refund replay rate")
        self.assertEqual(contract.required_results, ("refund_replay_rate",))

    def test_unsupported_natural_language_does_not_fall_back_to_old_metrics(self):
        with patch("retailops_agent.agent.load_orders", return_value=[]), patch(
            "retailops_agent.agent.load_refunds", return_value=[]):
            trace = RetailOpsAgent().run("Forecast next month's inventory", Path("orders"), Path("refunds"))
        self.assertFalse(trace.ok)
        self.assertEqual(trace.issues[0].code, "unsupported_task")
        self.assertFalse(trace.results)

    def test_missing_metric_is_detected_automatically(self):
        with patch("retailops_agent.agent.load_orders", return_value=[]), patch(
            "retailops_agent.agent.load_refunds", return_value=[]):
            trace = RetailOpsAgent().run("Calculate refund replay rate", Path("orders"), Path("refunds"))
        self.assertEqual(trace.issues[0].code, "unsupported_capability")
        self.assertGreaterEqual(trace.elapsed_ms, 0)

    def test_chinese_and_english_negation(self):
        for text in ("Do not apply cumulative refund cap; report daily sales", "不要应用累计退款上限；生成每日报告"):
            with self.subTest(text=text):
                manager = PluginManager()
                install(manager, GOOD_SOURCE, "1")
                self.assertFalse(manager.resolve_pipeline(text)[0])

    def test_registration_negation_is_respected_outside_manager(self):
        self.assertFalse(registration(GOOD_SOURCE).matches("Do not apply cumulative refund cap"))
        self.assertTrue(registration(GOOD_SOURCE).matches("Do not exceed cumulative refund cap"))

    def test_negation_does_not_disable_a_different_required_step(self):
        contract = infer_task_contract("Do not deduplicate refunds; apply cumulative refund cap")
        self.assertEqual(contract.required_capabilities, ("refund_cap",))
        self.assertEqual(contract.forbidden_capabilities, ("refund_dedup",))

    def test_partial_match_requires_both_transform_steps(self):
        manager = PluginManager()
        install(manager, GOOD_SOURCE, "1")
        manager.install(version="1", registration=dedup_registration(), capability=DEDUP_CAPABILITY,
                        source=DEDUP_SOURCE, spec=DEDUP_SPEC, activate=True)
        selected, _ = manager.resolve_pipeline(
            "deduplicate refunds by refund_id, then limit each order by its original value using a running allowance")
        self.assertEqual({record.plugin_id for record in selected}, {"refund-cap", "refund-dedup"})

    def test_missing_transform_stops_instead_of_returning_partial_pipeline(self):
        manager = PluginManager()
        install(manager, GOOD_SOURCE, "1")
        with self.assertRaises(PluginExecutionError):
            manager.resolve_pipeline("deduplicate refunds by refund_id then apply cumulative refund cap")

    def test_cache_key_includes_contract_and_capability_definition(self):
        manager = PluginManager()
        record = install(manager, GOOD_SOURCE, "1")
        key = manager._cache_key(record, "same task", TaskContract())
        self.assertNotEqual(key, manager._cache_key(record, "same task", TaskContract(("refund_replay_rate",))))
        record.capability = CapabilitySpec("refund-cap", "changed semantics", ())
        self.assertNotEqual(key, manager._cache_key(record, "same task", TaskContract()))


@unittest.skipUnless(sys.platform.startswith("linux"), "requires sandbox")
class MetricSafetyTests(unittest.TestCase):
    def test_transform_cannot_silently_discard_valid_refunds(self):
        source = "def transform_refunds(order_totals_cents, refunds):\n    return []\n"
        manager = PluginManager()
        record = install(manager, source, "1")
        with self.assertRaises(PluginExecutionError):
            manager.apply("Apply cumulative refund cap", ORDERS, REFUNDS)
        self.assertEqual(record.status, "quarantined")

    def test_cap_alone_can_preserve_repeated_ids_without_implicit_dedup(self):
        manager = PluginManager()
        record = install(manager, GOOD_SOURCE, "1")
        application = manager.apply("Apply cumulative refund cap", ORDERS, DUPLICATE_REFUNDS)
        self.assertEqual(len(application.refunds), 3)
        self.assertEqual(record.status, "active")

    def test_metric_cannot_claim_a_transform_requirement(self):
        payload = PluginSpec.metric_tool("refund_replay_rate").to_dict()
        payload["provides"] = ["refund_cap"]
        with self.assertRaises(ValueError):
            PluginSpec.from_dict(payload)

    def test_wrong_metric_is_quarantined_at_admission(self):
        source = "def compute_metric(orders, refunds):\n    return {'metric':'refund_replay_rate','data':{'total_refund_rows':999,'duplicate_rows':999,'replay_rate_bps':99999}}\n"
        record = install_metric(PluginManager(), source)
        self.assertEqual(record.status, "quarantined")

    def test_metric_verifier_rejects_wrong_types_and_inconsistent_values(self):
        definition = metric_definition("refund_replay_rate")
        for data in ({"total_refund_rows": False, "duplicate_rows": 0, "replay_rate_bps": 0},
                     {"total_refund_rows": 0, "duplicate_rows": 0, "replay_rate_bps": 1}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                definition.validate(data, [], [])

    def test_raw_metric_survives_dedup_and_cap_composition(self):
        manager = PluginManager()
        install(manager, GOOD_SOURCE, "1")
        manager.install(version="1", registration=dedup_registration(), capability=DEDUP_CAPABILITY,
                        source=DEDUP_SOURCE, spec=DEDUP_SPEC, activate=True)
        record = install_metric(manager)
        application = manager.apply("deduplicate refunds by refund_id, apply cumulative refund cap and calculate refund replay rate",
                                    ORDERS, DUPLICATE_REFUNDS)
        self.assertEqual(application.results["refund_replay_rate"].data["replay_rate_bps"], 3333)
        self.assertEqual(len(application.refunds), 2)
        self.assertEqual(record.admission["source_sha256"], record.registration.source_sha256)

    def test_unverified_metric_and_wrong_input_view_are_rejected(self):
        with self.assertRaises(ValueError):
            metric_definition("unreviewed_metric")
        record = PluginManager().install(version="1", registration=metric_registration(),
            capability=CapabilitySpec("replay", "description", ()), source=METRIC_SOURCE,
            spec=PluginSpec.metric_tool("refund_replay_rate", input_view="effective"), activate=True)
        self.assertEqual(record.status, "quarantined")

    def test_unknown_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            PluginSpec(kind="metric_tool", entrypoint="compute_metric", arguments=("orders", "refunds"),
                       input_schema="untrusted.v1", output_schema="untrusted.v1", output_name="metric")


class RegistryConcurrencyTests(unittest.TestCase):
    def test_stale_registry_writer_cannot_overwrite_new_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plugin.py").write_text(GOOD_SOURCE, encoding="utf-8")
            path = root / "registry.json"
            initial = PluginManager(manifest_path=path)
            install(initial, GOOD_SOURCE, "1")
            first, stale = PluginManager.from_manifest(path), PluginManager.from_manifest(path)
            install(first, GOOD_SOURCE, "2")
            with self.assertRaises(RegistryConflict):
                install(stale, GOOD_SOURCE, "3")
            self.assertEqual([record.version for record in stale.records if record.status == "active"], ["1"])
            read_only = stale.apply("Apply cumulative refund cap", ORDERS, REFUNDS)
            self.assertEqual(read_only.metadata["plugin_version"], "1")
            self.assertEqual([record.version for record in PluginManager.from_manifest(path).records if record.status == "active"], ["2"])
            self.assertFalse(list(root.glob(".registry.json.*")))


if __name__ == "__main__":
    unittest.main()

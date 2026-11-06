from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from contextlib import nullcontext
from pathlib import Path

from .code_plugin_registry import CodePluginRegistration
from .metric_catalog import metric_definition
from .persistence import atomic_json, file_lock
from .plugin_manager import PluginManager
from .plugin_protocol import PluginSpec
from .semantic_plugin_router import CapabilitySpec, JsonModel


class CapabilityEvolver:
    """Bounded online synthesis with a developer-owned definition and verifier.

    No caller dataset or candidate-generated expected output is used as an oracle.
    Generated capabilities remain limited to independently verifiable metric tools.
    """

    def __init__(self, model: JsonModel, artifact_root: Path, max_attempts: int = 2):
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self.model, self.artifact_root, self.max_attempts = model, artifact_root, max_attempts

    def ensure(self, manager: PluginManager, output_name: str, task: str) -> dict[str, object]:
        definition = metric_definition(output_name)  # unknown verifiers fail before any model call
        lock = file_lock(manager.manifest_path.with_name(manager.manifest_path.name + ".evolution")) if manager.manifest_path else nullcontext()
        with lock:
            if manager.manifest_path:
                manager.refresh()
            existing = next((record for record in manager.records
                             if record.status == "active" and record.spec.output_name == output_name), None)
            if existing:
                return {"reused": True, "plugin_id": existing.plugin_id}
            root = self.artifact_root / uuid.uuid4().hex
            root.mkdir(parents=True)
            previous, critic = "", ""
            spec = PluginSpec.metric_tool(output_name, input_view=definition.input_view)
            plugin_id = f"generated-{output_name.replace('_', '-')}-v1"
            capability = CapabilitySpec(plugin_id, definition.description,
                                        ("must not mutate inputs", "must not substitute another metric"))
            for attempt in range(1, self.max_attempts + 1):
                response = self.model.complete_json(
                    system=("Generate a metric plugin; return a JSON object with source and reason. "
                            "Define exactly compute_metric(orders, refunds). No imports, decorators, "
                            "annotations, nested functions, default arguments, private attributes, "
                            "comprehensions, lambda or aliases of methods. Never shadow builtins. "
                            "Allowed calls: dict, enumerate, int, len, list, max, min, range, sorted, str, sum. "
                            "Allowed direct methods: append, copy, get, items, keys, setdefault, values. "
                            "Return metric, data and evidence. All data fields are non-negative integers. "
                            "Do not invent a verifier or alter the trusted definition."),
                    user=json.dumps({"task": task, "output_name": output_name,
                                     "trusted_definition": definition.description,
                                     "fields": definition.fields, "input_view": definition.input_view,
                                     "input_schema": {"refunds": ["refund_id", "order_id", "refund_date", "refund_cents"],
                                                      "orders": ["order_id", "gross_cents", "quantity"]},
                                     "previous_source": previous or None, "critic": critic or None}, ensure_ascii=False),
                )
                source = str(response.get("source", "")).rstrip() + "\n"
                digest = hashlib.sha256(source.encode()).hexdigest()
                cues = tuple(alias for alias in definition.aliases if 4 <= len(alias) <= 80)[:12]
                registration = CodePluginRegistration(plugin_id, cues, digest, "candidate.py",
                                                      reason="independent-verifier gated online capability evolution")
                candidate = PluginManager()
                record = candidate.install(version="1", registration=registration, capability=capability,
                                           source=source, spec=spec, activate=True, persist=False)
                artifact = {"attempt": attempt, "source": source, "source_sha256": digest,
                            "status": record.status, "critic": record.quarantine_reason,
                            "admission": record.admission}
                atomic_json(root / f"attempt-{attempt}.json", artifact)
                if record.status != "active":
                    previous, critic = source, record.quarantine_reason
                    continue
                # A third seed is opened only after candidate code has been frozen.
                audit_cases = definition.cases(9817)
                try:
                    for order_rows, refund_rows in audit_cases:
                        candidate._run_verified_metric(record, order_rows, refund_rows)
                except (ValueError, RuntimeError) as exc:
                    atomic_json(root / "frozen-audit.json", {"accepted": False, "error": str(exc), "source_sha256": digest})
                    raise RuntimeError("frozen metric audit rejected the candidate; no activation") from exc
                atomic_json(root / "frozen-audit.json", {"accepted": True, "count": len(audit_cases), "source_sha256": digest})
                source_path = ""
                if manager.manifest_path:
                    directory = manager.manifest_path.parent / "generated"
                    directory.mkdir(parents=True, exist_ok=True)
                    target = directory / f"{output_name}-{digest}.py"
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as handle:
                        temporary = Path(handle.name)
                        handle.write(source)
                        handle.flush()
                        os.fsync(handle.fileno())
                    try:
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
                    source_path = str(target.relative_to(manager.manifest_path.parent))
                admitted = manager.install(version=digest[:12], registration=registration,
                                           capability=capability, source=source, spec=spec,
                                           source_path=source_path or "in-memory", activate=True)
                if admitted.status != "active":
                    raise RuntimeError(admitted.quarantine_reason)
                receipt = {"plugin_id": plugin_id, "source_sha256": digest, "attempts": attempt,
                           "admission": admitted.admission, "frozen_audit_cases": len(audit_cases),
                           "claim": "bounded verified metric synthesis; no runtime or model self-modification"}
                atomic_json(root / "receipt.json", receipt)
                return receipt
            raise RuntimeError(f"capability generation exhausted {self.max_attempts} attempts: {critic}")

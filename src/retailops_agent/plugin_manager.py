from __future__ import annotations

import hashlib
import json
import threading
import copy
from functools import wraps
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from .code_plugin_registry import CodePluginRegistration
from .models import OrderLine, Refund, ToolResult
from .metric_catalog import metric_definition
from .persistence import atomic_json, file_lock
from .plugin_protocol import PluginSpec
from .plugin_sandbox import (
    UnsafePluginError,
    run_json_plugin,
    run_refund_plugin,
    validate_plugin_source,
)
from .semantic_plugin_router import CapabilitySpec, JsonModel, route_items
from .task_contract import BUILTIN_RESULTS, TaskContract, infer_task_contract


PluginStatus = Literal["pending", "active", "quarantined", "retired"]


class PluginExecutionError(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, str]]):
        super().__init__(message)
        self.attempts = attempts


class RegistryConflict(PluginExecutionError):
    def __init__(self):
        super().__init__("registry changed in another process; reload before retrying", [])


def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            snapshot = copy.deepcopy(self._records)
            counter = self._installed_counter
            dirty = self._dirty
            try:
                return method(self, *args, **kwargs)
            except RegistryConflict:
                self._records = snapshot
                self._installed_counter = counter
                self._dirty = dirty
                raise
    return call


@dataclass
class ManagedPlugin:
    plugin_id: str
    version: str
    registration: CodePluginRegistration
    capability: CapabilitySpec
    spec: PluginSpec
    source: str
    source_path: str
    status: PluginStatus = "pending"
    installed_order: int = 0
    failure_count: int = 0
    quarantine_reason: str = ""
    admission: dict[str, object] = field(default_factory=dict)

    def identity(self) -> str:
        return f"{self.plugin_id}@{self.version}"


@dataclass(frozen=True)
class PluginApplication:
    refunds: list[Refund]
    matched: bool
    results: dict[str, ToolResult] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _to_cents(amount: Decimal) -> int:
    scaled = amount * 100
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise UnsafePluginError("plugin input contains sub-cent monetary values")
    return int(integral)


class PluginManager:
    """Versioned runtime for gated refund-transform plugins."""

    def __init__(
        self,
        *,
        semantic_model: JsonModel | None = None,
        cache_path: Path | None = None,
        manifest_path: Path | None = None,
    ):
        self.semantic_model = semantic_model
        self.cache_path = cache_path
        self.manifest_path = manifest_path
        self._lock = threading.RLock()
        self._manifest_revision: str | None = None
        self._dirty = False
        self._records: list[ManagedPlugin] = []
        self._installed_counter = 0
        self._route_cache: dict[str, dict[str, object]] = self._read_cache()
        self._cache_original = dict(self._route_cache)

    @property
    def records(self) -> tuple[ManagedPlugin, ...]:
        return tuple(self._records)

    @property
    def registry_version(self) -> str:
        payload = [
            {
                "plugin_id": record.plugin_id,
                "version": record.version,
                "status": record.status,
                "source_sha256": record.registration.source_sha256,
                "registration": record.registration.to_dict(),
                "spec": record.spec.to_dict(),
                "capability": {"definition": record.capability.positive_definition,
                               "exclusions": record.capability.exclusions},
            }
            for record in sorted(
                self._records, key=lambda item: (item.plugin_id, item.version)
            )
        ]
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def _read_cache(self) -> dict[str, dict[str, object]]:
        if not self.cache_path or not self.cache_path.is_file():
            return {}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            return {}
        return {
            str(key): value
            for key, value in entries.items()
            if isinstance(value, dict) and isinstance(value.get("use_plugin"), bool)
        }

    def _write_cache(self) -> None:
        if not self.cache_path:
            return
        updates = {key: value for key, value in self._route_cache.items()
                   if self._cache_original.get(key) != value}
        if not updates:
            return
        with file_lock(self.cache_path):
            merged = self._read_cache()
            merged.update(updates)
            merged = dict(list(merged.items())[-4096:])
            atomic_json(self.cache_path, {"schema_version": 2, "entries": merged})
            self._route_cache = merged
            self._cache_original = dict(merged)

    def _cache_key(self, record: ManagedPlugin, task: str, contract: TaskContract | None = None) -> str:
        material = json.dumps(
            {
                "registry_version": self.registry_version,
                "plugin": record.identity(),
                "task": _normalize(task),
                "contract": (contract or infer_task_contract(task)).to_dict(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return _sha256_text(material)

    @synchronized
    def prime_route_cache(
        self,
        task: str,
        selected_plugin_ids: tuple[str, ...],
        *,
        reason: str,
        contract: TaskContract | None = None,
    ) -> None:
        active = self._active()
        active_ids = {record.plugin_id for record in active}
        if len(selected_plugin_ids) != len(set(selected_plugin_ids)):
            raise ValueError("primed route contains duplicate plugin ids")
        if any(plugin_id not in active_ids for plugin_id in selected_plugin_ids):
            raise ValueError("primed route contains an unknown or inactive plugin id")
        selected = set(selected_plugin_ids)
        for record in active:
            self._route_cache[self._cache_key(record, task, contract)] = {
                "use_plugin": record.plugin_id in selected,
                "reason": reason[:300],
            }
        self._write_cache()

    @synchronized
    def install(
        self,
        *,
        version: str,
        registration: CodePluginRegistration,
        capability: CapabilitySpec,
        source: str,
        spec: PluginSpec | None = None,
        source_path: str = "",
        activate: bool = False,
        persist: bool = True,
    ) -> ManagedPlugin:
        if not version or len(version) > 40:
            raise ValueError("plugin version must contain 1-40 characters")
        if any(
            record.plugin_id == registration.plugin_id and record.version == version
            for record in self._records
        ):
            raise ValueError(f"duplicate plugin version: {registration.plugin_id}@{version}")
        self._installed_counter += 1
        self._dirty = True
        spec = spec or PluginSpec.refund_transform()
        record = ManagedPlugin(
            plugin_id=registration.plugin_id,
            version=version,
            registration=registration,
            capability=capability,
            spec=spec,
            source=source,
            source_path=source_path or registration.source_path,
            installed_order=self._installed_counter,
        )
        try:
            if _sha256_text(source) != registration.source_sha256:
                raise UnsafePluginError("plugin source digest does not match registration")
            validate_plugin_source(
                source,
                entrypoint=spec.entrypoint,
                argument_names=spec.arguments,
            )
            if spec.kind == "metric_tool":
                if spec.output_name in BUILTIN_RESULTS:
                    raise UnsafePluginError("metric plugin cannot overwrite builtin results")
                definition = metric_definition(spec.output_name)
                if definition.input_view != spec.input_view:
                    raise UnsafePluginError("metric input view differs from its trusted verifier")
                if activate:
                    self._admit_metric(record)
        except (UnsafePluginError, ValueError) as exc:
            record.status = "quarantined"
            record.failure_count = 1
            record.quarantine_reason = f"install validation failed: {exc}"
        self._records.append(record)
        if activate and record.status != "quarantined":
            self.activate(record, persist=False)
        if persist:
            self._persist_manifest()
        return record

    def _admit_metric(self, record: ManagedPlugin) -> None:
        definition = metric_definition(record.spec.output_name)
        seed_results = {}
        for label, seed in (("evolution", 4101), ("validation", 7109)):
            cases = definition.cases(seed)
            for order_rows, refund_rows in cases:
                self._run_verified_metric(record, order_rows, refund_rows)
            seed_results[label] = len(cases)
        record.admission = {
            "source_sha256": record.registration.source_sha256,
            "spec_sha256": _sha256_text(json.dumps(record.spec.to_dict(), sort_keys=True)),
            "verifier_sha256": definition.fingerprint,
            "gate_version": "metric-gate-v1", "passed": seed_results,
        }

    @synchronized
    def activate(self, record: ManagedPlugin, *, persist: bool = True) -> None:
        if record not in self._records:
            raise ValueError("plugin record is not managed by this registry")
        if record.status == "quarantined":
            raise ValueError("quarantined plugin cannot be activated")
        if record.spec.kind == "metric_tool" and not record.admission:
            self._admit_metric(record)
        for current in self._records:
            if current.plugin_id == record.plugin_id and current.status == "active":
                current.status = "retired"
        record.status = "active"
        self._dirty = True
        record.quarantine_reason = ""
        if persist:
            self._persist_manifest()

    def _active(self) -> list[ManagedPlugin]:
        return sorted(
            (record for record in self._records if record.status == "active"),
            key=lambda record: (record.plugin_id, -record.installed_order),
        )

    @synchronized
    def refresh(self) -> None:
        if self.manifest_path is None:
            return
        fresh = self.from_manifest(self.manifest_path, semantic_model=self.semantic_model,
                                   cache_path=self.cache_path)
        self._records = fresh._records
        self._installed_counter = fresh._installed_counter
        self._manifest_revision = fresh._manifest_revision
        self._route_cache = fresh._route_cache
        self._cache_original = fresh._cache_original
        self._dirty = False

    def _semantic_decision(
        self, record: ManagedPlugin, task: str, contract: TaskContract | None = None
    ) -> tuple[bool, str, bool]:
        key = self._cache_key(record, task, contract)
        cached = self._route_cache.get(key)
        if cached is not None:
            return bool(cached["use_plugin"]), str(cached.get("reason", "")), True
        if self.semantic_model is None:
            return False, "semantic fallback disabled", False
        decision = route_items(
            record.registration,
            self.semantic_model,
            record.capability,
            [("runtime-task", task)],
            batch_size=1,
        )[0]
        use_plugin = bool(decision["use_plugin"])
        reason = str(decision.get("reason", ""))
        self._route_cache[key] = {"use_plugin": use_plugin, "reason": reason}
        self._write_cache()
        return use_plugin, reason, False

    def _semantic_multi_decision(
        self, records: list[ManagedPlugin], task: str, contract: TaskContract | None = None
    ) -> tuple[list[ManagedPlugin], str, bool]:
        cached = [self._route_cache.get(self._cache_key(record, task, contract)) for record in records]
        if all(entry is not None for entry in cached):
            selected = [
                record
                for record, entry in zip(records, cached)
                if bool(entry and entry.get("use_plugin"))
            ]
            reason = str(
                next(
                    (
                        entry.get("reason", "")
                        for entry in cached
                        if entry and entry.get("use_plugin")
                    ),
                    "cached semantic rejection",
                )
            )
            return selected, reason, True
        if self.semantic_model is None:
            return [], "semantic fallback disabled", False
        response = self.semantic_model.complete_json(
            system=(
                "You are a conservative plugin router. Select every supplied plugin whose full "
                "capability is explicitly required by the task. Return an empty list if none fit. "
                "Related vocabulary is insufficient. Explicit negation of an essential condition "
                "and any explicit exclusion take priority over superficial similarity. For "
                "example, a task that says events do not share or accumulate state cannot match a "
                "cumulative-state plugin, and report-only detection cannot match a mutating plugin. "
                "Return a JSON object with plugin_ids and a short reason. plugin_ids must be a "
                "deduplicated array containing only supplied ids. Do not decide execution order."
            ),
            user=json.dumps(
                {
                    "task": task,
                    "task_contract": (contract or infer_task_contract(task)).to_dict(),
                    "plugins": [
                        {
                            "plugin_id": record.plugin_id,
                            "kind": record.spec.kind,
                            "output_name": record.spec.output_name,
                            "input_view": record.spec.input_view,
                            "provides": record.spec.provides,
                            "positive_definition": record.capability.positive_definition,
                            "explicit_exclusions": list(record.capability.exclusions),
                        }
                        for record in records
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        raw_selected = response.get("plugin_ids")
        if raw_selected is None and "plugin_id" in response:
            legacy = response.get("plugin_id")
            raw_selected = [] if legacy is None else [legacy]
        allowed = {record.plugin_id for record in records}
        if not isinstance(raw_selected, list) or any(
            not isinstance(value, str) or value not in allowed for value in raw_selected
        ):
            raise PluginExecutionError(
                "semantic router returned invalid plugin ids",
                [{"plugin": str(raw_selected), "error": "not in active registry"}],
            )
        if len(raw_selected) != len(set(raw_selected)):
            raise PluginExecutionError(
                "semantic router returned duplicate plugin ids",
                [{"plugin": str(raw_selected), "error": "duplicate selection"}],
            )
        selected_ids = set(raw_selected)
        reason = str(response.get("reason", ""))[:300]
        selected_records: list[ManagedPlugin] = []
        for record in records:
            use_plugin = record.plugin_id in selected_ids
            self._route_cache[self._cache_key(record, task, contract)] = {
                "use_plugin": use_plugin,
                "reason": reason,
            }
            if use_plugin:
                selected_records.append(record)
        self._write_cache()
        return selected_records, reason, False

    def _sort_pipeline(self, records: list[ManagedPlugin]) -> list[ManagedPlugin]:
        by_id = {record.plugin_id: record for record in records}
        if len(by_id) != len(records):
            raise PluginExecutionError(
                "pipeline contains duplicate plugin ids",
                [{"plugin": record.identity(), "error": "duplicate pipeline node"} for record in records],
            )
        for record in records:
            conflict = next(
                (
                    other_id
                    for other_id in record.spec.conflicts
                    if other_id in by_id
                ),
                None,
            )
            if conflict:
                raise PluginExecutionError(
                    "selected plugins conflict",
                    [{"plugin": record.identity(), "error": f"conflicts with {conflict}"}],
                )
        edges: dict[str, set[str]] = {plugin_id: set() for plugin_id in by_id}
        indegree: dict[str, int] = {plugin_id: 0 for plugin_id in by_id}

        def add_edge(before: str, after: str) -> None:
            if before == after or before not in by_id or after not in by_id:
                return
            if after not in edges[before]:
                edges[before].add(after)
                indegree[after] += 1

        for record in records:
            for target in record.spec.order_before:
                add_edge(record.plugin_id, target)
            for source in record.spec.order_after:
                add_edge(source, record.plugin_id)
        transforms = [record for record in records if record.spec.kind == "refund_transform"]
        metrics = [record for record in records if record.spec.kind == "metric_tool"]
        for transform in transforms:
            for metric in metrics:
                if metric.spec.input_view == "effective":
                    add_edge(transform.plugin_id, metric.plugin_id)

        ready = sorted(
            (by_id[plugin_id] for plugin_id, degree in indegree.items() if degree == 0),
            key=lambda record: (record.installed_order, record.plugin_id),
        )
        ordered: list[ManagedPlugin] = []
        while ready:
            record = ready.pop(0)
            ordered.append(record)
            for target in sorted(edges[record.plugin_id]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(by_id[target])
                    ready.sort(key=lambda item: (item.installed_order, item.plugin_id))
        if len(ordered) != len(records):
            raise PluginExecutionError(
                "plugin ordering constraints contain a cycle",
                [{"plugin": record.identity(), "error": "cyclic ordering"} for record in records],
            )
        return ordered

    @synchronized
    def resolve_pipeline(
        self, task: str, *, required_results: tuple[str, ...] = (),
        contract: TaskContract | None = None,
    ) -> tuple[list[ManagedPlugin], dict[str, object]]:
        contract = (contract or infer_task_contract(task)).with_results(required_results)
        required_results = contract.required_results
        active = [record for record in self._active()
                  if not set(record.spec.provides) & set(contract.forbidden_capabilities)]
        deterministic = [record for record in active if record.registration.matches(task)]
        if deterministic:
            selected = deterministic
            reason = "matched frozen literal/keyword registrations"
            cache_hit = False
            source = "deterministic"
            covered_results = {
                record.spec.output_name
                for record in deterministic
                if record.spec.kind == "metric_tool"
            }
            if (
                set(required_results) - BUILTIN_RESULTS - covered_results
                and len(active) > 1
            ):
                selected, reason, cache_hit = self._semantic_multi_decision(active, task, contract)
                source = (
                    "coverage_semantic_cache" if cache_hit else "coverage_semantic"
                )
        elif len(active) > 1:
            selected, reason, cache_hit = self._semantic_multi_decision(active, task, contract)
            source = "semantic_cache" if cache_hit else "semantic"
        else:
            selected = []
            reason = "no active plugin matched"
            cache_hit = False
            if active:
                use_plugin, reason, cache_hit = self._semantic_decision(active[0], task, contract)
                if use_plugin:
                    selected = [active[0]]
            source = "semantic_cache" if cache_hit else "semantic"
        selected = [record for record in selected
                    if not set(record.spec.provides) & set(contract.forbidden_capabilities)]
        if contract.required_results and not contract.required_capabilities:
            selected = [record for record in selected if record.spec.kind != "refund_transform"]
        # Explicit requirements are authoritative even after a partial semantic selection.
        for capability in contract.required_capabilities:
            if not any(capability in record.spec.provides for record in selected):
                candidates = [record for record in active if capability in record.spec.provides]
                if len(candidates) == 1:
                    selected.append(candidates[0])
        for result in set(required_results) - BUILTIN_RESULTS:
            if not any(record.spec.output_name == result for record in selected):
                candidates = [record for record in active if record.spec.output_name == result]
                if len(candidates) == 1:
                    selected.append(candidates[0])
        provided = {value for record in selected for value in record.spec.provides}
        missing_capabilities = sorted(set(contract.required_capabilities) - provided)
        if missing_capabilities:
            raise PluginExecutionError("required transform capability is unavailable", [
                {"plugin": value, "error": "unsupported_capability"} for value in missing_capabilities])
        ordered = self._sort_pipeline(selected) if selected else []
        return ordered, {
            "route_source": source if ordered else "no_match",
            "cache_hit": cache_hit,
            "route_reason": reason,
            "registry_version": self.registry_version,
            "selected_plugin_ids": [record.plugin_id for record in ordered],
            "task_contract": contract.to_dict(),
        }

    def resolve(self, task: str) -> tuple[ManagedPlugin | None, dict[str, object]]:
        records, route = self.resolve_pipeline(task)
        if len(records) > 1:
            raise PluginExecutionError(
                "task resolves to a plugin pipeline; use resolve_pipeline",
                [{"plugin": record.identity(), "error": "multi-plugin pipeline"} for record in records],
            )
        return (records[0] if records else None), route

    def _execute_refund_transform(
        self, record: ManagedPlugin, orders: list[OrderLine], refunds: list[Refund]
    ) -> list[Refund]:
        order_totals: dict[str, int] = {}
        for line in orders:
            order_totals[line.order_id] = order_totals.get(line.order_id, 0) + _to_cents(
                line.gross_amount
            )
        input_rows = [
            {
                "refund_id": refund.refund_id,
                "order_id": refund.order_id,
                "refund_date": refund.refund_date,
                "refund_cents": _to_cents(refund.refund_amount),
            }
            for refund in refunds
        ]
        output_rows = run_refund_plugin(
            record.source,
            order_totals_cents=order_totals,
            refunds=input_rows,
            timeout=record.spec.timeout_seconds,
        )
        # Independently check the supported transformation semantics, not just upper bounds.
        expected_rows = list(input_rows)
        if "refund_dedup" in record.spec.provides:
            unique_rows = {}
            for item in expected_rows:
                unique_rows.setdefault(item["refund_id"], item)
            expected_rows = list(unique_rows.values())
        if "refund_cap" in record.spec.provides:
            remaining = dict(order_totals)
            capped_rows = []
            for item in expected_rows:
                order_id = str(item["order_id"])
                accepted = min(int(item["refund_cents"]), remaining.get(order_id, 0))
                if accepted > 0:
                    capped_rows.append({**item, "refund_cents": accepted})
                    remaining[order_id] -= accepted
            expected_rows = capped_rows
        if record.spec.provides and output_rows != expected_rows:
            raise UnsafePluginError("transform failed independent business-semantic verification")
        original: dict[str, list[tuple[int, dict[str, object]]]] = {}
        for index, row in enumerate(input_rows):
            original.setdefault(str(row["refund_id"]), []).append((index, row))
        seen: set[str] = set()
        last_index = -1
        cumulative: dict[str, int] = {}
        transformed: list[Refund] = []
        for row in output_rows:
            refund_id = str(row["refund_id"])
            if (refund_id in seen and "refund_dedup" in record.spec.provides) or refund_id not in original:
                raise UnsafePluginError("plugin duplicated or invented a refund id")
            matching = [
                (index, source)
                for index, source in original[refund_id]
                if index > last_index
                and str(row["order_id"]) == str(source["order_id"])
                and str(row["refund_date"]) == str(source["refund_date"])
                and int(row["refund_cents"]) <= int(source["refund_cents"])
            ]
            if not matching:
                raise UnsafePluginError(
                    "plugin changed refund order, identity fields, or amount"
                )
            index, source = matching[0]
            last_index = index
            seen.add(refund_id)
            cents = int(row["refund_cents"])
            order_id = str(row["order_id"])
            cumulative[order_id] = cumulative.get(order_id, 0) + cents
            if (
                "order_gross_cap" in record.spec.invariants
                and cumulative[order_id] > order_totals.get(order_id, 0)
            ):
                raise UnsafePluginError("plugin exceeded the order gross refund cap")
            transformed.append(
                Refund(
                    refund_id=refund_id,
                    order_id=order_id,
                    refund_date=str(row["refund_date"]),
                    refund_amount=Decimal(cents) / Decimal(100),
                )
            )
        return transformed

    def _execute_metric_tool(
        self, record: ManagedPlugin, orders: list[OrderLine], refunds: list[Refund]
    ) -> ToolResult:
        order_rows = [
            {
                "order_id": line.order_id,
                "order_date": line.order_date,
                "category": line.category,
                "product": line.product,
                "quantity": line.quantity,
                "unit_price_cents": _to_cents(line.unit_price),
                "gross_cents": _to_cents(line.gross_amount),
            }
            for line in orders
        ]
        refund_rows = [
            {
                "refund_id": refund.refund_id,
                "order_id": refund.order_id,
                "refund_date": refund.refund_date,
                "refund_cents": _to_cents(refund.refund_amount),
            }
            for refund in refunds
        ]
        return self._run_verified_metric(record, order_rows, refund_rows)

    def _run_verified_metric(self, record: ManagedPlugin, order_rows, refund_rows) -> ToolResult:
        output = run_json_plugin(
            record.source,
            entrypoint=record.spec.entrypoint,
            argument_names=record.spec.arguments,
            arguments={"orders": order_rows, "refunds": refund_rows},
            timeout=record.spec.timeout_seconds,
        )
        if not isinstance(output, dict):
            raise UnsafePluginError("metric plugin must return an object")
        if output.get("metric") != record.spec.output_name:
            raise UnsafePluginError("metric plugin returned the wrong metric name")
        if "data" not in output:
            raise UnsafePluginError("metric plugin result is missing data")
        metric_definition(record.spec.output_name).validate(output["data"], order_rows, refund_rows)
        evidence = output.get("evidence", {})
        if not isinstance(evidence, dict):
            raise UnsafePluginError("metric plugin evidence must be an object")
        return ToolResult(
            tool=record.spec.output_name,
            data=output["data"],
            evidence={str(key): value for key, value in evidence.items()},
        )

    def _quarantine(self, record: ManagedPlugin, reason: str) -> None:
        self._dirty = True
        record.status = "quarantined"
        record.failure_count += 1
        record.quarantine_reason = reason[:500]

    def _rollback_candidate(self, plugin_id: str) -> ManagedPlugin | None:
        candidates = [
            record
            for record in self._records
            if record.plugin_id == plugin_id and record.status == "retired"
        ]
        return max(candidates, key=lambda record: record.installed_order, default=None)

    @synchronized
    def apply(
        self,
        task: str,
        orders: list[OrderLine],
        refunds: list[Refund],
        *,
        required_results: tuple[str, ...] = (),
        contract: TaskContract | None = None,
    ) -> PluginApplication:
        records, route = self.resolve_pipeline(
            task, required_results=required_results, contract=contract
        )
        if not records:
            return PluginApplication(
                refunds=list(refunds), matched=False, results={}, metadata=route
            )
        attempts: list[dict[str, str]] = []
        initial_versions = {record.plugin_id: record.version for record in records}
        rollbacks: list[dict[str, str]] = []
        maximum_restarts = len(self._records) + 1
        for _ in range(maximum_restarts):
            effective_refunds = list(refunds)
            plugin_results: dict[str, ToolResult] = {}
            steps: list[dict[str, object]] = []
            failed = False
            for record in records:
                try:
                    if record.spec.kind == "refund_transform":
                        before = len(effective_refunds)
                        effective_refunds = self._execute_refund_transform(
                            record, orders, effective_refunds
                        )
                        steps.append(
                            {
                                "plugin_id": record.plugin_id,
                                "version": record.version,
                                "kind": record.spec.kind,
                                "input_rows": before,
                                "output_rows": len(effective_refunds),
                            }
                        )
                    elif record.spec.kind == "metric_tool":
                        result = self._execute_metric_tool(
                            record, orders, refunds if record.spec.input_view == "raw" else effective_refunds
                        )
                        if result.tool in plugin_results:
                            raise UnsafePluginError("pipeline produced a duplicate metric")
                        plugin_results[result.tool] = result
                        steps.append(
                            {
                                "plugin_id": record.plugin_id,
                                "version": record.version,
                                "kind": record.spec.kind,
                                "output_name": result.tool,
                                "input_view": record.spec.input_view,
                            }
                        )
                    else:  # pragma: no cover - PluginSpec rejects unknown kinds
                        raise UnsafePluginError(f"unsupported plugin kind: {record.spec.kind}")
                except (UnsafePluginError, ValueError) as exc:
                    attempts.append({"plugin": record.identity(), "error": str(exc)})
                    self._quarantine(record, f"runtime validation failed: {exc}")
                    fallback = self._rollback_candidate(record.plugin_id)
                    if fallback is None:
                        self._persist_manifest()
                        raise PluginExecutionError(
                            "plugin pipeline failed and no stable version was available",
                            attempts,
                        ) from exc
                    self.activate(fallback, persist=False)
                    rollbacks.append(
                        {
                            "plugin_id": record.plugin_id,
                            "from": record.version,
                            "to": fallback.version,
                        }
                    )
                    records = [
                        fallback if item.plugin_id == record.plugin_id else item
                        for item in records
                    ]
                    records = self._sort_pipeline(records)
                    failed = True
                    break
            if failed:
                continue
            pipeline = [
                {
                    "plugin_id": record.plugin_id,
                    "version": record.version,
                    "kind": record.spec.kind,
                    "sha256": record.registration.source_sha256,
                }
                for record in records
            ]
            metadata: dict[str, object] = {
                **route,
                "pipeline": pipeline,
                "steps": steps,
                "original_refunds": len(refunds),
                "effective_refunds": len(effective_refunds),
                "rollbacks": rollbacks,
                "status": "rolled_back" if rollbacks else "ok",
                "registry_version": self.registry_version,
            }
            if len(records) == 1:
                record = records[0]
                metadata.update(
                    {
                        "plugin_id": record.plugin_id,
                        "plugin_version": record.version,
                        "plugin_sha256": record.registration.source_sha256,
                        "rollback_from": (
                            initial_versions[record.plugin_id]
                            if record.version != initial_versions[record.plugin_id]
                            else None
                        ),
                    }
                )
            self._persist_manifest()
            return PluginApplication(
                refunds=effective_refunds,
                matched=True,
                results=plugin_results,
                metadata=metadata,
            )
        self._persist_manifest()
        raise PluginExecutionError("plugin pipeline exceeded rollback budget", attempts)

    def _persist_manifest(self) -> None:
        if not self.manifest_path or not self._dirty:
            return
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 2,
            "plugins": [
                {
                    "plugin_id": record.plugin_id,
                    "version": record.version,
                    "status": record.status,
                    "source_path": record.source_path,
                    "registration": record.registration.to_dict(),
                    "capability": {
                        "capability_id": record.capability.capability_id,
                        "positive_definition": record.capability.positive_definition,
                        "exclusions": list(record.capability.exclusions),
                    },
                    "spec": record.spec.to_dict(),
                    "failure_count": record.failure_count,
                    "quarantine_reason": record.quarantine_reason,
                    "installed_order": record.installed_order,
                    "admission": record.admission,
                }
                for record in self._records
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with file_lock(self.manifest_path):
            current = self.manifest_path.read_bytes() if self.manifest_path.exists() else None
            revision = hashlib.sha256(current).hexdigest() if current is not None else None
            if current is not None and current.decode("utf-8") == encoded:
                self._manifest_revision = revision
                self._dirty = False
                return
            if revision != self._manifest_revision:
                raise RegistryConflict()
            atomic_json(self.manifest_path, payload)
            self._manifest_revision = hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()
            self._dirty = False

    @classmethod
    def from_manifest(
        cls,
        path: Path,
        *,
        semantic_model: JsonModel | None = None,
        cache_path: Path | None = None,
    ) -> "PluginManager":
        raw_content = path.read_bytes()
        payload = json.loads(raw_content.decode("utf-8"))
        raw_plugins = payload.get("plugins")
        if not isinstance(raw_plugins, list):
            raise ValueError("plugin manifest must contain a plugins list")
        manager = cls(
            semantic_model=semantic_model,
            cache_path=cache_path,
            manifest_path=path,
        )
        manager._manifest_revision = hashlib.sha256(raw_content).hexdigest()
        for raw in raw_plugins:
            if not isinstance(raw, dict):
                raise ValueError("plugin manifest entries must be objects")
            registration_raw = raw.get("registration")
            capability_raw = raw.get("capability")
            if not isinstance(registration_raw, dict) or not isinstance(capability_raw, dict):
                raise ValueError("plugin manifest entry is missing registration or capability")
            source_path = Path(str(raw.get("source_path", "")))
            if not source_path.is_absolute():
                source_path = path.parent / source_path
            source = source_path.read_text(encoding="utf-8")
            registration = CodePluginRegistration.from_dict(registration_raw)
            capability = CapabilitySpec(
                capability_id=str(capability_raw.get("capability_id", "")),
                positive_definition=str(capability_raw.get("positive_definition", "")),
                exclusions=tuple(str(item) for item in capability_raw.get("exclusions", [])),
            )
            spec_raw = raw.get("spec")
            spec = (
                PluginSpec.from_dict(spec_raw)
                if isinstance(spec_raw, dict)
                else PluginSpec.refund_transform()
            )
            record = manager.install(
                version=str(raw.get("version", "")),
                registration=registration,
                capability=capability,
                spec=spec,
                source=source,
                source_path=str(raw.get("source_path", "")),
                activate=False,
                persist=False,
            )
            requested_status = str(raw.get("status", "pending"))
            if record.status == "quarantined" and requested_status != "quarantined":
                raise ValueError(
                    f"manifest plugin {record.identity()} failed source validation: "
                    f"{record.quarantine_reason}"
                )
            if requested_status not in {"pending", "active", "quarantined", "retired"}:
                raise ValueError(f"invalid plugin status: {requested_status}")
            record.status = requested_status  # type: ignore[assignment]
            if requested_status == "active" and record.spec.kind == "metric_tool":
                manager._admit_metric(record)
            record.failure_count = int(raw.get("failure_count", record.failure_count))
            record.quarantine_reason = str(raw.get("quarantine_reason", ""))
            record.installed_order = int(raw.get("installed_order", record.installed_order))
            manager._installed_counter = max(manager._installed_counter, record.installed_order)
        active_ids = [record.plugin_id for record in manager._records if record.status == "active"]
        if len(active_ids) != len(set(active_ids)):
            raise ValueError("manifest contains multiple active versions of one plugin")
        manager._dirty = False
        return manager

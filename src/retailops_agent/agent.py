from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .contracts import normalize_metric_contract
from .data import DataLoadError, load_orders, load_refunds
from .models import AnalysisPlan, ExecutionPolicy, RunTrace, ValidationIssue
from .planner import ModelPlanner, RulePlanner
from .plugin_manager import PluginExecutionError, PluginManager
from .repair import PolicyRepairer
from .task_contract import BUILTIN_RESULTS, infer_task_contract
from .tools import TOOL_REGISTRY, execute_tool
from .validators import validate_results


class RetailOpsAgent:
    def __init__(self, planner: RulePlanner | ModelPlanner | None = None, *,
                 initial_policy: ExecutionPolicy | None = None,
                 repairer: PolicyRepairer | None = None,
                 plugin_manager: PluginManager | None = None, evolver=None):
        self.planner = planner or RulePlanner()
        self.initial_policy = initial_policy or ExecutionPolicy()
        self.repairer = repairer or PolicyRepairer()
        self.plugin_manager = plugin_manager
        self.evolver = evolver

    def run(self, task: str, orders_path: Path, refunds_path: Path, *,
            metric_contract: Mapping[str, object] | None = None,
            required_results: tuple[str, ...] = (),
            on_event: Callable[[dict[str, object]], None] | None = None) -> RunTrace:
        started = time.perf_counter()
        trace = RunTrace(task=task, plan=AnalysisPlan(task, ()))

        def emit(event):
            item = {**event, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
            trace.events.append(item)
            if on_event:
                on_event(item)

        try:
            try:
                plan = self.planner.plan(task)
                contract = (plan.contract or infer_task_contract(task)).with_results(required_results)
                business_contract = normalize_metric_contract(metric_contract)
            except (ValueError, RuntimeError) as exc:
                trace.issues = [ValidationIssue("planning_failed", str(exc))]
                emit({"stage": "plan", "status": "failed"})
                return trace
            trace.plan = plan
            trace.task_contract = contract.to_dict()
            emit({"stage": "plan", "status": "ok", "contract": contract.to_dict()})
            try:
                orders, refunds = load_orders(orders_path), load_refunds(refunds_path)
            except DataLoadError as exc:
                trace.issues = exc.issues
                emit({"stage": "load", "status": "failed"})
                return trace
            except (OSError, UnicodeError) as exc:
                trace.issues = [ValidationIssue("data_read_failed", str(exc))]
                return trace
            emit({"stage": "load", "status": "ok", "orders": len(orders), "refunds": len(refunds)})

            manager = self.plugin_manager
            available = {record.spec.output_name for record in manager.records
                         if record.status == "active"} if manager else set()
            missing = set(contract.required_results) - BUILTIN_RESULTS - available
            if self.evolver and manager and missing:
                if len(missing) > 2:
                    trace.issues = [ValidationIssue("evolution_budget_exceeded", "at most two new metrics per task")]
                    return trace
                for name in sorted(missing):
                    emit({"stage": "evolve", "status": "started", "output_name": name})
                    try:
                        receipt = self.evolver.ensure(manager, name, task)
                        emit({"stage": "evolve", "status": "adopted", "receipt": receipt})
                    except (ValueError, RuntimeError) as exc:
                        emit({"stage": "evolve", "status": "rejected", "error": str(exc)})

            effective_refunds, plugin_results = refunds, {}
            if manager:
                try:
                    application = manager.apply(task, orders, refunds, contract=contract)
                except PluginExecutionError as exc:
                    code = "unsupported_capability" if any(
                        row.get("error") == "unsupported_capability" for row in exc.attempts
                    ) else "plugin_execution_failed"
                    trace.issues = [ValidationIssue(code, str(exc))]
                    emit({"stage": "plugin", "status": "failed", "attempts": exc.attempts})
                    return trace
                except (ValueError, RuntimeError) as exc:
                    trace.issues = [ValidationIssue("plugin_execution_failed", str(exc))]
                    emit({"stage": "plugin", "status": "failed"})
                    return trace
                trace.plugin = application.metadata
                effective_refunds, plugin_results = application.refunds, application.results
                emit({"stage": "plugin", "status": application.metadata.get("status", "not_applied"),
                      "matched": application.matched, **application.metadata})
            elif contract.required_capabilities:
                trace.issues = [ValidationIssue("unsupported_capability", "task requires a plugin registry")]
                return trace

            tools = tuple(dict.fromkeys((*plan.tools,
                         *(name for name in contract.required_results if name in BUILTIN_RESULTS))))
            if not (tools or contract.required_results or contract.required_capabilities
                    or (trace.plugin and trace.plugin.get("selected_plugin_ids"))):
                trace.issues = [ValidationIssue("unsupported_task", "task has no recognized executable output contract")]
                return trace

            policy = self.initial_policy
            for attempt in range(plan.max_repairs + 1):
                trace.attempts = attempt + 1
                trace.policies.append(policy)
                trace.results = {name: execute_tool(name, orders, effective_refunds, policy)
                                 for name in tools if name in TOOL_REGISTRY}
                trace.results.update(plugin_results)
                trace.issues = validate_results(orders, effective_refunds, trace.results, business_contract)
                trace.issues.extend(ValidationIssue(
                    "unsupported_capability", f"任务要求的结果 {name} 当前没有可执行工具或插件", False, name
                ) for name in contract.required_results if name not in trace.results)
                emit({"stage": "validate", "attempt": attempt + 1,
                      "status": "ok" if not trace.issues else "failed",
                      "issues": [issue.code for issue in trace.issues], "policy": policy.to_dict(),
                      "metric_contract": business_contract})
                if not trace.issues or not any(issue.repairable for issue in trace.issues):
                    break
                repaired, changes = self.repairer.repair(policy, trace.issues)
                emit({"stage": "repair", "attempt": attempt + 1, "changes": changes})
                if repaired == policy:
                    break
                policy = repaired
            return trace
        except Exception as exc:
            trace.issues = [ValidationIssue("execution_failed", f"{type(exc).__name__}: {exc}")]
            return trace
        finally:
            trace.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            emit({"stage": "complete", "status": "ok" if trace.ok else "failed"})


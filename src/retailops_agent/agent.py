from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .contracts import normalize_metric_contract
from .data import DataLoadError, load_orders, load_refunds
from .models import AnalysisPlan, ExecutionPolicy, RunTrace, ValidationIssue
from .planner import ModelPlanner, RulePlanner
from .plugin_manager import PluginExecutionError, PluginManager
from .repair import PolicyRepairer
from .tools import TOOL_REGISTRY, execute_tool
from .validators import validate_results


class RetailOpsAgent:
    def __init__(
        self,
        planner: RulePlanner | ModelPlanner | None = None,
        *,
        initial_policy: ExecutionPolicy | None = None,
        repairer: PolicyRepairer | None = None,
        plugin_manager: PluginManager | None = None,
    ):
        self.planner = planner or RulePlanner()
        self.initial_policy = initial_policy or ExecutionPolicy()
        self.repairer = repairer or PolicyRepairer()
        self.plugin_manager = plugin_manager

    def run(
        self,
        task: str,
        orders_path: Path,
        refunds_path: Path,
        *,
        metric_contract: Mapping[str, object] | None = None,
        required_results: tuple[str, ...] = (),
    ) -> RunTrace:
        plan = self.planner.plan(task)
        trace = RunTrace(task=task, plan=plan)
        contract = normalize_metric_contract(metric_contract)
        try:
            orders = load_orders(orders_path)
            refunds = load_refunds(refunds_path)
        except DataLoadError as exc:
            trace.issues = exc.issues
            trace.events.append({"stage": "load", "status": "failed"})
            return trace

        trace.events.append(
            {
                "stage": "load",
                "status": "ok",
                "orders": len(orders),
                "refunds": len(refunds),
            }
        )
        effective_refunds = refunds
        plugin_results = {}
        if self.plugin_manager is not None:
            try:
                application = self.plugin_manager.apply(
                    task,
                    orders,
                    refunds,
                    required_results=required_results,
                )
            except PluginExecutionError as exc:
                trace.issues = [
                    ValidationIssue(
                        "plugin_execution_failed",
                        f"匹配的代码插件执行失败，已停止分析: {exc}",
                    )
                ]
                trace.events.append(
                    {
                        "stage": "plugin",
                        "status": "failed",
                        "attempts": exc.attempts,
                        "registry_version": self.plugin_manager.registry_version,
                    }
                )
                return trace
            trace.plugin = application.metadata
            effective_refunds = application.refunds
            plugin_results = application.results
            trace.events.append(
                {
                    "stage": "plugin",
                    "status": application.metadata.get("status", "not_applied"),
                    "matched": application.matched,
                    **application.metadata,
                }
            )
        policy = self.initial_policy
        for attempt in range(plan.max_repairs + 1):
            trace.attempts = attempt + 1
            trace.policies.append(policy)
            trace.results = {
                name: execute_tool(name, orders, effective_refunds, policy)
                for name in plan.tools
                if name in TOOL_REGISTRY
            }
            trace.results.update(plugin_results)
            trace.issues = validate_results(
                orders, effective_refunds, trace.results, contract
            )
            missing_results = [
                name for name in required_results if name not in trace.results
            ]
            trace.issues.extend(
                ValidationIssue(
                    "unsupported_capability",
                    f"任务要求的结果 {name} 当前没有可执行工具或插件",
                    False,
                    name,
                )
                for name in missing_results
            )
            trace.events.append(
                {
                    "stage": "validate",
                    "attempt": attempt + 1,
                    "status": "ok" if not trace.issues else "failed",
                    "issues": [issue.code for issue in trace.issues],
                    "policy": policy.to_dict(),
                    "metric_contract": contract,
                }
            )
            if not trace.issues or not any(issue.repairable for issue in trace.issues):
                break
            repaired, changes = self.repairer.repair(policy, trace.issues)
            trace.events.append(
                {
                    "stage": "repair",
                    "attempt": attempt + 1,
                    "changes": changes,
                }
            )
            if repaired == policy:
                break
            policy = repaired
        return trace


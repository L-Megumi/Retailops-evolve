from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from retailops_agent.agent import RetailOpsAgent
from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.data import load_orders, load_refunds
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.plugin_manager import ManagedPlugin, PluginManager
from retailops_agent.plugin_protocol import PluginSpec
from retailops_agent.semantic_plugin_router import CapabilitySpec


def _task_paths(suite: Path, split: str) -> list[Path]:
    return sorted((suite / "tasks" / split).glob("*/task.json"))


def _load_task(task_path: Path) -> dict[str, object]:
    return json.loads(task_path.read_text(encoding="utf-8"))


def _expected(suite: Path, split: str, case_id: str) -> dict[str, object]:
    payload = json.loads(
        (suite / "oracle" / split / f"{case_id}.json").read_text(encoding="utf-8")
    )
    return dict(payload["expected"])


def _baseline_score(manager: PluginManager, suite: Path, split: str) -> dict[str, object]:
    agent = RetailOpsAgent(plugin_manager=manager)
    rows = []
    for task_path in _task_paths(suite, split):
        task = _load_task(task_path)
        trace = agent.run(
            str(task["instruction"]),
            task_path.parent / "orders.csv",
            task_path.parent / "refunds.csv",
            required_results=tuple(str(value) for value in task["required_results"]),
        )
        rows.append(
            {
                "case_id": task_path.parent.name,
                "ok": trace.ok,
                "issues": [issue.code for issue in trace.issues],
            }
        )
    return {
        "passed": sum(bool(row["ok"]) for row in rows),
        "count": len(rows),
        "rows": rows,
    }


def _direct_score(
    manager: PluginManager,
    record: ManagedPlugin,
    suite: Path,
    split: str,
) -> dict[str, object]:
    rows = []
    for task_path in _task_paths(suite, split):
        case_id = task_path.parent.name
        expected = _expected(suite, split, case_id)
        error = ""
        actual = None
        try:
            result = manager._execute_metric_tool(  # experiment-level protocol gate
                record,
                load_orders(task_path.parent / "orders.csv"),
                load_refunds(task_path.parent / "refunds.csv"),
            )
            actual = result.data
            ok = actual == expected
            if not ok:
                error = f"expected {expected}, got {actual}"
        except (ValueError, RuntimeError) as exc:
            ok = False
            error = str(exc)
        rows.append({"case_id": case_id, "ok": ok, "error": error})
    return {
        "passed": sum(bool(row["ok"]) for row in rows),
        "count": len(rows),
        "rows": rows,
    }


def _agent_score(manager: PluginManager, suite: Path, split: str) -> dict[str, object]:
    agent = RetailOpsAgent(plugin_manager=manager)
    rows = []
    for task_path in _task_paths(suite, split):
        task = _load_task(task_path)
        case_id = task_path.parent.name
        expected = _expected(suite, split, case_id)
        trace = agent.run(
            str(task["instruction"]),
            task_path.parent / "orders.csv",
            task_path.parent / "refunds.csv",
            required_results=tuple(str(value) for value in task["required_results"]),
        )
        result = trace.results.get("refund_replay_rate")
        actual = result.data if result else None
        rows.append(
            {
                "case_id": case_id,
                "ok": trace.ok and actual == expected,
                "actual": actual,
                "issues": [issue.code for issue in trace.issues],
                "route_source": (trace.plugin or {}).get("route_source"),
                "pipeline": (trace.plugin or {}).get("selected_plugin_ids", []),
            }
        )
    return {
        "passed": sum(bool(row["ok"]) for row in rows),
        "count": len(rows),
        "rows": rows,
    }


def _candidate_record(
    source: str,
    registration: CodePluginRegistration,
    capability: CapabilitySpec,
) -> tuple[PluginManager, ManagedPlugin]:
    manager = PluginManager()
    record = manager.install(
        version="1",
        registration=registration,
        capability=capability,
        source=source,
        spec=PluginSpec.metric_tool("refund_replay_rate"),
        activate=True,
    )
    return manager, record


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Attempt bounded capability RSI from a missing-result failure trace."
    )
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    baseline_manager = PluginManager.from_manifest(args.manifest)
    baseline_manager.manifest_path = None
    baseline = {
        split: _baseline_score(baseline_manager, args.suite, split)
        for split in ("evolution", "validation", "audit")
    }
    first_task_path = _task_paths(args.suite, "evolution")[0]
    first_task = _load_task(first_task_path)
    initial_trace = RetailOpsAgent(plugin_manager=baseline_manager).run(
        str(first_task["instruction"]),
        first_task_path.parent / "orders.csv",
        first_task_path.parent / "refunds.csv",
        required_results=("refund_replay_rate",),
    )
    gap_issues = [
        {
            "code": issue.code,
            "message": issue.message,
            "expected": issue.expected,
        }
        for issue in initial_trace.issues
        if issue.code == "unsupported_capability"
    ]
    if not gap_issues:
        raise RuntimeError("initial task did not produce an unsupported_capability trace")

    model = OpenAICompatibleModel.deepseek_from_env()
    proposal = model.complete_json(
        system=(
            "You are a bounded Agent capability-gap proposer. Infer one minimal plugin from the "
            "failed task trace. Return JSON fields plugin_id, cues, positive_definition, exclusions, "
            "output_name, and reason. cues must contain 1-12 conservative literal substrings. The "
            "only allowed new protocol is metric_tool with compute_metric(orders, refunds), and the "
            "output_name must exactly match the missing expected result. Do not propose changes to "
            "existing plugins or model weights."
        ),
        user=json.dumps(
            {
                "failed_task": first_task["instruction"],
                "failure_trace": gap_issues,
                "existing_tools": [
                    "daily_net_sales",
                    "refund_rate",
                    "category_ranking",
                ],
                "existing_plugins": [
                    {
                        "plugin_id": record.plugin_id,
                        "kind": record.spec.kind,
                        "capability": record.capability.positive_definition,
                    }
                    for record in baseline_manager.records
                    if record.status == "active"
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    (args.output / "gap-proposal.json").write_text(
        json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    raw_cues = proposal.get("cues")
    if not isinstance(raw_cues, list):
        raise ValueError("gap proposal did not return cues")
    output_name = str(proposal.get("output_name", ""))
    if output_name != "refund_replay_rate":
        raise ValueError(f"gap proposal changed required output name: {output_name}")
    plugin_id = str(proposal.get("plugin_id", ""))
    if plugin_id in {record.plugin_id for record in baseline_manager.records}:
        raise ValueError("gap proposal reused an existing plugin id")
    capability = CapabilitySpec(
        capability_id=plugin_id,
        positive_definition=str(proposal.get("positive_definition", "")),
        exclusions=tuple(str(value) for value in proposal.get("exclusions", [])),
    )

    attempts = []
    previous_source = ""
    critic = ""
    accepted_source = ""
    accepted_registration = None
    for attempt in range(1, 3):
        response = model.complete_json(
            system=(
                "Generate a safe metric_tool plugin and return JSON fields source and reason. "
                "source must define exactly compute_metric(orders, refunds), with no imports, "
                "annotations, exceptions, helper functions, comprehensions, lambda, private names, "
                "or set(). Use a dict for membership. Allowed calls: dict, enumerate, int, len, list, "
                "max, min, range, sorted, str, sum. Allowed methods: append, copy, get, items, keys, "
                "setdefault, values. Return {'metric':'refund_replay_rate','data':{...},'evidence':{...}}. "
                "data must contain integer total_refund_rows, duplicate_rows, replay_rate_bps. A row "
                "is duplicate only after the first occurrence of the same refund_id; bps is floor of "
                "duplicate_rows*10000/total_refund_rows, or zero for empty input. Do not alter inputs."
            ),
            user=json.dumps(
                {
                    "capability_proposal": proposal,
                    "previous_source": previous_source or None,
                    "critic_error": critic or None,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        source = str(response.get("source", "")).rstrip() + "\n"
        attempt_root = args.output / f"attempt-{attempt}"
        attempt_root.mkdir(parents=True)
        (attempt_root / "proposal.json").write_text(
            json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (attempt_root / "candidate.py").write_text(source, encoding="utf-8")
        registration = CodePluginRegistration(
            plugin_id=plugin_id,
            cues=tuple(str(value) for value in raw_cues),
            source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            source_path="candidate.py",
            reason=str(proposal.get("reason", "")),
        )
        candidate_manager, record = _candidate_record(
            source, registration, capability
        )
        evolution = _direct_score(candidate_manager, record, args.suite, "evolution")
        validation = (
            _direct_score(candidate_manager, record, args.suite, "validation")
            if evolution["passed"] == evolution["count"]
            else None
        )
        attempt_result = {
            "attempt": attempt,
            "record_status": record.status,
            "quarantine_reason": record.quarantine_reason,
            "evolution": evolution,
            "validation": validation,
        }
        attempts.append(attempt_result)
        (attempt_root / "result.json").write_text(
            json.dumps(attempt_result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if (
            validation
            and validation["passed"] == validation["count"]
            and record.status == "active"
        ):
            accepted_source = source
            accepted_registration = registration
            break
        previous_source = source
        if record.status == "quarantined":
            critic = record.quarantine_reason
        else:
            score = validation or evolution
            failed = next((row for row in score["rows"] if not row["ok"]), None)
            critic = str(failed["error"] if failed else "candidate gate failed")

    route_development = None
    post_freeze_audit = None
    if accepted_registration is not None:
        (args.output / "candidate.py").write_text(accepted_source, encoding="utf-8")
        accepted_registration.write(args.output / "registration.json")
        (args.output / "spec.json").write_text(
            json.dumps(
                PluginSpec.metric_tool("refund_replay_rate").to_dict(),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_manager = PluginManager.from_manifest(
            args.manifest,
            semantic_model=model,
            cache_path=args.output / "route-cache.json",
        )
        runtime_manager.manifest_path = None
        runtime_manager.install(
            version="1",
            registration=accepted_registration,
            capability=capability,
            source=accepted_source,
            spec=PluginSpec.metric_tool("refund_replay_rate"),
            activate=True,
            persist=False,
        )
        route_development = {
            split: _agent_score(runtime_manager, args.suite, split)
            for split in ("evolution", "validation")
        }
        if all(
            score["passed"] == score["count"]
            for score in route_development.values()
        ):
            post_freeze_audit = _agent_score(runtime_manager, args.suite, "audit")

    accepted = bool(
        accepted_registration
        and route_development
        and all(
            score["passed"] == score["count"]
            for score in route_development.values()
        )
        and post_freeze_audit
        and post_freeze_audit["passed"] == post_freeze_audit["count"]
    )
    result = {
        "accepted": accepted,
        "baseline": baseline,
        "initial_failure_trace": gap_issues,
        "gap_proposal": proposal,
        "code_attempts": attempts,
        "route_development": route_development,
        "post_freeze_audit": post_freeze_audit,
        "model_calls": model.request_count,
        "model_usage": model.total_usage(),
        "claim_boundary": (
            "bounded harness-level capability RSI: a required-result failure triggers spec proposal, "
            "code generation, gated activation, and rerun; no model weights or unrestricted codebase are modified"
        ),
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit_text = (
        f"{post_freeze_audit['passed']}/{post_freeze_audit['count']}"
        if post_freeze_audit
        else "not_opened"
    )
    print(
        f"capability_rsi_accepted={accepted}, code_attempts={len(attempts)}, "
        f"audit={audit_text}, calls={model.request_count} -> {args.output}"
    )
    return int(not accepted)


if __name__ == "__main__":
    raise SystemExit(main())

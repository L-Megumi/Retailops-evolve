from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.plugin_manager import PluginManager
from retailops_agent.plugin_protocol import PluginSpec
from retailops_agent.semantic_plugin_router import CapabilitySpec
from retailops_agent.task_contract import infer_task_contract

try:
    from .run_capability_rsi import _agent_score, _load_task, _task_paths
except ImportError:  # pragma: no cover - direct script execution
    from run_capability_rsi import _agent_score, _load_task, _task_paths


def _prime_split(
    manager: PluginManager,
    model: OpenAICompatibleModel,
    suite: Path,
    split: str,
) -> dict[str, object]:
    tasks = [
        {
            "id": path.parent.name,
            "instruction": _load_task(path)["instruction"],
            "required_results": _load_task(path)["required_results"],
        }
        for path in _task_paths(suite, split)
    ]
    active = [record for record in manager.records if record.status == "active"]
    response = model.complete_json(
        system=(
            "Route a batch of independent tasks to zero or more active plugins. Return a JSON "
            "object with decisions, preserving every supplied id and order. Each decision contains "
            "id, plugin_ids, and reason. plugin_ids is a deduplicated array using only supplied "
            "plugin ids. Select every plugin whose full capability and declared output are required. "
            "Explicit exclusions and negations override vocabulary similarity. Do not infer execution "
            "order. A task asking for a metric should select the metric-producing plugin, not a "
            "mutating transform that merely shares words."
        ),
        user=json.dumps(
            {
                "plugins": [
                    {
                        "plugin_id": record.plugin_id,
                        "kind": record.spec.kind,
                        "output_name": record.spec.output_name,
                        "positive_definition": record.capability.positive_definition,
                        "explicit_exclusions": list(record.capability.exclusions),
                    }
                    for record in active
                ],
                "tasks": tasks,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    raw = response.get("decisions")
    if not isinstance(raw, list) or len(raw) != len(tasks):
        raise ValueError("batch router returned the wrong decision count")
    allowed = {record.plugin_id for record in active}
    rows = []
    for task, decision in zip(tasks, raw):
        if not isinstance(decision, dict) or decision.get("id") != task["id"]:
            raise ValueError("batch router changed a task id or order")
        plugin_ids = decision.get("plugin_ids")
        if not isinstance(plugin_ids, list) or any(
            not isinstance(value, str) or value not in allowed for value in plugin_ids
        ):
            raise ValueError("batch router returned invalid plugin ids")
        if len(plugin_ids) != len(set(plugin_ids)):
            raise ValueError("batch router returned duplicate plugin ids")
        manager.prime_route_cache(
            str(task["instruction"]),
            tuple(plugin_ids),
            reason=str(decision.get("reason", "")),
            contract=infer_task_contract(str(task["instruction"])).with_results(tuple(task["required_results"])),
        )
        rows.append({"id": task["id"], "plugin_ids": plugin_ids})
    return {"split": split, "count": len(rows), "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resume a frozen RSI candidate with batch route-cache priming."
    )
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    previous_result = json.loads(
        (args.previous / "result.json").read_text(encoding="utf-8")
    )
    proposal = json.loads(
        (args.previous / "gap-proposal.json").read_text(encoding="utf-8")
    )
    source = (args.previous / "attempt-2" / "candidate.py").read_text(
        encoding="utf-8"
    )
    plugin_id = str(proposal["plugin_id"])
    registration = CodePluginRegistration(
        plugin_id=plugin_id,
        cues=tuple(str(value) for value in proposal["cues"]),
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        source_path="candidate.py",
        reason=str(proposal.get("reason", "")),
    )
    capability = CapabilitySpec(
        capability_id=plugin_id,
        positive_definition=str(proposal["positive_definition"]),
        exclusions=tuple(str(value) for value in proposal["exclusions"]),
    )
    model = OpenAICompatibleModel.deepseek_from_env()
    manager = PluginManager.from_manifest(
        args.manifest,
        semantic_model=model,
        cache_path=args.output / "route-cache.json",
    )
    manager.manifest_path = None
    record = manager.install(
        version="1",
        registration=registration,
        capability=capability,
        source=source,
        spec=PluginSpec.metric_tool("refund_replay_rate"),
        activate=True,
        persist=False,
    )
    if record.status != "active":
        raise RuntimeError(f"frozen candidate failed static gate: {record.quarantine_reason}")

    priming = []
    route_development = {}
    for split in ("evolution", "validation"):
        priming.append(_prime_split(manager, model, args.suite, split))
        route_development[split] = _agent_score(manager, args.suite, split)
    development_passed = all(
        score["passed"] == score["count"] for score in route_development.values()
    )
    post_freeze_audit = None
    if development_passed:
        priming.append(_prime_split(manager, model, args.suite, "audit"))
        post_freeze_audit = _agent_score(manager, args.suite, "audit")
    accepted = bool(
        post_freeze_audit
        and post_freeze_audit["passed"] == post_freeze_audit["count"]
    )

    (args.output / "candidate.py").write_text(source, encoding="utf-8")
    registration.write(args.output / "registration.json")
    (args.output / "spec.json").write_text(
        json.dumps(
            PluginSpec.metric_tool("refund_replay_rate").to_dict(),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result = {
        "accepted": accepted,
        "resumed_from": str(args.previous),
        "frozen_candidate_sha256": registration.source_sha256,
        "previous_code_attempts": previous_result["code_attempts"],
        "routing_repair": "required-result coverage plus validated batch cache priming",
        "cache_priming": priming,
        "route_development": route_development,
        "post_freeze_audit": post_freeze_audit,
        "model_calls": model.request_count,
        "model_usage": model.total_usage(),
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
        f"batched_capability_rsi_accepted={accepted}, development="
        f"{sum(score['passed'] for score in route_development.values())}/"
        f"{sum(score['count'] for score in route_development.values())}, "
        f"audit={audit_text}, calls={model.request_count} -> {args.output}"
    )
    return int(not accepted)


if __name__ == "__main__":
    raise SystemExit(main())

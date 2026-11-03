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

try:
    from .run_capability_rsi import _agent_score
except ImportError:  # pragma: no cover - direct script execution
    from run_capability_rsi import _agent_score


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resume a frozen RSI candidate after a runtime routing repair."
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
        raise RuntimeError(f"frozen candidate no longer passes static gate: {record.quarantine_reason}")

    route_development = {
        split: _agent_score(manager, args.suite, split)
        for split in ("evolution", "validation")
    }
    development_passed = all(
        score["passed"] == score["count"] for score in route_development.values()
    )
    post_freeze_audit = (
        _agent_score(manager, args.suite, "audit") if development_passed else None
    )
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
        "routing_repair": (
            "required-result coverage triggers semantic selection when deterministic matches do not "
            "produce the declared output"
        ),
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
        f"capability_rsi_resumed_accepted={accepted}, development="
        f"{sum(score['passed'] for score in route_development.values())}/"
        f"{sum(score['count'] for score in route_development.values())}, "
        f"audit={audit_text}, calls={model.request_count} -> {args.output}"
    )
    return int(not accepted)


if __name__ == "__main__":
    raise SystemExit(main())

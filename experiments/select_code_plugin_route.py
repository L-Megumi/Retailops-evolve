from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

from retailops_agent.code_plugin_registry import CodePluginRegistration
from retailops_agent.strategy_library import StrategyLibrary

try:
    from .propose_code_plugin_route import _route_evaluation, _task_instructions
    from .run_rsi_evolution import evaluate_library
except ImportError:  # pragma: no cover - direct script execution
    from propose_code_plugin_route import _route_evaluation, _task_instructions
    from run_rsi_evolution import evaluate_library


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _group_matches(group: tuple[str, ...], instruction: str) -> bool:
    text = _normalize(instruction)
    return all(_normalize(token) in text for token in group)


def _cue_matches(cues: tuple[str, ...], instruction: str) -> bool:
    text = _normalize(instruction)
    return any(_normalize(cue) in text for cue in cues)


def select_minimum_non_regressing_groups(
    cues: tuple[str, ...],
    group_pool: tuple[tuple[str, ...], ...],
    development_instructions: dict[str, str],
    legacy_instructions: dict[str, str],
    max_groups: int = 8,
) -> tuple[tuple[tuple[str, ...], ...] | None, dict[str, object]]:
    """Find the smallest archived-group crossover with full dev recall and no collisions."""
    unsafe_groups = tuple(
        group
        for group in group_pool
        if any(_group_matches(group, text) for text in legacy_instructions.values())
    )
    safe_groups = tuple(group for group in group_pool if group not in unsafe_groups)
    base_covered = {
        task_id
        for task_id, text in development_instructions.items()
        if _cue_matches(cues, text)
    }
    target = set(development_instructions)
    group_coverage = {
        group: {
            task_id
            for task_id, text in development_instructions.items()
            if _group_matches(group, text)
        }
        for group in safe_groups
    }
    evaluated = 0
    selected = None
    for size in range(0, min(max_groups, len(safe_groups)) + 1):
        candidates = itertools.combinations(safe_groups, size)
        candidates = sorted(candidates, key=lambda groups: sum(map(len, groups)))
        for groups in candidates:
            evaluated += 1
            covered = set(base_covered)
            for group in groups:
                covered.update(group_coverage[group])
            if covered == target:
                selected = tuple(groups)
                break
        if selected is not None:
            break
    diagnostics = {
        "pool_size": len(group_pool),
        "safe_pool_size": len(safe_groups),
        "unsafe_groups": [list(group) for group in unsafe_groups],
        "base_cue_coverage": sorted(base_covered),
        "combinations_evaluated": evaluated,
    }
    return selected, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select a non-regressing crossover from archived plugin-route candidates."
    )
    parser.add_argument(
        "--code-suite", type=Path, default=Path("benchmarks/retailops/code-evolution-v1")
    )
    parser.add_argument("--audit-suite", type=Path, required=True)
    parser.add_argument(
        "--legacy-suite", type=Path, default=Path("benchmarks/retailops/rsi-v2")
    )
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--strategy-library", type=Path, required=True)
    parser.add_argument(
        "--candidate-registration", type=Path, action="append", required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    candidates = [CodePluginRegistration.read(path) for path in args.candidate_registration]
    base = candidates[0]
    if any(candidate.cues != base.cues for candidate in candidates[1:]):
        parser.error("candidate registrations must share the same literal cues")
    source_sha256 = hashlib.sha256(args.plugin.read_bytes()).hexdigest()
    if any(candidate.source_sha256 != source_sha256 for candidate in candidates):
        parser.error("candidate source digest does not match the frozen plugin")

    seen: set[tuple[str, ...]] = set()
    group_pool: list[tuple[str, ...]] = []
    for candidate in candidates:
        for group in candidate.keyword_groups:
            normalized = tuple(_normalize(token) for token in group)
            if normalized not in seen:
                seen.add(normalized)
                group_pool.append(group)

    development_instructions = {
        task_id: instruction
        for split in ("evolution", "validation", "transfer")
        for task_id, instruction in _task_instructions(args.code_suite, split).items()
    }
    legacy_instructions = {
        task_id: instruction
        for split in ("regression", "evolution", "validation", "transfer")
        for task_id, instruction in _task_instructions(args.legacy_suite, split).items()
    }
    selected, search = select_minimum_non_regressing_groups(
        base.cues,
        tuple(group_pool),
        development_instructions,
        legacy_instructions,
    )
    if selected is None:
        result = {
            "accepted": False,
            "error": "no archived group crossover satisfied the development gate",
            "search": search,
            "model_calls": 0,
        }
        (args.output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"crossover_accepted=False, audit=not_opened -> {args.output}")
        return 1

    registration = CodePluginRegistration(
        plugin_id=base.plugin_id,
        cues=base.cues,
        source_sha256=source_sha256,
        source_path=str(args.plugin),
        keyword_groups=selected,
        reason=(
            "Minimum non-regressing crossover selected from archived semantic-route candidates; "
            "selection used development tasks and legacy collision checks only."
        ),
    )
    registration.write(args.output / "registration.json")
    plugin_source = args.plugin.read_text(encoding="utf-8")
    development_scores = {
        split: _route_evaluation(args.code_suite, split, registration, plugin_source)
        for split in ("evolution", "validation", "transfer")
    }
    collisions = sorted(
        task_id
        for task_id, instruction in legacy_instructions.items()
        if registration.matches(instruction)
    )
    strategy_library = StrategyLibrary.read(args.strategy_library)
    legacy_scores = {
        split: evaluate_library(args.legacy_suite, split, strategy_library).summary()
        for split in ("regression", "evolution", "validation", "transfer")
    }
    gate_checks = {
        "development_code_full": all(
            score["passed"] == score["task_count"]
            for score in development_scores.values()
        ),
        "legacy_route_zero_collision": not collisions,
        "legacy_strategy_full": all(
            score["passed"] == score["task_count"] for score in legacy_scores.values()
        ),
    }
    development_gate_passed = all(gate_checks.values())
    audit = (
        _route_evaluation(args.audit_suite, "transfer", registration, plugin_source)
        if development_gate_passed
        else None
    )
    audit_passed = bool(
        audit and audit["passed"] == audit["task_count"]
    )
    accepted = development_gate_passed and audit_passed
    result = {
        "accepted": accepted,
        "development_gate_passed": development_gate_passed,
        "audit_passed": audit_passed,
        "gate_checks": gate_checks,
        "candidate_registrations": [str(path) for path in args.candidate_registration],
        "search": search,
        "registration": registration.to_dict(),
        "development_scores": development_scores,
        "legacy_collisions": collisions,
        "legacy_scores": legacy_scores,
        "post_selection_audit": audit,
        "model_calls": 0,
        "model_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    (args.output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit_text = f"{audit['passed']}/{audit['task_count']}" if audit else "not_opened"
    print(
        f"crossover_accepted={accepted}, groups={len(selected)}, "
        f"development_transfer={development_scores['transfer']['passed']}/4, "
        f"collisions={len(collisions)}, audit={audit_text}, calls=0 -> {args.output}"
    )
    return int(not accepted)


if __name__ == "__main__":
    raise SystemExit(main())

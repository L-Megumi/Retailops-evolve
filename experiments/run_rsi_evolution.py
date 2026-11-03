from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from retailops_agent.llm import OpenAICompatibleModel
from retailops_agent.models import ExecutionPolicy
from retailops_agent.strategy_library import (
    ACTION_DESCRIPTIONS,
    StrategyLibrary,
    candidate_rule_from_model,
    execute_with_library,
)


def _normalize(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _library_hash(library: StrategyLibrary) -> str:
    payload = json.dumps(
        library.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class RuleProposer(Protocol):
    request_count: int

    def propose(
        self,
        *,
        instruction: str,
        tools: list[str],
        library: StrategyLibrary,
        generation: int,
        rejection: str,
        cue_options: list[str],
    ) -> dict[str, object]: ...

    def usage(self) -> dict[str, int]: ...


class DeepSeekRuleProposer:
    def __init__(self, model: OpenAICompatibleModel):
        self.model = model

    @property
    def request_count(self) -> int:
        return self.model.request_count

    def usage(self) -> dict[str, int]:
        return self.model.total_usage()

    def propose(
        self,
        *,
        instruction: str,
        tools: list[str],
        library: StrategyLibrary,
        generation: int,
        rejection: str,
        cue_options: list[str],
    ) -> dict[str, object]:
        actions = "\n".join(
            f"- {name}: {description}" for name, description in ACTION_DESCRIPTIONS.items()
        )
        return self.model.complete_json(
            system=(
                "You improve a bounded retail strategy library. Propose exactly one rule for "
                "the failing task. Return one JSON object with string fields cue, action, reason. "
                "cue must equal one entry from cue_options exactly; do not paraphrase it. Choose "
                "an invariant business-rule phrase rather than a task id. The provided options omit "
                "instance-specific numeric thresholds so the rule can transfer. If a previous candidate "
                "failed the gain floor, generalize the cue instead of repeating it. action must be one "
                f"allowed name below. Do not output code or extra fields.\n{actions}"
            ),
            user=json.dumps(
                {
                    "generation": generation,
                    "failing_instruction": instruction,
                    "tools": tools,
                    "current_library": library.to_dict(),
                    "cue_options": cue_options,
                    "external_judge": "current strategy output does not equal the hidden oracle",
                    "previous_rejection": rejection,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )


class DeterministicRuleProposer:
    """Test-only proposer that follows the same one-rule interface without a model call."""

    def __init__(self):
        self.request_count = 0

    def usage(self) -> dict[str, int]:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def propose(
        self,
        *,
        instruction: str,
        tools: list[str],
        library: StrategyLibrary,
        generation: int,
        rejection: str,
        cue_options: list[str],
    ) -> dict[str, object]:
        self.request_count += 1
        mappings = (
            ("等额拆分", "equal_category_allocation"),
            ("商品数量之和加权分摊", "quantity_category_allocation"),
            ("相同 refund_id 只认第一次", "deduplicate_refunds"),
            ("逐笔计算退款日与原订单日的自然日间隔", "hybrid_refund_time"),
        )
        for cue, action in mappings:
            if cue in instruction:
                return {
                    "cue": cue,
                    "action": action,
                    "reason": "deterministic test proposal inferred from the task rule",
                }
        raise ValueError("test proposer could not infer a bounded action")


def _cue_options(instruction: str) -> list[str]:
    clauses = [
        clause.strip()
        for clause in re.split(r"[。；;：:，,\n]", instruction)
        if clause.strip()
    ]
    options: set[str] = set()
    for clause in clauses:
        upper = min(24, len(clause))
        for length in range(4, upper + 1):
            for candidate in (clause[:length].strip(), clause[-length:].strip()):
                if len(candidate) < 4 or any(character.isdigit() for character in candidate):
                    continue
                options.add(candidate)
    return sorted(options, key=lambda item: (len(item), item))


def _screen_cue_options(
    suite: Path,
    options: list[str],
    failing_tasks: set[str],
    *,
    minimum_coverage: int,
) -> list[str]:
    instructions: dict[str, str] = {}
    for task_path in sorted((suite / "tasks" / "evolution").glob("*/task.json")):
        task = json.loads(task_path.read_text(encoding="utf-8"))
        instructions[task_path.parent.name] = " ".join(
            str(task["instruction"]).casefold().split()
        )
    screened: list[str] = []
    for cue in options:
        normalized_cue = " ".join(cue.casefold().split())
        matched = {
            task_id
            for task_id, instruction in instructions.items()
            if normalized_cue in instruction
        }
        if len(matched) >= minimum_coverage and matched.issubset(failing_tasks):
            screened.append(cue)
    return screened


@dataclass(frozen=True)
class SplitEvaluation:
    split: str
    passed: int
    task_count: int
    rows: tuple[dict[str, object], ...]

    def summary(self) -> dict[str, int | str]:
        return {"split": self.split, "passed": self.passed, "task_count": self.task_count}


def evaluate_library(
    suite: Path,
    split: str,
    library: StrategyLibrary,
) -> SplitEvaluation:
    rows: list[dict[str, object]] = []
    task_root = suite / "tasks" / split
    for task_dir in sorted(path for path in task_root.iterdir() if path.is_dir()):
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        oracle = json.loads(
            (suite / "oracle" / split / f"{task_dir.name}.json").read_text(encoding="utf-8")
        )
        policy = ExecutionPolicy(**oracle["metric_contract"])
        execution = execute_with_library(task_dir, policy, library)
        actual = {
            name: _normalize(result.data) for name, result in execution.results.items()
        }
        ok = actual == oracle["results"]
        rows.append(
            {
                "task": task_dir.name,
                "ok": ok,
                "case_kind": task["benchmark_metadata"].get(
                    "case_kind", "contract_selection"
                ),
                "matched_rule_id": execution.matched_rule_id,
                "action": execution.action,
            }
        )
    return SplitEvaluation(
        split=split,
        passed=sum(bool(row["ok"]) for row in rows),
        task_count=len(rows),
        rows=tuple(rows),
    )


def _lost_passes(
    incumbent: SplitEvaluation, candidate: SplitEvaluation
) -> list[str]:
    candidate_by_task = {str(row["task"]): bool(row["ok"]) for row in candidate.rows}
    return [
        str(row["task"])
        for row in incumbent.rows
        if bool(row["ok"]) and not candidate_by_task.get(str(row["task"]), False)
    ]


def _evaluation_payload(
    evaluations: dict[str, SplitEvaluation],
) -> dict[str, object]:
    return {
        split: {**evaluation.summary(), "rows": list(evaluation.rows)}
        for split, evaluation in evaluations.items()
    }


def run_evolution(
    *,
    suite: Path,
    output: Path,
    proposer: RuleProposer,
    max_generations: int = 8,
    candidates_per_generation: int = 3,
    minimum_split_gain: int = 4,
) -> dict[str, object]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty evolution output: {output}")
    output.mkdir(parents=True, exist_ok=True)
    library = StrategyLibrary()
    library.write(output / "incumbent" / "library.json")
    incumbent = {
        split: evaluate_library(suite, split, library)
        for split in ("regression", "evolution", "validation")
    }
    initial = {split: evaluation.summary() for split, evaluation in incumbent.items()}
    _write_json(output / "initial-evaluation.json", _evaluation_payload(incumbent))
    ledger: list[dict[str, object]] = []
    stopped_reason = "generation_budget_exhausted"

    for generation in range(1, max_generations + 1):
        failures = [row for row in incumbent["evolution"].rows if not bool(row["ok"])]
        if not failures:
            stopped_reason = "all_evolution_tasks_passed"
            break
        source_task = str(failures[0]["task"])
        task_dir = suite / "tasks" / "evolution" / source_task
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        cue_options = _screen_cue_options(
            suite,
            _cue_options(str(task["instruction"])),
            {str(row["task"]) for row in failures},
            minimum_coverage=minimum_split_gain,
        )
        if not cue_options:
            stopped_reason = "scope_critic_found_no_safe_cue"
            break
        generation_dir = output / f"generation-{generation:02d}"
        accepted = False
        rejection = ""

        for candidate_index in range(1, candidates_per_generation + 1):
            candidate_dir = generation_dir / f"candidate-{candidate_index:02d}"
            proposal: dict[str, object] = {}
            decision: dict[str, object]
            try:
                proposal = proposer.propose(
                    instruction=str(task["instruction"]),
                    tools=[str(name) for name in task["tools"]],
                    library=library,
                    generation=generation,
                    rejection=rejection,
                    cue_options=cue_options,
                )
                rule = candidate_rule_from_model(
                    proposal,
                    generation=generation,
                    source_task=source_task,
                    instruction=str(task["instruction"]),
                    allowed_cues=cue_options,
                )
                candidate = library.with_rule(rule)
                candidate.write(candidate_dir / "library.json")
                candidate_evaluations = {
                    split: evaluate_library(suite, split, candidate)
                    for split in ("regression", "evolution", "validation")
                }
                lost = {
                    split: _lost_passes(incumbent[split], candidate_evaluations[split])
                    for split in incumbent
                }
                evolution_gain = (
                    candidate_evaluations["evolution"].passed
                    - incumbent["evolution"].passed
                )
                validation_gain = (
                    candidate_evaluations["validation"].passed
                    - incumbent["validation"].passed
                )
                gate_checks = {
                    "bounded_one_rule_edit": len(candidate.rules) == len(library.rules) + 1,
                    "regression_no_loss": not lost["regression"],
                    "evolution_no_loss": not lost["evolution"],
                    "validation_no_loss": not lost["validation"],
                    "evolution_improved": evolution_gain > 0,
                    "validation_improved": validation_gain > 0,
                    "evolution_gain_floor": evolution_gain >= minimum_split_gain,
                    "validation_gain_floor": validation_gain >= minimum_split_gain,
                }
                accepted = all(gate_checks.values())
                decision = {
                    "accepted": accepted,
                    "gate_checks": gate_checks,
                    "lost_passes": lost,
                    "score_gains": {
                        "evolution": evolution_gain,
                        "validation": validation_gain,
                        "minimum_required": minimum_split_gain,
                    },
                    "incumbent_scores": {
                        split: evaluation.summary() for split, evaluation in incumbent.items()
                    },
                    "candidate_scores": {
                        split: evaluation.summary()
                        for split, evaluation in candidate_evaluations.items()
                    },
                }
                _write_json(
                    candidate_dir / "evaluation.json",
                    _evaluation_payload(candidate_evaluations),
                )
                if accepted:
                    library = candidate
                    incumbent = candidate_evaluations
                    library.write(output / "incumbent" / "library.json")
            except (KeyError, TypeError, ValueError) as exc:
                rejection = f"{type(exc).__name__}: {exc}"
                decision = {
                    "accepted": False,
                    "gate_checks": {"proposal_valid": False},
                    "error": rejection,
                }

            _write_json(candidate_dir / "proposal.json", proposal)
            _write_json(candidate_dir / "decision.json", decision)
            event = {
                "generation": generation,
                "candidate": candidate_index,
                "source_task": source_task,
                "proposal": proposal,
                "decision": decision,
                "incumbent_version_after": library.version,
                "incumbent_hash_after": _library_hash(library),
            }
            ledger.append(event)
            _write_json(output / "ledger.json", ledger)
            if accepted:
                break
            rejection = json.dumps(decision, ensure_ascii=False, separators=(",", ":"))[:1500]

        if not accepted:
            stopped_reason = "candidate_attempts_exhausted"
            break

    transfer = evaluate_library(suite, "transfer", library)
    _write_json(
        output / "final-transfer-evaluation.json",
        {**transfer.summary(), "rows": list(transfer.rows)},
    )
    result = {
        "suite": suite.name,
        "initial": initial,
        "final": {
            **{split: evaluation.summary() for split, evaluation in incumbent.items()},
            "transfer": transfer.summary(),
        },
        "library": library.to_dict(),
        "library_hash": _library_hash(library),
        "accepted_generations": library.version,
        "candidate_attempts": len(ledger),
        "stopped_reason": stopped_reason,
        "transfer_opened_once_after_search": True,
        "proposer_calls": proposer.request_count,
        "proposer_usage": proposer.usage(),
        "ledger": ledger,
    }
    _write_json(output / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run bounded, persistent strategy-library evolution with holdout gates."
    )
    parser.add_argument(
        "--suite", type=Path, default=Path("benchmarks/retailops/rsi-v2")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/rsi-v2/evolution-deepseek")
    )
    parser.add_argument("--max-generations", type=int, default=8)
    parser.add_argument("--candidates-per-generation", type=int, default=3)
    parser.add_argument(
        "--minimum-split-gain",
        type=int,
        default=4,
        help="minimum evolution and validation tasks a candidate must newly solve",
    )
    parser.add_argument(
        "--proposer", choices=["deepseek", "deterministic"], default="deepseek"
    )
    args = parser.parse_args()
    if (
        args.max_generations < 1
        or args.candidates_per_generation < 1
        or args.minimum_split_gain < 1
    ):
        parser.error("generation and candidate budgets must be positive")
    proposer: RuleProposer
    if args.proposer == "deepseek":
        proposer = DeepSeekRuleProposer(OpenAICompatibleModel.deepseek_from_env())
    else:
        proposer = DeterministicRuleProposer()
    try:
        result = run_evolution(
            suite=args.suite,
            output=args.output,
            proposer=proposer,
            max_generations=args.max_generations,
            candidates_per_generation=args.candidates_per_generation,
            minimum_split_gain=args.minimum_split_gain,
        )
    except ValueError as exc:
        parser.error(str(exc))
    final = result["final"]
    print(
        f"accepted={result['accepted_generations']}, attempts={result['candidate_attempts']}, "
        f"evolution={final['evolution']['passed']}/{final['evolution']['task_count']}, "
        f"validation={final['validation']['passed']}/{final['validation']['task_count']}, "
        f"regression={final['regression']['passed']}/{final['regression']['task_count']}, "
        f"transfer={final['transfer']['passed']}/{final['transfer']['task_count']}, "
        f"calls={result['proposer_calls']} -> {args.output}"
    )
    return int(
        final["regression"]["passed"] != final["regression"]["task_count"]
        or final["transfer"]["passed"] != final["transfer"]["task_count"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
